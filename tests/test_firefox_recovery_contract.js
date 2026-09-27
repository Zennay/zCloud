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
