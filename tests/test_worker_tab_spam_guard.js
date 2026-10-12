// Critical guard: no automated extension code can spawn a new ChatGPT tab.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,"../firefox-extension/background.js"),"utf8");
const start = source.indexOf("async function createWorkerTab(");
const end = source.indexOf("\n\nfunction portfolioAssignmentReady(",start);
assert.ok(start>0 && end>start);
assert.equal((source.match(/browser\.tabs\.create\s*\(/g)||[]).length,0,
  "no automatic tabs may be opened anywhere in extension");
assert.equal((source.match(/browser\.tabs\.remove\s*\(/g)||[]).length,0,
  "no automatic tabs may be closed anywhere in extension");
const events=[];
const context=vm.createContext({String,Date,postStatus:x=>events.push(x)});
vm.runInContext(source.slice(start,end),context);
(async()=>{
  const tab=await vm.runInContext('createWorkerTab({project_id:"supa::w1"},"https://chatgpt.com/","after-send")',context);
  assert.equal(tab,null,"auto recovery must never open a new tab");
  assert.equal(events[0].event,"worker-tab-create-suppressed");
  assert.ok(source.includes("if (refreshingTargets) return"));
  assert.ok(source.includes("finally { refreshingTargets = false; }"));
  console.log("No automatic Firefox tab creation or deletion: PASS");
})().catch(e=>{console.error(e);process.exitCode=1});
