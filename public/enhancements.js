'use strict';
(function(){
  function qInline(p){
    var h=p&&p.quality&&p.quality.headline;
    if(!h)return '';
    return '<div class="quality-chip"><span>'+esc(h.label)+'</span><strong>'+esc(h.value)+esc(h.unit||'')+'</strong><small>'+esc(h.note||'')+'</small></div>';
  }
  function progressDelta(p){
    var rows=HISTORY[p.id]||[];
    if(rows.length<2)return null;
    var first=Number(rows[0].progress),last=Number(rows[rows.length-1].progress);
    if(!Number.isFinite(first)||!Number.isFinite(last))return null;
    return Math.round((last-first)*10)/10;
  }
  function evidencePoint(slot,item){
    if(!item)return '';
    var labels={latest:'Nieuwste',current:'Huidig gevalideerd',best:'Beste gevalideerd'};
    return '<div class="evidence-point"><span>'+esc(labels[slot]||slot)+'</span><strong>'+esc(item.value)+esc(item.unit||'')+'</strong><small>'+esc(item.label||'')+(item.note?' · '+esc(item.note):'')+'</small></div>';
  }
  function evidencePanel(p){
    var q=p&&p.quality||{},cmp=q.comparison||{};
    var slots=['latest','current','best'].map(function(k){return evidencePoint(k,cmp[k])}).join('');
    var delta=progressDelta(p);
    var deltaText=delta==null?'Nog geen vergelijkingspunt':(delta>0?'+':'')+num(delta)+' procentpunt';
    var bottleneck=p.next_step||p.next||p.phase||'Geen actuele bottleneck vastgelegd';
    var sourceBits=[];
    if(p.milestone_revision)sourceBits.push('Checkpoint '+String(p.milestone_revision));
    if(p.progress_basis)sourceBits.push(String(p.progress_basis));
    ['latest','current','best'].forEach(function(k){var x=cmp[k];if(x&&x.source)sourceBits.push((k==='latest'?'Nieuwste':k==='current'?'Huidig':'Beste')+': '+String(x.source))});
    var comparison=slots?'<div class="evidence-comparison">'+slots+'</div>':'<div class="evidence-empty">Nog geen betrouwbare latest/current/best-evidence voor dit project.</div>';
    return '<div class="panel evidence-progress-panel" style="--accent:'+esc(p.accent)+'"><div class="panel-header"><div><h2>Voortgang met bewijs</h2><div class="panel-subtitle">Checkpoint-voortgang, verandering en actuele bottleneck</div></div><strong class="evidence-progress-value">'+num(p.progress)+'%</strong></div><div class="evidence-summary"><div><span>Verandering ('+esc(range)+')</span><strong>'+esc(deltaText)+'</strong></div><div><span>Actuele bottleneck</span><strong>'+esc(bottleneck)+'</strong></div></div>'+comparison+'<details class="section-details evidence-details"><summary>Bronnen en technische details</summary><p>'+esc(sourceBits.join(' · ')||'Geen bronmetadata beschikbaar')+'</p></details></div>';
  }

  function qPanel(p){
    var q=p&&p.quality;
    if(!q||!q.available||!q.headline)return '';
    var items=(q.items||[]).map(function(x){
      return '<div><span>'+esc(x.label)+'</span><strong>'+esc(x.value)+esc(x.unit||'')+'</strong></div>';
    }).join('');
    return '<div class="panel quality-panel"><div class="panel-header"><div><h2>Live kwaliteit</h2><div class="panel-subtitle">'+esc(q.stage||'Evidence-backed')+'</div></div>'+icon('pulse')+'</div><div class="quality-hero">'+esc(q.headline.label)+'<strong>'+esc(q.headline.value)+esc(q.headline.unit||'')+'</strong><span>'+esc(q.headline.note||'')+'</span></div><div class="quality-grid">'+items+'</div></div>';
  }
  function readinessPanel(p){
    if(!p||p.id!=='ftmo')return '';
    var r=p.quality&&p.quality.readiness;
    if(!r||!r.available)return '';
    function pct(v){return v==null?'—':num(v)+'%'}
    var capability='<div class="readiness-summary">'
      +'<div><span>2-Step regels</span><strong>'+(r.two_step_configured?'Geconfigureerd':'Ontbreekt')+'</strong></div>'
      +'<div><span>1-Step regels</span><strong>'+(r.one_step_configured?'Geconfigureerd':'Nog niet')+'</strong></div>'
      +'<div><span>Simulator</span><strong>'+(r.simulator_ready?'Klaar':'Ontbreekt')+'</strong></div>'
      +'<div><span>Simulatietest</span><strong>'+(r.measured?'Gemeten':'Nog niet gedraaid')+'</strong></div>'
      +'</div>';
    var runs=r.runs||[];
    var table='';
    if(runs.length){
      table='<div class="readiness-table"><div class="readiness-row head"><span>Risico per trade</span><span>Geslaagd</span><span>Daglimiet geraakt</span><span>Totale limiet geraakt</span><span>Zware terugval</span><span>Typisch doelmoment</span></div>'
        +runs.map(function(x){return '<div class="readiness-row"><strong>'+esc(x.risk_pct==null?'—':x.risk_pct+'%')+'</strong><span>'+pct(x.pass_rate)+'</span><span>'+pct(x.daily_loss_breach_rate)+'</span><span>'+pct(x.total_loss_breach_rate)+'</span><span>'+pct(x.max_drawdown_p95)+'</span><span>'+esc(x.median_days_to_target==null?'—':x.median_days_to_target+' d')+'</span></div>'}).join('')
        +'</div>';
    }else{
      table='<div class="readiness-risk-grid">'+(r.planned_risk_pct||[]).map(function(x){return '<div><span>'+esc(x)+'% risico per trade</span><strong>—</strong><small>wacht op realistische FTMO-simulatietest</small></div>'}).join('')+'</div>';
    }
    return '<div class="panel readiness-panel"><div class="panel-header"><div><h2>FTMO-teststatus</h2><div class="panel-subtitle">Realistische FTMO-simulatietest · los van alleen winst of winstpercentage</div></div><span class="readiness-state '+(r.measured?'ready':'pending')+'">'+(r.measured?'Gemeten':'Nog niet gemeten')+'</span></div>'+capability+'<details class="section-details readiness-details"><summary>Technische testdetails</summary>'+table+'</details><div class="detail-note">'+esc(r.note||'')+'</div></div>';
  }

  function resourcePanel(){
    var list=(DATA.projects||[]).filter(function(p){return !!p.resource});
    if(!list.length)return '';
    var weights={background:100,normal:400,high:800,turbo:3000};
    var tech=[];
    var rows=list.map(function(p){
      var r=p.resource||{};
      var cpu=r.cpu_percent==null?'wordt gemeten':num(r.cpu_percent)+'%';
      var mem=r.memory_bytes?num(r.memory_bytes/1048576)+' MB':'—';
      var state=r.managed?'Draait op de VPS':'Geen vaste VPS-worker';
      tech.push('<div class="resource-tech-row"><strong>'+esc(p.name)+'</strong><span>CPU '+cpu+' · RAM '+mem+' · weight '+esc(weights[r.priority]||400)+'</span></div>');
      return '<div class="resource-row"><div class="resource-copy"><strong>'+esc(p.name)+'</strong><small>'+state+'</small></div><select data-resource-priority="'+esc(p.id)+'" data-previous-value="'+esc(r.priority||'normal')+'" aria-label="Voorrang voor '+esc(p.name)+'"><option value="background" '+(r.priority==='background'?'selected':'')+'>Achtergrond</option><option value="normal" '+(r.priority==='normal'?'selected':'')+'>Normaal</option><option value="high" '+(r.priority==='high'?'selected':'')+'>Hoog</option><option value="turbo" '+(r.priority==='turbo'?'selected':'')+'>Turbo</option></select></div>';
    }).join('');
    var details='<details class="section-details resource-details"><summary>Technische details</summary><div class="resource-tech-list">'+tech.join('')+'</div><p>Weights: Achtergrond 100 · Normaal 400 · Hoog 800 · Turbo 3000. Dit is alleen de verdeling wanneer meerdere projecten tegelijk CPU/IO nodig hebben; het is geen harde CPU-limiet.</p></details>';
    return '<div class="panel resource-panel"><div class="panel-header"><div><h2>Voorrang per project</h2><div class="panel-subtitle">Kies wie voorrang krijgt als de VPS druk is</div></div>'+icon('cpu')+'</div><div class="resource-grid">'+rows+'</div>'+details+'</div>';
  }
  function incidentPanel(){
    var center=DATA.incidents||{items:[],recovery:null},items=center.items||[];
    if(!items.length){
      var recovery=center.recovery||{};
      var recoveryText=recovery.available?'Herstelpunt beschikbaar':'Nog geen herstelpunt';
      return '<div class="panel alert-panel incident-panel"><div class="panel-header"><div><h2>Aandacht nodig</h2><div class="panel-subtitle">Geen actie nodig · '+esc(recoveryText)+'</div></div>'+icon('check')+'</div><div class="alert-empty">Alles wat nu draait heeft geen concrete interventie nodig.</div></div>';
    }
    var names={};(DATA.projects||[]).forEach(function(p){names[p.id]=p.name});
    var rows=items.map(function(x){
      var rollback=x.rollback||{},project=names[x.project]||x.project||'zCloud';
      var recovery=!rollback.available?'Geen herstelpunt':rollback.status==='tested'?'Herstel getest':rollback.status==='problem'?'Herstelcontrole nodig':rollback.status==='in_progress'?'Herstel loopt':'Herstelpunt klaar';
      return '<article class="incident-card '+esc(x.severity||'warning')+'"><div class="incident-card-head"><div><span>'+esc(project)+'</span><strong>'+esc(x.title)+'</strong></div><b>'+esc(x.health||'Aandacht')+'</b></div><dl><div><dt>Oorzaak</dt><dd>'+esc(x.cause||'Onbekend')+'</dd></div><div><dt>Impact</dt><dd>'+esc(x.impact||'Onbekend')+'</dd></div><div><dt>Herstel</dt><dd>'+esc(recovery)+'</dd></div></dl><p class="incident-action"><b>Wat nu:</b> '+esc(x.action||'Controleer dit incident.')+'</p><details class="section-details incident-details"><summary>Technische details</summary><p>'+esc(x.technical_detail||'Geen extra technische details')+(x.detected_at?' · '+esc(date(x.detected_at,true)):'')+'</p></details></article>';
    }).join('');
    return '<div class="panel alert-panel incident-panel"><div class="panel-header"><div><h2>Aandacht nodig</h2><div class="panel-subtitle">'+items.length+' concrete '+(items.length===1?'actie':'acties')+' · geen logspam</div></div><a class="events-link" href="#activity">Historie</a></div><div class="incident-list">'+rows+'</div></div>';
  }

  function milestonePanel(p){
    var rows=(p.milestones||[]).map(function(m,i){
      var pct=Math.round(Number(m.progress==null?(m.done?100:0):m.progress)*10)/10;
      return '<div class="milestone '+(pct>=100?'done':(m.title===p.next?'next':''))+'"><span class="milestone-check">'+(pct>=100?'✓':String(i+1).padStart(2,'0'))+'</span><div class="milestone-main"><span>'+esc(m.title)+'</span><div class="milestone-mini"><i style="width:'+pct+'%"></i></div></div><em>'+num(pct)+'%</em></div>';
    }).join('');
    return '<div class="panel milestone-progress-panel" style="--accent:'+esc(p.accent)+'"><div class="panel-header"><div><h2>Voortgang per projectstap</h2><div class="panel-subtitle">Meer detail dan alleen het totale projectpercentage</div></div><span class="count">'+p.completed+' / '+p.milestones.length+'</span></div><div class="milestones">'+rows+'</div></div>';
  }

  var baseProjectCard=projectCard;
  projectCard=function(p){
    var html=baseProjectCard(p);
    var q=qInline(p),delta=progressDelta(p);
    var deltaChip=delta==null?'':'<span class="progress-delta '+(delta>0?'up':delta<0?'down':'flat')+'">'+(delta>0?'+':'')+num(delta)+' pp</span>';
    if(deltaChip)html=html.replace('<div class="progress-row">','<div class="progress-row">'+deltaChip);
    return q?html.replace('<div class="progress-row">',q+'<div class="progress-row">'):html;
  };

  var baseOverview=overview;
  overview=function(){
    var html=baseOverview();
    var controls='<div class="dashboard-grid top-controls">'+resourcePanel()+incidentPanel()+'</div>';
    return html.replace('<div class="dashboard-grid">',controls+'<div class="dashboard-grid">');
  };

  var baseDetail=detail;
  detail=function(p){
    var html=baseDetail(p);
    var extras=evidencePanel(p)+qPanel(p)+readinessPanel(p)+milestonePanel(p);
    return html.replace('<div class="detail-columns">',extras+'<div class="detail-columns">');
  };

  var baseInfrastructure=infrastructure;
  infrastructure=function(){
    return baseInfrastructure()+resourcePanel();
  };

  document.addEventListener('change',async function(e){
    var el=e.target.closest&&e.target.closest('[data-resource-priority]');
    if(!el)return;
    e.stopImmediatePropagation();
    var previous=el.dataset.previousValue||'normal';
    el.disabled=true;
    try{
      var response=await fetch('/api/resource-priority',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project:el.dataset.resourcePriority,priority:el.value}),signal:AbortSignal.timeout(10000)});
      var data=await response.json().catch(function(){return {}});
      if(!response.ok){throw new Error(data.error||'Opslaan mislukt')}
      el.dataset.previousValue=el.value;
      await refresh(true);
    }catch(err){
      el.value=previous;
      $('notice').hidden=false;
      $('notice').textContent='Projectvoorrang kon niet worden opgeslagen: '+(err.message||err);
    }finally{
      el.disabled=false;
    }
  },true);
})();
