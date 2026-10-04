// ==UserScript==
// @name         zCloud Dynamic Worker
// @namespace    https://zcloud.local/
// @version      1.3.5
// @description  Browser-wide database-backed ChatGPT + Claude dynamic worker for zCloud.
// @match        http://*/*
// @match        https://*/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @run-at       document-idle
// @noframes
// @updateURL    http://127.0.0.1:8765/zcloud-worker.user.js
// @downloadURL  http://127.0.0.1:8765/zcloud-worker.user.js
// ==/UserScript==

(() => {
  "use strict";

  const API = "http://127.0.0.1:8765/api";
  const SCRIPT_VERSION = "1.3.5";
  const REQUIRED_THINKING_EFFORT = "high";
  const MODEL_PICKER_SELECTOR = [
    'button[aria-label="Select ChatGPT model"]',
    'button[title="Select ChatGPT model"]',
    '[data-testid="model-switcher-dropdown-button"]',
    'button[aria-label="Model selector"]',
    '[aria-label="Model selector"][aria-haspopup="menu"]',
    '[aria-haspopup="menu"][data-testid*="model"]',
    'button[aria-label^="Switch mode"]',
    '[aria-label*="current mode"]'
  ].join(",");
  const THINKING_OPTION_SELECTOR = [
    '[role="menuitemradio"]',
    '[role="option"]',
    '[role="menuitem"]',
    '[role="radio"]',
    '[data-testid*="thinking"]',
    '[data-testid*="reasoning"]'
  ].join(",");
  const DEFAULT_TIMING = Object.freeze({
    refreshMs: 5000,
    tickMs: 1500,
    heartbeatMs: 30000,
    generationStartTimeoutMs: 120000
  });
  const TAB_CLAIM_LEASE_MS = 90000;
  let timing = {...DEFAULT_TIMING};

  let target = null;
  let sending = false;
  let draining = false;
  let lastGenerating = null;
  let lastAssistantText = "";
  let sawGeneration = false;
  let finishedAt = 0;
  let awaitingGeneration = false;
  let generationDeadline = 0;
  let lastThinkingEffortWarningAt = 0;
  let lastSendBlockedReport = {reason: "", at: 0};
  let lastProgressAt = Date.now();
  let lastPromptSentAt = 0;
  let lastHandledCommandId = 0;
  let initialDispatchKey = "";
  let qualityRetryPending = false;
  let qualityRetryCount = 0;
  let lastThinkingDiagnostic = "";
  let refreshTimer = null;
  let tickTimer = null;
  let heartbeatTimer = null;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function gmRequest(path, options = {}) {
    const method = options.method || "GET";
    const body = options.body == null ? null : JSON.stringify(options.body);
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: API + path,
        headers: body ? {"Content-Type": "application/json"} : undefined,
        data: body,
        timeout: options.timeout || 8000,
        onload: response => {
          let data = null;
          try { data = response.responseText ? JSON.parse(response.responseText) : {}; }
          catch (_) { data = {}; }
          if (response.status >= 200 && response.status < 300) resolve(data);
          else reject(new Error(method + " " + path + " HTTP " + response.status));
        },
        onerror: () => reject(new Error(method + " " + path + " network error")),
        ontimeout: () => reject(new Error(method + " " + path + " timeout"))
      });
    });
  }

  function provider() {
    const host = String(location.hostname || "").toLowerCase();
    if (host === "claude.ai" || host === "claude.com" || host.endsWith(".claude.ai") || host.endsWith(".claude.com")) return "claude";
    if (host === "chatgpt.com" || host.endsWith(".chatgpt.com")) return "chatgpt";
    return "other";
  }

  function isWorkerProvider(value = provider()) {
    return value === "chatgpt" || value === "claude";
  }

  function normalizeProvider(value, fallback = "chatgpt") {
    const text = String(value || "").trim().toLowerCase();
    if (text === "claude" || text === "anthropic") return "claude";
    if (text === "chatgpt" || text === "openai") return "chatgpt";
    return fallback;
  }

  function conversationId() {
    const current = provider();
    if (current === "claude") {
      const match = location.pathname.match(/^\/chat\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\/|$)/i);
      return match ? match[1] : "";
    }
    if (current === "chatgpt") {
      const match = location.pathname.match(/^\/c\/([0-9a-f-]{20,})(?:\/|$)/i);
      return match ? match[1] : "";
    }
    return "";
  }

  function isNewChatPage(providerName = provider()) {
    if (providerName === "claude") return location.pathname === "/new" || location.pathname === "/";
    if (providerName === "chatgpt") return location.pathname === "/" || location.pathname === "";
    return false;
  }

  function newChatUrl(providerName = provider()) {
    return normalizeProvider(providerName) === "claude" ? "https://claude.ai/new" : "https://chatgpt.com/";
  }

  function conversationUrl(conversationIdValue, providerName = provider()) {
    const expected = normalizeProvider(providerName);
    const id = encodeURIComponent(String(conversationIdValue || "").trim());
    if (!id) return newChatUrl(expected);
    return expected === "claude"
      ? "https://claude.ai/chat/" + id
      : "https://chatgpt.com/c/" + id;
  }

  function candidateProvider(candidate) {
    if (!candidate || typeof candidate !== "object") return "chatgpt";
    const explicit = String(candidate.provider || candidate.ai_provider || candidate.chat_provider || candidate.runner_provider || "").trim().toLowerCase();
    if (explicit === "claude" || explicit === "anthropic") return "claude";
    if (explicit === "chatgpt" || explicit === "openai") return "chatgpt";
    const urlValue = String(candidate.conversation_url || candidate.target_url || candidate.url || "").trim();
    if (urlValue) {
      try {
        const host = new URL(urlValue, location.href).hostname.toLowerCase();
        if (host === "claude.ai" || host === "claude.com" || host.endsWith(".claude.ai") || host.endsWith(".claude.com")) return "claude";
        if (host === "chatgpt.com" || host.endsWith(".chatgpt.com")) return "chatgpt";
      } catch (_) {}
    }
    return "chatgpt";
  }

  function navigationUrlForCandidate(candidate, token) {
    const expected = candidateProvider(candidate);
    const base = candidate?.conversation_id
      ? conversationUrl(candidate.conversation_id, expected)
      : newChatUrl(expected);
    try {
      const url = new URL(base);
      const projectId = String(candidate?.project_id || "").trim();
      if (projectId) url.searchParams.set("zcloud_worker", projectId);
      if (token) url.searchParams.set("zcloud_tab", token);
      return url.toString();
    } catch (_) {
      return base;
    }
  }

  function randomToken() {
    try {
      const bytes = new Uint8Array(12);
      crypto.getRandomValues(bytes);
      return [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
    } catch (_) {
      return String(Date.now()) + "-" + Math.random().toString(16).slice(2);
    }
  }

  function readUrlValue(name) {
    try { return String(new URL(location.href).searchParams.get(name) || "").trim(); }
    catch (_) { return ""; }
  }

  let tabToken = readUrlValue("zcloud_tab");
  try {
    if (!tabToken) tabToken = sessionStorage.getItem("zcloud-tab-token") || "";
    if (!tabToken) tabToken = randomToken();
    sessionStorage.setItem("zcloud-tab-token", tabToken);
  } catch (_) {
    if (!tabToken) tabToken = randomToken();
  }

  function pendingProjectId() {
    const fromUrl = readUrlValue("zcloud_worker");
    if (fromUrl) return fromUrl;
    try { return sessionStorage.getItem("zcloud-pending-project") || ""; }
    catch (_) { return ""; }
  }

  function setPendingProject(projectId) {
    try { sessionStorage.setItem("zcloud-pending-project", String(projectId || "")); } catch (_) {}
  }

  function clearNavigationMarkers() {
    try { sessionStorage.removeItem("zcloud-pending-project"); } catch (_) {}
    try {
      const url = new URL(location.href);
      let changed = false;
      for (const key of ["zcloud_worker", "zcloud_tab"]) {
        if (url.searchParams.has(key)) { url.searchParams.delete(key); changed = true; }
      }
      if (changed) history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    } catch (_) {}
  }

  function claimKey(projectId) {
    return "zcloud-tab-claim:" + String(projectId || "");
  }

  function readClaim(projectId) {
    try {
      const value = GM_getValue(claimKey(projectId), null);
      return value && typeof value === "object" ? value : null;
    } catch (_) { return null; }
  }

  function claimCandidate(candidate) {
    const projectId = String(candidate?.project_id || "");
    if (!projectId) return false;
    const current = readClaim(projectId);
    const now = Date.now();
    if (current && current.token !== tabToken && Number(current.expiresAt || 0) > now) return false;
    try {
      GM_setValue(claimKey(projectId), {
        token: tabToken,
        expiresAt: now + TAB_CLAIM_LEASE_MS,
        provider: candidateProvider(candidate),
        href: location.href
      });
      const verified = readClaim(projectId);
      return !!verified && verified.token === tabToken;
    } catch (_) {
      return true;
    }
  }

  function renewClaim(candidate = target) {
    const projectId = String(candidate?.project_id || "");
    if (!projectId) return;
    const current = readClaim(projectId);
    if (current && current.token !== tabToken && Number(current.expiresAt || 0) > Date.now()) return;
    try {
      GM_setValue(claimKey(projectId), {
        token: tabToken,
        expiresAt: Date.now() + TAB_CLAIM_LEASE_MS,
        provider: candidateProvider(candidate),
        href: location.href
      });
    } catch (_) {}
  }

  function releaseClaim(candidate = target) {
    const projectId = String(candidate?.project_id || "");
    if (!projectId) return;
    const current = readClaim(projectId);
    if (current && current.token === tabToken) {
      try { GM_deleteValue(claimKey(projectId)); } catch (_) {}
    }
  }

  function clampTiming(value, fallback, min, max) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(min, Math.min(max, Math.round(parsed))) : fallback;
  }

  function applyRuntimeSettings(payload) {
    const cfg = payload?.dynamic_workers || payload?.runner_settings || {};
    const next = {
      refreshMs: clampTiming(cfg.check_interval_ms ?? cfg.refresh_ms, DEFAULT_TIMING.refreshMs, 1000, 60000),
      tickMs: clampTiming(cfg.tick_interval_ms, DEFAULT_TIMING.tickMs, 250, 10000),
      heartbeatMs: clampTiming(cfg.heartbeat_interval_ms, DEFAULT_TIMING.heartbeatMs, 5000, 300000),
      generationStartTimeoutMs: clampTiming(cfg.generation_start_timeout_ms, DEFAULT_TIMING.generationStartTimeoutMs, 10000, 120000)
    };
    const changed = Object.keys(next).some(key => next[key] !== timing[key]);
    timing = next;
    if (changed && (refreshTimer || tickTimer || heartbeatTimer)) scheduleTimers();
  }

  function baseProjectId() {
    return String(target?.base_project_id || target?.project_id || "").split("::w", 1)[0];
  }

  function assignmentReady(candidate) {
    if (!candidate || candidate.active !== true || candidate.assignment_ready !== true) return false;
    const baseProject = String(candidate.base_project_id || candidate.project_id || "").split("::w", 1)[0];
    const prompt = String(candidate.prompt || "");
    if (candidate.reviewer_mode === true) {
      return baseProject === "portfolio-review" &&
        prompt.includes("Portfolio Bird's-eye Reviewer") &&
        prompt.includes("Senior Team OS");
    }
    const queueId = String(candidate.queue_item?.queue_id || "").trim();
    const slot = Number(candidate.global_worker_slot || 0);
    const total = Number(candidate.global_worker_count || 0);
    return !!queueId &&
      Number.isInteger(slot) && slot >= 1 &&
      Number.isInteger(total) && total >= slot &&
      prompt.startsWith("Werk verder aan ") &&
      prompt.includes("Kijk in Notion in welke fase het project zit");
  }

  async function refreshTarget() {
    let payload;
    try { payload = await gmRequest("/runner-targets"); }
    catch (error) {
      if (target) await status("userscript-config-unavailable", {error: String(error.message || error)});
      return;
    }
    applyRuntimeSettings(payload);
    const projects = payload.projects || {};
    const active = Object.values(projects).filter(item => item?.active === true);
    const ready = active.filter(assignmentReady);
    const pending = pendingProjectId();
    let next = null;

    if (pending && projects[pending] && claimCandidate(projects[pending])) next = projects[pending];

    const currentProvider = provider();
    if (!next && isWorkerProvider(currentProvider)) {
      const cid = conversationId();
      if (cid) {
        const matched = active.find(item => candidateProvider(item) === currentProvider && item?.conversation_id === cid) || null;
        if (matched && claimCandidate(matched)) next = matched;
      }
    }

    if (!next && target?.project_id && projects[target.project_id] && claimCandidate(projects[target.project_id])) {
      next = projects[target.project_id];
    }

    if (!next) {
      const ordered = [
        ...ready.filter(item => candidateProvider(item) === currentProvider),
        ...ready.filter(item => candidateProvider(item) !== currentProvider),
        ...active.filter(item => candidateProvider(item) === currentProvider),
        ...active.filter(item => candidateProvider(item) !== currentProvider)
      ];
      next = ordered.find(claimCandidate) || null;
    }

    if (!next) {
      releaseClaim(target);
      target = null;
      draining = false;
      return;
    }

    const expectedProvider = candidateProvider(next);
    const currentConversation = conversationId();
    const onRightPage = next.conversation_id
      ? currentProvider === expectedProvider && currentConversation === String(next.conversation_id)
      : currentProvider === expectedProvider && isNewChatPage(expectedProvider);

    if (!onRightPage) {
      if (!generationActive() && !sending) {
        setPendingProject(next.project_id);
        renewClaim(next);
        location.assign(navigationUrlForCandidate(next, tabToken));
      }
      return;
    }

    clearNavigationMarkers();
    const previousConversation = target?.conversation_id || "";
    target = {...target, ...next};
    draining = target.desired_state === "draining";
    renewClaim(target);

    const cid = conversationId();
    if (cid && cid !== previousConversation && cid !== target.conversation_id) {
      await status("conversation-adopted", {
        reason: "violentmonkey-route-adoption",
        target: location.href,
        targetConversation: cid
      });
    }

    const forced = !!target.force_initial_dispatch;
    const key = String(target.queue_item?.queue_id || "");
    if (forced && key && initialDispatchKey !== key && assignmentReady(target)) {
      initialDispatchKey = key;
      await sendPrompt("violentmonkey-initial-dispatch");
    }
  }

  function stopButton() {
    const current = provider();
    if (current === "other") return null;
    const selectors = current === "claude"
      ? [
          'button[data-testid="chat-input-stop"]',
          'button[data-testid="stop-button"]',
          'button[data-testid*="stop"]',
          'button[data-testid*="cancel"]',
          'button[aria-label="Stop response"]',
          'button[aria-label="Stop generating"]',
          'button[aria-label*="Stop"]',
          'button[aria-label*="stop"]'
        ]
      : ['button[data-testid="stop-button"]'];
    const explicit = document.querySelector(selectors.join(","));
    if (explicit && visible(explicit)) return explicit;
    return [...document.querySelectorAll("button")].find(button => {
      if (!visible(button)) return false;
      const text = ((button.getAttribute("aria-label") || "") + " " + (button.textContent || "")).toLowerCase();
      return /(?:^|\s)(?:stop|stoppen)(?:\s|$)/i.test(text) || /stop (?:response|generating|generation)/i.test(text);
    }) || null;
  }

  function streamingNode() {
    if (provider() !== "claude") return null;
    return [...document.querySelectorAll('[data-is-streaming]')].find(el => {
      if (!visible(el)) return false;
      const value = String(el.getAttribute("data-is-streaming") || "").trim().toLowerCase();
      return value === "" || value === "true" || value === "1" || value === "yes";
    }) || null;
  }

  function generationActive() {
    if (!isWorkerProvider()) return false;
    return !!stopButton() || !!streamingNode();
  }

  function composer() {
    const current = provider();
    if (current === "other") return null;
    if (current === "claude") {
      return document.querySelector('[data-testid="chat-input"][contenteditable="true"]') ||
        document.querySelector('[contenteditable="true"][role="textbox"][aria-label*="Claude"]') ||
        document.querySelector('[data-cds="Editor"][contenteditable="true"]') ||
        document.querySelector('[contenteditable="true"][role="textbox"]');
    }
    return document.querySelector("#prompt-textarea") ||
      document.querySelector('[contenteditable="true"][role="textbox"]') ||
      document.querySelector("textarea");
  }

  function sendButton() {
    const current = provider();
    if (current === "other") return null;
    if (current === "claude") {
      return document.querySelector('button[data-testid="chat-input-send"]') ||
        document.querySelector('button[aria-label="Send message"]') ||
        [...document.querySelectorAll("button")].find(button => {
          if (!visible(button)) return false;
          const text = ((button.getAttribute("aria-label") || "") + " " + (button.textContent || "")).toLowerCase();
          return text.includes("send message") || text === "send";
        }) || null;
    }
    return document.querySelector('button[data-testid="send-button"]') ||
      [...document.querySelectorAll("button")].find(button => {
        const text = ((button.getAttribute("aria-label") || "") + " " + (button.textContent || "")).toLowerCase();
        return text.includes("send") || text.includes("verzenden");
      }) || null;
  }

  function assistantText() {
    const current = provider();
    if (current === "other") return "";
    if (current === "claude") {
      const responseSelector = [
        '.font-claude-response',
        '[data-testid="ai-message"]',
        '[data-testid="message-assistant"]',
        '[data-testid="assistant-message"]',
        '[data-testid^="assistant-message"]',
        '[data-testid*="assistant-response"]',
        '[data-testid*="claude-response"]',
        '.font-claude-message',
        '.assistant-message'
      ].join(",");
      const turns = [...document.querySelectorAll('[data-testid^="conversation-turn"],[data-testid*="conversation-turn"],[data-test-render-count]')];
      for (let i = turns.length - 1; i >= 0; i -= 1) {
        const response = turns[i].querySelector(responseSelector);
        if (response) return String(response.innerText || response.textContent || "").trim();
      }
      const nodes = [...document.querySelectorAll(responseSelector)].filter(visible);
      return nodes.length ? String(nodes[nodes.length - 1].innerText || nodes[nodes.length - 1].textContent || "").trim() : "";
    }
    const nodes = [...document.querySelectorAll('[data-message-author-role="assistant"]')];
    return nodes.length ? String(nodes[nodes.length - 1].innerText || "").trim() : "";
  }

  function buttonUnavailable(button) {
    if (!button) return true;
    return !!button.disabled ||
      button.getAttribute?.("aria-disabled") === "true" ||
      button.hasAttribute?.("data-disabled");
  }

  function visible(el) {
    if (!el) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }

  function label(el) {
    return String(
      el?.getAttribute?.("aria-valuetext") ||
      el?.getAttribute?.("aria-label") ||
      el?.getAttribute?.("title") ||
      el?.innerText ||
      el?.textContent ||
      ""
    ).trim();
  }

  function isHigh(value) {
    const text = String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
    if (!text) return false;
    if (/extra\s+high|very\s+high|zeer\s+hoog|pro\b/.test(text)) return false;
    return /(?:^|\s)(?:high|hoog)(?:\b|\s|$)/i.test(text) ||
      /(?:^|\b)(?:think|denk)\s+hard(?:er)?(?:\b|$)/i.test(text) ||
      /^(?:hard|harder)(?:\b|\s)/i.test(text);
  }

  function selected(el) {
    const state = String(el?.getAttribute?.("data-state") || "").toLowerCase();
    return el?.getAttribute?.("aria-selected") === "true" ||
      el?.getAttribute?.("aria-checked") === "true" ||
      el?.getAttribute?.("aria-pressed") === "true" ||
      el?.getAttribute?.("aria-current") === "true" ||
      el?.getAttribute?.("data-selected") === "true" ||
      el?.getAttribute?.("data-active") === "true" ||
      state === "checked" || state === "on" || state === "active" ||
      /(?:^|\s)(?:selected|active|checked)(?:\s|$)/i.test(String(el?.className || ""));
  }

  function thinkingOptions() {
    return [...document.querySelectorAll(THINKING_OPTION_SELECTOR)].filter(visible);
  }

  function selectedHighOption() {
    return thinkingOptions().find(el => isHigh(label(el)) && selected(el)) || null;
  }

  function pickerShowsHigh() {
    return [...document.querySelectorAll(MODEL_PICKER_SELECTOR)]
      .filter(visible)
      .some(el => isHigh(label(el)));
  }

  function thinkingSliders() {
    return [...document.querySelectorAll('[role="slider"],input[type="range"]')].filter(visible);
  }

  function thinkingEffortPicker() {
    return [...document.querySelectorAll(
      'button,[role="button"],[aria-haspopup="menu"],[aria-haspopup="listbox"],[aria-label]'
    )].filter(visible).find(el => {
      const aria = String(el.getAttribute?.("aria-label") || "").trim();
      const testId = String(el.getAttribute?.("data-testid") || "").toLowerCase();
      if (/model selector/i.test(aria) || /select chatgpt model/i.test(aria)) return false;
      return /^(?:high|medium|low|standard|extended|hoog|gemiddeld|laag)\s+selector\b/i.test(aria) ||
        /thinking.*(?:selector|effort)|reasoning.*(?:selector|effort)|effort.*selector/i.test(aria) ||
        ((testId.includes("thinking") || testId.includes("reasoning") || testId.includes("effort")) &&
          !!el.getAttribute?.("aria-haspopup"));
    }) || null;
  }

  function effortPickerShowsHigh() {
    const picker = thinkingEffortPicker();
    return !!picker && isHigh(label(picker));
  }

  function thinkingControls() {
    return [...document.querySelectorAll(
      'button,[role="button"],[role="menuitem"],[role="menuitemradio"],[role="option"],[role="radio"],[aria-haspopup="menu"],[aria-haspopup="listbox"]'
    )].filter(visible).filter(el => {
      const text = label(el).toLowerCase();
      const testId = String(el.getAttribute?.("data-testid") || "").toLowerCase();
      return el.matches?.(MODEL_PICKER_SELECTOR) ||
        /instant|medium|high|hoog|model|thinking|reasoning|effort|denk|redeneer|gpt|hard/.test(text) ||
        testId.includes("model") || testId.includes("thinking") || testId.includes("reasoning");
    });
  }

  function compactThinkingDiagnostic(stage) {
    const selector = [
      "button",
      '[role="button"]',
      '[role="menuitem"]',
      '[role="menuitemradio"]',
      '[role="option"]',
      '[role="radio"]',
      '[role="slider"]',
      'input[type="range"]',
      '[aria-haspopup]',
      '[aria-label]',
      '[data-testid]'
    ].join(",");
    const clean = (value, max = 120) => String(value || "")
      .replace(/[|;\n\r]+/g, " ").replace(/\s+/g, " ").trim().slice(0, max);
    const snap = el => {
      if (!el || !el.getAttribute) return null;
      return {
        t: clean(el.getAttribute("data-testid"), 60),
        a: clean(el.getAttribute("aria-label"), 90),
        r: clean(el.getAttribute("role"), 24),
        x: clean(label(el), 120),
        e: clean(el.getAttribute("aria-expanded"), 8),
        h: clean(el.getAttribute("aria-haspopup"), 16),
        c: clean(el.getAttribute("aria-checked"), 8),
        s: clean(el.getAttribute("aria-selected"), 8),
        st: clean(el.getAttribute("data-state"), 16),
        v: clean(el.getAttribute("aria-valuetext"), 60)
      };
    };
    const scored = [...document.querySelectorAll(selector)]
      .filter(visible)
      .map(el => {
        const item = snap(el);
        const haystack = [item?.t, item?.a, item?.r, item?.x].join(" ").toLowerCase();
        let score = 0;
        if (el.matches?.(MODEL_PICKER_SELECTOR)) score += 200;
        if (/model|mode|gpt|instant|medium|high|hoog|hard|think|thinking|reason|effort|denk|redeneer/.test(haystack)) score += 100;
        if (item?.h) score += 30;
        if (/menuitem|option|radio|slider/.test(item?.r || "")) score += 20;
        if (item?.t) score += 10;
        return {score, item};
      })
      .sort((a, b) => b.score - a.score)
      .slice(0, 12)
      .map(entry => entry.item);
    const menus = [...document.querySelectorAll(
      '[role="menu"],[role="listbox"],[role="dialog"],[data-radix-menu-content],[data-radix-popper-content-wrapper]'
    )].filter(visible).slice(0, 4).map(el => ({
      r: clean(el.getAttribute("role"), 24),
      x: clean(el.innerText || el.textContent, 420)
    }));
    const picker = [...document.querySelectorAll(MODEL_PICKER_SELECTOR)].find(visible) || null;
    const payload = {
      p: snap(picker),
      m: menus,
      c: scored,
      s: String(stage || "unknown"),
      u: clean(location.href, 120),
      a: snap(document.activeElement)
    };
    lastThinkingDiagnostic = (String(stage || "unknown") + ":" + JSON.stringify(payload)).slice(0, 3500);
    return lastThinkingDiagnostic;
  }

  function highVerified() {
    // Live ChatGPT DOM (2026-09-30) exposes thinking effort separately from
    // the model switcher, e.g. aria-label="High selector". Treat that
    // dedicated effort control as the authoritative current-effort signal.
    if (effortPickerShowsHigh() || pickerShowsHigh() || selectedHighOption()) return true;
    if (thinkingSliders().some(el => isHigh(label(el)))) return true;
    return thinkingControls().some(el => isHigh(label(el)) && selected(el));
  }

  function key(el, keyName) {
    if (!el) return;
    el.focus?.();
    el.dispatchEvent(new KeyboardEvent("keydown", {key: keyName, bubbles: true, cancelable: true}));
    el.dispatchEvent(new KeyboardEvent("keyup", {key: keyName, bubbles: true, cancelable: true}));
  }

  async function setSliderHigh(slider) {
    if (!slider) return false;
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const current = label(slider);
      if (isHigh(current)) return true;
      if (/extra\s+high|very\s+high|zeer\s+hoog/i.test(current)) key(slider, "ArrowLeft");
      else key(slider, "ArrowRight");
      await sleep(180);
    }
    if (isHigh(label(slider))) return true;

    key(slider, "Home");
    await sleep(160);
    for (let attempt = 0; attempt < 4; attempt += 1) {
      if (isHigh(label(slider))) return true;
      key(slider, "ArrowRight");
      await sleep(180);
    }
    return isHigh(label(slider));
  }

  function pickerButton() {
    const explicit = [...document.querySelectorAll(MODEL_PICKER_SELECTOR)].find(visible);
    if (explicit) return explicit;
    return thinkingControls().find(el => {
      const role = String(el.getAttribute?.("role") || "").toLowerCase();
      if (role === "menuitem" || role === "menuitemradio" || role === "option") return false;
      const text = label(el).toLowerCase();
      const testId = String(el.getAttribute?.("data-testid") || "").toLowerCase();
      return !!el.getAttribute?.("aria-haspopup") ||
        testId.includes("model") || testId.includes("thinking") || testId.includes("reasoning") ||
        /model|thinking|reasoning|denk|redeneer|instant|medium|high|hoog|hard/.test(text);
    }) || null;
  }

  async function ensureHighThinking() {
    if (provider() !== "chatgpt") return true;
    if (highVerified()) {
      lastThinkingDiagnostic = "";
      return true;
    }

    const slider = thinkingSliders()[0] || null;
    if (slider && await setSliderHigh(slider) && highVerified()) return true;

    // Prefer ChatGPT's dedicated thinking-effort selector over the model
    // switcher. The live UI exposes controls such as "High selector".
    const picker = thinkingEffortPicker() || pickerButton();
    if (!picker) {
      compactThinkingDiagnostic("picker-not-found");
      return false;
    }
    picker.click();
    await sleep(450);
    // Some ChatGPT layouts reveal the current effort control only after
    // opening the mode picker. Its High label is already authoritative.
    if (highVerified()) {
      lastThinkingDiagnostic = "";
      return true;
    }

    const openedSlider = thinkingSliders()[0] || null;
    if (openedSlider && await setSliderHigh(openedSlider) && highVerified()) return true;

    const exactHigh = /^(?:high|hoog|think\s+hard|think\s+harder|denk\s+hard|denk\s+harder|hard|harder)(?:\b|\s)/i;
    const highOption = [...new Set([...thinkingOptions(), ...thinkingControls()])].find(el =>
      (exactHigh.test(label(el)) || isHigh(label(el))) && el !== picker
    );
    if (!highOption) {
      compactThinkingDiagnostic("high-option-not-found");
      document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
      return false;
    }
    highOption.click();

    let deadline = Date.now() + 3000;
    while (Date.now() < deadline) {
      if (highVerified()) return true;
      await sleep(120);
    }

    // ChatGPT currently closes the Radix menu after selection. Re-open once
    // and verify the checked/selected High/Think Hard option before sending.
    const verifyPicker = pickerButton();
    if (verifyPicker && !selectedHighOption() && !pickerShowsHigh()) {
      verifyPicker.click();
      await sleep(350);
      deadline = Date.now() + 1200;
      while (Date.now() < deadline) {
        if (highVerified()) return true;
        await sleep(120);
      }
      document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
    }
    const verified = highVerified();
    if (verified) {
      lastThinkingDiagnostic = "";
      return true;
    }
    compactThinkingDiagnostic("selection-not-verifiable");
    return false;
  }

  async function fill(text) {
    const box = composer();
    if (!box) return false;
    box.focus();
    if (box.tagName === "TEXTAREA" || box.tagName === "INPUT") {
      const proto = box.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) setter.call(box, text);
      else box.value = text;
      box.dispatchEvent(new Event("input", {bubbles: true}));
    } else {
      document.execCommand("selectAll", false, null);
      document.execCommand("insertText", false, text);
      box.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: text}));
    }
    await sleep(350);
    return true;
  }

  function statusPayload(event, extra = {}) {
    const text = assistantText();
    return {
      projectId: target?.project_id || "",
      baseProjectId: baseProjectId(),
      workerSlot: Number(target?.worker_slot || 1),
      globalWorkerSlot: Number(target?.global_worker_slot || target?.worker_slot || 1),
      queueItem: String(target?.queue_item?.queue_id || ""),
      projectName: target?.name || "",
      target: location.href,
      targetConversation: conversationId(),
      title: document.title,
      event,
      at: new Date().toISOString(),
      generating: generationActive(),
      sending,
      progressAt: new Date(lastProgressAt).toISOString(),
      assistantCharacters: text.length,
      runner: "violentmonkey",
      provider: isWorkerProvider() ? provider() : candidateProvider(target),
      runnerVersion: SCRIPT_VERSION,
      ...extra
    };
  }

  async function status(event, extra = {}) {
    if (!target) return;
    try { await gmRequest("/runner-status", {method: "POST", body: statusPayload(event, extra)}); }
    catch (_) {}
  }

  async function commandResult(commandId, resultStatus, result) {
    if (!commandId) return;
    try {
      await gmRequest("/runner-command-result", {
        method: "POST",
        body: {command_id: commandId, status: resultStatus, result}
      });
    } catch (_) {}
  }

  function handoffKey() {
    const claim = target?.replacement_handoff?.claim || null;
    return String(claim?.claim_key || target?.replacement_handoff?.prepared_at || "").trim();
  }

  function handoffAlreadyConsumed() {
    const key = handoffKey();
    if (!key) return true;
    try { return sessionStorage.getItem("zcloud-handoff-consumed:" + key) === "1"; }
    catch (_) { return false; }
  }

  function promptWithHandoffAndRecovery(basePrompt) {
    // The worker message must be byte-for-byte the canonical backend prompt.
    // Handoff/retry state is telemetry/control-plane context, never prompt text.
    return String(basePrompt || "");
  }

  function markHandoffConsumed() {
    const key = handoffKey();
    if (!key) return;
    try { sessionStorage.setItem("zcloud-handoff-consumed:" + key, "1"); } catch (_) {}
  }


  // Explains WHY assignmentReady() is false, so a refused send is visible on the dashboard
  // instead of a silent `return false`. Keep the predicates identical to assignmentReady().
  function assignmentDiagnostic(candidate) {
    if (!candidate) return "no-target";
    const problems = [];
    if (candidate.active !== true) problems.push("not-active");
    if (candidate.assignment_ready !== true) problems.push("server-assignment-not-ready");
    const prompt = String(candidate.prompt || "");
    const baseProject = String(candidate.base_project_id || candidate.project_id || "").split("::w", 1)[0];
    if (candidate.reviewer_mode === true) {
      if (baseProject !== "portfolio-review") problems.push("reviewer-wrong-project");
      if (!prompt.includes("Portfolio Bird's-eye Reviewer")) problems.push("reviewer-prompt-missing-role");
      if (!prompt.includes("Senior Team OS")) problems.push("reviewer-prompt-missing-policy");
      return problems.join(",");
    }
    const queueId = String(candidate.queue_item?.queue_id || "").trim();
    const slot = Number(candidate.global_worker_slot || 0);
    const total = Number(candidate.global_worker_count || 0);
    if (!queueId) problems.push("missing-queue-id");
    if (!(Number.isInteger(slot) && slot >= 1)) problems.push("bad-global-slot");
    if (!(Number.isInteger(total) && total >= slot)) problems.push("bad-global-count");
    if (!prompt.startsWith("Werk verder aan ")) problems.push("prompt-missing-project-instruction");
    if (!prompt.includes("Kijk in Notion in welke fase het project zit")) problems.push("prompt-missing-notion-phase");
    return problems.join(",");
  }

  async function reportSendBlocked(reason) {
    const nowMs = Date.now();
    if (lastSendBlockedReport.reason === reason && nowMs - lastSendBlockedReport.at < 60000) return;
    lastSendBlockedReport = {reason, at: nowMs};
    await status("send-blocked", {reason});
  }

  async function sendPrompt(reason) {
    const currentProvider = provider();
    if (target && !assignmentReady(target)) {
      await reportSendBlocked("assignment-invalid:" + assignmentDiagnostic(target));
      return false;
    }
    if (!target || !isWorkerProvider(currentProvider) || candidateProvider(target) !== currentProvider || sending || draining || generationActive()) return false;
    const prompt = promptWithHandoffAndRecovery(target.prompt);
    if (!prompt) return false;

    sending = true;
    try {
      if (currentProvider === "chatgpt") {
        const highReady = await ensureHighThinking();
        if (!highReady) {
          const diagnostic = lastThinkingDiagnostic || compactThinkingDiagnostic("high-unverified");
          // Soft fallback: stuur gewoon, meldt zacht dat High niet beschikbaar is
          if (!generationActive() && (!lastThinkingEffortWarningAt || Date.now() - lastThinkingEffortWarningAt > 120000)) {
            await status("thinking-effort-unavailable", {
              diagnostic: diagnostic,
              reason: "high-thinking-picker-unavailable"
            });
            lastThinkingEffortWarningAt = Date.now();
          }
          // Continue anyway - don't block
        }
      }

      if (!(await fill(prompt))) {
        await status("send-blocked", {reason: "composer-missing"});
        return false;
      }
      const button = sendButton();
      if (buttonUnavailable(button)) {
        await status("send-blocked", {reason: "send-button-unavailable"});
        return false;
      }

      button.click();
      markHandoffConsumed();
      qualityRetryPending = false;
      lastPromptSentAt = Date.now();
      lastProgressAt = Date.now();
      awaitingGeneration = true;
      generationDeadline = Date.now() + timing.generationStartTimeoutMs;
      sawGeneration = false;
      finishedAt = 0;
      await status("prompt-sent", {
        reason,
        thinkingEffort: currentProvider === "chatgpt" ? REQUIRED_THINKING_EFFORT : "provider-default"
      });
      return true;
    } finally {
      await sleep(600);
      sending = false;
    }
  }

  function nextTaskFromText(text) {
    const match = text.match(/ZCLOUD_NEXT_TASK:\s*([^\n]+)/i);
    if (!match) return null;
    const parts = Object.fromEntries(match[1].split(";").map(part => {
      const index = part.indexOf("=");
      return index > 0 ? [part.slice(0, index).trim().toLowerCase(), part.slice(index + 1).trim()] : ["", ""];
    }).filter(([key]) => key));
    if (!parts.title) return null;
    return {
      project_id: String(parts.project || baseProjectId() || "").toLowerCase(),
      priority: String(parts.priority || "P2").toUpperCase(),
      title: parts.title,
      completion_criteria: parts.criteria || ""
    };
  }

  function cycleQuality(text, now) {
    const elapsedMs = lastPromptSentAt ? Math.max(0, now - lastPromptSentAt) : null;
    const queueResult = /ZCLOUD_QUEUE_RESULT:\s*(DONE|BLOCKED|CONTINUE)\b/i.exec(text || "");
    const queueEvidence = /ZCLOUD_QUEUE_EVIDENCE:\s*([^\n]+)/i.exec(text || "");
    const hasQueueResult = !!queueResult;
    const hasQueueEvidence = !!queueEvidence && queueEvidence[1].trim().length > 0;
    const waitHuman = /ZCLOUD_AUTONOMY:\s*WAIT_HUMAN\b/i.test(text || "");
    const waitVps = /ZCLOUD_AUTONOMY:\s*WAIT_VPS\b/i.test(text || "");
    const blocked = queueResult?.[1]?.toUpperCase() === "BLOCKED";
    const tooShort = String(text || "").trim().length < 500;
    const tooFast = elapsedMs !== null && elapsedMs <= 15000;
    const missingEvidence = hasQueueResult && !hasQueueEvidence;
    const recoverableBlocker = !waitHuman && (blocked || waitVps);
    return {
      weak: (!hasQueueResult && (tooShort || tooFast)) || missingEvidence || recoverableBlocker,
      elapsedMs,
      tooShort,
      tooFast,
      missingEvidence,
      recoverableBlocker,
      waitHuman
    };
  }

  async function scheduleQualityRetry(reason, details = {}) {
    qualityRetryCount += 1;
    qualityRetryPending = true;
    await status("quality-retry-scheduled", {
      reason,
      retryNumber: qualityRetryCount,
      retryLimit: "unbounded",
      adjustment: "same-assignment-non-stopping-execution-recovery",
      ...details
    });
  }

  async function reportFinishSignals(text) {
    const queueResult = text.match(/ZCLOUD_QUEUE_RESULT:\s*(DONE|BLOCKED|CONTINUE)\b/i);
    const queueEvidence = text.match(/ZCLOUD_QUEUE_EVIDENCE:\s*([^\n]+)/i);
    const queueItem = text.match(/ZCLOUD_QUEUE_ITEM:\s*([^\s\n]+)/i);
    if (queueResult) {
      await status("portfolio-queue-result", {
        queueItem: queueItem ? queueItem[1].trim() : String(target?.queue_item?.queue_id || ""),
        queueResult: queueResult[1].toUpperCase(),
        queueEvidence: queueEvidence ? queueEvidence[1].trim().slice(0, 4000) : "",
        nextTask: nextTaskFromText(text)
      });
    }

    const workProject = text.match(/ZCLOUD_WORK_PROJECT:\s*(HAXLAB|FTMO|CLOUD|SUPA|RAISEAI|ULAB|ZSSH|NONE)\b/i);
    if (workProject?.[1]?.toLowerCase() === "cloud") {
      if (text.includes("ZCLOUD_ITERATION_COMPLETE")) await status("improvement-iteration-complete");
      if (text.includes("ZCLOUD_FINISH_REVIEW: GREEN_NO_P0P1")) await status("improvement-review-green");
      if (text.includes("ZCLOUD_FINISH_REVIEW: OPEN_P0P1")) await status("improvement-review-open");
      if (text.includes("ZCLOUD_FINAL_AUDIT: GREEN")) await status("improvement-audit-green");
      if (text.includes("ZCLOUD_FINAL_AUDIT: FAIL")) await status("improvement-audit-failed");
    }
  }

  async function handleCommands() {
    if (!target?.project_id) return;
    let payload;
    try { payload = await gmRequest("/runner-commands"); }
    catch (_) { return; }

    const commands = (payload.commands || []).filter(command => {
      if (!command || Number(command.id || 0) <= lastHandledCommandId) return false;
      const sameWorker = command.project_id === target.project_id;
      const basePush = command.project_id === target.base_project_id && Number(target.worker_slot || 1) === 1;
      return sameWorker || basePush;
    }).sort((a, b) => Number(a.id || 0) - Number(b.id || 0));

    for (const command of commands) {
      const id = Number(command.id || 0);
      if (command.action === "push") {
        const ok = await sendPrompt("database-push");
        await commandResult(id, ok ? "completed" : "failed", ok ? "Prompt sent by Violentmonkey worker" : "Violentmonkey worker could not send safely");
        lastHandledCommandId = Math.max(lastHandledCommandId, id);
      } else if (command.action === "drain") {
        draining = true;
        await status("runner-draining", {reason: "database-drain"});
        await commandResult(id, "completed", "Violentmonkey worker will stop after current generation");
        lastHandledCommandId = Math.max(lastHandledCommandId, id);
      }
    }
  }

  async function tick() {
    if (!target) return;

    await handleCommands();

    const generating = generationActive();
    const text = assistantText();
    const now = Date.now();

    if (lastGenerating === null) lastGenerating = generating;
    if (generating !== lastGenerating) {
      lastGenerating = generating;
      if (generating) {
        sawGeneration = true;
        awaitingGeneration = false;
        finishedAt = 0;
        lastProgressAt = now;
        await status("generation-started");
      } else {
        finishedAt = now;
        await status("generation-finished");
      }
    }

    if (generating) {
      if (text !== lastAssistantText) {
        lastAssistantText = text;
        lastProgressAt = now;
        await status("generation-progress");
      }
      return;
    }

    if (awaitingGeneration) {
      if (text && text !== lastAssistantText) {
        awaitingGeneration = false;
        sawGeneration = true;
        finishedAt = now;
        lastAssistantText = text;
        await status("generation-started", {reason: "response-detected-between-polls"});
        await status("generation-finished", {reason: "response-detected-between-polls"});
      } else if (now >= generationDeadline) {
        awaitingGeneration = false;
        await status("generation-not-started", {reason: "no-generation-after-send"});
      }
      return;
    }

    if (draining && !sawGeneration) {
      await status("runner-drained", {reason: "already-idle"});
      return;
    }

    if (sawGeneration && finishedAt && now - finishedAt >= 900) {
      sawGeneration = false;
      finishedAt = 0;
      lastAssistantText = text;
      const quality = cycleQuality(text, now);
      await reportFinishSignals(text);
      if (quality.weak) {
        await scheduleQualityRetry("short-or-invalid-result", {
          cycleSeconds: quality.elapsedMs === null ? null : Math.round(quality.elapsedMs / 1000),
          assistantCharacters: String(text || "").length,
          tooShort: quality.tooShort,
          tooFast: quality.tooFast,
          missingQueueEvidence: quality.missingEvidence,
          recoverableBlocker: quality.recoverableBlocker
        });
      } else if (qualityRetryCount > 0) {
        qualityRetryPending = false;
        qualityRetryCount = 0;
        await status("quality-retry-cleared", {reason: "valid-generation"});
      }
      if (draining) {
        await status("runner-drained", {reason: "current-task-finished"});
        return;
      }
      await status("awaiting-vps-dispatch", {reason: "cycle-finished"});
    }
  }

  async function heartbeat() {
    if (!target) return;
    renewClaim(target);
    await status("heartbeat", {reason: "violentmonkey-primary-runner", provider: provider()});
  }

  function scheduleTimers() {
    if (refreshTimer) clearInterval(refreshTimer);
    if (tickTimer) clearInterval(tickTimer);
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    refreshTimer = setInterval(() => refreshTarget().catch(() => {}), timing.refreshMs);
    tickTimer = setInterval(() => tick().catch(() => {}), timing.tickMs);
    heartbeatTimer = setInterval(() => heartbeat().catch(() => {}), timing.heartbeatMs);
  }

  async function start() {
    document.documentElement?.setAttribute("data-zcloud-violentmonkey-ready", SCRIPT_VERSION);
    document.documentElement?.setAttribute("data-zcloud-provider", provider());
    window.dispatchEvent(new Event("zcloud-violentmonkey-ready"));
    await refreshTarget();
    await status("runner-started", {
      reason: "violentmonkey-primary-runner",
      scriptVersion: SCRIPT_VERSION,
      provider: isWorkerProvider() ? provider() : candidateProvider(target)
    });
    scheduleTimers();
  }

  start().catch(() => {});
})();
