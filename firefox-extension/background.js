const API = "http://127.0.0.1:8765/api";
const targets = Object.create(null);
const tabTargets = Object.create(null);
const projectTabs = Object.create(null);
const pendingAdoptions = Object.create(null);
const runningActions = new Set();
const processedCommands = new Set();

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
  const PROMPT = cfg.prompt;
  const BASE_PROJECT = cfg.base_project_id || cfg.projectId;
  const SINGLE_RUN = BASE_PROJECT === "portfolio-review";
  let autoContinue = cfg.auto_continue !== false;
  const CHECK_MS = 5000;
  const STALL_MS = 20 * 60 * 1000;
  const STARTUP_IDLE_MS = 8000;
  const COMPOSER_RECOVERY_MS = 45 * 1000;
  let sawGeneration = false;
  let awaitingGeneration = false;
  let generationDeadline = 0;
  let finishedAt = 0;
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
      return autoContinue;
    } catch (_) {
      return BASE_PROJECT === "cloud" ? false : autoContinue;
    }
  }
  async function reportFinishSignals(text) {
    if (BASE_PROJECT !== "cloud" || !text) return;
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
        status("generation-started");
        lastProgressAt = now;
      }
    } else if (generating !== lastGenerating) {
      status(generating ? "generation-started" : "generation-finished");
      lastGenerating = generating;
      if (generating) lastProgressAt = now;
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
      if (!finishedAt) { finishedAt = now; return; }
      if (now - finishedAt >= 5000) {
        sawGeneration = false;
        finishedAt = 0;
        lastText = text;
        await reportFinishSignals(text);
        if (draining) {
          paused = true;
          clearInterval(tickTimer);
          clearInterval(heartbeatTimer);
          status("runner-drained", {reason: "current-task-finished"});
          return;
        }
        if (SINGLE_RUN) { status("scheduled-run-complete", {reason: "single-run"}); return; }
        if (!paused && await canAutoContinue()) await send("antwoord klaar");
        else if (!paused) status("auto-continue-blocked", {reason: "finished-maintain"});
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
        try { await browser.tabs.remove(tabId); } catch (_) {}
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
    for (const target of Object.values(targets)) {
      const assignedTabId = projectTabs[target.project_id];
      if (!target.active) {
        if (assignedTabId != null) {
          try { await browser.tabs.sendMessage(assignedTabId, {type: "runner-stop", projectId: target.project_id, reason: "project-paused"}); } catch (_) {}
          try { await browser.tabs.remove(assignedTabId); } catch (_) {}
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
        try { await browser.tabs.get(assignedTabId); continue; }
        catch (_) { delete projectTabs[target.project_id]; }
      }
      const tab = target.conversation_id ? tabs.find(t => t.url && t.url.includes("/c/" + target.conversation_id)) : null;
      if (tab) {
        tabTargets[tab.id] = target;
        projectTabs[target.project_id] = tab.id;
        postStatus({projectId:target.project_id,projectName:target.name,target:tab.url,event:"target-tab-found",at:new Date().toISOString(),tabId:tab.id});
        await inject(tab.id, target);
      } else {
        postStatus({projectId:target.project_id,projectName:target.name,target:target.url,event:"target-tab-opening",at:new Date().toISOString()});
        const opened = await browser.tabs.create({url: target.url, active: false});
        tabTargets[opened.id] = target;
        projectTabs[target.project_id] = opened.id;
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
    tabTargets[tabId] = target;
    projectTabs[target.project_id] = tabId;
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
    const oldTab = projectTabs[projectId];
    if (oldTab != null) {
      try { await browser.tabs.sendMessage(oldTab, {type: "runner-stop", projectId: projectId, reason: reason}); } catch (_) {}
      try { await browser.tabs.remove(oldTab); } catch (_) {}
      delete tabTargets[oldTab];
      delete pendingAdoptions[oldTab];
    }
    target.active = true;
    const tab = await browser.tabs.create({url: "https://chatgpt.com/", active: false});
    tabTargets[tab.id] = target;
    projectTabs[projectId] = tab.id;
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
    try { await browser.tabs.remove(tabId); } catch (_) {}
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
        return {auto_continue: target ? target.auto_continue !== false : base !== "cloud"};
      })
      .catch(() => ({auto_continue: message.projectId.split("::w", 1)[0] !== "cloud"}));
  } else if (message?.type === "runner-new-chat" && message.projectId) {
    newProjectChat(message.projectId, message.reason || "stall-recovery", null);
  }
});
browser.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!tab.url || !tab.url.includes("chatgpt.com")) return;
  const assigned = tabTargets[tabId];
  if (assigned) {
    const match = tab.url.match(/\/c\/([0-9a-f-]{20,})/i);
    if (match && pendingAdoptions[tabId] === assigned.project_id && projectTabs[assigned.project_id] === tabId) {
      assigned.conversation_id = match[1];
      assigned.url = "https://chatgpt.com/c/" + match[1];
      targets[assigned.project_id] = assigned;
      delete pendingAdoptions[tabId];
      postStatus({
        projectId: assigned.project_id,
        projectName: assigned.name,
        target: assigned.url,
        targetConversation: match[1],
        event: "conversation-adopted",
        at: new Date().toISOString(),
        tabId: tabId
      });
    }
    if (changeInfo.status === "complete") inject(tabId, assigned);
  } else if (changeInfo.status === "complete") {
    const target = Object.values(targets).find(t => tab.url.includes("/c/" + t.conversation_id));
    if (target) inject(tabId, target);
  }
});
browser.tabs.onRemoved.addListener(tabId => {
  const target = tabTargets[tabId];
  if (target && projectTabs[target.project_id] === tabId) delete projectTabs[target.project_id];
  delete tabTargets[tabId];
  delete pendingAdoptions[tabId];
});
refreshTargets();
setInterval(refreshTargets, 120000);
pollCommands();
setInterval(pollCommands, 5000);
watchRunnerHealth();
setInterval(watchRunnerHealth, 60000);