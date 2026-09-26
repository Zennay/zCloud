const assert = require("node:assert/strict");
const recovery = require("../firefox-extension/recovery.js");

function target(project_id, conversation_id = "") {
  return {project_id, conversation_id};
}
const tabs = [
  {id: 10, url: "https://chatgpt.com/"},
  {id: 11, url: "https://chatgpt.com/c/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"},
  {id: 12, url: "https://chatgpt.com/c/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
];

assert.equal(recovery.SESSION_KEY, "zcloud-worker-id");
assert.equal(
  recovery.conversationFromUrl(tabs[1].url),
  "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
);

let picked = recovery.selectRecoveryTab(
  target("cloud::w1", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
  tabs,
  {10: "cloud::w1"},
  new Set()
);
assert.equal(picked.tab.id, 11);
assert.equal(picked.reason, "conversation", "persisted conversation must win over stale session tag");

picked = recovery.selectRecoveryTab(
  target("cloud::w2"),
  tabs,
  {10: "cloud::w2"},
  new Set()
);
assert.equal(picked.tab.id, 10);
assert.equal(picked.reason, "session", "pre-adoption tab must be recoverable from session tag");

picked = recovery.selectRecoveryTab(
  target("cloud::w1", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
  tabs,
  {10: "cloud::w1"},
  new Set([11])
);
assert.equal(picked, null, "stale generic tagged tab must not replace a persisted conversation");

picked = recovery.selectRecoveryTab(
  target("cloud::w2"),
  tabs,
  {10: "cloud::w2"},
  new Set([10])
);
assert.equal(picked, null, "one restored tab may not be claimed by two workers");

picked = recovery.selectRecoveryTab(
  target("cloud::w3"),
  tabs,
  {10: "cloud::w2"},
  new Set()
);
assert.equal(picked, null);

console.log("Firefox worker recovery selection checks passed.");
