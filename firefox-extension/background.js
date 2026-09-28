const API = "http://127.0.0.1:8765/api";
const targets = Object.create(null);
const tabTargets = Object.create(null);
const projectTabs = Object.create(null);
const pendingAdoptions = Object.create(null);
const runningActions = new Set();
const processedCommands = new Set();
const intentionalTabClosures = new Set();
const pendingTabHandoffs = new Set();
const REPLACEMENT_HANDOFF_SESSION_KEY = "zcloud-replacement-handoff-v1";
const Recovery = globalThis.ZCloudRecovery;
if (!Recovery) throw new Error("zCloud recovery helper ontbreekt");

async function setRecoveryTag(tabId, projectId) {
  if (tabId == null || !projectId) return;
  try { await browser.sessions.setTabValue(tabId, Recovery.SESSION_KEY, projectId); } catch (_) {}
}

async function clearRecoveryTag(tabId) {
  if (tabId == null) return;
  try { await browser.sessions.removeTabValue(tabId, Recovery.SESSION_KEY); } catch (_) {}
}

async function setReplacementHandoffTag(tabId, handoff) {
  if (tabId == null || !handoff) return;
  try { await browser.sessions.setTabValue(tabId, REPLACEMENT_HANDOFF_SESSION_KEY, handoff); } catch (_) {}
}

async function getReplacementHandoffTag(tabId) {
  if (tabId == null) return null;
  try { return await browser.sessions.getTabValue(tabId, REPLACEMENT_HANDOFF_SESSION_KEY) || null; } catch (_) { return null; }
}

async function clearReplacementHandoffTag(tabId) {
  if (tabId == null) return;
  try { await browser.sessions.removeTabValue(tabId, REPLACEMENT_HANDOFF_SESSION_KEY); } catch (_) {}
}

function compactClaimForHandoff(claim) {
  if (!claim) return null;
  const metadata = claim.metadata && typeof claim.metadata === "object" ? claim.metadata : {};
  return {
    project_id: claim.project_id || "",
    claim_key: claim.claim_key || "",
    owner_id: claim.owner_id || "",
    worker_id: claim.worker_id || "",
    lease_until: claim.lease_until || "",
    task: String(metadata.task || "").slice(0, 500),
    notion_task: String(metadata.notion_task || "").slice(0, 500),
    branch: String(metadata.branch || "").slice(0, 300),
    scope: String(metadata.scope || "").slice(0, 500),
    conflict_scope: metadata.conflict_scope && typeof metadata.conflict_scope === "object"
      ? metadata.conflict_scope : null
  };
}

async function prepareReplacementHandoff(target, reason) {
  if (!target?.project_id) throw new Error("Workerconfig ontbreekt voor replacement handoff");
  const baseProjectId = target.base_project_id || target.project_id.split("::w", 1)[0];
  const response = await fetch(API + "/task-claims?project=" + encodeURIComponent(baseProjectId), {cache: "no-store"});
  if (!response.ok) throw new Error("Claimcontext niet beschikbaar (HTTP " + response.status + ")");
  const data = await response.json();
  const claims = (data.claims || []).filter(claim => claim?.worker_id === target.project_id);
  if (claims.length > 1) throw new Error("Replacement geblokkeerd: meerdere actieve taakclaims voor dezelfde worker");
  const claim = claims.length === 1 ? compactClaimForHandoff(claims[0]) : null;
  if (claim && (!claim.claim_key || !claim.owner_id || !claim.worker_id)) {
    throw new Error("Replacement geblokkeerd: actieve claimcontext is onvolledig");
  }
  return {
    version: 1,
    reason: String(reason || "project-chat-replaced").slice(0, 120),
    prepared_at: new Date().toISOString(),
    project_id: target.project_id,
    base_project_id: baseProjectId,
    previous_conversation_id: target.conversation_id || "",
    claim: claim
  };
}

async function closeRunnerTab(tabId) {
  if (tabId == null) return;
  intentionalTabClosures.add(tabId);
  try {
    await browser.tabs.remove(tabId);
  } catch (_) {
    intentionalTabClosures.delete(tabId);
  }
}

async function recoverClosedWorker(target, reason = "unexpected-tab-closed") {
  if (!target?.project_id || pendingTabHandoffs.has(target.project_id)) return;
  if (projectTabs[target.project_id] != null) return;
  if (!target.active || target.desired_state === "paused" || target.desired_state === "draining") return;
  pendingTabHandoffs.add(target.project_id);
  postStatus({
    projectId: target.project_id,
    baseProjectId: target.base_project_id,
    workerSlot: target.worker_slot,
    projectName: target.name,
    target: target.url,
    event: "worker-handoff-started",
    reason,
    at: new Date().toISOString()
  });
  try {
    const opened = await browser.tabs.create({url: target.url || "https://chatgpt.com/", active: false});
    tabTargets[opened.id] = target;
    projectTabs[target.project_id] = opened.id;
    await setRecoveryTag(opened.id, target.project_id);
    if (target.replacement_handoff) await setReplacementHandoffTag(opened.id, target.replacement_handoff);
    if (!target.conversation_id) pendingAdoptions[opened.id] = target.project_id;
    postStatus({
      projectId: target.project_id,
      baseProjectId: target.base_project_id,
      workerSlot: target.worker_slot,
      projectName: target.name,
      target: target.url,
      event: "worker-handoff-opened",
      reason,
      at: new Date().toISOString(),
      tabId: opened.id
    });
  } catch (error) {
    postStatus({
      projectId: target.project_id,
      baseProjectId: target.base_project_id,
      workerSlot: target.worker_slot,
      projectName: target.name,
      target: target.url,
      event: "worker-handoff-failed",
      reason,
      error: String(error?.message || error),
      at: new Date().toISOString()
    });
  } finally {
    pendingTabHandoffs.delete(target.project_id);
  }
}

