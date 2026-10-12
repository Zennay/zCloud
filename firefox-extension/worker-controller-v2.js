/* zCloud worker-controller v2: the ONLY Firefox tab lifecycle owner.
 * Never touch a non-owned tab, select an active tab or open a new window.
 * Temporary API failures/invalid assignments never imply worker removal.
 */
(() => {
  "use strict";
  const API = "http://127.0.0.1:8765/api";
  const TAG = "zcloud-v2-worker-key";
  const TICK_MS = 30000;
  const ownership = new Map(); // worker key -> tab ID (same tab across chats)
  const candidateRemovals = new Map();
  const closeCooldown = new Map();
  let workerWindow = null;
  let busy = false;
  let lastSnapshot = null;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function isWorkerUrl(url) {
    try { return new URL(url).hostname === "chatgpt.com"; } catch (_) { return false; }
  }
  function workerMarker(url) {
    try { return new URL(url).searchParams.get("zcloud_worker") || ""; } catch (_) { return ""; }
  }
  function ready(t) {
    if (!t || t.active !== true || t.assignment_ready !== true ||
        t.desired_state === "paused" || t.desired_state === "draining") return false;
    const key = String(t.project_id || "");
    const count = Number(t.global_worker_count || 0);
    const slot = Number(t.global_worker_slot || 0);
    return !!key && count >= 1 && slot >= 1 && slot <= count &&
      !!String(t.queue_item?.queue_id || "") && String(t.prompt || "").startsWith("Werk verder aan ");
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
  async function note(event, id, reason) {
    // Status only: never use a synthetic user prompt to report failure.
    console.log("[zCloud v2]", event, id || "", reason || "");
  }
  async function recoverOwnership() {
    const tabs = await browser.tabs.query({});
    const liveIds = new Set(tabs.map(t => t.id));
    for (const [id, tabId] of ownership) if (!liveIds.has(tabId)) {
      ownership.delete(id);
      closeCooldown.set(id, Date.now() + 30000);
    }
    const claimed = new Set(ownership.values());
    for (const tab of tabs) {
      if (claimed.has(tab.id) || !isWorkerUrl(tab.url)) continue;
      const id = await tagOf(tab);
      if (!id || ownership.has(id)) continue;
      ownership.set(id, tab.id);
      claimed.add(tab.id);
      try { await browser.sessions.setTabValue(tab.id, TAG, id); } catch (_) {}
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
    await note("worker-tab-created", key, "allocated-new-worker");
  }
  async function reconcile() {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch(API + "/runner-targets", {cache:"no-store"});
      if (!response.ok) throw new Error("targets-http-" + response.status);
      const payload = await response.json();
      if (!payload || typeof payload.projects !== "object" || !payload.projects)
        throw new Error("invalid-target-payload");
      const tabs = await recoverOwnership();
      const desired = new Map(Object.values(payload.projects).filter(ready).map(t => [t.project_id,t]));
      const windowId = await chooseWindow(tabs);
      // Never start additional workers while any existing tagged worker is
      // still running the old Violentmonkey script. The previous v1 script
      // could send prompts in Medium and restart chats on stale commands.
      // A cold empty window may bootstrap ONE tab, then prove the installed
      // v2 script before provisioning the next one.
      // If this profile already contains unbound ChatGPT tabs, assume they
      // may be legacy workers or an expert. Never provision a duplicate
      // until identity is proven. A genuinely empty profile can bootstrap.
      const unclaimedChatTabs=tabs.some(t=>isWorkerUrl(t.url) &&
        ![...ownership.values()].includes(t.id));
      let canProvision=ownership.size>0||!unclaimedChatTabs,
          bootstrapped=ownership.size===0;
      for(const [key,tabId] of ownership){
        try{
          const result=await browser.tabs.executeScript(tabId,{
            code:'document.documentElement?.getAttribute("data-zcloud-violentmonkey-ready") || ""'
          });
          if(!Array.isArray(result)||String(result[0]||"")!=="2.0.0"){
            canProvision=false;
            await note("v2-provision-deferred",key,"old-or-unverified-monkeyscript");
          }
        }catch(_){
          canProvision=false;
          await note("v2-provision-deferred",key,"script-version-not-readable");
        }
      }
      // Workers are never moved between tabs. Existing tabs are preferred.
      for (const [key, target] of desired) {
        candidateRemovals.delete(key);
        if(!ownership.has(key) && canProvision){
          await createWorkerTab(key,target,windowId);
          if(bootstrapped)canProvision=false; // one cold-start tab per check
        }
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
    return {ok:true,workerId:key};
  });
  browser.tabs.onRemoved.addListener(tabId => {
    for (const [id, ownedTab] of ownership)
      if (ownedTab === tabId) {
        ownership.delete(id);
        closeCooldown.set(id, Date.now() + 30000);
      }
  });
  browser.tabs.onUpdated.addListener((tabId, details, tab) => {
    if (!ownership.size) return;
    const key = [...ownership.entries()].find(([,tid]) => tid===tabId)?.[0];
    if (key && details.url && !isWorkerUrl(details.url))
      note("worker-navigated-outside-chatgpt",key,"preserving-tab");
  });
  // Browser restarts: Firefox restores windows/tabs; tags restore bindings.
  // This controller NEVER starts Firefox and NEVER calls windows.create.
  reconcile();
  setInterval(reconcile,TICK_MS);
  globalThis.ZCLOUD_V2_CONTROLLER = {snapshot:()=>lastSnapshot};
})();