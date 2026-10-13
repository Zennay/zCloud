// Control room UI: zCloud workers, project priority, the work queue, expert proposals and the team roster.
// Everything shown comes from zCloud itself (through the Office server), so it matches the dashboard; every change is
// a two-step prepare -> confirm. Only textContent is used, never innerHTML.
export const PRIORITY_LABELS = {background: 'achtergrond', normal: 'normaal', high: 'hoog', turbo: 'turbo'};
export const STATE_LABELS = {running: 'draait', paused: 'gepauzeerd', draining: 'loopt uit', stopped: 'gestopt'};
export const WORKER_BUTTONS = [['start', 'Start'], ['pause', 'Pauzeer'], ['new_chat', 'Nieuwe chat'], ['push', 'Duwtje'], ['drain', 'Laat uitlopen']];

export function summarizeState(state, pending = 0) {
  if (!state || !state.online) return {online: false, workers: [], queue: [], pending};
  return {online: true, workers: state.workers || [], queue: state.queue || [], pending, checked_at: state.checked_at};
}
export function groupByProject(state) {
  const groups = new Map();
  for (const p of state?.projects || []) groups.set(p.id, {project: p, workers: []});
  for (const w of state?.workers || []) { if (!groups.has(w.project)) groups.set(w.project, {project: {id: w.project, name: w.name || w.project, priority: ''}, workers: []}); groups.get(w.project).workers.push(w); }
  return [...groups.values()].filter((g) => g.workers.length || g.project.priority !== undefined);
}
export const describeWorker = (w) => (w.name || w.project) + ' · ' + (w.worker !== w.project ? w.worker : 'worker') + (w.slot ? ' · slot ' + w.slot : '') + (w.provider ? ' · ' + w.provider : '');

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v; else if (k.startsWith('on')) el[k] = v; else if (k === 'value') el.value = v; else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}