async function sessionAssignments(tabs) {
  const out = Object.create(null);
  await Promise.all((tabs || []).map(async tab => {
    if (tab?.id == null) return;
    try {
      const value = await browser.sessions.getTabValue(tab.id, Recovery.SESSION_KEY);
      if (value) out[tab.id] = String(value);
    } catch (_) {}
  }));
  return out;
}

async function adoptConversation(tabId, target, url) {
  const conversationId = Recovery.conversationFromUrl(url);
  if (!conversationId || !target || projectTabs[target.project_id] !== tabId) return false;
  target.conversation_id = conversationId;
  target.url = "https://chatgpt.com/c/" + conversationId;
  targets[target.project_id] = target;
  delete pendingAdoptions[tabId];
  await setRecoveryTag(tabId, target.project_id);
  await postStatus({
    projectId: target.project_id,
    baseProjectId: target.base_project_id,
    workerSlot: target.worker_slot,
    projectName: target.name,
    target: target.url,
    targetConversation: conversationId,
    event: "conversation-adopted",
    reason: "persistent-tab-recovery",
    at: new Date().toISOString(),
    tabId: tabId
  });
  return true;
}

function workerKeysFor(projectId, activeOnly = false) {
  if (targets[projectId]) return (!activeOnly || targets[projectId].active) ? [projectId] : [];
  return Object.values(targets)
    .filter(t => (t.base_project_id || t.project_id) === projectId && (!activeOnly || t.active))
    .sort((a,b) => (a.worker_slot || 1) - (b.worker_slot || 1))
    .map(t => t.project_id);
}

