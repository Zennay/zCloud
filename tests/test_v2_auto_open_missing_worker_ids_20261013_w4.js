// Regression: missing ChatGPT worker identities get a background tab even if
// the scheduler's queue lease has not arrived. No personal tab is adopted.
const assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../firefox-extension/worker-controller-v2.js"),"utf8");
const keys=["ftmo::w1","supa::w1","haxlab::w1","lightup::w1","raiseai::w1","zssh::w1"];
const targets=Object.fromEntries(keys.map((key,i)=>[key,{
  project_id:key,base_project_id:key.split("::")[0],provider:"chatgpt",
  global_worker_slot:i+1,global_worker_count:6,
  worker_slot:1,worker_count:1,active:true,desired_state:"running",
  assignment_ready:false,queue_item:null,
  prompt:"Werk verder aan Supa. Kijk in Notion in welke fase het project zit."
}]));
targets["paused::w1"]={...targets[keys[0]],project_id:"paused::w1",
  active:false,desired_state:"paused",global_worker_slot:1,
  assignment_ready:true,queue_item:{queue_id:"q-paused"}};
targets["claude::w1"]={...targets[keys[0]],project_id:"claude::w1",
  provider:"claude",global_worker_slot:1,
  assignment_ready:true,queue_item:{queue_id:"q-claude"}};
targets["unallocated::w1"]={...targets[keys[0]],project_id:"unallocated::w1",
  active:false,global_worker_slot:null};

const manualTabs=Array.from({length:6},(_,i)=>({
  id:i+1,windowId:7,url:"https://chatgpt.com/c/manual-"+(i+1)
}));
const tabs=[...manualTabs],tags=new Map(),storage={},actions=[],quickTimers=[];
let nextId=10;
const browser={
 storage:{local:{get:async key=>({[key]:storage[key]||[]}),
   set:async v=>Object.assign(storage,v)}},
 sessions:{
   getTabValue:async(id,key)=>tags.get(id+"|"+key)||null,
   setTabValue:async(id,key,val)=>{tags.set(id+"|"+key,val)}
 },
 tabs:{
   query:async()=>tabs.slice(),
   get:async id=>tabs.find(t=>t.id===id)||Promise.reject(Error("missing tab")),
   executeScript:async()=>[""], // unmarked expert tabs cannot be adopted
   create:async opts=>{
     assert.equal(opts.active,false,"must not steal focus");
     assert.equal(opts.windowId,7,"existing Firefox window only");
     const tab={id:nextId++,windowId:7,url:opts.url,status:"complete"};
     tabs.push(tab);actions.push({kind:"create",tab});return tab;
   },
   remove:async id=>{actions.push({kind:"remove",id})},
   reload:async id=>{actions.push({kind:"reload",id})},
   onRemoved:{addListener:()=>{}},onUpdated:{addListener:()=>{}}
 },
 windows:{getAll:async()=>[{id:7,type:"normal"}]},
 runtime:{id:"zcloud-test-extension",onMessage:{addListener:()=>{}}}
};
const ctx=vm.createContext({
 browser,Map,Set,URL,Number,Object,String,Date,Promise,
 console:{log:()=>{}},
 setInterval:(cb,ms)=>{if(ms===30000)ctx.tick=cb;else if(ms===5000)ctx.saveProbe=cb;else if(ms===2500)ctx.commandProbe=cb},
 setTimeout:(cb,ms)=>{
   assert.equal(ms,2000,"missing tabs must not wait for 30s polling");
   quickTimers.push(cb);return quickTimers.length;
 },
 fetch:async url=>{
   if(url.includes("config-audit"))return {ok:true,json:async()=>({items:[]})};
   assert.ok(url.endsWith("/runner-targets"));
   return {ok:true,json:async()=>({
     projects:targets,dynamic_workers:{chatgpt_count:6},
     global_allocation:{workers:keys.map((worker_key,i)=>({worker_key,global_worker_slot:i+1}))}
   })};
 }
});
const sleep=()=>new Promise(r=>setTimeout(r,25));
(async()=>{
 vm.runInContext(source,ctx,{timeout:1000});
 await sleep();
 assert.equal(actions.length,0,"allow one snapshot for Firefox session restore");
 assert.equal(quickTimers.length,1,"missing worker IDs trigger prompt follow-up reconciliation");
 await quickTimers.shift()();await sleep();
 assert.equal(actions.filter(x=>x.kind==="create").length,6,
   "all six allocated ChatGPT worker IDs open even without queue claims");
 for(const key of keys){
   const matched=tabs.filter(t=>new URL(t.url).searchParams.get("zcloud_worker")===key);
   assert.equal(matched.length,1,"exactly one tab must be created for "+key);
   assert.equal(tags.get(matched[0].id+"|zcloud-v2-worker-key"),key);
 }
 for(const tab of manualTabs){
   assert.ok(tabs.includes(tab),"personal existing tab must be preserved");
   assert.ok(![...tags.keys()].some(k=>k.startsWith(tab.id+"|")),
     "personal tab never receives a worker identity");
 }
 assert.equal(actions.filter(x=>x.kind==="remove").length,0);
 assert.equal(actions.filter(x=>x.kind==="reload").length,0);
 assert.equal(tabs.length,12,"six manual personal tabs plus six owned worker tabs");
 await ctx.tick();await sleep();
 assert.equal(actions.filter(x=>x.kind==="create").length,6,"no duplicates during steady state");
 assert.equal(actions.filter(x=>x.kind==="remove").length,0);
 assert.ok(!tabs.some(t=>new URL(t.url).searchParams.get("zcloud_worker")==="paused::w1"));
 assert.ok(!tabs.some(t=>new URL(t.url).searchParams.get("zcloud_worker")==="claude::w1"));
 assert.ok(!tabs.some(t=>new URL(t.url).searchParams.get("zcloud_worker")==="unallocated::w1"));
 console.log("v2 auto-open missing worker identities: 6/6, quick retry, no duplicates, paused/Claude/expert isolation PASS");
})().catch(err=>{console.error(err);process.exitCode=1});
