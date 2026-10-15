// ==UserScript==
// @name         zCloud Office Relay
// @namespace    https://github.com/Zennay/zCloud
// @version      0.3.8
// @downloadURL  https://raw.githubusercontent.com/Zennay/zCloud/feature/virtual-ai-office-mvp-20261012/virtual-office/office-relay.user.js
// @updateURL    https://raw.githubusercontent.com/Zennay/zCloud/feature/virtual-ai-office-mvp-20261012/virtual-office/office-relay.user.js
// @description  Explicitly connect one dedicated ChatGPT conversation to one Office employee.
// @match        https://chatgpt.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @connect      198.244.191.182
// @run-at       document-idle
// ==/UserScript==
(() => {
 'use strict';
 // Explicit per-browser consent is required before saving an Office code in
 // Tampermonkey for crash recovery. No ChatGPT cookie/token extraction, no
 // account-limit bypass, no worker-tab hijack, no uncertain-message replay.
 let config=null,active=null,busy=false,polling=false,rememberAfterSuccess=false;
 // A NON-SECRET tab identifier survives a ChatGPT reload. By default the
 // Office code remains in memory; users may explicitly opt into profile storage.
 // This lets the same tab reclaim its prior lease after manually reconnecting.
 let client=crypto.randomUUID();
 try{
  const prior=sessionStorage.getItem('zcloud-office-relay-client');
  if(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(prior||''))client=prior;
  else sessionStorage.setItem('zcloud-office-relay-client',client);
 }catch{}

 // Saved pairings are deliberately scoped to EXACT chat URLs. Never bind an
 // employee to a new conversation simply because its tab or title looks similar.
 // Tampermonkey local storage is protected by this browser profile but is not
 // encrypted. It may be used ONLY after the owner explicitly opts in.
 const PERSIST_KEY='zcloud-office-relay-remembered-v1';
 const EMPLOYEE_PROMPTS=Object.freeze({
  maya:'Je bent Maya, projectleider en portfolio-assistent van zCloud. Je helpt prioriteiten, projectvoortgang, risico’s en afhankelijkheden overzichtelijk te houden. Controleer Notion of beschikbare projectbronnen voordat je feiten over voortgang claimt; verzin geen deployments of status. Adviseer concreet, in het Nederlands.',
  noah:'Je bent Noah, senior product-engineer met Supa als specialisme. Denk vanuit functionaliteit, UX, betrouwbare implementatie, regressietests en bestaande code. Beschrijf wat je werkelijk hebt gecontroleerd; zeg niet dat code is aangepast zonder bewijs. Antwoord doelgericht in het Nederlands.',
  lina:'Je bent Lina, kwaliteits- en securityexpert met LightUp als specialisme. Geef reproduceerbare testgevallen, veilige validatie, duidelijke risico-inschattingen en herstelstappen. Actieve securitytests alleen op expliciet geautoriseerde scope; neem geen toestemming aan. Antwoord helder in het Nederlands.'
 });
 const EMPLOYEE_ID=/^[a-z][a-z0-9]{1,15}$/;   // the team is expandable: any expert id the Office server knows
 const CHAT_PATH=new RegExp('^/c/[0-9a-f-]{16,80}$','i');
 const currentChat=()=>CHAT_PATH.test(location.pathname)?'https://chatgpt.com'+location.pathname:'';
 const safeSavedOrigin=origin=>['http://127.0.0.1:3422','http://localhost:3422'].includes(String(origin||''));
 function savedPairings(){
  try{
   const obj=GM_getValue(PERSIST_KEY,{});
   if(!obj||obj.version!==1||typeof obj.chats!=='object'||Array.isArray(obj.chats))return {};
   return {...obj.chats};
  }catch{return {}}
 }
 function savePairing(cfg){
  if(!cfg||!safeSavedOrigin(cfg.origin)||!CHAT_PATH.test(new URL(cfg.conversation).pathname))return false;
  const chats=savedPairings();
  for(const [url,item] of Object.entries(chats)){
   if(item?.employee===cfg.employee)delete chats[url]; // exactly one chat per expert
  }
  chats[cfg.conversation]={origin:cfg.origin,employee:cfg.employee,token:cfg.token};
  try{GM_setValue(PERSIST_KEY,{version:1,chats});return true}catch{return false}
 }
 function clearPairing(url){
  const chats=savedPairings();
  if(!Object.prototype.hasOwnProperty.call(chats,url))return;
  delete chats[url];
  try{if(Object.keys(chats).length)GM_setValue(PERSIST_KEY,{version:1,chats});
      else GM_deleteValue(PERSIST_KEY)}catch{}
 }
 function validSavedPairing(url,item){
  return !!url&&!!item&&safeSavedOrigin(item.origin)&&
    EMPLOYEE_ID.test(item.employee||'')&&
    typeof item.token==='string'&&item.token.length>=16&&item.token.length<=512;
 }
 const personaFor=job=>(typeof job.persona==='string'&&job.persona.trim()?job.persona.trim().slice(0,1800):EMPLOYEE_PROMPTS[config.employee]||('Je bent '+config.employee+', een expert in het team van de eigenaar. Geef een onderbouwd oordeel en verzin geen resultaten.'));
 const contextualInstruction=job=>personaFor(job)+
   '\\n\\nBericht van de eigenaar aan '+config.employee+':\\n'+String(job.instruction||'')+
   '\\n\\nAntwoord alsof je met de eigenaar spreekt, gebruik concrete bewijzen en claim geen uitgevoerde acties zonder verificatie.';

 const status=document.createElement('div');
 Object.assign(status.style,{position:'fixed',bottom:'12px',left:'12px',zIndex:'99999',background:'#182329',color:'#d9c19b',font:'11px system-ui',border:'1px solid #8d7652',padding:'8px 12px',borderRadius:'8px'});
 status.textContent='Office Relay · uit';status.title='Klik om Office Relay te verbinden of opnieuw in te stellen';status.setAttribute('role','status');document.body.append(status);
 // Probe the expected Office server BEFORE requesting an access code. A localhost
 // endpoint can belong to an unrelated app if the SSH tunnel is not running.
 const checkOffice=origin=>new Promise((resolve,reject)=>GM_xmlhttpRequest({
  method:'GET',url:origin+'/api/health',timeout:6500,
  onload:r=>{
   if(r.status!==200)return reject(Error('Office reageert met HTTP '+r.status+'. Controleer je PC→VPS SSH-tunnel.'));
   let info;try{info=JSON.parse(r.responseText)}catch{return reject(Error('Lokaal poort 3422 is niet de Office-server. Controleer je SSH-tunnel.'))}
   if(info?.mode!=='zcloud_chatgpt_chats'||info.online!==true)return reject(Error('Poort 3422 toont niet de zCloud Office-server. Controleer je SSH-tunnel.'));
   resolve();
  },
  onerror:()=>reject(Error('Office-server niet bereikbaar. Op VPS: controleer Office-service. Op Mac: start de SSH-tunnel.')),
  ontimeout:()=>reject(Error('Office time-out. Controleer de SSH-tunnel op je pc.'))
 }));
 const request=(path,body)=>new Promise((resolve,reject)=>GM_xmlhttpRequest({
  method:'POST',url:config.origin+path,headers:{'Content-Type':'application/json','X-Office-Token':config.token},
  data:JSON.stringify(body),timeout:12000,onload:r=>{let d;try{d=JSON.parse(r.responseText)}catch{return reject(Error('Office antwoordt niet met JSON (HTTP '+r.status+'). Controleer de server-URL.'))}if(r.status===401)return reject(Error('Toegangscode geweigerd (401). Verbind opnieuw met dezelfde Office-code.'));r.status>=200&&r.status<300?resolve(d):reject(Error(d.error||'Office HTTP '+r.status))},
  ontimeout:()=>reject(Error('Office time-out')),onerror:()=>reject(Error('Office niet bereikbaar'))
 }));
 async function connect(){
  if(active)return alert('Rond het huidige bericht eerst af.');
  if(!/^\/c\/[0-9a-f-]{16,80}$/i.test(location.pathname))return alert('Open eerst een aparte bestaande ChatGPT-conversatie voor deze medewerker.');
  if(document.documentElement.hasAttribute('data-zcloud-worker-id'))return alert('Deze tab is al een zCloud-worker. Gebruik een aparte gesprekstab.');
  const origin=prompt('Office bereikbaar vanuit DEZE browser (op VPS: localhost; op Mac: SSH-tunnel):','http://127.0.0.1:3422');if(!origin)return;
  let u;try{u=new URL(origin)}catch{return alert('Ongeldig Office-adres.')}
  if(!['127.0.0.1','localhost','198.244.191.182'].includes(u.hostname)||u.pathname!=='/'||u.search||u.hash||u.username||u.password||!['http:','https:'].includes(u.protocol))return alert('Gebruik alleen het vaste Office-adres.');
  if(u.protocol==='http:'&&!['127.0.0.1','localhost'].includes(u.hostname))return alert('VPS via publiek HTTP is onveilig. Start op je eigen pc: ssh -N -L 127.0.0.1:3422:127.0.0.1:3422 <ssh-gebruiker>@198.244.191.182 en gebruik http://127.0.0.1:3422, of gebruik een vertrouwde HTTPS-proxy.');
  status.textContent='Office Relay · Office-server controleren…';
  try{await checkOffice(u.origin)}catch(e){status.textContent='Office Relay · '+e.message;alert(e.message);return}
  const employee=(prompt('Medewerker-id (bijv. maya, noah, lina of de id van een nieuwe expert uit het Beheer-paneel)','maya')||'').trim().toLowerCase();if(!EMPLOYEE_ID.test(employee))return;
  const token=prompt('Office-toegangscode (alleen in geheugen van deze tab, behalve bij expliciet onthouden)');if(!token)return;
  const remember=confirm('AUTOMATISCH HERSTELLEN NA VPS FIREFOX-CRASH?\\n\\nJa = sla de Office-code lokaal en ONVERSLEUTELD op in Tampermonkey van dit vertrouwde Firefox-profiel. Bij browserherstart wordt uitsluitend DEZE ChatGPT-conversatie opnieuw gekoppeld.\\n\\nNee = alleen tijdelijk verbinden; code na herstart opnieuw invullen.\\n\\nGebruik Ja niet in een gedeeld of onveilig browserprofiel.');
  config={origin:u.origin,employee,token,conversation:'https://chatgpt.com'+location.pathname};
  rememberAfterSuccess=remember; // persist only after an authenticated relay poll succeeds
  if(!remember)clearPairing(config.conversation);
  status.textContent='Office Relay · verbinden…';tick();
 }
 GM_registerMenuCommand('Verbind deze chat met Office',connect);
 GM_registerMenuCommand('Office Relay stoppen + onthouden koppeling wissen',()=>{
  if(active)return alert('Wacht tot het lopende antwoord is teruggestuurd.');
  clearPairing(config?.conversation||currentChat());config=null;rememberAfterSuccess=false;status.textContent='Office Relay · uit';
 });
 GM_registerMenuCommand('Office Relay opnieuw instellen',()=>{if(active)return alert('Wacht tot het lopende antwoord is teruggestuurd.');config=null;connect()});
 GM_registerMenuCommand('Koppel nieuwe chat aan opgeslagen medewerker',()=>{
  if(active)return alert('Wacht tot het lopende antwoord is teruggestuurd.');
  if(document.documentElement.hasAttribute('data-zcloud-worker-id'))return alert('Een zCloud-worker-tab mag niet aan een Office-medewerker worden gekoppeld.');
  const conversation=currentChat();
  if(!conversation)return alert('Open eerst een bestaande opgeslagen nieuwe ChatGPT-conversatie.');
  const previous=Object.values(savedPairings()).filter(item=>validSavedPairing(conversation,item));
  if(!previous.length)return alert('Geen onthouden medewerker. Verbind eerst één keer via Office Relay en kies expliciet Onthouden.');
  const employee=prompt('Koppel deze nieuwe chat aan welke medewerker-id?',previous[0].employee);
  const pair=previous.find(item=>item.employee===employee);
  if(!pair)return alert('Geen opgeslagen code voor deze medewerker gevonden.');
  if(!confirm('Koppel deze chat aan '+employee+'? Dit vervangt na geslaagde verificatie de oude onthouden chat voor deze medewerker.'))return;
  config={origin:pair.origin,employee,token:pair.token,conversation};
  rememberAfterSuccess=true;
  status.textContent='Office Relay · '+employee+' nieuwe chat koppelen…';
  tick();
 });
 status.onclick=()=>{if(!config)return connect();if(active)return alert('Er wordt nog een antwoord verwerkt.');if(confirm('Relay voor '+config.employee+' op '+config.origin+' opnieuw verbinden? Je kunt de vorige opgeslagen koppeling hierbij vervangen.')){clearPairing(config.conversation);config=null;rememberAfterSuccess=false;connect()}};
 const editor=()=>document.querySelector('#prompt-textarea[contenteditable="true"],textarea#prompt-textarea');
 const generating=()=>!!document.querySelector('[data-testid="stop-button"],button[aria-label="Stop generating"],button[aria-label="Stop streaming"]');
 const content=e=>e?.innerText||e?.value||'';
 async function send(job){
  active=job;busy=true;status.textContent='Office Relay · bericht versturen';
  const field=editor(),before=[...document.querySelectorAll('[data-message-author-role="assistant"]')];
  const instruction=contextualInstruction(job);
  try{
   if(!field||content(field).trim()||generating())throw Error('Chat is niet vrij. Bericht is niet automatisch opnieuw verstuurd.');
   field.focus();
   if(field.tagName==='TEXTAREA'){const set=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;set.call(field,instruction);field.dispatchEvent(new Event('input',{bubbles:true}))}
   else {const selection=window.getSelection(),range=document.createRange();range.selectNodeContents(field);selection.removeAllRanges();selection.addRange(range);document.execCommand('insertText',false,instruction);field.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:instruction}))}
   await new Promise(r=>setTimeout(r,350));
   const button=document.querySelector('[data-testid="send-button"],button[aria-label="Send prompt"],button[aria-label="Send message"]');
   if(!button||button.disabled||content(field).trim()!==instruction.trim())throw Error('Verzendknop of invoer niet bevestigd. Controleer je ChatGPT-tab.');
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
  }finally{busy=false;status.textContent=active?'Office Relay · antwoord niet afgeleverd; controleer verbinding':'Office Relay · '+config.employee+' verbonden'}
 }
 async function tick(){
  // Keep staff relay completely separate from dynamically allocated workers.
  if(document.documentElement.hasAttribute('data-zcloud-worker-id')){
   config=null;rememberAfterSuccess=false;status.style.display='none';return;
  }
  if(!config||polling)return;polling=true;
  try{
   if('https://chatgpt.com'+location.pathname!==config.conversation){status.textContent='Office Relay · gepauzeerd: andere chat';return}
   const f=editor();
   const d=await request('/api/relay/poll',{employee:config.employee,client_id:client,conversation:config.conversation,ready:!busy&&!active&&!!f&&!content(f).trim()&&!generating()});
   if(rememberAfterSuccess){
    rememberAfterSuccess=false;
    if(!savePairing(config))status.title='Verbonden, maar kon opt-in koppeling niet opslaan';
   }
   if(d.job&&!busy)send(d.job).catch(()=>{status.textContent='Office Relay · afleverfout; controleer Office'});
   else if(!busy&&!active)status.textContent='Office Relay · '+config.employee+' verbonden';
  }catch(e){status.textContent='Office Relay · '+e.message}finally{polling=false}
 }
 // A browser may throttle a background tab's interval to once per minute.
 // The server tolerates throttled heartbeats; on returning to the tab probe now.
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)tick()});
 window.addEventListener('pageshow',()=>{if(config)tick()});
 let resuming=false;
 async function resumeSavedPairing(){
  if(resuming||config||active||document.documentElement.hasAttribute('data-zcloud-worker-id'))return;
  const conversation=currentChat();
  const pair=savedPairings()[conversation];
  if(!validSavedPairing(conversation,pair))return;
  resuming=true;
  status.textContent='Office Relay · '+pair.employee+' VPS-verbinding verifiëren…';
  try{
   await checkOffice(pair.origin); // never send a stored token to a wrong loopback service
   if(config||document.documentElement.hasAttribute('data-zcloud-worker-id')||currentChat()!==conversation)return;
   config={origin:pair.origin,employee:pair.employee,token:pair.token,conversation};
   status.textContent='Office Relay · '+pair.employee+' automatisch herstellen…';
   tick();
  }catch(e){
   status.textContent='Office Relay · herstellen wachten: '+e.message;
   setTimeout(resumeSavedPairing,10000);
  }finally{resuming=false}
 }
 // A restored ChatGPT tab is resumed without a new prompt or stored claim;
 // only a valid, exact-URL pairing can poll. Browser tab ownership remains
 // enforced by Office's client-id lease, never bypassed on crash/restart.
 setTimeout(resumeSavedPairing,1200);
 setInterval(tick,4000);
})();
