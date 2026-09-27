const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const manifest = JSON.parse(
  fs.readFileSync(path.join(root, "firefox-extension", "manifest.json"), "utf8")
);
const background = fs.readFileSync(
  path.join(root, "firefox-extension", "background.js"), "utf8"
);

assert.ok(
  manifest.permissions.includes("sessions"),
  "Firefox sessions permission is required for persistent worker-tab identity"
);
assert.deepEqual(
  manifest.background.scripts.slice(0, 2),
  ["recovery.js", "background.js"],
  "recovery helper must load before background.js"
);

for (const marker of [
  "browser.sessions.setTabValue",
  "browser.sessions.getTabValue",
  "browser.sessions.removeTabValue",
  "Recovery.selectRecoveryTab",
  "target-tab-recovered",
  "await setRecoveryTag(opened.id",
  "await clearRecoveryTag(tabId)",
  "const intentionalTabClosures = new Set()",
  "const pendingTabHandoffs = new Set()",
  "async function closeRunnerTab",
  "async function recoverClosedWorker",
  "worker-handoff-started",
  "worker-handoff-opened",
  "worker-handoff-failed",
  "intentionalTabClosures.delete(tabId)",
  'recoverClosedWorker(target, "unexpected-tab-closed")',
  'target.desired_state === "paused"',
  'const REPLACEMENT_HANDOFF_SESSION_KEY = "zcloud-replacement-handoff-v1"',
  "async function prepareReplacementHandoff",
  'fetch(API + "/task-claims?project="',
  "claims.length > 1",
  "compactClaimForHandoff",
  "previous_conversation_id",
  "target.replacement_handoff = handoff",
  "await setReplacementHandoffTag(tab.id, handoff)",
  "await getReplacementHandoffTag(tabId)",
  "BEWUSTE WORKER-HANDOFF",
  "Neem GEEN nieuwe taakclaim",
  "runner-replacement-handoff-consumed",
  "worker-replacement-handoff-consumed",
  "const BASE_PROMPT = cfg.prompt",
  "let PROMPT = REPLACEMENT_HANDOFF",
  "PROMPT = BASE_PROMPT",
  "await clearReplacementHandoffTag(tabId)",
  'target.desired_state === "paused"',
  'target.desired_state === "draining"',
  "if (pendingTabHandoffs.has(target.project_id)) continue;",
]) {
  assert.ok(background.includes(marker), "missing recovery contract marker: " + marker);
}


const directRemoveCalls = background.match(/browser\.tabs\.remove\(/g) || [];
assert.equal(
  directRemoveCalls.length,
  1,
  "all intentional worker-tab closes must flow through closeRunnerTab"
);
for (const marker of [
  "await closeRunnerTab(tabId)",
  "await closeRunnerTab(assignedTabId)",
  "await closeRunnerTab(oldTab)",
]) {
  assert.ok(background.includes(marker), "intentional close is not handoff-suppressed: " + marker);
}

console.log("Firefox recovery manifest/background contract checks passed.");


const replacementStart = background.indexOf("async function newProjectChat");
const replacementEnd = background.indexOf("async function startProject");
assert.ok(replacementStart >= 0 && replacementEnd > replacementStart, "newProjectChat block missing");
const replacementBlock = background.slice(replacementStart, replacementEnd);
assert.ok(
  replacementBlock.indexOf("await prepareReplacementHandoff(target, reason)") <
    replacementBlock.indexOf('browser.tabs.sendMessage(oldTab'),
  "claim/task handoff must be captured before stopping the old runner"
);
assert.ok(
  replacementBlock.indexOf("await prepareReplacementHandoff(target, reason)") <
    replacementBlock.indexOf("await closeRunnerTab(oldTab)"),
  "claim/task handoff must be captured before closing the old tab"
);
assert.ok(
  !replacementBlock.includes('"action":"release"') && !replacementBlock.includes("'action':'release'"),
  "deliberate replacement must preserve the existing task lease instead of releasing it"
);

const runStart = background.indexOf("function runProject");
const refreshStart = background.indexOf("function postStatus");
assert.ok(runStart >= 0 && refreshStart > runStart, "runProject block missing");
const runBlock = background.slice(runStart, refreshStart);
assert.ok(
  runBlock.indexOf("PROMPT = BASE_PROMPT") >
    runBlock.indexOf("button.click();"),
  "replacement prompt must become one-shot only after the first send succeeds"
);
assert.ok(
  runBlock.indexOf("button.click();") <
    runBlock.indexOf('type: "runner-replacement-handoff-consumed"'),
  "replacement handoff may only be cleared after the first prompt was actually sent"
);
