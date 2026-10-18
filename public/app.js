'use strict';
const ICONS={grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',cloud:'<path d="M7 18a5 5 0 1 1 .8-9.9A7 7 0 0 1 21 11a3.5 3.5 0 0 1-1.5 7Z"/>',game:'<path d="M7 7h10c3 0 4 4 4 9 0 3-3 4-5 0H8c-2 4-5 3-5 0 0-5 1-9 4-9Z"/><path d="M7 10v4m-2-2h4m7-1h.01m2 3h.01"/>',chart:'<path d="M4 4v16h16M7 14l4-4 4 2 5-7"/>',pulse:'<path d="M2 12h4l3-8 5 16 3-8h5"/>',server:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M15 6.5h3m-3 11h3"/>',refresh:'<path d="M20 8A8 8 0 0 0 6 5L3 8m0-5v5h5m-4 8a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',watch:'<rect x="5" y="6" width="14" height="12" rx="4"/><path d="M8 6l1-4h6l1 4M8 18l1 4h6l1-4m-4-9v3l2 1"/>',arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',back:'<path d="M19 12H5m5-5-5 5 5 5"/>',calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18"/>',check:'<path d="m5 12 4 4L19 6"/>',commit:'<circle cx="12" cy="12" r="4"/><path d="M3 12h5m8 0h5"/>',target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',cpu:'<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3"/>',clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',layers:'<path d="m12 3 10 5-10 5L2 8Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',disk:'<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 14h18m-5 3h2"/>',play:'<path d="M8 5v14l11-7Z"/>',stop:'<rect x="7" y="7" width="10" height="10" rx="1.5"/>'};
const icon=n=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[n]||ICONS.layers}</svg>`;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $=id=>document.getElementById(id),num=n=>new Intl.NumberFormat('en-GB',{maximumFractionDigits:1}).format(n),gb=n=>num(n/1073741824)+' GB';
const date=(x,full=false)=>new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Amsterdam',day:'numeric',month:'short',...(full?{year:'numeric',hour:'2-digit',minute:'2-digit'}:{})}).format(new Date(x));
const clock=x=>new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Amsterdam',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date(x));
const rel=x=>{const m=Math.floor((Date.now()-Date.parse(x))/60000);return m<1?'zojuist':m<60?m+' min geleden':m<1440?Math.floor(m/60)+' u geleden':date(x)};
const STATUS_TIMEOUT_MS=30000,STATUS_CACHE_KEY='zcloud:last-status:v1',STATUS_CACHE_MAX_AGE_MS=24*60*60*1000;
let DATA=null,ACTIVITY=[],HOST=[],HISTORY={},WORKER_SCALING=null,WORKER_SCALING_ERROR='',route='',range='7d',filter='',busy=false,routeVersion=0,dynamicWorkerSaveTimer=null,scrollToWorkersOnOverview=false,mobileWorkersActive=false;
const ACTIVITY_CACHE_INTERVAL_MS=120000,ACTIVITY_MAX_PROJECTS=24,ACTIVITY_MAX_VISIBLE=240;
let ACTIVITY_COVERAGE={kind:'local-only',lastSuccess:0,projectsTotal:0,projectsRead:0,failed:0,limited:false,pending:false,scope:'',lastError:''};
let ACTIVITY_PROJECT_FETCH=null;
let ACTIVITY_PROJECT_FETCH_TIME=0;
function activityCoverageText(){
  const c=ACTIVITY_COVERAGE;
  const prefix='Local VPS events only · GitHub PRs, branches and CI are not synchronized.';
  if(c.pending)return prefix+' Reading project histories…';
  if(!c.lastSuccess)return prefix+' Project coverage has not been checked.';
  const partial=c.failed||c.projectsRead<c.projectsTotal;
  const status=partial?'Partial coverage — some project histories are unavailable or not included.':'Project history read completed.';
  const limit=c.limited?' Some projects have reached the last-80-events API limit.':'';
  return prefix+' '+status+' '+c.projectsRead+'/'+c.projectsTotal+' project sources checked · last read '+date(c.lastSuccess,true)+'.'+limit;
}
/**
 * The existing read-only API accepts ?project=<allowlisted id>, but its global
 * 80-row cap can hide every quiet project behind one noisy checkout. Read each
 * visible project at most once per two minutes, using bounded parallelism, and
 * round-robin their local events without treating a successful /api/status as
 * evidence that remote GitHub PRs or CI have been synchronized.
 */
async function refreshProjectActivity(version,force=false){
  if(!DATA||route!=='activity')return;
  const visible=(DATA.projects||[]).filter(p=>p&&typeof p.id==='string'
      &&/^[a-z][a-z0-9_-]{0,55}$/.test(p.id));
  const scoped=filter?visible.filter(p=>p.id===filter):visible;
  const ids=scoped.slice(0,ACTIVITY_MAX_PROJECTS).map(p=>p.id);
  const omitted=Math.max(0,scoped.length-ids.length);
  const scope=ids.join('|');
  if(!ids.length){
    ACTIVITY_COVERAGE={kind:'local-only',lastSuccess:0,projectsTotal:scoped.length,projectsRead:0,failed:0,limited:false,pending:false,scope,lastError:'no-visible-projects'};
    render();return;
  }
  if(ACTIVITY_PROJECT_FETCH){
    await ACTIVITY_PROJECT_FETCH;
    if(version!==routeVersion||route!=='activity')return;
    // New filter/scope while a previous request was pending: start the new
    // scope rather than leaving an indefinite "Reading…" indication.
    return refreshProjectActivity(version,force);
  }
  if(!force&&ACTIVITY_COVERAGE.scope===scope
    &&Date.now()-ACTIVITY_PROJECT_FETCH_TIME<ACTIVITY_CACHE_INTERVAL_MS)return;
  ACTIVITY_COVERAGE={...ACTIVITY_COVERAGE,pending:true,scope,lastError:''};
  render();
  const work=(async()=>{
    const streams=new Map(),errors=[];
    for(let start=0;start<ids.length;start+=4){
      const batch=ids.slice(start,start+4);
      const results=await Promise.allSettled(batch.map(id=>api('/api/activity?project='+encodeURIComponent(id),8000)));
      if(version!==routeVersion||route!=='activity')return;
      results.forEach((r,index)=>{
        const id=batch[index];
        if(r.status!=='fulfilled'||!Array.isArray(r.value)){errors.push(id);return;}
        // Fail closed if an endpoint returns cross-project or malformed data.
        const rows=r.value;
        if(rows.length>80||rows.some(a=>!a||a.project!==id
            ||typeof a.id!=='string'||a.id.length>256
            ||typeof a.ts!=='string'||!Number.isFinite(Date.parse(a.ts)))){
          errors.push(id);return;
        }
        streams.set(id,rows);
      });
    }
    if(version!==routeVersion||route!=='activity'||filter!==((ids.length===1&&scope===filter)?filter:''))return;
    const merged=[],seen=new Set(),indices=new Map(ids.map(id=>[id,0]));
    while(merged.length<ACTIVITY_MAX_VISIBLE){
      let advanced=false;
      for(const id of ids){
        const items=streams.get(id)||[];
        let i=indices.get(id);
        if(i>=items.length)continue;
        const item=items[i];indices.set(id,i+1);
        if(!seen.has(item.id)){merged.push(item);seen.add(item.id);}
        advanced=true;
        if(merged.length>=ACTIVITY_MAX_VISIBLE)break;
      }
      if(!advanced)break;
    }
    // A partial API outage must not silently delete the last-known local feed.
    // Successful reads are merged with the previous global result when needed.
    if(errors.length){
      for(const item of ACTIVITY){
        if(merged.length>=ACTIVITY_MAX_VISIBLE)break;
        if(item&&typeof item.id==='string'&&!seen.has(item.id)){
          merged.push(item);seen.add(item.id);
        }
      }
    }
    if(streams.size)ACTIVITY=merged;
    ACTIVITY_PROJECT_FETCH_TIME=Date.now();
    ACTIVITY_COVERAGE={kind:'local-only',lastSuccess:streams.size?ACTIVITY_PROJECT_FETCH_TIME:ACTIVITY_COVERAGE.lastSuccess,
      projectsTotal:scoped.length,projectsRead:streams.size,failed:errors.length+omitted,
      limited:[...streams.values()].some(rows=>rows.length===80),
      pending:false,scope,lastError:errors.length?'partial-local-reads':''};
    render();
  })();
  ACTIVITY_PROJECT_FETCH=work;
  try{await work;}finally{
    if(ACTIVITY_PROJECT_FETCH===work)ACTIVITY_PROJECT_FETCH=null;
    if(ACTIVITY_COVERAGE.pending&&version===routeVersion){
      ACTIVITY_COVERAGE={...ACTIVITY_COVERAGE,pending:false,lastError:'interrupted'};
      render();
    }
  }
}

function loadCachedStatus(){try{const cached=JSON.parse(localStorage.getItem(STATUS_CACHE_KEY)||'null');if(!cached||!cached.data||!cached.saved_at)return null;if(Date.now()-Number(cached.saved_at)>STATUS_CACHE_MAX_AGE_MS)return null;return cached.data}catch{return null}}
function saveCachedStatus(data){try{localStorage.setItem(STATUS_CACHE_KEY,JSON.stringify({saved_at:Date.now(),data}))}catch{}}
function hydrate(){document.querySelectorAll('[data-icon]').forEach(el=>{el.innerHTML=icon(el.dataset.icon);el.removeAttribute('data-icon')})}
async function api(path,timeoutMs=10000){const r=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(timeoutMs)});if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}
async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}
async function saveProjectLayout(order,archived){
  if(projectLayoutActionPending)return false;
  projectLayoutActionPending=true;
  let saved=false;
  try{
    await post('/api/project-layout',{order,archived});
    saved=true;
    if(!await refresh(true))throw new Error('Saved project order, status refresh failed');
    projectLayoutNotice('Project order saved.');
    return true;
  }catch(_error){
    projectLayoutNotice(saved
      ?'Project order saved, but dashboard refresh failed. Reload to see the current order.'
      :'Project order could not be confirmed. Check the project list before trying again.');
    return false;
  }finally{
    projectLayoutActionPending=false;
  }
}
let projectLayoutActionPending=false;
function projectLayoutNotice(message){
  const notice=$('notice');
  if(notice){notice.hidden=false;notice.textContent=message}
}
async function changeProjectLayout(control,operation,order,archived){
  if(projectLayoutActionPending)return;
  projectLayoutActionPending=true;
  const label=control.querySelector('b')||control;
  const previousLabel=label.textContent;
  control.disabled=true;
  control.setAttribute('aria-busy','true');
  label.textContent=operation==='archive'?'Archiving…':'Restoring…';
  let backendConfirmed=false;
  try{
    await post('/api/project-layout',{order,archived});
    backendConfirmed=true;
    if(!await refresh(true))throw new Error('Saved layout, status refresh failed');
    projectLayoutNotice(operation==='archive'?'Project archived successfully.':'Project restored successfully.');
  }catch(_error){
    projectLayoutNotice(backendConfirmed
      ?'Your change was saved, but the dashboard could not refresh. Reload to view the latest projects.'
      :'The project change could not be confirmed. Check the project list before trying again.');
  }finally{
    label.textContent=previousLabel;
    control.removeAttribute('aria-busy');
    control.disabled=false;
    projectLayoutActionPending=false;
  }
}
function syncNav(){document.querySelectorAll('[data-nav]').forEach(a=>a.classList.toggle('active',a.dataset.nav===route&&!(route==='overview'&&mobileWorkersActive)));document.querySelectorAll('[data-jump-workers]').forEach(a=>a.classList.toggle('active',route==='overview'&&mobileWorkersActive));$('projectCount').textContent=DATA.projects.length;const links=DATA.projects.map(p=>`<a href="#project/${esc(p.id)}" class="${route==='project/'+p.id?'active':''}"><span class="project-dot" style="--accent:${p.accent}"></span>${esc(p.name)}</a>`).join('');$('projectNav').innerHTML=links;const mobileProjects=$('mobileProjectNav');if(mobileProjects){mobileProjects.classList.toggle('is-overview',route==='overview');mobileProjects.innerHTML=`<a href="#overview" data-nav="overview" class="mobile-overview-link ${route==='overview'?'active':''}">${icon('grid')}<span>Overview</span></a>`+links}}
function stat(label,value,note,ico,emphasis=false){return `<div class="stat"><div class="stat-label">${label}${icon(ico)}</div><div class="stat-value">${value}</div><div class="stat-note ${emphasis?'emphasis':''}">${note}</div></div>`}
function heading(eyebrow,title,sub,right=''){return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${sub}</p></div>${right}</div>`}
function badge(p){return `<span class="badge ${p.health==='healthy'?'':'warn'}">${p.health==='healthy'?'Operational':'Needs attention'}</span>`}
function runnerToggle(p,placement='card'){
  const r=(DATA?.chatgpt_runners||{})[p.id]||{active:false,state:'paused'};
  const active=!!r.active,pending=['pending','dispatched'].includes(r.command?.status);
  const action=active?'pause':'start';
  const title=pending?(active?'ChatGPT automation is stopping':'ChatGPT automation is starting'):(active?'Stop automation for '+p.name:'Start automation for '+p.name);
  return `<button type="button" draggable="false" class="runner-toggle ${active?'is-active':'is-idle'} ${placement==='detail'?'runner-toggle-detail':''}" data-runner-toggle="${esc(p.id)}" data-runner-action="${action}" aria-label="${esc(title)}" title="${esc(title)}" ${pending?'disabled':''}>${icon(active?'stop':'play')}<span class="runner-toggle-label">${pending?'…':active?'Stop':'Start'}</span></button>`;
}
function runnerEventLabel(event){
  const labels={
    'generation-started':'Response started',
    'generation-finished':'Response completed',
    'prompt-sent':'Prompt sent',
    'runner-started':'Worker started',
    'target-tab-recovered':'Chat recovered',
    'conversation-adopted':'Conversation linked',
    'worker-paused':'Worker paused',
    'worker-drained':'Worker completed'
  };
  return labels[String(event||'')]||'Status updated';
}
function runnerLastAction(r){
  const candidates=[],add=(time,label)=>{if(time&&Date.parse(time))candidates.push({time,label})};
  add(r?.last_generation_finished?.time,'Response completed');
  add(r?.last_prompt_sent?.time,'Prompt sent');
  if(r?.command?.updated_at&&r?.command?.result)add(r.command.updated_at,String(r.command.result));
  const projectEvent=r?.last_event||{};
  if(projectEvent.time&&projectEvent.event&&projectEvent.event!=='heartbeat')add(projectEvent.time,runnerEventLabel(projectEvent.event));
  (r?.workers||[]).forEach(w=>{
    const event=w?.last_event||{};
    if(event.time&&event.event&&event.event!=='heartbeat')add(event.time,runnerEventLabel(event.event));
  });
  candidates.sort((a,b)=>Date.parse(b.time)-Date.parse(a.time));
  return candidates[0]||null;
}
function scalingForProject(projectId){
  const found=(WORKER_SCALING?.projects||[]).find(x=>x.project_id===projectId);
  if(found)return found;
  const r=(DATA?.chatgpt_runners||{})[projectId]||{};
  const desired=Math.max(1,Number(r.desired_worker_count||r.worker_count||1));
  return desired>1?{project_id:projectId,desired_workers:desired,assessment:{state:'insufficient_data',reason:'Multi-worker allocation is active, but there is not enough reliable telemetry yet.'}}:null;
}
function scalingStateCopy(state){
  return {
    useful_scaling:['Extra workers help','good'],
    diminishing_returns:['Extra workers add little','warn'],
    inconclusive:['Scaling signal mixed','neutral'],
    insufficient_data:['Still measuring scaling','neutral'],
    single_worker:['Single worker','neutral']
  }[state]||['Scaling unknown','neutral'];
}
function scalingChip(projectId){
  const item=scalingForProject(projectId);
  if(!item||Number(item.desired_workers||1)<=1)return '';
  const state=String(item.assessment?.state||'insufficient_data'),copy=scalingStateCopy(state);
  return '<div class="project-scaling-chip '+copy[1]+'" title="'+esc(item.assessment?.reason||'Observational worker telemetry')+'"><small>Scaling</small><strong>'+esc(copy[0])+'</strong></div>';
}
function projectCardOps(p){
  const r=(DATA?.chatgpt_runners||{})[p.id];
  if(!r)return '';
  const workers=Array.isArray(r.workers)?r.workers:[];
  const activeWorkers=workers.filter(w=>w.active!==false&&w.desired_state!=='paused');
  const tasks=[...new Set(workers.map(w=>String(w.current_task||'').trim()).filter(Boolean))];
  let nowText='';
  if(tasks.length)nowText=tasks.slice(0,2).join(' · ')+(tasks.length>2?' · +'+(tasks.length-2):'');
  else if(r.generating)nowText='AI working now · no task claimed yet';
  else if(r.active||activeWorkers.length)nowText='Active · no task claimed yet';
  else nowText='No active task';
  const action=runnerLastAction(r);
  const actionText=action?action.label+' · '+rel(action.time):'No action recorded yet';
  const workerError=workers.map(w=>w.error).find(Boolean),error=String(r.error||workerError||'').trim();
  const problem=error?'<div class="project-card-op project-card-problem"><small>Problem</small><span>'+esc(error.length>140?error.slice(0,137)+'…':error)+'</span></div>':'';
  return scalingChip(p.id)+'<div class="project-card-ops"><div class="project-card-op"><small>Now</small><strong>'+esc(nowText)+'</strong></div><div class="project-card-op"><small>Last action</small><span>'+esc(actionText)+'</span></div>'+problem+'</div>';
}
function runnerControls(p){
  const r=(DATA?.chatgpt_runners||{})[p.id]||{active:false,state:'paused'};
  const active=!!r.active,pending=['pending','dispatched'].includes(r.command?.status);
  const hasWorkerDetail=Array.isArray(r.workers),activeWorkers=Number(r.active_worker_count||0),desiredWorkers=Math.max(1,Number(r.desired_worker_count||r.worker_count||1));
  const workerWord=desiredWorkers===1?'worker':'workers';
  const countLine=hasWorkerDetail?`<small>${activeWorkers} of ${desiredWorkers} ${workerWord} active</small>`:'';
  const label=active?(r.generating?'AI working now':r.state==='live'?'AI active':'AI starting'):'AI paused';
  const buttons=active
    ? `<button type="button" class="runner-action" data-runner-push="${esc(p.id)}" ${pending?'disabled':''}>Push now</button>`
    : '';
  return `<div class="project-runner-controls"><span class="project-runner-state ${active?'active':''}"><i></i><span>${esc(label)}${countLine}</span></span><div class="project-runner-buttons">${buttons}</div></div>`;
}
function mobileRunnerControls(p,placement='card'){
  const r=(DATA?.chatgpt_runners||{})[p.id]||{active:false,state:'paused'};
  const desiredWorkers=Math.max(1,Number(r.desired_worker_count||r.worker_count||1));
  const activeWorkers=Number(r.active_worker_count||0);
  const state=r.active?(r.generating?'Working now':'Automation active'):'Automation paused';
  return `<div class="mobile-runner-controls mobile-runner-controls-${esc(placement)} worker-count-only" data-mobile-runner-project="${esc(p.id)}"><div class="mobile-runner-copy"><small>Workers</small><strong>${esc(state)}</strong><span>${activeWorkers} active · ${desiredWorkers} desired</span></div><label class="mobile-runner-workers"><span>Set workers</span><input type="number" inputmode="numeric" min="1" max="8" step="1" value="${desiredWorkers}" data-runner-workers="${esc(p.id)}" aria-label="Number of ChatGPT workers for ${esc(p.name)}"></label></div>`;
}

