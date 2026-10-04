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
  "runner-replacement-handoff-consumed",
  "worker-replacement-handoff-consumed",
  "let BASE_PROMPT = cfg.prompt",
  "let PROMPT = promptWithQualityRecovery(BASE_PROMPT)",
  "PROMPT = promptWithQualityRecovery(BASE_PROMPT)",
  "return clearReplacementHandoffTag(tabId).then",
  'target.desired_state === "paused"',
  'target.desired_state === "draining"',
  "if (pendingTabHandoffs.has(target.project_id)) continue;",
  "function portfolioAssignmentReady(target)",
  "runner-config-update",
  "runner-config-updated",
  "assignment-invalid",
  "assignment-refresh-failed",
  "async function syncRunnerConfig",
  "runnerConfigChanged(previous, target)",
  "Werk verder aan ",
  "Kijk in Notion in welke fase het project zit",
]) {
  assert.ok(background.includes(marker), "missing recovery contract marker: " + marker);
}


for (const forbidden of [
  "Ga verder met hetzelfde vrije werkgebied",
  "neem geen werk over dat al door een andere worker wordt uitgevoerd",
  "Werk verder aan het project en voer nu een concrete volgende stap uit",
]) {
  assert.ok(!background.includes(forbidden), "worker prompt augmentation must stay removed: " + forbidden);
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
assert.ok(
  !background.includes("doe alleen read-only werk"),
  "replacement handoff must reclaim and continue instead of falling back to read-only work"
);

const runStart = background.indexOf("function runProject");
const refreshStart = background.indexOf("function postStatus");
assert.ok(runStart >= 0 && refreshStart > runStart, "runProject block missing");
const runBlock = background.slice(runStart, refreshStart);
const handoffPromptReset = runBlock.indexOf(
  "PROMPT = promptWithQualityRecovery(BASE_PROMPT)",
  runBlock.indexOf("button.click();")
);
assert.ok(
  handoffPromptReset > runBlock.indexOf("button.click();"),
  "replacement prompt must become one-shot only after the first send succeeds"
);
assert.ok(
  runBlock.indexOf("button.click();") <
    runBlock.indexOf('type: "runner-replacement-handoff-consumed"'),
  "replacement handoff may only be cleared after the first prompt was actually sent"
);

const configUpdateStart = runBlock.indexOf('if (message.type === "runner-config-update")');
const configUpdateEnd = runBlock.indexOf('if (message.type === "runner-push")', configUpdateStart);
assert.ok(configUpdateStart >= 0 && configUpdateEnd > configUpdateStart, "runner config-update block missing");
const configUpdateBlock = runBlock.slice(configUpdateStart, configUpdateEnd);
assert.ok(
  configUpdateBlock.includes('paused = next.desired_state === "paused"'),
  "config refresh must apply backend paused state inside an already-open worker tab"
);
assert.ok(
  configUpdateBlock.includes('draining = next.desired_state === "draining"'),
  "config refresh must apply backend draining state so deploy safe-idle can be acknowledged"
);

const userscript = fs.readFileSync(
  path.join(root, "public", "zcloud-worker.user.js"),
  "utf8"
);
assert.ok(
  userscript.includes('draining = target.desired_state === "draining"'),
  "Violentmonkey primary runner must remain driven by backend desired_state"
);
assert.ok(
  userscript.includes('if (draining && !sawGeneration)'),
  "an already-idle Violentmonkey worker must acknowledge backend drain state"
);
assert.ok(
  userscript.includes('status("runner-drained", {reason: "already-idle"})'),
  "idle desired-state drain must report runner-drained to the backend"
);

const refreshTargetStart = background.indexOf("async function refreshTargets");
const refreshTargetEnd = background.indexOf("async function inject");
assert.ok(refreshTargetStart >= 0 && refreshTargetEnd > refreshTargetStart, "refreshTargets block missing");
const refreshBlock = background.slice(refreshTargetStart, refreshTargetEnd);
assert.ok(
  refreshBlock.includes("await syncRunnerConfig(assignedTabId, target)"),
  "an already-open worker tab must receive the freshly rendered VPS assignment"
);
assert.ok(
  refreshBlock.indexOf("portfolioAssignmentReady(target)") <
    refreshBlock.indexOf('browser.tabs.create({url: target.url'),
  "invalid assignments must fail closed before opening a worker tab"
);
