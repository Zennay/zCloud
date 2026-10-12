const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const text = fs.readFileSync(path.join(__dirname, "..", "firefox-extension", "background.js"), "utf8");
const start = text.indexOf("// All automatic recovery opens are rate-limited");
const end = text.indexOf("function portfolioAssignmentReady(", start);
assert.ok(start > 0 && end > start);
const events = [], requests = [], storage = new Map();
const memory = {
  getItem:key=>storage.get(key)||null,
  setItem:(key,value)=>storage.set(key,String(value))
};
function harness() {
  const browser={tabs:{create:async options=>{
    requests.push(options);
    return {id:requests.length,url:options.url};
  }}};
  const ctx=vm.createContext({localStorage:memory,browser,Set,Map,Date,Number,JSON,Object,
    Array,String,console,postStatus:data=>events.push(data)});
  vm.runInContext(text.slice(start,end),ctx);
  return {
    open(id){return vm.runInContext("createWorkerTab("+JSON.stringify({project_id:id})+',"https://chatgpt.com/","auto")',ctx)},
    cool(id){vm.runInContext("tabCloseCooldownUntil.set("+JSON.stringify(id)+",Date.now()+CLOSED_TAB_COOLDOWN_MS)",ctx)}
  };
}
(async()=>{
  const a=harness();
  assert.ok(await a.open("supa::w1"));
  assert.ok(await a.open("supa::w1"));
  assert.equal(await a.open("supa::w1"),null,"third open denied");
  assert.ok(await a.open("ftmo::w1"));
  assert.ok(requests.every(x=>x.active===false),"do not activate background tabs");
  const b=harness();
  assert.equal(await b.open("supa::w1"),null,"persistent open ledger");
  b.cool("ftmo::w1");
  assert.equal(await b.open("ftmo::w1"),null,"cooldown after close");
  assert.ok(await b.open("haxlab::w1"),"independent worker admitted");

  assert.equal((text.match(/browser\.tabs\.create\(/g)||[]).length,1,"centralize all creates");
  assert.ok(text.includes("browser.tabs.create({url, active:false})"));
  assert.ok(text.includes("if (refreshingTargets) return"));
  assert.ok(text.includes("finally { refreshingTargets = false; }"));
  assert.ok(text.includes("bindingRetryAt.set(target.project_id, Date.now() + BINDING_RETRY_MS)"));
  assert.ok(text.includes("worker-tab-closed-cooldown"));
  const focus=text.slice(text.indexOf('} else if (message?.type === "zcloud-focus-request")'),
                         text.indexOf('} else if (message?.type === "runner-replacement-handoff-consumed"'));
  assert.ok(!focus.includes("browser.tabs.update"));
  assert.ok(!focus.includes("browser.windows.update"));
  assert.ok(!text.includes("await browser.tabs.update(current, {active: true})"));
  assert.ok(text.includes("keep the existing bound worker tab"));
  console.log("Worker tab rate-limit + inactive tabs + recovery cooldown: PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
