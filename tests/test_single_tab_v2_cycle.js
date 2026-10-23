// Execute real userscript v2 with a deterministic fake ChatGPT DOM/API.
// No browser windows, tab activations, or URL rewrites exist in this engine.
const assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const code=fs.readFileSync(path.join(__dirname,"../public/zcloud-worker-v2-engine.js"),"utf8");
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const key="supa::w1";
const target={
 project_id:key,base_project_id:"supa",worker_slot:1,active:true,
 global_worker_slot:1,global_worker_count:2,assignment_ready:true,
 queue_item:{queue_id:"qid"},desired_state:"running",
 prompt:"Werk verder aan Supa. Kijk in Notion in welke fase het project zit, werk verder."
};
function harness({age=0,failure=false,high=true,initialAssistant="",initialAssistantCount=0,pathname="/"}={}){
 let clock=Date.now();
 class Clock extends Date { static now(){return clock;} }
 const state=new Map();
 if(age)state.set("zcloud-v2-chat-at",String(Date.now()-age));
 const sent=[],statuses=[],chatClicks=[];
 const finishTimers=new Map();let timerId=0,mutationCallback=null;
 let generating=false,assistantText=initialAssistant,assistantMessages=initialAssistantCount;
 const element=(name)=>({tagName:"BUTTON",disabled:false,
  getAttribute:k=>k==="aria-label"?name:null,
  getBoundingClientRect:()=>({width:20,height:20}),
  click:()=>{},dispatchEvent:()=>{}});
 const send=element("Send message");send.click=()=>sent.push("sent");
 const newChat=element("Nieuwe chat");newChat.click=()=>chatClicks.push("new-chat-clicked");
 const highEl=element("High");highEl.getAttribute=k=>k==="data-selected-reasoning-effort"?"high":null;
 const composer=element("composer");composer.tagName="TEXTAREA";
 composer.value="";composer.focus=()=>{};composer.dispatchEvent=()=>{};
 const doc={
  title:"Supa chat",documentElement:{setAttribute:()=>{}},
  body:{innerText:failure?"Could not load conversation":""},
  querySelector:q=>{
   if(q==="main")return {innerText:failure?"Could not load conversation":""};
   if(q.includes("stop-button")||q.includes("Stop generating"))return generating?element("stop"):null;
   if(q.includes("#prompt-textarea"))return composer;
   if(q.includes('button[data-testid="send-button"]'))return send;
   if(q.startsWith('button[aria-label="Nieuwe chat"]'))return newChat;
   return null;
  },
  querySelectorAll:q=>{
   if(q.includes('[data-selected-reasoning-effort="high"]'))return high?[highEl]:[];
   if(q.includes('[data-message-author-role="assistant"]'))return Array.from({length:assistantMessages},()=>({innerText:assistantText}));
   return [];
  }
 };
 const url="https://chatgpt.com/?zcloud_worker="+encodeURIComponent(key);
 let check=null;
 const ctx=vm.createContext({
  window:{},location:{hostname:"chatgpt.com",href:url,pathname},
  sessionStorage:{getItem:k=>state.get(k)||null,setItem:(k,v)=>state.set(k,String(v))},
  document:doc,URL,Array,String,Number,Date:Clock,Map,Set,Promise,
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
  MutationObserver:class{constructor(cb){mutationCallback=cb}observe(){}},
  Event:class{constructor(type){this.type=type}},HTMLTextAreaElement:class{},
  HTMLInputElement:class{},
  GM_xmlhttpRequest:opt=>{
   let json={};
   if(opt.url.endsWith("/runner-targets"))json={projects:{[key]:target}};
   else if(opt.url.endsWith("/runner-commands"))json={commands:[]};
   else if(opt.url.endsWith("/runner-status"))statuses.push(JSON.parse(opt.data));
   queueMicrotask(()=>opt.onload({status:200,responseText:JSON.stringify(json)}));
  },
  setInterval:(fn,ms)=>{assert.equal(ms,30000);check=fn;return 1},
  setTimeout:(fn,ms)=>{const id=++timerId;if(ms===1800)finishTimers.set(id,fn);else queueMicrotask(fn);return id},
  clearTimeout:id=>finishTimers.delete(id)
 });
 vm.runInContext(code,ctx,{timeout:1000});
 return {tick:async()=>{check();await wait(20)},sent,statuses,chatClicks,
  dom:()=>mutationCallback?.(),
  settle:async()=>{clock+=1800;const calls=[...finishTimers.values()];finishTimers.clear();calls.forEach(cb=>cb());await wait(40)},
  setPath:p=>{ctx.location.pathname=p},advance:ms=>{clock+=ms},setGenerating:v=>generating=v,setAssistant:(v,n=1)=>{assistantText=v;assistantMessages=n;}};
}
(async()=>{
 const worker=harness();
 await wait(40);
 assert.equal(worker.sent.length,1,"initial dispatch sent in existing tab");
 worker.setGenerating(true);await worker.tick();
 assert.equal(worker.sent.length,1,"30-second monitor cannot send during generation");
 worker.setGenerating(false);worker.setAssistant("Result of the work");await worker.tick();
 assert.equal(worker.sent.length,2,"completed response sends next canonical prompt");
 assert.ok(worker.statuses.filter(x=>x.event==="prompt-sent").every(x=>x.thinkingEffort==="high"));
 // This must work in a background tab WITHOUT waiting for the 30s timer.
 const fast=harness();await wait(40);
 assert.equal(fast.sent.length,1);
 fast.setGenerating(true);fast.dom();
 fast.setGenerating(false);fast.setAssistant("Fresh result");fast.dom();
 assert.equal(fast.sent.length,1,"no dispatch before DOM settles");
 await fast.settle();
 assert.equal(fast.sent.length,2,"next prompt after stable DOM completion");
 const resumed=harness();await wait(40);
 resumed.setGenerating(true);resumed.dom();
 resumed.setGenerating(false);resumed.dom();
 resumed.setGenerating(true);resumed.dom();
 await resumed.settle();
 assert.equal(resumed.sent.length,1,"transient stop disappearance cannot dispatch");
 resumed.setGenerating(false);resumed.setAssistant("Completed stable response");resumed.dom();
 await resumed.settle();
 assert.equal(resumed.sent.length,2,"sends promptly after stable generation stop");
 const quick=harness();await wait(40);
 quick.setAssistant("Immediate assistant answer");quick.dom();
 await quick.settle();
 assert.equal(quick.sent.length,2,"very fast assistant completion detected");
 const identical=harness({initialAssistant:"Unchanged answer text",initialAssistantCount:1});await wait(40);
 assert.equal(identical.sent.length,1);
 identical.setAssistant("Unchanged answer text",1);await identical.tick();
 assert.equal(identical.sent.length,1,"unchanged older assistant node must not trigger a duplicate");
 identical.setAssistant("Unchanged answer text",2);await identical.tick();
 assert.equal(identical.sent.length,2,"a new assistant message completes generation even with unchanged previous reply text");
 const stalled=harness();await wait(40);
 stalled.advance(301000);await stalled.tick();
 assert.equal(stalled.chatClicks.length,1,"five minutes without generation recovers in same tab");
 assert.equal(stalled.sent.length,1,"no duplicate send during recovery");
 const protectedWorker=harness();await wait(40);
 protectedWorker.setGenerating(true);protectedWorker.advance(301000);await protectedWorker.tick();
 assert.equal(protectedWorker.chatClicks.length,0,"long active generation is protected");
 const broken=harness({failure:true});await wait(40);
 assert.equal(broken.chatClicks.length,1,"failed conversation uses in-page New chat control");
 assert.equal(broken.sent.length,0,"recovery does not send while clicking New chat");
 const old=harness({age:3700000});await wait(40);
 assert.equal(old.chatClicks.length,1,"one hour of tab age triggers in-place new chat");
 const transition=harness({age:3700000,pathname:"/c/old-chat"});await wait(40);
 await transition.tick();
 assert.equal(transition.sent.length,0,"do not dispatch into old chat before SPA navigation finishes");
 transition.setPath("/");await transition.tick();
 assert.equal(transition.sent.length,1,"resume only after new-chat navigation");
 const medium=harness({high:false});await wait(40);
 assert.equal(medium.sent.length,0,"never send without verified High");
 assert.ok(medium.statuses.some(x=>x.event==="thinking-high-unavailable"||x.reason==="high-not-verified"));
 console.log("single-tab v2: High, DOM-completion next prompt, 30s fallback, transient safety, broken chat PASS");
})().catch(e=>{console.error(e);process.exitCode=1});
