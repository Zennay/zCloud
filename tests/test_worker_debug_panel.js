// Executes the real worker-diagnostics block from public/app.js (extracted by markers) and checks the output.
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const src = fs.readFileSync(path.resolve(__dirname, "../public/app.js"), "utf8");
const start = src.indexOf("/*wd:start*/");
const end = src.indexOf("/*wd:end*/");
assert.ok(start >= 0 && end > start, "worker diagnostics block markers missing in app.js");

const esc = x => String(x ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const $ = () => null;
const api = new Function("esc", "$", src.slice(start, end) +
  ";return {workerDebugPanel, verdictClass, ageText, driverLine, setDebug(d){WORKER_DEBUG=d}, setError(e){WORKER_DEBUG_ERROR=e}};")(esc, $);

// loading state has no global push-all control
let html = api.workerDebugPanel();
assert.ok(html.includes("Loading worker diagnostics"));
assert.ok(!html.includes("data-force-push-all") && !html.includes("Force push all"));

// populated state
api.setDebug({
  workers: [
    {worker: "cloud::w1", project: "cloud", slot: 1, provider: "chatgpt", queue_id: "cloud-q", verdict: "generating", reason: "", last_event: "heartbeat", last_event_age_s: 12, last_prompt_age_s: 40, desired_state: "running"},
    {worker: "raiseai::w1", project: "raiseai", slot: 1, provider: "claude", queue_id: "raiseai-q", verdict: "blocked:contract", reason: "prompt-missing-worker-line", last_event: null, last_event_age_s: null, last_prompt_age_s: null, desired_state: "running"},
  ],
  events_15m: {heartbeat: 30, "prompt-sent": 2, "assignment-invalid": 0}, failed_commands_15m: 7, firefox: {active: true},
});
html = api.workerDebugPanel();
assert.ok(html.includes("Generating") && html.includes("wd-ok"));
assert.ok(html.includes("Blocked · prompt contract") && html.includes("wd-bad"));
assert.ok(html.includes("prompt-missing-worker-line"));
assert.ok(html.includes("failed commands ×7") && html.includes("Firefox initiator active"));
assert.ok(html.includes("12s ago") && html.includes("prompt 40s ago"));
assert.ok(html.includes('data-worker-action="push"'));
assert.ok(html.includes('data-worker-id="cloud::w1"') && html.includes('data-worker-id="raiseai::w1"'));
assert.strictEqual((html.match(/>Push now<\/button>/g)||[]).length, 2);

// everything from the API is escaped
api.setDebug({workers: [{project: "<b>x</b>", slot: 1, provider: "chatgpt", verdict: "blocked:client", reason: "<img src=x onerror=alert(1)>"}], events_15m: {}});
html = api.workerDebugPanel();
assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;") && !html.includes("<img src=x"));
assert.ok(!html.includes("<b>x</b>"));

// error state
api.setError("Diagnostics are only visible from a trusted admin device.");
html = api.workerDebugPanel();
assert.ok(html.includes("trusted admin device"));

// helpers
assert.strictEqual(api.verdictClass("prompt-sent"), "ok");
assert.strictEqual(api.verdictClass("no-heartbeat"), "bad");
assert.strictEqual(api.verdictClass("idle:no-assignment"), "idle");
assert.strictEqual(api.ageText(null), "—");
assert.strictEqual(api.ageText(3600), "60 min ago");

// wiring: overview renders the panel, refresh loads it, click handler + endpoint are hooked up
const overview = src.slice(src.indexOf("function overview(){"), src.indexOf("\nfunction aiRunPanel"));
assert.ok(overview.includes('globalWorkerConsole()'));
assert.ok(src.includes('data-disclosure="worker-diagnostics"'));
assert.ok(overview.indexOf('globalWorkerConsole()') < overview.indexOf('class="project-grid" id="projectGrid"'), "workers must precede projects");
assert.ok(src.includes("if(route==='overview')loadWorkerDebug()"));
assert.ok(src.includes("[data-worker-action]") && src.includes("/api/runner-control"));
assert.ok(!src.includes("data-force-push-all"), "global dynamic-worker push must stay removed");
assert.ok(!src.includes("/api/dynamic-workers/force-push"), "dashboard must not call the global force-push endpoint");
assert.ok(!src.includes("data-runner-force-push"), "per-project force push must stay removed");

console.log("worker debug panel OK");