function projectPrimaryActions(p,placement='card'){
  const r=(DATA?.chatgpt_runners||{})[p.id]||{active:false,state:'paused'};
  const active=!!r.active,pending=['pending','dispatched'].includes(r.command?.status);
  const state=active?(r.generating?'Working now':'Ready for the next step'):'Paused';
  let primary='';
  if(!active){
    primary=`<button type="button" class="project-primary-button" data-runner-toggle="${esc(p.id)}" data-runner-action="start" ${pending?'disabled':''}>${pending?'Starting…':'Start work'}</button>`;
  }else if(r.generating){
    primary='<button type="button" class="project-primary-button is-working" disabled>Working…</button>';
  }else{
    primary=`<button type="button" class="project-primary-button" data-runner-push="${esc(p.id)}" ${pending?'disabled':''}>${pending?'Working…':'Continue work'}</button>`;
  }
  const force=`<button type="button" class="project-secondary-button" data-runner-force-start="${esc(p.id)}" title="Force a fresh start for the allocated workers">Force start</button>`;
  const pause=active?`<button type="button" class="project-secondary-button" data-runner-toggle="${esc(p.id)}" data-runner-action="pause" ${pending?'disabled':''}>Pause</button>`:'';
  return `<div class="project-primary-actions project-primary-actions-${esc(placement)}"><div class="project-action-copy"><small>${placement==='detail'?'PROJECT CONTROL':'WORK'}</small><span><i class="${active?'is-active':''}"></i>${esc(state)}</span></div><div class="project-action-buttons">${primary}${force}${pause}</div></div>`;
}
function projectCard(p){const draggable=!(window.matchMedia&&window.matchMedia('(pointer: coarse)').matches);return `<article class="project-card" style="--accent:${p.accent}" draggable="${draggable}" data-project-id="${esc(p.id)}"><div class="project-card-top"><span class="project-icon">${icon(p.icon)}</span><div class="project-card-actions"><span class="drag-handle" title="Drag to move" aria-hidden="true">⋮⋮</span><button class="archive-button" data-archive="${esc(p.id)}" type="button">Archive</button></div></div><a class="project-card-link" href="#project/${esc(p.id)}" aria-label="View ${esc(p.name)}: ${num(p.progress)} percent of project steps"><div class="card-badge-row">${badge(p)}</div><div class="eyebrow">${p.eyebrow}</div><h3>${p.name}</h3><p>${p.goal}</p><div class="progress-row"><span>${p.completed} / ${p.milestones.length} project steps</span><strong>${num(p.progress)}<small>%</small></strong></div><div class="progress-track"><span style="width:${p.progress}%"></span></div><div class="project-bottom">${icon('target')}<span>${p.phase?'Phase: '+esc(p.phase):'Next: '+esc(p.next)}</span><span class="arrow">${icon('arrow')}</span></div></a></article>`}
function attentionPanel(projectId=''){
  const all=Array.isArray(DATA?.attention_needed)?DATA.attention_needed:[];
  const items=projectId?all.filter(item=>item.project_id===projectId):all;
  const notion=DATA?.portfolio_queue_health?.notion_attention_url||'';
  if(!items.length){
    if(projectId)return '';
    return `<section class="attention-panel attention-clear"><div class="attention-head"><div><span class="eyebrow">ATTENTION NEEDED</span><h2>Nothing needs you right now</h2></div><span class="attention-count">0</span></div><p>Workers can keep moving without a human gate.</p>${notion?`<a class="text-link" href="${esc(notion)}" target="_blank" rel="noreferrer">Open Notion list →</a>`:''}</section>`;
  }
  const rows=items.map(item=>{
    const project=DATA.projects.find(p=>p.id===item.project_id);
    const detail=String(item.detail||'').trim();
    return `<article class="attention-item ${item.severity==='urgent'?'is-urgent':''}"><div class="attention-copy"><small>${esc(project?.name||item.project_id)}</small><strong>${esc(item.action)}</strong>${detail?`<p>${esc(detail.length>220?detail.slice(0,217)+'…':detail)}</p>`:''}<div class="attention-links">${item.source_url?`<a href="${esc(item.source_url)}" target="_blank" rel="noreferrer">Open source</a>`:''}</div></div><button type="button" data-attention-resolve="${esc(item.attention_id)}">Done</button></article>`;
  }).join('');
  return `<section class="attention-panel"><div class="attention-head"><div><span class="eyebrow">ATTENTION NEEDED</span><h2>${items.length===1?'1 thing needs you':items.length+' things need you'}</h2><p>Only human, physical or external gates appear here. They never occupy a worker slot.</p></div><span class="attention-count">${items.length}</span></div><div class="attention-list">${rows}</div>${notion&&!projectId?`<a class="text-link attention-notion" href="${esc(notion)}" target="_blank" rel="noreferrer">Open the simple Notion list →</a>`:''}</section>`;
}
function archivedPanel(){const list=DATA.archived_projects||[];if(!list.length)return '';return `<div class="archived-panel"><div><strong>Archived</strong><span>Hidden from the dashboard and Watch</span></div><div class="archived-list">${list.map(p=>`<button type="button" data-restore="${esc(p.id)}"><span style="--accent:${p.accent}" class="project-dot"></span>${esc(p.name)}<b>Restore</b></button>`).join('')}</div></div>`}
function activityItem(a){const p=DATA.projects.find(p=>p.id===a.project);return `<div class="activity-item" style="--accent:${p?.accent||'#aab5c4'}"><span class="activity-symbol">${icon(a.kind==='commit'?'commit':'check')}</span><div class="activity-copy"><p>${esc(a.title)}</p><div class="activity-meta"><strong>${esc(p?.name||a.project)}</strong><span>·</span><time datetime="${esc(a.ts)}" title="${date(a.ts,true)}">${rel(a.ts)}</time><span title="${esc(a.detail)}">${a.kind==='commit'?esc(a.detail):'Project step'}</span></div></div></div>`}
function activityPanel(pid='',limit=4){const visible=new Set(DATA.projects.map(p=>p.id));const list=ACTIVITY.filter(a=>pid?a.project===pid:visible.has(a.project)).slice(0,limit);return `<div class="panel"><div class="panel-header"><div><h2>Recent activity</h2><div class="panel-subtitle">Code and project updates</div></div>${icon('pulse')}</div><div class="activity-list">${list.map(activityItem).join('')||'<div class="empty">No activity recorded yet.</div>'}</div><div class="panel-footer"><a href="#activity${pid?'?project='+pid:''}" class="text-link">Full history ${icon('arrow')}</a></div></div>`}
function ranges(){return `<div class="range-control" role="group" aria-label="Chart period">${[['24h','24h'],['7d','7d'],['30d','30d'],['all','Alles']].map(([id,title])=>`<button data-range="${id}" class="${range===id?'active':''}" aria-pressed="${range===id}">${title}</button>`).join('')}</div>`}
function graphPanel(projects){return `<div class="panel"><div class="panel-header"><div><h2>Progress over time</h2><div class="panel-subtitle">Project steps · Amsterdam time</div></div>${ranges()}</div><div class="legend">${projects.map(p=>`<span style="--accent:${p.accent}"><i class="legend-dot"></i>${p.name}</span>`).join('')}</div><div id="progressChart" class="chart-wrap"></div><div class="chart-detail" id="chartDetail">Select a data point for date and progress.</div></div>`}
function infraStrip(){const h=DATA.host,mem=h.memory.used/h.memory.total*100,disk=h.disk.used/h.disk.total*100;return `<div class="infra-strip"><div class="server-icon">${icon('server')}</div><div class="infra-title"><strong>VPS · OVHcloud</strong><small>${h.cores} vCPU · ${gb(h.memory.total)} RAM</small></div><div class="infra-item">CPU<strong>${h.cpu==null?'First measurement…':num(h.cpu)+'%'}</strong><div class="microbar"><span style="width:${h.cpu||0}%"></span></div></div><div class="infra-item">Memory<strong>${num(mem)}%</strong><div class="microbar"><span style="width:${mem}%"></span></div></div><div class="infra-item">Free storage<strong>${gb(h.disk.free)}</strong><div class="microbar"><span style="width:${disk}%"></span></div></div><a href="#infrastructure" class="text-link" aria-label="View infrastructure">View VPS ${icon('arrow')}</a></div>`}
function runnerPanel(){
  const runners=DATA.chatgpt_runners||{};
  const service=DATA.chatgpt_firefox||{state:'unknown',active:false,main_pid:0,restarts:0,auto_restart:'unknown'};
  const entries=DATA.projects.map(p=>[p.id,p.name]);
  const labels={live:'Active',stale:'Last active is stale',offline:'Offline',stalled:'Needs attention',paused:'Paused'};
  const serviceCard='<article class="runner-card '+(service.active?'runner-live':'')+'"><div class="runner-card-head"><div><span class="eyebrow">ZCLOUD ENGINE</span><h3>Firefox initiator</h3></div><span class="runner-state '+(service.active?'':'runner-warn')+'">'+(service.active?'Service active':'Service '+esc(service.state||'unknown'))+'</span></div><div class="runner-metrics"><div><small>Process</small><strong>'+(service.main_pid?'PID '+esc(service.main_pid):'Inactive')+'</strong></div><div><small>Auto-restart</small><strong>'+(service.auto_restart==='always'?'Always on':esc(service.auto_restart||'Unknown'))+'</strong></div><div><small>Systemd restarts</small><strong>'+esc(service.restarts??0)+'</strong></div><div><small>Function</small><strong>ChatGPT tabs + scripts</strong></div></div>'+(service.error?'<div class="runner-error">'+esc(service.error)+'</div>':'')+'<div class="runner-card-foot"><span>'+(service.active?'Firefox and the extension are running on the VPS':'Initiator is inactive; restore the service')+'</span><button type="button" class="runner-action" data-runner-service-restart>Restart Firefox initiator</button></div></article>';
  const cards=entries.map(([id,name])=>{
    const r=runners[id]||{state:'paused',active:false,name:name};
    const active=!!r.active,state=r.stalled?'stalled':r.state;
    const pending=['pending','dispatched'].includes(r.command?.status);
    const heartbeat=r.last_heartbeat?.time?rel(r.last_heartbeat.time):'No activity measured yet';
    const lastPrompt=r.last_prompt_sent?.time?date(r.last_prompt_sent.time,true):'No prompt measured yet';
    const progress=r.progress_age_seconds==null?'No text progress':r.progress_age_seconds<60?'Text updated just now':Math.floor(r.progress_age_seconds/60)+' min without a text update';
    const mode=!active?'Paused':r.generating?'ChatGPT generating':r.state==='live'?'Waiting for response':'Starting';
    const workerCount=Math.max(1,Number(r.worker_count||1));
    const actions=active
      ? '<button type="button" class="runner-action" data-runner-push="'+esc(id)+'" '+(pending?'disabled':'')+'>Push now</button><button type="button" class="runner-action runner-action-secondary" data-runner-pause="'+esc(id)+'" '+(pending?'disabled':'')+'>Pause</button><button type="button" class="runner-action runner-action-secondary" data-runner-new-chat="'+esc(id)+'" '+(pending?'disabled':'')+'>New chat</button>'
      : '<button type="button" class="runner-action" data-runner-start="'+esc(id)+'" '+(pending?'disabled':'')+'>'+(pending?'Starting…':'Start project')+'</button>';
    return '<article class="runner-card '+(active&&state==='live'?'runner-live':'')+'"><div class="runner-card-head"><div><span class="eyebrow">'+esc(name)+'</span><h3>'+esc(r.name||name)+'</h3></div><span class="runner-state '+(active&&state==='live'?'':'runner-warn')+'">'+(labels[state]||'Unknown')+'</span></div><div class="runner-metrics"><div><small>ChatGPT automation</small><strong>'+esc(mode)+'</strong></div><div><small>Last active</small><strong>'+esc(heartbeat)+'</strong></div><div><small>Last prompt</small><strong>'+esc(lastPrompt)+'</strong></div><div><small>Progress</small><strong>'+esc(progress)+'</strong></div></div><div class="runner-worker-row"><label>ChatGPT tabs <input type="number" min="1" max="8" step="1" value="'+workerCount+'" data-runner-workers="'+esc(id)+'" aria-label="Number of ChatGPT tabs for '+esc(name)+'"></label><small>Each tab gets its own workspace.</small></div>'+(r.error?'<div class="runner-error">'+esc(r.error)+'</div>':'')+'<div class="runner-card-foot"><span>'+(pending?'Action is being executed':r.command?.result?esc(r.command.result):active?'Push uses the current project chat.':'Starting or resuming a dedicated project chat with the Notion handoff.')+'</span><div class="runner-actions">'+actions+'</div></div></article>';
  }).join('');
  return '<section class="runner-section"><div class="section-title"><div><h2>Project initiators</h2><p class="reduced">Firefox service, project status and recovery. New chats always receive a project-specific prompt.</p></div></div><div class="runner-grid">'+serviceCard+cards+'</div></section>';
}
async function restartRunner(projectId,button){
  const projectName=DATA?.projects?.find(p=>p.id===String(projectId).split('::w')[0])?.name||projectId;
  if(!window.confirm('Start a new ChatGPT chat for '+projectName+'? Any running response in the old chat will be stopped.'))return;
  button.disabled=true;
  try{
    const response=await fetch('/api/runner-control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:projectId,action:'new_chat'}),signal:AbortSignal.timeout(10000)});
    const data=await response.json();
    if(!response.ok){throw new Error(data.error||'Action failed')}
    button.textContent='Restart queued';setTimeout(()=>refresh(true),1500);
  }catch(error){window.alert(error.message||'ZCloud could not send the action.');button.disabled=false}
}
async function setRunnerActive(projectId,action,button){
  const labelEl=button.querySelector('.runner-toggle-label');const previous=labelEl?labelEl.textContent:button.textContent;const setLabel=value=>{if(labelEl)labelEl.textContent=value;else button.textContent=value};button.disabled=true;setLabel(action==='start'?'Starting…':button.classList.contains('runner-toggle')?'Stoppen…':'Pausing…');
  try{
    const response=await fetch('/api/runner-control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:projectId,action}),signal:AbortSignal.timeout(10000)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok){throw new Error(data.error||'Action failed')}
    setLabel(action==='start'?'Started':button.classList.contains('runner-toggle')?'Stopped':'Paused');
    setTimeout(()=>refresh(true),450);
  }catch(error){setLabel('Failed');window.alert(error.message||String(error))}
  finally{setTimeout(()=>{button.disabled=false;setLabel(previous)},1800)}
}
async function forceStartProject(projectId,button){
  const previous=button.textContent;button.disabled=true;button.textContent='Force starting…';
  try{
    // Start exactly the requested PROJECT, not a stale list of per-worker IDs
    // from the previous dashboard snapshot. The v2 page runner resolves the
    // newest queue-backed w1 identity and its actual prompt at send time.
    // A single parent command also prevents duplicate Force sends.
    const response=await fetch('/api/runner-control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:projectId,action:'start',force:true}),signal:AbortSignal.timeout(10000)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||'Force start failed');
    // Accepted != sent. The addon wakes the newly assigned worker to consume
    // this command; do not display "started" before prompt-sent evidence.
    button.textContent='Force requested';
    setTimeout(()=>refresh(true),650);
  }catch(error){button.textContent='Failed';window.alert(error.message||String(error))}
  finally{setTimeout(()=>{button.disabled=false;button.textContent=previous},2200)}
}
async function pushRunner(projectId,button){
  const previous=button.textContent;button.disabled=true;button.textContent='Pushing…';
  try{
    const response=await fetch('/api/runner-control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:projectId,action:'push'}),signal:AbortSignal.timeout(10000)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok){throw new Error(data.error||'Push failed')}
    button.textContent='Push started';
    setTimeout(()=>refresh(true),1200);
  }catch(error){button.textContent='Failed';window.alert(error.message||String(error))}
  finally{setTimeout(()=>{button.disabled=false;button.textContent=previous},2200)}
}
async function setRunnerWorkers(projectId,count,input){
  const previous=input.value;input.disabled=true;
  try{
    const response=await fetch('/api/runner-workers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:projectId,worker_count:Number(count)}),signal:AbortSignal.timeout(10000)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||'Saving the number of ChatGPT tabs failed');
    input.value=String(data.worker_count);setTimeout(()=>refresh(true),700);
  }catch(error){input.value=previous;window.alert(error.message||String(error))}
  finally{input.disabled=false}
}
function dynamicWorkerControl(){
  const cfg=DATA?.dynamic_workers||{};
  const max=Math.max(1,Number(cfg.max_workers_per_provider||8));
  const chatgpt=Math.max(0,Number(cfg.chatgpt_count??cfg.providers?.chatgpt?.count??2));
  const claude=Math.max(0,Number(cfg.claude_count??cfg.providers?.claude?.count??1));
  const total=chatgpt+claude;
  const state=total>0?total+' worker'+(total===1?'':'s')+' beschikbaar':'Geen workers actief';
  const field=(key,label,value,min,max,step=1,suffix='')=>`<label><span>${esc(label)}</span><div class="dynamic-worker-input"><input type="number" inputmode="numeric" min="${min}" max="${max}" step="${step}" value="${esc(value)}" data-dynamic-setting="${esc(key)}" aria-label="${esc(label)}">${suffix?`<em>${esc(suffix)}</em>`:''}</div></label>`;
  return `<div class="dynamic-worker-control dynamic-worker-control-advanced" data-dynamic-worker-control>
    <div class="dynamic-worker-copy">
      <small>WERKERPOOL</small>
      <strong>${esc(state)}</strong>
      <span>${chatgpt} ChatGPT · ${claude} Claude</span>
    </div>
    <div class="dynamic-worker-counts">
      ${field('chatgpt_count','ChatGPT',chatgpt,0,max)}
      ${field('claude_count','Claude',claude,0,max)}
    </div>
    <details class="dynamic-worker-advanced" data-disclosure="dynamic-worker-timing">
      <summary>Cooldowns & timing</summary>
      <div class="dynamic-worker-provider-grid">
        ${field('chatgpt_cooldown_seconds','ChatGPT cooldown',Number(cfg.chatgpt_cooldown_seconds??cfg.providers?.chatgpt?.cooldown_seconds??120),5,86400,1,'sec')}
        ${field('claude_cooldown_seconds','Claude cooldown',Number(cfg.claude_cooldown_seconds??cfg.providers?.claude?.cooldown_seconds??120),5,86400,1,'sec')}
        ${field('check_interval_ms','Assignment check',Number(cfg.check_interval_ms??5000),1000,60000,250,'ms')}
        ${field('tick_interval_ms','DOM check',Number(cfg.tick_interval_ms??1500),250,10000,250,'ms')}
        ${field('heartbeat_interval_ms','Heartbeat',Number(cfg.heartbeat_interval_ms??30000),5000,300000,1000,'ms')}
        ${field('generation_start_timeout_ms','Generation timeout',Number(cfg.generation_start_timeout_ms??120000),10000,600000,1000,'ms')}
        ${field('scheduler_interval_seconds','Backend scheduler',Number(cfg.scheduler_interval_seconds??5),1,300,1,'sec')}
      </div>
    </details>
    <button type="button" class="dynamic-worker-save" data-save-dynamic-workers>Opslaan</button>
  </div>`;
}
async function saveDynamicWorkerSettings(button){
  const root=button.closest('[data-dynamic-worker-control]');if(!root)return;
  const inputs=[...root.querySelectorAll('[data-dynamic-setting]')];
  const payload={};
  for(const input of inputs){
    if(!input.validity.valid){input.reportValidity();return}
    payload[input.dataset.dynamicSetting]=Number(input.value);
  }
  button.disabled=true;const before=button.textContent;button.textContent='Saving…';
  try{
    const response=await fetch('/api/dynamic-workers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(10000)});
    const data=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(data.error||'Saving dynamic worker settings failed');
    if(data.dynamic_workers?.reconciled!==true)throw new Error('Worker settings were saved but the runtime did not reconcile');
    if(DATA)DATA.dynamic_workers=data.dynamic_workers;
    // Update rows immediately after a successful server-side reconciliation.
    render();
    button.textContent='Opgeslagen';
    setTimeout(()=>refresh(true),250);
  }catch(error){button.textContent=before;window.alert(error.message||String(error))}
  finally{button.disabled=false}
}
async function restartFirefoxInitiator(button){
  if(!window.confirm('Restart the Firefox ChatGPT initiator on the VPS? The project tabs will reopen and the runners will continue automatically.'))return;
  button.disabled=true;button.textContent='Herstarten…';
  try{
    const response=await fetch('/api/runner-control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'restart_firefox'}),signal:AbortSignal.timeout(15000)});
    const data=await response.json();
    if(!response.ok){throw new Error(data.error||'Restart failed')}
    button.textContent='Initiator restart';setTimeout(()=>refresh(true),1500);
  }catch(error){window.alert(error.message||'Firefox-initiator could not be restarted.');button.disabled=false;button.textContent='Restart Firefox initiator'}
}
/*wd:start*/
let WORKER_DEBUG=null,WORKER_DEBUG_ERROR='';
const VERDICT_LABEL={'generating':'Generating','prompt-sent':'Prompt sent','paused':'Paused','idle:no-assignment':'Idle · no assignment','waiting':'Waiting','no-heartbeat':'No heartbeat','blocked:contract':'Blocked · prompt contract','blocked:client':'Blocked · browser refused'};
function verdictClass(v){v=String(v||'');return v==='generating'||v==='prompt-sent'?'ok':v.startsWith('blocked')||v==='no-heartbeat'?'bad':'idle'}
function ageText(s){if(s==null)return '—';if(s<90)return s+'s ago';if(s<5400)return Math.round(s/60)+' min ago';return Math.round(s/3600)+' h ago'}
function driverLine(d){const e=d.events_15m||{};const ff=d.firefox&&typeof d.firefox==='object'&&d.firefox.active!=null?(d.firefox.active?'Firefox initiator active':'Firefox initiator inactive'):'';return [ff,'last 15 min: '+['heartbeat','prompt-sent','send-blocked','assignment-invalid'].map(k=>k+' ×'+(e[k]||0)).join(', '),'failed commands ×'+(d.failed_commands_15m||0)].filter(Boolean).join(' · ')}
function workerRow(w){
  const workerId=String(w.worker||'').trim();
  const unavailable=w.desired_state==='paused'||w.desired_state==='draining';
  const push=workerId?`<button type="button" class="runner-action wd-worker-push" data-worker-action="push" data-worker-id="${esc(workerId)}" ${unavailable?'disabled title="Worker is not running"':''}>Push now</button>`:'';
  return `<div class="wd-row wd-${verdictClass(w.verdict)}"><div class="wd-main"><strong>${esc(w.project)} · w${esc(w.slot)}</strong><span>${esc(w.provider)} · ${esc(w.queue_id||'no assignment')}</span></div><div class="wd-state"><b>${esc(VERDICT_LABEL[w.verdict]||w.verdict)}</b><small>${esc(w.reason||'')}</small></div><div class="wd-meta">last signal ${esc(ageText(w.last_event_age_s))}${w.last_event?' · '+esc(w.last_event):''}${w.last_prompt_age_s!=null?' · prompt '+esc(ageText(w.last_prompt_age_s)):''}</div>${push}</div>`;
}
function workerScalingPanel(){
  let body;
  if(WORKER_SCALING_ERROR)body='<div class="wd-empty">'+esc(WORKER_SCALING_ERROR)+'</div>';
  else if(!WORKER_SCALING)body='<div class="wd-empty">Measuring multi-worker scaling…</div>';
  else{
    const items=(DATA?.projects||[]).map(p=>({project:p,scaling:scalingForProject(p.id)})).filter(x=>x.scaling&&Number(x.scaling.desired_workers||1)>1);
    body=items.length?'<div class="worker-scaling-rows">'+items.map(({project,scaling})=>{
      const a=scaling.assessment||{},copy=scalingStateCopy(a.state);
      const ratio=Number.isFinite(Number(a.extra_vs_primary_throughput_ratio))?' · extra/primary '+num(Number(a.extra_vs_primary_throughput_ratio))+'×':'';
      const nonwork=Number.isFinite(Number(a.extra_idle_blocked_pct))?' · idle/blocked '+num(Number(a.extra_idle_blocked_pct))+'%':'';
      return '<div class="worker-scaling-row '+copy[1]+'"><div><strong>'+esc(project.name)+'</strong><span>'+esc(copy[0])+'</span></div><p>'+esc(a.reason||'No assessment yet')+'</p><small>'+Number(scaling.desired_workers||1)+' desired workers'+esc(ratio+nonwork)+'</small></div>';
    }).join('')+'</div>':'<div class="wd-empty">No multi-worker projects are currently configured.</div>';
  }
  const method=WORKER_SCALING?.method?' · '+esc(WORKER_SCALING.method):'';
  return '<section class="panel worker-scaling" id="workerScaling"><div class="panel-header"><div><h2>Worker scaling</h2><div class="panel-subtitle">Do extra chats create extra throughput?</div></div></div>'+body+'<div class="wd-foot">12-hour observational window'+method+'</div></section>';
}
async function loadWorkerScaling(){
  try{
    const r=await fetch('/api/worker-scaling?hours=12',{cache:'no-store',signal:AbortSignal.timeout(10000)});
    if(r.status===403){WORKER_SCALING=null;WORKER_SCALING_ERROR='Scaling telemetry is only visible from a trusted admin device.'}
    else if(!r.ok)throw new Error('HTTP '+r.status);
    else{WORKER_SCALING=await r.json();WORKER_SCALING_ERROR=''}
  }catch(e){WORKER_SCALING_ERROR='Scaling telemetry unavailable: '+(e.message||e)}
  const el=$('workerScaling');if(el)el.outerHTML=workerScalingPanel();
  if(route==='overview'&&DATA)render();
}

function workerDebugPanel(){
  let body;
  if(WORKER_DEBUG_ERROR)body=`<div class="wd-empty">${esc(WORKER_DEBUG_ERROR)}</div>`;
  else if(!WORKER_DEBUG)body='<div class="wd-empty">Loading worker diagnostics…</div>';
  else body=`<div class="wd-rows">${(WORKER_DEBUG.workers||[]).map(workerRow).join('')||'<div class="wd-empty">No allocated workers.</div>'}</div><div class="wd-foot">${esc(driverLine(WORKER_DEBUG))}</div>`;
  return `<section class="panel worker-debug" id="workerDebug"><div class="panel-header"><div><h2>Worker diagnostics</h2><div class="panel-subtitle">Push one dynamic worker directly without touching the others.</div></div></div>${body}</section>`;
}
async function loadWorkerDebug(){
  try{
    const r=await fetch('/api/worker-debug',{cache:'no-store',signal:AbortSignal.timeout(10000)});
    if(r.status===403){WORKER_DEBUG=null;WORKER_DEBUG_ERROR='Diagnostics are only visible from a trusted admin device.'}
    else if(!r.ok)throw new Error('HTTP '+r.status);
    else{WORKER_DEBUG=await r.json();WORKER_DEBUG_ERROR=''}
  }catch(e){WORKER_DEBUG_ERROR='Diagnostics unavailable: '+(e.message||e)}
  const el=$('workerDebug');if(el)el.outerHTML=workerDebugPanel();const rows=$('globalWorkerRows');if(rows)rows.outerHTML=globalWorkerRows();
}
/*wd:end*/

/* Pool slots remain queue-owned. A project dropdown requests scheduler priority;
   it never claims to rebind a busy worker or an existing SQLite claim. */
const GLOBAL_WORKER_PROJECT_REQUESTS={};
const GLOBAL_WORKER_REQUEST_RESULTS={};
const GLOBAL_WORKER_REQUEST_AT={};
const WORKER_REQUEST_RETRY_MS=30000;
function workerControlIcon(kind){
  const paths={
    play:'<path d="m8 5 11 7-11 7z"/>',
    pause:'<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
    force:'<path d="M13 2 5 13h6l-1 9 9-12h-6z"/>'
  };
  return '<svg aria-hidden="true" viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round" stroke-linecap="round">'+paths[kind]+'</svg>';
}
function globalWorkerRows(){
  const settings=DATA?.dynamic_workers||{};
  const count=Math.max(0,Math.min(16,Number(settings.chatgpt_count??0)+Number(settings.claude_count??0)));
  const workers=Array.isArray(WORKER_DEBUG?.workers)?WORKER_DEBUG.workers:[];
  const bySlot=new Map(workers.filter(w=>Number.isInteger(Number(w.global_slot))).map(w=>[Number(w.global_slot),w]));
  const verified=Boolean(WORKER_DEBUG&&!WORKER_DEBUG_ERROR);
  if(!count)return '<div class="global-worker-empty" id="globalWorkerRows">Nog geen workers. Kies hierboven een aantal en klik op Opslaan.</div>';
  return '<div class="global-worker-list" id="globalWorkerRows">'+(WORKER_DEBUG_ERROR?'<p class="global-worker-access" role="alert">'+esc(WORKER_DEBUG_ERROR)+'</p>':'')+Array.from({length:count},(_,i)=>{
    const slot=i+1,w=bySlot.get(slot),provider=slot<=Number(settings.chatgpt_count||0)?'ChatGPT':'Claude';
    const assigned=verified?(w?.project||''):'';
    if(assigned&&assigned===GLOBAL_WORKER_PROJECT_REQUESTS[slot]&&String(GLOBAL_WORKER_REQUEST_RESULTS[slot]||'').startsWith('Project priority requested')){
      delete GLOBAL_WORKER_PROJECT_REQUESTS[slot];delete GLOBAL_WORKER_REQUEST_RESULTS[slot];delete GLOBAL_WORKER_REQUEST_AT[slot];
    }
    const choice=Object.prototype.hasOwnProperty.call(GLOBAL_WORKER_PROJECT_REQUESTS,slot)?GLOBAL_WORKER_PROJECT_REQUESTS[slot]:assigned;
    const options=[...(DATA?.projects||[]),...(DATA?.archived_projects||[]),...(DATA?.worker_modes||[])].map(p=>'<option value="'+esc(p.id)+'"'+(choice===p.id?' selected':'')+'>'+esc(p.name)+'</option>').join('');
    const pending=GLOBAL_WORKER_REQUEST_RESULTS[slot]||'';
    const waiting=(pending.startsWith('Project priority requested')||pending.startsWith('Force voor worker'))&&Date.now()-Number(GLOBAL_WORKER_REQUEST_AT[slot]||0)<WORKER_REQUEST_RETRY_MS;
    const verdict=verified?(w?(VERDICT_LABEL[w.verdict]||w.verdict):'Beschikbaar'):'Status niet geverifieerd';
    const workerId=verified?String(w?.worker||''):'';
    const paused=w?.desired_state==='paused';
    const busySlot=Boolean(w&&['generating','prompt-sent'].includes(w.verdict));
    const switching=Boolean(choice&&choice!==assigned);
    const canPlay=verified&&!waiting&&((switching&&!busySlot)||(!switching&&workerId&&paused)||(!workerId&&Boolean(choice)));
    const canPause=verified&&Boolean(workerId)&&!paused;
    const canForce=verified&&!busySlot&&(Boolean(workerId)||Boolean(choice));
    const chatId=workerId?((DATA?.chatgpt_runners||{})[assigned]?.workers||[]).find(x=>x.worker_id===workerId)?.conversation_id:'';
    const chatUrl=chatId?(provider==='Claude'?'https://claude.ai/chat/':'https://chatgpt.com/c/')+encodeURIComponent(chatId):'';
    const disabled=(allowed,reason)=>allowed?'':' disabled';
    // Only one compact status notice per worker. Keep the full explanation in
    // a native disclosure, so long scheduler responses cannot resize controls.
    const noteLabel=pending
      ?(waiting?'Aanvraag ingediend · wacht op werkertoewijzing':pending.startsWith('Project priority requested')?'Nog niet toegewezen. Probeer Play opnieuw.':pending)
      :(switching?'Projectkeuze nog niet actief':'');
    const noteDetail=pending
      ?(waiting?'De projectwissel is aangevraagd. Hij wordt actief zodra de scheduler dit slot toewijst. '+pending:pending.startsWith('Project priority requested')?'De aanvraag is nog niet toegewezen. Je kunt opnieuw op Play drukken. '+pending:pending)
      :(switching?'Druk op Play of Force om het gekozen project aan te vragen. Een lopende taak wordt niet onderbroken.':'');
    return '<article class="global-worker-row" data-global-slot="'+slot+'">'
      +'<div class="global-worker-identity"><span class="global-worker-index">'+String(slot).padStart(2,'0')+'</span><div><strong>Worker '+slot+'</strong><small>'+esc(provider)+'</small></div></div>'
      +'<div class="global-worker-state '+(w?verdictClass(w.verdict):'idle')+'"><span class="global-worker-dot" aria-hidden="true"></span><span>'+esc(verdict)+'</span></div>'
      +'<label class="global-worker-project"><span>Project</span><select data-global-worker-project="'+slot+'" aria-label="Project voor worker '+slot+'"><option value=""'+(!choice?' selected':'')+'>Geen project</option>'+options+'</select></label>'
      +'<div class="global-worker-actions" aria-label="Bediening worker '+slot+'">'
      +'<button type="button" class="worker-icon-action worker-play" data-global-worker-play="'+slot+'" aria-label="Start worker '+slot+'" title="Start worker"'+disabled(canPlay,'Kies een project of wacht totdat de worker beschikbaar is')+'>'+workerControlIcon('play')+'</button>'
      +'<button type="button" class="worker-icon-action worker-pause" data-worker-action="pause" data-worker-id="'+esc(workerId)+'" aria-label="Pauzeer worker '+slot+'" title="Pauzeer worker"'+disabled(canPause,'Geen draaiende worker om te pauzeren')+'>'+workerControlIcon('pause')+'</button>'
      +'<button type="button" class="worker-icon-action worker-force" data-global-worker-force="'+slot+'" aria-label="Forceer worker '+slot+'" title="Forceer opnieuw starten"'+disabled(canForce,'Worker is bezig of status ontbreekt')+'>'+workerControlIcon('force')+'</button>'
      +'</div>'
      +(chatUrl?'<a class="global-worker-chat" target="_blank" rel="noopener noreferrer" href="'+esc(chatUrl)+'" aria-label="Open de chat van worker '+slot+'">↗ Chat</a>':'')
      +(noteLabel?'<details class="global-worker-note"><summary><span class="global-worker-note-icon" aria-hidden="true">i</span><span class="global-worker-note-label" role="status">'+esc(noteLabel)+'</span></summary><p class="global-worker-note-detail">'+esc(noteDetail)+'</p></details>':'')
      +'</article>';
  }).join('')+'</div>';
}
function globalWorkerConsole(){
  const cfg=DATA?.dynamic_workers||{},count=Math.max(0,Number(cfg.chatgpt_count||0)+Number(cfg.claude_count||0));
  return '<section class="worker-console worker-cockpit" id="workers" tabindex="-1" aria-label="Workers toewijzen">'
    +'<div class="worker-console-heading"><div><span class="eyebrow">ZCLOUD / CONTROL CENTER</span><h2>Worker verdeling <span class="count">'+count+'</span></h2><p>Eén plek voor al je workers. Stel capaciteit in en kies per worker het project.</p></div><span class="worker-console-live"><i aria-hidden="true"></i> CENTRALE BESTURING</span></div>'
    +'<div class="global-worker-pool-heading"><strong>Capaciteit</strong><small>Opslaan voegt workers toe of haalt ze weg</small></div>'
    +dynamicWorkerControl()
    +'<div class="global-worker-subhead"><strong>Toewijzingen</strong><small>Projectkeuzes worden aangevraagd zodra je Start of Force gebruikt. Een lopende taak wordt niet onderbroken.</small></div>'
    +globalWorkerRows()
    +'<details class="worker-console-diagnostics" data-disclosure="worker-diagnostics"><summary>Diagnostiek en scaling</summary>'+workerDebugPanel()+workerScalingPanel()+'</details>'
    +'</section>';
}
async function forceGlobalWorker(slot,button){
  const row=button.closest('[data-global-slot]');
  const selected=String(row?.querySelector('[data-global-worker-project]')?.value||'');
  const observed=(WORKER_DEBUG?.workers||[]).find(w=>Number(w.global_slot)===slot);
  if(!WORKER_DEBUG||WORKER_DEBUG_ERROR){window.alert('Live workerstatus ontbreekt.');return}
  if(observed&&['generating','prompt-sent'].includes(observed.verdict)){window.alert('Deze worker is nog bezig.');return}
  if(selected&&selected!==observed?.project){await requestGlobalWorkerProject(slot,button,true);return}
  if(observed?.worker){await controlWorker(observed.worker,'start',button,true,slot);return}
  if(selected){await requestGlobalWorkerProject(slot,button,true);return}
  window.alert('Kies eerst een project.');
}
async function playGlobalWorker(slot,button){
  const row=button.closest('[data-global-slot]');
  const selected=String(row?.querySelector('[data-global-worker-project]')?.value||'');
  const verified=Boolean(WORKER_DEBUG&&!WORKER_DEBUG_ERROR);
  const observed=(WORKER_DEBUG?.workers||[]).find(w=>Number(w.global_slot)===slot);
  if(!verified){window.alert('Live workerstatus ontbreekt. Probeer opnieuw na verversen.');return}
  if(selected&&selected!==observed?.project){await requestGlobalWorkerProject(slot,button);return}
  if(observed?.worker){await controlWorker(observed.worker,'start',button);return}
  if(selected){await requestGlobalWorkerProject(slot,button);return}
  window.alert('Kies eerst een project om deze worker te starten.');
}
async function requestGlobalWorkerProject(slot,button,force=false){
  const row=button.closest('[data-global-slot]'),selector=row?.querySelector('[data-global-worker-project]');
  if(!selector)return;
  const target=String(selector.value||'');
  const observed=(WORKER_DEBUG?.workers||[]).find(w=>Number(w.global_slot)===slot);
  if(!target){GLOBAL_WORKER_REQUEST_RESULTS[slot]='Automatic queue selection is already active. No project was reassigned.';render();return}
  if(![...(DATA?.projects||[]),...(DATA?.archived_projects||[]),...(DATA?.worker_modes||[])].some(p=>p.id===target)){window.alert('Choose a valid project or worker role.');return}
  if(observed&&observed.project!==target&&['generating','prompt-sent'].includes(observed.verdict)){
    window.alert('This worker is busy. Finish or stop its current task before requesting a different project.');return;
  }
  const old=button.innerHTML;button.disabled=true;button.setAttribute('aria-busy','true');
  try{
    const response=await fetch('/api/runner-control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:target,action:'start',force,global_worker_slot:slot}),signal:AbortSignal.timeout(10000)});
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(result.error||'Could not request project');
    // This API requests project admission; the scheduler can still select another slot.
    GLOBAL_WORKER_REQUEST_RESULTS[slot]=force?'Force voor worker '+slot+' aangevraagd. Wacht op een veilige taaktoewijzing en nieuwe tab.':'Project priority requested. Waiting for queue allocation; no immediate slot switch is guaranteed.';
    GLOBAL_WORKER_REQUEST_AT[slot]=Date.now();
    GLOBAL_WORKER_PROJECT_REQUESTS[slot]=target;
    setTimeout(()=>refresh(true),650);render();
  }catch(error){delete GLOBAL_WORKER_REQUEST_AT[slot];GLOBAL_WORKER_REQUEST_RESULTS[slot]='Projectaanvraag mislukt; druk opnieuw op Play. '+(error.message||error);render()}
  finally{button.disabled=false;button.innerHTML=old;button.removeAttribute('aria-busy')}
}

function overview(){const p=DATA.projects,done=p.reduce((n,p)=>n+p.completed,0),total=p.reduce((n,p)=>n+p.milestones.length,0),active=p.filter(p=>p.health==='healthy').length,recent=ACTIVITY.filter(a=>a.kind==='commit'&&Date.parse(a.ts)>Date.now()-86400000).length;return globalWorkerConsole()+heading('PROJECT PORTFOLIO','Keep the work moving.','Check project progress and activity below the worker controls.',`<span class="date-label">${icon('calendar')}${new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Amsterdam',weekday:'short',day:'numeric',month:'long',year:'numeric'}).format(new Date())}</span>`)+attentionPanel()+`<div class="section-title project-section-title"><div><h2>Projects <span class="count">${p.length}</span></h2><span class="reduced">Open a project for context · drag to reorder</span></div></div><div class="project-grid" id="projectGrid">${p.map(projectCard).join('')}</div>${archivedPanel()}<div class="section-title snapshot-title"><div><h2>Portfolio snapshot</h2><span class="reduced">Useful context after the actions above</span></div></div><div class="stat-grid">${stat('Active projects',active+'<span>/ '+p.length+'</span>',active===p.length?'Everything is operating normally':'Check service status','layers',active===p.length)}${stat('Project steps completed',done+'<span>/ '+total+'</span>','According to the project plans','target')}${stat('Recent commits',recent,'Recorded in the last 24 hours','commit')}${stat('VPS uptime',Math.floor(DATA.host.uptime/86400)+'<span>d '+Math.floor(DATA.host.uptime%86400/3600)+'u</span>',DATA.host.cores+' vCPU · load '+num(DATA.host.load),'server')}</div><div class="dashboard-grid">${graphPanel(p)}${activityPanel()}</div>${infraStrip()}`}
function aiRunPanel(p){const r=p.last_chatgpt_run;return `<div class="panel"><div class="panel-header"><div><h2>Latest ChatGPT action</h2><div class="panel-subtitle">${r?(r.estimated?'Estimate based on project activity':'Automation measurement'):'No ChatGPT action measured yet'}</div></div>${icon('clock')}</div><div class="detail-note">${r?`${esc(r.label)}<br><br>${date(r.time,true)} · ${rel(r.time)}`:'No ChatGPT action is linked to this project yet.'}</div></div>`}
function codePanel(p){if(p.updated)return `<div class="panel"><div class="panel-header"><h2>Latest code update</h2>${icon('commit')}</div><div class="detail-note">${esc(p.message)}<br><br>${esc(p.commit)} · ${date(p.updated,true)}${p.shallow?'<br>The runner uses a shallow Git checkout; the commit count is not the project total.':''}</div></div>`;if(p.source_status==='empty')return `<div class="panel"><div class="panel-header"><h2>Latest code update</h2>${icon('commit')}</div><div class="detail-note">Repository linked · no commits yet. New commits are picked up automatically by zCloud.</div></div>`;return ''}
function services(p){const labels={active:'Active',activating:'Starting',waiting:'Waiting for next run',inactive:'Inactive',failed:'Failed',unknown:'Unknown'};return `<div class="panel"><div class="panel-header"><div><h2>Services</h2><div class="panel-subtitle">Live status · read-only</div></div>${badge(p)}</div><div class="service-list">${p.services.length?p.services.map(s=>`<div class="service-row"><div>${esc(s.name)}${s.name==='Research'&&s.last_run?`<div class="service-sub">Last run: ${Number.isFinite(Date.parse(s.last_run))?date(s.last_run,true):esc(s.last_run)}</div>`:''}</div><span class="service-state ${['active','activating','waiting'].includes(s.state)?'':'warn'}">${labels[s.state]||esc(s.state)}</span></div>`).join(''):'<div class="service-row"><span>Dashboard API</span><span class="service-state">Active</span></div>'}</div>${p.id==='ftmo'?'<div class="detail-note">The research task runs every five minutes. Waiting between runs is normal.</div>':''}</div>`}
function componentPanel(p){const c=(p.components||[])[0];if(!c)return '';const rows=(c.milestones||[]).map((m,i)=>`<div class="milestone ${m.done?'done':i===0?'next':''}"><span class="milestone-check">${m.done?'✓':String(i+1).padStart(2,'0')}</span><span>${esc(m.title)}</span><em>${m.done?'Completed':''}</em></div>`).join('');return `<div class="panel" style="--accent:${esc(c.accent||p.accent)}"><div class="panel-header"><div><h2>${esc(c.name)}</h2><div class="panel-subtitle">Part of ${esc(p.name)}</div></div>${icon('watch')}</div><div class="detail-note">${esc(c.summary)}<br><br><strong>${num(c.progress)}%</strong> · ${esc(c.next_step)}</div><div class="progress-track"><span style="width:${c.progress}%"></span></div><div class="milestones">${rows}</div></div>`}
function detail(p){let mini='';if(p.metrics?.available)mini=`<div class="metrics-mini"><div><strong>${num(p.metrics.analyzed)}</strong><span>Replays analyzed</span></div><div><strong>${num(p.metrics.hours)}</strong><span>Hours of replay data</span></div><div><strong>${num(p.metrics.failed)}</strong><span>Analysis errors</span></div></div>`;else if(p.id==='haxlab')mini='<p class="muted">Replay measurements are temporarily unavailable.</p>';return `<a class="back" href="#overview" data-nav="overview">${icon('back')}All projects</a><div class="page-heading"><div class="detail-heading" style="--accent:${p.accent}"><span class="project-icon">${icon(p.icon)}</span><div><div class="eyebrow">${p.eyebrow}</div><h1>${p.name}</h1></div></div><div class="detail-heading-actions">${badge(p)}</div></div>${attentionPanel(p.id)}<div class="detail-summary" style="--accent:${p.accent}"><div class="detail-progress">${num(p.progress)}<small>%</small></div><div class="detail-summary-copy"><strong>${p.goal}</strong>${p.phase?`<p><b>Phase:</b> ${esc(p.phase)} · <b>Status:</b> ${esc(p.status||'active')}</p>`:''}<p>${p.progress_basis}</p><div class="progress-track"><span style="width:${p.progress}%"></span></div>${mini}</div></div><div class="detail-columns"><div class="detail-primary">${graphPanel([p])}${activityPanel(p.id,6)}${codePanel(p)}${aiRunPanel(p)}</div><div class="detail-primary"><div class="panel" style="--accent:${p.accent}"><div class="panel-header"><h2>Project steps</h2><span class="count">${p.completed} / ${p.milestones.length}</span></div><div class="milestones">${p.milestones.map((m,i)=>`<div class="milestone ${m.done?'done':m.title===p.next?'next':''}"><span class="milestone-check">${m.done?'✓':String(i+1).padStart(2,'0')}</span><span>${m.title}</span><em>${m.done?'Completed':m.title===p.next?'NEXT':''}</em></div>`).join('')}</div><div class="detail-note">This is plan progress. Live results and model quality are not evaluated by this metric.</div></div>${services(p)}${componentPanel(p)}</div></div>`}
function workerDetailPanel(p){
  const r=(DATA?.chatgpt_runners||{})[p.id]||{},workers=r.workers||[];
  if(!workers.length)return '';
  const labels={live:'Active',stale:'Not seen recently',offline:'Offline',stalled:'Needs attention',paused:'Paused',draining:'Finishing current task',starting:'Starting'};
  const activeCount=Number(r.active_worker_count||0),desiredCount=Number(r.desired_worker_count||workers.length);
  const cards=workers.map(w=>{
    const task=w.current_task?.title||'No claimed task reported yet';
    const last=w.last_event?.time?rel(w.last_event.time):'No activity measured yet';
    const warning=(w.state==='offline'||w.state==='stalled'||w.error)
      ? `<div class="worker-warning">${w.error?esc(w.error):w.state==='stalled'?'This worker appears stuck and is not making text progress.':'This worker is offline.'}</div>`:'';
    let actions='';
    if(r.active){
      if(w.desired_state==='paused')actions=`<button type="button" class="runner-action" data-worker-action="start" data-worker-id="${esc(w.worker_id)}">Resume</button>`;
      else if(w.desired_state==='draining')actions='<button type="button" class="runner-action runner-action-secondary" disabled>Finishing current task…</button>';
      else actions=`<button type="button" class="runner-action" data-worker-action="push" data-worker-id="${esc(w.worker_id)}">Push now</button><button type="button" class="runner-action runner-action-secondary" data-worker-action="drain" data-worker-id="${esc(w.worker_id)}">Finish current task</button><button type="button" class="runner-action runner-action-secondary" data-worker-action="pause" data-worker-id="${esc(w.worker_id)}">Pause</button>`;
    }
    const tech=[['Worker-ID',w.worker_id],['Conversation-ID',w.conversation_id||'Not linked yet'],['Heartbeat',w.last_heartbeat?.time?date(w.last_heartbeat.time,true):'Not measured yet'],['Branch / PR',w.current_task?.branch||w.current_task?.pr||'Not reported'],['Lease',w.current_task?.lease_until?date(w.current_task.lease_until,true):'No active task lease']].map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('');
    return `<article class="worker-card ${warning?'worker-needs-attention':''}"><div class="worker-card-head"><div><span class="worker-number">Worker ${w.worker_slot}/${w.worker_count}</span><strong>${esc(labels[w.state]||w.state)}</strong></div><span class="worker-last">${esc(last)}</span></div>${warning}<div class="worker-main"><div><small>Workspace</small><p>${esc(w.work_area)}</p></div><div><small>Current task</small><p>${esc(task)}</p></div></div>${actions?`<div class="worker-actions">${actions}</div>`:''}<details class="worker-details"><summary>Meer details</summary><dl>${tech}</dl></details></article>`;
  }).join('');
  return `<div class="panel worker-panel"><div class="panel-header"><div><h2>ChatGPT workers</h2><div class="panel-subtitle">${activeCount} of ${desiredCount} active · technical details only when needed</div></div><span class="count">${activeCount}/${desiredCount}</span></div><div class="worker-list">${cards}</div></div>`;
}
async function controlWorker(workerId,action,button,force=false,globalSlot=null){
  const previous=button.innerHTML,iconOnly=button.classList?.contains('worker-icon-action');
  button.disabled=true;button.setAttribute('aria-busy','true');if(!iconOnly)button.textContent='Working…';
  try{
    const response=await fetch('/api/runner-control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:workerId,action,force,global_worker_slot:globalSlot}),signal:AbortSignal.timeout(10000)});
    const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.error||'Workeraction failed');
    if(!iconOnly)button.textContent=action==='drain'?'Finishing task':action==='pause'?'Paused':action==='start'?'Resume':'Push started';setTimeout(()=>refresh(true),650);
  }catch(error){if(!iconOnly)button.textContent='Failed';window.alert(error.message||String(error))}
  finally{setTimeout(()=>{button.disabled=false;button.innerHTML=previous;button.removeAttribute('aria-busy')},1800)}
}
// Explicit local operator evidence: never mix uploaded GitHub observations into
// the local VPS feed, and never grant merge/deploy/worker authority from a file.
let IMPORTED_GITHUB_ACTIVITY=null,IMPORTED_GITHUB_ERROR='';
const GITHUB_RECEIPT_KINDS=new Set(['pr_created','pr_updated','pr_merged','ci_run','main_commit']);
function validateGitHubActivityReceipt(value){
  if(!value||typeof value!=='object'||Array.isArray(value)
      ||value.schema!=='zcloud-github-activity-readonly-v1'
      ||value.feed_complete!==false||value.authorizes_merge!==false
      ||value.authorizes_repair!==false||value.authorizes_deploy!==false
      ||!Array.isArray(value.events)||value.events.length>200
      ||!Array.isArray(value.coverage)||value.coverage.length>8
      ||!Array.isArray(value.requested_projects)||value.requested_projects.length>8
      ||!Number.isFinite(Date.parse(value.observed_at)))throw Error('Unrecognized or unsafe read-only receipt');
  const observed=Date.parse(value.observed_at);
  if(observed>Date.now()+120000)throw Error('Snapshot time is in the future');
  const projects=new Set((DATA?.projects||[]).map(p=>p.id));
  const sourceByProject=new Map(),coverage=[];
  for(const row of value.coverage){
    if(!row||typeof row!=='object'||typeof row.project!=='string'
       ||!projects.has(row.project)||sourceByProject.has(row.project)
       ||typeof row.repository!=='string'
       ||!/^Zennay\/[A-Za-z0-9_.-]{1,100}$/.test(row.repository))throw Error('Unexpected project or repository');
    sourceByProject.set(row.project,row.repository);
    coverage.push({project:row.project,status:['partial_sources','partial_error','unavailable'].includes(row.status)?row.status:'unavailable'});
  }
  const seen=new Set(),events=[];
  for(const entry of value.events){
    if(!entry||typeof entry!=='object'||!GITHUB_RECEIPT_KINDS.has(entry.kind)
       ||typeof entry.id!=='string'||entry.id.length>256
       ||!sourceByProject.has(entry.project)
       ||!entry.id.startsWith('github:'+sourceByProject.get(entry.project)+':'+entry.kind+':')
       ||!Number.isFinite(Date.parse(entry.source_at))
       ||Date.parse(entry.source_at)>observed+120000
       ||seen.has(entry.id))throw Error('Malformed or duplicate activity event');
    seen.add(entry.id);
    const repository=sourceByProject.get(entry.project);
    let url='',description='';
    if(entry.kind==='main_commit'){
      if(typeof entry.sha!=='string'||!/^[a-f0-9]{40}$/.test(entry.sha)
          ||entry.id!=='github:'+repository+':main_commit:'+entry.sha
          ||entry.main_merged!==true)throw Error('Invalid main commit');
      url='https://github.com/'+repository+'/commit/'+entry.sha;
      description='Main commit '+entry.sha.slice(0,12);
    }else if(entry.kind==='ci_run'){
      if(!Number.isSafeInteger(entry.run_id)||entry.run_id<=0
          ||entry.id!=='github:'+repository+':ci_run:'+entry.run_id)throw Error('Invalid CI run');
      url='https://github.com/'+repository+'/actions/runs/'+entry.run_id;
      description='Actions #'+entry.run_id+' · '+(['success','failure','cancelled','skipped','timed_out'].includes(entry.conclusion)?entry.conclusion:'not completed');
    }else{
      if(!Number.isSafeInteger(entry.pr)||entry.pr<=0
          ||entry.id!=='github:'+repository+':'+entry.kind+':'+entry.pr
          ||entry.main_merged!==false)throw Error('Invalid PR event');
      url='https://github.com/'+repository+'/pull/'+entry.pr;
      description='PR #'+entry.pr+' · '+entry.kind.replaceAll('_',' ');
      if(typeof entry.pr_head_sha==='string'&&/^[a-f0-9]{40}$/.test(entry.pr_head_sha))
        description+=' · '+entry.pr_head_sha.slice(0,12);
      if(entry.kind==='pr_merged')
        description+=' · reported target only, main not verified';
    }
    events.push({project:entry.project,id:entry.id,kind:entry.kind,url,description,
      ts:entry.source_at,firstSeen:entry.new_since_previous===true});
  }
  if(coverage.length===0)throw Error('Snapshot contains no canonical project coverage');
  return {observedAt:value.observed_at,coverage,events};
}
// Optional browser-only public GitHub refresh. Never uses the zCloud API token,
// scheduled workers, a write endpoint, or a background polling loop.
let PUBLIC_GITHUB_BUSY=false;
const PUBLIC_GITHUB_LAST_FETCH=new Map();
async function publicGitHubGET(repository,suffix){
  if(!/^[A-Za-z0-9_.-]{1,100}$/.test(repository))throw Error('Invalid canonical repository');
  const url='https://api.github.com/repos/Zennay/'+repository+'/'+suffix;
  const response=await fetch(url,{method:'GET',credentials:'omit',redirect:'error',cache:'no-store',
    headers:{'Accept':'application/vnd.github+json'},signal:AbortSignal.timeout(8000)});
  if(!response.ok)throw Error(response.status===403||response.status===429?'Rate limited or inaccessible':'GitHub source unavailable');
  if(new URL(response.url).origin!=='https://api.github.com')throw Error('Unexpected GitHub origin');
  const raw=await response.text();
  if(raw.length>600000)throw Error('GitHub source too large');
  return JSON.parse(raw);
}
async function refreshPublicGitHubEvidence(){
  if(PUBLIC_GITHUB_BUSY)return;
  const selected=(DATA?.projects||[]).find(p=>p.id===filter);
  if(!selected){IMPORTED_GITHUB_ERROR='Select one project before fetching public GitHub evidence.';render();return}
  const m=/^https:\/\/github\.com\/Zennay\/([A-Za-z0-9_.-]{1,100})\/?$/.exec(selected.repo_url||'');
  if(!m){IMPORTED_GITHUB_ERROR='This project has no canonical public GitHub repository.';render();return}
  const repo=m[1],projectId=selected.id;
  if(Date.now()-(PUBLIC_GITHUB_LAST_FETCH.get(projectId)||0)<120000){
    IMPORTED_GITHUB_ERROR='Public GitHub may be rate limited. Recheck is limited to once per project every two minutes.';
    render();return;
  }
  PUBLIC_GITHUB_BUSY=true;PUBLIC_GITHUB_LAST_FETCH.set(projectId,Date.now());
  IMPORTED_GITHUB_ERROR='Reading public PR, CI and main-commit evidence…';render();
  const suffixes=['pulls?state=all&sort=updated&direction=desc&per_page=20',
    'actions/runs?per_page=20','commits?sha=main&per_page=15'];
  const outcomes=await Promise.allSettled(suffixes.map(suffix=>publicGitHubGET(repo,suffix)));
  const events=[],seen=new Set();
  const push=(kind,ts,url,description)=>{
    const parsed=Date.parse(ts||'');
    if(!Number.isFinite(parsed)||parsed>Date.now()+120000||parsed<0||seen.has(kind+':'+url))return;
    seen.add(kind+':'+url);
    events.push({project:projectId,kind,ts:new Date(parsed).toISOString(),url,description,firstSeen:false});
  };
  try{
    const prs=outcomes[0].status==='fulfilled'&&Array.isArray(outcomes[0].value)?outcomes[0].value:[];
    for(const pr of prs.slice(0,20)){
      if(!Number.isSafeInteger(pr.number)||pr.number<=0||!pr.head||typeof pr.head.sha!=='string'||
        !/^[a-f0-9]{40}$/i.test(pr.head.sha))continue;
      const link='https://github.com/Zennay/'+repo+'/pull/'+pr.number;
      const merged=typeof pr.merged_at==='string'&&Number.isFinite(Date.parse(pr.merged_at));
      const kind=merged?'pr_merged':pr.created_at===pr.updated_at?'pr_created':'pr_updated';
      const sourceTime=merged?pr.merged_at:(pr.updated_at||pr.created_at);
      push(kind,sourceTime,link,'PR #'+pr.number+' · '+(merged?'GitHub reports merged':kind==='pr_created'?'Created':'Updated')+
        ' · head '+pr.head.sha.slice(0,10)+(merged?' · deploy not verified':''));
    }
    const runs=outcomes[1].status==='fulfilled'&&Array.isArray(outcomes[1].value?.workflow_runs)?outcomes[1].value.workflow_runs:[];
    for(const run of runs.slice(0,20)){
      if(!Number.isSafeInteger(run.id)||run.id<=0)continue;
      const status=['queued','in_progress','completed','waiting','pending','requested'].includes(run.status)?run.status:'unknown';
      const conclusion=['success','failure','cancelled','skipped','timed_out','neutral','action_required'].includes(run.conclusion)?run.conclusion:'not_final';
      push('ci_run',run.updated_at||run.created_at,'https://github.com/Zennay/'+repo+'/actions/runs/'+run.id,
        'GitHub Actions #'+run.id+' · '+status+' / '+conclusion+' · no deploy claim');
    }
    const commits=outcomes[2].status==='fulfilled'&&Array.isArray(outcomes[2].value)?outcomes[2].value:[];
    for(const commit of commits.slice(0,15)){
      if(typeof commit.sha!=='string'||!/^[a-f0-9]{40}$/i.test(commit.sha))continue;
      push('main_commit',commit.commit?.committer?.date||commit.commit?.author?.date,
        'https://github.com/Zennay/'+repo+'/commit/'+commit.sha,
        'Commit on GitHub main history · '+commit.sha.slice(0,10)+' · deploy not verified');
    }
    const good=outcomes.filter((o,i)=>o.status==='fulfilled'&&
      (i===1?Array.isArray(o.value?.workflow_runs):Array.isArray(o.value))).length;
    IMPORTED_GITHUB_ACTIVITY={observedAt:new Date().toISOString(),events:events.sort((a,b)=>Date.parse(b.ts)-Date.parse(a.ts)).slice(0,90),
      coverage:[{project:projectId,status:good===3?'partial_sources':good?'partial_error':'unavailable'}]};
    IMPORTED_GITHUB_ERROR=(good<3?'Incomplete public GitHub sources ('+good+'/3 readable); ':'Public GitHub source sample ready; ')+
      'branch-only work and private repositories are not covered. Not a live deployment receipt.';
  }catch(_error){
    IMPORTED_GITHUB_ERROR='Public GitHub source could not be interpreted. No authorization or deployment inferred.';
  }finally{PUBLIC_GITHUB_BUSY=false;if(route==='activity')render()}
}

function githubActivityImportPanel(){
  const snapshot=IMPORTED_GITHUB_ACTIVITY;
  const visible=new Set((DATA?.projects||[]).map(p=>p.id));
  const rows=(snapshot?.events||[]).filter(a=>visible.has(a.project)&&(!filter||a.project===filter));
  const counts=snapshot?.coverage.filter(c=>visible.has(c.project)&&(!filter||c.project===filter))
    .map(c=>c.project+': '+c.status).join(' · ')||'No GitHub receipt loaded';
  const content=rows.slice(0,80).map(a=>'<div class="service-row"><div><small>'+esc(a.project)+' · '+esc(a.kind)+
    (a.firstSeen?' · absent in preceding bounded snapshot':'')+'</small><br><a target="_blank" rel="noopener noreferrer" href="'+
    esc(a.url)+'">'+esc(a.description)+'</a></div><small>'+esc(date(a.ts,true))+'</small></div>').join('');
  return '<div class="panel"><div class="panel-header"><div><h2>GitHub evidence · optional local import</h2>'+
    '<p class="panel-subtitle">PRs, CI and main commits from an operator-generated bounded read-only snapshot. Not automatically synchronized.</p></div></div>'+
    '<button type="button" class="runner-action" data-github-public-refresh '+(PUBLIC_GITHUB_BUSY?'disabled aria-busy="true"':'')+'>Read public GitHub for selected project</button> '+
    '<span class="detail-note">Opt-in browser GET only · three bounded GitHub requests · no credentials or background sync.</span><br>'+ 
    '<label for="githubReceiptFile">Or import a JSON receipt created by the zCloud GitHub activity observer</label> '+
    '<input type="file" id="githubReceiptFile" accept=".json,application/json" aria-label="Import read-only GitHub activity JSON receipt">'+
    (snapshot?'<button type="button" class="runner-action" data-github-receipt-clear>Clear snapshot</button>':'')+
    '<p role="status" aria-live="polite">'+esc(IMPORTED_GITHUB_ERROR||(snapshot?'Imported observation '+date(snapshot.observedAt,true)+' · '+snapshot.events.length+' bounded events.':'No GitHub receipt imported. Local VPS events continue below.'))+'</p>'+
    (snapshot?'<p class="detail-note">Partial provider coverage: '+esc(counts)+
      '. A PR reported merged or a green CI run never proves deployment, active ownership or worker recovery.</p>'+
      (content?'<div class="service-list">'+content+'</div>':'<div class="empty">No GitHub events in this project-filtered snapshot.</div>')+
      (rows.length>80?'<p class="detail-note">Only 80 of the imported events are displayed.</p>':''):'')+
    '</div>';
}

// Existing zSentinel product issue-inbox receipts are independent of Activity
// events. Never infer a live worker lease, permission to merge or authorization
// to repair from a GitHub issue or a PR mention.
let IMPORTED_PRODUCT_ISSUES=null,IMPORTED_PRODUCT_ERROR='',IMPORTED_ISSUE_SHOW_ALL=false;
function validateProductIssueInbox(value){
  if(!value||typeof value!=='object'||Array.isArray(value)
      ||value.schema!=='zsentinel-product-issue-inbox-v1'
      ||value.actions_authorized!==false||value.active_claims_verified!==false
      ||value.queue_ownership_known!==false||value.fully_synchronized!==false
      ||!Array.isArray(value.items)||value.items.length>150
      ||!Array.isArray(value.coverage)||value.coverage.length>18
      ||!Number.isFinite(Date.parse(value.observed_at))
      ||Date.parse(value.observed_at)>Date.now()+120000)throw Error('Unrecognized or unsafe issue-inbox receipt');
  const visible=new Set((DATA?.projects||[]).map(p=>p.id)),covered=new Map();
  for(const item of value.coverage){
    if(!item||typeof item.project!=='string'||!visible.has(item.project)||covered.has(item.project)
       ||typeof item.repository!=='string'
       ||!/^Zennay\/[A-Za-z0-9_.-]{1,100}$/.test(item.repository))throw Error('Unknown issue-inbox project');
    covered.set(item.project,item.repository);
  }
  if(!covered.size)throw Error('No canonical issue coverage');
  const coverage=[...value.coverage].map(item=>({
    project:item.project,
    status:['partial','degraded','unavailable'].includes(item.status)?item.status:'unavailable',
    oldest:typeof item.oldest_window==='string'?item.oldest_window.slice(0,70):'unknown',
    recent:typeof item.recent_window==='string'?item.recent_window.slice(0,70):'unknown',
    prs:typeof item.open_pr_window==='string'?item.open_pr_window.slice(0,70):'unknown'
  }));
  const seen=new Set(),issues=[];
  for(const item of value.items){
    if(!item||!covered.has(item.project)||item.repository!==covered.get(item.project)
       ||!Number.isSafeInteger(item.number)||item.number<=0
       ||item.id!==item.repository+'#'+item.number||seen.has(item.id)
       ||typeof item.title!=='string'||item.title.length>180
       ||!['P0','P1','P2','Unspecified'].includes(item.priority)
       ||item.qa_only!==false||item.active_claim!=='not_verified'
       ||!Array.isArray(item.related_open_prs)||item.related_open_prs.length>3)throw Error('Invalid issue-inbox entry');
    seen.add(item.id);
    const prs=[];
    for(const pr of item.related_open_prs){
      if(!pr||!Number.isSafeInteger(pr.number)||pr.number<=0
          ||typeof pr.draft!=='boolean'||pr.active_claim!=='not_verified'
          ||!['text_reference_only','closing_keyword_only'].includes(pr.relationship))
        throw Error('Invalid issue-to-PR reference');
      prs.push({number:pr.number,draft:pr.draft,relationship:pr.relationship,
        url:'https://github.com/'+item.repository+'/pull/'+pr.number});
    }
    issues.push({project:item.project,number:item.number,priority:item.priority,
      title:item.title,url:'https://github.com/'+item.repository+'/issues/'+item.number,
      prs});
  }
  return {observedAt:value.observed_at,issues,coverage,partial:true};
}
// Optional read-only public issue lookup for one explicitly chosen project.
// The result is a bounded advisory sample, never queue authorization.
let PUBLIC_ISSUES_BUSY=false;
const PUBLIC_ISSUES_LAST_FETCH=new Map();
async function refreshPublicProductIssues(){
  if(PUBLIC_ISSUES_BUSY)return;
  const selected=(DATA?.projects||[]).find(p=>p.id===filter);
  if(!selected){IMPORTED_PRODUCT_ERROR='Select one project before fetching public product issues.';render();return}
  const match=/^https:\/\/github\.com\/Zennay\/([A-Za-z0-9_.-]{1,100})\/?$/.exec(selected.repo_url||'');
  if(!match){IMPORTED_PRODUCT_ERROR='The selected project has no canonical public GitHub repository.';render();return}
  const projectId=selected.id,repository=match[1];
  if(Date.now()-(PUBLIC_ISSUES_LAST_FETCH.get(projectId)||0)<120000){
    IMPORTED_PRODUCT_ERROR='Recent public issue lookup is cached; retry after two minutes.';render();return;
  }
  PUBLIC_ISSUES_BUSY=true;PUBLIC_ISSUES_LAST_FETCH.set(projectId,Date.now());
  IMPORTED_PRODUCT_ERROR='Reading bounded public issues and existing PR references…';render();
  const tasks=['issues?state=open&sort=created&direction=asc&per_page=30',
    'issues?state=open&sort=updated&direction=desc&per_page=30',
    'pulls?state=open&sort=updated&direction=desc&per_page=12'];
  const outcomes=await Promise.allSettled(tasks.map(path=>publicGitHubGET(repository,path)));
  try{
    const issues=new Map();
    for(let source=0;source<2;source++){
      const rows=outcomes[source].status==='fulfilled'&&Array.isArray(outcomes[source].value)?outcomes[source].value:[];
      for(const issue of rows.slice(0,30)){
        if(!issue||issue.pull_request||!Number.isSafeInteger(issue.number)||issue.number<=0||
          typeof issue.title!=='string'||issue.title.length>1000)continue;
        const labels=Array.isArray(issue.labels)?issue.labels.map(label=>String(label?.name||'').toLowerCase()):[];
        const title=issue.title;
        const prefix=/^\s*(?:\[(P[01])\]|(P[01])(?=\s|:|-)|(?:[A-Za-z][A-Za-z -]{0,38}\s+)(P[01])\s*:)/i.exec(title);
        const marked=prefix?prefix.slice(1).find(Boolean)?.toUpperCase():null;
        const priority=labels.includes('p0')||labels.includes('priority:p0')||marked==='P0'?'P0':
          labels.includes('p1')||labels.includes('priority:p1')||marked==='P1'?'P1':
          labels.includes('p2')||labels.includes('priority:p2')?'P2':'Unspecified';
        const qa=/^\s*(?:P[0-3]\s*[:\-]?\s*)?(?:QA|tests?|regression|quality[ -]validation)\b/i.test(title)||
          labels.some(label=>['qa','quality-validation','qa-validation','regression','tests-only'].includes(label));
        if(qa||issue.state!=='open')continue;
        issues.set(issue.number,{project:projectId,number:issue.number,priority,title:title.slice(0,180),
          url:'https://github.com/Zennay/'+repository+'/issues/'+issue.number,prs:[],
          observed:Date.parse(issue.updated_at||issue.created_at)||0});
      }
    }
    const candidates=outcomes[2].status==='fulfilled'&&Array.isArray(outcomes[2].value)?outcomes[2].value:[];
    for(const pr of candidates.slice(0,12)){
      if(!pr||!Number.isSafeInteger(pr.number)||pr.number<=0||typeof pr.body!=='string'||pr.body.length>32000)continue;
      const refs=[...pr.body.matchAll(/(?<![A-Za-z0-9/_])#([1-9][0-9]{0,7})\b/g)];
      const closing=new Set([...pr.body.matchAll(/\b(?:fix(?:es)?|clos(?:e|es)|resolv(?:e|es))\s+#([1-9][0-9]{0,7})\b/ig)]
        .map(match=>Number(match[1])));
      for(const ref of refs.slice(0,40)){
        const item=issues.get(Number(ref[1]));
        if(!item||item.prs.length>=3||item.prs.some(p=>p.number===pr.number))continue;
        item.prs.push({number:pr.number,draft:pr.draft===true,
          relationship:closing.has(Number(ref[1]))?'closing_keyword_only':'text_reference_only',
          url:'https://github.com/Zennay/'+repository+'/pull/'+pr.number});
      }
    }
    const ranked={P0:0,P1:1,P2:2,Unspecified:3};
    const readable=outcomes.filter(o=>o.status==='fulfilled'&&Array.isArray(o.value)).length;
    IMPORTED_PRODUCT_ISSUES={observedAt:new Date().toISOString(),partial:true,
      issues:[...issues.values()].sort((a,b)=>ranked[a.priority]-ranked[b.priority]||b.observed-a.observed).slice(0,60),
      coverage:[{project:projectId,status:readable===3?'partial':readable?'degraded':'unavailable',
        oldest:outcomes[0].status==='fulfilled'?'bounded 30':'unavailable',
        recent:outcomes[1].status==='fulfilled'?'bounded 30':'unavailable',
        prs:outcomes[2].status==='fulfilled'?'bounded 12':'unavailable'}]};
    IMPORTED_ISSUE_SHOW_ALL=false;
    IMPORTED_PRODUCT_ERROR=(readable===3?'Public issue sample loaded. ':'Partial public issue source ('+readable+'/3 readable). ')+
      'These references do not verify fixes, worker ownership or queue claims.';
  }catch(_error){
    IMPORTED_PRODUCT_ERROR='Public issue source unavailable or malformed. No queue claim taken.';
  }finally{PUBLIC_ISSUES_BUSY=false;if(route==='activity')render()}
}

function productIssueInboxPanel(){
  const snapshot=IMPORTED_PRODUCT_ISSUES;
  const visible=new Set((DATA?.projects||[]).map(p=>p.id));
  const rows=(snapshot?.issues||[]).filter(item=>visible.has(item.project)&&(!filter||item.project===filter)&&
    (IMPORTED_ISSUE_SHOW_ALL||item.priority==='P0'||item.priority==='P1'));
  const hiddenLowerPriority=(snapshot?.issues||[]).filter(item=>visible.has(item.project)&&(!filter||item.project===filter)
    &&item.priority!=='P0'&&item.priority!=='P1').length;
  const coverageText=(snapshot?.coverage||[]).filter(item=>visible.has(item.project)&&(!filter||item.project===filter))
    .map(item=>item.project+' · '+item.status+' (oldest '+item.oldest+', recent '+item.recent+', PRs '+item.prs+')').join(' · ');
  const html=rows.slice(0,50).map(item=>'<div class="service-row"><div><strong>'+esc(item.priority)+
    ' · '+esc(item.project)+'</strong> <a target="_blank" rel="noopener noreferrer" href="'+esc(item.url)+'">'+
    esc(item.title)+'</a><small> · open issue #'+item.number+'</small>'+
    (item.prs.length?'<div class="detail-note">Referenced open PRs (not proven fixes or active claims): '+
      item.prs.map(pr=>'<a href="'+esc(pr.url)+'" target="_blank" rel="noopener noreferrer">#'+pr.number+
      (pr.draft?' draft':'')+(pr.relationship==='closing_keyword_only'?' closing keyword':' mention')+'</a>').join(' · ')+'</div>':'')+
    '</div></div>').join('');
  return '<div class="panel"><div class="panel-header"><div><h2>Product issue inbox · optional local import</h2>'+
    '<p class="panel-subtitle">Existing GitHub P0/P1 issues from the zSentinel bounded inbox, never an automated claim or task assignment.</p></div></div>'+
    '<button type="button" class="runner-action" data-public-issues-refresh '+(PUBLIC_ISSUES_BUSY?'disabled aria-busy="true"':'')+'>Read public P0/P1 issues for selected project</button> '+
    '<span class="detail-note">Opt-in public GitHub GET only · bounded sample · no credentials or queue actions.</span><br>'+ 
    '<label for="productIssueReceiptFile">Or import a JSON product issue-inbox receipt</label> '+
    '<input type="file" id="productIssueReceiptFile" accept=".json,application/json" aria-label="Import bounded product issue inbox">'+
    (snapshot?'<button type="button" class="runner-action" data-product-issues-clear>Clear issues</button> '+
      (hiddenLowerPriority?'<button type="button" class="runner-action" data-product-issues-toggle>'+(
        IMPORTED_ISSUE_SHOW_ALL?'Focus P0/P1 only':'Include '+hiddenLowerPriority+' lower-priority sampled issues')+'</button>':''):'')+
    '<p role="status" aria-live="polite">'+esc(IMPORTED_PRODUCT_ERROR||
      (snapshot?'Imported bounded issue sample · '+date(snapshot.observedAt,true)+'.':'No product issue receipt imported.'))+'</p>'+
    (snapshot?'<p class="detail-note">Source coverage: '+esc(coverageText)+' · Bounded sources may be missing, truncated or rate-limited. Existing PR references do not prove active ownership, fixes or deploy approval.</p>'+
      (html?'<div class="service-list">'+html+'</div>':'<div class="empty">No visible issues in this bounded project filter.</div>')+
      (rows.length>50?'<p class="detail-note">Only 50 of the sampled issues are displayed.</p>':''):'')+'</div>';
}

// Local control-plane progress is a separate, opt-in evidence source.
// It never proves live text generation, lease identity or a deployed worker.
let WORKER_PROGRESS_SLOT=1,WORKER_PROGRESS_DATA=null,WORKER_PROGRESS_ERROR='',WORKER_PROGRESS_BUSY=false;
async function refreshWorkerProgress(){
  if(WORKER_PROGRESS_BUSY)return;
  const projectId=filter;
  const slot=WORKER_PROGRESS_SLOT;
  if(!projectId||(DATA?.projects||[]).every(p=>p.id!==projectId)){
    WORKER_PROGRESS_ERROR='Select one project to inspect a worker slot.';render();return;
  }
  if(!Number.isInteger(slot)||slot<1||slot>8){
    WORKER_PROGRESS_ERROR='Select a worker slot from 1 to 8.';render();return;
  }
  WORKER_PROGRESS_BUSY=true;WORKER_PROGRESS_ERROR='Reading local worker observations…';render();
  try{
    const report=await api('/api/worker-progress-history?project='+encodeURIComponent(projectId)+'&slot='+slot+'&limit=24',10000);
    if(!report||report.schema!=='zcloud-worker-progress-readonly-v1'||report.project!==projectId||
      report.worker_slot!==slot||report.claim_bound!==false||report.claim_epoch_verified!==false||
      report.worker_resumed_verified!==false||report.browser_progress_is_independent_proof!==false||
      !Array.isArray(report.recent_events)||report.recent_events.length>40){
      throw Error('Unexpected worker progress source');
    }
    WORKER_PROGRESS_DATA=report;
    WORKER_PROGRESS_ERROR=report.available===true?'Bounded local browser observations loaded; claim and deployment unverified.':
      'The local worker telemetry database is not available.';
  }catch(_error){
    WORKER_PROGRESS_DATA=null;
    WORKER_PROGRESS_ERROR='Worker progress API unavailable, unauthorized or not yet deployed. No live worker activity can be inferred.';
  }finally{WORKER_PROGRESS_BUSY=false;if(route==='activity')render()}
}
function workerProgressPanel(){
  const report=WORKER_PROGRESS_DATA;
  const scoped=report&&report.project===filter&&report.worker_slot===WORKER_PROGRESS_SLOT?report:null;
  const facts=scoped?[
    ['Last browser event',scoped.latest_event],['Latest prompt',scoped.latest_prompt],
    ['Latest progress',scoped.latest_progress],['Latest completion',scoped.latest_completion]
  ]:[];
  const cards=facts.map(([label,item])=>'<div class="service-row"><div><strong>'+esc(label)+'</strong><br><small>'+
    esc(item?.kind||'No observation')+' · '+esc(item?.clock?.utc||'No UTC timestamp')+' · '+
    esc(item?.clock?.status||'unavailable')+'</small></div></div>').join('');
  const history=(scoped?.recent_events||[]).slice(0,20).filter(row=>Number.isSafeInteger(row?.id)&&row.id>0).map(row=>
    '<div class="service-row"><div><small>Event #'+row.id+' · '+esc(row.kind||'other_browser_event')+
    ' · '+esc(row.clock?.utc||'Timestamp unavailable')+' · '+esc(row.clock?.status||'invalid')+
    '</small></div><small>chars reported: '+(Number.isSafeInteger(row.reported_assistant_chars)&&row.reported_assistant_chars>=0?
      row.reported_assistant_chars:'unknown')+'</small></div>').join('');
  return '<section class="panel"><div class="panel-header"><div><h2>Worker progress · on-demand local view</h2>'+
    '<p class="panel-subtitle">Inspect a single worker without changing its queue, leases, or runtime state.</p></div></div>'+
    '<label for="workerProgressSlot">Worker slot (1–8)</label> <input id="workerProgressSlot" type="number" min="1" max="8" step="1" inputmode="numeric" value="'+WORKER_PROGRESS_SLOT+'"> '+
    '<button type="button" class="runner-action" data-worker-progress-refresh '+(WORKER_PROGRESS_BUSY?'disabled aria-busy="true"':'')+'>Read progress</button>'+
    '<p role="status" aria-live="polite">'+esc(WORKER_PROGRESS_ERROR||(scoped?'Browser observations read.':'Choose a project and press Read progress.'))+'</p>'+
    '<p class="detail-note">Browser-reported events are not independent proof of generated text. Queue claim, claim epoch, worker resumption and deployment remain unverified.</p>'+
    (scoped?'<div class="service-list">'+cards+(history?'<details data-disclosure="worker-progress-events"><summary>Recent browser event history</summary>'+history+'</details>':'')+'</div>':'')+'</section>';
}

/* Product-only admission is a read-only, explicitly requested preview.
   It never dispatches, changes a claim, or treats a candidate as authorized. */
let DELIVERY_QUEUE_DATA=null,DELIVERY_QUEUE_ERROR='',DELIVERY_QUEUE_BUSY=false,DELIVERY_QUEUE_OBSERVED='';
async function refreshDeliveryQueue(){
  if(DELIVERY_QUEUE_BUSY)return;
  DELIVERY_QUEUE_BUSY=true;DELIVERY_QUEUE_ERROR='';DELIVERY_QUEUE_DATA=null;
  if(route==='activity')render();
  try{
    const response=await fetch('/api/portfolio-queue',{cache:'no-store',signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw new Error(response.status===403?'Admin access required':'HTTP '+response.status);
    const body=await response.json(),p=body?.delivery_policy;
    if(!p||p.policy!=='product-delivery-2026-10-17'||p.not_claimed_or_authorized!==true||
       p.admission_scope_lease_and_capacity_not_verified!==true||
       !Number.isSafeInteger(p.queued_qa_only_held)||p.queued_qa_only_held<0||
       !Number.isSafeInteger(p.queued_product_candidates)||p.queued_product_candidates<0||
       !Array.isArray(p.candidates)||p.candidates.length>8||
       !p.candidates.every(row=>row&&typeof row.project_id==='string'&&
         typeof row.queue_id==='string'&&typeof row.priority==='string')){
      throw new Error('Incomplete admission receipt');
    }
    DELIVERY_QUEUE_DATA=p;DELIVERY_QUEUE_OBSERVED=String(body.time||'');
  }catch(error){
    DELIVERY_QUEUE_DATA=null;DELIVERY_QUEUE_OBSERVED='';
    DELIVERY_QUEUE_ERROR='Admission preview unavailable: '+String(error?.message||'request failed');
  }finally{DELIVERY_QUEUE_BUSY=false;if(route==='activity')render()}
}
function productQueueAdmissionPanel(){
  const p=DELIVERY_QUEUE_DATA;
  const rows=p?.candidates||[];
  const candidates=filter?rows.filter(row=>row.project_id===filter):rows;
  const rendered=candidates.map(row=>'<div class="service-row"><div><strong>'+esc(row.priority)+
    ' · '+esc(row.project_id)+'</strong><small> Queue '+esc(row.queue_id)+
    '</small></div><small>Not claimed</small></div>').join('');
  const info=p?('<p class="detail-note" role="status">Snapshot '+esc(DELIVERY_QUEUE_OBSERVED||'unknown time')+
    ' · '+p.queued_product_candidates+' queued product candidates · '+p.queued_qa_only_held+
    ' QA-only items held for new admission (global counts; up to eight candidate IDs shown).</p>'+
    (rendered?'<div class="service-list">'+rendered+'</div>':
      '<p class="detail-note">No candidates from this project in the bounded preview. This does not mean the queue is empty.</p>')):
    '<p class="detail-note">Not loaded. Read the authorized queue only when needed.</p>';
  return '<section class="panel"><div class="panel-header"><div><h2>Product delivery queue</h2>'+
    '<p class="panel-subtitle">New product work only. Active worker claims remain protected.</p></div>'+
    '<button type="button" class="runner-action" data-delivery-queue-refresh '+
    (DELIVERY_QUEUE_BUSY?'disabled aria-busy="true"':'')+'>'+
    (DELIVERY_QUEUE_BUSY?'Reading…':'Read admission')+'</button></div>'+
    (DELIVERY_QUEUE_ERROR?'<p class="detail-note" role="alert">'+esc(DELIVERY_QUEUE_ERROR)+'</p>':'')+
    info+'<p class="detail-note">Preview only: eligible is not an authorized, conflict-free or memory-admitted claim. Existing CI/review and runner gates remain required.</p></section>';
}

/* Optional read-only keyset pagination of LOCAL SQLite activity only.
   Cursor scope follows the selected project, not an active worker/claim. */
let PAGED_LOCAL_ACTIVITY={scope:null,events:[],cursor:null,hasRead:false,busy:false,error:'',pages:0};
function pagedLocalActivityScope(){return filter||''}
async function readPagedLocalActivity(){
  if(PAGED_LOCAL_ACTIVITY.busy)return;
  const scope=pagedLocalActivityScope();
  if(PAGED_LOCAL_ACTIVITY.scope!==scope){
    PAGED_LOCAL_ACTIVITY={scope,events:[],cursor:null,hasRead:false,busy:false,error:'',pages:0};
  }
  if(PAGED_LOCAL_ACTIVITY.hasRead&&!PAGED_LOCAL_ACTIVITY.cursor)return;
  const state=PAGED_LOCAL_ACTIVITY,baseCursor=state.cursor,callVersion=routeVersion;
  state.busy=true;state.error='';if(route==='activity')render();
  try{
    const params=new URLSearchParams({limit:'40'});
    if(scope)params.set('project',scope);
    if(baseCursor)params.set('cursor',JSON.stringify(baseCursor));
    const response=await fetch('/api/activity-page?'+params.toString(),{
      cache:'no-store',signal:AbortSignal.timeout(10000)
    });
    if(!response.ok)throw new Error(response.status===403?'Access denied':'HTTP '+response.status);
    const page=await response.json();
    const registry=new Set((DATA?.projects||[]).map(p=>p.id));
    if(!page||!Array.isArray(page.events)||page.events.length>40||
       !page.coverage||page.coverage.local_sqlite!=='read_only'||
       page.coverage.github_pr_branch_ci!=='not_synchronized'||
       page.coverage.feed_complete!==false||
       !page.events.every(ev=>ev&&typeof ev.id==='string'&&ev.id.length<=256&&
         typeof ev.ts==='string'&&ev.ts.length<=64&&typeof ev.kind==='string'&&
         typeof ev.project==='string'&&registry.has(ev.project)&&(!scope||ev.project===scope))||
       (page.next_cursor!==null&&
         (!page.next_cursor||typeof page.next_cursor!=='object'||Array.isArray(page.next_cursor)||
          Object.keys(page.next_cursor).some(key=>!registry.has(key)||Boolean(scope&&key!==scope))))){
      throw new Error('Incomplete or cross-project local history page');
    }
    if(routeVersion!==callVersion||pagedLocalActivityScope()!==scope)return;
    const seen=new Set(state.events.map(ev=>ev.project+'|'+ev.id));
    for(const ev of page.events){
      const key=ev.project+'|'+ev.id;
      if(!seen.has(key)){state.events.push(ev);seen.add(key)}
    }
    state.events=state.events.slice(0,400);
    state.pages+=1;state.hasRead=true;
    state.cursor=state.events.length>=400?null:page.next_cursor;
    if(state.cursor&&JSON.stringify(state.cursor)===JSON.stringify(baseCursor))
      throw new Error('Local history cursor did not advance');
  }catch(error){
    if(routeVersion===callVersion&&pagedLocalActivityScope()===scope)
      state.error='Local history unavailable: '+String(error?.message||'request failed');
  }finally{state.busy=false;if(route==='activity'&&pagedLocalActivityScope()===scope)render()}
}
function pagedLocalActivityPanel(){
  const state=PAGED_LOCAL_ACTIVITY.scope===pagedLocalActivityScope()?PAGED_LOCAL_ACTIVITY:null;
  const rows=(state?.events||[]).map(ev=>'<div class="service-row"><div><strong>'+
    esc(ev.project)+'</strong> · '+esc(ev.kind)+
    '<br><small>'+esc(ev.ts)+'</small></div></div>').join('');
  const canLoad=!state?.hasRead||!!state?.cursor;
  const current=state?.events.length||0;
  return '<section class="panel"><div class="panel-header"><div><h2>Older local history</h2>'+
    '<p class="panel-subtitle">Read older VPS events, fairly across projects; GitHub PR/CI is not synchronized.</p></div>'+
    '<button type="button" class="runner-action" data-local-activity-next '+
    ((state?.busy||!canLoad)?'disabled aria-busy="'+(state?.busy?'true':'false')+'"':'')+'>'+
    (state?.busy?'Reading…':!state?.hasRead?'Read history':state?.cursor?'Load older':'End of local pages')+
    '</button></div>'+
    (state?.error?'<p class="detail-note" role="alert">'+esc(state.error)+'</p>':'')+
    (state?.hasRead?'<p class="detail-note" role="status">'+current+' distinct local events on '+state.pages+
      ' pages. This does not verify merged code or live worker recovery.</p>':
      '<p class="detail-note">History loads only when requested. The initial activity list stays unchanged.</p>')+
    (rows?'<div class="service-list">'+rows+'</div>':'')+
    '<p class="detail-note">Read-only cursor limited to 400 displayed events. Newer arrivals may change later pages. No background sync.</p></section>';
}

/* zSentinel issue-first delivery board: a joined READ VIEW, never an
   automatic owner/approval/CI or runner authorization decision. */
let ZSENTINEL_SOURCES_BUSY=false;
async function readZSentinelSources(){
  if(ZSENTINEL_SOURCES_BUSY)return;
  if(!(DATA?.projects||[]).some(project=>project.id===filter)){
    IMPORTED_PRODUCT_ERROR='Select one canonical project for bounded GitHub discovery.';
    render();return;
  }
  ZSENTINEL_SOURCES_BUSY=true;
  if(route==='activity')render();
  try{
    // GitHub public issue/PR and queue evidence come from different independent
    // sources. Failures stay local to their component; never imply empty work.
    await Promise.allSettled([
      refreshPublicProductIssues(),
      refreshPublicGitHubEvidence(),
      refreshDeliveryQueue(),
    ]);
  }finally{ZSENTINEL_SOURCES_BUSY=false;if(route==='activity')render()}
}
function zSentinelDeliveryBoard(){
  const ids=new Set((DATA?.projects||[]).map(project=>project.id));
  const snapshot=IMPORTED_PRODUCT_ISSUES;
  const admitted=DELIVERY_QUEUE_DATA;
  const github=IMPORTED_GITHUB_ACTIVITY;
  const focus=filter;
  const relevant=(snapshot?.issues||[]).filter(issue=>
    ids.has(issue.project)&&(!focus||issue.project===focus)
    &&(issue.priority==='P0'||issue.priority==='P1'));
  const priorityValue={P0:0,P1:1};
  relevant.sort((a,b)=>(priorityValue[a.priority]-priorityValue[b.priority])||
    a.project.localeCompare(b.project)||a.number-b.number);
  const candidates=(admitted?.candidates||[]).filter(item=>
    ids.has(item.project_id)&&(!focus||item.project_id===focus));
  const coveredIssues=(snapshot?.coverage||[]).filter(item=>
    ids.has(item.project)&&(!focus||item.project===focus));
  const coveredGitHub=(github?.coverage||[]).filter(item=>
    ids.has(item.project)&&(!focus||item.project===focus));
  const formatted=relevant.slice(0,24).map(issue=>{
    const refs=issue.prs.slice(0,3).map(pr=>
      '<a target="_blank" rel="noopener noreferrer" href="'+esc(pr.url)+'">PR #'+pr.number+
      (pr.draft?' (draft)':'')+'</a> <small>'+
      (pr.relationship==='closing_keyword_only'?'closing text, unverified':'mentioned, unverified')+
      '</small>').join(' · ');
    return '<div class="service-row"><div><strong>'+esc(issue.priority)+
      ' · '+esc(issue.project)+'</strong><br><a target="_blank" rel="noopener noreferrer" href="'+
      esc(issue.url)+'">'+esc(issue.title)+'</a><br><small>GitHub #'+issue.number+
      ' · open when observed · claim unverified</small>'+
      (refs?'<div class="detail-note">Existing PR references: '+refs+'</div>':
       '<div class="detail-note">No linked open PR in sampled window; ownership unknown.</div>')+
      '</div></div>';
  }).join('');
  const queueRows=candidates.slice(0,8).map(item=>
    '<div class="service-row"><div><strong>'+esc(item.priority)+' · '+
    esc(item.project_id)+'</strong><br><small>Queue '+esc(item.queue_id)+
    '</small></div><small>Candidate only · not claimed</small></div>').join('');
  const issueTime=snapshot?.observedAt&&Number.isFinite(Date.parse(snapshot.observedAt))?
    date(snapshot.observedAt,true):'Not observed';
  const queueTime=admitted?(DELIVERY_QUEUE_OBSERVED||'Unknown time'):'Not observed';
  const gitTime=github?.observedAt&&Number.isFinite(Date.parse(github.observedAt))?
    date(github.observedAt,true):'Not observed';
  const active=ZSENTINEL_SOURCES_BUSY||PUBLIC_ISSUES_BUSY||PUBLIC_GITHUB_BUSY||DELIVERY_QUEUE_BUSY;
  return '<section class="panel" id="zSentinelDeliveryBoard"><div class="panel-header">'+
    '<div><h2>zSentinel · product delivery board</h2>'+
    '<p class="panel-subtitle">P0/P1 issues first; existing PR references and queue candidates without false ownership claims.</p></div>'+
    '<button type="button" class="runner-action" data-zsentinel-source-read '+
    (active?'disabled aria-busy="true"':'')+'>'+
    (active?'Reading…':'Read product sources')+'</button></div>'+
    '<p class="detail-note" role="status">Issue source '+esc(issueTime)+
    ' · GitHub activity '+esc(gitTime)+' · Queue '+esc(queueTime)+
    '. Reads are manual, separate, bounded and may be partial or stale.</p>'+
    '<p class="detail-note">'+
    (focus?'Project: '+esc(focus)+'.':'Select a single project to refresh the sources; current snapshots can be viewed across projects.')+
    ' Issues sampled: '+coveredIssues.length+' project sources; GitHub activity: '+
    coveredGitHub.length+' project sources.</p>'+
    '<h3>Open high-impact product issues ('+relevant.length+' in available snapshot)</h3>'+
    (formatted?'<div class="service-list">'+formatted+'</div>':
     '<p class="detail-note">No P0/P1 issues in the available snapshot. This is NOT proof the repository has no open issues.</p>')+
    (relevant.length>24?'<p class="detail-note">Showing the first 24 prioritised issues.</p>':'')+
    '<h3>Queued product candidates ('+(admitted?admitted.queued_product_candidates:'unknown global count')+')</h3>'+
    (queueRows?'<div class="service-list">'+queueRows+'</div>':
     '<p class="detail-note">No candidate shown. Queue access, project coverage or remaining pages may be missing.</p>')+
    '<p class="detail-note">Queue and GitHub records are NOT joined as a verified claim. A PR mention does not confer ownership; green CI is not a merge; merge is not deploy. Active human approvals, runner health and release gates remain separate.</p>'+
    '</section>';
}

function activityPage(){
  const visible=new Set(DATA.projects.map(p=>p.id));
  const list=ACTIVITY.filter(a=>visible.has(a.project)&&(!filter||a.project===filter));
  const coverage=activityCoverageText();
  return `<div class="activity-page">${heading('WORKSPACE / ACTIVITY LOG','Local project activity.','Local VPS commits and milestones, not a complete GitHub activity history.',`<select class="filter-select" id="activityFilter" aria-label="Filter activity by project"><option value="">All projects</option>${DATA.projects.map(p=>`<option value="${esc(p.id)}" ${filter===p.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select>`)}
  ${zSentinelDeliveryBoard()}
  ${githubActivityImportPanel()}
  ${productIssueInboxPanel()}
  ${workerProgressPanel()}
  ${productQueueAdmissionPanel()}
  ${pagedLocalActivityPanel()}
  <div class="panel"><div class="panel-header"><div><h2>Local event coverage</h2><p class="panel-subtitle" role="status" aria-live="polite">${esc(coverage)}</p></div><button type="button" class="runner-action" data-activity-refresh ${ACTIVITY_COVERAGE.pending?'disabled aria-busy="true"':''}>Refresh project history</button></div>
  <div class="activity-list">${list.map(activityItem).join('')||'<div class="empty">No local events are available for this selection. GitHub activity may still exist.</div>'}</div>
  <div class="detail-note">Local event timestamps are not proof that a PR was merged or a CI run passed. The API returns at most 80 events per project; projects are interleaved to stop noisy projects hiding quieter work. Archived projects are hidden.</div></div></div>`;
}
function infrastructure(){const h=DATA.host,mem=h.memory.used/h.memory.total*100,disk=h.disk.used/h.disk.total*100;return heading('WORKSPACE / INFRASTRUCTURE','My VPS, up close.','Current load and trends, without interrupting my projects.')+`<div class="stat-grid">${stat('CPU usage',h.cpu==null?'—':num(h.cpu)+'<span>%</span>',h.cores+' vCPU · load '+num(h.load),'cpu')}${stat('Memory',num(mem)+'<span>%</span>',gb(h.memory.used)+' of '+gb(h.memory.total),'server')}${stat('Storage',num(disk)+'<span>%</span>',gb(h.disk.free)+' free on '+gb(h.disk.total),'disk')}${stat('Uptime',Math.floor(h.uptime/86400)+'<span>days</span>',Math.floor(h.uptime%86400/3600)+' hours since the last full day','clock')}</div><p class="infra-info">Live measurements every 15 seconds. History every five minutes, up to 24 hours in this chart.</p><div class="infra-grid"><div class="panel full-width"><div class="panel-header"><div><h2>Resource usage</h2><div class="panel-subtitle">Percentage · Amsterdam time</div></div><span class="tiny-tag">24 HOURS</span></div><div class="legend"><span style="--accent:#c2f970"><i class="legend-dot"></i>CPU</span><span style="--accent:#82b8ff"><i class="legend-dot"></i>Memory</span><span style="--accent:#a899ff"><i class="legend-dot"></i>Storage</span></div><div class="chart-wrap" id="hostChart"></div><div class="chart-detail" id="chartDetail">Select a data point for the exact measurement.</div></div>${DATA.projects.filter(p=>p.services.length).map(services).join('')}</div>`}
function chart(target,series){const el=$(target);if(!el)return;let points=series.flatMap(s=>s.data).filter(p=>p.value!=null&&Number.isFinite(Date.parse(p.date)));if(!points.length){el.innerHTML='<div class="empty">No measurements in this period.<br>New history is recorded every five minutes.</div>';return}const w=660,h=215,left=44,right=15,top=12,bottom=33,plotw=w-left-right,ploth=h-top-bottom;let lo=Math.min(...points.map(p=>Date.parse(p.date))),hi=Math.max(...points.map(p=>Date.parse(p.date)));const single=lo===hi;if(single){lo-=150000;hi+=150000}const x=t=>left+(Date.parse(t)-lo)/(hi-lo)*plotw,y=v=>top+(100-v)/100*ploth;let svg=`<svg class="chart-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" role="group" aria-label="Time chart with percentages. Select a data point for the date.">`;
for(const v of [0,25,50,75,100])svg+=`<line x1="${left}" x2="${w-right}" y1="${y(v)}" y2="${y(v)}" stroke="#2a303b" stroke-dasharray="3 5"/><text x="${left-10}" y="${y(v)+4}" text-anchor="end">${v}%</text>`;
const count=single?1:4;for(let i=0;i<count;i++){const t=single?(lo+hi)/2:lo+(hi-lo)*i/(count-1),xx=single?left+plotw/2:left+plotw*i/(count-1);const label=(hi-lo)<86400000?new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Amsterdam',hour:'2-digit',minute:'2-digit'}).format(t):date(t);svg+=`<text x="${xx}" y="${h-7}" text-anchor="${!single&&i===0?'start':!single&&i===count-1?'end':'middle'}">${label}</text>`}
for(const s of series){const data=s.data.filter(p=>p.value!=null&&Number.isFinite(Date.parse(p.date)));if(!data.length)continue;const path=data.map((p,i)=>`${i?'L':'M'}${x(p.date).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ');if(data.length>1)svg+=`<path d="${path} L${x(data.at(-1).date)},${y(0)} L${x(data[0].date)},${y(0)} Z" fill="${s.color}" opacity=".035"/><path d="${path}" fill="none" stroke="${s.color}" stroke-width="2.3" stroke-linejoin="round"/>`;const stride=Math.max(1,Math.ceil(data.length/35));data.forEach((p,i)=>{if(i%stride&&i!==data.length-1)return;const label=s.name+' · '+date(p.date,true)+' · '+num(p.value)+'%';svg+=`<circle class="point" cx="${x(p.date)}" cy="${y(p.value)}" r="${data.length<3?5:3.5}" fill="${s.color}" stroke="#14171e" stroke-width="2" tabindex="0" role="button" aria-label="${esc(label)}" data-point="${esc(label)}"><title>${esc(label)}</title></circle>`})}
svg+='</svg>';el.innerHTML=svg;const countTimes=new Set(points.map(p=>p.date)).size;$('chartDetail').textContent=countTimes===1?'First data point · '+date(points[0].date,true)+' · History grows from now on.':date(Math.min(...points.map(p=>Date.parse(p.date))),true)+' — '+date(Math.max(...points.map(p=>Date.parse(p.date))),true)+' · Select a data point.';
}
function paintCharts(){if(route==='overview')chart('progressChart',DATA.projects.map(p=>({name:p.name,color:p.accent,data:(HISTORY[p.id]||[]).map(r=>({date:r.date,value:r.progress}))})));else if(route.startsWith('project/')){const p=DATA.projects.find(p=>route==='project/'+p.id);if(p)chart('progressChart',[{name:p.name,color:p.accent,data:(HISTORY[p.id]||[]).map(r=>({date:r.date,value:r.progress}))}])}else if(route==='infrastructure')chart('hostChart',[['cpu','CPU','#c2f970'],['memory','Memory','#82b8ff'],['disk','Storage','#a899ff']].map(([key,name,color])=>({name,color,data:HOST.map(r=>({date:r.ts,value:r[key]}))}))) }
function render(){if(!DATA)return;const focused=document.activeElement;const restore=focused?.dataset.range?'[data-range="'+focused.dataset.range+'"]':focused?.id==='activityFilter'?'#activityFilter':null;const openDisclosures=[...document.querySelectorAll('details[data-disclosure][open]')].map(el=>el.dataset.disclosure);const selectedProject=document.activeElement?.dataset.globalWorkerProject;syncNav();let title='Overview',html;if(route==='activity'){html=activityPage();title='Activity'}else if(route==='infrastructure'){html=infrastructure();title='Infrastructure'}else if(route.startsWith('project/')){const p=DATA.projects.find(p=>route==='project/'+p.id);if(p){html=detail(p);title=p.name}else{html='<div class="empty">This project was not found. <a href="#overview">Back to overview</a></div>';title='Unknown project'}}else html=overview();$('crumb').textContent=title;document.title='zCloud — '+title;$('view').innerHTML=html;[...document.querySelectorAll('details[data-disclosure]')].forEach(el=>{if(openDisclosures.includes(el.dataset.disclosure))el.open=true});paintCharts();hydrate();document.querySelectorAll('[data-global-worker-project]').forEach(el=>{const slot=Number(el.dataset.globalWorkerProject);const w=(WORKER_DEBUG?.workers||[]).find(x=>Number(x.global_slot)===slot);el.value=Object.prototype.hasOwnProperty.call(GLOBAL_WORKER_PROJECT_REQUESTS,slot)?GLOBAL_WORKER_PROJECT_REQUESTS[slot]:(w?.project||'')});if(selectedProject)document.querySelector('[data-global-worker-project="'+selectedProject+'"]')?.focus({preventScroll:true});if(restore)document.querySelector(restore)?.focus({preventScroll:true})}
async function loadExtras(){const version=++routeVersion;const results=await Promise.allSettled([api('/api/activity'),api('/api/host-history'),...DATA.projects.map(p=>api('/api/history?project='+p.id+'&range='+range))]);if(version!==routeVersion)return;const fails=results.filter(r=>r.status==='rejected').length;if(results[0].status==='fulfilled'&&Array.isArray(results[0].value)&&
    (route!=='activity'||ACTIVITY_COVERAGE.projectsRead===0))ACTIVITY=results[0].value;if(results[1].status==='fulfilled')HOST=results[1].value;DATA.projects.forEach((p,i)=>{if(results[i+2].status==='fulfilled')HISTORY[p.id]=results[i+2].value;else HISTORY[p.id]=[]});if(fails){$('notice').hidden=false;$('notice').textContent='Part of the history is temporarily unavailable. Live measurements remain visible.'}render();if(route==='activity')void refreshProjectActivity(version)}
function setConnection(ok){$('connection').classList.toggle('offline',!ok);$('connection').innerHTML='<span class="status-dot"></span>'+(ok?'Live connected':'Connection lost')}
async function refresh(initial=false){if(busy)return false;busy=true;$('refresh').disabled=true;try{DATA=await api('/api/status',STATUS_TIMEOUT_MS);saveCachedStatus(DATA);const stale=DATA.stale||Date.now()-Date.parse(DATA.time)>90000;setConnection(!stale);$('notice').hidden=!stale&&!DATA.errors.length;$('notice').textContent=stale?'The latest measurement is stale. Showing the most recent available data.':DATA.errors.join(' · ');$('syncTime').textContent='Latest measurement '+clock(DATA.time)+' · every 15 sec';if(initial||!$('view').children.length)render();await loadExtras();if(route==='overview')loadWorkerDebug();if(route==='overview')loadWorkerScaling();return true}catch(e){setConnection(false);$('notice').hidden=false;$('notice').textContent=DATA?'Connection lost. The last loaded data remains visible; we will retry automatically.':'The VPS is temporarily unreachable. Try again or wait for the next refresh.';if(!DATA)$('view').innerHTML='<div class="loading">Not connected to zCloud yet.<button class="error-action" data-retry>Try again</button></div>';return false}finally{busy=false;$('refresh').disabled=false}}
function navigate(){const hash=location.hash.slice(1)||'overview';const [path,query]=hash.split('?');route=path;if(route!=='overview')mobileWorkersActive=false;if(scrollToWorkersOnOverview)mobileWorkersActive=true;filter=new URLSearchParams(query||'').get('project')||'';routeVersion++;if(DATA){render();loadExtras()}window.scrollTo({top:0,behavior:'instant'});if(route==='overview'&&scrollToWorkersOnOverview){scrollToWorkersOnOverview=false;window.requestAnimationFrame(()=>{const panel=$('workers');if(panel){panel.scrollIntoView({behavior:'smooth',block:'start'});panel.focus({preventScroll:true})}})}}
let draggedProject=null;
document.addEventListener('dragstart',e=>{const card=e.target.closest('[data-project-id]');if(!card)return;draggedProject=card.dataset.projectId;card.classList.add('dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',draggedProject)});
document.addEventListener('dragover',e=>{const card=e.target.closest('[data-project-id]');if(!card||!draggedProject||card.dataset.projectId===draggedProject)return;e.preventDefault();card.classList.add('drag-over')});
document.addEventListener('dragleave',e=>{e.target.closest('[data-project-id]')?.classList.remove('drag-over')});
document.addEventListener('drop',async e=>{const target=e.target.closest('[data-project-id]');if(!target||!draggedProject)return;e.preventDefault();if(projectLayoutActionPending){draggedProject=null;projectLayoutNotice('Finish archiving or restoring before reordering projects.');return}const ids=DATA.projects.map(p=>p.id).filter(id=>id!==draggedProject);const targetIndex=ids.indexOf(target.dataset.projectId);const rect=target.getBoundingClientRect();const after=e.clientY>rect.top+rect.height/2;ids.splice(targetIndex+(after?1:0),0,draggedProject);const archived=(DATA.archived_projects||[]).map(p=>p.id);const tail=(DATA.project_layout?.order||[]).filter(id=>archived.includes(id));await saveProjectLayout([...ids,...tail],archived);draggedProject=null});
document.addEventListener('dragend',()=>{document.querySelectorAll('.dragging,.drag-over').forEach(el=>el.classList.remove('dragging','drag-over'));draggedProject=null});
document.addEventListener('click',async e=>{const zSentinelRead=e.target.closest('[data-zsentinel-source-read]');if(zSentinelRead){e.preventDefault();void readZSentinelSources();return}const localPage=e.target.closest('[data-local-activity-next]');if(localPage){e.preventDefault();void readPagedLocalActivity();return}const deliveryRefresh=e.target.closest('[data-delivery-queue-refresh]');if(deliveryRefresh){e.preventDefault();void refreshDeliveryQueue();return}const progressRefresh=e.target.closest('[data-worker-progress-refresh]');if(progressRefresh){e.preventDefault();void refreshWorkerProgress();return}const issueRefresh=e.target.closest('[data-public-issues-refresh]');if(issueRefresh){e.preventDefault();void refreshPublicProductIssues();return}const publicRefresh=e.target.closest('[data-github-public-refresh]');if(publicRefresh){e.preventDefault();void refreshPublicGitHubEvidence();return}const priorityToggle=e.target.closest('[data-product-issues-toggle]');if(priorityToggle){e.preventDefault();IMPORTED_ISSUE_SHOW_ALL=!IMPORTED_ISSUE_SHOW_ALL;render();return}const issueClear=e.target.closest('[data-product-issues-clear]');if(issueClear){e.preventDefault();IMPORTED_PRODUCT_ISSUES=null;IMPORTED_PRODUCT_ERROR='';IMPORTED_ISSUE_SHOW_ALL=false;render();return}const githubClear=e.target.closest('[data-github-receipt-clear]');if(githubClear){e.preventDefault();IMPORTED_GITHUB_ACTIVITY=null;IMPORTED_GITHUB_ERROR='';render();return}const activityRefresh=e.target.closest('[data-activity-refresh]');if(activityRefresh){e.preventDefault();if(!ACTIVITY_COVERAGE.pending){ACTIVITY_PROJECT_FETCH_TIME=0;void refreshProjectActivity(routeVersion,true)}return}const workerShortcut=e.target.closest('[data-jump-workers]');if(workerShortcut){e.preventDefault();e.stopPropagation();mobileWorkersActive=true;if(route!=='overview'){scrollToWorkersOnOverview=true;location.hash='#overview'}else{if(DATA)syncNav();const panel=$('workers');if(panel){panel.scrollIntoView({behavior:'smooth',block:'start'});panel.focus({preventScroll:true})}}return}const navAction=e.target.closest('[data-nav]');if(navAction){e.preventDefault();mobileWorkersActive=false;const next=String(navAction.dataset.nav||'overview');const nextHash='#'+next;if(location.hash===nextHash){route=next;if(DATA){render();loadExtras()}window.scrollTo({top:0,behavior:'instant'})}else{location.hash=next}return}const attentionResolve=e.target.closest('[data-attention-resolve]');if(attentionResolve){e.preventDefault();e.stopPropagation();attentionResolve.disabled=true;try{await post('/api/portfolio-attention',{action:'resolve',attention_id:attentionResolve.dataset.attentionResolve});DATA.attention_needed=(DATA.attention_needed||[]).filter(item=>item.attention_id!==attentionResolve.dataset.attentionResolve);render()}catch(err){attentionResolve.disabled=false;$('notice').hidden=false;$('notice').textContent='Could not mark the attention item done: '+(err.message||err)}return}const globalPlay=e.target.closest('[data-global-worker-play]');if(globalPlay){e.preventDefault();e.stopPropagation();await playGlobalWorker(Number(globalPlay.dataset.globalWorkerPlay),globalPlay);return}const globalForce=e.target.closest('[data-global-worker-force]');if(globalForce){e.preventDefault();e.stopPropagation();await forceGlobalWorker(Number(globalForce.dataset.globalWorkerForce),globalForce);return}const dynamicSave=e.target.closest('[data-save-dynamic-workers]');if(dynamicSave){e.preventDefault();e.stopPropagation();await saveDynamicWorkerSettings(dynamicSave);return}const workerAction=e.target.closest('[data-worker-action]');if(workerAction){e.preventDefault();e.stopPropagation();await controlWorker(workerAction.dataset.workerId,workerAction.dataset.workerAction,workerAction);return}const forceStartAction=e.target.closest('[data-runner-force-start]');if(forceStartAction){e.preventDefault();e.stopPropagation();await forceStartProject(forceStartAction.dataset.runnerForceStart,forceStartAction);return}const toggleAction=e.target.closest('[data-runner-toggle]');if(toggleAction){e.preventDefault();e.stopPropagation();await setRunnerActive(toggleAction.dataset.runnerToggle,toggleAction.dataset.runnerAction,toggleAction);return}const serviceAction=e.target.closest('[data-runner-service-restart]');if(serviceAction){e.preventDefault();await restartFirefoxInitiator(serviceAction);return}const startAction=e.target.closest('[data-runner-start]');if(startAction){e.preventDefault();e.stopPropagation();await setRunnerActive(startAction.dataset.runnerStart,'start',startAction);return}const pauseAction=e.target.closest('[data-runner-pause]');if(pauseAction){e.preventDefault();e.stopPropagation();await setRunnerActive(pauseAction.dataset.runnerPause,'pause',pauseAction);return}const pushAction=e.target.closest('[data-runner-push]');if(pushAction){e.preventDefault();e.stopPropagation();await pushRunner(pushAction.dataset.runnerPush,pushAction);return}const runnerAction=e.target.closest('[data-runner-new-chat]');if(runnerAction){e.preventDefault();e.stopPropagation();await restartRunner(runnerAction.dataset.runnerNewChat,runnerAction);return}const archive=e.target.closest('[data-archive]');if(archive){e.preventDefault();e.stopPropagation();if(projectLayoutActionPending)return;const id=archive.dataset.archive;const archived=[...(DATA.archived_projects||[]).map(p=>p.id),id];await changeProjectLayout(archive,'archive',DATA.project_layout?.order||DATA.projects.map(p=>p.id),[...new Set(archived)]);return}const restore=e.target.closest('[data-restore]');if(restore){e.preventDefault();e.stopPropagation();if(projectLayoutActionPending)return;const id=restore.dataset.restore;const archived=(DATA.archived_projects||[]).map(p=>p.id).filter(x=>x!==id);await changeProjectLayout(restore,'restore',DATA.project_layout?.order||DATA.projects.map(p=>p.id),archived);return}const projectCardTarget=e.target.closest('[data-project-id]');if(projectCardTarget&&!e.target.closest('a,button,input,select,textarea,label,[role="button"]')){e.preventDefault();location.hash='project/'+encodeURIComponent(projectCardTarget.dataset.projectId);return}const b=e.target.closest('[data-range]');if(b){range=b.dataset.range;HISTORY={};render();loadExtras()}const point=e.target.closest('[data-point]');if(point&&$('chartDetail'))$('chartDetail').textContent=point.dataset.point;const svg=e.target.closest('.chart-svg');if(svg&&!point&&$('chartDetail')){const near=[...svg.querySelectorAll('[data-point]')].map(el=>{const r=el.getBoundingClientRect();return {el,d:(r.x+r.width/2-e.clientX)**2+(r.y+r.height/2-e.clientY)**2}}).sort((a,b)=>a.d-b.d)[0];if(near)$('chartDetail').textContent=near.el.dataset.point}if(e.target.closest('[data-retry]'))refresh(true)});
document.addEventListener('focusin',e=>{if(e.target.dataset.point&&$('chartDetail'))$('chartDetail').textContent=e.target.dataset.point});
document.addEventListener('keydown',e=>{if(e.target.dataset.point&&['Enter',' '].includes(e.key)){e.preventDefault();$('chartDetail').textContent=e.target.dataset.point}});
document.addEventListener('change',async e=>{if(e.target.id==='workerProgressSlot'){const slot=Number(e.target.value);WORKER_PROGRESS_SLOT=Number.isInteger(slot)&&slot>=1&&slot<=8?slot:1;WORKER_PROGRESS_DATA=null;WORKER_PROGRESS_ERROR='';render();return}if(e.target.id==='productIssueReceiptFile'){const file=e.target.files?.[0];if(!file)return;try{if(file.size>1024*1024||file.size<2)throw Error('JSON receipt exceeds 1 MiB or is empty');IMPORTED_PRODUCT_ISSUES=validateProductIssueInbox(JSON.parse(await file.text()));IMPORTED_PRODUCT_ERROR='';IMPORTED_ISSUE_SHOW_ALL=false;}catch(error){IMPORTED_PRODUCT_ISSUES=null;IMPORTED_PRODUCT_ERROR='Product issue receipt not imported: '+(error instanceof SyntaxError?'Invalid JSON':String(error.message||'Invalid receipt'));}if(route==='activity')render();return}if(e.target.id==='githubReceiptFile'){const file=e.target.files?.[0];if(!file)return;try{if(file.size>1024*1024||file.size<2)throw Error('JSON receipt exceeds 1 MiB or is empty');const payload=JSON.parse(await file.text());IMPORTED_GITHUB_ACTIVITY=validateGitHubActivityReceipt(payload);IMPORTED_GITHUB_ERROR='';}catch(error){IMPORTED_GITHUB_ACTIVITY=null;IMPORTED_GITHUB_ERROR='GitHub receipt not imported: '+(error instanceof SyntaxError?'Invalid JSON':String(error.message||'Invalid receipt'));}if(route==='activity')render();return}const projectSelector=e.target.closest?.('[data-global-worker-project]');if(projectSelector){const slot=Number(projectSelector.dataset.globalWorkerProject);GLOBAL_WORKER_PROJECT_REQUESTS[slot]=projectSelector.value;delete GLOBAL_WORKER_REQUEST_RESULTS[slot];render();return}const workerInput=e.target.closest?.('[data-runner-workers]');if(workerInput){await setRunnerWorkers(workerInput.dataset.runnerWorkers,workerInput.value,workerInput);return}if(e.target.id==='activityFilter'){filter=e.target.value;history.replaceState(null,'','#activity'+(filter?'?project='+filter:''));render()}});
$('refresh').addEventListener('click',()=>refresh(true));window.addEventListener('hashchange',navigate);hydrate();navigate();const cachedStatus=loadCachedStatus();if(cachedStatus){DATA=cachedStatus;setConnection(false);$('notice').hidden=false;$('notice').textContent='Live connection is temporarily unavailable. Showing the last successful dashboard snapshot while reconnecting automatically.';$('syncTime').textContent='Last successful measurement '+clock(DATA.time)+' · reconnecting…';render()}refresh(true);setInterval(()=>{if(!document.hidden)refresh()},15000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});