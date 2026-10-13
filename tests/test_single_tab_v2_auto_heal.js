// Running worker tabs must self-heal without confusing background and missing tabs.
// Paused slots, live generation, unsent drafts and personal tabs are untouchable.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../firefox-extension/worker-controller-v2.js"), "utf8");
const userScript = fs.readFileSync(path.join(__dirname, "../public/zcloud-worker.user.js"), "utf8");
const engine = fs.readFileSync(path.join(__dirname, "../public/zcloud-worker-v2-engine.js"), "utf8");
assert.match(engine, /data-zcloud-v2-last-poll/);
assert.ok(userScript.includes(engine.trim()), "installed VM script must contain the same liveness monitor");
assert.ok(!source.includes("browser.tabs.update("), "never steal focus or navigate a worker tab");
assert.ok(!source.includes("browser.windows.create("), "never create a second Firefox window");

let clock=1_900_000_000_000, nextTab=20, network=true;
class FakeDate extends Date {static now(){return clock}}
const key="zcloud-v2-known-worker-ids", stored={[key]:[]};
const tags=new Map(),actions=[], handlers={};
const tabs=[
  {id:1,windowId:5,url:"https://chatgpt.com/c/personal",active:true},
  {id:2,windowId:5,url:"https://chatgpt.com/c/stalled",active:false},
  {id:3,windowId:5,url:"https://chatgpt.com/c/paused",active:false},
  {id:4,windowId:5,url:"https://chatgpt.com/c/healthy-background",active:false},
];
tags.set("2|zcloud-v2-worker-key","ftmo::w1");
tags.set("3|zcloud-v2-worker-key","supa::w1");
tags.set("4|zcloud-v2-worker-key","raiseai::w1");
const live=new Map([
  [2,{lastPoll:clock-600_000,generating:false,draft:false}],
  [3,{lastPoll:clock-600_000,generating:false,draft:false}],
  [4,{lastPoll:clock-5000,generating:false,draft:false}]
]);
const row=(project_id,slot,paused=false)=>({
  project_id,assignment_ready:true,active:!paused,desired_state:paused?"paused":"running",
  global_worker_count:8,global_worker_slot:slot,queue_item:{queue_id:"lease-"+project_id},
  prompt:"Werk verder aan Supa. Kijk in Notion in welke fase het project zit."
});
let projects={
  "ftmo::w1":row("ftmo::w1",1),
  "supa::w1":row("supa::w1",2,true),
  "raiseai::w1":row("raiseai::w1",3),
  "haxlab::w1":row("haxlab::w1",4)
};
const browser={
  storage:{local:{get:async()=>stored,set:async values=>Object.assign(stored,values)}},
  sessions:{
    getTabValue:async(id,k)=>tags.get(id+"|"+k)||null,
    setTabValue:async(id,k,value)=>tags.set(id+"|"+k,value)
  },
  windows:{getAll:async()=>[{id:5,type:"normal"}]},
  tabs:{
    query:async()=>tabs.slice(),
    get:async id=>tabs.find(t=>t.id===id)||Promise.reject(new Error("not found")),
    create:async opts=>{
      assert.equal(opts.active,false,"new worker tabs must be in the background");
      assert.equal(opts.windowId,5);
      const tab={id:nextTab++,windowId:5,url:opts.url,active:false};
      tabs.push(tab);actions.push({action:"create",id:tab.id,url:tab.url});
      return tab;
    },
    reload:async id=>{assert.ok(tabs.some(t=>t.id===id));actions.push({action:"reload",id})},
    remove:async id=>{actions.push({action:"remove",id});const i=tabs.findIndex(t=>t.id===id);if(i>=0)tabs.splice(i,1)},
    executeScript:async (id,opts)=>{
      if(opts.code.includes("sessionStorage.getItem"))return [""];
      if(opts.code.includes("data-zcloud-v2-last-poll")){
        const state=live.get(id)||{lastPoll:clock,generating:false,draft:false};
        const project=Object.entries({
          "ftmo::w1":2,"supa::w1":3,"raiseai::w1":4
        }).find(([,tabId])=>tabId===id)?.[0] || "";
        return [{version:"2.0.0",owner:project,lastPoll:state.lastPoll,
          generating:state.generating,draft:state.draft}];
      }
      return ["2.0.0"];
    },
    onRemoved:{addListener:cb=>{handlers.onRemoved=cb}},
    onUpdated:{addListener:cb=>{handlers.onUpdated=cb}}
  },
  runtime:{id:"project-runner-v07@local",onMessage:{addListener:()=>{}}}
};
const ctx=vm.createContext({
  browser,Date:FakeDate,URL,Map,Set,Number,String,Object,Promise,
  console:{log:()=>{}},setInterval:cb=>{ctx.tick=cb},
  fetch:async url=>{
    if(!network)throw Error("offline");
    if(url.includes("config-audit"))return {ok:true,json:async()=>({items:[]})};
    return {ok:true,json:async()=>({projects})};
  }
});
const settle=async()=>new Promise(r=>setTimeout(r,20));
(async()=>{
  vm.runInContext(source,ctx,{timeout:1000});
  await settle();
  assert.equal(actions.length,0,"one snapshot cannot mutate any tabs");
  await ctx.tick();
  assert.deepEqual(actions.map(a=>a.action).sort(),["create","reload"],
    "restore exactly one missing running tab; reload only the confirmed idle stale worker");
  assert.equal(actions.find(a=>a.action==="reload").id,2);
  assert.equal(tabs.find(t=>t.id===1).url,"https://chatgpt.com/c/personal");
  assert.ok(tabs.some(t=>t.id===3),"paused tab is preserved");
  assert.ok(tabs.some(t=>t.id===4),"healthy background tab is preserved");
  const created=actions.find(a=>a.action==="create");
  assert.ok(created.url.includes("haxlab%3A%3Aw1"),"replacement is bound to exact worker");

  // The next checks never duplicate or repeatedly reload.
  await ctx.tick();await ctx.tick();
  assert.equal(actions.filter(a=>a.action==="create").length,1);
  assert.equal(actions.filter(a=>a.action==="reload").length,1);
  live.get(2).lastPoll=clock; // stale tab starts polling again after reload
  await ctx.tick();
  assert.equal(actions.filter(a=>a.action==="reload").length,1);

  // A paused worker whose tab disappears must NEVER be auto-recreated.
  tabs.splice(tabs.findIndex(t=>t.id===3),1);
  handlers.onRemoved(3);
  await ctx.tick();await ctx.tick();await ctx.tick();
  assert.equal(actions.filter(a=>a.action==="create").length,1);
  assert.equal(tabs.filter(t=>t.url.includes("supa%3A%3Aw1")).length,0);

  // A generating worker, and then a worker with an unsent draft, must not reload.
  const state=live.get(2);
  clock+=660_000;state.lastPoll=clock-600_000;state.generating=true;
  await ctx.tick();await ctx.tick();
  assert.equal(actions.filter(a=>a.action==="reload").length,1);
  state.generating=false;state.draft=true;
  await ctx.tick();await ctx.tick();
  assert.equal(actions.filter(a=>a.action==="reload").length,1);

  // An explicitly resumed worker gets a replacement, no tab focus change.
  projects["supa::w1"]=row("supa::w1",2,false);
  await ctx.tick();await ctx.tick();
  const recreated=actions.filter(a=>a.action==="create");
  assert.equal(recreated.length,2,"resumed worker is allocated one new tab");
  assert.ok(recreated[1].url.includes("supa%3A%3Aw1"));
  network=false; tabs.splice(tabs.findIndex(t=>t.id===4),1); handlers.onRemoved(4);
  await ctx.tick(); await ctx.tick();
  assert.equal(actions.filter(a=>a.action==="create").length,2,"API outage does not create tabs");
  console.log("worker v2 auto-heal: missed slot, idle stall, paused, background, generating, drafts, offline PASS");
})().catch(err=>{console.error(err);process.exitCode=1});
