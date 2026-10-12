// Execute the real v2 High selector against a mocked copy of provided UI.
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const src=fs.readFileSync(path.join(__dirname,"../public/zcloud-worker-v2-engine.js"),"utf8");
const start=src.indexOf("  function high(){"),end=src.indexOf("  function newChatButton()",start);
assert.ok(start>0&&end>start,"locate live High verification implementation");
const impl=src.slice(start,end);
const observed=[];
const rectangle=()=>({width:20,height:20});
const model=(name,onClick=()=>{})=>({
 innerText:name,textContent:name,
 getAttribute:k=>k==="aria-label"?name:k==="aria-checked"?"false":null,
 getBoundingClientRect:rectangle,click:onClick
});
function runner({initialHigh=false,highOption=true,pickerExists=true}){
 let selected=initialHigh,opened=false,clicks=0;
 const button=model("ChatGPT-model selecteren",()=>{opened=true;clicks++});
 const choice=model("High",()=>{selected=true;clicks++});
 const data={getAttribute:k=>k==="data-selected-reasoning-effort"?"high":null,
  getBoundingClientRect:rectangle};
 const document={
  querySelector:q=>pickerExists&&q==='button[aria-label="ChatGPT-model selecteren"]'?button:null,
  querySelectorAll:q=>{
   if(q.includes('[data-selected-reasoning-effort="high"]'))return selected?[data]:[];
   if(q==="button,[role=\"button\"]")return [];
   if(q.includes("menuitemradio")||q.includes("menuitem")||q.includes("role=\"option\""))
     return opened&&highOption?[choice]:[];
   return [];
  }
 };
 const ctx=vm.createContext({
  document,visible:e=>!!e&&e.getBoundingClientRect().width>0,
  label:e=>e.getAttribute("aria-label")||e.innerText||"",
  sleep:async()=>{},status:async(...x)=>observed.push(x),
  Array,Set,Number,String,Date
 });
 vm.runInContext(impl,ctx,{timeout:1000});
 return {high:()=>vm.runInContext("high()",ctx),
  ensureHigh:()=>vm.runInContext("ensureHigh()",ctx),getClicks:()=>clicks};
}
(async()=>{
 const existing=runner({initialHigh:true});
 assert.equal(await existing.ensureHigh(),true);
 assert.equal(existing.getClicks(),0,"already High requires no menu change");
 const change=runner({});
 assert.equal(await change.ensureHigh(),true,"must select exact High option");
 assert.ok(change.getClicks()>=2,"must first open selector, then click High");
 const noOption=runner({highOption:false});
 assert.equal(await noOption.ensureHigh(),false,"unverifiable High blocks prompt");
 const noPicker=runner({pickerExists:false});
 assert.equal(await noPicker.ensureHigh(),false,"missing UI blocks prompt");
 assert.ok(observed.some(x=>x[0]==="thinking-high-unavailable"));
 console.log("High Thinking v2: already High, click and verify, fail closed PASS");
})().catch(e=>{console.error(e);process.exitCode=1});
