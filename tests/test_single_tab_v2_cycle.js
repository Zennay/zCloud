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
function harness({age=0,failure=false,high=true}={}){
 const state=new Map();
 if(age)state.set("zcloud-v2-chat-at",String(Date.now()-age));
 const sent=[],statuses=[],chatClicks=[];
 let generating=false,assistantText="";
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
   if(q.includes('[data-message-author-role="assistant"]'))return assistantText?[{innerText:assistantText}]:[];
   return [];
  }
 };
 const url="https://chatgpt.com/?zcloud_worker="+encodeURIComponent(key);
 let check=null;
 const ctx=vm.createContext({
  window:{},location:{hostname:"chatgpt.com",href:url,pathname:"/"},
  sessionStorage:{getItem:k=>state.get(k)||null,setItem:(k,v)=>state.set(k,String(v))},
  document:doc,URL,Array,String,Number,Date,Map,Set,Promise,
  getComputedStyle:()=>({display:"block",visibility:"visible"}),
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
  setTimeout:(fn,ms)=>{queueMicrotask(fn);return 1}
 });
 vm.runInContext(code,ctx,{timeout:1000});
 return {tick:async()=>{check();await wait(20)},sent,statuses,chatClicks,
  setGenerating:v=>generating=v,setAssistant:v=>assistantText=v};
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
 const broken=harness({failure:true});await wait(40);
 assert.equal(broken.chatClicks.length,1,"failed conversation uses in-page New chat control");
 assert.equal(broken.sent.length,0,"recovery does not send while clicking New chat");
 const old=harness({age:3700000});await wait(40);
 assert.equal(old.chatClicks.length,1,"one hour of tab age triggers in-place new chat");
 const medium=harness({high:false});await wait(40);
 assert.equal(medium.sent.length,0,"never send without verified High");
 assert.ok(medium.statuses.some(x=>x.event==="thinking-high-unavailable"||x.reason==="high-not-verified"));
 console.log("single-tab v2: High, 30s generation, next prompt, broken chat, one-hour rotation PASS");
})().catch(e=>{console.error(e);process.exitCode=1});
