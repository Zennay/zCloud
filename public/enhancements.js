'use strict';
(function(){
  function qInline(p){
    var h=p&&p.quality&&p.quality.headline;
    if(!h)return '';
    return '<div class="quality-chip"><span>'+esc(h.label)+'</span><strong>'+esc(h.value)+esc(h.unit||'')+'</strong><small>'+esc(h.note||'')+'</small></div>';
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
      +'<div><span>2-Step rules</span><strong>'+(r.two_step_configured?'Geconfigureerd':'Ontbreekt')+'</strong></div>'
      +'<div><span>1-Step rules</span><strong>'+(r.one_step_configured?'Geconfigureerd':'Nog niet')+'</strong></div>'
      +'<div><span>Simulator</span><strong>'+(r.simulator_ready?'Klaar':'Ontbreekt')+'</strong></div>'
      +'<div><span>Candidate path test</span><strong>'+(r.measured?'Gemeten':'Nog niet gedraaid')+'</strong></div>'
      +'</div>';
    var runs=r.runs||[];
    var table='';
    if(runs.length){
      table='<div class="readiness-table"><div class="readiness-row head"><span>Risk / trade</span><span>Pass</span><span>Daily breach</span><span>Total breach</span><span>P95 DD</span><span>Median target</span></div>'
        +runs.map(function(x){return '<div class="readiness-row"><strong>'+esc(x.risk_pct==null?'—':x.risk_pct+'%')+'</strong><span>'+pct(x.pass_rate)+'</span><span>'+pct(x.daily_loss_breach_rate)+'</span><span>'+pct(x.total_loss_breach_rate)+'</span><span>'+pct(x.max_drawdown_p95)+'</span><span>'+esc(x.median_days_to_target==null?'—':x.median_days_to_target+' d')+'</span></div>'}).join('')
        +'</div>';
    }else{
      table='<div class="readiness-risk-grid">'+(r.planned_risk_pct||[]).map(function(x){return '<div><span>'+esc(x)+'% risk</span><strong>—</strong><small>wacht op chronologische R-path test</small></div>'}).join('')+'</div>';
    }
    return '<div class="panel readiness-panel"><div class="panel-header"><div><h2>FTMO readiness</h2><div class="panel-subtitle">Challenge-path testing · los van pips/win-rate</div></div><span class="readiness-state '+(r.measured?'ready':'pending')+'">'+(r.measured?'GEMETEN':'PENDING')+'</span></div>'+capability+table+'<div class="detail-note">'+esc(r.note||'')+'</div></div>';
  }

  function resourcePanel(){
    var list=(DATA.projects||[]).filter(function(p){return !!p.resource});
    if(!list.length)return '';
    var rows=list.map(function(p){
      var r=p.resource||{};
      var cpu=r.cpu_percent==null?'meten…':num(r.cpu_percent)+'%';
      var mem=r.memory_bytes?num(r.memory_bytes/1048576)+' MB':'—';
      var detail=r.managed?'CPU '+cpu+' · RAM '+mem:'Geen persistente VPS-worker · instelling wordt bewaard';
      return '<div class="resource-row"><div><strong>'+esc(p.name)+'</strong><small>'+detail+'</small></div><select data-resource-priority="'+esc(p.id)+'" data-previous-value="'+esc(r.priority||'normal')+'" aria-label="Resourceprioriteit '+esc(p.name)+'"><option value="background" '+(r.priority==='background'?'selected':'')+'>Achtergrond · 100</option><option value="normal" '+(r.priority==='normal'?'selected':'')+'>Normaal · 400</option><option value="high" '+(r.priority==='high'?'selected':'')+'>Hoog · 800</option><option value="turbo" '+(r.priority==='turbo'?'selected':'')+'>Turbo · 3000</option></select></div>';
    }).join('');
    return '<div class="panel resource-panel"><div class="panel-header"><div><h2>Resource priority</h2><div class="panel-subtitle">Relatieve CPU/IO-weight · 100 / 400 / 800 / 3000</div></div>'+icon('cpu')+'</div><div class="resource-grid">'+rows+'</div><div class="detail-note">Geen niveau is een CPU-cap: elk project mag alle vrije cores gebruiken. Alleen bij contention bepaalt de weight de verdeling; Turbo krijgt dan de sterkste voorrang, gevolgd door Hoog, Normaal en Achtergrond. Wijzigingen zijn alleen toegestaan vanaf vertrouwde beheer-IP’s; er is geen actiesleutel meer nodig.</div></div>';
  }
  function alertsPanel(){
    var a=DATA.alerts||[];
    if(!a.length)return '<div class="panel alert-panel"><div class="panel-header"><div><h2>Important events</h2><div class="panel-subtitle">Geen grote alerts</div></div>'+icon('check')+'</div><div class="detail-note">Alleen freezes, blokkades, serviceproblemen, afgeronde milestones en echte breakthroughs komen hier.</div></div>';
    var rows=a.slice(0,6).map(function(x){
      return '<div class="alert-row '+esc(x.severity)+'"><div><strong>'+esc(x.title)+'</strong><span>'+esc(x.detail||'')+'</span></div><time>'+rel(x.ts)+'</time></div>';
    }).join('');
    return '<div class="panel alert-panel"><div class="panel-header"><div><h2>Important events</h2><div class="panel-subtitle">Rustige alerts met cooldown</div></div>'+icon('pulse')+'</div><div class="alert-list">'+rows+'</div></div>';
  }
  function milestonePanel(p){
    var rows=(p.milestones||[]).map(function(m,i){
      var pct=Math.round(Number(m.progress==null?(m.done?100:0):m.progress)*10)/10;
      return '<div class="milestone '+(pct>=100?'done':(m.title===p.next?'next':''))+'"><span class="milestone-check">'+(pct>=100?'✓':String(i+1).padStart(2,'0'))+'</span><div class="milestone-main"><span>'+esc(m.title)+'</span><div class="milestone-mini"><i style="width:'+pct+'%"></i></div></div><em>'+num(pct)+'%</em></div>';
    }).join('');
    return '<div class="panel milestone-progress-panel" style="--accent:'+esc(p.accent)+'"><div class="panel-header"><div><h2>Milestone progress</h2><div class="panel-subtitle">Meer detail dan alleen het totale projectpercentage</div></div><span class="count">'+p.completed+' / '+p.milestones.length+'</span></div><div class="milestones">'+rows+'</div></div>';
  }

  var baseProjectCard=projectCard;
  projectCard=function(p){
    var html=baseProjectCard(p);
    var q=qInline(p);
    return q?html.replace('<div class="progress-row">',q+'<div class="progress-row">'):html;
  };

  var baseOverview=overview;
  overview=function(){
    var html=baseOverview();
    var controls='<div class="dashboard-grid top-controls">'+resourcePanel()+alertsPanel()+'</div>';
    return html.replace('<div class="dashboard-grid">',controls+'<div class="dashboard-grid">');
  };

  var baseDetail=detail;
  detail=function(p){
    var html=baseDetail(p);
    var extras=qPanel(p)+readinessPanel(p)+milestonePanel(p);
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
      $('notice').textContent='Resource priority kon niet worden opgeslagen: '+(err.message||err);
    }finally{
      el.disabled=false;
    }
  },true);
})();