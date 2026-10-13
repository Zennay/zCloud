// Regression: after an actual Firefox process loss, restore every assigned
// worker in the same window, excluding paused or inactive worker replacements. Never adopt experts,
// rotate tabs or create extra windows; recover old sessionStorage identities.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../firefox-extension/worker-controller-v2.js"), "utf8");
const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "../firefox-extension/manifest.json"), "utf8"));
assert.ok(manifest.permissions.includes("storage"), "worker slot ledger needs storage permission");
const key="zcloud-v2-known-worker-ids";
const stored={[key]:["supa::w1","ftmo::w1","haxlab::w1"]};
const allocated=(id,slot,paused=false)=>({
  project_id:id,assignment_ready:true,active:!paused,
  desired_state:paused?"paused":"running",
  global_worker_count:8,global_worker_slot:slot,
  queue_item:{queue_id:"claim-"+id},prompt:"Werk verder aan Supa. Kijk in Notion"
});
let projects={
  "supa::w1":allocated("supa::w1",1),
  "ftmo::w1":allocated("ftmo::w1",2),
  "haxlab::w1":allocated("haxlab::w1",3,true),
};
let networkOk=true, nextTab=40;
const calls=[],tags=new Map();
const tabs=[
  {id:1,windowId:7,url:"https://chatgpt.com/c/expert-personal"},
  {id:2,windowId:7,url:"https://chatgpt.com/c/restored-supa"},
];
const handlers={};
function startup(){
  const browser={
    storage:{local:{
      get:async k=>({[k]:stored[k]}),
      set:async values=>Object.assign(stored,values)
    }},
    sessions:{
      getTabValue:async(id,k)=>tags.get(id+"|"+k)||null,
      setTabValue:async(id,k,value)=>{tags.set(id+"|"+k,value)}
    },
    tabs:{
      query:async()=>tabs.slice(),
      get:async id=>tabs.find(t=>t.id===id)||Promise.reject(Error("missing")),
      create:async opts=>{
        assert.equal(opts.windowId,7,"recover workers inside the original window");
        assert.equal(opts.active,false,"never steal browser focus");
        const tab={id:nextTab++,windowId:7,url:opts.url};
        tabs.push(tab);calls.push({action:"create",id:tab.id,url:tab.url});
        return tab;
      },
      remove:async id=>{calls.push({action:"remove",id});const idx=tabs.findIndex(t=>t.id===id);if(idx>=0)tabs.splice(idx,1)},
      executeScript:async(id,{code})=>{
        if(code.includes("sessionStorage.getItem"))return [id===2?"supa::w1":""];
        if(code.includes("data-zcloud-violentmonkey-ready"))return ["2.0.0"];
        return [true];
      },
      onRemoved:{addListener:cb=>{handlers.removed=cb}},
      onUpdated:{addListener:cb=>{handlers.updated=cb}}
    },
    windows:{getAll:async()=>[{id:7,type:"normal"}]},
    runtime:{id:"project-runner-v07@local",onMessage:{addListener:()=>{}}}
  };
  const ctx=vm.createContext({
    browser,console:{log:()=>{}},URL,Date,Promise,Map,Set,String,Object,Number,
    setInterval:fn=>{ctx.tick=fn;return 1},
    fetch:async url=>{
      if(!networkOk)throw Error("API down");
      if(url.includes("config-audit"))return {ok:true,json:async()=>({items:[]})};
      return {ok:true,json:async()=>({projects,dynamic_workers:{chatgpt_count:3}})};
    }
  });
  vm.runInContext(source,ctx,{timeout:1000});
  return ctx;
}
async function settle(){await new Promise(r=>setTimeout(r,25))}
(async()=>{
  let ctx=startup();
  await settle();
  assert.equal(tabs.length,2,"first snapshot reclaims restored owner without creating tabs");
  await ctx.tick();
  assert.equal(tabs.length,3,"second snapshot creates missing active worker, not paused worker");
  assert.ok(tabs.some(t=>t.id===2),"restored supa tab reused with no duplicates");
  assert.ok(tabs.some(t=>t.id===1),"personal expert tab untouched");
  assert.equal(stored[key].length,3,"persist exact worker slot ledger");
  assert.equal(calls.filter(c=>c.action==="create").length,1);
  assert.equal(tabs.filter(t=>t.url.includes("haxlab::w1")).length,0,"paused tabs are never regenerated");
  await ctx.tick();
  assert.equal(calls.filter(c=>c.action==="create").length,1,"polling creates no duplicates");

  // Firefox dies; sessionstore comes back with ONLY the expert. The ledger
  // survives as extension storage, and all worker tabs must be reconstructed.
  tabs.splice(0,tabs.length,{id:3,windowId:7,url:"https://chatgpt.com/c/expert-personal"});
  tags.clear();
  ctx=startup();
  await settle();
  assert.equal(tabs.length,1,"first snapshot waits for Firefox session restore");
  await ctx.tick();
  assert.equal(tabs.length,3,"second valid snapshot restores all running worker slots");
  await ctx.tick();
  assert.equal(tabs.length,3,"paused worker remains uncreated after additional polls");
  assert.equal(new Set(tabs.map(t=>t.id)).size,tabs.length);
  assert.equal(calls.filter(c=>c.action==="create").length,3);
  assert.equal(calls.filter(c=>c.action==="remove").length,0);
  assert.equal(tabs.filter(t=>t.url.includes("zcloud_worker=")).length,2);
  assert.ok(tabs.some(t=>t.id===3),"expert still present after full crash recovery");
  const before=tabs.length;
  networkOk=false;
  await ctx.tick();
  assert.equal(tabs.length,before,"offline API does not spawn or delete tabs");
  networkOk=true;
  await ctx.tick();
  assert.equal(tabs.length,before,"steady state maintains one tab per worker");
  assert.equal(ctx.ZCLOUD_V2_CONTROLLER.snapshot().tabs.length,2);
  // The scheduler can release all queue claims during Firefox recovery.
  // A previously known running worker still gets a tab; an unrecognized
  // project, or a paused worker, must NEVER be constructed.
  projects={
    "ftmo::w1":{...allocated("ftmo::w1",1),active:false,
      assignment_ready:false,worker_slot:1,worker_count:1,
      global_worker_slot:null,queue_item:null,provider:"chatgpt"},
    "haxlab::w1":{...allocated("haxlab::w1",2,true),
      assignment_ready:false,worker_slot:1,worker_count:1,
      global_worker_slot:null,queue_item:null,provider:"chatgpt"},
    "unseen::w1":{...allocated("unseen::w1",3),active:false,
      assignment_ready:false,worker_slot:1,worker_count:1,
      global_worker_slot:null,queue_item:null,provider:"chatgpt"}
  };
  tabs.splice(0,tabs.length,{id:5,windowId:7,url:"https://chatgpt.com/c/expert-personal"});
  tags.clear();
  ctx=startup();
  await settle();
  assert.equal(tabs.length,1,"unclaimed workers wait for session restore");
  await ctx.tick();
  assert.equal(tabs.length,2,"known running worker without queue lease regains its tab");
  assert.equal(tabs.filter(t=>t.url.includes("ftmo%3A%3Aw1")).length,1);
  assert.equal(tabs.filter(t=>t.url.includes("haxlab%3A%3Aw1")).length,0,
    "paused worker never regains a missing tab");
  assert.equal(tabs.filter(t=>t.url.includes("unseen%3A%3Aw1")).length,0,
    "unrecognized worker IDs never gain an invented tab");
  await ctx.tick();
  assert.equal(tabs.length,2,"same worker never opens twice after recovery");
  console.log("Firefox crash recovery: active slots reconstructed, paused slots ignored, expert isolation PASS");
})().catch(err=>{console.error(err);process.exitCode=1});
