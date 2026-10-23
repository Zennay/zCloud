// Regression from the supplied Work-mode HTML: hidden duplicate picker,
// asynchronous menu, descriptive High radio, and authoritative Medium state.
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const src=fs.readFileSync("public/zcloud-worker-v2-engine.js","utf8");
const impl=src.slice(src.indexOf("  function high(){"),src.indexOf("  function newChatButton()"));
async function scenario({missing=false,ignored=false,initial="medium"}={}){
 let effort=initial,opened=false,waits=0,visibleClicks=0,highClicks=0;
 const el=(attrs,text="",visible=true)=>({innerText:text,textContent:text,visible,
  getAttribute:k=>attrs[k]??null,click:()=>{}});
 const hidden=el({"aria-expanded":"false"},"Medium",false);
 hidden.click=()=>{throw Error("hidden picker clicked")};
 const picker=el({"aria-expanded":"false"},"Medium");
 picker.getAttribute=k=>k==="data-selected-reasoning-effort"?effort:k==="aria-expanded"?String(opened):null;
 picker.click=()=>{opened=true;visibleClicks++};
 const high=el({role:"radio"},"High\nSpend more time reasoning");
 high.click=()=>{highClicks++;if(!ignored)effort="high"};
 const unrelated=el({"aria-checked":"true","aria-label":"High"},"High");
 const doc={
  querySelector:s=>s.includes("data-codex-intelligence-trigger")?hidden:null,
  querySelectorAll:s=>{
   if(s==='button[data-codex-intelligence-trigger][data-selected-reasoning-effort]')return [hidden,picker];
   if(s.includes('button[data-codex-intelligence-trigger]'))return [hidden,picker];
   if(s==='[data-selected-reasoning-effort="high"]')return effort==="high"?[picker]:[];
   if(s.includes('button,[role="menuitemradio"]'))return opened&&waits>=5&&!missing?[high]:[];
   if(s.startsWith('[role="menuitemradio"]'))return [unrelated];
   return [];
  }
 };
 const ctx=vm.createContext({document:doc,visible:x=>!!x?.visible,
  label:x=>x?.getAttribute("aria-label")||x?.innerText||"",
  sleep:async()=>{waits++},status:async()=>{}});
 vm.runInContext(impl,ctx);
 const result=await vm.runInContext("ensureHigh()",ctx);
 return {result,effort,visibleClicks,highClicks};
}
(async()=>{
 assert.deepEqual(await scenario(),{result:true,effort:"high",visibleClicks:1,highClicks:1});
 assert.equal((await scenario({missing:true})).result,false,"unrelated checked High cannot override Medium");
 assert.equal((await scenario({ignored:true})).result,false,"clicking High without state change is not success");
 assert.equal((await scenario({initial:"high"})).visibleClicks,0,"already High needs no click");
 console.log("Work-mode High controls: hidden duplicate, slow radio, verification PASS");
})().catch(e=>{console.error(e);process.exitCode=1});
