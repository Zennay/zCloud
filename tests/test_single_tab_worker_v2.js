// Executable end-to-end lifecycle contract for Firefox single-tab controller v2.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const root=path.join(__dirname,"..");
const source=fs.readFileSync(path.join(root,"firefox-extension/worker-controller-v2.js"),"utf8");
const engine=fs.readFileSync(path.join(root,"public/zcloud-worker-v2-engine.js"),"utf8");
const userscript=fs.readFileSync(path.join(root,"public/zcloud-worker.user.js"),"utf8");
const bg=fs.readFileSync(path.join(root,"firefox-extension/background.js"),"utf8");
const manifest=JSON.parse(fs.readFileSync(path.join(root,"firefox-extension/manifest.json"),"utf8"));
assert.equal(manifest.background.scripts.at(-1),"worker-controller-v2.js");
assert.ok(bg.startsWith("/* Legacy extension worker controller permanently retired by v2."));
assert.ok(bg.includes("if (!globalThis.ZCLOUD_SINGLE_TAB_CONTROLLER_V2) {"));
assert.ok(userscript.includes(engine.trim()),"v2 engine must be bundled in the installed userscript");
assert.ok(userscript.includes("if (window.__ZCLOUD_SINGLE_TAB_V2) return;"),"old VM timers must be inert");
assert.ok(engine.includes('POLL_MS=30000'),"worker generation checks run every 30 seconds");
assert.ok(engine.includes('MAX_AGE=3600000'),"conversation rotates once an hour");
assert.ok(engine.includes('[data-selected-reasoning-effort="high"]'),"provided DOM High attribute");
assert.ok(engine.includes('button[aria-label="Nieuwe chat"]'),"new chat uses in-tab UI control");
assert.ok(engine.includes('if(!(await ensureHigh()))'),"send must fail closed without confirmed High");
assert.ok(!/location\.(assign|replace)\s*\(|window\.open\s*\(/.test(engine),
 "DOM worker must not navigate to or create a new window");
assert.ok(!/browser\.windows\.(create|update|remove)\s*\(/.test(source),
 "extension must not create, activate or remove a Firefox window");
assert.ok(!/browser\.tabs\.update\s*\(/.test(source),
 "extension must never activate or navigate an existing tab");

const events=[],tabs=[],tags=new Map(),calls=[];
let nextId=20,snapshot={},v2VersionConfirmed=true;
const row=(id,slot)=>({project_id:id,active:true,assignment_ready:true,
 desired_state:"running",global_worker_count:4,global_worker_slot:slot,
 queue_item:{queue_id:"q"+slot},prompt:"Werk verder aan Supa. Kijk in Notion voor de taak."});
const noop=()=>{};
const browser={
 storage:{local:{get:async()=>({}),set:async()=>{}}},
 sessions:{
  getTabValue:async(id,key)=>tags.get(id+"|"+key)||null,
  setTabValue:async(id,key,value)=>{tags.set(id+"|"+key,value)}
 },
 tabs:{
  query:async()=>tabs.slice(),get:async(id)=>tabs.find(t=>t.id===id)||Promise.reject(Error("no tab")),
  create:async options=>{
   calls.push({type:"create",...options});
   assert.equal(options.active,false,"new worker tab may not grab focus");
   assert.equal(options.windowId,1,"new workers must share the existing Firefox window");
   const item={id:nextId++,url:options.url,windowId:options.windowId,status:"complete"};
   tabs.push(item);return item;
  },
  executeScript:async(_tab,opts)=>opts?.code?.includes("data-zcloud-violentmonkey-ready")?
    [v2VersionConfirmed?"2.0.0":"1.3.18"]:[true],
  remove:async id=>{calls.push({type:"remove",id});const index=tabs.findIndex(t=>t.id===id);if(index>=0)tabs.splice(index,1)},
  onRemoved:{addListener:noop},onUpdated:{addListener:noop}
 },
 windows:{getAll:async()=>[{id:1,type:"normal"},{id:2,type:"normal"}]},
 runtime:{id:"project-runner-v07@local",onMessage:{addListener:noop}}
};
// One pre-existing worker from v1 and a personal/expert ChatGPT tab.
tabs.push({id:1,windowId:1,url:"https://chatgpt.com/c/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"});
tabs.push({id:2,windowId:1,url:"https://chatgpt.com/c/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"});
tags.set("1|zcloud-worker-id","supa::w1"); // migrate historical session ownership
const ctx=vm.createContext({
 console:{log:(...xs)=>events.push(xs)},browser,Map,Set,Date,URL,Number,String,Object,
 setInterval:cb=>{ctx.timer=cb},fetch:async url=>{
   if(url.includes("config-audit"))return {ok:true,json:async()=>({items:[{
    config_key:"runner.worker_count",target:"supa",old_value:2,new_value:1,
    result:"succeeded",ts:new Date().toISOString()
   }]})};
   return {ok:true,json:async()=>({projects:snapshot})};
 }
});
snapshot={"supa::w1":row("supa::w1",1),"supa::w2":row("supa::w2",2)};
vm.runInContext(source,ctx,{timeout:1000});
const pump=async()=>{await ctx.timer();await new Promise(r=>setTimeout(r,1));};
(async()=>{
 await new Promise(r=>setTimeout(r,10)); // initial reconcile
 assert.equal(tabs.length,2,"first snapshot waits for Firefox sessionstore");
 await pump();
 assert.equal(tabs.length,3,"second snapshot opens the one missing worker; personal tab untouched");
 const mapping1=ctx.ZCLOUD_V2_CONTROLLER.snapshot();
 assert.equal(mapping1.tabs.length,2);
 assert.ok(mapping1.tabs.some(([key,id])=>key==="supa::w1"&&id===1));
 assert.equal(tabs.find(t=>t.id===2).url,"https://chatgpt.com/c/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb");
 await pump();await pump();
 assert.equal(tabs.length,3,"30-second refresh preserves every existing tab");
 // New allocation needs two confirmed snapshots; a v1 userscript must not authorize sending in Medium.
 v2VersionConfirmed=false;
 snapshot={...snapshot,"supa::w3":row("supa::w3",3)};
 await pump();
 assert.equal(calls.filter(x=>x.type==="create").length,1,
   "single snapshot never opens a worker or disturbs a legacy tab");
 v2VersionConfirmed=true;
 snapshot={"supa::w1":row("supa::w1",1),"supa::w2":row("supa::w2",2)};
 // A temporary data outage or missing worker allocation never permits remove.
 snapshot={"supa::w1":row("supa::w1",1)};
 await pump();await pump();
 assert.equal(calls.filter(x=>x.type==="remove").length,0,"no tab closes during 2 stale polls");
 await pump();
 assert.equal(calls.filter(x=>x.type==="remove").length,1,
  "verified explicit dashboard count reduction removes exactly one idle owned worker");
 assert.ok(tabs.some(t=>t.id===1),"worker 1 remains the original tab");
 assert.ok(tabs.some(t=>t.id===2),"expert tab remains untouched");
 const original=tabs.length;
 snapshot={};await pump();
 assert.equal(tabs.length,original,"arbitrary target loss never closes a tab");
 assert.equal(calls.filter(x=>x.type==="create").length,1,"only one new worker tab ever created");
 console.log("single-tab-worker-v2: preserve, create one, explicit remove one, expert isolation PASS");
})().catch(e=>{console.error(e);process.exitCode=1});
