/* zCloud worker-controller v2: the ONLY Firefox tab lifecycle owner.
 * Never touch a non-owned tab, select an active tab or open a new window.
 * Temporary API failures/invalid assignments never imply worker removal.
 */
(() => {
  "use strict";
  const API = "http://127.0.0.1:8765/api";
  const TAG = "zcloud-v2-worker-key";
  const KNOWN_KEY = "zcloud-v2-known-worker-ids";
  const TICK_MS = 30000;
  const STALE_POLL_MS = 4 * 60 * 1000;
  const RELOAD_COOLDOWN_MS = 10 * 60 * 1000;
  const ownership = new Map(); // worker key -> tab ID (same tab across chats)
  const candidateRemovals = new Map();
  const closeCooldown = new Map();
  const stalePollObservations = new Map();
  const reloadCooldown = new Map();
  let workerWindow = null;
  let busy = false;
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
  // A paused or draining assignment STILL owns its Firefox tab. The page
  // runner controls dispatch; the extension must restore the tab, not run it.
  function assigned(t) {
    if (!t || t.assignment_ready !== true) return false;
    const key = String(t.project_id || "");
    const count = Number(t.global_worker_count || 0);
    const slot = Number(t.global_worker_slot || 0);
    return /^[a-z0-9_-]+::w[1-9][0-9]*$/i.test(key) &&
      count >= 1 && slot >= 1 && slot <= count &&
      !!String(t.queue_item?.queue_id || "");
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
  async function createWorkerTab(key, target, windowId) {
    if (Date.now() < Number(closeCooldown.get(key) || 0)) return;
    if (windowId == null || ownership.has(key)) return;
    const url = new URL("https://chatgpt.com/");
    url.searchParams.set("zcloud_worker", key);
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
      const desired = new Map(Object.values(payload.projects).filter(assigned).map(t => [t.project_id,t]));
      const windowId = await chooseWindow(tabs);
      // The authenticated local backend is the authority for active worker
      // identities. A restored personal/expert ChatGPT tab must not block
      // creation of an unrelated, missing worker tab. Conversely, we NEVER
      // adopt an untagged ChatGPT tab by its position or conversation URL.
      for (const key of desired.keys()) {
        if (ownership.has(key)) { recoveryObservations.delete(key); continue; }
        recoveryObservations.set(key, (recoveryObservations.get(key) || 0) + 1);
      }
      for (const [key, target] of desired) {
        candidateRemovals.delete(key);
        // Paused workers don't generate and must NOT be recreated. Existing
        // paused tabs remain untouched if Firefox happened to restore them.
        if (target.desired_state === "paused" || target.active !== true) continue;
        // Wait for two valid backend snapshots to allow Firefox sessionstore
        // to finish restoring tabs. Then create exactly the missing slot.
        // The v2 in-page runner alone decides whether a prompt is permitted.
        if (!ownership.has(key) && Number(recoveryObservations.get(key) || 0) >= 2)
          await createWorkerTab(key, target, windowId);
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
        if (!probe || probe.version !== "2.0.0" || probe.owner !== key ||
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
        let audit=null;
        try{
          const reply=await fetch(API+"/config-audit?key=runner.worker_count&limit=200",{cache:"no-store"});
          if(reply.ok)audit=await reply.json();
        }catch(_){}
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
          const recent=Number.isFinite(Date.parse(row?.ts))&&Date.now()-Date.parse(row.ts)<10*60*1000;
          const explicitlyReduced=row?.result==="succeeded"&&
            oldCount>newCount&&Number(match[2])>newCount&&recent;
          if(!explicitlyReduced||payload.projects?.[key])continue;
          const tabId=ownership.get(key);
          if(tabId==null)continue;
          const tab=await browser.tabs.get(tabId).catch(()=>null);
          if(!tab||await tagOf(tab)!==key)continue;
          let idle=false;
          try{
            const [running]=await browser.tabs.executeScript(tabId,{
              code:'!document.querySelector(\'button[data-testid="stop-button"],button[aria-label="Stop generating"]\')'
            });
            idle=running===true;
          }catch(_){}
          if(!idle){await note("remove-deferred",key,"generation-or-idle-unverified");continue}
          await browser.tabs.remove(tabId);
          ownership.delete(key);
          candidateRemovals.delete(key);
          await forgetWorker(key);
          await note("worker-tab-removed",key,"verified-dashboard-worker-count-decrease");
        }
      }
      lastSnapshot = {at:Date.now(),workers:[...desired.keys()],tabs:[...ownership]};
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
  globalThis.ZCLOUD_V2_CONTROLLER = {snapshot:()=>lastSnapshot};
})();