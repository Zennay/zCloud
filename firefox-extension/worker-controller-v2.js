/* zCloud worker-controller v2: the ONLY Firefox tab lifecycle owner.
 * Never touch a non-owned tab, select an active tab or open a new window.
 * Temporary API failures/invalid assignments never imply worker removal.
 */
(() => {
  "use strict";
  const API = "http://127.0.0.1:8765/api";
  const TAG = "zcloud-v2-worker-key";
  const RESERVE_TAG = "zcloud-v2-pool-reserve";
  const KNOWN_KEY = "zcloud-v2-known-worker-ids";
  const TICK_MS = 30000;
  const STALE_POLL_MS = 4 * 60 * 1000;
  const RELOAD_COOLDOWN_MS = 10 * 60 * 1000;
  const MISSING_TAB_RECHECK_MS = 2000;
  const POOL_SAVE_CHECK_MS = 5000;
  const ownership = new Map(); // worker key -> tab ID (same tab across chats)
  const reserves = new Map(); // idle ChatGPT capacity slot -> dedicated Firefox tab ID
  const reserveObservations = new Map();
  const candidateRemovals = new Map();
  const closeCooldown = new Map();
  const stalePollObservations = new Map();
  const reloadCooldown = new Map();
  const lastChatGPTProviderSlot = new Map(); // verified allocation before a pool resize
  let workerWindow = null;
  let busy = false;
  let quickRecheckTimer = null;
  let lastSnapshot = null;
  let knownWorkers = new Set();
  let knownLoaded = false;
  const recoveryObservations = new Map();
 
  function isWorkerUrl(url) {
    try { return new URL(url).hostname === "chatgpt.com"; } catch (_) { return false; }
  }
  function workerMarker(url) {
    try { return new URL(url).searchParams.get("zcloud_worker") || ""; } catch (_) { return ""; }
  }
  function reservedSlot(url) {
    try {
      const u = new URL(url);
      if (u.hostname !== "chatgpt.com" || u.pathname !== "/" ||
          u.searchParams.size !== 1 || !u.searchParams.has("zcloud_pool")) return 0;
      const raw = u.searchParams.get("zcloud_pool");
      const n = Number(raw);
      return /^(?:[1-9][0-9]*)$/.test(raw || "") &&
        Number.isInteger(n) && n >= 1 && n <= 16 ? n : 0;
    } catch (_) { return 0; }
  }
  async function reserveIdle(tabId) {
    try {
      const [idle] = await browser.tabs.executeScript(tabId, {
        code:'(() => { const el=document.querySelector(\'#prompt-textarea,[contenteditable="true"][role="textbox"],textarea\'); return document.readyState==="complete" && !document.querySelector(\'button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop response"]\') && !String(el?.value||el?.innerText||el?.textContent||"").trim(); })()'
      });
      return idle === true;
    } catch (_) { return false; }
  }
  async function recoverReserves(tabs) {
    const live = new Set(tabs.map(t => t.id));
    for (const [slot, id] of reserves) if (!live.has(id)) reserves.delete(slot);
    for (const tab of tabs) {
      const slot = reservedSlot(tab.url);
      if (!slot || reserves.has(slot) || [...ownership.values()].includes(tab.id)) continue;
      let tag = "";
      try { tag = String(await browser.sessions.getTabValue(tab.id, RESERVE_TAG) || ""); }
      catch (_) {}
      // An exact pool-only URL identifies our own parked tab after session
      // restore. Never adopt arbitrary ChatGPT conversations or expert tabs.
      if (tag && tag !== String(slot)) continue;
      reserves.set(slot, tab.id);
      try { await browser.sessions.setTabValue(tab.id, RESERVE_TAG, String(slot)); } catch (_) {}
      if (workerWindow === null) workerWindow = tab.windowId;
    }
  }
  // A paused or draining assignment STILL owns its Firefox tab. The page
  // runner controls dispatch; the extension must restore the tab, not run it.
  function assigned(t) {
    if (!t) return false;
    const key = String(t.project_id || "");
    const count = Number(t.global_worker_count || 0);
    const slot = Number(t.global_worker_slot || 0);
    const chatgpt = !t.provider || t.provider === "chatgpt";
    // A worker slot is already owned by the backend allocation even while its
    // queue claim is being reconciled. Create the missing identity-bound tab
    // independently of assignment_ready/queue_id; only the page runner may
    // dispatch, and it still requires a valid lease. Never invent unallocated
    // projects or take over untagged personal ChatGPT tabs.
    const runningSlot = t.active === true && t.desired_state !== "paused" &&
      t.desired_state !== "draining";
    const leasedSlot = t.assignment_ready === true &&
      !!String(t.queue_item?.queue_id || "");
    return chatgpt && /^[a-z0-9_-]+::w[1-9][0-9]*$/i.test(key) &&
      count >= 1 && slot >= 1 && slot <= count &&
      (runningSlot || leasedSlot);
  }
  async function loadKnownWorkers() {
    if (knownLoaded) return;
    const saved = await browser.storage.local.get(KNOWN_KEY);
    const ids = saved?.[KNOWN_KEY];
    knownWorkers = new Set((Array.isArray(ids) ? ids : []).filter(id =>
      /^[a-z0-9_-]+::w[1-9][0-9]*$/i.test(String(id))));
    knownLoaded = true;
  }
  async function rememberWorker(key) {
    if (knownWorkers.has(key)) return;
    knownWorkers.add(key);
    // Persist the SLOT identity, not Firefox's ephemeral tab ID. Tab IDs can
    // change across a real browser crash and session restore.
    await browser.storage.local.set({[KNOWN_KEY]:[...knownWorkers].slice(-128)});
  }
  async function forgetWorker(key) {
    if (!knownWorkers.delete(key)) return;
    await browser.storage.local.set({[KNOWN_KEY]:[...knownWorkers]});
  }
  async function tagOf(tab) {
    for (const sessionKey of [TAG, "zcloud-worker-id"]) {
      try {
        // Migrate the legacy Firefox session tag without adopting any expert
        // ChatGPT tab by URL/conversation similarity.
        const value = await browser.sessions.getTabValue(tab.id, sessionKey);
        if (value) return String(value);
      } catch (_) {}
    }
    return workerMarker(tab.url);
  }
  browser.runtime.onMessage.addListener((message,sender) => {
    if(message?.type!=="zcloud-worker-identity" || !sender.tab ||
       !isWorkerUrl(sender.tab.url))return undefined;
    return tagOf(sender.tab).then(key=>
      /^[a-z0-9_-]+::w[1-9][0-9]*$/i.test(key||"")?key:null);
  });
  async function restoredTabIdentity(tab) {
    const tagged = await tagOf(tab);
    if (tagged) return tagged;
    if (!knownWorkers.size) return "";
    // Firefox sessionstore can restore an URL but lose an extension's tab
    // session value. Recover an exact worker marker from that SAME restored
    // page before considering a replacement tab.
    try {
      const [worker] = await browser.tabs.executeScript(tab.id, {
        code:'sessionStorage.getItem("zcloud-v2-worker") || document.documentElement?.getAttribute("data-zcloud-v2-owner") || ""'
      });
      return knownWorkers.has(String(worker || "")) ? String(worker) : "";
    } catch (_) { return ""; }
  }
  async function note(event, id, reason) {
    // Status only: never use a synthetic user prompt to report failure.
    console.log("[zCloud v2]", event, id || "", reason || "");
  }
  async function recoverOwnership() {
    const tabs = await browser.tabs.query({});
    const liveIds = new Set(tabs.map(t => t.id));
    for (const [id, tabId] of ownership) {
      const tab = tabs.find(t => t.id === tabId);
      if (!liveIds.has(tabId) || (tab && !isWorkerUrl(tab.url))) {
        // An owned tab was closed or navigated away. The latter is now a
        // personal tab: never navigate/remove it to reclaim the slot.
        ownership.delete(id);
        stalePollObservations.delete(id);
        closeCooldown.set(id, Date.now() + 30000);
      }
    }
    const claimed = new Set(ownership.values());
    for (const tab of tabs) {
      if (claimed.has(tab.id) || !isWorkerUrl(tab.url)) continue;
      const id = await restoredTabIdentity(tab);
      if (!/^[a-z0-9_-]+::w[1-9][0-9]*$/i.test(id) || ownership.has(id)) continue;
      ownership.set(id, tab.id);
      claimed.add(tab.id);
      try { await browser.sessions.setTabValue(tab.id, TAG, id); } catch (_) {}
      await rememberWorker(id);
      if (workerWindow === null) workerWindow = tab.windowId;
    }
    // A restored Firefox session tag is the durable authority. Bootstrap
    // the page even if document-start messaging raced extension startup.
    for(const [key,tabId] of ownership){
      const tab=tabs.find(t=>t.id===tabId);
      if(!tab || !isWorkerUrl(tab.url) || await tagOf(tab)!==key)continue;
      try{await browser.tabs.executeScript(tabId,{code:
        '(() => { const key='+JSON.stringify(key)+'; const prior=sessionStorage.getItem("zcloud-v2-worker"); if(prior && prior!==key)return; sessionStorage.setItem("zcloud-v2-worker",key); document.documentElement?.dispatchEvent(new Event("zcloud-worker-identity-ready")); })()'
      })}catch(_){}
    }
    return tabs;
  }
  async function chooseWindow(tabs) {
    if (workerWindow != null) {
      const matching = tabs.find(t => t.windowId === workerWindow);
      if (matching) return workerWindow;
    }
    const existingWorker = tabs.find(t => [...ownership.values()].includes(t.id));
    if (existingWorker) return (workerWindow = existingWorker.windowId);
    const all = await browser.windows.getAll({windowTypes:["normal"]});
    const normal = all.find(w => w.type === "normal");
    if (normal) return (workerWindow = normal.id);
    // The Firefox crash watcher is responsible for restoring a window.
    return null;
  }
  async function rebindReleasedSlot(key, target, allTargets, desired, url, slot) {
    // Project switches reuse the same *capacity slot* instead of creating
    // endless new project tabs. Never touch a producing worker, paused tab,
    // unsent draft, or any tab not explicitly owned by this extension.
    for (const [oldKey, tabId] of ownership) {
      if (oldKey === key || desired.has(oldKey) ||
          lastChatGPTProviderSlot.get(oldKey) !== slot) continue;
      const old = allTargets?.[oldKey];
      if (!old || old.active !== false || old.assignment_ready === true ||
          old.desired_state !== "running" || old.queue_item?.queue_id ||
          (old.provider && old.provider !== "chatgpt")) continue;
      const tab = await browser.tabs.get(tabId).catch(() => null);
      if (!tab || !isWorkerUrl(tab.url) || await tagOf(tab) !== oldKey) continue;
      let state = null;
      try {
        [state] = await browser.tabs.executeScript(tabId, {
          code:'(() => { const el=document.documentElement; const box=document.querySelector(\'#prompt-textarea,[contenteditable="true"][role="textbox"],textarea\'); return {owner:el?.getAttribute("data-zcloud-v2-owner")||"",version:el?.getAttribute("data-zcloud-violentmonkey-ready")||"",poll:Number(el?.getAttribute("data-zcloud-v2-last-poll")||0),generating:!!document.querySelector(\'button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop response"]\'),draft:!!String(box?.value||box?.innerText||box?.textContent||"").trim()}; })()'
        });
      } catch (_) { continue; }
      if (!state || state.owner !== oldKey ||
          !/^2\.0\.[0123]$/.test(state.version) ||
          state.generating !== false || state.draft !== false ||
          !Number.isFinite(state.poll) || state.poll <= 0 ||
          Date.now()-state.poll < 0 || Date.now()-state.poll >= 120000)
        continue;
      try {
        // The new same-origin tab would otherwise inherit sessionStorage's
        // OLD worker ID and the v2 runner would reject the new URL.
        // Project switch: retire only an independently verified idle OLD tab.
        // A Force request must launch a NEW identity-owned Firefox tab.
        await browser.tabs.remove(tabId);
        ownership.delete(oldKey);
        stalePollObservations.delete(oldKey);
        candidateRemovals.delete(oldKey);
        lastChatGPTProviderSlot.delete(oldKey);
        await forgetWorker(oldKey);
        await note("worker-tab-project-retired",oldKey,"replacement-worker="+key);
        return false; // createWorkerTab now opens a brand-new key-bound tab
      } catch (_) { return false; }
    }
    return false;
  }
  async function createWorkerTab(key, target, windowId, allTargets, desired) {
    if (Date.now() < Number(closeCooldown.get(key) || 0)) return;
    if (windowId == null || ownership.has(key)) return;
    const url = new URL("https://chatgpt.com/");
    url.searchParams.set("zcloud_worker", key);
    // Reuse an extension-owned, EMPTY capacity tab from Opslaan instead of
    // closing/reopening tabs. No conversation tab or draft may be repurposed.
    const slot = Number(target.provider_worker_slot ?? target.global_worker_slot);
    const reservedId = reserves.get(slot);
    if (reservedId != null) {
      const tab = await browser.tabs.get(reservedId).catch(() => null);
      let tag = "";
      try { tag = String(await browser.sessions.getTabValue(reservedId, RESERVE_TAG) || ""); }
      catch (_) {}
      if (tab && reservedSlot(tab.url) === slot && tag === String(slot) &&
          await reserveIdle(reservedId)) {
        await browser.tabs.update(reservedId, {url:String(url), active:false});
        reserves.delete(slot);
        ownership.set(key, reservedId);
        try { await browser.sessions.setTabValue(reservedId, TAG, key); } catch (_) {}
        try { await browser.sessions.removeTabValue(reservedId, RESERVE_TAG); } catch (_) {}
        await rememberWorker(key);
        await note("worker-tab-reserve-activated", key, "safe-idle-same-tab-rebind");
        return;
      }
    }
    // Retire a released slot only if verified idle; NEVER navigate it into a
    // different project. Even if retirement is unsafe, open the new owner tab.
    await rebindReleasedSlot(key,target,allTargets,desired,url,slot);
    // active:false is absolute: starting a worker may NEVER switch active tabs.
    const tab = await browser.tabs.create({url:String(url), windowId, active:false});
    ownership.set(key, tab.id);
    try { await browser.sessions.setTabValue(tab.id, TAG, key); } catch (_) {}
    await rememberWorker(key);
    await note("worker-tab-created", key, "allocated-or-crash-restored-worker");
  }
  async function reconcile() {
    if (busy) return;
    busy = true;
    try {
      await loadKnownWorkers();
      const response = await fetch(API + "/runner-targets", {cache:"no-store"});
      if (!response.ok) throw new Error("targets-http-" + response.status);
      const payload = await response.json();
      if (!payload || typeof payload.projects !== "object" || !payload.projects)
        throw new Error("invalid-target-payload");
      const tabs = await recoverOwnership();
      await recoverReserves(tabs);
      const desired = new Map(Object.values(payload.projects).filter(assigned).map(t => [t.project_id,t]));
      // Queue leases may disappear during a browser crash or scheduler
      // handover, even though the worker still exists in the user's saved
      // configuration. Recover only exact, previously seen worker identities.
      // Never invent a project for an empty slot or exceed the ChatGPT pool
      // limit. An unclaimed worker remains idle until a valid lease returns.
      const restorableWithoutLease = new Set();
      const poolSize = Number(payload.dynamic_workers?.chatgpt_count);
      if (Number.isInteger(poolSize) && poolSize > 0) {
        const currentlyAllocatedSlots = new Set([...desired.values()].map(t =>
          Number(t.provider_worker_slot ?? t.global_worker_slot))
          .filter(n => Number.isInteger(n) && n >= 1 && n <= poolSize));
        for (const key of knownWorkers) {
          if (desired.size >= poolSize) break;
          if (desired.has(key)) continue;
          const target = payload.projects[key];
          const workerSlot=Number(target?.worker_slot);
          const workerCount=Number(target?.worker_count);
          const previousSlot=lastChatGPTProviderSlot.get(key);
          // Once a different project owns this physical provider slot, it
          // is NOT an interrupted browser session to resurrect. Free it for
          // immediate safe idle-tab reuse (Force project switch).
          if (Number.isInteger(previousSlot) &&
              currentlyAllocatedSlots.has(previousSlot)) continue;
          if (!target || String(target.project_id) !== key ||
              target.provider !== "chatgpt" ||
              target.desired_state !== "running" ||
              !/^[a-z0-9_-]+::w[1-9][0-9]*$/i.test(key) ||
              !Number.isInteger(workerSlot) || !Number.isInteger(workerCount) ||
              workerSlot < 1 || workerSlot > workerCount) continue;
          desired.set(key, target);
          restorableWithoutLease.add(key);
        }
      }
      const windowId = await chooseWindow(tabs);
      // The authenticated local backend is the authority for active worker
      // identities. A restored personal/expert ChatGPT tab must not block
      // creation of an unrelated, missing worker tab. Conversely, we NEVER
      // adopt an untagged ChatGPT tab by its position or conversation URL.
      let missingRunningTab = false;
      for (const [key, target] of desired) {
        if (ownership.has(key)) { recoveryObservations.delete(key); continue; }
        recoveryObservations.set(key, (recoveryObservations.get(key) || 0) + 1);
        if (target.desired_state !== "paused" && target.desired_state !== "draining" &&
            (target.active === true || restorableWithoutLease.has(key)))
          missingRunningTab = true;
      }
      // Two backend observations protect against session-restore races, but a
      // missing identity must not wait for the 30-second normal poll (or for
      // the user to manually create a tab bearing that identity).
      if (missingRunningTab && quickRecheckTimer === null &&
          typeof setTimeout === "function")
        quickRecheckTimer = setTimeout(() => {
          quickRecheckTimer = null;
          reconcile();
        }, MISSING_TAB_RECHECK_MS);
      for (const [key, target] of desired) {
        // A dynamic ChatGPT pool reduction can remove a worker from the
        // allocation entirely. Remember its last *provider* slot, never its
        // project-local ordinal, to recognize only the actually removed slot.
        const providerSlot = Number(target.provider_worker_slot ?? target.global_worker_slot);
        if ((!target.provider || target.provider === "chatgpt") && Number.isInteger(providerSlot) &&
            providerSlot >= 1 && providerSlot <= 128)
          lastChatGPTProviderSlot.set(key, providerSlot);
        candidateRemovals.delete(key);
        // Paused workers don't generate and must NOT be recreated. Existing
        // paused tabs remain untouched if Firefox happened to restore them.
        if (target.desired_state === "paused" || target.desired_state === "draining" ||
            (target.active !== true && !restorableWithoutLease.has(key))) continue;
        // Wait for two valid backend snapshots to allow Firefox sessionstore
        // to finish restoring tabs. Then create exactly the missing slot.
        // The v2 in-page runner alone decides whether a prompt is permitted.
        if (!ownership.has(key) && Number(recoveryObservations.get(key) || 0) >= 2)
          await createWorkerTab(key, target, windowId, payload.projects, desired);
      }
      // Saved ChatGPT pool capacity owns exactly one Firefox tab per slot,
      // even while the queue allocator has not assigned a project. Unassigned
      // slots are parked (no zcloud_worker identity, so no prompt dispatch).
      // This is the missing path for "2 -> 3 workers, click Opslaan".
      const poolCount = Number(payload.dynamic_workers?.chatgpt_count);
      if (Number.isInteger(poolCount) && poolCount >= 0 && poolCount <= 16) {
        const used = new Set();
        for (const target of desired.values()) {
          const slot = Number(target.provider_worker_slot ?? target.global_worker_slot);
          if ((!target.provider || target.provider === "chatgpt") &&
              Number.isInteger(slot) && slot >= 1 && slot <= poolCount)
            used.add(slot);
        }
        for (const [key] of ownership) {
          const slot = Number(lastChatGPTProviderSlot.get(key));
          if (Number.isInteger(slot) && slot >= 1 && slot <= poolCount) used.add(slot);
        }
        let missingAssigned = 0;
        for (const key of desired.keys()) if (!ownership.has(key)) missingAssigned++;
        const missingCapacity = Math.max(0, poolCount -
          ownership.size - reserves.size - missingAssigned);
        // "Pool count" alone is not an operator request to manufacture
        // additional tabs on startup. Require a recent successful Save
        // that increased the ChatGPT count; this also prevents replay after
        // unrelated scheduler churn.
        let savedPoolExpansion = false;
        if (missingCapacity > 0) {
          try {
            const reply = await fetch(API +
              "/config-audit?key=runner.dynamic_worker_settings&target=portfolio&limit=10",
              {cache:"no-store"});
            if (reply.ok) {
              const audit = await reply.json();
              const latest = (audit?.items || []).find(x =>
                x.config_key === "runner.dynamic_worker_settings" &&
                x.target === "portfolio");
              const before = Number(latest?.old_value?.chatgpt_count);
              const after = Number(latest?.new_value?.chatgpt_count);
              const at = Date.parse(latest?.ts);
              savedPoolExpansion = latest?.result === "succeeded" &&
                Number.isInteger(before) && Number.isInteger(after) &&
                before >= 0 && after > before && after === poolCount &&
                Number.isFinite(at) && Date.now()-at >= 0 &&
                Date.now()-at < 10*60*1000;
            }
          } catch (_) { /* No new tabs without a verified user Save. */ }
        }
        let newReserves = 0;
        for (let slot = 1; savedPoolExpansion && slot <= poolCount && newReserves < missingCapacity; slot++) {
          if (used.has(slot) || reserves.has(slot)) continue;
          const observations = (reserveObservations.get(slot) || 0) + 1;
          reserveObservations.set(slot, observations);
          if (observations < 2) {
            if (quickRecheckTimer === null && typeof setTimeout === "function")
              quickRecheckTimer = setTimeout(() => {
                quickRecheckTimer = null;
                reconcile();
              }, MISSING_TAB_RECHECK_MS);
            continue;
          }
          if (windowId === null) continue;
          const url = "https://chatgpt.com/?zcloud_pool=" + slot;
          const tab = await browser.tabs.create({url,windowId,active:false});
          reserves.set(slot,tab.id);
          try { await browser.sessions.setTabValue(tab.id,RESERVE_TAG,String(slot)); } catch (_) {}
          newReserves++;
          await note("worker-pool-tab-created",String(slot),"dashboard-saved-unassigned-capacity");
        }
        for (const slot of [...reserveObservations.keys()])
          if (used.has(slot) || reserves.has(slot) || slot > poolCount)
            reserveObservations.delete(slot);
        // Shrinking the pool never removes a real worker here. Only surplus
        // explicitly tagged reserve tabs may be retired after 3 stable polls,
        // and only when idle with no unsent text.
        for (const [slot, tabId] of reserves) {
          if (slot <= poolCount && !used.has(slot)) {
            reserveObservations.delete(slot);
            continue;
          }
          const seen = (reserveObservations.get(slot) || 0) + 1;
          reserveObservations.set(slot, seen);
          if (seen < 3) continue;
          const tab = await browser.tabs.get(tabId).catch(() => null);
          let tag = "";
          try { tag = String(await browser.sessions.getTabValue(tabId, RESERVE_TAG) || ""); }
          catch (_) {}
          if (!tab || reservedSlot(tab.url) !== slot ||
              tag !== String(slot) || !(await reserveIdle(tabId))) continue;
          await browser.tabs.remove(tabId);
          reserves.delete(slot);
          reserveObservations.delete(slot);
          await note("worker-pool-tab-retired",String(slot),"unneeded-empty-reserve");
        }
      }
      // Detect a *stalled running worker*, not an inactive browser tab.
      // Background/minimized tabs are healthy when their v2 poll timestamp
      // advances. Reload ONLY a verified idle, identity-matched tab after
      // two stale observations; never close or duplicate a generating tab.
      for (const [key, tabId] of ownership) {
        const target = desired.get(key);
        if (!target || !target.active || target.desired_state === "paused" ||
            target.desired_state === "draining") {
          stalePollObservations.delete(key);
          continue;
        }
        let probe = null;
        try {
          const [result] = await browser.tabs.executeScript(tabId, {code:"(() => { const e=document.documentElement; const b=document.querySelector('button[data-testid=\"stop-button\"],button[aria-label=\"Stop generating\"],button[aria-label=\"Stop response\"]'); const d=document.querySelector('#prompt-textarea,[contenteditable=\"true\"][role=\"textbox\"],textarea'); return {version:e?.getAttribute(\"data-zcloud-violentmonkey-ready\")||\"\",owner:e?.getAttribute(\"data-zcloud-v2-owner\")||\"\",lastPoll:Number(e?.getAttribute(\"data-zcloud-v2-last-poll\")||0),generating:!!b,draft:!!String(d?.value||d?.innerText||d?.textContent||\"\").trim()}; })()"});
          probe = result;
        } catch (_) { /* A loading/unresponsive tab is not proven idle. */ }
        if (!probe || !/^2\.0\.[0123]$/.test(probe.version) || probe.owner !== key ||
            !Number.isFinite(probe.lastPoll) || probe.lastPoll <= 0 ||
            Date.now() - probe.lastPoll < STALE_POLL_MS ||
            probe.generating || probe.draft) {
          stalePollObservations.delete(key);
          continue;
        }
        const observations = (stalePollObservations.get(key) || 0) + 1;
        stalePollObservations.set(key, observations);
        if (observations < 2 || Date.now() < Number(reloadCooldown.get(key) || 0)) continue;
        const tab = await browser.tabs.get(tabId).catch(() => null);
        if (!tab || await tagOf(tab) !== key || !isWorkerUrl(tab.url)) continue;
        // browser.tabs.reload does not activate/focus the tab or create a new
        // Firefox window. No active generation or composer draft is discarded.
        await browser.tabs.reload(tabId);
        reloadCooldown.set(key, Date.now() + RELOAD_COOLDOWN_MS);
        stalePollObservations.delete(key);
        await note("stalled-idle-worker-reloaded", key, "v2-poll-stale-and-generation-idle");
      }
      // Missing configuration alone is NOT deletion. Require three complete
      // snapshots plus the latest durable dashboard worker-count reduction,
      // with a confirmed idle generation and an exact per-tab session tag.
      for (const [key] of ownership) {
        if (!desired.has(key)) {
          candidateRemovals.set(key,(candidateRemovals.get(key)||0)+1);
          await note("worker-preserved-unassigned",key,"await-explicit-removal");
        }
      }
      const waiting=[...candidateRemovals].filter(([id,count])=>count>=3&&!desired.has(id));
      if(waiting.length){
        let audit=null,poolAudit=null;
        try{
          const reply=await fetch(API+"/config-audit?key=runner.worker_count&limit=200",{cache:"no-store"});
          if(reply.ok)audit=await reply.json();
        }catch(_){}
        // Dashboard Opslaan changes the GLOBAL provider pool through a
        // different audit key than per-project worker_count. Never close a
        // tab for a generic allocation/preemption gap: require a recent,
        // successful pool reduction matching the backend's current count.
        try{
          const reply=await fetch(API+"/config-audit?key=runner.dynamic_worker_settings&target=portfolio&limit=20",{cache:"no-store"});
          if(reply.ok)poolAudit=await reply.json();
        }catch(_){}
        const poolChange=(poolAudit?.items||[]).find(row=>
          row.config_key==="runner.dynamic_worker_settings" && row.target==="portfolio");
        const oldPool=Number(poolChange?.old_value?.chatgpt_count);
        const newPool=Number(poolChange?.new_value?.chatgpt_count);
        const currentPool=Number(payload.dynamic_workers?.chatgpt_count);
        const poolChangeAt=Date.parse(poolChange?.ts);
        const poolReduced=poolChange?.result==="succeeded" &&
          Number.isInteger(oldPool) && Number.isInteger(newPool) &&
          Number.isInteger(currentPool) && oldPool>newPool && newPool>=0 &&
          currentPool===newPool && Number.isFinite(poolChangeAt) &&
          Date.now()-poolChangeAt>=0 && Date.now()-poolChangeAt<10*60*1000;
        const latest=new Map();
        for(const entry of audit?.items||[]){
          if(entry.config_key!=="runner.worker_count"||latest.has(entry.target))continue;
          latest.set(entry.target,entry);
        }
        for(const [key] of waiting){
          const match=/^(.+)::w([1-9][0-9]*)$/.exec(key);
          if(!match)continue;
          const row=latest.get(match[1]);
          const oldCount=Number(row?.old_value),newCount=Number(row?.new_value);
          const recent=Number.isFinite(Date.parse(row?.ts))&&Date.now()-Date.parse(row.ts)>=0&&Date.now()-Date.parse(row.ts)<10*60*1000;
          const explicitlyReduced=row?.result==="succeeded"&&
            oldCount>newCount&&Number(match[2])>newCount&&recent;
          const previousProviderSlot=lastChatGPTProviderSlot.get(key);
          const explicitlyRemovedPoolSlot=poolReduced &&
            Number.isInteger(previousProviderSlot) &&
            previousProviderSlot>newPool && previousProviderSlot<=oldPool;
          // Pausing, draining, or an active backend identity does not grant
          // permission to remove a worker, even if Save recently ran.
          const current=payload.projects?.[key];
          if((!explicitlyReduced&&!explicitlyRemovedPoolSlot) ||
             current?.active===true || current?.assignment_ready===true ||
             current?.desired_state==="paused" || current?.desired_state==="draining")continue;
          const tabId=ownership.get(key);
          if(tabId==null)continue;
          const tab=await browser.tabs.get(tabId).catch(()=>null);
          if(!tab||await tagOf(tab)!==key)continue;
          let idle=false;
          try{
            const [state]=await browser.tabs.executeScript(tabId,{
              code:'(() => { const e=document.documentElement; const box=document.querySelector(\'#prompt-textarea,[contenteditable="true"][role="textbox"],textarea\'); return {owner:e?.getAttribute("data-zcloud-v2-owner")||"",version:e?.getAttribute("data-zcloud-violentmonkey-ready")||"",lastPoll:Number(e?.getAttribute("data-zcloud-v2-last-poll")||0),generating:!!document.querySelector(\'button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop response"]\'),draft:!!String(box?.value||box?.innerText||box?.textContent||"").trim()}; })()'
            });
            idle=state?.owner===key && /^2\.0\.[0123]$/.test(state.version) &&
              state.generating===false && state.draft===false &&
              Number.isFinite(state.lastPoll) && state.lastPoll>0 &&
              Date.now()-state.lastPoll>=0 && Date.now()-state.lastPoll<120000;
          }catch(_){}
          if(!idle){await note("remove-deferred",key,"generation-or-idle-unverified");continue}
          await browser.tabs.remove(tabId);
          ownership.delete(key);
          candidateRemovals.delete(key);
          await forgetWorker(key);
          lastChatGPTProviderSlot.delete(key);
          await note("worker-tab-removed",key,explicitlyReduced?"verified-project-count-decrease":"verified-provider-pool-decrease");
        }
      }
      lastSnapshot = {at:Date.now(),workers:[...desired.keys()],tabs:[...ownership],reserves:[...reserves],
        slots:[...desired.values()].map(t=>[String(t.project_id),
          Number(t.global_worker_slot||0)])};
    } catch (error) {
      await note("worker-reconcile-failed",null,String(error));
      // API failure never authorizes tab deletion or another browser window.
    } finally { busy = false; }
  }
  // An intentional user removal from zCloud must carry an explicit worker
  // identity; this runtime message is private to the extension, not webpages.
  browser.runtime.onMessage.addListener(async (message, sender) => {
    if (message?.type !== "zcloud-v2-remove-worker") return undefined;
    // Do not accept arbitrary website messages for destructive actions.
    if (sender?.tab?.id != null || sender?.id !== browser.runtime.id)
      return {ok:false,reason:"extension-only-command"};
    const key = String(message.workerId || "");
    const tabId = ownership.get(key);
    if (!tabId || !candidateRemovals.has(key) ||
        candidateRemovals.get(key) < 3) return {ok:false,reason:"no-confirmed-removal"};
    const tab = await browser.tabs.get(tabId).catch(()=>null);
    if (!tab || await tagOf(tab) !== key) return {ok:false,reason:"tab-owner-mismatch"};
    await browser.tabs.remove(tabId);
    ownership.delete(key);
    candidateRemovals.delete(key);
    await forgetWorker(key);
    return {ok:true,workerId:key};
  });
  browser.tabs.onRemoved.addListener(tabId => {
    for (const [id, ownedTab] of ownership)
      if (ownedTab === tabId) {
        ownership.delete(id);
        stalePollObservations.delete(id);
        closeCooldown.set(id, Date.now() + 30000);
      }
  });
  browser.tabs.onUpdated.addListener((tabId, details, tab) => {
    if (!ownership.size) return;
    const key = [...ownership.entries()].find(([,tid]) => tid===tabId)?.[0];
    if (key && details.url && !isWorkerUrl(details.url))
      note("worker-navigated-outside-chatgpt",key,"preserving-tab");
  });
  // Browser restarts: Firefox restores windows/tabs; durable worker slots
  // reconstruct any assigned tabs that sessionstore failed to restore.
  // This controller NEVER starts Firefox and NEVER calls windows.create.
  reconcile();
  setInterval(reconcile,TICK_MS);
  // Cheap saved-count probe makes Opslaan visible without waiting 30 seconds.
  // Full allocation reads happen only when the count actually changes.
  let lastSavedPoolCount = null;
  let poolProbeBusy = false;
  async function checkSavedPoolCount() {
    if (poolProbeBusy) return;
    poolProbeBusy = true;
    try {
      const response = await fetch(API + "/dynamic-workers", {cache:"no-store"});
      if (!response.ok) return;
      const payload = await response.json();
      const count = Number(payload?.dynamic_workers?.chatgpt_count);
      if (!Number.isInteger(count) || count < 0 || count > 16) return;
      if (lastSavedPoolCount !== null && count !== lastSavedPoolCount)
        await reconcile();
      lastSavedPoolCount = count;
    } catch (_) { /* No tab mutation on a failed config probe. */ }
    finally { poolProbeBusy = false; }
  }
  checkSavedPoolCount();
  setInterval(checkSavedPoolCount, POOL_SAVE_CHECK_MS);
  // One inexpensive central command watcher, not a heavy 2-second
  // /runner-targets poll in every ChatGPT tab. React immediately to Forceer
  // and project switches, but only the verified v2 page runner sends text.
  const commandWakeAt = new Map();
  async function forceNewWorkerTab(key, commandId, active) {
    const commandKey = Number(commandId);
    if (!Number.isSafeInteger(commandKey) || commandKey <= 0 ||
        !active.includes(key)) return null;
    const previous = ownership.get(key);
    const existing = previous == null ? null :
      await browser.tabs.get(previous).catch(() => null);
    // A Force command is one-shot even if the new tab navigates to /c/... .
    // Its URL ticket is no longer visible after ChatGPT's SPA transition.
    if (existing && forceTabAt.get(commandKey) === previous) return previous;
    if (existing) {
      try {
        const current = new URL(existing.url);
        if (current.searchParams.get("zcloud_worker") === key &&
            current.searchParams.get("zcloud_force_command") === String(commandKey))
          return previous;
      } catch (_) {}
      if (await tagOf(existing) !== key || !isWorkerUrl(existing.url)) return null;
      // A Force never interrupts generation, a draft, pending send, or an
      // unverified/missing userscript. Retry when the worker becomes safe.
      let state = null;
      try {
        [state] = await browser.tabs.executeScript(previous, {
          code:'(() => {const e=document.documentElement;const b=document.querySelector(\'#prompt-textarea,[contenteditable="true"][role="textbox"],textarea\');return {owner:e?.getAttribute("data-zcloud-v2-owner")||"",version:e?.getAttribute("data-zcloud-violentmonkey-ready")||"",poll:Number(e?.getAttribute("data-zcloud-v2-last-poll")||0),busy:e?.getAttribute("data-zcloud-v2-busy"),generating:!!document.querySelector(\'button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop response"]\'),draft:!!String(b?.value||b?.innerText||b?.textContent||"").trim()};})()'
        });
      } catch (_) { return null; }
      if (!state || state.owner !== key || !/^2\.0\.[0123]$/.test(state.version) ||
          state.busy !== "false" || state.generating !== false ||
          state.draft !== false || !Number.isFinite(state.poll) ||
          state.poll <= 0 || Date.now()-state.poll < 0 ||
          Date.now()-state.poll >= 120000) return null;
    }
    const allTabs = await browser.tabs.query({});
    const windowId = await chooseWindow(allTabs);
    if (windowId == null) return null;
    // Pre-create and tag the replacement before retiring old ownership.
    // The old tab cannot consume Force: only the tab with this command ID can.
    const url = new URL("https://chatgpt.com/");
    url.searchParams.set("zcloud_worker",key);
    url.searchParams.set("zcloud_force_command",String(commandKey));
    const tab = await browser.tabs.create({url:String(url),windowId,active:false});
    try {
      await browser.sessions.setTabValue(tab.id,TAG,key);
      await rememberWorker(key);
    } catch (error) {
      await browser.tabs.remove(tab.id).catch(() => {});
      await note("force-new-tab-tag-failed",key,String(error));
      return null;
    }
    if (previous != null && existing) {
      // A trusted idle tab can be closed only AFTER its successor was tagged.
      await browser.tabs.remove(previous).catch(() => {});
    }
    ownership.set(key,tab.id);
    stalePollObservations.delete(key);
    closeCooldown.delete(key);
    forceTabAt.set(commandKey,tab.id);
    await note("worker-force-tab-created",key,"command="+commandKey);
    return tab.id;
  }
  const forceTabAt = new Map(); // command ID -> one newly bound tab; no repeated churn
  let commandProbeBusy = false;
  async function checkPendingCommands() {
    if (commandProbeBusy) return;
    commandProbeBusy = true;
    try {
      const response = await fetch(API + "/runner-commands?limit=100",{cache:"no-store"});
      if (!response.ok) return;
      const payload = await response.json();
      const pending = (Array.isArray(payload?.commands) ? payload.commands : [])
        .filter(x => x && (x.action === "start" || x.action === "force_start" || x.action === "push") &&
          Number.isInteger(Number(x.id)) && Number(x.id) > 0);
      if (!pending.length) return;
      // Pending commands are not proof of a granted queue lease. Reconcile
      // existing assignments and create missing owned tabs if authorized.
      await reconcile();
      const active = lastSnapshot?.workers || [];
      for (const cmd of pending) {
        const id = Number(cmd.id);
        if (Date.now() < (commandWakeAt.get(id) || 0)) continue;
        const projectId = String(cmd.project_id || "");
        const wantedSlot=Number(cmd.global_worker_slot||0);
        // Exact slot ownership matters when a second LightUp worker starts:
        // never wake a generating w1 in another slot by mistake.
        const slotEntry=wantedSlot?
          (lastSnapshot?.slots||[]).find(([worker,slot])=>
            slot===wantedSlot && (worker===projectId || worker.startsWith(projectId+"::w"))) : null;
        const key=wantedSlot ? (slotEntry?.[0]||"") :
          (ownership.has(projectId) ? projectId :
           active.includes(projectId+"::w1") ? projectId+"::w1" : "");
        if (!key) continue;
        // Force requests demand a NEW tab, not a recycled project tab.
        // The command ID travels in the URL and the in-page runner checks it
        // before processing the corresponding force action.
        const tabId = cmd.force === true ?
          await forceNewWorkerTab(key,id,active) : ownership.get(key);
        if (tabId == null) continue;
        // A wakeup only asks the userscript to check authoritative API
        // state. It never sends a prompt or selects a Firefox tab itself.
        const tab = await browser.tabs.get(tabId).catch(() => null);
        if (!tab || await tagOf(tab) !== key || !isWorkerUrl(tab.url)) continue;
        try {
          await browser.tabs.executeScript(tabId, {
            code:'document.documentElement?.dispatchEvent(new Event("zcloud-v2-wake"));'
          });
          commandWakeAt.set(id,Date.now()+5000);
        } catch (_) {}
      }
      for (const id of [...commandWakeAt.keys()])
        if (!pending.some(x => Number(x.id) === id)) commandWakeAt.delete(id);
      for (const id of [...forceTabAt.keys()])
        if (!pending.some(x => Number(x.id) === id)) forceTabAt.delete(id);
    } catch (_) { /* A failed API call cannot authorize prompt dispatch. */ }
    finally { commandProbeBusy = false; }
  }
  setInterval(checkPendingCommands, 2500);
  globalThis.ZCLOUD_V2_CONTROLLER = {snapshot:()=>lastSnapshot};
})();