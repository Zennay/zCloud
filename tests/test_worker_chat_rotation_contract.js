const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const background = fs.readFileSync(
  path.join(root, "firefox-extension", "background.js"),
  "utf8"
);
const server = fs.readFileSync(path.join(root, "server.py"), "utf8");

for (const marker of [
  'const CONVERSATION_LOAD_FAILURE_SCAN_MS = 5000',
  'const MAX_GENERATIONS_PER_CHAT = 6',
  'async function detectConversationLoadFailure',
  'could not load this chatgpt conversation',
  'conversation not found',
  'async function watchConversationLoadFailures',
  'chatgpt-conversation-load-failed',
  'generation-budget-rotation',
  'setInterval(watchConversationLoadFailures, CONVERSATION_LOAD_FAILURE_SCAN_MS)',
]) {
  assert.ok(background.includes(marker), "missing worker-chat recovery marker: " + marker);
}

const replacementStart = background.indexOf("async function newProjectChat");
const replacementEnd = background.indexOf("async function startProject", replacementStart);
assert.ok(replacementStart >= 0 && replacementEnd > replacementStart, "newProjectChat block missing");
const replacement = background.slice(replacementStart, replacementEnd);
const resetIndex = replacement.indexOf('event: "conversation-reset-requested"');
const clearLocalIndex = replacement.indexOf('target.conversation_id = ""');
const openIndex = replacement.indexOf('browser.tabs.create({url: "https://chatgpt.com/"');
assert.ok(resetIndex >= 0, "fresh chat must reset the durable conversation binding");
assert.ok(clearLocalIndex > resetIndex, "local stale conversation id must clear after backend reset request");
assert.ok(openIndex > clearLocalIndex, "replacement tab must open only after stale conversation binding is cleared");

assert.ok(
  server.includes("if event == 'conversation-reset-requested'"),
  "backend must handle explicit conversation reset requests"
);
assert.ok(
  server.includes("DO UPDATE SET conversation_id='',provider=excluded.provider"),
  "worker slot binding must be cleared durably"
);
assert.ok(
  server.includes("UPDATE runner_targets SET conversation_id='' WHERE project_id=?"),
  "primary runner target must not redirect a fresh tab back to the broken conversation"
);

console.log("Worker chat load-failure recovery and rotation contract checks passed.");