function runProject(cfg) {
  const marker = "__ZC_RUNNER_" + cfg.projectId.replace(/[^a-z0-9]/gi, "");
  if (window[marker]) return;
  window[marker] = true;
  const REPLACEMENT_HANDOFF = cfg.replacement_handoff || null;
  const BASE_PROMPT = cfg.prompt;
  let PROMPT = REPLACEMENT_HANDOFF
    ? BASE_PROMPT + "\n\n" +
      "BEWUSTE WORKER-HANDOFF — je vervangt dezelfde zCloud-worker, niet de taak. " +
      "Neem GEEN nieuwe taakclaim zolang onderstaande bestaande claim nog geldig is. " +
      "Controleer vóór iedere write dat claim_key, owner_id en worker_id server-side nog exact overeenkomen; " +
      "heartbeat en release moeten dezelfde owner_id blijven gebruiken. " +
      "Als de claim ontbreekt, verlopen is of een andere owner heeft: doe alleen read-only werk, voer een verse coordination-preflight uit en claim pas daarna veilig opnieuw. " +
      "Handoff-context: " + JSON.stringify(REPLACEMENT_HANDOFF)
    : cfg.prompt;
  let replacementHandoffPending = !!REPLACEMENT_HANDOFF;
  const BASE_PROJECT = cfg.base_project_id || cfg.projectId;
  const SINGLE_RUN = BASE_PROJECT === "portfolio-review";
  let autoContinue = cfg.auto_continue !== false;
  let autoContinueDelayMs = Math.max(30000, Number(cfg.auto_continue_delay_seconds || 300) * 1000);
  const CHECK_MS = 5000;
  const STALL_MS = 20 * 60 * 1000;
  const STARTUP_IDLE_MS = 8000;
  const COMPOSER_RECOVERY_MS = 45 * 1000;
  let sawGeneration = false;
  let awaitingGeneration = false;
  let generationDeadline = 0;
  let finishedAt = 0;
  let finishSignalsReported = false;
  let lastText = "";
  let lastProgressAt = Date.now();
  let sending = false;
  let lastGenerating = null;
  let paused = false;
  let draining = cfg.desired_state === "draining";
  let recoveryRequested = false;
  const startedAt = Date.now();
  let lastPromptSentAt = 0;
  let lastStartupStatusAt = 0;
  let lastStartupAttemptAt = 0;
  let composerMissingSince = 0;
  let tickTimer = null;
  let heartbeatTimer = null;

  function stopButton() {
    return document.querySelector('button[data-testid="stop-button"]') ||
      [...document.querySelectorAll("button")].find(b => {
        const x = ((b.getAttribute("aria-label") || "") + " " + (b.textContent || "")).toLowerCase();
        return x.includes("stop") || x.includes("stoppen");
      });
  }
  function composer() {
    return document.querySelector("#prompt-textarea") ||
      document.querySelector('[contenteditable="true"][role="textbox"]') ||
      document.querySelector("textarea");
  }
  function composerText(box = composer()) {
    if (!box) return null;
    return box.tagName === "TEXTAREA" || box.tagName === "INPUT"
      ? (box.value || "").trim()
      : (box.innerText || box.textContent || "").trim();
  }
  function sendButton() {
    return document.querySelector('button[data-testid="send-button"]') ||
      [...document.querySelectorAll("button")].find(b => {
        const x = ((b.getAttribute("aria-label") || "") + " " + (b.textContent || "")).toLowerCase();
        return x.includes("send") || x.includes("verzenden");
      });
  }
  function assistantText() {
    const nodes = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
    return nodes.length ? (nodes[nodes.length - 1].innerText || "").trim() : "";
  }
  function statusPayload(event, extra = {}) {
    const text = assistantText();
    return {
      projectId: cfg.projectId,
      baseProjectId: BASE_PROJECT,
      workerSlot: cfg.worker_slot || 1,
      projectName: cfg.name,
      target: location.href,
      targetConversation: cfg.conversation_id,
      title: document.title,
      event: event,
      at: new Date().toISOString(),
      generating: !!stopButton(),
      sending: sending,
      progressAt: new Date(lastProgressAt).toISOString(),
      assistantCharacters: text.length,
      ...extra
    };
  }
  function status(event, extra = {}) {
    const payload = statusPayload(event, extra);
    window.__ZC_RUNNER_STATUS__ = payload;
    try { browser.runtime.sendMessage({type: "runner-status", payload: payload}).catch(() => {}); } catch (_) {}
  }
  async function syncStatus(event, extra = {}) {
    const payload = statusPayload(event, extra);
    window.__ZC_RUNNER_STATUS__ = payload;
    try { await browser.runtime.sendMessage({type: "runner-status-sync", payload: payload}); } catch (_) {}
  }
  async function canAutoContinue() {
    if (SINGLE_RUN) return false;
    try {
      const policy = await browser.runtime.sendMessage({type: "runner-policy-check", projectId: cfg.projectId});
      if (policy && typeof policy.auto_continue === "boolean") autoContinue = policy.auto_continue;
      if (policy && Number.isFinite(Number(policy.continue_delay_seconds))) {
        autoContinueDelayMs = Math.max(30000, Number(policy.continue_delay_seconds) * 1000);
      }
      return autoContinue;
    } catch (_) {
      autoContinue = false;
      return false;
    }
  }
  async function reportFinishSignals(text) {
    if (!text) return;
    if (text.includes("ZCLOUD_AUTONOMY: WAIT_VPS")) {
      await syncStatus("autonomy-wait-vps", {reason: "assistant-marker"});
    } else if (text.includes("ZCLOUD_AUTONOMY: WAIT_HUMAN")) {
      await syncStatus("autonomy-wait-human", {reason: "assistant-marker"});
    } else if (text.includes("ZCLOUD_AUTONOMY: COMPLETE")) {
      await syncStatus("autonomy-complete", {reason: "assistant-marker"});
    } else if (text.includes("ZCLOUD_AUTONOMY: CONTINUE")) {
      await syncStatus("autonomy-continue", {reason: "assistant-marker"});
    }
    if (BASE_PROJECT !== "cloud") return;
    if (text.includes("ZCLOUD_ITERATION_COMPLETE")) {
      await syncStatus("improvement-iteration-complete", {reason: "assistant-marker"});
    }
    if (text.includes("ZCLOUD_FINISH_REVIEW: GREEN_NO_P0P1")) {
      await syncStatus("improvement-review-green", {reason: "assistant-marker"});
    } else if (text.includes("ZCLOUD_FINISH_REVIEW: OPEN_P0P1")) {
      await syncStatus("improvement-review-open", {reason: "assistant-marker"});
    }
    if (text.includes("ZCLOUD_FINAL_AUDIT: GREEN")) {
      await syncStatus("improvement-audit-green", {reason: "assistant-marker"});
    } else if (text.includes("ZCLOUD_FINAL_AUDIT: FAIL")) {
      await syncStatus("improvement-audit-failed", {reason: "assistant-marker"});
    }
  }
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function fill(text) {
    const box = composer();
    if (!box) return false;
    box.focus();
    if (box.tagName === "TEXTAREA" || box.tagName === "INPUT") {
      const proto = box.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) setter.call(box, text); else box.value = text;
      box.dispatchEvent(new Event("input", {bubbles: true}));
    } else {
      document.execCommand("selectAll", false, null);
      document.execCommand("insertText", false, text);
      box.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: text}));
    }
    await sleep(700);
    return true;
  }
  async function send(reason) {
    if (paused || draining || sending || stopButton()) return false;
    const draft = composerText();
    if (draft === null) { status("send-blocked", {reason: "composer-missing"}); return false; }
    if (draft && draft !== PROMPT) { status("send-blocked", {reason: "draft-present"}); return false; }
    sending = true;
    try {
      const ok = draft === PROMPT || await fill(PROMPT);
      if (!ok) { status("send-blocked", {reason: "composer-missing"}); return false; }
      await sleep(700);
      const button = sendButton();
      if (!button || button.disabled) { status("send-blocked", {reason: "send-button-unavailable"}); return false; }
      button.click();
      if (replacementHandoffPending) {
        replacementHandoffPending = false;
        PROMPT = BASE_PROMPT;
        try {
          await browser.runtime.sendMessage({
            type: "runner-replacement-handoff-consumed",
            projectId: cfg.projectId
          });
        } catch (_) {}
        status("worker-replacement-handoff-consumed", {
          reason: "handoff-context-sent",
          claimKey: REPLACEMENT_HANDOFF?.claim?.claim_key || ""
        });
      }
      lastPromptSentAt = Date.now();
      lastProgressAt = Date.now();
      sawGeneration = false;
      awaitingGeneration = true;
      generationDeadline = Date.now() + 120000;
      finishedAt = 0;
      status("prompt-sent", {reason: reason});
      return true;
    } finally {
      await sleep(1000);
      sending = false;
    }
  }
  async function tick() {
    if (paused) return;
    const generating = !!stopButton();
    const text = assistantText();
    const now = Date.now();
    if (lastGenerating === null) {
      lastGenerating = generating;
      if (generating) {
        finishSignalsReported = false;
        status("generation-started");
        lastProgressAt = now;
      }
    } else if (generating !== lastGenerating) {
      status(generating ? "generation-started" : "generation-finished");
      lastGenerating = generating;
      if (generating) { finishSignalsReported = false; lastProgressAt = now; }
    }
    if (generating) {
      awaitingGeneration = false;
      sawGeneration = true;
      finishedAt = 0;
      if (text !== lastText) { lastText = text; lastProgressAt = now; status("generation-progress"); }
      if (now - lastProgressAt >= STALL_MS && !sending && !recoveryRequested) {
        if (!(await canAutoContinue())) {
          status("auto-continue-blocked", {reason: "finished-maintain"});
          return;
        }
        recoveryRequested = true;
        status("stall-detected", {reason: "no-response-progress-for-20m"});
        const stop = stopButton();
        if (stop) stop.click();
        await sleep(1200);
        browser.runtime.sendMessage({type: "runner-new-chat", projectId: cfg.projectId, reason: "stall-recovery"}).catch(() => {});
      }
      return;
    }
    if (awaitingGeneration) {
      if (text && text !== lastText) {
        awaitingGeneration = false;
        sawGeneration = true;
        finishedAt = now;
        finishSignalsReported = false;
        lastText = text;
        lastProgressAt = now;
        status("generation-started", {reason: "response-detected-between-polls"});
        status("generation-finished", {reason: "response-detected-between-polls"});
        return;
      }
      if (now < generationDeadline) return;
      awaitingGeneration = false;
      lastPromptSentAt = 0;
      status("generation-not-started", {reason: "no-generation-after-send"});
      return;
    }
    if (sawGeneration) {
      if (!finishedAt) { finishedAt = now; finishSignalsReported = false; return; }
      if (!finishSignalsReported && now - finishedAt >= 5000) {
        finishSignalsReported = true;
        lastText = text;
        await reportFinishSignals(text);
      }
      if (now - finishedAt >= autoContinueDelayMs) {
        sawGeneration = false;
        finishedAt = 0;
        lastText = text;
        if (draining) {
          paused = true;
          clearInterval(tickTimer);
          clearInterval(heartbeatTimer);
          status("runner-drained", {reason: "current-task-finished"});
          return;
        }
        if (SINGLE_RUN) { status("scheduled-run-complete", {reason: "single-run"}); return; }
        if (!paused && await canAutoContinue()) await send("autonomy-cooldown-complete");
        else if (!paused) status("auto-continue-blocked", {reason: "autonomy-gate-closed"});
      }
      return;
    }
    if (draining) {
      paused = true;
      clearInterval(tickTimer);
      clearInterval(heartbeatTimer);
      status("runner-drained", {reason: "already-idle"});
      return;
    }
    if (SINGLE_RUN) return;
    const draft = composerText();
    if (draft === null) {
      if (!composerMissingSince) composerMissingSince = now;
      if (now - lastStartupStatusAt >= 30000) {
        lastStartupStatusAt = now;
        status("startup-waiting", {reason: "composer-missing"});
      }
      if (now - composerMissingSince >= COMPOSER_RECOVERY_MS && !recoveryRequested) {
        if (!(await canAutoContinue())) {
          status("auto-continue-blocked", {reason: "finished-maintain"});
          return;
        }
        recoveryRequested = true;
        status("composer-stalled", {reason: "composer-missing-for-90s"});
        browser.runtime.sendMessage({type: "runner-new-chat", projectId: cfg.projectId, reason: "composer-missing"}).catch(() => {});
      }
      return;
    }
    composerMissingSince = 0;
    if (!SINGLE_RUN && !sending && now - startedAt >= STARTUP_IDLE_MS &&
        (!lastPromptSentAt || now - lastPromptSentAt >= 300000)) {
      if (draft === "" || draft === PROMPT) {
        if (now - lastStartupAttemptAt < 5000) return;
        if (!(await canAutoContinue())) {
          status("auto-continue-blocked", {reason: "finished-maintain"});
          return;
        }
        lastStartupAttemptAt = now;
        await send(lastPromptSentAt ? "idle-retry" : "startup-retry");
      } else if (draft !== PROMPT && now - lastStartupStatusAt >= 30000) {
        lastStartupStatusAt = now;
        status("startup-blocked", {reason: "draft-present"});
      }
    }
  }
  browser.runtime.onMessage.addListener(message => {
    if (!message || message.projectId !== cfg.projectId) return;
    if (message.type === "runner-push") {
      return (async () => {
        if (paused) return {ok: false, reason: "paused"};
        if (!(await canAutoContinue())) {
          status("auto-continue-blocked", {reason: "finished-maintain"});
          return {ok: false, reason: "improvement-finished"};
        }
        if (stopButton() || sending || awaitingGeneration) {
          status("push-skipped", {reason: "runner-busy"});
          return {ok: false, reason: "runner-busy"};
        }
        const draft = composerText();
        if (draft === null) return {ok: false, reason: "composer-missing"};
        if (draft && draft !== PROMPT) return {ok: false, reason: "draft-present"};
        const ok = await send(message.reason || "dashboard-push");
        return {ok: !!ok, reason: ok ? "prompt-sent" : "send-unavailable"};
      })();
    }
    if (message.type === "runner-drain") {
      draining = true;
      status("runner-draining", {reason: message.reason || "dashboard-drain"});
      return {ok: true, reason: "draining"};
    }
    if (message.type === "runner-stop") {
      paused = true;
      clearInterval(tickTimer);
      clearInterval(heartbeatTimer);
      const stop = stopButton();
      if (stop) stop.click();
      status("runner-stopped", {reason: message.reason || "project-chat-replaced"});
    }
  });
  status("runner-started");
  heartbeatTimer = setInterval(() => status("heartbeat"), 60000);
  setTimeout(async () => {
    if (paused) return;
    if (draining) { status("runner-draining", {reason: "restart-drain"}); return; }
    if (SINGLE_RUN) { status("scheduled-ready", {reason: "awaiting-daily-push"}); return; }
    if (!(await canAutoContinue())) { status("auto-continue-blocked", {reason: "finished-maintain"}); return; }
    if (stopButton()) { status("startup-blocked", {reason: "generation-active"}); return; }
    const draft = composerText();
    if (draft === null) { status("startup-waiting", {reason: "composer-missing"}); return; }
    if (draft && draft !== PROMPT) { status("startup-blocked", {reason: "draft-present"}); return; }
    send(draft === PROMPT ? "startup-adopted-draft" : "startup-idle").catch(error =>
      status("runner-error", {error: String(error?.message || error)})
    );
  }, STARTUP_IDLE_MS);
  tickTimer = setInterval(() => tick().catch(error =>
    status("runner-error", {error: String(error?.message || error)})
  ), CHECK_MS);
}

