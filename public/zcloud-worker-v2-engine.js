// zCloud v2 browser DOM engine. Bundled into zcloud-worker.user.js.
// Exactly one slot-bound browser tab; no window/tab APIs and no location redirects.
(() => {
  "use strict";
  window.__ZCLOUD_SINGLE_TAB_V2 = true;
  if (location.hostname !== "chatgpt.com") return;
  const fromUrl = new URL(location.href).searchParams.get("zcloud_worker") || "";
  const stored = sessionStorage.getItem("zcloud-v2-worker") || sessionStorage.getItem("zcloud-bound-project") || "";
  if (fromUrl && stored && fromUrl !== stored) return;
  const id = fromUrl || stored;
  if (!/^[a-z0-9_-]+::w[1-9][0-9]*$/i.test(id) || window.__ZCLOUD_V2_ENGINE_ACTIVE) return;
  window.__ZCLOUD_V2_ENGINE_ACTIVE = true;
  sessionStorage.setItem("zcloud-v2-worker",id);
  document.documentElement?.setAttribute("data-zcloud-violentmonkey-ready","2.0.0");
  document.documentElement?.setAttribute("data-zcloud-v2-owner",id);
  const API="http://127.0.0.1:8765/api",POLL_MS=30000,MAX_AGE=3600000;
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  let target=null,inflight=false,sending=false,awaiting=false,everGenerating=false;
  let promptAt=0,awaitingAt=0,baseline="",baselineAssistantCount=0,next=false,lastBlock="",lastCommand=0;
  let reportedConversation=sessionStorage.getItem("zcloud-v2-reported-conversation")||"";
  let chatAt=Number(sessionStorage.getItem("zcloud-v2-chat-at")||0);
  if(!chatAt||chatAt>Date.now())chatAt=Date.now();
  sessionStorage.setItem("zcloud-v2-chat-at",String(chatAt));
  const label=el=>String(el?.getAttribute?.("aria-label")||el?.getAttribute?.("aria-valuetext")||el?.innerText||el?.textContent||"").trim();
  const visible=el=>{if(!el)return false;const s=getComputedStyle(el),r=el.getBoundingClientRect();return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};
  const generating=()=>!!document.querySelector('button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop response"]');
  const reply=()=>{const a=[...document.querySelectorAll('[data-message-author-role="assistant"]')];return a.length?String(a[a.length-1].innerText||a[a.length-1].textContent||"").trim():""};
  const conversation=()=>location.pathname.match(/^\/c\/([0-9a-f-]{20,})/i)?.[1]||"";
  const assistantCount=()=>document.querySelectorAll('[data-message-author-role="assistant"]').length;
  function api(path,method="GET",body=null){
    return new Promise((resolve,reject)=>GM_xmlhttpRequest({
      method,url:API+path,timeout:9000,headers:body?{"Content-Type":"application/json"}:undefined,
      data:body?JSON.stringify(body):undefined,
      onload:r=>{try{if(r.status<200||r.status>=300)throw Error("HTTP "+r.status);resolve(JSON.parse(r.responseText||"{}"))}catch(e){reject(e)}},
      onerror:()=>reject(Error("offline")),ontimeout:()=>reject(Error("timeout"))
    }));
  }
  async function status(event,extra={}){
    if(!target)return;
    const at=new Date().toISOString();
    try{await api("/runner-status","POST",{
      projectId:id,baseProjectId:target.base_project_id||id.split("::w")[0],
      workerSlot:target.worker_slot||1,globalWorkerSlot:target.global_worker_slot||1,
      queueItem:target.queue_item?.queue_id||"",projectName:target.name||id,
      target:location.href,targetConversation:conversation(),title:document.title,
      event,at,progressAt:at,generating:generating(),sending,
      assistantCharacters:reply().length,runner:"violentmonkey",
      provider:"chatgpt",runnerVersion:"2.0.0",...extra
    })}catch(_){}
  }
  async function blocked(reason){if(lastBlock===reason)return;lastBlock=reason;await status("send-blocked",{reason})}
  function high(){
    if([...document.querySelectorAll('[data-selected-reasoning-effort="high"]')].some(visible))return true;
    if([...document.querySelectorAll('button,[role="button"]')].some(el=>visible(el)&&/^(high|hoog) selector\b/i.test(label(el))))return true;
    return [...document.querySelectorAll('[role="menuitemradio"],[role="radio"],[role="option"]')]
      .some(el=>visible(el)&&/^(high|hoog)$/i.test(label(el))&&
        (el.getAttribute("aria-checked")==="true"||el.getAttribute("aria-selected")==="true"));
  }
  async function ensureHigh(){
    if(high())return true;
    const selectors=['button[data-codex-intelligence-trigger]',
      'button[aria-label="ChatGPT-model selecteren"]','button[aria-label="Select ChatGPT model"]',
      'button[data-testid="model-switcher-dropdown-button"]',
      'button[aria-label*="Thinking" i][aria-haspopup]'];
    const picker=selectors.map(s=>document.querySelector(s)).find(visible);
    if(!picker){await status("thinking-high-unavailable",{reason:"selector-missing"});return false}
    picker.click();await sleep(450);
    if(high())return true;
    let option=[...document.querySelectorAll('button,[role="menuitemradio"],[role="menuitem"],[role="option"]')]
      .find(e=>visible(e)&&/^(high|hoog)(?: thinking)?$/i.test(label(e)));
    if(!option){
      const thinking=[...document.querySelectorAll('button,[role="menuitem"],[role="option"]')]
        .find(e=>visible(e)&&/^(thinking|denkmodus)$/i.test(label(e)));
      if(thinking){thinking.click();await sleep(350)}
      option=[...document.querySelectorAll('button,[role="menuitemradio"],[role="menuitem"],[role="option"]')]
        .find(e=>visible(e)&&/^(high|hoog)(?: thinking)?$/i.test(label(e)));
    }
    if(option)option.click();
    for(let n=0;n<15;n++){await sleep(170);if(high())return true}
    await status("thinking-high-unavailable",{reason:"not-confirmed"});
    return false; // NEVER send in Instant/Medium.
  }
  function newChatButton(){
    return ['button[aria-label="Nieuwe chat"]','button[aria-label="New chat"]',
      'button[data-testid="new-chat-button"]'].map(s=>document.querySelector(s)).find(visible)||null;
  }
  function failedConversation(){
    const t=String((document.querySelector("main")||document.body)?.innerText||"").toLowerCase();
    return /could not load (?:this )?(?:chatgpt )?conversation|conversation not found|gesprek kon niet worden geladen/.test(t);
  }
  async function newChat(reason){
    if(generating()||sending||awaiting)return false;
    const button=newChatButton();
    if(!button){await blocked("new-chat-button-missing");return false}
    button.click(); // SAME browser tab; SPA route, not location.assign
    chatAt=Date.now();sessionStorage.setItem("zcloud-v2-chat-at",String(chatAt));
    promptAt=0;next=true;everGenerating=false;baseline="";
    await status("conversation-reset-requested",{reason});
    return true;
  }
  const composer=()=>document.querySelector('#prompt-textarea,[contenteditable="true"][role="textbox"],textarea');
  const sendButton=()=>document.querySelector('button[data-testid="send-button"],button[aria-label="Send message"],button[aria-label="Verzenden"]');
  async function fill(text){
    const box=composer();if(!visible(box))return false;
    box.focus({preventScroll:true});
    if(box.tagName==="TEXTAREA"||box.tagName==="INPUT"){
      const proto=box.tagName==="TEXTAREA"?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
      const setter=Object.getOwnPropertyDescriptor(proto,"value")?.set;
      if(setter)setter.call(box,text);else box.value=text;
      box.dispatchEvent(new Event("input",{bubbles:true}));
    }else{
      document.execCommand("selectAll",false,null);
      document.execCommand("insertText",false,text);
      box.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText",data:text}));
    }
    await sleep(350);
    return !!String(box.value||box.innerText||box.textContent||"").trim();
  }
  function ready(t){
    if(!t||t.active!==true||t.assignment_ready!==true||t.desired_state==="paused"||t.desired_state==="draining")return false;
    const slot=Number(t.global_worker_slot||0),total=Number(t.global_worker_count||0);
    return slot>0&&total>=slot&&!!String(t.queue_item?.queue_id||"")&&
      String(t.prompt||"").startsWith("Werk verder aan ")&&
      String(t.prompt||"").includes("Kijk in Notion");
  }
  async function send(reason){
    if(sending||awaiting||generating()||!target||!ready(target))return false;
    const prompt=String(target.prompt||"").trim();if(!prompt)return false;
    sending=true;
    try{
      if(!(await ensureHigh())){await blocked("high-not-verified");return false}
      if(!(await fill(prompt))){await blocked("composer-missing");return false}
      const button=sendButton();
      if(!visible(button)||button.disabled||button.getAttribute("aria-disabled")==="true"){
        await blocked("send-button-unavailable");return false;
      }
      baseline=reply();baselineAssistantCount=assistantCount();button.click();promptAt=Date.now();awaitingAt=promptAt;
      awaiting=true;everGenerating=false;next=false;lastBlock="";
      await status("prompt-sent",{reason,thinkingEffort:"high"});
      return true;
    }finally{sending=false}
  }
  async function commands(){
    let result;try{result=await api("/runner-commands")}catch(_){return}
    const incoming=(result.commands||[]).filter(c=>c&&Number(c.id||0)>lastCommand&&
      (c.project_id===id||(c.project_id===target?.base_project_id&&Number(target?.worker_slot||1)===1)));
    for(const command of incoming){
      const action=String(command.action||"");
      if(!["start","push","new_chat"].includes(action))continue;
      if(awaiting||generating()||sending)continue;
      // Legacy watchdogs queue new_chat after a stale heartbeat, which used
      // to steal a tab straight after Send. In v2, ONLY a visible load error
      // or the 1-hour idle policy may reset the conversation.
      if(action==="new_chat"){
        try{await api("/runner-command-result","POST",{
          command_id:command.id,status:"failed",
          result:"v2 preserved tab; in-tab rotation limited to error or one-hour idle"
        });lastCommand=Number(command.id)}catch(_){}
        continue;
      }
      if(!(await send("database-"+action)))continue;
      try{await api("/runner-command-result","POST",{
        command_id:command.id,status:"completed",result:"single-tab worker v2 action"
      });lastCommand=Number(command.id)}catch(_){}
    }
  }
  async function poll(){
    if(inflight)return;inflight=true;
    // This is a liveness signal for the Firefox controller; independent of
    // the selected tab or an ongoing assignment. A paused worker may report
    // liveness, but it is NEVER reloaded while its assignment is paused.
    document.documentElement?.setAttribute("data-zcloud-v2-last-poll",String(Date.now()));
    try{
      const payload=await api("/runner-targets"),actual=payload.projects?.[id]||null;
      target=actual;
      if(!ready(actual))return;
      await status("heartbeat",{reason:"worker-v2-check-30s"});
      const cid=conversation();
      if(cid && cid!==reportedConversation) {
        reportedConversation=cid;
        sessionStorage.setItem("zcloud-v2-reported-conversation",cid);
        await status("conversation-adopted",{
          reason:"v2-in-place-conversation",targetConversation:cid
        });
      }
      if(generating()){
        everGenerating=true;awaiting=false;
        await status("generation-progress");return;
      }
      const result=reply();
      if(awaiting){
        if(everGenerating||assistantCount()>baselineAssistantCount||(result&&result!==baseline)){
          awaiting=false;everGenerating=false;next=true;
          await status("generation-finished");
        }else if(Date.now()-awaitingAt<120000){return}
        else{await blocked("generation-unconfirmed-no-duplicate");return}
      }else if(everGenerating){
        everGenerating=false;next=true;await status("generation-finished");
      }
      if(failedConversation()){await newChat("could-not-load-conversation");return}
      if(Date.now()-chatAt>=MAX_AGE){
        if(await newChat("one-hour-conversation"))return;
      }
      await commands();
      if((next||!promptAt)&&!awaiting&&!generating())
        await send(next?"next-prompt-after-response":"initial-prompt");
    }catch(e){await blocked("poll-error:"+String(e.message||e).slice(0,80))}
    finally{inflight=false}
  }
  poll();setInterval(poll,POLL_MS);
})();
