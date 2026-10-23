// Regression: sending a ChatGPT message must not navigate away from the
// worker's existing tab while ChatGPT assigns its new /c/... URL.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,"../public/zcloud-worker.user.js"),"utf8");
const refreshStart = source.indexOf("  async function refreshTarget() {");
const refreshEnd = source.indexOf("\n  function stopButton()",refreshStart);
const freshStart = source.indexOf("  async function requestFreshConversation(");
const freshEnd = source.indexOf("\n  function claimKey(",freshStart);
assert.ok(refreshStart>0 && refreshEnd>refreshStart);
assert.ok(freshStart>0 && freshEnd>freshStart);
const key="supa::w1", oldCid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const newCid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const reported=[], navigation=[];
const target={project_id:key, active:true, provider:"chatgpt",
  conversation_id:oldCid, queue_item:{queue_id:"q1"}, assignment_ready:true};
const page={
  href:"https://chatgpt.com/c/"+newCid,
  assign:url=>navigation.push(url)
};
const context=vm.createContext({
  JSON,Date,String,Object,Number,Array,Set,
  location:page, target, sending:false, awaitingGeneration:true,
  sawGeneration:false, draining:false, freshConversationPending:false,
  lastRoutePreservationReportAt:0,lastDeferredNewChatReportAt:0,
  gmRequest:async()=>({projects:{[key]:target}}),
  applyRuntimeSettings:()=>{},
  assignmentReady:()=>true,
  pendingProjectId:()=>"",
  boundProjectId:()=>key,
  rememberBoundProject:()=>{},
  claimCandidate:()=>true,
  candidateProvider:()=> "chatgpt",
  provider:()=> "chatgpt",
  isWorkerProvider:()=>true,
  conversationId:()=>newCid,
  status:async(event,data)=>reported.push({event,data}),
  generationActive:()=>false
});
vm.runInContext(source.slice(refreshStart,refreshEnd),context);
vm.runInContext(source.slice(freshStart,freshEnd),context);
(async()=>{
  await vm.runInContext("refreshTarget()",context);
  assert.deepEqual(navigation,[],"post-send route change must not navigate");
  assert.ok(reported.some(v=>v.event==="worker-tab-route-preserved"));
  context.awaitingGeneration=false;
  await vm.runInContext("refreshTarget()",context);
  assert.deepEqual(navigation,[],"even idle polling cannot steal the browser tab");
  await vm.runInContext('requestFreshConversation("database-new-chat",42)',context);
  await vm.runInContext('requestFreshConversation("conversation-load-failed",0)',context);
  assert.deepEqual(navigation,[],"automated recovery must never replace a chat");
  assert.ok(source.includes('button.click();'),"send path must still dispatch messages");
  assert.equal((source.match(/location\.assign\(/g)||[]).length,0,
    "userscript no longer allowed to auto-navigate any tab");
  console.log("Post-send browser tab preserved across route change and new_chat: PASS");
})().catch(err=>{console.error(err);process.exitCode=1});