function postStatus(payload) {
  return fetch(API + "/runner-status", {method: "POST", mode: "no-cors", body: JSON.stringify(payload)}).catch(() => {});
}
async function refreshTargets() {
  try {
    const response = await fetch(API + "/runner-targets", {cache: "no-store"});
    if (!response.ok) throw new Error("config HTTP " + response.status);
    const data = await response.json();
    const incoming = data.projects || {};
    for (const id of Object.keys(targets)) {
      if (Object.prototype.hasOwnProperty.call(incoming, id)) continue;
      const tabId = projectTabs[id];
      if (tabId != null) {
        try { await browser.tabs.sendMessage(tabId, {type: "runner-stop", projectId: id, reason: "worker-count-reduced"}); } catch (_) {}
        await clearRecoveryTag(tabId);
        await closeRunnerTab(tabId);
        delete tabTargets[tabId];
        delete pendingAdoptions[tabId];
      }
      delete projectTabs[id];
      delete targets[id];
    }
    for (const [id, target] of Object.entries(incoming)) {
      targets[id] = {...target, projectId: target.project_id || id, projectName: target.name || id};
    }
    postStatus({event: "targets-loaded", at: new Date().toISOString(), reason: Object.keys(targets).join(",")});
    const tabs = await browser.tabs.query({url: "https://chatgpt.com/*"});
    const restoredAssignments = await sessionAssignments(tabs);
    const claimedTabIds = new Set(
      Object.values(projectTabs).filter(tabId => tabId != null)
    );
    for (const target of Object.values(targets)) {
      if (pendingTabHandoffs.has(target.project_id)) continue;
      const assignedTabId = projectTabs[target.project_id];
      if (!target.active) {
        if (assignedTabId != null) {
          try { await browser.tabs.sendMessage(assignedTabId, {type: "runner-stop", projectId: target.project_id, reason: "project-paused"}); } catch (_) {}
          await clearRecoveryTag(assignedTabId);
          await closeRunnerTab(assignedTabId);
          delete tabTargets[assignedTabId];
          delete projectTabs[target.project_id];
          delete pendingAdoptions[assignedTabId];
        }
        continue;
      }
      if (target.desired_state === "draining" && assignedTabId == null) {
        postStatus({projectId:target.project_id,baseProjectId:target.base_project_id,workerSlot:target.worker_slot,
          projectName:target.name,target:target.url,event:"runner-drained",reason:"already-idle",at:new Date().toISOString()});
        continue;
      }
      if (assignedTabId != null) {
        try {
          await browser.tabs.get(assignedTabId);
          claimedTabIds.add(assignedTabId);
          await setRecoveryTag(assignedTabId, target.project_id);
          continue;
        } catch (_) {
          claimedTabIds.delete(assignedTabId);
          delete projectTabs[target.project_id];
        }
      }
      const recovered = Recovery.selectRecoveryTab(
        target, tabs, restoredAssignments, claimedTabIds
      );
      const tab = recovered?.tab || null;
      if (tab) {
        tabTargets[tab.id] = target;
        projectTabs[target.project_id] = tab.id;
        claimedTabIds.add(tab.id);
        await setRecoveryTag(tab.id, target.project_id);
        if (!target.conversation_id) {
          pendingAdoptions[tab.id] = target.project_id;
          await adoptConversation(tab.id, target, tab.url);
        }
        postStatus({projectId:target.project_id,baseProjectId:target.base_project_id,workerSlot:target.worker_slot,
          projectName:target.name,target:tab.url,event:"target-tab-recovered",reason:recovered.reason,
          at:new Date().toISOString(),tabId:tab.id});
        await inject(tab.id, target);
      } else {
        postStatus({projectId:target.project_id,projectName:target.name,target:target.url,event:"target-tab-opening",at:new Date().toISOString()});
        const opened = await browser.tabs.create({url: target.url, active: false});
        tabTargets[opened.id] = target;
        projectTabs[target.project_id] = opened.id;
        claimedTabIds.add(opened.id);
        await setRecoveryTag(opened.id, target.project_id);
        if (!target.conversation_id) pendingAdoptions[opened.id] = target.project_id;
      }
    }
  } catch (error) {
    console.warn("[ZCloud Runner] config unavailable", error);
    postStatus({event:"config-load-failed",error:String(error?.message||error),at:new Date().toISOString()});
  }
}
async function inject(tabId, target) {
  try {
    if (!target.replacement_handoff) {
      const persistedHandoff = await getReplacementHandoffTag(tabId);
      if (persistedHandoff) target.replacement_handoff = persistedHandoff;
    }
    tabTargets[tabId] = target;
    projectTabs[target.project_id] = tabId;
    await setRecoveryTag(tabId, target.project_id);
    await browser.tabs.executeScript(tabId, {code: "(" + runProject.toString() + ")(" + JSON.stringify(target) + ");", runAt: "document_idle"});
    postStatus({projectId: target.project_id, projectName: target.name, target: target.url || ("https://chatgpt.com/c/" + target.conversation_id),
      targetConversation: target.conversation_id, event: "injection-success", at: new Date().toISOString(), tabId: tabId});
  } catch (error) {
    postStatus({projectId: target.project_id, projectName: target.name, target: target.url || ("https://chatgpt.com/c/" + target.conversation_id),
      targetConversation: target.conversation_id, event: "injection-failed", error: String(error?.message || error),
      at: new Date().toISOString(), tabId: tabId});
  }
}
async function commandResult(commandId, status, result) {
  if (!commandId) return;
  await fetch(API + "/runner-command-result", {method: "POST", mode: "no-cors",
    body: JSON.stringify({command_id: commandId, status: status, result: result})}).catch(() => {});
}
async function newProjectChat(projectId, reason, commandId) {
  const workerKeys = workerKeysFor(projectId);
  if (!targets[projectId] && workerKeys.length) {
    if (runningActions.has(projectId)) return;
    runningActions.add(projectId);
    try {
      for (const key of workerKeys) await newProjectChat(key, reason, null);
      await commandResult(commandId, "completed", workerKeys.length + " aparte workerchats geopend");
    } catch (error) {
      await commandResult(commandId, "failed", String(error?.message || error));
    } finally { runningActions.delete(projectId); }
    return;
  }
  if (runningActions.has(projectId)) return;
  runningActions.add(projectId);
  const target = targets[projectId];
  try {
    if (!target) throw new Error("Projectconfig ontbreekt");
    const handoff = await prepareReplacementHandoff(target, reason);
    const oldTab = projectTabs[projectId];
    if (oldTab != null) {
      try { await browser.tabs.sendMessage(oldTab, {type: "runner-stop", projectId: projectId, reason: reason}); } catch (_) {}
      await clearRecoveryTag(oldTab);
      await closeRunnerTab(oldTab);
      delete tabTargets[oldTab];
      delete pendingAdoptions[oldTab];
    }
    target.active = true;
    target.replacement_handoff = handoff;
    const tab = await browser.tabs.create({url: "https://chatgpt.com/", active: false});
    tabTargets[tab.id] = target;
    projectTabs[projectId] = tab.id;
    await setRecoveryTag(tab.id, projectId);
    await setReplacementHandoffTag(tab.id, handoff);
    pendingAdoptions[tab.id] = projectId;
    await commandResult(commandId, "completed", "Nieuwe projectchat geopend");
  } catch (error) {
    await commandResult(commandId, "failed", String(error?.message || error));
    postStatus({projectId: projectId, projectName: target?.name, event: "recovery-failed",
      error: String(error?.message || error), reason: reason, at: new Date().toISOString()});
  } finally { runningActions.delete(projectId); }
}
async function startProject(projectId, commandId) {
  const workerKeys = workerKeysFor(projectId);
  if (!targets[projectId] && workerKeys.length) {
    if (runningActions.has(projectId)) return;
    runningActions.add(projectId);
    try {
      for (const key of workerKeys) await startProject(key, null);
      await commandResult(commandId, "completed", workerKeys.length + " ChatGPT-workers gestart");
    } catch (error) {
      await commandResult(commandId, "failed", String(error?.message || error));
    } finally { runningActions.delete(projectId); }
    return;
  }
  if (runningActions.has(projectId)) return;
  runningActions.add(projectId);
  const target = targets[projectId];
  try {
    if (!target) throw new Error("Projectconfig ontbreekt");
    target.active = true;
    const current = projectTabs[projectId];
    if (current != null) {
      try {
        await browser.tabs.get(current);
        await commandResult(commandId, "completed", "Project draait al");
        return;
      } catch (_) {
        delete projectTabs[projectId];
      }
    }
    if (!target.conversation_id) {
      runningActions.delete(projectId);
      await newProjectChat(projectId, "dashboard-start", commandId);
      return;
    }
    const tab = await browser.tabs.create({url: target.url, active: false});
    tabTargets[tab.id] = target;
    projectTabs[projectId] = tab.id;
    await setRecoveryTag(tab.id, projectId);
    await commandResult(commandId, "completed", "Project gestart");
  } catch (error) {
    await commandResult(commandId, "failed", String(error?.message || error));
    postStatus({projectId: projectId, projectName: target?.name, event: "start-failed",
      error: String(error?.message || error), at: new Date().toISOString()});
  } finally {
    runningActions.delete(projectId);
  }
}
async function pauseProject(projectId, commandId) {
  const workerKeys = workerKeysFor(projectId);
  if (!targets[projectId] && workerKeys.length) {
    for (const key of workerKeys) await pauseProject(key, null);
    await commandResult(commandId, "completed", workerKeys.length + " ChatGPT-workers gepauzeerd");
    return;
  }
  const target = targets[projectId];
  if (target) target.active = false;
  const tabId = projectTabs[projectId];
  if (tabId != null) {
    try { await browser.tabs.sendMessage(tabId, {type: "runner-stop", projectId: projectId, reason: "dashboard-pause"}); } catch (_) {}
    await clearRecoveryTag(tabId);
    await closeRunnerTab(tabId);
    delete tabTargets[tabId];
    delete projectTabs[projectId];
    delete pendingAdoptions[tabId];
  }
  postStatus({projectId: projectId, projectName: target?.name, target: target?.url || "",
    event: "runner-paused", reason: "dashboard-pause", at: new Date().toISOString()});
  await commandResult(commandId, "completed", "Project gepauzeerd");
}
async function drainProject(projectId, commandId) {
  if (!targets[projectId]) {
    await commandResult(commandId, "failed", "Workerconfig ontbreekt");
    return;
  }
  const target = targets[projectId];
  const tabId = projectTabs[projectId];
  if (tabId == null) {
    postStatus({projectId: projectId, projectName: target?.name, target: target?.url || "",
      event: "runner-drained", reason: "already-idle", at: new Date().toISOString()});
    await commandResult(commandId, "completed", "Worker was al klaar");
    return;
  }
  let result = null;
  try {
    result = await browser.tabs.sendMessage(tabId, {type: "runner-drain", projectId: projectId, reason: "dashboard-drain"});
  } catch (_) {
    try {
      await inject(tabId, target);
      await new Promise(resolve => setTimeout(resolve, 800));
      result = await browser.tabs.sendMessage(tabId, {type: "runner-drain", projectId: projectId, reason: "dashboard-drain"});
    } catch (_) {}
  }
  if (result?.ok) {
    await commandResult(commandId, "completed", "Worker rondt de huidige taak af");
  } else {
    await commandResult(commandId, "failed", "Drain kon niet veilig worden bevestigd");
  }
}

