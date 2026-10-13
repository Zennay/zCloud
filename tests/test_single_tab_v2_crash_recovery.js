// Regression: after an actual Firefox process loss, restore every assigned
// worker in the same window, including paused workers. Never adopt experts,
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
      return {ok:true,json:async()=>({projects})};
    }
  });
  vm.runInContext(source,ctx,{timeout:1000});
  return ctx;
}
async function settle(){await new Promise(r=>setTimeout(r,25))}
(async()=>{
  let ctx=startup();
  await settle();
  assert.equal(tabs.length,4,"restore known sessionStorage owner; allocate exactly two missing tabs");
  assert.ok(tabs.some(t=>t.id===2),"restored supa tab reused with no duplicates");
  assert.ok(tabs.some(t=>t.id===1),"personal expert tab untouched");
  assert.equal(stored[key].length,3,"persist exact worker slot ledger");
  assert.equal(calls.filter(c=>c.action==="create").length,2);
  await ctx.tick();
  assert.equal(calls.filter(c=>c.action==="create").length,2,"polling creates no duplicates");

  // Firefox dies; sessionstore comes back with ONLY the expert. The ledger
  // survives as extension storage, and all worker tabs must be reconstructed.
  tabs.splice(0,tabs.length,{id:3,windowId:7,url:"https://chatgpt.com/c/expert-personal"});
  tags.clear();
  ctx=startup();
  await settle();
  assert.equal(tabs.length,1,"first snapshot waits for Firefox session restore");
  await ctx.tick();
  assert.equal(tabs.length,2,"second valid snapshot restores one trusted worker slot");
  await ctx.tick();
  assert.equal(tabs.length,4,"all three assigned worker tabs are now present");
  assert.equal(new Set(tabs.map(t=>t.id)).size,tabs.length);
  assert.equal(calls.filter(c=>c.action==="create").length,5);
  assert.equal(calls.filter(c=>c.action==="remove").length,0);
  assert.equal(tabs.filter(t=>t.url.includes("zcloud_worker=")).length,3);
  assert.ok(tabs.some(t=>t.id===3),"expert still present after full crash recovery");
  const before=tabs.length;
  networkOk=false;
  await ctx.tick();
  assert.equal(tabs.length,before,"offline API does not spawn or delete tabs");
  networkOk=true;
  await ctx.tick();
  assert.equal(tabs.length,before,"steady state maintains one tab per worker");
  assert.equal(ctx.ZCLOUD_V2_CONTROLLER.snapshot().tabs.length,3);
  console.log("Firefox crash recovery: ledger, restored tabs, paused slots, expert isolation PASS");
})().catch(err=>{console.error(err);process.exitCode=1});
