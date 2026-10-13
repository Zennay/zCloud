// Real VM regression for fail-closed v2 dispatch + terminal chat error recovery.
// Run: node tests/test_v2_dispatch_recovery_20261013_w3.js
const assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const root=path.join(__dirname,"..");
const engine=fs.readFileSync(path.join(root,"public/zcloud-worker-v2-engine.js"),"utf8");
const bundle=fs.readFileSync(path.join(root,"public/zcloud-worker.user.js"),"utf8");
const start=bundle.indexOf("// zCloud v2 browser DOM engine.");
const end=bundle.indexOf("(() => {\n  if (window.__ZCLOUD_SINGLE_TAB_V2) return;",start);
assert.ok(start>=0&&end>start);
assert.equal(bundle.slice(start,end).trim(),engine.trim(),"the installed userscript must match its v2 engine");

const wait=ms=>new Promise(r=>setTimeout(r,ms));
const key="supa::w1";
const base={project_id:key,base_project_id:"supa",worker_slot:1,
  active:true,assignment_ready:true,desired_state:"running",
  global_worker_slot:1,global_worker_count:8,queue_item:{queue_id:"q-live"},
  prompt:"Werk verder aan Supa. Kijk in Notion in welke fase het project zit."};
function setup(target) {
  let broken=false,generating=false,check,clicked=0,resets=0;
  const statuses=[],stored=new Map();
  const el=name=>({tagName:"BUTTON",disabled:false,
    getAttribute:k=>k==="aria-label"?name:null,
    getBoundingClientRect:()=>({width:20,height:20}),click:()=>{},
    dispatchEvent:()=>{}});
  const send=el("Send message");send.click=()=>{clicked++};
  const chat=el("Nieuwe chat");chat.click=()=>{resets++};
  const high=el("High");
  const box=el("composer");box.tagName="TEXTAREA";box.value="";box.focus=()=>{};
  const document={
    title:"Supa",documentElement:{setAttribute:()=>{}},
    body:{innerText:""},
    querySelector:q=>{
      if(q==="main")return {innerText:broken?"Could not load conversation":""};
      if(q.includes("stop-button"))return generating?el("stop"):null;
      if(q.includes("#prompt-textarea"))return box;
      if(q.includes('button[data-testid="send-button"]'))return send;
      if(q.startsWith('button[aria-label="Nieuwe chat"]'))return chat;
      return null;
    },
    querySelectorAll:q=>{
      if(q.includes('[data-selected-reasoning-effort="high"]'))return [high];
      if(q.includes('[data-message-author-role="assistant"]'))return [];
      return [];
    }
  };
  const ctx=vm.createContext({
    window:{},location:{hostname:"chatgpt.com",href:"https://chatgpt.com/?zcloud_worker=supa%3A%3Aw1",pathname:"/"},
    document,URL,Date,Map,Set,String,Number,Array,Promise,
    sessionStorage:{getItem:k=>stored.get(k)||null,setItem:(k,v)=>stored.set(k,String(v))},
    getComputedStyle:()=>({display:"block",visibility:"visible"}),
    Event:class{constructor(type){this.type=type}},
    HTMLTextAreaElement:class{},HTMLInputElement:class{},
    GM_xmlhttpRequest:opt=>{
      let value={};
      if(opt.url.endsWith("/runner-targets"))value={projects:{[key]:target}};
      if(opt.url.endsWith("/runner-commands"))value={commands:[]};
      if(opt.url.endsWith("/runner-status"))statuses.push(JSON.parse(opt.data));
      queueMicrotask(()=>opt.onload({status:200,responseText:JSON.stringify(value)}));
    },
    setInterval:(fn,ms)=>{assert.equal(ms,30000);check=fn;return 1},
    setTimeout:(fn)=>{queueMicrotask(fn);return 1}
  });
  vm.runInContext(engine,ctx,{timeout:1000});
  return {tick:async()=>{check();await wait(25)},broken:v=>{broken=v},
    counts:()=>({clicked,resets}),statuses};
}
(async()=>{
  const worker=setup({...base});await wait(35);
  assert.equal(worker.counts().clicked,1,"initial prompt must be dispatched");
  worker.broken(true);
  await worker.tick();
  assert.equal(worker.counts().resets,1,"terminal error must escape awaiting state and reset same tab");
  assert.equal(worker.counts().clicked,1,"never duplicate the old prompt during error recovery");
  assert.ok(worker.statuses.some(s=>s.event==="generation-aborted"));
  worker.broken(false);
  await worker.tick();
  assert.equal(worker.counts().clicked,2,"resume canonical prompt after recovered in-page new chat");

  const missing=setup({...base,assignment_ready:false,queue_item:null});await wait(35);
  assert.equal(missing.counts().clicked,0,"no dispatch without a confirmed queue lease");
  assert.ok(missing.statuses.some(s=>s.event==="send-blocked"&&s.reason==="queue-assignment-not-ready"),
    "missing lease must be visible rather than silently ignored");
  const paused=setup({...base,active:false,desired_state:"paused"});await wait(35);
  assert.equal(paused.counts().clicked,0);
  assert.ok(!paused.statuses.some(s=>s.event==="send-blocked"),
    "an intentionally paused worker must not be reported as broken");
  console.log("v2 terminal error + readiness regression PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
