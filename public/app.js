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
const ZSENTINEL_PORTFOLIO_SOURCE_LIMIT=16; // 13 canonical projects + bounded growth.
function fairProductIssueHistory(rows,coverage,limit=150){
  // Global P0/P1 priority is preserved while each observed project gets an
  // opportunity. Do not let one noisy repository erase all other P1 evidence.
  const projects=[...new Set((coverage||[]).map(row=>row.project))]
    .filter(id=>typeof id==='string').slice(0,ZSENTINEL_PORTFOLIO_SOURCE_LIMIT).sort();
  const retained=new Set(projects),known=new Map();
  for(const item of rows||[]){
    if(!item||!retained.has(item.project)||!Number.isSafeInteger(item.number)
       ||item.number<1||!['P0','P1','P2','Unspecified'].includes(item.priority))continue;
    // Fresh data is appended after older evidence by all existing callers.
    known.set(item.project+':'+item.number,item);
  }
  const buckets=new Map(projects.map(id=>[id,[]]));
  for(const item of known.values())buckets.get(item.project).push(item);
  const rank={P0:0,P1:1,P2:2,Unspecified:3};
  for(const bucket of buckets.values())bucket.sort((a,b)=>
    rank[a.priority]-rank[b.priority]||a.number-b.number);
  const output=[],used=new Map(projects.map(id=>[id,0]));
  // Complete each global priority tier before lower priority, with fair
  // round-robin turns inside that tier. The cap remains 150 across all repos.
  for(const priority of ['P0','P1','P2','Unspecified']){
    while(output.length<limit){
      let moved=false;
      for(const project of projects){
        const items=buckets.get(project),cursor=used.get(project);
        if(cursor>=items.length||items[cursor].priority!==priority)continue;
        output.push(items[cursor]);used.set(project,cursor+1);moved=true;
        if(output.length>=limit)break;
      }
      if(!moved)break;
    }
    if(output.length>=limit)break;
  }
  return output;
}

// A new validated operator receipt updates only its declared canonical
// projects. Earlier observations for other projects remain available and
// unchanged; an older file cannot silently roll back newer observations.
function mergeValidatedZSentinelReceipts(previous,next,kind){
  if(!previous)return next;
  const ids=new Set(next.coverage.map(row=>row.project));
  if(ids.size!==next.coverage.length)throw Error('Duplicate project coverage in imported receipt');
  for(const row of next.coverage){
    const prior=previous.coverage.find(item=>item.project===row.project);
    if(prior&&Date.parse(row.observedAt)<Date.parse(prior.observedAt)){
      throw Error('Receipt is older than the current '+row.project+' observation; clear first if an older snapshot is intended');
    }
  }
  const coverage=[...previous.coverage.filter(row=>!ids.has(row.project)),...next.coverage].slice(-ZSENTINEL_PORTFOLIO_SOURCE_LIMIT);
  const retained=new Set(coverage.map(row=>row.project));
  if(kind==='github'){
    const events=fairPublicGitHubHistory([
      ...previous.events.filter(row=>!ids.has(row.project)&&retained.has(row.project)),
      ...next.events.filter(row=>retained.has(row.project))],coverage);
    return {observedAt:new Date().toISOString(),events,coverage};
  }
  const issues=fairProductIssueHistory([
    ...previous.issues.filter(row=>!ids.has(row.project)&&retained.has(row.project)),
    ...next.issues.filter(row=>retained.has(row.project))],coverage);
  return {observedAt:new Date().toISOString(),issues,coverage,partial:true};
}
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
    coverage.push({project:row.project,status:['partial_sources','partial_error','unavailable'].includes(row.status)?row.status:'unavailable',observedAt:value.observed_at});
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
function fairPublicGitHubHistory(rows,coverage,limit=200){
  // A globally timestamp-sorted 200-item cap lets active repositories crowd
  // out quiet projects. Reserve one round-robin slot per covered project
  // before sorting the retained events by their actual source timestamps.
  const ids=[...new Set((coverage||[]).map(row=>row.project))]
    .filter(id=>typeof id==='string').slice(0,ZSENTINEL_PORTFOLIO_SOURCE_LIMIT);
  const grouped=new Map(ids.map(id=>[id,[]]));
  const seen=new Set();
  for(const row of rows||[]){
    if(!row||!grouped.has(row.project)||typeof row.url!=='string'||typeof row.kind!=='string')continue;
    const key=row.project+'|'+row.kind+'|'+row.url;
    if(seen.has(key))continue;
    seen.add(key);
    const ts=Date.parse(row.ts||'');
    if(!Number.isFinite(ts)||ts>Date.now()+120000)continue;
    grouped.get(row.project).push(row);
  }
  for(const group of grouped.values())group.sort((a,b)=>Date.parse(b.ts)-Date.parse(a.ts));
  const result=[],offsets=new Map(ids.map(id=>[id,0]));
  while(result.length<limit){
    let progressed=false;
    for(const id of ids){
      if(result.length===limit)break;
      const group=grouped.get(id),index=offsets.get(id);
      if(index<group.length){result.push(group[index]);offsets.set(id,index+1);progressed=true;}
    }
    if(!progressed)break;
  }
  return result.sort((a,b)=>Date.parse(b.ts)-Date.parse(a.ts));
}
async function refreshPublicGitHubEvidence(selectedOverride=null,quiet=false){
  if(PUBLIC_GITHUB_BUSY)return;
  const selected=selectedOverride||(DATA?.projects||[]).find(p=>p.id===filter);
  if(!selected){IMPORTED_GITHUB_ERROR='Select one project before fetching public GitHub evidence.';if(!quiet)render();return}
  const m=/^https:\/\/github\.com\/Zennay\/([A-Za-z0-9_.-]{1,100})\/?$/.exec(selected.repo_url||'');
  if(!m){IMPORTED_GITHUB_ERROR='This project has no canonical public GitHub repository.';if(!quiet)render();return}
  const repo=m[1],projectId=selected.id;
  if(Date.now()-(PUBLIC_GITHUB_LAST_FETCH.get(projectId)||0)<120000){
    IMPORTED_GITHUB_ERROR='Public GitHub may be rate limited. Recheck is limited to once per project every two minutes.';
    if(!quiet)render();return;
  }
  PUBLIC_GITHUB_BUSY=true;PUBLIC_GITHUB_LAST_FETCH.set(projectId,Date.now());
  if(!quiet){IMPORTED_GITHUB_ERROR='Reading public PR, CI and main-commit evidence…';render();}
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
      const head=pr.head.sha.slice(0,10),draft=pr.draft===true;
      const created=Date.parse(pr.created_at||''),updated=Date.parse(pr.updated_at||'');
      const merged=Date.parse(pr.merged_at||''),closed=Date.parse(pr.closed_at||'');
      const valid=t=>Number.isFinite(t)&&t>0&&t<Date.now()+120000;
      // One current PR record can contain three distinct source lifecycle
      // facts. Do not confuse PR creation with later updates/merges or a green
      // CI result with an accepted deployment.
      if(valid(created))push('pr_created',pr.created_at,link,
        'PR #'+pr.number+' · Created'+(draft?' · draft':'')+' · head '+head);
      const stop=valid(merged)?merged:valid(closed)?closed:Infinity;
      if(valid(updated)&&(!valid(created)||updated-created>1000)&&updated<stop){
        push('pr_updated',pr.updated_at,link,
          'PR #'+pr.number+' · Updated'+(draft?' · draft':'')+' · head '+head);
      }
      if(valid(merged)){
        push('pr_merged',pr.merged_at,link,'PR #'+pr.number+
          ' · GitHub reports merged · head '+head+' · deployment not verified');
      }else if(pr.state==='closed'&&valid(closed)){
        push('pr_closed',pr.closed_at,link,'PR #'+pr.number+
          ' · Closed without merge · head '+head);
      }
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
    // Provider failure MUST NOT turn previously observed PR/CI activity into
    // an empty current result. Incomplete source windows can retain older
    // evidence, clearly labelled stale/partial, until a full new sample arrives.
    const previousGitHub=IMPORTED_GITHUB_ACTIVITY||{events:[],coverage:[]};
    const previousCoverage=previousGitHub.coverage.find(row=>row.project===projectId);
    const previousRows=previousGitHub.events.filter(row=>row.project===projectId);
    const sampledAt=new Date().toISOString();
    const githubCoverage=[...previousGitHub.coverage.filter(row=>row.project!==projectId),
      {project:projectId,status:good===3?'partial_sources':good?'partial_error':'unavailable',
        observedAt:good===3||!previousCoverage?sampledAt:previousCoverage.observedAt,
        lastAttemptAt:sampledAt,staleEvidence:good<3&&previousRows.length>0}].slice(-ZSENTINEL_PORTFOLIO_SOURCE_LIMIT);
    const coveredIds=new Set(githubCoverage.map(row=>row.project));
    const retainedProjectRows=good===3?events:[...previousRows,...events];
    const githubEvents=fairPublicGitHubHistory(
      [...previousGitHub.events.filter(row=>row.project!==projectId&&coveredIds.has(row.project)),
       ...retainedProjectRows],githubCoverage);
    IMPORTED_GITHUB_ACTIVITY={observedAt:sampledAt,events:githubEvents,coverage:githubCoverage};
    IMPORTED_GITHUB_ERROR=(good<3?'Incomplete public GitHub sources ('+good+'/3 readable); prior observations retained and may be stale. ':'Public GitHub source sample ready; ')+
      'branch-only work and private repositories are not covered. Not a live deployment receipt.';
    return {project:projectId,readable:good,attempted:true};
  }catch(_error){
    IMPORTED_GITHUB_ERROR='Public GitHub source could not be interpreted. No authorization or deployment inferred.';
  }finally{PUBLIC_GITHUB_BUSY=false;if(!quiet&&route==='activity')render()}
}

// Explicit operator request. Source side uses 5-minute in-memory server cache,
// bounded singleflight and fixed canonical repo registry. Never an auto-sync.
let VPS_GITHUB_BUSY=false,VPS_GITHUB_ERROR='';
async function readVpsGitHubActivity(){
  if(VPS_GITHUB_BUSY)return;
  const selected=(DATA?.projects||[]).find(project=>project.id===filter);
  if(!selected){VPS_GITHUB_ERROR='Select one project to request the authorized VPS source.';render();return}
  const project=selected.id,version=routeVersion;
  VPS_GITHUB_BUSY=true;VPS_GITHUB_ERROR='Requesting bounded GitHub observation from VPS…';
  if(route==='activity')render();
  try{
    let data=null;
    // First request schedules GitHub collection if not cached; subsequent
    // short reads query the same singleflight result without new provider GETs.
    for(let attempt=0;attempt<6;attempt++){
      const response=await fetch('/api/github-activity-live?project='+encodeURIComponent(project),
        {method:'GET',cache:'no-store',credentials:'same-origin',signal:AbortSignal.timeout(7000)});
      if(!response.ok)throw Error(response.status===403?'Trusted administrator network required':
        'HTTP '+response.status);
      const envelope=await response.json();
      if(!envelope||envelope.schema!=='zcloud-github-activity-live-v1'||
         envelope.project!==project||envelope.feed_complete!==false||
         envelope.authorizes_repair!==false||envelope.authorizes_merge!==false||
         envelope.authorizes_deploy!==false||
         !['refreshing','partial','unavailable','capacity_wait','rate_budget_wait'].includes(envelope.state)){
        throw Error('Unrecognized GitHub server source');
      }
      if(envelope.state==='partial'){data=envelope.receipt;break}
      if(envelope.state!=='refreshing'){
        throw Error(envelope.state==='rate_budget_wait'?'Anonymous GitHub hourly request budget reached; retry later':
          envelope.state==='capacity_wait'?'Source capacity busy':'GitHub unavailable');
      }
      if(attempt===5)throw Error('Observation still collecting; choose Read VPS GitHub again.');
      await new Promise(resolve=>setTimeout(resolve,2500));
      if(routeVersion!==version||filter!==project)return;
    }
    const parsed=validateGitHubActivityReceipt(data);
    if(parsed.coverage.length!==1||parsed.coverage[0].project!==project)
      throw Error('Server source project mismatch');
    if(routeVersion!==version||filter!==project)return;
    const prior=IMPORTED_GITHUB_ACTIVITY||{events:[],coverage:[]};
    const retained=[...prior.coverage.filter(row=>row.project!==project),...parsed.coverage].slice(-ZSENTINEL_PORTFOLIO_SOURCE_LIMIT);
    const projects=new Set(retained.map(row=>row.project));
    const events=fairPublicGitHubHistory(
      [...prior.events.filter(row=>row.project!==project&&projects.has(row.project)),...parsed.events],
      retained);
    IMPORTED_GITHUB_ACTIVITY={observedAt:new Date().toISOString(),coverage:retained,events};
    VPS_GITHUB_ERROR='Server GitHub sample loaded for '+project+
      '. Partial PR/CI/main source; no deployment or branch completeness implied.';
  }catch(error){
    if(routeVersion===version&&filter===project)
      VPS_GITHUB_ERROR='VPS GitHub evidence unavailable: '+
        String(error?.message||'read failed')+
        '. Use the independent browser GET or operator receipt; missing evidence is unknown.';
  }finally{
    VPS_GITHUB_BUSY=false;
    if(route==='activity')render();
  }
}
function githubActivityImportPanel(){
  const snapshot=IMPORTED_GITHUB_ACTIVITY;
  const visible=new Set((DATA?.projects||[]).map(p=>p.id));
  const rows=(snapshot?.events||[]).filter(a=>visible.has(a.project)&&(!filter||a.project===filter));
  const counts=snapshot?.coverage.filter(c=>visible.has(c.project)&&(!filter||c.project===filter))
    .map(c=>c.project+': '+c.status+
      (c.staleEvidence?' · STALE observation from '+c.observedAt:'')).join(' · ')||'No GitHub receipt loaded';
  const content=rows.slice(0,80).map(a=>'<div class="service-row"><div><small>'+esc(a.project)+' · '+esc(a.kind)+
    (a.firstSeen?' · absent in preceding bounded snapshot':'')+'</small><br><a target="_blank" rel="noopener noreferrer" href="'+
    esc(a.url)+'">'+esc(a.description)+'</a></div><small>'+esc(date(a.ts,true))+'</small></div>').join('');
  return '<div class="panel"><div class="panel-header"><div><h2>GitHub evidence · optional local import</h2>'+
    '<p class="panel-subtitle">PRs, CI and main commits from an operator-generated bounded read-only snapshot. Not automatically synchronized.</p></div></div>'+
    '<button type="button" class="runner-action" data-github-public-refresh '+(PUBLIC_GITHUB_BUSY?'disabled aria-busy="true"':'')+'>Read public GitHub for selected project</button> '+
    '<button type="button" class="runner-action" data-vps-github-refresh '+
    (VPS_GITHUB_BUSY?'disabled aria-busy="true"':'')+'>Read VPS GitHub</button> '+
    (VPS_GITHUB_ERROR?'<p class="detail-note" role="status">'+esc(VPS_GITHUB_ERROR)+'</p>':'')+
    '<span class="detail-note">Opt-in browser GET only · three bounded requests per selected project. Up to 16 bounded project snapshots retained in this tab; no credentials or background sync.</span><br>'+ 
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
    observedAt:value.observed_at,
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
async function refreshPublicProductIssues(selectedOverride=null,quiet=false){
  if(PUBLIC_ISSUES_BUSY)return;
  const selected=selectedOverride||(DATA?.projects||[]).find(p=>p.id===filter);
  if(!selected){IMPORTED_PRODUCT_ERROR='Select one project before fetching public product issues.';if(!quiet)render();return}
  const match=/^https:\/\/github\.com\/Zennay\/([A-Za-z0-9_.-]{1,100})\/?$/.exec(selected.repo_url||'');
  if(!match){IMPORTED_PRODUCT_ERROR='The selected project has no canonical public GitHub repository.';if(!quiet)render();return}
  const projectId=selected.id,repository=match[1];
  if(Date.now()-(PUBLIC_ISSUES_LAST_FETCH.get(projectId)||0)<120000){
    IMPORTED_PRODUCT_ERROR='Recent public issue lookup is cached; retry after two minutes.';if(!quiet)render();return;
  }
  PUBLIC_ISSUES_BUSY=true;PUBLIC_ISSUES_LAST_FETCH.set(projectId,Date.now());
  if(!quiet){IMPORTED_PRODUCT_ERROR='Reading bounded public issues and existing PR references…';render();}
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
    // A failed or incomplete GitHub read must not erase known open P0/P1
    // work. Only the full bounded sample can supersede prior source evidence.
    const previousIssues=IMPORTED_PRODUCT_ISSUES||{issues:[],coverage:[]};
    const priorCoverage=previousIssues.coverage.find(row=>row.project===projectId);
    const priorRows=previousIssues.issues.filter(row=>row.project===projectId);
    const sampledAt=new Date().toISOString();
    const currentCoverage={project:projectId,status:readable===3?'partial':readable?'degraded':'unavailable',
      observedAt:readable===3||!priorCoverage?sampledAt:priorCoverage.observedAt,
      lastAttemptAt:sampledAt,staleEvidence:readable<3&&priorRows.length>0,
      oldest:outcomes[0].status==='fulfilled'?'bounded 30':'unavailable',
      recent:outcomes[1].status==='fulfilled'?'bounded 30':'unavailable',
      prs:outcomes[2].status==='fulfilled'?'bounded 12':'unavailable'};
    const issueCoverage=[...previousIssues.coverage.filter(row=>row.project!==projectId),
      currentCoverage].slice(-ZSENTINEL_PORTFOLIO_SOURCE_LIMIT);
    const coveredProjects=new Set(issueCoverage.map(row=>row.project));
    const currentRows=[...issues.values()].map(row=>{
      const old=priorRows.find(item=>item.number===row.number);
      // Missing provider pages are not evidence that referenced PRs vanished.
      return outcomes[2].status!=='fulfilled'&&old?.prs?.length
        ?{...row,prs:old.prs.slice(0,3)}:row;
    });
    const safeRows=readable===3?currentRows:[...priorRows.filter(row=>
      !currentRows.some(fresh=>fresh.number===row.number)),...currentRows];
    const issueRows=fairProductIssueHistory([
      ...previousIssues.issues.filter(row=>row.project!==projectId&&coveredProjects.has(row.project)),
      ...safeRows],issueCoverage);
    IMPORTED_PRODUCT_ISSUES={observedAt:sampledAt,partial:true,issues:issueRows,coverage:issueCoverage};
    IMPORTED_ISSUE_SHOW_ALL=false;
    IMPORTED_PRODUCT_ERROR=(readable===3?'Public issue sample loaded. ':'Partial public issue source ('+readable+'/3 readable); older P0/P1 observations retained and may be stale. ')+
      'These references do not verify fixes, worker ownership or queue claims.';
    return {project:projectId,readable,attempted:true};
  }catch(_error){
    IMPORTED_PRODUCT_ERROR='Public issue source unavailable or malformed. No queue claim taken.';
  }finally{PUBLIC_ISSUES_BUSY=false;if(!quiet&&route==='activity')render()}
}

