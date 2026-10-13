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
assert.ok(source.includes("await browser.tabs.update(reservedId, {url:String(url), active:false})"),
 "only idle, marked saved-pool capacity may be navigated into its worker assignment");
assert.ok(!/browser\.tabs\.update\([^\n]*active:\s*true/.test(source),
 "extension must never activate any existing tab");

const events=[],tabs=[],tags=new Map(),calls=[],busyTabs=new Set();
let nextId=20,snapshot={},v2VersionConfirmed=true,poolSize=4,auditMode="project",pendingCommands=[];
const row=(id,slot)=>({project_id:id,active:true,assignment_ready:true,
 desired_state:"running",global_worker_count:4,global_worker_slot:slot,
 queue_item:{queue_id:"q"+slot},prompt:"Werk verder aan Supa. Kijk in Notion voor de taak."});
const noop=()=>{};
const browser={
 storage:{local:{get:async()=>({}),set:async()=>{}}},
 sessions:{
  getTabValue:async(id,key)=>tags.get(id+"|"+key)||null,
  setTabValue:async(id,key,value)=>{tags.set(id+"|"+key,value)},
  removeTabValue:async(id,key)=>{tags.delete(id+"|"+key)}
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
  executeScript:async(tabId,opts)=>{
    if(opts?.code?.includes('dispatchEvent(new Event("zcloud-v2-wake"))')){
      calls.push({type:"wake",id:tabId});return [true];
    }
    if(opts?.code?.includes("data-zcloud-violentmonkey-ready"))return [{
      version:v2VersionConfirmed?"2.0.1":"1.3.18",
      owner:tags.get(tabId+"|zcloud-v2-worker-key")||tags.get(tabId+"|zcloud-worker-id")||"",
      lastPoll:Date.now(),poll:Date.now(),busy:busyTabs.has(tabId)?"true":"false",generating:busyTabs.has(tabId),draft:false
    }];
    return [true];
  },
  update:async(id,opts)=>{
    assert.equal(opts.active,false,"rebound worker must stay in background");
    const tab=tabs.find(t=>t.id===id);
    assert.ok(tab,"rebind only an existing tab");
    tab.url=opts.url;calls.push({type:"update",id,url:opts.url});return tab;
  },
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
 setInterval:(cb,ms)=>{if(ms===30000)ctx.timer=cb;else if(ms===5000)ctx.saveProbe=cb;else if(ms===2500)ctx.commandProbe=cb},fetch:async url=>{
   if(url.includes("config-audit")){
     if(url.includes("runner.dynamic_worker_settings"))return {ok:true,json:async()=>({items:
       auditMode==="global"||auditMode==="expand"?
       [{config_key:"runner.dynamic_worker_settings",target:"portfolio",
         old_value:{chatgpt_count:auditMode==="expand"?2:3},
         new_value:{chatgpt_count:auditMode==="expand"?3:2},
         result:"succeeded",ts:new Date().toISOString()}]:[]})};
     return {ok:true,json:async()=>({items:auditMode==="project"?[{
       config_key:"runner.worker_count",target:"supa",old_value:2,new_value:1,
       result:"succeeded",ts:new Date().toISOString()
     }]:[]})};
   }
   if(url.includes("/runner-commands"))return {ok:true,json:async()=>({commands:pendingCommands})};
   return {ok:true,json:async()=>({projects:snapshot,dynamic_workers:{chatgpt_count:poolSize}})};
 }
});
snapshot={"supa::w1":row("supa::w1",1),"supa::w2":row("supa::w2",2)};
vm.runInContext(source,ctx,{timeout:1000});
const ownershipSnapshot=()=>ctx.ZCLOUD_V2_CONTROLLER.snapshot();
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
 // Opslaan of the global ChatGPT worker pool is NOT a per-project worker-count
 // audit. A 3 -> 2 reduction must close only its now-unallocated, idle w3 tab.
 snapshot={"supa::w1":row("supa::w1",1),"supa::w3":row("supa::w3",3)};
 poolSize=3;
 await pump();await pump();
 assert.equal(calls.filter(x=>x.type==="create").length,2,"pool expansion opens the new allocated slot");
 auditMode="global";poolSize=2;snapshot={"supa::w1":row("supa::w1",1)};
 await pump();await pump();
 assert.equal(calls.filter(x=>x.type==="remove").length,1,"do not close pool slot before 3 stable observations");
 await pump();
 assert.equal(calls.filter(x=>x.type==="remove").length,2,"global pool Save 3 to 2 removes exactly the vacated slot");
 assert.ok(tabs.some(t=>t.id===1),"existing w1 tab preserved across pool reduction");
 assert.ok(tabs.some(t=>t.id===2),"manual expert tab preserved across pool reduction");
 // The Save button expands capacity from 2 to 3, even when the queue allocator
 // has not assigned the third slot to any project. Exactly one empty, tagged
 // capacity tab must open; personal tabs are never reused.
 auditMode="project";poolSize=2;
 snapshot={"supa::w1":row("supa::w1",1),"ftmo::w1":row("ftmo::w1",2)};
 await pump();await pump();
 assert.equal(ownershipSnapshot().tabs.length,2,"two named workers own the initial pool");
 auditMode="expand";poolSize=3;
 await ctx.saveProbe();await pump();
 const capacity=tabs.filter(t=>t.url.includes("zcloud_pool=3"));
 assert.equal(capacity.length,1,"Save 2 to 3 opens exactly one background capacity tab");
 assert.equal(tags.get(capacity[0].id+"|zcloud-v2-pool-reserve"),"3");
 assert.ok(tabs.some(t=>t.id===2),"Save never adopts personal expert tab");
 // Forceer a different project: the backend replaces provider slot 2.
 // The old idle worker tab is retired; a NEW owned tab is created for the
 // freshly assigned project. No personal/expert tab can be touched.
 const previous=ownershipSnapshot().tabs.find(([k])=>k==="ftmo::w1")[1];
 const beforeSwitchCreates=calls.filter(x=>x.type==="create").length;
 snapshot={
   "supa::w1":row("supa::w1",1),
   "ftmo::w1":{...row("ftmo::w1",2),active:false,assignment_ready:false,
     global_worker_slot:null,queue_item:null,provider:"chatgpt"},
   "lightup::w1":{...row("lightup::w1",2),provider:"chatgpt"}
 };
 await pump();await pump();
 const rebound=ownershipSnapshot().tabs.find(([k])=>k==="lightup::w1")?.[1];
 assert.ok(rebound&&rebound!==previous,
   "project switch creates a distinct freshly tagged worker tab");
 assert.equal(tags.get(rebound+"|zcloud-v2-worker-key"),"lightup::w1",
   "new worker tab has exactly its assigned identity");
 assert.equal(calls.filter(x=>x.type==="create").length,beforeSwitchCreates+1,
   "one new tab for the new project allocation");
 assert.equal(calls.filter(x=>x.type==="update").length,0,
   "project switch never redirects or steals a prior conversation");
 assert.ok(!ownershipSnapshot().tabs.some(([k])=>k==="ftmo::w1"),
   "retired project releases previous owner");
 pendingCommands=[{id:101,project_id:"lightup",action:"start",force:false}];
 await ctx.commandProbe();
 assert.deepEqual(calls.filter(c=>c.type==="wake").map(c=>c.id),[rebound],
   "Start wakes the correct fresh worker tab");
 pendingCommands=[];await ctx.commandProbe();
 // Explicit Force must replace an idle worker tab even for the SAME worker ID.
 // The new tab is ticketed; old tab never receives a second Force prompt.
 const createsBeforeForce=calls.filter(x=>x.type==="create").length;
 pendingCommands=[{id:102,project_id:"lightup",action:"force_start",force:true}];
 await ctx.commandProbe();
 const forced=tabs.find(t=>t.url.includes("zcloud_force_command=102"))?.id;
 assert.ok(forced&&forced!==rebound,"Force replaces same-ID worker with a new tab");
 assert.equal(calls.filter(x=>x.type==="create").length,createsBeforeForce+1);
 assert.ok(!tabs.some(t=>t.id===rebound),"verified idle old worker tab is retired");
 assert.ok(tabs.find(t=>t.id===forced)?.url.includes("zcloud_force_command=102"),
   "new tab carries the exact explicit Force ticket");
 assert.equal(tags.get(forced+"|zcloud-v2-worker-key"),"lightup::w1");
 const createsAfterForce=calls.filter(x=>x.type==="create").length;
 await ctx.commandProbe();
 assert.equal(calls.filter(x=>x.type==="create").length,createsAfterForce,
   "same pending Force command never creates another tab");
 pendingCommands=[];
 await ctx.commandProbe();
 // Busy worker may NOT be interrupted; retry after it is independently idle.
 busyTabs.add(forced);
 pendingCommands=[{id:103,project_id:"lightup",action:"force_start",force:true}];
 await ctx.commandProbe();
 assert.equal(calls.filter(x=>x.type==="create").length,createsAfterForce,
   "Force never interrupts a generating worker");
 assert.ok(tabs.some(t=>t.id===forced),"busy generation is preserved");
 busyTabs.delete(forced);
 await ctx.commandProbe();
 assert.equal(calls.filter(x=>x.type==="create").length,createsAfterForce+1,
   "once idle the pending Force creates its new dedicated tab");
 assert.ok(tabs.some(t=>t.id===2),"expert tab survives every Force");
 assert.ok(engine.includes('"force_start"'),"legacy versions must never consume Force");
 assert.ok(engine.includes('String(command.id)!==forceTicket'),
   "old userscript must refuse a Force ticket belonging to the replacement");
 assert.ok(engine.includes('data-zcloud-v2-busy'),
   "busy DOM handshake blocks unsafe forced replacement");
 console.log("single-tab-worker-v2: Save, new Force tab, exact owner/ticket, dedupe, busy safety PASS");
})().catch(e=>{console.error(e);process.exitCode=1});
