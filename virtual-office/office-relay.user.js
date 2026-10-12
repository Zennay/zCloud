// ==UserScript==
// @name         zCloud Office Relay
// @namespace    https://github.com/Zennay/zCloud
// @version      0.3.0
// @description  Explicitly connect one dedicated ChatGPT conversation to one Office employee.
// @match        https://chatgpt.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @connect      198.244.191.182
// @run-at       document-idle
// ==/UserScript==
(() => {
 'use strict';
 // Opt-in per tab, credentials stay in closure memory. No cookie/token extraction,
 // no account-limit bypass, no background-tab ownership theft and no auto resend.
 let config=null,active=null,busy=false,polling=false,client=crypto.randomUUID();
 const status=document.createElement('div');
 Object.assign(status.style,{position:'fixed',bottom:'12px',left:'12px',zIndex:'99999',background:'#182329',color:'#d9c19b',font:'11px system-ui',border:'1px solid #8d7652',padding:'8px 12px',borderRadius:'8px'});
 status.textContent='Office Relay · uit';document.body.append(status);
 const request=(path,body)=>new Promise((resolve,reject)=>GM_xmlhttpRequest({
  method:'POST',url:config.origin+path,headers:{'Content-Type':'application/json','X-Office-Token':config.token},
  data:JSON.stringify(body),timeout:12000,onload:r=>{try{const d=JSON.parse(r.responseText);r.status>=200&&r.status<300?resolve(d):reject(Error(d.error||'Office niet bereikbaar'))}catch{reject(Error('Ongeldig serverantwoord'))}},
  ontimeout:()=>reject(Error('Office time-out')),onerror:()=>reject(Error('Office niet bereikbaar'))
 }));
 function connect(){
  if(active)return alert('Rond het huidige bericht eerst af.');
  if(!/^\/c\/[0-9a-f-]{16,80}$/i.test(location.pathname))return alert('Open eerst een aparte bestaande ChatGPT-conversatie voor deze medewerker.');
  if(document.documentElement.hasAttribute('data-zcloud-worker-id'))return alert('Deze tab is al een zCloud-worker. Gebruik een aparte gesprekstab.');
  const origin=prompt('Office-adres op de VPS (lokaal: http://127.0.0.1:3422):','http://127.0.0.1:3422');if(!origin)return;
  let u;try{u=new URL(origin)}catch{return alert('Ongeldig Office-adres.')}
  if(!['127.0.0.1','localhost','198.244.191.182'].includes(u.hostname)||u.pathname!=='/'||u.search||u.hash||u.username||u.password||!['http:','https:'].includes(u.protocol))return alert('Gebruik alleen het vaste Office-adres.');
  if(u.protocol==='http:'&&!['127.0.0.1','localhost'].includes(u.hostname))return alert('Gebruik de lokale VPS-browser of een beveiligd HTTPS-adres. De relay verstuurt geen toegangscode via publiek HTTP.');
  const employee=prompt('Medewerker: maya, noah of lina','maya');if(!['maya','noah','lina'].includes(employee))return;
  const token=prompt('Office-toegangscode (alleen in geheugen van deze tab)');if(!token)return;
  config={origin:u.origin,employee,token,conversation:'https://chatgpt.com'+location.pathname};
  status.textContent='Office Relay · verbinden…';tick();
 }
 GM_registerMenuCommand('Verbind deze chat met Office',connect);
 GM_registerMenuCommand('Office Relay stoppen',()=>{if(active)return alert('Wacht tot het lopende antwoord is teruggestuurd.');config=null;status.textContent='Office Relay · uit'});
 status.onclick=()=>{if(!config)connect()};
 const editor=()=>document.querySelector('#prompt-textarea[contenteditable="true"],textarea#prompt-textarea');
 const generating=()=>!!document.querySelector('[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop streaming"]');
 const content=e=>e?.innerText||e?.value||'';
 async function send(job){
  active=job;busy=true;status.textContent='Office Relay · bericht versturen';
  const field=editor(),before=[...document.querySelectorAll('[data-message-author-role="assistant"]')];
  try{
   if(!field||content(field).trim()||generating())throw Error('Chat is niet vrij. Bericht is niet automatisch opnieuw verstuurd.');
   field.focus();
   if(field.tagName==='TEXTAREA'){const set=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;set.call(field,job.instruction);field.dispatchEvent(new Event('input',{bubbles:true}))}
   else {const selection=window.getSelection(),range=document.createRange();range.selectNodeContents(field);selection.removeAllRanges();selection.addRange(range);document.execCommand('insertText',false,job.instruction);field.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:job.instruction}))}
   await new Promise(r=>setTimeout(r,350));
   const button=document.querySelector('[data-testid="send-button"],button[aria-label="Send prompt"],button[aria-label="Send message"]');
   if(!button||button.disabled||content(field).trim()!==job.instruction.trim())throw Error('Verzendknop of invoer niet bevestigd. Controleer je ChatGPT-tab.');
   button.click();status.textContent='Office Relay · ChatGPT antwoordt';
   const started=Date.now();let last='',stable=0;
   while(Date.now()-started<180000){
    await new Promise(r=>setTimeout(r,1000));
    if('https://chatgpt.com'+location.pathname!==config.conversation)throw Error('Chat is gewijzigd. Controleer het antwoord in ChatGPT.');
    const nodes=[...document.querySelectorAll('[data-message-author-role="assistant"]')].filter(n=>!before.includes(n));
    const reply=nodes.at(-1)?.innerText?.trim()||'';
    stable=reply&&reply===last?stable+1:0;last=reply;
    // Require a finished message's visible copy control, not just a pause in streaming.
    const finished=nodes.at(-1)?.closest('article')?.querySelector('button[data-testid="copy-turn-action-button"]');
    if(reply&&!generating()&&stable>=3&&finished){
     const payload={id:job.id,client_id:client,claim_token:job.claim_token,status:'done',reply:reply.slice(0,18000)};
     await request('/api/relay/reply',payload);active=null;return;
    }
   }
   throw Error('Geen voltooid antwoord bevestigd binnen drie minuten. Controleer de ChatGPT-tab; dit bericht wordt niet opnieuw verstuurd.');
  }catch(e){
   await request('/api/relay/reply',{id:job.id,client_id:client,claim_token:job.claim_token,status:'attention',reply:e.message});
   active=null;
  }finally{busy=false;status.textContent=active?'Office Relay · antwoord niet afgeleverd; controleer verbinding':'Office Relay · verbonden'}
 }
 async function tick(){
  if(!config||polling)return;polling=true;
  try{
   if('https://chatgpt.com'+location.pathname!==config.conversation){status.textContent='Office Relay · gepauzeerd: andere chat';return}
   const f=editor();
   const d=await request('/api/relay/poll',{employee:config.employee,client_id:client,conversation:config.conversation,ready:!busy&&!active&&!!f&&!content(f).trim()&&!generating()});
   if(d.job&&!busy)send(d.job).catch(()=>{status.textContent='Office Relay · afleverfout; controleer Office'});
   else if(!busy&&!active)status.textContent='Office Relay · '+config.employee+' verbonden';
  }catch(e){status.textContent='Office Relay · '+e.message}finally{polling=false}
 }
 setInterval(tick,4000);
})();