export function createPanel({root, tabsRoot, confirmRoot, api, toast, getStaff, applyStaff, onSummary}) {
  let lastSig = '', tab = 'workers', state = null, proposals = [], log = [], error = '', busy = false, pendingAction = null, timer = null, lastAt = 0;
  const TABS = [['workers', 'Workers'], ['queue', 'Queue'], ['proposals', 'Voorstellen'], ['team', 'Team'], ['log', 'Logboek']];

  async function load(force = false) {
    if (busy) return; busy = true;
    try {
      state = await api('/api/control/state' + (force ? '?fresh=1' : '')); error = '';
      try { proposals = (await api('/api/proposals')).proposals || []; } catch { proposals = []; }
      if (tab === 'log') { try { log = (await api('/api/control/log')).log || []; } catch { log = []; } }
      lastAt = Date.now();
    } catch (e) { error = e.message || String(e); state = null; }
    finally { busy = false; }
    onSummary?.(summarizeState(state, proposals.length));
    if (root.contains(document.activeElement) && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;   // do not wipe a form while someone types
    const sig = JSON.stringify([state && [state.online, state.workers, state.queue, state.projects], proposals, error, log, tab]);
    if (sig === lastSig && !force) return;   // nothing changed in zCloud: keep the DOM (and the scroll position) as it is
    lastSig = sig; render();
  }

  async function prepare(body) {
    try {
      const prep = await api('/api/control/prepare', body);
      pendingAction = {id: prep.pending_id, summary: prep.summary, risk: prep.risk, until: Date.now() + (prep.expires_in || 90) * 1000};
      renderConfirm();
    } catch (e) { toast(e.message); }
  }
  async function confirm() {
    if (!pendingAction) return; const id = pendingAction.id; pendingAction = null; renderConfirm();
    try { const r = await api('/api/control/confirm', {pending_id: id}); toast('Gedaan: ' + r.summary); await load(true); } catch (e) { toast(e.message); await load(true); }
  }
  function renderConfirm() {
    confirmRoot.replaceChildren();
    if (!pendingAction) { confirmRoot.hidden = true; return; }
    confirmRoot.hidden = false;
    confirmRoot.append(h('strong', {}, pendingAction.risk === 'high' ? 'Bevestig (invloed op lopend werk)' : 'Bevestig'), h('p', {}, pendingAction.summary),
      h('div', {class: 'row'}, h('button', {class: 'primary', onclick: confirm}, 'Ja, uitvoeren'), h('button', {onclick: () => { pendingAction = null; renderConfirm(); }}, 'Annuleren'),
        h('span', {class: 'small'}, 'Vervalt na 90 seconden.')));
  }

  const select = (options, value, onchange, label) => { const s = h('select', {'aria-label': label, onchange: () => onchange(s.value)}, options.map(([v, t]) => h('option', {value: v, selected: v === value}, t))); return s; };

  function workersView() {
    const out = [];
    if (!state?.online) return [h('p', {class: 'small'}, 'zCloud is nu niet bereikbaar, dus er is niets om te tonen.')];
    out.push(h('p', {class: 'small'}, 'Bron: zCloud zelf · bijgewerkt ' + new Date(lastAt).toLocaleTimeString('nl-NL') + '. Wijzigingen in het dashboard verschijnen hier vanzelf en andersom.'));
    for (const g of groupByProject(state)) {
      const maxWorkers = Math.min(8, Number(state.max_workers) || 4);
      const head = h('div', {class: 'ctl-head'}, h('strong', {}, g.project.name || g.project.id),
        g.project.priority !== undefined && select([['background', 'Achtergrond'], ['normal', 'Normaal'], ['high', 'Hoog'], ...(g.project.priority === 'turbo' ? [['turbo', 'Turbo (dashboard)']] : [])], g.project.priority || 'normal',
          (v) => v !== g.project.priority && v !== 'turbo' && prepare({action: 'priority', project: g.project.id, priority: v}), 'Prioriteit ' + g.project.id),
        g.workers.length ? select(Array.from({length: maxWorkers}, (_, i) => [String(i + 1), (i + 1) + (i ? ' tabs' : ' tab')]), String(g.workers.length),
          (v) => Number(v) !== g.workers.length && prepare({action: 'worker_count', project: g.project.id, count: Number(v)}), 'Aantal tabs ' + g.project.id) : null);
      out.push(h('article', {class: 'project-card'}, head, g.workers.length ? g.workers.map((w) => h('div', {class: 'ctl-row'},
        h('span', {class: 'badge ' + (w.active && w.state !== 'paused' ? 'ok' : 'idle')}, STATE_LABELS[w.state] || w.state || (w.active ? 'actief' : 'uit')),
        h('span', {class: 'grow small'}, describeWorker(w) + (w.queue_id ? ' · taak ' + w.queue_priority : '')),
        WORKER_BUTTONS.map(([op, text]) => h('button', {class: 'mini', title: text, onclick: () => prepare({action: 'worker', worker: w.worker, op})}, text)))) : h('p', {class: 'small'}, 'Geen worker-tabs voor dit project.')));
    }
    out.push(h('div', {class: 'row'}, h('button', {onclick: () => prepare({action: 'force_push'})}, 'Alle actieve workers een duwtje'), h('button', {onclick: () => load(true)}, 'Vernieuwen')));
    return out;
  }

  function queueView() {
    if (!state?.online) return [h('p', {class: 'small'}, 'zCloud is nu niet bereikbaar.')];
    const projects = (state.projects || []).map((p) => [p.id, p.name || p.id]);
    const form = {project: projects[0]?.[0] || '', level: 'P2'}, title = h('input', {maxlength: 220, placeholder: 'Wat moet er gebeuren? (8-220 tekens)', 'aria-label': 'Titel van de taak'}), criteria = h('textarea', {maxlength: 1500, placeholder: 'Wanneer is het klaar? (criteria, optioneel)', 'aria-label': 'Criteria'});
    const items = (state.queue || []).map((q) => h('div', {class: 'ctl-row'}, h('span', {class: 'badge ' + (q.priority === 'P0' || q.priority === 'P1' ? 'hot' : 'idle')}, q.priority || '?'),
      h('span', {class: 'grow small'}, (q.project || '') + ' · ' + q.title + ' · ' + (q.status || '')), h('button', {class: 'mini', onclick: () => prepare({action: 'queue_drop', queue_id: q.queue_id, reason: 'Via Office'})}, 'Laat vervallen')));
    return [h('p', {class: 'small'}, 'Dit is de echte zCloud-queue. P0 blijft voorbehouden aan het dashboard.'), items.length ? items : h('p', {class: 'small'}, 'De queue is leeg.'),
      h('article', {class: 'project-card'}, h('strong', {}, 'Taak toevoegen'),
        h('div', {class: 'row'}, select(projects, form.project, (v) => { form.project = v; }, 'Project'), select([['P1', 'P1 hoog'], ['P2', 'P2 normaal'], ['P3', 'P3 laag']], 'P2', (v) => { form.level = v; }, 'Prioriteit')),
        h('div', {class: 'row'}, title), h('div', {class: 'row'}, criteria),
        h('div', {class: 'row'}, h('button', {class: 'primary', onclick: () => prepare({action: 'queue_add', project: form.project, title: title.value, queue_priority: form.level, criteria: criteria.value})}, 'Toevoegen (met bevestiging)')))];
  }

  async function decide(p, ok) {
    try { await api('/api/proposals/' + p.id + (ok ? '/approve' : '/reject'), {}); toast(ok ? 'Goedgekeurd en uitgevoerd.' : 'Voorstel afgewezen.'); } catch (e) { toast(e.message); }
    await load(true);
  }
  function proposalsView() {
    const staff = getStaff(), who = {value: staff[0]?.id || ''}, question = h('input', {maxlength: 600, placeholder: 'Optionele vraag aan de expert', 'aria-label': 'Vraag voor review'});
    return [h('p', {class: 'small'}, 'Experts stellen voor; jij beslist. Een voorstel wordt pas uitgevoerd na jouw akkoord, ook als je ‘ja’ zegt tegen de expert.'),
      proposals.length ? proposals.map((p) => h('article', {class: 'project-card'}, h('strong', {}, (staff.find((s) => s.id === p.employee)?.name || p.employee) + ' stelt voor'), h('p', {}, p.summary), p.why ? h('p', {class: 'small'}, p.why) : null,
        h('div', {class: 'row'}, h('button', {class: 'primary', onclick: () => decide(p, true)}, 'Goedkeuren'), h('button', {onclick: () => decide(p, false)}, 'Afwijzen')))) : h('p', {class: 'small'}, 'Geen openstaande voorstellen.'),
      h('article', {class: 'project-card'}, h('strong', {}, 'Laat een expert meekijken'), h('div', {class: 'row'}, select(staff.map((s) => [s.id, s.name]), who.value, (v) => { who.value = v; }, 'Expert'), question),
        h('div', {class: 'row'}, h('button', {onclick: async () => { try { await api('/api/experts/review', {employee: who.value, question: question.value}); toast('De expert kijkt mee. Het voorstel verschijnt hier en wordt voorgelezen.'); } catch (e) { toast(e.message); } }}, 'Kijk mee met queue en workers')))];
  }

  function teamView() {
    const staff = getStaff(), f = {id: '', name: '', title: '', project_id: 'cloud', focus: '', personality: '', color: '#e0a458', style: 'short', gender: 'f'};
    const text = (key, placeholder, max = 80) => h('input', {maxlength: max, placeholder, 'aria-label': placeholder, oninput: (e) => { f[key] = e.target.value; }});
    const projects = [...new Set([...(state?.projects || []).map((p) => p.id), ...staff.map((s) => s.project_id), 'cloud'])].map((p) => [p, p]);
    const save = async () => {
      f.id = f.id || f.name.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 16);
      try { const r = await api('/api/staff', {id: f.id, name: f.name, title: f.title, project_id: f.project_id, focus: f.focus, personality: f.personality, color: f.color, look: {style: f.style}, voice: {gender: f.gender, pitch: f.gender === 'm' ? 0.9 : 1.08}}); applyStaff(r.staff); toast(f.name + ' is aan het team toegevoegd. Ze zit nu aan een bureau.'); render(); } catch (e) { toast(e.message); }
    };
    return [h('p', {class: 'small'}, 'Je team bestaat uit experts met elk een eigen ChatGPT-gesprek. Je kunt tot 8 experts tegelijk laten werken; nieuwe experts krijgen een eigen bureau.'),
      staff.map((s) => h('div', {class: 'ctl-row'}, h('span', {class: 'badge', style: 'background:' + s.color + '33;border-color:' + s.color}, s.name), h('span', {class: 'grow small'}, (s.title || '') + ' · project ' + s.project_id),
        staff.length > 1 ? h('button', {class: 'mini', onclick: async () => { try { const r = await api('/api/staff', {id: s.id, active: false}); applyStaff(r.staff); render(); } catch (e) { toast(e.message); } }}, 'Van de werkvloer halen') : null)),
      h('article', {class: 'project-card'}, h('strong', {}, 'Nieuwe expert'), h('div', {class: 'row'}, text('name', 'Naam', 24), text('title', 'Functie, bijv. Data-expert · FTMO', 60)),
        h('div', {class: 'row'}, select(projects, 'cloud', (v) => { f.project_id = v; }, 'Project'), text('focus', 'Specialisme', 200)), h('div', {class: 'row'}, text('personality', 'Karakter, bijv. precies en kritisch', 200)),
        h('div', {class: 'row'}, select([['short', 'Kort haar'], ['long', 'Lang haar'], ['bun', 'Knot']], 'short', (v) => { f.style = v; }, 'Kapsel'), select([['f', 'Vrouwelijke stem'], ['m', 'Mannelijke stem']], 'f', (v) => { f.gender = v; }, 'Stem'),
          h('input', {type: 'color', value: '#e0a458', style: 'width:46px;flex:none;padding:2px', 'aria-label': 'Kleur', oninput: (e) => { f.color = e.target.value; }})),
        h('div', {class: 'row'}, h('button', {class: 'primary', onclick: save}, 'Expert toevoegen')),
        h('p', {class: 'small'}, 'Na het toevoegen koppel je een nieuwe ChatGPT-chat aan deze expert met de Office Relay (Tampermonkey-menu: “Koppel nieuwe chat aan opgeslagen medewerker”).'))];
  }

  function logView() {
    return log.length ? log.map((l) => h('div', {class: 'ctl-row'}, h('span', {class: 'badge ' + (l.ok ? 'ok' : 'hot')}, l.ok ? 'ok' : 'mislukt'), h('span', {class: 'grow small'}, new Date(l.created_at).toLocaleString('nl-NL') + ' · ' + l.source + ' · ' + l.summary + (l.detail ? ' · ' + l.detail : '')))) : [h('p', {class: 'small'}, 'Nog geen wijzigingen via Office.')];
  }

  function render() {
    if (tabsRoot.children.length !== TABS.length) tabsRoot.replaceChildren(...TABS.map(([id]) => h('button', {'data-tab': id, onclick: () => setTab(id)})));
    for (const b of tabsRoot.children) { const id = b.dataset.tab, text = TABS.find((t) => t[0] === id)[1] + (id === 'proposals' && proposals.length ? ' (' + proposals.length + ')' : ''); if (b.textContent !== text) b.textContent = text; b.classList.toggle('active', id === tab); }
    root.replaceChildren();
    if (error) { root.append(h('p', {class: 'small'}, error), h('p', {class: 'small'}, 'Beheren werkt alleen via de SSH-tunnel (localhost), niet via het openbare adres.'), tab === 'team' ? teamView() : null); return; }
    const view = {workers: workersView, queue: queueView, proposals: proposalsView, team: teamView, log: logView}[tab];
    root.append(...(view ? view() : []).flat(Infinity).filter((n) => n && n.nodeType));
  }
  function setTab(next) { tab = next; render(); if (next === 'log') load(); }
  function start() { stop(); load(true); timer = setInterval(() => { if (!document.hidden) load(); }, 6000); }
  function stop() { clearInterval(timer); timer = null; }
  return {start, stop, load, setTab, render, get tab() { return tab; }, get pendingCount() { return proposals.length; }};
}
