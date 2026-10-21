// zCloud v2 browser DOM engine. Bundled into zcloud-worker.user.js.
// Exactly one slot-bound browser tab; no window/tab APIs and no location redirects.
(() => {
  "use strict";
  window.__ZCLOUD_SINGLE_TAB_V2 = true;
  if (location.hostname !== "chatgpt.com") return;
  const fromUrl = new URL(location.href).searchParams.get("zcloud_worker") || "";
  const stored = sessionStorage.getItem("zcloud-v2-worker") || sessionStorage.getItem("zcloud-bound-project") || "";
  if (fromUrl && stored && fromUrl !== stored) return;
  // Force commands have a unique browser-tab ticket. An older conversation
  // with the same worker ID must not steal a forced prompt on project switch.
  const ticketFromUrl = new URL(location.href).searchParams.get("zcloud_force_command") || "";
  const forceTicket = /^(?:[1-9][0-9]*)$/.test(ticketFromUrl) ? ticketFromUrl :
    sessionStorage.getItem("zcloud-v2-force-command") || "";
  if (ticketFromUrl) sessionStorage.setItem("zcloud-v2-force-command",forceTicket);
  const id = fromUrl || stored;
  if (!/^[a-z0-9_-]+::w[1-9][0-9]*$/i.test(id) || window.__ZCLOUD_V2_ENGINE_ACTIVE) return;
  window.__ZCLOUD_V2_ENGINE_ACTIVE = true;
  sessionStorage.setItem("zcloud-v2-worker",id);
  document.documentElement?.setAttribute("data-zcloud-violentmonkey-ready","2.0.3");
  document.documentElement?.setAttribute("data-zcloud-v2-owner",id);
  const API="http://127.0.0.1:8765/api",POLL_MS=30000,MAX_AGE=3600000;
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  let target=null,inflight=false,sending=false,awaiting=false,everGenerating=false;
  // One bounded composer repair episode per verified queue assignment.
  const COMPOSER_IDLE_REPAIR_MS=45000;
  let missingComposerSince=0,composerRepairAttempted=false,composerRepairQueueId="";
  const markBusy=()=>document.documentElement?.setAttribute("data-zcloud-v2-busy",
    sending||awaiting||generating()?"true":"false");
  // An explicit Force owns one command ID and may only be delivered to its
  // freshly created tab. Fail closed when the local command service is down.
  async function forceAllowed(){
    try{
      const payload=await api("/runner-commands?limit=100");
      return !(payload.commands||[]).some(c=>c?.force===true &&
        (c.project_id===id||c.project_id===target?.base_project_id) &&
        String(c.id)!==forceTicket);
    }catch(_){return false}
  }
  let resetRoute=null;
  let promptAt=0,awaitingAt=0,baseline="",baselineAssistantCount=0,next=false,lastBlock="",lastCommand=0;
  // The normal in-page reply loop may continue a queue, but a full reload
  // loses all reply/ack evidence. Retain only the previously dispatched queue
  // identity in this tab, never the private prompt or its answer.
  const SENT_QUEUE_KEY="zcloud-v2-last-dispatched-queue";
  let sentInThisDocument=false;
  function sameQueueWasSentBeforeReload(queueId){
    const raw=sessionStorage.getItem(SENT_QUEUE_KEY);
    if(raw===null)return false;
    if(raw.length>320)return true; // Corrupt previous evidence is not clearance.
    try{
      const seen=JSON.parse(raw);
      if(!seen||typeof seen!=="object"||Array.isArray(seen)||
         typeof seen.worker!=="string"||typeof seen.queue!=="string"||
         !seen.worker||!seen.queue)return true;
      return seen.worker===id&&seen.queue===queueId;
    }catch(_){return true}
  }
  let reportedConversation=sessionStorage.getItem("zcloud-v2-reported-conversation")||"";
  let chatAt=Number(sessionStorage.getItem("zcloud-v2-chat-at")||0);
  if(!chatAt||chatAt>Date.now())chatAt=Date.now();
  sessionStorage.setItem("zcloud-v2-chat-at",String(chatAt));
  const label=el=>String(el?.getAttribute?.("aria-label")||el?.getAttribute?.("aria-valuetext")||el?.innerText||el?.textContent||"").trim();
  const visible=el=>{if(!el)return false;const s=getComputedStyle(el),r=el.getBoundingClientRect();return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};
  // Responsive portals can retain a hidden old stop button. Only the visible
  // control indicates that the assigned conversation is currently generating.
  const generating=()=>[...document.querySelectorAll('button[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop response"]')].some(visible);
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
      provider:"chatgpt",runnerVersion:"2.0.3",...extra
    })}catch(_){}
  }
  async function blocked(reason){if(lastBlock===reason)return;lastBlock=reason;await status("send-blocked",{reason})}
  function high(){
    // The live composer attribute is authoritative: a checked menu item
    // elsewhere must never override an explicitly Medium/Low composer.
    const triggers=[...document.querySelectorAll('button[data-codex-intelligence-trigger][data-selected-reasoning-effort]')].filter(visible);
    if(triggers.length)return triggers.every(el=>el.getAttribute("data-selected-reasoning-effort")==="high");
    if([...document.querySelectorAll('[data-selected-reasoning-effort="high"]')].some(visible))return true;
    if([...document.querySelectorAll('button,[role="button"]')].some(el=>visible(el)&&/^(high|hoog) selector\b/i.test(label(el))))return true;
    return [...document.querySelectorAll('[role="menuitemradio"],[role="radio"],[role="option"]')]
      .some(el=>visible(el)&&/^(high|hoog)$/i.test(label(el))&&
        (el.getAttribute("aria-checked")==="true"||el.getAttribute("aria-selected")==="true"));
  }
  async function ensureHigh(){
    if(high())return true;
    const selectors=['button[data-codex-intelligence-trigger][data-composer-navigation-target="reasoning"]',
      'button[data-codex-intelligence-trigger]',
      'button[aria-label="ChatGPT-model selecteren"]','button[aria-label="Select ChatGPT model"]',
      'button[data-testid="model-switcher-dropdown-button"]',
      'button[aria-label*="Thinking" i][aria-haspopup]'];
    // Responsive layouts can contain a hidden first match. Inspect all.
    const picker=selectors.flatMap(s=>[...document.querySelectorAll(s),document.querySelector(s)]).find(visible);
    if(!picker){await status("thinking-high-unavailable",{reason:"selector-missing"});return false}
    if(picker.getAttribute("aria-expanded")!=="true")picker.click();
    const exactHigh=el=>{
      const value=String(el.getAttribute("data-value")||"").trim();
      const name=String(el.getAttribute("aria-label")||"").trim();
      const text=String(el.innerText||el.textContent||"").trim().split(/\n/)[0].trim();
      return /^(high|hoog)(?: thinking)?$/i.test(value||name||text);
    };
    let selected=false,openedThinking=false;
    for(let n=0;n<20;n++){
      await sleep(170);
      if(high())return true;
      const candidates=[...document.querySelectorAll('button,[role="menuitemradio"],[role="radio"],[role="menuitem"],[role="option"]')];
      const option=candidates.find(el=>visible(el)&&exactHigh(el));
      if(option&&!selected){option.click();selected=true;continue}
      if(!option&&!openedThinking){
        const thinking=candidates.find(el=>visible(el)&&/^(thinking|denkmodus)$/i.test(label(el)));
        if(thinking){thinking.click();openedThinking=true}
      }
    }
    await status("thinking-high-unavailable",{reason:"not-confirmed"});
    return false; // NEVER send in Instant/Medium.
  }
  function newChatButton(){
    return ['button[aria-label="Nieuwe chat"]','button[aria-label="New chat"]',
      'button[data-testid="new-chat-button"]'].flatMap(s=>[...document.querySelectorAll(s),document.querySelector(s)]).find(visible)||null;
  }
  function failedConversation(){
    // Never treat a quoted error in an actual conversation as an error page.
    // A terminal load failure has no usable composer and no visible chat turns.
    const main=document.querySelector("main");
    if(!main||visible(composer()))return false;
    if([...main.querySelectorAll('[data-message-author-role],[data-testid="conversation-turn"]')].some(visible))
      return false;
    const t=String(main.innerText||"").trim().toLowerCase();
    if(!t||t.length>2000)return false; // Large pages are not a bounded error screen.
    return /could not load (?:this )?(?:chatgpt )?conversation|conversation not found|gesprek kon niet worden geladen/.test(t);
  }
  async function newChat(reason){
    if(generating()||sending||awaiting)return false;
    const button=newChatButton();
    if(!button){await blocked("new-chat-button-missing");return false}
    resetRoute=location.pathname;
    button.click(); // SAME browser tab; SPA route, not location.assign
    chatAt=Date.now();sessionStorage.setItem("zcloud-v2-chat-at",String(chatAt));
    promptAt=0;next=true;everGenerating=false;baseline="";
    await status("conversation-reset-requested",{reason});
    return true;
  }
  function resetComposerRepair(queueId=""){
    // A new queue assignment needs its own operator blocker receipt. The
    // previous assignment's last reason must not suppress an identical issue
    // in this distinct lease, but do not emit duplicate events on each poll.
    if(composerRepairQueueId!==queueId)lastBlock="";
    missingComposerSince=0;
    composerRepairAttempted=false;
    composerRepairQueueId=queueId;
  }
  async function recoverIdleComposer(nowMs=Date.now()){
    const queueId=String(target?.queue_item?.queue_id||"");
    if(!queueId||!ready(target)||target?.project_id!==id){
      resetComposerRepair();return false;
    }
    if(composerRepairQueueId!==queueId)resetComposerRepair(queueId);
    if(visible(composer())){
      if(missingComposerSince>0){
        lastBlock="";
        await status("composer-restored",{reason:"composer-visible-on-owned-worker"});
      }
      resetComposerRepair(queueId);
      return false;
    }
    // Never reset after sending, during a response, while the SPA is
    // transitioning, or if a new assignment is not verified. This is not
    // general permission to change an existing conversation.
    if(sending||awaiting||generating()){
      missingComposerSince=0;
      return false;
    }
    if(resetRoute!==null)return false;
    // A confirmed terminal conversation-load error has its own guarded
    // recovery later in poll(). Do not let the missing-composer grace return
    // early and permanently hide that stronger, existing recovery signal.
    if(failedConversation())return false;
    // Hold all prompt sends until the composer returns or one safe repair
    // has been requested; do not keep retrying fill() against a missing DOM.
    if(!missingComposerSince){missingComposerSince=nowMs;return true}
    if(composerRepairAttempted||nowMs-missingComposerSince<COMPOSER_IDLE_REPAIR_MS)
      return true;
    composerRepairAttempted=true;
    if(promptAt>0||everGenerating||conversation()||location.pathname!=="/"){
      await blocked("composer-missing-manual-recovery-required");
      return true;
    }
    // The tab is already on an empty/new-chat route, and this worker has
    // never dispatched a prompt. A single existing SPA New chat action is
    // allowed; never navigate/reload an existing chat or repeat the action.
    const started=await newChat("composer-missing-before-first-send");
    if(!started)await blocked("composer-missing-operator-action-required");
    return true;
  }
  const composer=()=>{
    // Responsive layouts can leave a hidden duplicate composer in the DOM.
    // Never mistake the first hidden match for a missing live input.
    const selectors=['#prompt-textarea','[contenteditable="true"][role="textbox"]','textarea'];
    for(const selector of selectors){
      const shown=[...document.querySelectorAll(selector)].find(visible);
      if(shown)return shown;
    }
    return document.querySelector(selectors.join(",")); // Preserve draft detection if all are hidden.
  };
  const sendButton=()=>[...document.querySelectorAll('button[data-testid="send-button"],button[aria-label="Send message"],button[aria-label="Verzenden"]')].find(visible)||null;
  const composerText=box=>String(box?.value??box?.innerText??box?.textContent??"")
    .replace(/\r\n?/g,"\n").trim();
  async function fill(text){
    const box=composer();if(!visible(box))return "missing";
    const wanted=String(text).replace(/\r\n?/g,"\n").trim();
    // Never overwrite a human draft or a previous assignment's unsent text.
    // Check again here because ensureHigh() can spend seconds in UI menus.
    const previous=composerText(box);
    if(previous&&previous!==wanted)return "draft-preserved";
    box.focus({preventScroll:true});
    if(box.tagName==="TEXTAREA"||box.tagName==="INPUT"){
      const proto=box.tagName==="TEXTAREA"?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
      const setter=Object.getOwnPropertyDescriptor(proto,"value")?.set;
      if(setter)setter.call(box,text);else box.value=text;
      box.dispatchEvent(new Event("input",{bubbles:true}));
    }else if(previous!==wanted){
      document.execCommand("selectAll",false,null);
      document.execCommand("insertText",false,text);
      box.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText",data:text}));
    }
    await sleep(350);
    // Earlier a merely nonempty (possibly stale) draft authorized Send.
    // A source-equivalent exact value is required before lease revalidation.
    return composerText(box)===wanted?"ready":"text-not-confirmed";
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
    const dispatchedQueue=String(target.queue_item.queue_id);
    if(!sentInThisDocument&&sameQueueWasSentBeforeReload(dispatchedQueue)){
      await blocked("previous-queue-send-after-reload-manual-review");
      return false;
    }
    if(!(await forceAllowed())){await blocked("force-tab-handoff-required");return false}
    const prompt=String(target.prompt||"").trim();if(!prompt)return false;
    // An operator's existing draft is never permission to replace it with
    // an automated queue prompt, even before the Thinking menu opens.
    const initialComposer=composer();
    if(visible(initialComposer)){
      const draft=composerText(initialComposer);
      if(draft&&draft!==prompt){await blocked("composer-draft-preserved");return false}
    }
    sending=true;markBusy();
    try{
      if(!(await ensureHigh())){await blocked("high-not-verified");return false}
      const editor=await fill(prompt);
      if(editor!=="ready"){
        await blocked(editor==="missing"?"composer-missing":
          editor==="draft-preserved"?"composer-draft-preserved":"composer-text-not-confirmed");
        return false;
      }
      // Recheck after asynchronous UI work; settings/allocation can change.
      if(!high()){await blocked("high-changed-before-send");return false}
      const fresh=await api("/runner-targets");
      const current=fresh.projects?.[id];
      // A queue ID alone is not proof that the assignment still authorizes
      // this exact prompt. A same-queue edit, slot move or owner change
      // during the Thinking/editor wait must not dispatch stale instructions.
      if(!ready(current)||current.project_id!==id||
         current.queue_item.queue_id!==target.queue_item.queue_id||
         current.prompt!==prompt||
         Number(current.global_worker_slot)!==Number(target.global_worker_slot)){
        await blocked("assignment-changed-before-send");return false;
      }
      const button=sendButton();
      if(!visible(button)||button.disabled||button.getAttribute("aria-disabled")==="true"){
        await blocked("send-button-unavailable");return false;
      }
      // Human edits or late SPA transitions can happen while checking the
      // current queue lease. Never send a stale or partially replaced draft.
      const currentEditor=composer();
      if(!visible(currentEditor)||composerText(currentEditor)!==prompt){
        await blocked("composer-changed-before-send");return false;
      }
      // Persist a conservative may-have-sent marker BEFORE the irreversible
      // click. If storage fails, do not send; do not erase or auto-ack the
      // original queue after a browser restart.
      try{
        sessionStorage.setItem(SENT_QUEUE_KEY,JSON.stringify({
          worker:id,queue:dispatchedQueue
        }));
      }catch(_){await blocked("dispatch-ledger-unavailable");return false}
      baseline=reply();baselineAssistantCount=assistantCount();button.click();promptAt=Date.now();awaitingAt=promptAt;
      sentInThisDocument=true;
      awaiting=true;everGenerating=false;next=false;lastBlock="";
      await status("prompt-sent",{reason,thinkingEffort:"high"});
      return true;
    }finally{sending=false;markBusy()}
  }
  async function commands(){
    let result;try{result=await api("/runner-commands")}catch(_){return}
    const incoming=(result.commands||[]).filter(c=>c&&Number(c.id||0)>lastCommand&&
      (c.project_id===id||(c.project_id===target?.base_project_id&&
       (c.global_worker_slot?
        Number(c.global_worker_slot)===Number(target?.global_worker_slot||0):
        Number(target?.worker_slot||1)===1))));
    for(const command of incoming){
      const action=String(command.action||"");
      if(!["start","force_start","push","new_chat"].includes(action))continue;
      if(command.force===true && String(command.id)!==forceTicket)continue;
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
      if(!ready(actual)){
        resetComposerRepair();
        // An open browser tab is not an authorized dispatch. Report the
        // missing queue/slot contract instead of silently looking healthy.
        if(actual && actual.active===true && actual.desired_state!=="paused" &&
           actual.desired_state!=="draining"){
          const reason=!actual.assignment_ready?"queue-assignment-not-ready":
            !actual.queue_item?.queue_id?"queue-lease-missing":
            !actual.global_worker_slot?"worker-slot-unallocated":
            !String(actual.prompt||"").startsWith("Werk verder aan ")?"prompt-contract-mismatch":
            "worker-target-not-ready";
          await blocked(reason);
        }
        return;
      }
      await status("heartbeat",{reason:"worker-v2-check-30s"});
      // Before any initial dispatch, give the web composer a bounded render
      // grace period. The repair branch is one-shot and fail-closed.
      if(await recoverIdleComposer())return;
      const cid=conversation();
      if(cid && cid!==reportedConversation) {
        reportedConversation=cid;
        sessionStorage.setItem("zcloud-v2-reported-conversation",cid);
        await status("conversation-adopted",{
          reason:"v2-in-place-conversation",targetConversation:cid
        });
      }
      if(generating()){
        observedGenerating=true;
        everGenerating=true;awaiting=false;
        await status("generation-progress");return;
      }
      if(resetRoute!==null){
        if(failedConversation()||!visible(composer())||
           (location.pathname===resetRoute&&/^\/c\//.test(resetRoute))){
          // Only one in-tab UI transition was requested. Escalate a stale
          // transition to its operator, without clicking New chat again.
          await blocked(Date.now()-chatAt>=COMPOSER_IDLE_REPAIR_MS?
            "new-chat-transition-operator-action-required":"new-chat-transition-pending");
          return;
        }
        resetRoute=null;
        chatAt=Date.now();sessionStorage.setItem("zcloud-v2-chat-at",String(chatAt));
      }
      // A terminal conversation-load error is stronger evidence than the
      // pending generation timeout. It must be handled BEFORE the awaiting
      // guard, otherwise a failed page remains stuck forever.
      if(failedConversation()){
        // A failed old /c/ page may hide a prompt already accepted before
        // this userscript loaded. Never click New chat and resend that same
        // queue item merely because the browser lost the reply surface.
        // Only an unsent, blank new-chat route can use automatic recovery.
        if(awaiting||everGenerating||promptAt>0||conversation()||
           location.pathname!=="/"){
          await blocked("conversation-load-failed-manual-review");
          return;
        }
        await newChat("could-not-load-conversation-before-send");
        return;
      }
      const result=reply();
      if(awaiting){
        if(everGenerating||assistantCount()>baselineAssistantCount||(result&&result!==baseline)){
          awaiting=false;everGenerating=false;next=true;
          await status("generation-finished");
        }else if(Date.now()-awaitingAt<120000){return}
        else if(Date.now()-awaitingAt<300000){await blocked("generation-unconfirmed-no-duplicate");return}
        else{
          const box=composer(),draft=String(box?.value||box?.innerText||box?.textContent||"").trim();
          if(!visible(box)||(draft&&draft!==String(target.prompt||"").trim())){
            await blocked("stalled-send-preserved-draft");return;
          }
          awaiting=false;everGenerating=false;
          await newChat("generation-never-started-five-minutes");
          return;
        }
      }else if(everGenerating){
        everGenerating=false;next=true;await status("generation-finished");
      }
      if(Date.now()-chatAt>=MAX_AGE){
        if(await newChat("one-hour-conversation"))return;
      }
      await commands();
      if((next||!promptAt)&&!awaiting&&!generating())
        await send(next?"next-prompt-after-response":"initial-prompt");
    }catch(e){await blocked("poll-error:"+String(e.message||e).slice(0,80))}
    finally{inflight=false;markBusy()}
  }
  // Firefox addon wakes this exact slot after Forceer/Start, without
  // waiting for the 30-second background heartbeat cycle. The page still
  // verifies a fresh queue lease and Thinking High before any send.
  markBusy();
  document.documentElement?.addEventListener?.("zcloud-v2-wake", () => {
    if (!inflight) poll();
  });
  // Background tabs should continue within seconds after completion,
  // not wait for the next 30-second heartbeat. This is local DOM observation,
  // not an extra per-worker API polling loop.
  const FINISH_SETTLE_MS = 1800;
  let finishTimer = null, observedGenerating = false;
  function scheduleFinishedResponseCheck() {
    if (finishTimer !== null) clearTimeout(finishTimer);
    finishTimer = setTimeout(() => {
      finishTimer = null;
      if (generating() || sending || (!awaiting && !everGenerating)) return;
      if (inflight) { scheduleFinishedResponseCheck(); return; }
      // poll() refreshes allocation; send() verifies High and exact lease.
      poll();
    }, FINISH_SETTLE_MS);
  }
  function generationDomChanged() {
    if (!awaiting && !everGenerating) {
      observedGenerating = false;
      return;
    }
    if (generating()) {
      observedGenerating = true;
      if (awaiting) { awaiting = false; everGenerating = true; }
      if (finishTimer !== null) { clearTimeout(finishTimer); finishTimer = null; }
      return;
    }
    if (observedGenerating) {
      observedGenerating = false;
      scheduleFinishedResponseCheck();
    } else if (awaiting &&
        (assistantCount() > baselineAssistantCount ||
         (reply() && reply() !== baseline))) {
      // A very fast response can elude the stop-button observer.
      // Require fresh assistant content and the same settling window.
      scheduleFinishedResponseCheck();
    }
  }
  if (typeof MutationObserver === "function" && document.documentElement) {
    const generationObserver = new MutationObserver(generationDomChanged);
    generationObserver.observe(document.documentElement, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ["data-testid", "aria-label"]
    });
  }
  poll();setInterval(poll,POLL_MS);
})();
