// Regression: automatic conversation rotation must never close a Firefox tab
// or open a new ChatGPT window while the worker's original tab still exists.
// Run with node tests/test_worker_tab_reuse.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "firefox-extension/background.js"), "utf8");
const start = source.indexOf("async function newProjectChat(");
const end = source.indexOf("async function startProject(", start);
assert.ok(start > 0 && end > start, "newProjectChat implementation must exist");
const trace = [];
const key = "supa::w1";
const target = {project_id:key, name:"Supa", base_project_id:"supa", active:true,
  conversation_id:"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  url:"https://chatgpt.com/c/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"};
const projectTabs = {[key]:42};
const ctx = vm.createContext({
  Set, Map, Object, JSON, Date, Error, String,
  targets:{[key]:target}, tabTargets:{42:target}, projectTabs,
  pendingAdoptions:{}, pendingInitialDispatches:new Set(), runningActions:new Set(),
  workerKeysFor:id=>[id],
  portfolioAssignmentReady:()=>true,
  canOpenWorkerTab:()=>{throw new Error("must not consult open budget for a live tab");},
  prepareReplacementHandoff:async()=>{trace.push("handoff");return {queue_item:{queue_id:"q1"}};},
  drainRunnerBeforeReplacement:async id=>{assert.equal(id,42);trace.push("drain");},
  verifyReplacementHandoffStillCurrent:async()=>{trace.push("verify");},
  browser:{tabs:{
    get:async id=>({id}),
    update:async(id,opts)=>{
      assert.equal(id,42);
      assert.deepEqual(Object.keys(opts), ["url"]);
      assert.equal(opts.url,"https://chatgpt.com/");
      trace.push("navigate");return {id,url:opts.url};
    },
    sendMessage:async()=>{trace.push("stop-old-runner");}
  }},
  postStatus:async data=>{trace.push(data.event);},
  setRecoveryTag:async id=>{assert.equal(id,42);trace.push("tag");},
  setReplacementHandoffTag:async id=>{assert.equal(id,42);trace.push("handoff-tag");},
  createWorkerTab:()=>{throw new Error("existing worker tab must not be replaced");},
  clearRecoveryTag:()=>{throw new Error("rotation may not discard tab identity");},
  closeRunnerTab:()=>{throw new Error("rotation may not close a tab");},
  commandResult:async()=>{trace.push("result");}
});
vm.runInContext(source.slice(start,end),ctx);
(async()=>{
  await vm.runInContext('newProjectChat("supa::w1","generation-budget-rotation","c1")',ctx);
  assert.equal(projectTabs[key],42,"same worker keeps same tab id");
  assert.equal(target.conversation_id,"","old binding is reset only for the new chat");
  assert.equal(ctx.pendingAdoptions[42],key);
  assert.ok(ctx.pendingInitialDispatches.has(key));
  assert.ok(trace.indexOf("handoff") < trace.indexOf("drain"));
  assert.ok(trace.indexOf("verify") < trace.indexOf("navigate"));
  assert.ok(trace.indexOf("conversation-reset-requested") < trace.indexOf("navigate"));
  assert.ok(trace.includes("result"));
  const refresh = source.slice(source.indexOf("async function refreshTargets("),
                               source.indexOf("async function inject(",source.indexOf("async function refreshTargets(")));
  const pause = source.slice(source.indexOf("async function pauseProject("),
                             source.indexOf("async function drainProject("));
  const prune = source.slice(source.indexOf("async function pruneInactiveRestoredTabs("),
                             source.indexOf("async function adoptConversation("));
  for(const block of [refresh,pause,prune]) {
    assert.ok(!block.includes("closeRunnerTab("),"allocation and pause must preserve browser tabs");
    assert.ok(!block.includes("browser.tabs.remove("),"no tab removal in automatic control paths");
  }
  const server = fs.readFileSync(path.join(root, "server.py"), "utf8");
  const restart = server.slice(server.indexOf("def restart_firefox_runtime()"),
                               server.indexOf("# --- Firefox crash watchdog",server.indexOf("def restart_firefox_runtime()")));
  assert.ok(restart.includes("if current.get('active'):"));
  assert.ok(restart.indexOf("if current.get('active'):") < restart.indexOf("os.kill(pid,signal.SIGTERM)"),
    "active browser guard must precede any process termination");
  const workflow=fs.readFileSync(path.join(root,".github/workflows/zcloud-restart-workers.yml"),"utf8");
  assert.ok(workflow.includes("if legacy_vm_only and running_browser:"),
    "manual workflow must reuse an already-running standalone browser");
  console.log("Worker tab reuse + no duplicate Firefox window regressions: PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
