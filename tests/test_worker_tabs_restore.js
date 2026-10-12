// Run with: node tests/test_worker_tabs_restore.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const Recovery = require("../firefox-extension/recovery.js");
const bg = fs.readFileSync(path.join(root, "firefox-extension/background.js"), "utf8");
const script = fs.readFileSync(path.join(root, "public/zcloud-worker.user.js"), "utf8");
const unit = fs.readFileSync(path.join(root, "deploy/chatgpt-firefox.service"), "utf8");

// 6 independently allocated slots must each reclaim precisely their own tab;
// a human assistant's separate profile/conversation must never be adopted.
const targets = Array.from({length: 6}, (_, i) => ({
  project_id: `project-${i + 1}::w1`,
  conversation_id: `${String(i + 1).padStart(8, "0")}-aaaa-bbbb-cccc-0123456789ab`
}));
const tabs = targets.map((t, i) => ({
  id: i + 1, url: `https://chatgpt.com/c/${t.conversation_id}`
}));
tabs.push({id: 999, url: "https://chatgpt.com/c/12345678-bbbb-aaaa-1111-ffffffffffff"}); // Maya
const selected = new Set();
for (const target of targets) {
  const result = Recovery.selectRecoveryTab(target, tabs, {}, selected);
  assert.ok(result?.tab, "must restore worker " + target.project_id);
  assert.equal(result.reason, "conversation");
  selected.add(result.tab.id);
}
assert.equal(selected.size, targets.length);
assert.ok(!selected.has(999), "Maya must never become a dynamic worker");

// A new chat may not be rebound to an arbitrary saved session from another
// worker. Once the original worker session tag exists, that tab is reclaimable.
const newChat = {id: 42, url: "https://chatgpt.com/"};
assert.equal(Recovery.selectRecoveryTab(
  {project_id: "cloud::w3", conversation_id: ""},
  [newChat], {42: "cloud::w2"}
), null);
assert.equal(Recovery.selectRecoveryTab(
  {project_id: "cloud::w3", conversation_id: ""},
  [newChat], {42: "cloud::w3"}
).reason, "session");

// Keep the existing first-party WebExtension tab controller plus VM DOM worker,
// NOT a second competing Office message consumer.
for (const marker of [
  "const recoveryTabs = await pruneInactiveRestoredTabs(tabs, restoredAssignments, incoming)",
  "Recovery.selectRecoveryTab(",
  "await setRecoveryTag(opened.id, target.project_id)",
  "await prepareReplacementHandoff(target, reason)",
  "await drainRunnerBeforeReplacement(reusableTab.id, target, reason)",
  "await verifyReplacementHandoffStillCurrent(target, handoff)",
  "pendingAdoptions[tab.id] = target.project_id",
  "async function adoptConversation("
]) assert.ok(bg.includes(marker), "missing worker safety contract " + marker);
for (const marker of ["zcloud_worker", "zcloud_tab", "zcloud_recover", "conversation-adopted"]) {
  assert.ok(script.includes(marker), "userscript navigation identity lost: " + marker);
}
assert.ok(unit.includes("KillMode=control-group"));
assert.ok(unit.includes("Restart=on-abnormal"),"profile-lock conflict must not start a restart loop");
console.log("Worker pool 6/6 recovery and expert tab isolation: PASS");
