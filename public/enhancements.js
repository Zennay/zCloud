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
  function resourcePanel(){
    var list=(DATA.projects||[]).filter(function(p){return !!p.resource});
    if(!list.length)return '';
    var rows=list.map(function(p){
      var r=p.resource||{};
      var cpu=r.cpu_percent==null?'meten…':num(r.cpu_percent)+'%';
      var mem=r.memory_bytes?num(r.memory_bytes/1048576)+' MB':'—';
      var disabled=' disabled';
      return '<div class="resource-row"><div><strong>'+esc(p.name)+'</strong><small>'+(r.managed?'CPU '+cpu+' · RAM '+mem:'Geen persistente VPS-worker')+'</small></div><select data-resource-priority="'+esc(p.id)+'"'+disabled+'><option value="background" '+(r.priority==='background'?'selected':'')+'>Background</option><option value="normal" '+(r.priority==='normal'?'selected':'')+'>Normaal</option><option value="high" '+(r.priority==='high'?'selected':'')+'>High</option></select></div>';
    }).join('');
    return '<div class="panel resource-panel"><div class="panel-header"><div><h2>Resource priority</h2><div class="panel-subtitle">Relatieve CPU/IO-prioriteit · geen harde cap</div></div>'+icon('cpu')+'</div><div class="resource-grid">'+rows+'</div><div class="detail-note">Background blijft doorwerken en mag vrije CPU gebruiken; bij contention krijgen High-projecten voorrang. Wijzigen is bewust geblokkeerd zolang het dashboard geen login heeft.</div></div>';
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
    var extras=qPanel(p)+milestonePanel(p);
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
    el.disabled=true;
    try{
      await post('/api/resource-priority',{project:el.dataset.resourcePriority,priority:el.value});
      await refresh(true);
    }catch(err){
      $('notice').hidden=false;
      $('notice').textContent='Resource priority kon niet worden opgeslagen.';
    }finally{
      el.disabled=false;
    }
  },true);
})();