// Bounded operator-requested source from the trusted VPS endpoint. The server
// collector shares an anti-burst cache with PR/CI activity; it takes no claims.
let VPS_PRODUCT_ISSUES_BUSY=false,VPS_PRODUCT_ISSUES_ERROR='';
async function readVpsProductIssues(){
  if(VPS_PRODUCT_ISSUES_BUSY)return;
  const project=(DATA?.projects||[]).find(item=>item.id===filter)?.id;
  if(!project){VPS_PRODUCT_ISSUES_ERROR='Choose a single project for VPS issue evidence.';render();return}
  const version=routeVersion;
  VPS_PRODUCT_ISSUES_BUSY=true;
  VPS_PRODUCT_ISSUES_ERROR='Reading bounded P0/P1 GitHub issue evidence through VPS…';
  if(route==='activity')render();
  try{
    let receipt=null;
    for(let attempt=0;attempt<6;attempt++){
      const response=await fetch('/api/product-issues-live?project='+encodeURIComponent(project),
        {method:'GET',credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(7000)});
      if(!response.ok)throw Error(response.status===403?'Trusted administrator network required':
        'HTTP '+response.status);
      const data=await response.json();
      if(!data||data.schema!=='zsentinel-product-issues-live-v1'||
          data.project!==project||data.feed_complete!==false||
          data.authorizes_repair!==false||data.authorizes_merge!==false||
          data.authorizes_deploy!==false||
          !['refreshing','partial','unavailable','capacity_wait','rate_budget_wait'].includes(data.state))
        throw Error('Unexpected issue source envelope');
      if(data.state==='partial'){receipt=data.receipt;break}
      if(data.state!=='refreshing')
        throw Error(data.state==='rate_budget_wait'?'Anonymous GitHub hourly request budget reached; retry later':
          data.state==='capacity_wait'?'Source capacity busy':'Issue source unavailable');
      if(attempt===5)throw Error('Issue observation still collecting; use Read VPS issues again.');
      await new Promise(resolve=>setTimeout(resolve,2500));
      if(routeVersion!==version||filter!==project)return;
    }
    const parsed=validateProductIssueInbox(receipt);
    if(parsed.coverage.length!==1||parsed.coverage[0].project!==project)
      throw Error('Issue source project mismatch');
    if(routeVersion!==version||filter!==project)return;
    const old=IMPORTED_PRODUCT_ISSUES||{issues:[],coverage:[]};
    const coverage=[...old.coverage.filter(row=>row.project!==project),...parsed.coverage].slice(-ZSENTINEL_PORTFOLIO_SOURCE_LIMIT);
    const allowed=new Set(coverage.map(row=>row.project));
    const rows=fairProductIssueHistory([
      ...old.issues.filter(row=>row.project!==project&&allowed.has(row.project)),
      ...parsed.issues],coverage);
    IMPORTED_PRODUCT_ISSUES={observedAt:new Date().toISOString(),partial:true,coverage,issues:rows};
    VPS_PRODUCT_ISSUES_ERROR='VPS issue source loaded for '+project+
      '. Partial open P0/P1 source only; ownership and deployment unverified.';
  }catch(error){
    if(routeVersion===version&&filter===project)
      VPS_PRODUCT_ISSUES_ERROR='VPS issue source unavailable: '+String(error?.message||'failed')+
        '. Browser GitHub GET and operator receipts remain independent.';
  }finally{VPS_PRODUCT_ISSUES_BUSY=false;if(route==='activity')render()}
}
function productIssueInboxPanel(){
  const snapshot=IMPORTED_PRODUCT_ISSUES;
  const visible=new Set((DATA?.projects||[]).map(p=>p.id));
  const rows=(snapshot?.issues||[]).filter(item=>visible.has(item.project)&&(!filter||item.project===filter)&&
    (IMPORTED_ISSUE_SHOW_ALL||item.priority==='P0'||item.priority==='P1'));
  const hiddenLowerPriority=(snapshot?.issues||[]).filter(item=>visible.has(item.project)&&(!filter||item.project===filter)
    &&item.priority!=='P0'&&item.priority!=='P1').length;
  const coverageText=(snapshot?.coverage||[]).filter(item=>visible.has(item.project)&&(!filter||item.project===filter))
    .map(item=>item.project+' · '+item.status+
      (item.staleEvidence?' · STALE observation from '+item.observedAt:'')+
      ' (oldest '+item.oldest+', recent '+item.recent+', PRs '+item.prs+')').join(' · ');
  const html=rows.slice(0,50).map(item=>'<div class="service-row"><div><strong>'+esc(item.priority)+
    ' · '+esc(item.project)+'</strong> <a target="_blank" rel="noopener noreferrer" href="'+esc(item.url)+'">'+
    esc(item.title)+'</a><small> · open issue #'+item.number+'</small>'+
    (item.prs.length?'<div class="detail-note">Referenced open PRs (not proven fixes or active claims): '+
      item.prs.map(pr=>'<a href="'+esc(pr.url)+'" target="_blank" rel="noopener noreferrer">#'+pr.number+
      (pr.draft?' draft':'')+(pr.relationship==='closing_keyword_only'?' closing keyword':' mention')+'</a>').join(' · ')+'</div>':'')+
    '</div></div>').join('');
  return '<div class="panel"><div class="panel-header"><div><h2>Product issue inbox · optional local import</h2>'+
    '<p class="panel-subtitle">Existing GitHub P0/P1 issues from the zSentinel bounded inbox, never an automated claim or task assignment.</p></div></div>'+
    '<button type="button" class="runner-action" data-vps-issues-read '+
    (VPS_PRODUCT_ISSUES_BUSY?'disabled aria-busy="true"':'')+'>Read VPS issues</button> '+
    (VPS_PRODUCT_ISSUES_ERROR?'<p class="detail-note" role="status">'+esc(VPS_PRODUCT_ISSUES_ERROR)+'</p>':'')+
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
const SAFE_WORKER_COMPOSER_HINTS=Object.freeze({
  'composer-missing-manual-recovery-required':'Composer missing in an existing conversation; inspect assigned worker tab manually.',
  'composer-missing-operator-action-required':'Composer remains missing after bounded repair; inspect tab before resend.',
  'composer-missing-before-first-send':'One same-tab repair was requested before first send; resumed generation unverified.',
  'new-chat-button-missing':'New chat button unavailable; operator action required.'
});
function composerHint(row){
  if(row?.kind==='composer-restored')return 'Composer visible again; claim and generation still unverified.';
  if(row?.kind!=='send-blocked'&&row?.kind!=='conversation-reset-requested')return '';
  const reason=String(row.reason_code||'');
  return Object.prototype.hasOwnProperty.call(SAFE_WORKER_COMPOSER_HINTS,reason)?
    SAFE_WORKER_COMPOSER_HINTS[reason]:'';
}
function workerProgressPanel(){
  const report=WORKER_PROGRESS_DATA;
  const scoped=report&&report.project===filter&&report.worker_slot===WORKER_PROGRESS_SLOT?report:null;
  const facts=scoped?[
    ['Last browser event',scoped.latest_event],['Latest prompt',scoped.latest_prompt],
    ['Latest progress',scoped.latest_progress],['Latest completion',scoped.latest_completion]
  ]:[];
  const composerAlerts=(scoped?.recent_events||[]).filter(row=>
    composerHint(row)&&row.clock?.status==='observed').slice(0,4).map(row=>
    '<div class="service-row"><div><strong>Composer · browser observation</strong><br><small>'+
    esc(row.clock?.utc||'Unknown time')+' · '+esc(composerHint(row))+'</small></div></div>').join('');
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
    (scoped?'<div class="service-list">'+composerAlerts+cards+(history?'<details data-disclosure="worker-progress-events"><summary>Recent browser event history</summary>'+history+'</details>':'')+'</div>':'')+'</section>';
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
       !Number.isSafeInteger(p.queued_requires_owner_review)||p.queued_requires_owner_review<0||
       !Array.isArray(p.review_items)||p.review_items.length>8||
       !p.review_items.every(row=>row&&typeof row.project_id==='string'&&
         typeof row.queue_id==='string'&&typeof row.priority==='string'&&
         ['invalid_metadata','invalid_title','oversized_criteria'].includes(row.reason))||
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
  const reviewItems=(p?.review_items||[]).filter(row=>!filter||row.project_id===filter);
  const needsReview=reviewItems.map(row=>'<div class="service-row"><div><strong>'+
    esc(row.priority)+' · '+esc(row.project_id)+'</strong><br><small>Queue '+esc(row.queue_id)+
    '</small></div><small>Owner review needed · '+esc(row.reason)+'</small></div>').join('');
  const rendered=candidates.map(row=>'<div class="service-row"><div><strong>'+esc(row.priority)+
    ' · '+esc(row.project_id)+'</strong><small> Queue '+esc(row.queue_id)+
    '</small></div><small>Not claimed</small></div>').join('');
  const info=p?('<p class="detail-note" role="status">Snapshot '+esc(DELIVERY_QUEUE_OBSERVED||'unknown time')+
    ' · '+p.queued_product_candidates+' queued product candidates · '+p.queued_qa_only_held+
    ' QA-only items held · '+p.queued_requires_owner_review+
    ' unclassified items need owner review (global counts; up to eight IDs per type shown).</p>'+
    (needsReview?'<h3>Requires owner review (not QA)</h3><div class="service-list">'+needsReview+'</div>':'')+
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


/* A manually imported PR-less branch snapshot from #1554 is advisory evidence,
   NEVER an active owner, claim, gate or proof of current production delivery. */
let IMPORTED_PRLESS_BRANCHES=null,IMPORTED_PRLESS_ERROR='';
function validateZSentinelBranchSnapshot(value){
  const fail=()=>{throw Error('Invalid or incomplete zSentinel branch receipt')};
  const isInt=(n,max)=>Number.isSafeInteger(n)&&n>=0&&n<=max;
  const sha=x=>typeof x==='string'&&/^[0-9a-f]{40}$/.test(x);
  const ref=x=>typeof x==='string'&&x.length<=200
    &&new RegExp('^[A-Za-z0-9_./-]+$').test(x)&&!x.includes('..');
  const path=x=>typeof x==='string'&&x.length>=1&&x.length<=300
    &&!x.includes('\\')&&!x.startsWith('/')
    &&![...x].some(c=>c.charCodeAt(0)<32||c.charCodeAt(0)===127)
    &&!x.split('/').some(p=>!p||p==='.'||p==='..');
  const stamp=x=>typeof x==='string'&&x.length<=40
    &&Number.isFinite(Date.parse(x))&&Date.parse(x)<=Date.now()+300000;
  if(!value||typeof value!=='object'||Array.isArray(value)
      ||value.schema!=='zsentinel-recent-prless-ownership-v1'
      ||value.source!=='github_readonly'||value.repository!=='Zennay/zCloud'
      ||value.snapshot_complete!==true||value.ownership_granted!==false
      ||value.may_admit_work!==false||value.release_authorized!==false
      ||!sha(value.main_sha)||!stamp(value.observed_at)
      ||typeof value.coverage_complete!=='boolean'
      ||!isInt(value.branch_inventory_count,2400)
      ||!isInt(value.prless_count,2400)
      ||!isInt(value.matching_prefix_count,2400)
      ||value.matching_prefix_count>value.prless_count
      ||value.branch_inventory_count<value.prless_count
      ||!isInt(value.slice_offset,2400)||!isInt(value.slice_count,12)
      ||!Array.isArray(value.branches)||value.branches.length!==value.slice_count
      ||typeof value.overlap_in_slice!=='boolean')fail();
  const prefix=value.branch_prefix;
  if(prefix!==null&&!ref(prefix))fail();
  if(value.slice_count>value.matching_prefix_count
      ||value.slice_offset+value.slice_count>value.matching_prefix_count)fail();
  if(value.coverage_complete&&(prefix!==null||value.slice_offset!==0
      ||value.slice_count!==value.prless_count))fail();
  const seen=new Set(),branches=[];
  for(const row of value.branches){
    if(!row||!ref(row.ref)||!sha(row.sha)||seen.has(row.ref)
        ||(prefix!==null&&!row.ref.startsWith(prefix))
        ||!stamp(row.last_commit_at)||row.owner_proven!==false
        ||!['historical','recent_unmerged','not_ahead'].includes(row.classification))fail();
    seen.add(row.ref);
    if(row.classification!=='historical'
        &&(!isInt(row.ahead_commits,250)
           ||!Array.isArray(row.changed_paths)||row.changed_paths.length>600
           ||!Array.isArray(row.overlapping_paths)||row.overlapping_paths.length>600))fail();
    const all=row.classification==='historical'?[]:row.changed_paths;
    const overlaps=row.classification==='historical'?[]:row.overlapping_paths;
    if((all||[]).some(p=>!path(p))||(overlaps||[]).some(p=>!path(p))
        ||(overlaps||[]).some(p=>!all.includes(p))
        ||new Set(overlaps).size!==overlaps.length)fail();
    branches.push({ref:row.ref,sha:row.sha,lastCommit:row.last_commit_at,
      classification:row.classification,ahead:row.ahead_commits??null,
      changedCount:all.length,overlapCount:overlaps.length,
      overlaps:overlaps.slice(0,12)});
  }
  if(value.overlap_in_slice!==branches.some(row=>row.overlapCount>0))fail();
  return {observedAt:value.observed_at,repository:value.repository,main:value.main_sha,
    coverageComplete:value.coverage_complete,prefix,offset:value.slice_offset,
    scanned:branches.length,prlessTotal:value.prless_count,
    matchingCount:value.matching_prefix_count,slices:1,
    ranges:[[value.slice_offset,value.slice_offset+value.slice_count]],branches};
}
function mergeZSentinelBranchSnapshots(current,next){
  if(!current)return next;
  if(current.repository!==next.repository||current.main!==next.main
      ||current.prlessTotal!==next.prlessTotal||current.matchingCount!==next.matchingCount
      ||current.prefix!==next.prefix)throw Error('Branch slices use different source identities; clear and import a new snapshot');
  if(Math.abs(Date.parse(current.observedAt)-Date.parse(next.observedAt))>10*60000)
    throw Error('Branch slices are more than 10 minutes apart; do not combine old and new evidence');
  if(current.slices>=8||current.branches.length+next.branches.length>96)
    throw Error('Branch import is limited to eight independent slices');
  const start=next.ranges[0][0],end=next.ranges[0][1];
  if(current.ranges.some(([a,b])=>start<b&&a<end))
    throw Error('Overlapping branch slice indices; existing evidence preserved');
  const seen=new Set(current.branches.map(row=>row.ref));
  if(next.branches.some(row=>seen.has(row.ref)))
    throw Error('Duplicate branch identity across slices; source changed or indices overlap');
  return {...current,observedAt:current.observedAt,
    // Separate observation times are never a single atomically complete source.
    coverageComplete:false,slices:current.slices+1,
    ranges:[...current.ranges,...next.ranges],scanned:current.scanned+next.scanned,
    branches:[...current.branches,...next.branches]};
}
function zSentinelBranchEvidencePanel(){
  const project=(DATA?.projects||[]).find(p=>['https://github.com/Zennay/zCloud','https://github.com/Zennay/zCloud/'].includes(p.repo_url||''));
  const eligible=!filter||(project&&project.id===filter);
  const snapshot=eligible?IMPORTED_PRLESS_BRANCHES:null;
  const old=snapshot&&Date.now()-Date.parse(snapshot.observedAt)>6*3600000;
  const rows=(snapshot?.branches||[]).slice().sort((a,b)=>
    (b.overlapCount>0)-(a.overlapCount>0)||b.lastCommit.localeCompare(a.lastCommit)).slice(0,40);
  return '<div class="panel" id="zSentinelBranchEvidence">'+
    '<div class="panel-header"><div><h3>PR-less branches · optional local evidence</h3>'+
    '<p class="panel-subtitle">Import a zSentinel PR-less JSON report for zCloud. Browser-local only; no claims or release authority.</p></div>'+
    '<button type="button" class="runner-action runner-action-secondary" data-zsentinel-branch-open>Import branch .json</button>'+
    '<input id="zSentinelBranchFile" type="file" accept=".json,application/json" multiple hidden>'+
    (IMPORTED_PRLESS_BRANCHES?'<button type="button" class="runner-action runner-action-secondary" data-zsentinel-branch-clear>Clear</button>':'')+
    '</div>'+
    (IMPORTED_PRLESS_ERROR?'<p class="detail-note" role="alert">'+esc(IMPORTED_PRLESS_ERROR)+'</p>':'')+
    (snapshot?'<p class="detail-note" role="status">Repository '+esc(snapshot.repository)+
       ' · source '+esc(snapshot.observedAt)+(old?' · STALE (>6h)':' · manually imported')+
       ' · '+snapshot.scanned+'/'+snapshot.prlessTotal+' PR-less branches sampled across '+snapshot.slices+' slice(s)'+
       ' · '+(snapshot.coverageComplete?'whole snapshot':'PARTIAL coverage')+
       '. SHA '+esc(snapshot.main.slice(0,12))+'; current main not rechecked. No ownership verified.</p>'+
       '<div class="service-list">'+rows.map(row=>
         '<div class="service-row"><div><strong>'+esc(row.ref)+'</strong><br><small>'+
         esc(row.classification)+' · '+esc(row.lastCommit)+
         ' · '+(row.ahead===null?'unverified':'ahead '+row.ahead)+
         ' · '+row.changedCount+' paths</small>'+
         (row.overlapCount?'<div class="detail-note">Potential conflicts ('+row.overlapCount+'): '+
             row.overlaps.map(p=>'<code>'+esc(p)+'</code>').join(' · ')+
             (row.overlapCount>row.overlaps.length?' · remaining paths omitted':'')+'</div>':'')+
         '</div><small>'+(row.overlapCount?'Overlap observed':'Ownership unknown')+'</small></div>').join('')+
       '</div>'+(snapshot.branches.length>40?'<p class="detail-note">Showing 40 prioritised rows; remaining '+(snapshot.branches.length-40)+' in local evidence summary.</p>':''):
      '<p class="detail-note">No local branch report for the selected project. Missing evidence is UNKNOWN, not a clear ownership check.</p>')+
    '<p class="detail-note">This panel does not synchronize hidden/private branches, contact GitHub or grant worker claims. Recheck Notion active owners and exact-head CI before changing files.</p>'+
    '</div>';
}
// Opt-in portfolio-wide product discovery: four repositories per click, with
// existing public-source rate budgets and failure-as-unknown semantics intact.
// No background loops, authenticated requests, claims or queue mutations.
const ZSENTINEL_ALL_BATCH_SIZE=4;
let ZSENTINEL_ALL_RUNNING=false,ZSENTINEL_ALL_INDEX=0,ZSENTINEL_ALL_MESSAGE='';
async function readZSentinelAllProjects(){
  if(ZSENTINEL_ALL_RUNNING||ZSENTINEL_SOURCES_BUSY||PUBLIC_GITHUB_BUSY||PUBLIC_ISSUES_BUSY)return;
  if(route!=='activity'||filter||!DATA){ZSENTINEL_ALL_MESSAGE='Choose All projects on Activity.';render();return;}
  const eligible=(DATA.projects||[]).filter(p=>p&&typeof p.id==='string'
      &&/^https:\/\/github\.com\/Zennay\/[A-Za-z0-9_.-]{1,100}\/?$/.test(p.repo_url||'')).slice(0,24);
  if(!eligible.length){ZSENTINEL_ALL_MESSAGE='No canonical public GitHub repositories configured.';render();return;}
  if(ZSENTINEL_ALL_INDEX>=eligible.length)ZSENTINEL_ALL_INDEX=0;
  const start=ZSENTINEL_ALL_INDEX;
  const batch=eligible.slice(start,start+ZSENTINEL_ALL_BATCH_SIZE);
  const version=routeVersion;
  ZSENTINEL_ALL_RUNNING=true;
  let completed=0,readableGitHub=0,readableIssues=0,unavailable=0;
  ZSENTINEL_ALL_MESSAGE='Reading '+batch.length+' repositories (bounded and read-only)…';
  render();
  try{
    for(const project of batch){
      if(routeVersion!==version||route!=='activity'||filter){
        ZSENTINEL_ALL_MESSAGE='Portfolio scan paused after '+completed+' project(s); return to All projects to continue.';
        break;
      }
      // A sequential, explicit operator batch avoids anonymous GitHub bursts.
      const issues=await refreshPublicProductIssues(project,true);
      const activity=await refreshPublicGitHubEvidence(project,true);
      completed++;
      if(issues?.readable>0)readableIssues++;
      if(activity?.readable>0)readableGitHub++;
      if(!(issues?.readable>0)||!(activity?.readable>0))unavailable++;
      ZSENTINEL_ALL_MESSAGE='Attempted '+completed+'/'+batch.length+
        ' project(s) · GitHub activity readable '+readableGitHub+
        ' · issue source readable '+readableIssues+
        ' · incomplete projects '+unavailable+
        '. Provider denial, timeout, cache limits and private repositories stay unknown.';
      if(route==='activity')render();
    }
    ZSENTINEL_ALL_INDEX=start+completed;
    if(completed===batch.length){
      const activityCoverage=new Set((IMPORTED_GITHUB_ACTIVITY?.coverage||[]).map(row=>row.project));
      const issueCoverage=new Set((IMPORTED_PRODUCT_ISSUES?.coverage||[]).map(row=>row.project));
      const observedBoth=eligible.filter(p=>activityCoverage.has(p.id)&&issueCoverage.has(p.id)).length;
      ZSENTINEL_ALL_MESSAGE='Attempted '+Math.min(ZSENTINEL_ALL_INDEX,eligible.length)+'/'+eligible.length+
        ' eligible repositories in this scan · '+observedBoth+'/'+eligible.length+
        ' have both bounded source records · this batch: '+readableGitHub+
        ' GitHub activity readable, '+readableIssues+' issue source readable, '+unavailable+
        ' incomplete. Up to 16 source records retained; NOT a complete synchronized feed.';
    }
  }finally{ZSENTINEL_ALL_RUNNING=false;if(route==='activity')render();}
}

function zSentinelCapacityNotice(){
  const metrics=DATA?.host||{},stamp=Date.parse(DATA?.time||'');
  const fresh=Number.isFinite(stamp)&&stamp<=Date.now()+120000&&Date.now()-stamp<=120000;
  const finite=n=>typeof n==='number'&&Number.isFinite(n)&&n>=0;
  const cores=metrics.cores,load=metrics.load,swap=metrics.swap||{};
  const disk=metrics.disk||{},warnings=[];
  if(finite(cores)&&cores>0&&finite(load)&&load>cores*2){
    warnings.push('Host load '+num(load)+' exceeds twice '+num(cores)+' vCPU; queued jobs may take longer.');
  }
  const swapObserved=finite(swap.total)&&finite(swap.free)&&finite(swap.used)
    &&swap.free<=swap.total&&swap.used<=swap.total
    &&swap.free+swap.used===swap.total;
  if(swapObserved&&swap.total>0){
    const pct=swap.used/swap.total*100;
    if(pct>=85||swap.free<512*1048576){
      warnings.push('Swap '+num(pct)+'% used · '+gb(swap.free)+' free. Capacity for new workers may be constrained.');
    }
  }
  if(finite(disk.total)&&disk.total>0&&finite(disk.used)
      &&disk.used/disk.total>=0.9){
    warnings.push('Disk '+num(disk.used/disk.total*100)+'% used; investigate free space before additional builds.');
  }
  const status=!fresh?'Host observation stale or unavailable; capacity UNKNOWN.':
    !swapObserved?'Swap telemetry not yet available from the deployed backend; complete capacity assessment UNKNOWN.':
    warnings.length?'Capacity warnings based on current host telemetry:':'No configured threshold exceeded in available host telemetry. Runner health still unverified.';
  return '<section class="panel" id="zSentinelCapacityNotice">'+
    '<div class="panel-header"><div><h3>zSentinel · VPS capacity signals</h3>'+
    '<p class="panel-subtitle">Read-only CPU/load, disk and swap from /api/status; never a proof of runner availability.</p></div></div>'+
    '<p role="status" class="detail-note">'+esc(status)+'</p>'+
    (fresh&&warnings.length?'<div class="service-list">'+
      warnings.map(message=>'<div class="service-row"><div>'+esc(message)+'</div></div>').join('')+
      '</div>':'')+
    '<p class="detail-note">No automatic service restarts, worker cancellations or swap clearing. Use the authorised zSSH path and owner coordination for recovery.</p>'+
    '</section>';
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
/* Deliberately exports public issue and PR references, never raw queue ids,
   claims, worker prompt text, tokens or privately scoped API response bodies. */
function exportZSentinelHandoff(){
  const projects=new Set((DATA?.projects||[]).map(item=>item.id));
  const scope=filter||'all-projects';
  const clean=(value,n=180)=>String(value??'').replace(/[\r\n\t]+/g,' ')
    .replace(/[\x00-\x1f\x7f]/g,'').slice(0,n);
  const issues=(IMPORTED_PRODUCT_ISSUES?.issues||[]).filter(item=>
    projects.has(item.project)&&(!filter||item.project===filter)
    &&(item.priority==='P0'||item.priority==='P1')).slice(0,60);
  const order={P0:0,P1:1};
  issues.sort((a,b)=>order[a.priority]-order[b.priority]||
    a.project.localeCompare(b.project)||a.number-b.number);
  const observed=IMPORTED_PRODUCT_ISSUES?.observedAt||'unavailable';
  const github=IMPORTED_GITHUB_ACTIVITY?.observedAt||'unavailable';
  const queue=DELIVERY_QUEUE_DATA;
  const lines=[
    '# zSentinel product handoff — '+clean(scope,56),
    '',
    'Snapshot generated: '+new Date().toISOString(),
    'Issue source last observed: '+clean(observed,40),
    'GitHub activity source last observed: '+clean(github,40),
    'Queue admission snapshot: '+clean(DELIVERY_QUEUE_OBSERVED||'unavailable',48),
    '',
    '**Evidence only — no verified claim ownership, reviewer approval, production deploy, runner recovery or permission to merge.**',
    '**Missing or partial provider data is unknown work, never zero work.**',
    '',
    '## Open P0/P1 product issues (bounded source)',
    '',
  ];
  for(const item of issues){
    // URLs are generated by the canonical GitHub issue importer or its strict
    // offline receipt validator. Do not export arbitrary user-supplied URLs.
    if(!/^https:\/\/github\.com\/Zennay\/[A-Za-z0-9_.-]{1,100}\/issues\/[1-9][0-9]*$/.test(item.url))continue;
    lines.push('- **'+item.priority+' '+clean(item.project,56)+' #'+item.number+'** '+clean(item.title)+' — '+item.url);
    const prs=(item.prs||[]).slice(0,3).filter(pr=>
      /^https:\/\/github\.com\/Zennay\/[A-Za-z0-9_.-]{1,100}\/pull\/[1-9][0-9]*$/.test(pr.url));
    for(const pr of prs)lines.push('  - Referenced open PR #'+pr.number+' ('+
      (pr.draft?'draft, ':'')+clean(pr.relationship,32)+', active claim unverified): '+pr.url);
  }
  if(!issues.length)lines.push('No matching issues in currently loaded partial sample; sources may be missing.');
  lines.push('', '## Source freshness and missing-data warnings', '');
  for(const source of [
    {name:'P0/P1 issue source',data:IMPORTED_PRODUCT_ISSUES},
    {name:'PR/CI GitHub source',data:IMPORTED_GITHUB_ACTIVITY}
  ]){
    for(const row of (source.data?.coverage||[]).filter(r=>projects.has(r.project)&&(!filter||r.project===filter)).slice(0,ZSENTINEL_PORTFOLIO_SOURCE_LIMIT)){
      lines.push('- '+source.name+' '+clean(row.project,56)+' · '+clean(row.status,32)+
        ' · observed '+clean(row.observedAt||'unknown',48)+
        (row.staleEvidence?' · STALE RETAINED EVIDENCE, NOT CURRENT STATUS':'')+
        (row.lastAttemptAt?' · last attempt '+clean(row.lastAttemptAt,48):''));
    }
  }
  lines.push('', '## Product queue admission (provisional)', '');
  if(queue){
    lines.push('Total product candidates in observed global queue: '+queue.queued_product_candidates);
    lines.push('QA-only queued items held from new admission: '+queue.queued_qa_only_held);
    lines.push('Queued items requiring owner review (not QA): '+queue.queued_requires_owner_review);
    for(const item of (queue.review_items||[]).filter(row=>projects.has(row.project_id)&&
      (!filter||row.project_id===filter)).slice(0,8)){
      lines.push('- Needs owner review: '+clean(item.priority,4)+' '+clean(item.project_id,56)+
        ' · '+clean(item.reason,32)+'; queue ID withheld');
    }
    const rows=(queue.candidates||[]).filter(item=>projects.has(item.project_id)&&(!filter||item.project_id===filter));
    for(const item of rows.slice(0,8))lines.push('- '+clean(item.priority,4)+' '+clean(item.project_id,56)+
      ': candidate, unclaimed; ID withheld from export');
  }else lines.push('Queue admission source unavailable; no capacity or worker ownership inferred.');
  lines.push('', '## Independent existing GitHub observations (bounded)', '');
  const recent=(IMPORTED_GITHUB_ACTIVITY?.events||[]).filter(item=>
    projects.has(item.project)&&(!filter||item.project===filter)).slice(0,30);
  for(const item of recent){
    if(!/^https:\/\/github\.com\/Zennay\/[A-Za-z0-9_.-]{1,100}\/(?:pull|commit|actions\/runs)\/[A-Za-z0-9]+$/.test(item.url))continue;
    lines.push('- '+clean(item.project,56)+' · '+clean(item.kind,32)+' · '+clean(item.ts,40)+
      ' · '+clean(item.description,160)+' — '+item.url);
  }
  if(!recent.length)lines.push('No valid GitHub events in currently loaded source window.');
  lines.push('', '## Locally imported PR-less branch ownership sample (zCloud only)', '');
  const branchProject=(DATA?.projects||[]).find(p=>['https://github.com/Zennay/zCloud','https://github.com/Zennay/zCloud/'].includes(p.repo_url||''));
  if(IMPORTED_PRLESS_BRANCHES&&(!filter||(branchProject&&filter===branchProject.id))){
    const proof=IMPORTED_PRLESS_BRANCHES;
    lines.push('Repository: '+clean(proof.repository,80),
      'Source observed: '+clean(proof.observedAt,40),
      'Exact source main SHA (not rechecked): '+clean(proof.main,40),
      'Global branch coverage: '+(proof.coverageComplete?'complete when observed':'PARTIAL'),
      'PR-less branches in sample: '+proof.scanned+' of '+proof.prlessTotal);
    lines.push('Imported slices: '+proof.slices+' (independent observations, not an atomic inventory)');
    for(const row of proof.branches.slice(0,40)){
      lines.push('- '+clean(row.ref,200)+' · '+clean(row.classification,24)+
        ' · '+clean(row.lastCommit,40)+' · overlap count '+row.overlapCount+
        ' · owner unverified');
    }
    if(proof.branches.length>40)lines.push('Other '+(proof.branches.length-40)+' branch rows omitted from bounded handoff.');
  }else lines.push('Branch source not imported for selected project; ownership unknown.');
  lines.push('', '## Required continuation', '',
    'Check actual current Notion claims and exact GitHub PR/CI status before editing. Keep existing human, security, runner and serialized production-release gates intact.',
    'Only implement unclaimed work and never infer deployment from PR or main-commit history.',
    '');
  const blob=new Blob([lines.join('\n')],{type:'text/markdown;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const element=document.createElement('a');
  element.href=url;element.download='zsentinel-workboard-'+clean(scope,56)+'.md';
  document.body.appendChild(element);element.click();element.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
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
  const staleProjects=[...new Set([...coveredIssues,...coveredGitHub]
    .filter(row=>row.staleEvidence===true).map(row=>row.project))];
  // First 24 P0/P1 issue cards should not be monopolized by one
  // high-volume project after 13-project source coverage is retained.
  const fairHighImpact=fairProductIssueHistory(
    relevant,(snapshot?.coverage||[]).filter(item=>ids.has(item.project)&&(!focus||item.project===focus)),24);
  const formatted=fairHighImpact.map(issue=>{
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
  const scopedTime=(rows,fallback)=>{
    const observed=rows.map(item=>item.observedAt||fallback)
      .filter(value=>value&&Number.isFinite(Date.parse(value)));
    if(!observed.length)return 'Not observed';
    const oldest=Math.min(...observed.map(value=>Date.parse(value)));
    return date(new Date(oldest).toISOString(),true)+
      (Date.now()-oldest>6*3600000?' · stale (>6h)':'');
  };
  const issueTime=scopedTime(coveredIssues,snapshot?.observedAt);
  const queueTime=admitted?(DELIVERY_QUEUE_OBSERVED||'Unknown time'):'Not observed';
  const gitTime=scopedTime(coveredGitHub,github?.observedAt);
  const active=ZSENTINEL_SOURCES_BUSY||ZSENTINEL_ALL_RUNNING||PUBLIC_ISSUES_BUSY||PUBLIC_GITHUB_BUSY||DELIVERY_QUEUE_BUSY;
  return '<section class="panel" id="zSentinelDeliveryBoard"><div class="panel-header">'+
    '<div><h2>zSentinel · product delivery board</h2>'+
    '<p class="panel-subtitle">P0/P1 issues first; existing PR references and queue candidates without false ownership claims.</p></div>'+
    '<button type="button" class="runner-action" data-zsentinel-source-read '+
    (active?'disabled aria-busy="true"':'')+'>'+
    (active?'Reading…':'Read product sources')+'</button>'+
    (!focus?'<button type="button" class="runner-action runner-action-secondary" data-zsentinel-all-read '+(active?'disabled aria-busy="true"':'')+'>'+
    (ZSENTINEL_ALL_RUNNING?'Reading portfolio…':'Read next 4 projects')+'</button>':'')+
    '<button type="button" class="runner-action" data-zsentinel-export '+(!snapshot&&!github&&!IMPORTED_PRLESS_BRANCHES?'disabled':'')+
    '>Export handoff .md</button></div>'+
    (ZSENTINEL_ALL_MESSAGE&&!focus?'<p class="detail-note" role="status">'+esc(ZSENTINEL_ALL_MESSAGE)+'</p>':'')+
    '<p class="detail-note" role="status">Issue source '+esc(issueTime)+
    ' · GitHub activity '+esc(gitTime)+' · Queue '+esc(queueTime)+
    '. Reads are manual, separate, bounded and may be partial or stale.</p>'+
    (staleProjects.length?'<p class="detail-note" role="alert">GitHub source unavailable/partial — last known issue/PR/CI evidence preserved, NOT current proof: '+esc(staleProjects.join(', '))+'.</p>':'')+
    '<p class="detail-note">'+
    (focus?'Project: '+esc(focus)+'.':'Select a single project to refresh the sources; current snapshots can be viewed across projects.')+
    ' Issues sampled: '+coveredIssues.length+' project sources; GitHub activity: '+
    coveredGitHub.length+' project sources.</p>'+
    '<h3>Open high-impact product issues ('+relevant.length+' in available snapshot)</h3>'+
    (formatted?'<div class="service-list">'+formatted+'</div>':
     '<p class="detail-note">No P0/P1 issues in the available snapshot. This is NOT proof the repository has no open issues.</p>')+
    (relevant.length>24?'<p class="detail-note">Showing the first 24 prioritised issues.</p>':'')+
    '<h3>Queued items requiring owner review ('+(admitted?admitted.queued_requires_owner_review:'unknown global count')+')</h3>'+
    (admitted?.review_items?.length?'<div class="service-list">'+admitted.review_items.filter(item=>
      ids.has(item.project_id)&&(!focus||item.project_id===focus)).slice(0,8).map(item=>
      '<div class="service-row"><div><strong>'+esc(item.priority)+' · '+esc(item.project_id)+
      '</strong><br><small>'+esc(item.reason)+'</small></div><small>Not claimed</small></div>').join('')+
      '</div>':'<p class="detail-note">No review item in bounded snapshot; source may be partial.</p>')+
    '<h3>Queued product candidates ('+(admitted?admitted.queued_product_candidates:'unknown global count')+')</h3>'+
    (queueRows?'<div class="service-list">'+queueRows+'</div>':
     '<p class="detail-note">No candidate shown. Queue access, project coverage or remaining pages may be missing.</p>')+
    '<p class="detail-note">Queue and GitHub records are NOT joined as a verified claim. A PR mention does not confer ownership; green CI is not a merge; merge is not deploy. Active human approvals, runner health and release gates remain separate.</p>'+
    '</section>';
}


/* zSentinel issue #677 — local, explicitly imported retry incident evidence.
 * JSON is validated and normalized; no server mutations or claim authorization.
 * The companion offline CLI is in PR #1555. */
let ZSENTINEL_RETRY_SNAPSHOT=null,ZSENTINEL_RETRY_TREND=null,ZSENTINEL_RETRY_MESSAGE='';
let ZSENTINEL_RETRY_BUSY=false;
let ZSENTINEL_RETRY_EXPANDED_ITEMS=false,ZSENTINEL_RETRY_EXPANDED_MOVEMENT=false;
function retryKnownProjects(){
  const active=Array.isArray(DATA?.projects)?DATA.projects:[];
  const archived=Array.isArray(DATA?.archived_projects)?DATA.archived_projects:[];
  return new Set([...active,...archived].map(project=>project.id));
}
function retryTime(value){
  return typeof value==='string'&&value.length<=45&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d/.test(value)
    &&/(?:Z|[+-]\d\d:\d\d)$/.test(value)&&Number.isFinite(Date.parse(value))
    &&Date.parse(value)<=Date.now()+120000;
}
function retryLeaseTime(value){
  // Lease deadlines can legitimately be later than the report observation.
  // Unlike source timestamps they must NOT be capped at now + 2 minutes.
  return typeof value==='string'&&value.length<=45
    &&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d/.test(value)
    &&/(?:Z|[+-]\d\d:\d\d)$/.test(value)&&Number.isFinite(Date.parse(value));
}
function retryScope(raw){
  const known=retryKnownProjects();
  if(!raw||typeof raw!=='object'||Array.isArray(raw)
    ||typeof raw.project!=='string'||(raw.project!=='all'&&!known.has(raw.project))
    ||!Number.isSafeInteger(raw.minimum_claim_attempts)
    ||raw.minimum_claim_attempts<1||raw.minimum_claim_attempts>1000)
    throw Error('Invalid project/threshold scope');
  return {project:raw.project,minimum_claim_attempts:raw.minimum_claim_attempts};
}
function retryCount(number){return Number.isSafeInteger(number)&&number>=0}
function validateRetryIncident(raw){
  if(!raw||typeof raw!=='object'||Array.isArray(raw)
    ||raw.schema!=='zsentinel-retry-incident-v1'
    ||!retryTime(raw.observed_at_utc)
    ||!raw.coverage||raw.coverage.database_read_only!==true
    ||raw.coverage.safe_to_authorize_retry!==false
    ||raw.coverage.attempts_count_claims_not_failures!==true
    ||raw.coverage.requires_release_owner_for_mutations!==true
    ||!Number.isSafeInteger(raw.coverage.sample_limit)
    ||raw.coverage.sample_limit<1||raw.coverage.sample_limit>100
    ||typeof raw.coverage.sample_is_complete!=='boolean'
    ||!Array.isArray(raw.project_groups)||raw.project_groups.length>300
    ||!Array.isArray(raw.high_attempt_samples)
    ||raw.high_attempt_samples.length>raw.coverage.sample_limit)throw Error('Unknown or unsafe retry snapshot');
  const scope=retryScope(raw.scope), known=retryKnownProjects();
  const fields=['active_items','high_attempt_items','ten_plus_items','expired_active_leases','max_claim_attempts'];
  if(!raw.totals||fields.some(k=>!retryCount(raw.totals[k]))
    ||raw.totals.high_attempt_items>raw.totals.active_items
    ||raw.totals.ten_plus_items>raw.totals.active_items
    ||raw.totals.expired_active_leases>raw.totals.active_items)throw Error('Invalid retry totals');
  const groups=[],byProject={};
  for(const item of raw.project_groups){
    if(!item||!known.has(item.project_id)||scope.project!=='all'&&item.project_id!==scope.project
      ||!['P0','P1','P2','P3'].includes(item.priority)
      ||!['queued','claimed','running','verifying'].includes(item.status)
      ||['items','eligible_items','high_attempts','ten_plus','max_attempts','expired_lease_rows']
        .some(k=>!retryCount(item[k]))
      ||item.high_attempts>item.items||item.ten_plus>item.items
      ||item.expired_lease_rows>item.items)throw Error('Invalid retry project group');
    groups.push({project:item.project_id,priority:item.priority,status:item.status,
      items:item.items,eligible:item.eligible_items,tenPlus:item.ten_plus,
      maxAttempts:item.max_attempts,high:item.high_attempts,
      expired:item.expired_lease_rows});
    byProject[item.project_id]=(byProject[item.project_id]||0)+item.high_attempts;
  }
  if(groups.reduce((total,item)=>total+item.items,0)!==raw.totals.active_items
    ||groups.reduce((total,item)=>total+item.high,0)!==raw.totals.high_attempt_items)
    throw Error('Retry counts do not match group coverage');
  const refs=new Set(),rows=[];
  for(const item of raw.high_attempt_samples){
    if(!item||!known.has(item.project)||scope.project!=='all'&&item.project!==scope.project
      ||!['P0','P1','P2','P3'].includes(item.priority)
      ||!['queued','claimed','running','verifying'].includes(item.status)
      ||typeof item.queue_ref!=='string'||!/^[0-9a-f]{16}$/.test(item.queue_ref)
      ||refs.has(item.queue_ref)||!retryCount(item.attempts)
      ||item.attempts<scope.minimum_claim_attempts||typeof item.eligible!=='boolean'
      ||!retryTime(item.updated_at)||item.title!==undefined||item.queue_id!==undefined
      ||(item.lease_expires_at!==null&&!retryLeaseTime(item.lease_expires_at)))
      throw Error('Invalid, duplicate or sensitive retry item');
    refs.add(item.queue_ref);
    rows.push({project:item.project,priority:item.priority,status:item.status,
      ref:item.queue_ref,attempts:item.attempts,eligible:item.eligible,
      updatedAt:item.updated_at,leaseExpires:item.lease_expires_at,
      action:item.status==='queued'?'Investigate claim cycles; do not auto-retry.':
        'Respect current owner; inspect assignment and lease evidence.'});
  }
  if(rows.length>raw.totals.high_attempt_items)throw Error('Retry sample exceeds reported totals');
  rows.sort((a,b)=>({P0:0,P1:1,P2:2,P3:3}[a.priority]-{P0:0,P1:1,P2:2,P3:3}[b.priority])
    ||b.attempts-a.attempts);
  return {observedAt:raw.observed_at_utc,scope,totals:raw.totals,rows,
    groups,highByProject:byProject,complete:raw.coverage.sample_is_complete,
    sampleLimit:raw.coverage.sample_limit};
}
function validateRetryTrend(raw){
  if(!raw||typeof raw!=='object'||Array.isArray(raw)
    ||raw.schema!=='zsentinel-retry-trend-v1'||raw.non_authorizing!==true
    ||!retryTime(raw.before_utc)||!retryTime(raw.after_utc)
    ||Date.parse(raw.after_utc)<=Date.parse(raw.before_utc)
    ||!Array.isArray(raw.projects)||raw.projects.length>80||!raw.metrics)
    throw Error('Invalid retry trend');
  const scope=retryScope(raw.scope),known=retryKnownProjects(),projects=[],seen=new Set();
  for(const item of raw.projects){
    if(!item||!known.has(item.project)||seen.has(item.project)
      ||scope.project!=='all'&&item.project!==scope.project
      ||!retryCount(item.before_high_attempts)||!retryCount(item.after_high_attempts)
      ||!Number.isSafeInteger(item.delta)
      ||item.delta!==item.after_high_attempts-item.before_high_attempts)
      throw Error('Invalid retry trend project');
    seen.add(item.project);projects.push({project:item.project,
      before:item.before_high_attempts,after:item.after_high_attempts,delta:item.delta});
  }
  const metrics={};
  for(const name of ['active_items','high_attempt_items','ten_plus_items','expired_active_leases','max_claim_attempts']){
    const v=raw.metrics[name];
    if(!v||!retryCount(v.before)||!retryCount(v.after)
      ||!Number.isSafeInteger(v.delta)||v.delta!==v.after-v.before)
      throw Error('Invalid retry trend totals');
    metrics[name]={before:v.before,after:v.after,delta:v.delta};
  }
  // Optional extended evidence from zsentinel_retry_trend.py. Preserve legacy
  // aggregate-only imports; fail closed when an extended payload is malformed.
  let sampleCoverage=null,recordChanges=[];
  if(raw.sample_coverage!==undefined||raw.record_changes!==undefined){
    const c=raw.sample_coverage,changes=raw.record_changes;
    const numbers=['previous_sampled','current_sampled','matched_samples',
      'only_previous_sample','only_current_sample',
      'matched_rows_with_increased_claim_cycles','known_increase_in_claim_cycles'];
    if(!c||typeof c!=='object'||!Array.isArray(changes)||changes.length>100
      ||numbers.some(k=>!retryCount(c[k]))
      ||typeof c.both_samples_complete!=='boolean'
      ||c.not_in_both_samples_is_not_completion!==true
      ||c.previous_sampled>100||c.current_sampled>100
      ||c.matched_samples>Math.min(c.previous_sampled,c.current_sampled)
      ||c.only_previous_sample!==c.previous_sampled-c.matched_samples
      ||c.only_current_sample!==c.current_sampled-c.matched_samples)
      throw Error('Invalid retry sample movement coverage');
    const refs=new Set();
    for(const row of changes){
      if(!row||typeof row.queue_ref!=='string'||!/^[0-9a-f]{16}$/.test(row.queue_ref)
        ||refs.has(row.queue_ref)||!known.has(row.project)
        ||scope.project!=='all'&&row.project!==scope.project
        ||!['P0','P1','P2','P3'].includes(row.priority_before)
        ||!['P0','P1','P2','P3'].includes(row.priority_after)
        ||!['queued','claimed','running','verifying'].includes(row.status_before)
        ||!['queued','claimed','running','verifying'].includes(row.status_after)
        ||!retryCount(row.attempts_before)||!retryCount(row.attempts_after)
        ||!Number.isSafeInteger(row.claim_cycle_delta)
        ||row.claim_cycle_delta!==row.attempts_after-row.attempts_before
        ||!['claim_cycles_increased','claim_counter_decreased_review',
             'status_changed','priority_changed'].includes(row.observation)
        ||(row.claim_cycle_delta>0)!==(row.observation==='claim_cycles_increased')
        ||(row.claim_cycle_delta<0)!==(row.observation==='claim_counter_decreased_review')
        ||row.claim_cycle_delta===0&&row.status_before===row.status_after
          &&row.priority_before===row.priority_after)
        throw Error('Invalid retry record movement');
      refs.add(row.queue_ref);
      recordChanges.push({ref:row.queue_ref,project:row.project,
        priority:row.priority_after,beforePriority:row.priority_before,
        beforeStatus:row.status_before,afterStatus:row.status_after,
        beforeAttempts:row.attempts_before,afterAttempts:row.attempts_after,
        delta:row.claim_cycle_delta,observation:row.observation});
    }
    if(changes.length>c.matched_samples
      ||c.matched_rows_with_increased_claim_cycles!==recordChanges.filter(r=>r.delta>0).length
      ||c.known_increase_in_claim_cycles!==recordChanges.reduce((n,r)=>n+Math.max(0,r.delta),0))
      throw Error('Retry movement does not match coverage');
    sampleCoverage={...Object.fromEntries(numbers.map(k=>[k,c[k]])),
      bothSamplesComplete:c.both_samples_complete};
  }
  return {before:raw.before_utc,after:raw.after_utc,scope,projects,metrics,
    sampleCoverage,recordChanges};
}

function retrySameScope(a,b){
  return a?.scope?.project===b?.scope?.project
    &&a?.scope?.minimum_claim_attempts===b?.scope?.minimum_claim_attempts;
}
function retryTrendMatchesSnapshot(trend,snapshot){
  return !trend||!snapshot||(retrySameScope(trend,snapshot)
    &&Date.parse(trend.after)===Date.parse(snapshot.observedAt));
}
function retryTrendFromSnapshots(before,after){
  if(!before||!after||!retrySameScope(before,after)
    ||Date.parse(after.observedAt)<=Date.parse(before.observedAt))return null;
  const metrics={},names=['active_items','high_attempt_items','ten_plus_items',
    'expired_active_leases','max_claim_attempts'];
  for(const key of names){
    const earlier=before.totals[key],later=after.totals[key];
    metrics[key]={before:earlier,after:later,delta:later-earlier};
  }
  const projects=[];
  for(const project of new Set([...Object.keys(before.highByProject),...Object.keys(after.highByProject)])){
    const a=before.highByProject[project]||0,b=after.highByProject[project]||0;
    projects.push({project,before:a,after:b,delta:b-a});
  }
  projects.sort((a,b)=>b.delta-a.delta||a.project.localeCompare(b.project));
  const earlier=new Map(before.rows.map(r=>[r.ref,r]));
  const later=new Map(after.rows.map(r=>[r.ref,r]));
  const matched=[...later.keys()].filter(ref=>earlier.has(ref));
  const recordChanges=[];
  for(const ref of matched){
    const a=earlier.get(ref),b=later.get(ref),delta=b.attempts-a.attempts;
    if(a.project!==b.project)throw Error('Ambiguous queue reference across snapshots');
    if(!delta&&a.status===b.status&&a.priority===b.priority)continue;
    recordChanges.push({ref,project:b.project,priority:b.priority,beforePriority:a.priority,
      beforeStatus:a.status,
      afterStatus:b.status,beforeAttempts:a.attempts,afterAttempts:b.attempts,delta,
      observation:delta>0?'claim_cycles_increased':delta<0?'claim_counter_decreased_review':
        a.status!==b.status?'status_changed':'priority_changed'});
  }
  recordChanges.sort((a,b)=>
    ({P0:0,P1:1,P2:2,P3:3}[a.priority]-{P0:0,P1:1,P2:2,P3:3}[b.priority])
    ||b.delta-a.delta||a.project.localeCompare(b.project));
  const sampleCoverage={
    previous_sampled:earlier.size,current_sampled:later.size,matched_samples:matched.length,
    only_previous_sample:earlier.size-matched.length,
    only_current_sample:later.size-matched.length,
    matched_rows_with_increased_claim_cycles:recordChanges.filter(r=>r.delta>0).length,
    known_increase_in_claim_cycles:recordChanges.reduce((n,r)=>n+Math.max(0,r.delta),0),
    bothSamplesComplete:before.complete&&after.complete,
  };
  return {before:before.observedAt,after:after.observedAt,scope:after.scope,
    projects,metrics,sampleCoverage,recordChanges};
}

async function retryReadJsonFile(file){
  if(!file||file.size<2||file.size>1024*1024)
    throw Error('Each JSON file must be 2 bytes to 1 MiB');
  return JSON.parse(await file.text());
}
async function importRetrySnapshots(files){
  // One or two explicit operator-selected snapshots, never hidden polling.
  // Stage ALL validation before touching the currently visible evidence.
  if(files.length<1||files.length>2)throw Error('Select one or two snapshot JSON files');
  const snapshots=[];
  for(const file of files)snapshots.push(validateRetryIncident(await retryReadJsonFile(file)));
  snapshots.sort((a,b)=>Date.parse(a.observedAt)-Date.parse(b.observedAt));
  if(snapshots.length===2){
    const [before,after]=snapshots;
    if(!retrySameScope(before,after))throw Error('Snapshots have different project or threshold scope');
    if(Date.parse(before.observedAt)>=Date.parse(after.observedAt))
      throw Error('Two snapshots need distinct, increasing observation times');
    const trend=retryTrendFromSnapshots(before,after);
    if(!trend)throw Error('Snapshots cannot form a comparable trend');
    ZSENTINEL_RETRY_SNAPSHOT=after;ZSENTINEL_RETRY_TREND=trend;
    ZSENTINEL_RETRY_MESSAGE='Compared two operator-selected queue snapshots from '+
      date(before.observedAt,true)+' to '+date(after.observedAt,true)+
      '; per-item movement is limited to matched sampled queue refs. No claims changed.';
    return;
  }
  const fresh=snapshots[0],previous=ZSENTINEL_RETRY_SNAPSHOT;
  if(previous&&retrySameScope(previous,fresh)
    &&Date.parse(fresh.observedAt)<=Date.parse(previous.observedAt)){
    throw Error('Snapshot is not newer for this scope; clear evidence or select two snapshots');
  }
  const trend=retryTrendFromSnapshots(previous,fresh);
  ZSENTINEL_RETRY_SNAPSHOT=fresh;ZSENTINEL_RETRY_TREND=trend;
  ZSENTINEL_RETRY_MESSAGE=trend?
    'Compared this snapshot with the previous same-scope observation; matched item movement displayed.':
    'Imported one queue snapshot; import a newer same-scope snapshot to compare. Existing unrelated trend cleared.';
}
async function importRetryTrend(file){
  const trend=validateRetryTrend(await retryReadJsonFile(file));
  if(!retryTrendMatchesSnapshot(trend,ZSENTINEL_RETRY_SNAPSHOT))
    throw Error('Trend scope or ending time differs from displayed queue snapshot; clear evidence first');
  if(ZSENTINEL_RETRY_TREND&&retrySameScope(trend,ZSENTINEL_RETRY_TREND)
      &&Date.parse(trend.after)<=Date.parse(ZSENTINEL_RETRY_TREND.after))
    throw Error('Trend is not newer for this scope; clear existing evidence to import older history');
  ZSENTINEL_RETRY_TREND=trend;
  ZSENTINEL_RETRY_MESSAGE='Source-bound offline retry trend imported; no worker or queue changes.';
}

async function readVpsRetryIncidents(){
  if(ZSENTINEL_RETRY_BUSY||route!=='activity')return;
  const version=routeVersion,project=filter||'all';
  ZSENTINEL_RETRY_BUSY=true;ZSENTINEL_RETRY_MESSAGE='Reading trusted VPS queue source…';render();
  try{
    const response=await api('/api/queue-retry-incidents?project='+encodeURIComponent(project),10000);
    if(routeVersion!==version||(filter||'all')!==project)return;
    const fresh=validateRetryIncident(response);
    if(fresh.scope.project!==project)throw Error('VPS response scope mismatch');
    const previous=ZSENTINEL_RETRY_SNAPSHOT;
    if(previous&&retrySameScope(previous,fresh)
       &&Date.parse(fresh.observedAt)<=Date.parse(previous.observedAt))
      throw Error('Trusted source observation is not newer; current evidence retained');
    // Compare only two explicit operator reads of the same scope. No poll.
    const trend=retryTrendFromSnapshots(previous,fresh);
    ZSENTINEL_RETRY_SNAPSHOT=fresh;
    ZSENTINEL_RETRY_TREND=trend;
    ZSENTINEL_RETRY_MESSAGE='Trusted VPS queue snapshot read at '+date(fresh.observedAt,true)+
      '. '+(trend?'Change since previous operator read is shown.':'A second manual read can show movement.')+
      ' No worker state changed.';
  }catch(error){
    if(routeVersion===version&&(filter||'all')===project)
      ZSENTINEL_RETRY_MESSAGE='VPS retry source unavailable or denied ('+
        (String(error.message||'').includes('403')?'management access required':
         String(error.message||'').includes('503')?'queue read incomplete':'unverified response')+
        '). Existing evidence preserved; offline import remains available.';
  }finally{ZSENTINEL_RETRY_BUSY=false;if(route==='activity')render()}
}

function zSentinelRetryPanel(){
  const snap=ZSENTINEL_RETRY_SNAPSHOT,trend=ZSENTINEL_RETRY_TREND;
  const project=filter,known=retryKnownProjects();
  const samples=(snap?.rows||[]).filter(r=>known.has(r.project)&&(!project||r.project===project));
  const groups=(snap?.groups||[]).filter(r=>known.has(r.project)&&(!project||r.project===project));
  const changed=(trend?.projects||[]).filter(r=>known.has(r.project)&&(!project||r.project===project));
  const observedRows=(trend?.recordChanges||[]).filter(r=>known.has(r.project)
    &&(!project||r.project===project));
  const stale=snap&&Date.now()-Date.parse(snap.observedAt)>6*3600000;
  const trendStale=trend&&Date.now()-Date.parse(trend.after)>6*3600000;
  const trendCovered=!project||(trend&&(trend.scope.project==='all'||trend.scope.project===project));
  const scopedCount=groups.reduce((n,g)=>n+g.high,0);
  const projectCovered=!project||(snap&&(snap.scope.project==='all'||snap.scope.project===project));
  const totals=snap?(projectCovered?(project?scopedCount:snap.totals.high_attempt_items):'unknown'):'unknown';
  return '<section class="panel" id="zSentinelRetryPanel"><div class="panel-header"><div>'+
    '<h2>zSentinel · queue retry incidents</h2><p class="panel-subtitle">'+
    'Import local, read-only queue snapshots and trend evidence; never assign, retry or release from this view.</p></div>'+
    '<button type="button" class="runner-action" data-retry-vps-read '+
    (ZSENTINEL_RETRY_BUSY?'disabled aria-busy="true"':'')+'>'+
    (ZSENTINEL_RETRY_BUSY?'Reading VPS…':'Read VPS queue')+'</button>'+
    '<button type="button" class="runner-action runner-action-secondary" data-retry-handoff '+
    (!snap&&!trend?'disabled':'')+'>Export retry handoff .md</button>'+
    '<button type="button" class="runner-action runner-action-secondary" data-retry-trend-json '+
    (!trend?'disabled':'')+'>Export offline trend .json</button>'+
    '<button type="button" class="runner-action runner-action-secondary" data-retry-snapshot-json '+
    (!snap?'disabled':'')+'>Export snapshot .json</button>'+
    '<button type="button" class="runner-action" data-retry-clear '+
    (!snap&&!trend?'disabled':'')+'>Clear imported evidence</button></div>'+
    '<label for="zSentinelRetryReceiptFile">One or two queue snapshot JSON files (before / after)</label> '+
    '<input id="zSentinelRetryReceiptFile" type="file" multiple accept=".json,application/json" aria-label="Import up to two bounded zSentinel retry incident snapshots"> '+
    '<label for="zSentinelRetryTrendFile">Optional trend JSON</label> '+
    '<input id="zSentinelRetryTrendFile" type="file" accept=".json,application/json" aria-label="Import zSentinel retry trend">'+
    '<p role="status" aria-live="polite" class="detail-note">'+
    esc(ZSENTINEL_RETRY_MESSAGE||(snap?'Snapshot '+date(snap.observedAt,true)+
      ' · '+snap.rows.length+' sampled incidents · '+(snap.complete?'sample marked complete':'partial sample')+
      (stale?' · STALE (>6h)':''):'No retry data imported.'))+'</p>'+
    (snap?'<h3>Observed high-attempt queue items: '+totals+
      (project?' in '+esc(project):' across canonical projects')+'</h3>'+
      '<p class="detail-note">Claims are NOT failures. Source '+esc(date(snap.observedAt,true))+
      (stale?' (stale)':'')+' · threshold '+snap.scope.minimum_claim_attempts+
      ' · bounded '+snap.rows.length+'/'+snap.sampleLimit+' sample. '+
      (!projectCovered?'NO COVERAGE FOR SELECTED PROJECT — count unknown; import its source or select All projects.':
        'Owner and lease status are not independently verified.')+
      '</p><div class="service-list">'+
      (samples.slice(0,ZSENTINEL_RETRY_EXPANDED_ITEMS?100:12).map(item=>'<div class="service-row"><div><strong>'+
        esc(item.priority)+' · '+esc(item.project)+' · '+item.attempts+' claim cycles</strong><br>'+
        '<small>'+esc(item.status)+' · '+esc(item.ref)+' · '+esc(item.action)+'</small></div>'+
        '<small>'+esc(date(item.updatedAt,true))+'</small></div>').join('')||
        '<p class="detail-note">No sample for current project. This does not prove zero incidents.</p>')+
      (samples.length>12?'<button type="button" class="runner-action runner-action-secondary" data-retry-items-expand>'+
        (ZSENTINEL_RETRY_EXPANDED_ITEMS?'Show first 12 items':'Show all '+samples.length+' sampled items')+'</button>':'')+
      '</div>':'')+
    (trend?'<h3>Retry movement (offline snapshots)</h3>'+
      '<p class="detail-note">'+esc(date(trend.before,true))+' → '+esc(date(trend.after,true))+
      (trendStale?' · STALE (>6h)':'')+
      (trendCovered?' · scoped to '+esc(trend.scope.project):' · NO COVERAGE for selected project')+
      '. Counts describe observed queue state, not confirmed failures or recovery.</p>'+
      '<div class="service-list">'+(changed.slice(0,18).map(item=>
        '<div class="service-row"><div><strong>'+esc(item.project)+
        '</strong><br><small>High-attempt items '+item.before+' → '+item.after+
        '</small></div><strong>'+esc(item.delta>0?'+'+item.delta:String(item.delta))+
        '</strong></div>').join('')||
        '<p class="detail-note">'+(trendCovered?'No movement in available project sample.':'Project movement unknown: imported trend covers another scope.')+'</p>')+
      '</div>':'')+
    (trend?.sampleCoverage?'<h3>Matched individual queue items</h3>'+
      '<p class="detail-note">Compared '+trend.sampleCoverage.matched_samples+' opaque references · '+
      trend.sampleCoverage.matched_rows_with_increased_claim_cycles+
      ' matched items gained '+trend.sampleCoverage.known_increase_in_claim_cycles+
      ' claim cycles. Earlier-only '+trend.sampleCoverage.only_previous_sample+
      ' · later-only '+trend.sampleCoverage.only_current_sample+
      '. '+(trend.sampleCoverage.bothSamplesComplete?'Both bounded samples marked complete.':
        'PARTIAL: one or both bounded samples may omit items.')+
      ' Disappearance from a sample never proves completion.</p>'+
      '<div class="service-list">'+
      (observedRows.slice(0,ZSENTINEL_RETRY_EXPANDED_MOVEMENT?100:12).map(row=>'<div class="service-row"><div><strong>'+
        esc(row.priority)+' · '+esc(row.project)+' · '+esc(row.ref)+
        '</strong><br><small>'+esc(row.beforeStatus)+' → '+esc(row.afterStatus)+
        ' · '+row.beforeAttempts+' → '+row.afterAttempts+' claims ('+
        esc(row.delta>0?'+'+row.delta:String(row.delta))+') · '+esc(row.observation)+
        '</small></div></div>').join('')||
        '<p class="detail-note">No changed items in the matched sample; does not prove no churn.</p>')+
      (observedRows.length>12?'<button type="button" class="runner-action runner-action-secondary" data-retry-movement-expand>'+
        (ZSENTINEL_RETRY_EXPANDED_MOVEMENT?'Show first 12 changes':'Show all '+observedRows.length+' matched changes')+'</button>':'')+
      '</div>':'')+
    '<p class="detail-note">Local browser memory only. No upload, auto-polling, credential storage, queue write, claim takeover or retry dispatch. '+
    '<a href="https://github.com/Zennay/zCloud/issues/677" target="_blank" rel="noopener noreferrer">Queue retry issue #677</a>.</p></section>';
}
function exportRetrySnapshotJson(){
  // Canonical safe subset of one explicitly observed, validated snapshot.
  // Deliberately never copies the untrusted raw response or its extra fields.
  const snap=ZSENTINEL_RETRY_SNAPSHOT;
  if(!snap)return;
  const report={
    schema:'zsentinel-retry-incident-v1',
    observed_at_utc:snap.observedAt,
    scope:{...snap.scope},
    coverage:{
      active_queue_states_only:['queued','claimed','running','verifying'],
      sample_limit:snap.sampleLimit,
      sample_is_complete:snap.complete,
      database_read_only:true,safe_to_authorize_retry:false,
      attempts_count_claims_not_failures:true,
      requires_release_owner_for_mutations:true
    },
    totals:Object.fromEntries(['active_items','high_attempt_items','ten_plus_items',
      'expired_active_leases','max_claim_attempts'].map(key=>[key,snap.totals[key]])),
    project_groups:snap.groups.map(row=>({
      project_id:row.project,priority:row.priority,status:row.status,
      items:row.items,eligible_items:row.eligible,high_attempts:row.high,
      ten_plus:row.tenPlus,max_attempts:row.maxAttempts,
      expired_lease_rows:row.expired,
      interpretation:row.status==='queued'?'candidate-for-manual-retry-policy-review':
        'requires-owner-review: active claim/lease'
    })),
    high_attempt_samples:snap.rows.map(row=>({
      queue_ref:row.ref,project:row.project,priority:row.priority,status:row.status,
      attempts:row.attempts,eligible:row.eligible,updated_at:row.updatedAt,
      lease_expires_at:row.leaseExpires,operator_action:row.action
    })),
    operator_note:'Explicit operator observation only; claim attempts are not proven failures and never grant retry, release or worker ownership.'
  };
  const blob=new Blob([JSON.stringify(report,null,2)+'\n'],{type:'application/json;charset=utf-8'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;
  link.download='zsentinel-retry-snapshot-'+snap.scope.project+'-'+
    snap.observedAt.slice(0,19).replace(/[:]/g,'-')+'.json';
  document.body.appendChild(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function exportRetryTrendJson(){
  // Explicit client-side export from previously validated evidence.
  // No stored raw queue identities, titles, prompt text, credentials or API calls.
  const trend=ZSENTINEL_RETRY_TREND;
  if(!trend||!retryTrendMatchesSnapshot(trend,ZSENTINEL_RETRY_SNAPSHOT))return;
  const coverage=trend.sampleCoverage;
  const raw={
    schema:'zsentinel-retry-trend-v1',before_utc:trend.before,after_utc:trend.after,
    scope:{...trend.scope},
    metrics:Object.fromEntries(Object.entries(trend.metrics).map(([key,row])=>[
      key,{before:row.before,after:row.after,delta:row.delta}])),
    projects:trend.projects.map(row=>({
      project:row.project,before_high_attempts:row.before,
      after_high_attempts:row.after,delta:row.delta})),
    worsening_projects:trend.projects.filter(row=>row.delta>0).length,
    non_authorizing:true,
    notes:'Operator-observed queue claim cycles, not confirmed failures. Missing sample rows are not completed tasks, and this JSON grants no retry or claim authority.'
  };
  if(coverage){
    raw.sample_coverage={
      previous_sampled:coverage.previous_sampled,
      current_sampled:coverage.current_sampled,
      matched_samples:coverage.matched_samples,
      only_previous_sample:coverage.only_previous_sample,
      only_current_sample:coverage.only_current_sample,
      both_samples_complete:coverage.bothSamplesComplete,
      matched_rows_with_increased_claim_cycles:coverage.matched_rows_with_increased_claim_cycles,
      known_increase_in_claim_cycles:coverage.known_increase_in_claim_cycles,
      not_in_both_samples_is_not_completion:true,
    };
    raw.record_changes=(trend.recordChanges||[]).slice(0,100).map(row=>({
      queue_ref:row.ref,project:row.project,
      priority_before:row.beforePriority,priority_after:row.priority,
      status_before:row.beforeStatus,status_after:row.afterStatus,
      attempts_before:row.beforeAttempts,attempts_after:row.afterAttempts,
      claim_cycle_delta:row.delta,observation:row.observation
    }));
  }
  const payload=JSON.stringify(raw,null,2)+'\n';
  const blob=new Blob([payload],{type:'application/json;charset=utf-8'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download='zsentinel-retry-trend-'+String(trend.scope.project).replace(/[^a-z0-9_-]/g,'')+'.json';
  document.body.appendChild(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function exportRetryHandoff(){
  if(!ZSENTINEL_RETRY_SNAPSHOT&&!ZSENTINEL_RETRY_TREND)return;
  const snap=ZSENTINEL_RETRY_SNAPSHOT,trend=ZSENTINEL_RETRY_TREND,known=retryKnownProjects();
  const clean=value=>String(value??'').replace(/[\r\n<>|]/g,' ').slice(0,160);
  const lines=['# zSentinel queue retry incident handoff','',
    'LOCAL SNAPSHOT ONLY · No retry/claim/release authorization. Confirm current Notion ownership and required CI gates.',''];
  if(snap){
    lines.push('Observed at: '+clean(snap.observedAt),
      'Project scope: '+clean(snap.scope.project),
      'Claim-attempt threshold: '+snap.scope.minimum_claim_attempts,
      'Sample complete: '+snap.complete,
      'High-attempt items: '+snap.totals.high_attempt_items,'');
    for(const row of snap.rows.filter(x=>known.has(x.project)&&(!filter||x.project===filter)).slice(0,30))
      lines.push('- '+row.priority+' '+row.project+' ['+row.ref+']: '+row.attempts+
        ' claims · '+row.status+' · owner unverified; do not auto-retry.');
    if(!snap.complete)lines.push('PARTIAL coverage: do not infer a complete list.');
  }else lines.push('No queue snapshot available.');
  if(trend){
    lines.push('','## Offline trend',clean(trend.before)+' → '+clean(trend.after),'');
    for(const row of trend.projects.filter(x=>known.has(x.project)&&(!filter||x.project===filter)).slice(0,30))
      lines.push('- '+row.project+': '+row.before+' → '+row.after+' ('+(row.delta>0?'+':'')+row.delta+')');
    if(trend.sampleCoverage){
      const c=trend.sampleCoverage;
      lines.push('','## Matched queue-item movement',
        'Matched '+c.matched_samples+' items; additional claim cycles observed: '+
          c.known_increase_in_claim_cycles+' across '+
          c.matched_rows_with_increased_claim_cycles+' matched rows.',
        'Only in earlier sample: '+c.only_previous_sample+
          '; only in later sample: '+c.only_current_sample+
          '; both samples complete: '+c.bothSamplesComplete+'.',
        'Not sampled in both does NOT prove completion, creation or release.','');
      for(const row of (trend.recordChanges||[]).filter(x=>known.has(x.project)
          &&(!filter||x.project===filter)).slice(0,30))
        lines.push('- '+row.project+' ['+row.ref+']: '+row.beforeAttempts+' → '+
          row.afterAttempts+' claim cycles ('+(row.delta>0?'+':'')+row.delta+
          '); '+row.beforeStatus+' → '+row.afterStatus+'; '+row.observation+'.');
    }
  }else lines.push('','No trend snapshot available.');
  lines.push('','Read-only evidence only. Preserve original claims and the serialized runtime owner/release gates.','');
  const blob=new Blob([lines.join('\n')],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob);
  const anchor=document.createElement('a');
  anchor.href=url;anchor.download='zsentinel-retry-'+clean(filter||'portfolio')+'.md';
  document.body.appendChild(anchor);anchor.click();anchor.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

// Issue #1518: unified, explicitly sourced operator timeline.
//
// Evidence reaches this view only through the existing verified per-project
// local Activity API or validated, manually requested GitHub observations.
// No new poller, API mutation, source authorization or background fetch.
let COMBINED_ACTIVITY_SOURCE='all';
const COMBINED_ACTIVITY_LIMIT=140;
function combinedActivityRows(){
  const visible=new Set((DATA?.projects||[]).map(project=>project.id));
  const groups=new Map([...visible].filter(id=>!filter||id===filter).map(id=>[id,[]]));
  const seen=new Set();
  // Same immutable commit in both inventories is one code change, not two.
  // Match only exact 40-hex SHA and canonical project; never conflate a
  // branch PR with a main merge or dedupe by a fuzzy title/date.
  const githubMainByCommit=new Map();
  for(const item of IMPORTED_GITHUB_ACTIVITY?.events||[]){
    if(item?.kind!=='main_commit'||!groups.has(item.project)
        ||typeof item.id!=='string'||typeof item.url!=='string')continue;
    const match=/^github:Zennay\/[A-Za-z0-9_.-]{1,100}:main_commit:([0-9a-f]{40})$/.exec(item.id);
    if(match&&item.url.endsWith('/commit/'+match[1]))
      githubMainByCommit.set(item.project+':'+match[1],item);
  }
  const pairedMainCommits=new Set();
  for(const row of ACTIVITY||[]){
    if(!row||!groups.has(row.project)||typeof row.id!=='string'
        ||typeof row.title!=='string'||typeof row.ts!=='string'
        ||!Number.isFinite(Date.parse(row.ts)))continue;
    const key='local:'+row.project+':'+row.id;
    if(seen.has(key))continue;
    seen.add(key);
    const sha=row.kind==='commit'&&row.id.startsWith(row.project+':git:')
      ?row.id.slice((row.project+':git:').length):'';
    const matched=/^[0-9a-f]{40}$/.test(sha)
      ?githubMainByCommit.get(row.project+':'+sha):null;
    if(matched)pairedMainCommits.add(matched.id);
    groups.get(row.project).push({key,source:matched?'both':'local',project:row.project,
      kind:matched?'main_commit':row.kind,summary:row.title,
      detail:matched?'Local checkout + independently observed GitHub main; not deployment proof':
        row.detail||'Local project event',
      observedAt:matched?
        (IMPORTED_GITHUB_ACTIVITY?.coverage||[]).find(c=>c.project===row.project)?.observedAt||row.ts:
        row.ts,
      ts:row.ts,url:matched?matched.url:''});
  }
  for(const row of IMPORTED_GITHUB_ACTIVITY?.events||[]){
    if(!row||!groups.has(row.project)||!GITHUB_RECEIPT_KINDS.has(row.kind)
        ||typeof row.id!=='string'||typeof row.description!=='string'
        ||typeof row.url!=='string'||typeof row.ts!=='string'
        ||!/^https:\/\/github\.com\/Zennay\/[A-Za-z0-9_.-]{1,100}\/(?:pull|commit|actions\/runs)\/[A-Za-z0-9]+$/.test(row.url)
        ||!Number.isFinite(Date.parse(row.ts)))continue;
    const key='github:'+row.project+':'+row.id;
    if(seen.has(key)||pairedMainCommits.has(row.id))continue;
    seen.add(key);
    groups.get(row.project).push({key,source:'github',project:row.project,
      kind:row.kind,summary:row.description,
      detail:row.firstSeen?'New in latest bounded GitHub observation':'GitHub observation',
      observedAt:(IMPORTED_GITHUB_ACTIVITY?.coverage||[]).find(c=>c.project===row.project)?.observedAt||null,
      ts:row.ts,url:row.url});
  }
  // Operator-imported PR-less zCloud branches carry an actual last-commit
  // timestamp but only a bounded source-observation moment. They are not
  // GitHub main commits, merge proof or evidence of an active Notion owner.
  const zcloud=(DATA?.projects||[]).find(p=>
    /^https:\/\/github\.com\/Zennay\/zCloud\/?$/.test(p.repo_url||''));
  const imported=IMPORTED_PRLESS_BRANCHES;
  if(zcloud&&groups.has(zcloud.id)&&imported?.repository==='Zennay/zCloud'){
    for(const row of (imported.branches||[]).slice(0,96)){
      if(!row||typeof row.ref!=='string'||!/^[A-Za-z0-9_./-]{1,200}$/.test(row.ref)
          ||row.ref.includes('..')||!(/^[a-f0-9]{40}$/).test(row.sha)
          ||!Number.isFinite(Date.parse(row.lastCommit))
          ||Date.parse(row.lastCommit)>Date.now()+300000)continue;
      const key='prless:'+zcloud.id+':'+row.ref+':'+row.sha;
      if(seen.has(key))continue;
      seen.add(key);
      groups.get(zcloud.id).push({
        key,source:'github',project:zcloud.id,kind:'branch_observed',
        summary:'PR-less branch '+row.ref+' · '+row.classification,
        detail:'Offline imported branch inventory; owner NOT verified; '+row.sha.slice(0,12)+
          ' · '+(row.ahead===null?'ahead count unknown':'ahead '+row.ahead)+
          (row.overlapCount?' · possible path overlaps '+row.overlapCount:''),
        observedAt:imported.observedAt,ts:row.lastCommit,url:'',
      });
    }
  }
  for(const rows of groups.values()){
    rows.sort((a,b)=>Date.parse(b.ts)-Date.parse(a.ts)||a.key.localeCompare(b.key));
  }
  // Reserve turns for quiet projects instead of allowing a flood from zCloud
  // or Supa to crowd out every other project's latest event.
  const keys=[...groups.keys()].sort(),cursor=new Map(keys.map(key=>[key,0]));
  const output=[];
  while(output.length<COMBINED_ACTIVITY_LIMIT){
    let changed=false;
    for(const project of keys){
      const rows=groups.get(project),index=cursor.get(project);
      if(index<rows.length){
        const row=rows[index];
        cursor.set(project,index+1);
        if(COMBINED_ACTIVITY_SOURCE==='all'||row.source==='both'
            ||row.source===COMBINED_ACTIVITY_SOURCE){
          output.push(row);
        }
        changed=true;
      }
      if(output.length>=COMBINED_ACTIVITY_LIMIT)break;
    }
    if(!changed)break;
  }
  return output.sort((a,b)=>Date.parse(b.ts)-Date.parse(a.ts)||a.key.localeCompare(b.key));
}
function combinedActivityCoverage(){
  const visible=new Set((DATA?.projects||[]).map(p=>p.id));
  const scope=[...visible].filter(id=>!filter||id===filter);
  const local=ACTIVITY_COVERAGE,coverage=(IMPORTED_GITHUB_ACTIVITY?.coverage||[])
    .filter(c=>scope.includes(c.project));
  const liveProjects=coverage.filter(c=>c.status==='partial_sources').length;
  const stale=coverage.filter(c=>c.staleEvidence||
    !Number.isFinite(Date.parse(c.observedAt))||Date.now()-Date.parse(c.observedAt)>6*3600000).length;
  const missing=scope.length-coverage.length;
  const textParts=[
    'Local VPS '+(local.lastSuccess?'last read '+date(local.lastSuccess,true):'not yet read'),
    'GitHub '+coverage.length+'/'+scope.length+' projects observed in bounded windows',
    missing?'missing '+missing:'',stale?'stale '+stale:'',
    liveProjects?'partial source '+liveProjects:'',
    'never a complete GitHub or production ledger'
  ];
  if(IMPORTED_PRLESS_BRANCHES&&scope.includes(
      (DATA?.projects||[]).find(p=>/^https:\/\/github\.com\/Zennay\/zCloud\/?$/.test(p.repo_url||''))?.id)){
    textParts.push('PR-less branch inventory '+IMPORTED_PRLESS_BRANCHES.scanned+
      ' sampled, '+(IMPORTED_PRLESS_BRANCHES.coverageComplete?'whole when observed':'PARTIAL')+
      (Date.now()-Date.parse(IMPORTED_PRLESS_BRANCHES.observedAt)>6*3600000?' (stale)':''));
  }
  return textParts.filter(Boolean).join(' · ');
}
function combinedProjectCoverageRows(){
  const projectIds=(DATA?.projects||[]).map(project=>project.id).filter(id=>!filter||filter===id);
  const observations=IMPORTED_GITHUB_ACTIVITY?.coverage||[];
  const current=Date.now(),rows=[];
  for(const id of projectIds.slice(0,24)){
    const local=(ACTIVITY||[]).filter(item=>item?.project===id
      &&Number.isFinite(Date.parse(item.ts)));
    const github=(IMPORTED_GITHUB_ACTIVITY?.events||[]).filter(item=>item?.project===id
      &&Number.isFinite(Date.parse(item.ts)));
    const localLast=local.length?local.reduce((x,y)=>Date.parse(x.ts)>=Date.parse(y.ts)?x:y).ts:null;
    const ghLast=github.length?github.reduce((x,y)=>Date.parse(x.ts)>=Date.parse(y.ts)?x:y).ts:null;
    const ghCoverage=observations.find(c=>c.project===id);
    const syncAt=ghCoverage?.observedAt||null;
    const localFresh=localLast&&current-Date.parse(localLast)<=6*3600000;
    const ghFresh=syncAt&&current-Date.parse(syncAt)<=6*3600000;
    // A read source returning zero events is not proof of no source activity.
    const status=!ghCoverage?'not observed':ghCoverage.status==='unavailable'
      ?'unavailable':!ghFresh||ghCoverage.staleEvidence?'stale/partial':
      ghCoverage.status||'partial';
    rows.push({project:id,localLast,ghLast,syncAt,status,localFresh:Boolean(localFresh)});
  }
  return rows;
}
function combinedProjectCoverageTable(){
  const entries=combinedProjectCoverageRows();
  const missing=entries.filter(r=>r.status==='not observed').length;
  const stale=entries.filter(r=>['stale/partial','unavailable'].includes(r.status)).length;
  return '<details data-disclosure="timelineCoverage"><summary>Project source coverage ('+
    entries.length+' projects · '+missing+' GitHub unobserved · '+stale+' GitHub unavailable/stale)</summary>'+
    '<div class="service-list">'+entries.map(row=>
      '<div class="service-row"><div><a class="text-link" href="#activity?project='+encodeURIComponent(row.project)+'">'+
      esc((DATA.projects.find(p=>p.id===row.project)||{}).name||row.project)+'</a><br>'+
      '<small>Local last event: '+(row.localLast?esc(date(row.localLast,true)):'unknown')+
      ' · GitHub last event: '+(row.ghLast?esc(date(row.ghLast,true)):'unknown')+
      '</small></div><small>GitHub '+esc(row.status)+'<br>'+
      (row.syncAt?esc(date(row.syncAt,true)):'not fetched')+'</small></div>').join('')+
    '</div><p class="detail-note">This table measures observed evidence coverage, not product completion, actual claim ownership or deployment. Stale data is retained, not treated as no work.</p></details>';
}
function combinedActivityPanel(){
  const rows=combinedActivityRows();
  const list=rows.map(row=>{
    const project=DATA.projects.find(p=>p.id===row.project);
    const main=row.url?
      '<a href="'+esc(row.url)+'" rel="noopener noreferrer" target="_blank">'+esc(row.summary)+'</a>':
      '<strong>'+esc(row.summary)+'</strong>';
    const source=row.source==='both'?'Local VPS + GitHub main commit':
      row.source==='github'?'GitHub '+row.kind.replaceAll('_',' '):
      'Local VPS '+(row.kind||'event');
    const freshness=row.observedAt&&Date.now()-Date.parse(row.observedAt)>6*3600000?' · STALE OBSERVATION':'';
    return '<div class="service-row"><div>'+main+'<br><small>'+
      esc(project?.name||row.project)+' · '+esc(source)+
      ' · '+esc(row.detail)+esc(freshness)+'</small></div><small>'+
      esc(date(row.ts,true))+'</small></div>';
  }).join('');
  return '<section class="panel" id="combinedActivityTimeline">'+
    '<div class="panel-header"><div><h2>Project timeline · VPS + GitHub</h2>'+
    '<p class="panel-subtitle">Unified read-only evidence. A PR, CI completion, and main merge are different events; none alone proves VPS deployment.</p></div>'+
    '<button type="button" class="runner-action" data-activity-timeline-source-read '+
    (ZSENTINEL_SOURCES_BUSY||ZSENTINEL_ALL_RUNNING||PUBLIC_GITHUB_BUSY||PUBLIC_ISSUES_BUSY?'disabled aria-busy="true"':'')+'>'+
    (ZSENTINEL_ALL_RUNNING?'Reading portfolio…':filter?'Read project sources':'Read next 4 project sources')+'</button>'+
    '<button type="button" class="runner-action runner-action-secondary" data-activity-timeline-export>'+
    'Export timeline .md</button></div>'+
    '<div role="group" aria-label="Timeline source" class="range-control">'+
    [['all','All sources'],['local','Local VPS'],['github','GitHub']].map(pair=>
      '<button type="button" data-activity-source="'+pair[0]+'" class="'+
      (COMBINED_ACTIVITY_SOURCE===pair[0]?'active':'')+'" aria-pressed="'+
      (COMBINED_ACTIVITY_SOURCE===pair[0]?'true':'false')+'">'+pair[1]+'</button>').join('')+
    '</div>'+
    '<p class="detail-note" role="status">'+esc(combinedActivityCoverage())+'</p>'+
    combinedProjectCoverageTable()+
    '<div class="service-list">'+(list||'<div class="empty">No evidence for the selected source and project. Missing coverage is not proof of zero work.</div>')+'</div>'+
    '<p class="detail-note">'+rows.length+' visible bounded events; local event IDs and GitHub provider IDs stay distinct. '+
    'Use the separate manual source buttons to refresh GitHub. No automatic GitHub polling, lease assignment, PR merge or deploy occurs here.</p></section>';
}
function exportCombinedActivityTimeline(){
  const rows=combinedActivityRows(),clean=value=>String(value||'').replace(/[\r\n<>|]/g,' ').slice(0,200);
  const lines=['# zCloud combined Activity handoff','','Observation only · no claim/merge/deploy authorization.',
    'Source scope: '+clean(COMBINED_ACTIVITY_SOURCE),'Project filter: '+clean(filter||'all'),'',
    combinedActivityCoverage(),'','## Bounded timeline',''];
  for(const row of rows){
    lines.push('- '+clean(row.ts)+' · '+clean(row.project)+' · '+clean(row.source)+
      '/'+clean(row.kind)+' · '+clean(row.summary)+
      (row.url?' · '+row.url:''));
  }
  lines.push('','Missing provider coverage is unknown, not zero. An Actions run is not production proof.','');
  const blob=new Blob([lines.join('\n')],{type:'text/markdown;charset=utf-8'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download='zcloud-activity-'+clean(filter||'portfolio')+'.md';
  document.body.appendChild(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function activityPage(){
  const visible=new Set(DATA.projects.map(p=>p.id));
  const list=ACTIVITY.filter(a=>visible.has(a.project)&&(!filter||a.project===filter));
  const coverage=activityCoverageText();
  return `<div class="activity-page">${heading('WORKSPACE / ACTIVITY LOG','Local project activity.','Local VPS commits and milestones, not a complete GitHub activity history.',`<select class="filter-select" id="activityFilter" aria-label="Filter activity by project"><option value="">All projects</option>${DATA.projects.map(p=>`<option value="${esc(p.id)}" ${filter===p.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select>`)}
  ${zSentinelCapacityNotice()}
  ${combinedActivityPanel()}
  ${zSentinelDeliveryBoard()}
  ${zSentinelRetryPanel()}
  ${zSentinelBranchEvidencePanel()}
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
document.addEventListener('click',async e=>{const timelineRead=e.target.closest('[data-activity-timeline-source-read]');if(timelineRead){e.preventDefault();if(filter)void readZSentinelSources();else void readZSentinelAllProjects();return}const activitySource=e.target.closest('[data-activity-source]');if(activitySource){e.preventDefault();const next=activitySource.dataset.activitySource;if(['all','local','github'].includes(next)){COMBINED_ACTIVITY_SOURCE=next;render();}return}const activityTimelineExport=e.target.closest('[data-activity-timeline-export]');if(activityTimelineExport){e.preventDefault();exportCombinedActivityTimeline();return}const expandItems=e.target.closest('[data-retry-items-expand]');if(expandItems){e.preventDefault();ZSENTINEL_RETRY_EXPANDED_ITEMS=!ZSENTINEL_RETRY_EXPANDED_ITEMS;render();return}
const expandMovement=e.target.closest('[data-retry-movement-expand]');if(expandMovement){e.preventDefault();ZSENTINEL_RETRY_EXPANDED_MOVEMENT=!ZSENTINEL_RETRY_EXPANDED_MOVEMENT;render();return}
const retryVps=e.target.closest('[data-retry-vps-read]');if(retryVps){e.preventDefault();void readVpsRetryIncidents();return}const retryClear=e.target.closest('[data-retry-clear]');if(retryClear){e.preventDefault();ZSENTINEL_RETRY_SNAPSHOT=null;ZSENTINEL_RETRY_TREND=null;ZSENTINEL_RETRY_MESSAGE='';ZSENTINEL_RETRY_EXPANDED_ITEMS=false;ZSENTINEL_RETRY_EXPANDED_MOVEMENT=false;render();return}const retryExport=e.target.closest('[data-retry-handoff]');if(retryExport){e.preventDefault();exportRetryHandoff();return}const retryTrendJson=e.target.closest('[data-retry-trend-json]');if(retryTrendJson){e.preventDefault();exportRetryTrendJson();return}const retrySnapshotJson=e.target.closest('[data-retry-snapshot-json]');if(retrySnapshotJson){e.preventDefault();exportRetrySnapshotJson();return}const vpsIssuesRead=e.target.closest('[data-vps-issues-read]');if(vpsIssuesRead){e.preventDefault();void readVpsProductIssues();return}const vpsGithubRead=e.target.closest('[data-vps-github-refresh]');if(vpsGithubRead){e.preventDefault();void readVpsGitHubActivity();return}const branchImport=e.target.closest('[data-zsentinel-branch-open]');if(branchImport){e.preventDefault();document.getElementById('zSentinelBranchFile')?.click();return}const branchClear=e.target.closest('[data-zsentinel-branch-clear]');if(branchClear){e.preventDefault();IMPORTED_PRLESS_BRANCHES=null;IMPORTED_PRLESS_ERROR='';render();return}const zSentinelExport=e.target.closest('[data-zsentinel-export]');if(zSentinelExport){e.preventDefault();exportZSentinelHandoff();return}const zSentinelAll=e.target.closest('[data-zsentinel-all-read]');if(zSentinelAll){e.preventDefault();void readZSentinelAllProjects();return}const zSentinelRead=e.target.closest('[data-zsentinel-source-read]');if(zSentinelRead){e.preventDefault();void readZSentinelSources();return}const localPage=e.target.closest('[data-local-activity-next]');if(localPage){e.preventDefault();void readPagedLocalActivity();return}const deliveryRefresh=e.target.closest('[data-delivery-queue-refresh]');if(deliveryRefresh){e.preventDefault();void refreshDeliveryQueue();return}const progressRefresh=e.target.closest('[data-worker-progress-refresh]');if(progressRefresh){e.preventDefault();void refreshWorkerProgress();return}const issueRefresh=e.target.closest('[data-public-issues-refresh]');if(issueRefresh){e.preventDefault();void refreshPublicProductIssues();return}const publicRefresh=e.target.closest('[data-github-public-refresh]');if(publicRefresh){e.preventDefault();void refreshPublicGitHubEvidence();return}const priorityToggle=e.target.closest('[data-product-issues-toggle]');if(priorityToggle){e.preventDefault();IMPORTED_ISSUE_SHOW_ALL=!IMPORTED_ISSUE_SHOW_ALL;render();return}const issueClear=e.target.closest('[data-product-issues-clear]');if(issueClear){e.preventDefault();IMPORTED_PRODUCT_ISSUES=null;IMPORTED_PRODUCT_ERROR='';IMPORTED_ISSUE_SHOW_ALL=false;render();return}const githubClear=e.target.closest('[data-github-receipt-clear]');if(githubClear){e.preventDefault();IMPORTED_GITHUB_ACTIVITY=null;IMPORTED_GITHUB_ERROR='';render();return}const activityRefresh=e.target.closest('[data-activity-refresh]');if(activityRefresh){e.preventDefault();if(!ACTIVITY_COVERAGE.pending){ACTIVITY_PROJECT_FETCH_TIME=0;void refreshProjectActivity(routeVersion,true)}return}const workerShortcut=e.target.closest('[data-jump-workers]');if(workerShortcut){e.preventDefault();e.stopPropagation();mobileWorkersActive=true;if(route!=='overview'){scrollToWorkersOnOverview=true;location.hash='#overview'}else{if(DATA)syncNav();const panel=$('workers');if(panel){panel.scrollIntoView({behavior:'smooth',block:'start'});panel.focus({preventScroll:true})}}return}const navAction=e.target.closest('[data-nav]');if(navAction){e.preventDefault();mobileWorkersActive=false;const next=String(navAction.dataset.nav||'overview');const nextHash='#'+next;if(location.hash===nextHash){route=next;if(DATA){render();loadExtras()}window.scrollTo({top:0,behavior:'instant'})}else{location.hash=next}return}const attentionResolve=e.target.closest('[data-attention-resolve]');if(attentionResolve){e.preventDefault();e.stopPropagation();attentionResolve.disabled=true;try{await post('/api/portfolio-attention',{action:'resolve',attention_id:attentionResolve.dataset.attentionResolve});DATA.attention_needed=(DATA.attention_needed||[]).filter(item=>item.attention_id!==attentionResolve.dataset.attentionResolve);render()}catch(err){attentionResolve.disabled=false;$('notice').hidden=false;$('notice').textContent='Could not mark the attention item done: '+(err.message||err)}return}const globalPlay=e.target.closest('[data-global-worker-play]');if(globalPlay){e.preventDefault();e.stopPropagation();await playGlobalWorker(Number(globalPlay.dataset.globalWorkerPlay),globalPlay);return}const globalForce=e.target.closest('[data-global-worker-force]');if(globalForce){e.preventDefault();e.stopPropagation();await forceGlobalWorker(Number(globalForce.dataset.globalWorkerForce),globalForce);return}const dynamicSave=e.target.closest('[data-save-dynamic-workers]');if(dynamicSave){e.preventDefault();e.stopPropagation();await saveDynamicWorkerSettings(dynamicSave);return}const workerAction=e.target.closest('[data-worker-action]');if(workerAction){e.preventDefault();e.stopPropagation();await controlWorker(workerAction.dataset.workerId,workerAction.dataset.workerAction,workerAction);return}const forceStartAction=e.target.closest('[data-runner-force-start]');if(forceStartAction){e.preventDefault();e.stopPropagation();await forceStartProject(forceStartAction.dataset.runnerForceStart,forceStartAction);return}const toggleAction=e.target.closest('[data-runner-toggle]');if(toggleAction){e.preventDefault();e.stopPropagation();await setRunnerActive(toggleAction.dataset.runnerToggle,toggleAction.dataset.runnerAction,toggleAction);return}const serviceAction=e.target.closest('[data-runner-service-restart]');if(serviceAction){e.preventDefault();await restartFirefoxInitiator(serviceAction);return}const startAction=e.target.closest('[data-runner-start]');if(startAction){e.preventDefault();e.stopPropagation();await setRunnerActive(startAction.dataset.runnerStart,'start',startAction);return}const pauseAction=e.target.closest('[data-runner-pause]');if(pauseAction){e.preventDefault();e.stopPropagation();await setRunnerActive(pauseAction.dataset.runnerPause,'pause',pauseAction);return}const pushAction=e.target.closest('[data-runner-push]');if(pushAction){e.preventDefault();e.stopPropagation();await pushRunner(pushAction.dataset.runnerPush,pushAction);return}const runnerAction=e.target.closest('[data-runner-new-chat]');if(runnerAction){e.preventDefault();e.stopPropagation();await restartRunner(runnerAction.dataset.runnerNewChat,runnerAction);return}const archive=e.target.closest('[data-archive]');if(archive){e.preventDefault();e.stopPropagation();if(projectLayoutActionPending)return;const id=archive.dataset.archive;const archived=[...(DATA.archived_projects||[]).map(p=>p.id),id];await changeProjectLayout(archive,'archive',DATA.project_layout?.order||DATA.projects.map(p=>p.id),[...new Set(archived)]);return}const restore=e.target.closest('[data-restore]');if(restore){e.preventDefault();e.stopPropagation();if(projectLayoutActionPending)return;const id=restore.dataset.restore;const archived=(DATA.archived_projects||[]).map(p=>p.id).filter(x=>x!==id);await changeProjectLayout(restore,'restore',DATA.project_layout?.order||DATA.projects.map(p=>p.id),archived);return}const projectCardTarget=e.target.closest('[data-project-id]');if(projectCardTarget&&!e.target.closest('a,button,input,select,textarea,label,[role="button"]')){e.preventDefault();location.hash='project/'+encodeURIComponent(projectCardTarget.dataset.projectId);return}const b=e.target.closest('[data-range]');if(b){range=b.dataset.range;HISTORY={};render();loadExtras()}const point=e.target.closest('[data-point]');if(point&&$('chartDetail'))$('chartDetail').textContent=point.dataset.point;const svg=e.target.closest('.chart-svg');if(svg&&!point&&$('chartDetail')){const near=[...svg.querySelectorAll('[data-point]')].map(el=>{const r=el.getBoundingClientRect();return {el,d:(r.x+r.width/2-e.clientX)**2+(r.y+r.height/2-e.clientY)**2}}).sort((a,b)=>a.d-b.d)[0];if(near)$('chartDetail').textContent=near.el.dataset.point}if(e.target.closest('[data-retry]'))refresh(true)});
document.addEventListener('focusin',e=>{if(e.target.dataset.point&&$('chartDetail'))$('chartDetail').textContent=e.target.dataset.point});
document.addEventListener('keydown',e=>{if(e.target.dataset.point&&['Enter',' '].includes(e.key)){e.preventDefault();$('chartDetail').textContent=e.target.dataset.point}});
document.addEventListener('change',async e=>{if(e.target.id==='zSentinelRetryReceiptFile'||e.target.id==='zSentinelRetryTrendFile'){const files=[...(e.target.files||[])];if(!files.length)return;try{if(e.target.id==='zSentinelRetryReceiptFile')await importRetrySnapshots(files);else {if(files.length!==1)throw Error('Select one trend JSON file');await importRetryTrend(files[0]);}}catch(error){ZSENTINEL_RETRY_MESSAGE='Retry receipt rejected: '+(error instanceof SyntaxError?'invalid JSON':String(error.message||'invalid format'))+'. Existing evidence preserved.';}e.target.value='';if(route==='activity')render();return}if(e.target.id==='zSentinelBranchFile'){const files=[...(e.target.files||[])];if(!files.length)return;try{if(files.length>8)throw Error('Import at most eight bounded branch receipts');let merged=IMPORTED_PRLESS_BRANCHES;for(const file of files){if(file.size<2||file.size>512*1024)throw Error('Each branch JSON must be under 512 KiB');const report=validateZSentinelBranchSnapshot(JSON.parse(await file.text()));merged=mergeZSentinelBranchSnapshots(merged,report);}IMPORTED_PRLESS_BRANCHES=merged;IMPORTED_PRLESS_ERROR='';}catch(error){IMPORTED_PRLESS_ERROR='Branch receipt not imported: '+(error instanceof SyntaxError?'invalid JSON':String(error.message||'invalid source'))+'; previous evidence preserved.';}if(route==='activity')render();return}if(e.target.id==='workerProgressSlot'){const slot=Number(e.target.value);WORKER_PROGRESS_SLOT=Number.isInteger(slot)&&slot>=1&&slot<=8?slot:1;WORKER_PROGRESS_DATA=null;WORKER_PROGRESS_ERROR='';render();return}if(e.target.id==='productIssueReceiptFile'){const file=e.target.files?.[0];if(!file)return;try{if(file.size>1024*1024||file.size<2)throw Error('JSON receipt exceeds 1 MiB or is empty');const receipt=validateProductIssueInbox(JSON.parse(await file.text()));IMPORTED_PRODUCT_ISSUES=mergeValidatedZSentinelReceipts(IMPORTED_PRODUCT_ISSUES,receipt,'issues');IMPORTED_PRODUCT_ERROR='';IMPORTED_ISSUE_SHOW_ALL=false;}catch(error){IMPORTED_PRODUCT_ERROR='Product issue receipt not imported: '+(error instanceof SyntaxError?'Invalid JSON':String(error.message||'Invalid receipt'))+'. Previous observations preserved; use Clear to discard them.';}if(route==='activity')render();return}if(e.target.id==='githubReceiptFile'){const file=e.target.files?.[0];if(!file)return;try{if(file.size>1024*1024||file.size<2)throw Error('JSON receipt exceeds 1 MiB or is empty');const payload=JSON.parse(await file.text());const receipt=validateGitHubActivityReceipt(payload);IMPORTED_GITHUB_ACTIVITY=mergeValidatedZSentinelReceipts(IMPORTED_GITHUB_ACTIVITY,receipt,'github');IMPORTED_GITHUB_ERROR='';}catch(error){IMPORTED_GITHUB_ERROR='GitHub receipt not imported: '+(error instanceof SyntaxError?'Invalid JSON':String(error.message||'Invalid receipt'))+'. Previous observations preserved; use Clear to discard them.';}if(route==='activity')render();return}const projectSelector=e.target.closest?.('[data-global-worker-project]');if(projectSelector){const slot=Number(projectSelector.dataset.globalWorkerProject);GLOBAL_WORKER_PROJECT_REQUESTS[slot]=projectSelector.value;delete GLOBAL_WORKER_REQUEST_RESULTS[slot];render();return}const workerInput=e.target.closest?.('[data-runner-workers]');if(workerInput){await setRunnerWorkers(workerInput.dataset.runnerWorkers,workerInput.value,workerInput);return}if(e.target.id==='activityFilter'){filter=e.target.value;history.replaceState(null,'','#activity'+(filter?'?project='+filter:''));render()}});
$('refresh').addEventListener('click',()=>refresh(true));window.addEventListener('hashchange',navigate);hydrate();navigate();const cachedStatus=loadCachedStatus();if(cachedStatus){DATA=cachedStatus;setConnection(false);$('notice').hidden=false;$('notice').textContent='Live connection is temporarily unavailable. Showing the last successful dashboard snapshot while reconnecting automatically.';$('syncTime').textContent='Last successful measurement '+clock(DATA.time)+' · reconnecting…';render()}refresh(true);setInterval(()=>{if(!document.hidden)refresh()},15000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});