async function pushProject(projectId, commandId) {
  const workerKeys = workerKeysFor(projectId, true);
  if (!targets[projectId] && workerKeys.length) {
    for (const key of workerKeys) await pushProject(key, null);
    await commandResult(commandId, "completed", workerKeys.length + " ChatGPT-workers gepusht");
    return;
  }
  const target = targets[projectId];
  if (!target) {
    await commandResult(commandId, "failed", "Projectconfig ontbreekt");
    return;
  }
  let tabId = projectTabs[projectId];
  if (tabId == null) {
    await newProjectChat(projectId, "dashboard-push-recovery", commandId);
    return;
  }
  try { await browser.tabs.get(tabId); }
  catch (_) {
    delete projectTabs[projectId];
    await newProjectChat(projectId, "dashboard-push-recovery", commandId);
    return;
  }
  let result = null;
  try {
    result = await browser.tabs.sendMessage(tabId, {type: "runner-push", projectId: projectId, reason: "dashboard-push"});
  } catch (_) {
    await inject(tabId, target);
    await new Promise(resolve => setTimeout(resolve, 1000));
    try {
      result = await browser.tabs.sendMessage(tabId, {type: "runner-push", projectId: projectId, reason: "dashboard-push"});
    } catch (_) {}
  }
  if (result?.ok) {
    await commandResult(commandId, "completed", "Prompt direct verstuurd");
    return;
  }
  if (result?.reason === "runner-busy") {
    await commandResult(commandId, "completed", "Runner is al bezig; extra prompt was niet nodig");
    return;
  }
  if (result?.reason === "improvement-finished") {
    await commandResult(commandId, "failed", "zCloud improvements staan op Finished / Maintain");
    return;
  }
  await newProjectChat(projectId, "dashboard-push-recovery", commandId);
}
const lastHealthRecovery = Object.create(null);
async function watchRunnerHealth() {
  try {
    const response = await fetch(API + "/status", {cache: "no-store"});
    if (!response.ok) return;
    const data = await response.json();
    for (const [projectId, status] of Object.entries(data.chatgpt_runners || {})) {
      if (!status.active || status.auto_continue === false) continue;
      const stale = status.age_seconds != null && status.age_seconds > 300;
      const stalled = status.stalled === true;
      if ((!stale && !stalled) || Date.now() - (lastHealthRecovery[projectId] || 0) < 900000) continue;
      lastHealthRecovery[projectId] = Date.now();
      newProjectChat(projectId, stale ? "heartbeat-watchdog" : "stalled-watchdog", null);
    }
  } catch (_) {}
}
async function pollCommands() {
  try {
    const response = await fetch(API + "/runner-commands", {cache: "no-store"});
    if (!response.ok) return;
    const data = await response.json();
    for (const command of data.commands || []) {
      const hasTarget = !!targets[command.project_id] || workerKeysFor(command.project_id).length > 0;
      if (!hasTarget || processedCommands.has(command.id) || runningActions.has(command.project_id)) continue;
      processedCommands.add(command.id);
      if (command.action === "push") await pushProject(command.project_id, command.id);
      else if (command.action === "start") await startProject(command.project_id, command.id);
      else if (command.action === "pause") await pauseProject(command.project_id, command.id);
      else if (command.action === "drain") await drainProject(command.project_id, command.id);
      else await newProjectChat(command.project_id, "dashboard-restart", command.id);
    }
  } catch (_) {}
}
browser.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === "runner-status" && message.payload) {
    postStatus({...message.payload, tabId: sender?.tab?.id ?? null});
  } else if (message?.type === "runner-status-sync" && message.payload) {
    return postStatus({...message.payload, tabId: sender?.tab?.id ?? null}).then(() => ({ok:true}));
  } else if (message?.type === "runner-policy-check" && message.projectId) {
    return fetch(API + "/runner-targets", {cache: "no-store"})
      .then(response => response.ok ? response.json() : Promise.reject(new Error("policy HTTP " + response.status)))
      .then(data => {
        const target = (data.projects || {})[message.projectId];
        const base = message.projectId.split("::w", 1)[0];
        return {
          auto_continue: target ? target.auto_continue !== false : base !== "cloud",
          continue_delay_seconds: target ? Number(target.auto_continue_delay_seconds || 300) : 300,
          autonomy: target?.autonomy || null
        };
      })
      .catch(() => ({auto_continue: false, continue_delay_seconds: 300, autonomy: {reason:"policy-unavailable"}}));
  } else if (message?.type === "runner-new-chat" && message.projectId) {
    newProjectChat(message.projectId, message.reason || "stall-recovery", null);
  } else if (message?.type === "runner-replacement-handoff-consumed" && message.projectId) {
    const tabId = sender?.tab?.id ?? null;
    const target = tabId != null ? tabTargets[tabId] : null;
    if (!target || target.project_id !== message.projectId) return {ok:false, reason:"worker-tab-mismatch"};
    delete target.replacement_handoff;
    if (targets[message.projectId]) delete targets[message.projectId].replacement_handoff;
    return clearReplacementHandoffTag(tabId).then(() => ({ok:true}));
  }
});
browser.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!tab.url || !tab.url.includes("chatgpt.com")) return;
  const assigned = tabTargets[tabId];
  if (assigned) {
    if (pendingAdoptions[tabId] === assigned.project_id && projectTabs[assigned.project_id] === tabId) {
      adoptConversation(tabId, assigned, tab.url).catch(() => {});
    }
    if (changeInfo.status === "complete") inject(tabId, assigned);
  } else if (changeInfo.status === "complete") {
    const target = Object.values(targets).find(t => tab.url.includes("/c/" + t.conversation_id));
    if (target) inject(tabId, target);
  }
});
browser.tabs.onRemoved.addListener(tabId => {
  const target = tabTargets[tabId];
  const intentional = intentionalTabClosures.delete(tabId);
  if (target && projectTabs[target.project_id] === tabId) delete projectTabs[target.project_id];
  delete tabTargets[tabId];
  delete pendingAdoptions[tabId];
  if (target && !intentional) {
    recoverClosedWorker(target, "unexpected-tab-closed").catch(() => {});
  }
});
refreshTargets();
setInterval(refreshTargets, 120000);
pollCommands();
setInterval(pollCommands, 5000);
watchRunnerHealth();
setInterval(watchRunnerHealth, 60000);