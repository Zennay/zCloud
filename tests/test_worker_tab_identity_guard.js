// Regression: an unmarked personal/expert ChatGPT tab may not become a
// zCloud worker just because a backend slot or matching conversation exists.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const script = fs.readFileSync(path.join(__dirname, "..", "public", "zcloud-worker.user.js"), "utf8");
const begin = script.indexOf("async function refreshTarget()");
const end = script.indexOf("function stopButton()", begin);
assert.ok(begin > 0 && end > begin, "userscript refreshTarget function available");
const snippet = script.slice(begin,end);
assert.ok(snippet.includes("const ownedProjectId = pending || target?.project_id || boundProjectId()"),
  "worker identity must be pinned to this tab, not a free project");
assert.ok(snippet.includes("item?.project_id === ownedProjectId && claimCandidate(item)"),
  "only the original worker may be reclaimed");
assert.ok(script.includes('sessionStorage.setItem("zcloud-bound-project"'),
  "worker identity must survive URL marker removal");
const worker = {project_id:"lightup::w1", conversation_id:"a1a1a1a1-bbbb-cccc-dddd-eeeeeeeeeeee",
  active:true, assignment_ready:true};
let claims=0, navigations=0, stopped=0;
const ctx=vm.createContext({
  target:null, draining:false, Object, String, Error,
  gmRequest:async()=>({projects:{"lightup::w1":worker}}),
  applyRuntimeSettings:()=>{},
  assignmentReady:()=>true,
  pendingProjectId:()=>"",
  boundProjectId:()=>"",
  provider:()=> "chatgpt",
  isWorkerProvider:()=>true,
  conversationId:()=>worker.conversation_id,
  claimCandidate:()=>{claims++;return true;},
  candidateProvider:()=> "chatgpt",
  releaseClaim:()=>{stopped++;},
  location:{href:"https://chatgpt.com/c/"+worker.conversation_id,
    assign:()=>{navigations++;}},
  status:()=>{throw new Error("Unmarked tab must not become worker");}
});
vm.runInContext(snippet,ctx);
(async()=>{
  await vm.runInContext("refreshTarget()",ctx);
  assert.equal(claims,0,"unmarked expert tab may never claim a worker");
  assert.equal(navigations,0,"unmarked expert tab must not navigate away");
  assert.equal(stopped,1,"unowned tab should be left alone");
  assert.equal(ctx.target,null);
  console.log("Unmarked expert tab isolation: PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
