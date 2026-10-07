(function(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.ZCloudRecovery = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function() {
  const SESSION_KEY = "zcloud-worker-id";

  function normalizeProvider(value) {
    const provider = String(value || "").trim().toLowerCase();
    return provider === "claude" || provider === "anthropic" ? "claude" : "chatgpt";
  }

  function providerFromUrl(url) {
    try {
      const host = new URL(String(url || "")).hostname.toLowerCase();
      if (host === "claude.ai" || host === "claude.com" || host.endsWith(".claude.ai") || host.endsWith(".claude.com")) return "claude";
      if (host === "chatgpt.com" || host.endsWith(".chatgpt.com")) return "chatgpt";
    } catch (_) {}
    return "";
  }

  function providerForTarget(target) {
    const explicit = String(
      target?.provider || target?.ai_provider || target?.chat_provider || target?.runner_provider || ""
    ).trim().toLowerCase();
    if (explicit === "claude" || explicit === "anthropic") return "claude";
    if (explicit === "chatgpt" || explicit === "openai") return "chatgpt";
    return providerFromUrl(target?.url || target?.conversation_url || target?.target_url) || "chatgpt";
  }

  function newChatUrl(targetOrProvider) {
    const provider = typeof targetOrProvider === "object"
      ? providerForTarget(targetOrProvider)
      : normalizeProvider(targetOrProvider);
    return provider === "claude" ? "https://claude.ai/new" : "https://chatgpt.com/";
  }

  function conversationFromUrl(url) {
    const value = String(url || "");
    const provider = providerFromUrl(value);
    const pattern = provider === "claude"
      ? /\/chat\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:\/|$|[?#])/i
      : /\/c\/([0-9a-f-]{20,})(?:\/|$|[?#])/i;
    const match = value.match(pattern);
    return match ? match[1] : "";
  }

  function conversationUrl(target, conversationId) {
    const id = encodeURIComponent(String(conversationId || "").trim());
    if (!id) return newChatUrl(target);
    return providerForTarget(target) === "claude"
      ? "https://claude.ai/chat/" + id
      : "https://chatgpt.com/c/" + id;
  }

  function matchesProvider(tab, target) {
    return providerFromUrl(tab?.url) === providerForTarget(target);
  }

  function matchesConversation(tab, target) {
    const wanted = String(target?.conversation_id || "");
    return !!wanted && matchesProvider(tab, target) && conversationFromUrl(tab?.url) === wanted;
  }

  function sessionMatches(tab, target, sessionAssignments) {
    return String(sessionAssignments?.[tab?.id] || "") === String(target?.project_id || "");
  }

  function selectRecoveryTab(target, tabs, sessionAssignments = {}, claimedTabIds = new Set()) {
    const available = (tabs || []).filter(tab =>
      tab && tab.id != null &&
      !claimedTabIds.has(tab.id) &&
      matchesProvider(tab, target)
    );

    const byConversation = available.find(tab => matchesConversation(tab, target));
    if (byConversation) return {tab: byConversation, reason: "conversation"};

    const bySession = available.find(tab => {
      if (!sessionMatches(tab, target, sessionAssignments)) return false;
      const persisted = String(target?.conversation_id || "");
      if (!persisted) return true;
      return matchesConversation(tab, target);
    });
    return bySession ? {tab: bySession, reason: "session"} : null;
  }

  return {
    SESSION_KEY,
    providerFromUrl,
    providerForTarget,
    newChatUrl,
    conversationUrl,
    conversationFromUrl,
    matchesProvider,
    matchesConversation,
    sessionMatches,
    selectRecoveryTab
  };
});
