const API = "http://127.0.0.1:8765/api";
const targets = Object.create(null);
const tabTargets = Object.create(null);
const projectTabs = Object.create(null);
const pendingAdoptions = Object.create(null);
const runningActions = new Set();
const processedCommands = new Set();

function runProject(cfg) {
  const marker = "__ZC_RUNNER_" + cfg.projectId.replace(/[^a-z0-9]/gi, "");
  if (window[marker]) return;
  window[marker] = true;
  const PROMPT = cfg.prompt;
  const CHECK_MS = 5000;
  const STALL_MS = 20 * 60 * 1000;
  const STARTUP_IDLE_MS = 15000;
  const COMPOSER_RECOVERY_MS = 90 * 1000;
  let sawGeneration = false;
  let awaitingGeneration = false;
  let generationDeadline = 0;
  let finishedAt = 0;
  let lastText = "";
  let lastProgressAt = Date.now();
  let sending = false;
  let lastGenerating = null;
  let paused = false;
  let recoveryRequested = false;
  const startedAt = Date.now();
  let lastPromptSentAt = 0;
  let lastStartupStatusAt = 0;
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
  function status(event, extra = {}) {
    const text = assistantText();
    const payload = {
      projectId: cfg.projectId,
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
    window.__ZC_RUNNER_STATUS__ = payload;
    try { browser.runtime.sendMessage({type: "runner-status", payload: payload}).catch(() => {}); } catch (_) {}
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
    if (paused || sending || stopButton()) return;
    const draft = composerText();
    if (draft === null) { status("send-blocked", {reason: "composer-missing"}); return; }
    if (draft && draft !== PROMPT) { status("send-blocked", {reason: "draft-present"}); return; }
    sending = true;
    try {
      const ok = draft === PROMPT || await fill(PROMPT);
      if (!ok) { status("send-blocked", {reason: "composer-missing"}); return; }
      await sleep(700);
      const button = sendButton();
      if (!button || button.disabled) { status("send-blocked", {reason: "send-button-unavailable"}); return; }
      button.click();
      lastPromptSentAt = Date.now();
      lastProgressAt = Date.now();
      sawGeneration = false;
      awaitingGeneration = true;
      generationDeadline = Date.now() + 120000;
      finishedAt = 0;
      status("prompt-sent", {reason: reason});
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
        if (!paused) await send("antwoord klaar");
      }
      return;
    }
    const draft = composerText();
    if (draft === null) {
      if (!composerMissingSince) composerMissingSince = now;
      if (now - lastStartupStatusAt >= 30000) {
        lastStartupStatusAt = now;
        status("startup-waiting", {reason: "composer-missing"});
      }
      if (now - composerMissingSince >= COMPOSER_RECOVERY_MS && !recoveryRequested) {
        recoveryRequested = true;
        status("composer-stalled", {reason: "composer-missing-for-90s"});
        browser.runtime.sendMessage({type: "runner-new-chat", projectId: cfg.projectId, reason: "composer-missing"}).catch(() => {});
      }
      return;
    }
    composerMissingSince = 0;
    if (!sending && now - startedAt >= STARTUP_IDLE_MS && now - lastStartupStatusAt >= 30000 &&
        (!lastPromptSentAt || now - lastPromptSentAt >= 300000)) {
      if (draft === "" || draft === PROMPT) {
        lastStartupStatusAt = now;
        await send(lastPromptSentAt ? "idle-retry" : "startup-retry");
      } else if (draft !== PROMPT) {
        lastStartupStatusAt = now;
        status("startup-blocked", {reason: "draft-present"});
      }
    }
  }
  browser.runtime.onMessage.addListener(message => {
    if (!message || message.projectId !== cfg.projectId) return;
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
  setTimeout(() => {
    if (paused) return;
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
    for (const [id, target] of Object.entries(data.projects || {})) {
      targets[id] = {...target, projectId: target.project_id || id, projectName: target.name || id};
    }
    postStatus({event: "targets-loaded", at: new Date().toISOString(), reason: Object.keys(targets).join(",")});
    const tabs = await browser.tabs.query({url: "https://chatgpt.com/*"});
    for (const target of Object.values(targets)) {
      const assignedTabId = projectTabs[target.project_id];
      if (assignedTabId != null) {
        try { await browser.tabs.get(assignedTabId); continue; }
        catch (_) { delete projectTabs[target.project_id]; }
      }
      const tab = tabs.find(t => t.url && t.url.includes("/c/" + target.conversation_id));
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
    postStatus({projectId: target.project_id, projectName: target.name, target: "https://chatgpt.com/c/" + target.conversation_id,
      targetConversation: target.conversation_id, event: "injection-success", at: new Date().toISOString(), tabId: tabId});
  } catch (error) {
    postStatus({projectId: target.project_id, projectName: target.name, target: "https://chatgpt.com/c/" + target.conversation_id,
      targetConversation: target.conversation_id, event: "injection-failed", error: String(error?.message || error),
      at: new Date().toISOString(), tabId: tabId});
  }
}
async function newProjectChat(projectId, reason, commandId) {
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
    const tab = await browser.tabs.create({url: "https://chatgpt.com/", active: true});
    tabTargets[tab.id] = target;
    projectTabs[projectId] = tab.id;
    pendingAdoptions[tab.id] = projectId;
    if (commandId) await fetch(API + "/runner-command-result", {method: "POST", mode: "no-cors",
      body: JSON.stringify({command_id: commandId, status: "completed", result: "Nieuwe projectchat geopend"})}).catch(() => {});
  } catch (error) {
    if (commandId) await fetch(API + "/runner-command-result", {method: "POST", mode: "no-cors",
      body: JSON.stringify({command_id: commandId, status: "failed", result: String(error?.message || error)})}).catch(() => {});
    postStatus({projectId: projectId, projectName: target?.name, event: "recovery-failed",
      error: String(error?.message || error), reason: reason, at: new Date().toISOString()});
  } finally { runningActions.delete(projectId); }
}
const lastHealthRecovery = Object.create(null);
async function watchRunnerHealth() {
  try {
    const response = await fetch(API + "/status", {cache: "no-store"});
    if (!response.ok) return;
    const data = await response.json();
    for (const [projectId, status] of Object.entries(data.chatgpt_runners || {})) {
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
      if (!targets[command.project_id] || processedCommands.has(command.id) || runningActions.has(command.project_id)) continue;
      processedCommands.add(command.id);
      await newProjectChat(command.project_id, "dashboard-restart", command.id);
    }
  } catch (_) {}
}
browser.runtime.onMessage.addListener((message, sender) => {
  if (message?.type === "runner-status" && message.payload) {
    postStatus({...message.payload, tabId: sender?.tab?.id ?? null});
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
