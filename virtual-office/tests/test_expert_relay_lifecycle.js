// Small isolated Tampermonkey lifecycle test. No ChatGPT account or VPS tokens.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const script = fs.readFileSync(path.join(__dirname, "..", "office-relay.user.js"), "utf8");
const URL_A = "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef";
const URL_B = "https://chatgpt.com/c/22222222-89ab-cdef-0123-456789abcdef";
const KEY = "zcloud-office-relay-remembered-v1";

function mount(options = {}) {
  const storage = options.storage || new Map();
  const pathName = new URL(options.url || URL_A).pathname;
  const timerQueue = [];
  const menu = {};
  const requests = [];
  const promptAnswers = [...(options.prompts || [
    "http://127.0.0.1:3422", "maya", "a-test-only-office-code-do-not-use"
  ])];
  const status = {style:{},setAttribute(){},textContent:"",title:""};
  const doc = {
    body:{append(){}},
    addEventListener(){},
    createElement(){return status},
    documentElement:{hasAttribute:(name)=>name==="data-zcloud-worker-id" && !!options.worker},
    querySelector(){return null}
  };
  const tabStorage = new Map();
  const context = {
    document:doc,
    window:{addEventListener(){}},
    location:{pathname:pathName},
    URL, Math, Date, Promise, JSON, String, Error, Object, RegExp,
    crypto:{randomUUID(){return "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"}},
    sessionStorage:{
      getItem:k=>tabStorage.get(k)||null,
      setItem:(k,v)=>tabStorage.set(k,v),
      removeItem:k=>tabStorage.delete(k)
    },
    GM_registerMenuCommand:(label,handler)=>{menu[label]=handler},
    GM_getValue:(key,fallback)=>storage.has(key)?storage.get(key):fallback,
    GM_setValue:(key,value)=>storage.set(key,value),
    GM_deleteValue:key=>storage.delete(key),
    GM_xmlhttpRequest(req){
      requests.push(req);
      const response = req.url.endsWith("/api/health")
        ? {status:200,responseText:JSON.stringify({online:true,mode:"zcloud_chatgpt_chats"})}
        : {status:options.pollStatus || 200,
           responseText:JSON.stringify(options.pollStatus===401?{error:"wrong code"}:{ok:true,job:null})};
      req.onload(response);
    },
    prompt:()=>promptAnswers.shift() || null,
    confirm:()=>options.remember !== false,
    alert(){},
    setInterval(){return 1},
    setTimeout:(fn,ms)=>{timerQueue.push({fn,ms});return timerQueue.length},
    console
  };
  vm.runInNewContext(script,context,{filename:"office-relay.user.js"});
  return {status,menu,requests,storage,timerQueue};
}
async function flush() {for(let i=0;i<20;i++)await Promise.resolve()}

(async()=>{
  const saved = new Map();
  const initial=mount({storage:saved});
  assert.equal(initial.status.textContent,"Office Relay · uit");
  await initial.status.onclick();
  await flush();
  let data=saved.get(KEY);
  assert.equal(data.version,1);
  assert.equal(data.chats[URL_A].employee,"maya");
  assert.equal(data.chats[URL_A].origin,"http://127.0.0.1:3422");
  assert.match(initial.status.textContent,/maya verbonden/);
  assert.equal(initial.requests.some(r=>r.url.endsWith("/api/relay/poll")),true);

  // A crash restores the exact same conversation, without prompts or a
  // user-generated duplicate ChatGPT instruction.
  const restored=mount({storage:saved});
  const startup=restored.timerQueue.find(t=>t.ms===1200);
  assert.ok(startup);
  await startup.fn();
  await flush();
  assert.match(restored.status.textContent,/maya verbonden/);
  assert.equal(restored.requests.filter(r=>r.url.endsWith("/api/relay/poll")).length,1);
  assert.equal(restored.requests.some(r=>r.url.endsWith("/api/relay/reply")),false);

  // A different new chat is *not* adopted just because Maya was saved.
  const unrelated=mount({storage:saved,url:URL_B,prompts:["maya"]});
  await unrelated.timerQueue.find(t=>t.ms===1200).fn();
  await flush();
  assert.equal(unrelated.requests.length,0);
  assert.equal(unrelated.status.textContent,"Office Relay · uit");

  // User can deliberately migrate Maya to a new chat; old binding then goes.
  unrelated.menu["Koppel nieuwe chat aan opgeslagen medewerker"]();
  await flush();
  data=saved.get(KEY);
  assert.equal(data.chats[URL_A],undefined);
  assert.equal(data.chats[URL_B].employee,"maya");

  // A dynamic worker tab must never restore an expert identity.
  const worker=mount({storage:saved,url:URL_B,worker:true});
  await worker.timerQueue.find(t=>t.ms===1200).fn();
  await flush();
  assert.equal(worker.requests.length,0);

  // The owner can refuse unencrypted storage; no secret is persisted.
  const privateStore=new Map();
  const ephemeral=mount({storage:privateStore,remember:false});
  await ephemeral.status.onclick();
  await flush();
  assert.equal(privateStore.has(KEY),false);

  // Incorrect token must never be persisted just because the user opted in.
  const rejected=new Map();
  const wrong=mount({storage:rejected,pollStatus:401});
  await wrong.status.onclick();
  await flush();
  assert.equal(rejected.has(KEY),false);

  // Stop removes stored secrets for this chat, not unrelated workers.
  const clean=mount({storage:saved,url:URL_B});
  await clean.timerQueue.find(t=>t.ms===1200).fn();
  await flush();
  clean.menu["Office Relay stoppen + onthouden koppeling wissen"]();
  assert.equal(saved.has(KEY),false);

  for(const id of ["maya","noah","lina"])
    assert.ok(script.includes(id+":'Je bent"),"missing employee persona: "+id);
  assert.ok(script.includes("data-zcloud-worker-id"));
  assert.ok(script.includes("rememberAfterSuccess"));
  assert.ok(script.includes("onload:r"));
  console.log("Office Relay: opt-in crash restore, role isolation and new-chat rebinding PASS");
})().catch(e=>{console.error(e);process.exitCode=1});
