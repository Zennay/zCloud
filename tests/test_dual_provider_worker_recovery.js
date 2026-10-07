const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const recovery = require("../firefox-extension/recovery.js");

const claudeId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const claudeTarget = {
  project_id: "zguard::w1",
  provider: "claude",
  conversation_id: claudeId
};
const chatgptTarget = {
  project_id: "cloud::w1",
  provider: "chatgpt",
  conversation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
};

assert.equal(recovery.providerForTarget(claudeTarget), "claude");
assert.equal(recovery.newChatUrl(claudeTarget), "https://claude.ai/new");
assert.equal(
  recovery.conversationUrl(claudeTarget, claudeId),
  "https://claude.ai/chat/" + claudeId
);
assert.equal(
  recovery.conversationFromUrl("https://claude.ai/chat/" + claudeId),
  claudeId
);

const tabs = [
  {id: 1, url: "https://chatgpt.com/c/" + chatgptTarget.conversation_id},
  {id: 2, url: "https://claude.ai/chat/" + claudeId},
  {id: 3, url: "https://claude.ai/new"}
];
let picked = recovery.selectRecoveryTab(claudeTarget, tabs, {3: "zguard::w1"}, new Set());
assert.equal(picked.tab.id, 2, "Claude recovery must prefer its persisted Claude conversation");
assert.equal(picked.reason, "conversation");

picked = recovery.selectRecoveryTab(
  {...claudeTarget, conversation_id: ""},
  tabs,
  {3: "zguard::w1"},
  new Set()
);
assert.equal(picked.tab.id, 3, "Claude pre-adoption recovery must use its Claude session tab");

const root = path.resolve(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(root, "firefox-extension/manifest.json"), "utf8"));
for (const origin of ["https://chatgpt.com/*", "https://claude.ai/*", "https://claude.com/*"]) {
  assert.ok(manifest.permissions.includes(origin), "missing extension host permission: " + origin);
}

const background = fs.readFileSync(path.join(root, "firefox-extension/background.js"), "utf8");
assert.ok(background.includes("workerNewChatUrl(target)"));
assert.ok(background.includes('"https://claude.ai/*"'));
assert.ok(background.includes('"https://claude.com/*"'));
assert.ok(background.includes("claude-requires-violentmonkey-primary-runner"));
assert.ok(!background.includes('target.url = "https://chatgpt.com/";'));
assert.ok(background.includes("browser.tabs.create({url: target.url, active: true})"));

console.log("Dual-provider worker recovery checks passed.");
