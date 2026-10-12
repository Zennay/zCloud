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
    try {
      const value = await browser.sessions.getTabValue(tab.id, TAG);
      if (value) return String(value);
    } catch (_) {}
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
      // Workers are never moved between tabs. Existing tabs are preferred.
      for (const [key, target] of desired) {
        candidateRemovals.delete(key);
        if (!ownership.has(key)) await createWorkerTab(key,target,windowId);
      }
      // Do not close a worker tab on a temporary loss of its target, status
      // mismatch, pause or endpoint timeout. Removal requires an EXPLICIT
      // durable worker deletion signal, not inferred from allocation churn.
      for (const [key] of ownership) {
        if (!desired.has(key)) {
          candidateRemovals.set(key,(candidateRemovals.get(key)||0)+1);
          await note("worker-preserved-unassigned",key,"await-explicit-removal");
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