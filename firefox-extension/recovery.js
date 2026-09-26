(function(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.ZCloudRecovery = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function() {
  const SESSION_KEY = "zcloud-worker-id";

  function conversationFromUrl(url) {
    const match = String(url || "").match(/\/c\/([0-9a-f-]{20,})/i);
    return match ? match[1] : "";
  }

  function matchesConversation(tab, target) {
    const wanted = String(target?.conversation_id || "");
    return !!wanted && conversationFromUrl(tab?.url) === wanted;
  }

  function sessionMatches(tab, target, sessionAssignments) {
    return String(sessionAssignments?.[tab?.id] || "") === String(target?.project_id || "");
  }

  function selectRecoveryTab(target, tabs, sessionAssignments = {}, claimedTabIds = new Set()) {
    const available = (tabs || []).filter(tab =>
      tab && tab.id != null &&
      !claimedTabIds.has(tab.id) &&
      String(tab.url || "").includes("chatgpt.com")
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
    conversationFromUrl,
    matchesConversation,
    sessionMatches,
    selectRecoveryTab
  };
});
