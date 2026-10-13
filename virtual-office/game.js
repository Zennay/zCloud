// Game layer for The Penthouse: voice intents, "finished work" calls, spoken summaries and self-presenting decks.
// DOM-free on purpose (speech/voice adapters touch `window` lazily), so the logic is unit-testable in Node.
export let EMPLOYEES = ['maya', 'noah', 'lina'];
const BASE_ALIASES = {
  maya: ['maya', 'maja', 'maia', 'mija', 'maaya'],
  noah: ['noah', 'noa', 'noach', 'noë'],
  lina: ['lina', 'lena', 'leena', 'liena', 'lien a'],
};
let ALIASES = {...BASE_ALIASES};
let TONES = {maya: {pitch: 1.1, rate: 1.0, gender: 'f'}, noah: {pitch: 0.85, rate: 1.0, gender: 'm'}, lina: {pitch: 1.0, rate: 1.05, gender: 'f'}};

/** The team is data, not code: call this with the /api/staff list so new experts can be addressed by name. */
export function setRoster(staff) {
  const next = {}, tones = {};
  for (const s of staff || []) {
    next[s.id] = [...new Set([s.id, normalize(s.name), ...(BASE_ALIASES[s.id] || [])])].filter(Boolean);
    tones[s.id] = {pitch: s.voice?.pitch ?? 1, rate: s.voice?.rate ?? 1, gender: s.voice?.gender || 'f'};
  }
  if (Object.keys(next).length) { ALIASES = next; TONES = tones; EMPLOYEES = Object.keys(next); }
  return EMPLOYEES;
}
export const tones = () => TONES;
function editDistance(a, b) {
  const m = a.length, n = b.length; if (Math.abs(m - n) > 1) return 2;
  const d = Array.from({length: m + 1}, (_, i) => [i, ...Array(n).fill(0)]);
  for (let j = 0; j <= n; j++) d[0][j] = j;
  for (let i = 1; i <= m; i++) for (let j = 1; j <= n; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[m][n];
}
export const REMIND_AFTER_MS = 5 * 60 * 1000;

export function normalize(text) {
  return String(text || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim();
}

function findEmployee(norm) {
  for (const [id, names] of Object.entries(ALIASES)) {
    const re = new RegExp('(^| )(' + names.map(n => normalize(n)).join('|') + ')( |$)');
    if (re.test(norm)) return id;
  }
  // Speech recognition mangles new names: accept one wrong letter for names of 4+ letters, in first two words only.
  const words = norm.split(' ').slice(0, 2);
  for (const [id, names] of Object.entries(ALIASES)) {
    if (BASE_ALIASES[id]) continue;
    if (names.some(n => normalize(n).length >= 4 && words.some(w => w.length >= 4 && editDistance(w, normalize(n)) <= 1))) return id;
  }
  return '';
}

function stripName(text) {
  let out = String(text || '').trim();
  for (const names of Object.values(ALIASES)) {
    const re = new RegExp('^\\s*(hey |hoi |dag |ok |oke |okay )?(' + names.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')\\b[\\s,:;.!-]*', 'i');
    out = out.replace(re, '');
  }
  return out.trim();
}

const SPEAK = [/\bzeg( het)? maar\b/, /\bga je gang\b/, /\bvertel( het)?( maar| eens)?\b/, /\bwat is er\b/, /\bwat heb je( voor me| voor mij)?\b/,
  /\blaat( maar)? horen\b/, /\bgeef maar\b/, /\bpraat maar\b/, /\bspreek maar\b/, /\bwat (wil|wou|wilde) je (me )?(zeggen|vertellen)\b/, /\bik luister\b/];
const AFFIRM = /^(ja|jawel|ok|oke|okay|goed|prima|graag|ja hoor|ja zeg)$/;
const DISMISS = [/\bnu niet\b/, /\bniet nu\b/, /\blater\b/, /\bstraks\b/, /\bga maar (weer )?(zitten|terug|werken)\b/, /\bga (weer )?aan het werk\b/,
  /\blaat maar\b/, /\bdank( je| u)?( wel)?\b/, /\bbedankt\b/, /\bdankjewel\b/, /\bga maar weer\b/];
const STOP = [/^(stop|genoeg|hou op|stop maar|stop de presentatie)\b/, /\b(stop|beeindig) (de )?presentatie\b/];
const DECK = [/\b(maak|bouw|doe|geef|schrijf)\b(?: me| mij| even| maar| een| nog| wat)* (?:een )?(?:presentatie|slides|slidedeck|deck)\b ?(?:over|voor|met|van)? ?(.*)$/,
  /\bpresenteer\b(?: maar| eens| even| mij| me)*(?: iets| wat)?(?: over| voor)? ?(.*)$/];
const SHOW_DECK = [/\b(laat|toon|speel|geef)\b.*\b(presentatie|slides|deck)\b.*\b(zien|af|horen|maar)\b/, /\bpresenteer (die|dat|hem|de laatste|je presentatie)\b/];
const TASK_VERBS = ['maak', 'bouw', 'fix', 'repareer', 'implementeer', 'schrijf', 'voeg', 'onderzoek', 'test', 'zet', 'los', 'verbeter', 'ontwikkel',
  'werk', 'controleer', 'check', 'refactor', 'ruim', 'verwijder', 'update', 'bereid', 'analyseer'];

const YES = /^(ja|jawel|jazeker|doe maar|ja doe maar|ja graag|graag|akkoord|bevestig|bevestigd|voer( het)? uit|prima|oke|ok|okay|zeker)( .*)?$/;
const NO = /^(nee|neen|niet doen|doe maar niet|laat maar|annuleer|breek af|toch niet|stop)( .*)?$/;
const SKIP = /^(volgende|sla( dat)? over|overslaan|later|pas|misschien later)( .*)?$/;
const PRIORITY_WORDS = [[/\b(hoog|hoge|snel|sneller|belangrijk)\b/, 'high'], [/\b(normaal|normale|gewoon)\b/, 'normal'], [/\b(achtergrond|laag|lage|rustig)\b/, 'background']];
const REVIEW = [/\b(kijk|kijk eens|bekijk|beoordeel|check|loop)\b.*\b(queue|wachtrij|workers?|werkers|projecten|notion|planning)\b/,
  /\bstuur( de)? (workers?|werkers).*(aan|bij|beter)\b/, /\bwat (kunnen|moeten|zouden) we (beter|verbeteren|anders)\b/, /\bkijk (even )?mee\b/,
  /\bwat (valt|zie) je op\b/];
const PROPOSALS = [/\b(welke )?voorstel(len)?\b/, /\bwat stel je voor\b/, /\bwat wil je (veranderen|aanpassen)\b/];
const STATUS = [/\b(hoe staat|hoe gaat|wat doen|status|overzicht|laat zien|wat is de stand)\b.*\b(workers?|werkers|queue|wachtrij|projecten|taken)\b/,
  /\b(workers?|werkers|queue|wachtrij)\b.*\b(status|overzicht|stand)\b/];

/** ctx.control = {targets: [{id, name, kind: 'worker'|'project'}]} lets voice commands name a worker or project. */
function findTarget(norm, targets = []) {
  let best = null, score = 0;
  for (const t of targets) {
    for (const label of [t.id, t.name]) {
      const n = normalize(label); if (!n || n.length < 3) continue;
      if (new RegExp('(^| )' + n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '( |$)').test(norm) && n.length + (t.kind === 'worker' ? 1 : 0) > score) { best = t; score = n.length + (t.kind === 'worker' ? 1 : 0); }
    }
  }
  return best;
}

function controlIntent(norm, ctx, employee) {
  const target = findTarget(norm, ctx.control?.targets);
  const everything = /\b(alle|alles|iedereen|allemaal)\b.*\b(workers?|werkers)\b|\b(workers?|werkers) allemaal\b/.test(norm);
  if (STATUS.some(re => re.test(norm))) return {type: 'control_status', employee, target};
  if (everything && /\b(duw\w*|push\w*|schop\w*|aan de gang|wakker)\b/.test(norm)) return {type: 'control_act', employee, body: {action: 'force_push'}};
  if (!target) return null;
  const id = target.id;
  const level = PRIORITY_WORDS.find(([re]) => re.test(norm));
  if (/\bprioriteit\b/.test(norm) || (target.kind === 'project' && /\bzet\b.*\bop\b/.test(norm) && level && !/\bpauze\b/.test(norm)))
    return level ? {type: 'control_act', employee, body: {action: 'priority', project: target.project || id, priority: level[1]}} : {type: 'control_unclear', employee};
  const count = norm.match(/\bop (\d) (workers?|tabs?|chatgpt)/);
  if (count) return {type: 'control_act', employee, body: {action: 'worker_count', project: target.project || id, count: Number(count[1])}};
  const ops = [[/\b(pauzeer|pauzeren|leg .* stil|zet .* op pauze|zet .* stil)\b/, 'pause'], [/\b(nieuwe chat)\b/, 'new_chat'], [/\b(uitlopen|drain)\b/, 'drain'],
    [/\b(duw\w*|push\w*|schop\w*)\b/, 'push'], [/\b(start\w*|hervat\w*|zet .* aan|laat .* (weer )?draaien|zet .* weer aan)\b/, 'start']];
  const op = ops.find(([re]) => re.test(norm));
  return op ? {type: 'control_act', employee, body: {action: 'worker', worker: id, op: op[1]}} : null;
}

/**
 * Turn a spoken sentence into an intent. ctx.waiting = employees currently standing at the player (FIFO),
 * ctx.selected = employee the player last interacted with.
 */
export function parseIntent(text, ctx = {}) {
  const norm = normalize(text), raw = String(text || '').trim();
  const named = findEmployee(norm), waiting = ctx.waiting || [];
  const employee = named || waiting[0] || ctx.selected || 'maya';
  if (!norm) return {type: 'none'};
  if (ctx.awaiting) { // a yes/no question is open: answers win over everything else
    if (YES.test(norm)) return {type: 'yes', employee};
    if (SKIP.test(norm) && ctx.awaiting.type === 'proposal') return {type: 'skip', employee};
    if (NO.test(norm) || DISMISS.some(re => re.test(norm))) return {type: 'no', employee};
  }
  if (STOP.some(re => re.test(norm))) return {type: 'stop', employee};
  if (DISMISS.some(re => re.test(norm))) return {type: 'dismiss', employee};
  const control = controlIntent(norm, ctx, employee);
  if (control) return control;
  if (PROPOSALS.some(re => re.test(norm)) && !TASK_VERBS.includes(normalize(stripName(raw)).split(' ')[0])) return {type: 'proposals', employee};
  if (REVIEW.some(re => re.test(norm))) return {type: 'review', employee, question: stripName(raw).slice(0, 500)};
  for (const re of SHOW_DECK) if (re.test(norm)) return {type: 'show_deck', employee};
  for (const re of DECK) {
    const m = norm.match(re);
    if (m) {
      const topic = stripName(raw.replace(/^.*?\b(presentatie|slides|slidedeck|deck|presenteer)\b\s*(?:(?:maar|eens|even|iets|wat)\s+)*(?:over|voor|met|van)?\s*/i, '')).slice(0, 1200).trim();
      return topic.length >= 3 ? {type: 'deck', employee, topic} : {type: 'deck_missing_topic', employee};
    }
  }
  const words = norm.split(' ');
  const body = normalize(stripName(raw)).split(' ');
  if (TASK_VERBS.includes(body[0]) && body.length >= 3) return {type: 'task', employee, text: stripName(raw).slice(0, 2000)};
  if (SPEAK.some(re => re.test(norm)) || (words.length <= 3 && AFFIRM.test(norm))) return {type: 'speak', employee};
  if (named && body.length >= 2) return {type: 'chat', employee, text: stripName(raw).slice(0, 2000)};
  return {type: 'unknown', employee};
}

export function slideSpeech(slide) {
  if (slide.speaker) return slide.speaker;
  const lines = String(slide.body || '').split('\n').map(s => s.trim()).filter(Boolean);
  return [slide.title, lines.join('. ')].filter(Boolean).join('. ');
}

/** Make an expert's written answer pleasant to hear: no markdown, no links, max ~4 sentences. */
export function speakable(text, maxChars = 520) {
  let t = String(text || '').replace(/```[\s\S]*?```/g, ' een codevoorbeeld ').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' een link ').replace(/[*_`#>|~]+/g, ' ').replace(/^\s*[-•\d.)]+\s+/gm, '').replace(/\s+/g, ' ').trim();
  if (t.length <= maxChars) return t;
  const sentences = t.split(/(?<=[.!?])\s+/); let out = '';
  for (const sentence of sentences) { if ((out + ' ' + sentence).length > maxChars && out) break; out = (out + ' ' + sentence).trim(); }
  return (out.length > maxChars ? out.slice(0, maxChars).replace(/\s+\S*$/, '') : out) + ' De rest staat in het gesprek.';
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

export class Game {
  /** deps: api(path, body?), world, speech{speak(text,{employee}),cancel()}, ui{status(text),waiting(list)}, storage, sleep */
  constructor({api, world, speech, ui = {}, storage = null, wait = sleep}) {
    Object.assign(this, {api, world, speech, ui, storage, wait});
    this.since = null; this.queue = {}; this.waiting = []; this.selected = 'maya'; this.busy = new Set();
    this.reminders = {}; this.stopFlag = false; this.presenting = null; this.lastDeckId = '';
    this.awaiting = null; this.control = {targets: [], state: null, at: 0}; this.proposals = []; this.lastTalk = {employee: '', at: 0};
    this.chatPollMs = 2000; this.chatPollMax = 90; this.now = () => Date.now();
  }

  setRoster(staff) { setRoster(staff); this.roster = staff; }

  /** Worker/project names for voice commands, straight from zCloud through the Office server (null when not reachable). */
  async refreshControl(force = false) {
    if (!force && this.now() - this.control.at < 20000) return this.control.state;
    this.control.at = this.now();
    try {
      const state = await this.api('/api/control/state');
      this.control.state = state;
      this.control.targets = [
        ...(state.projects || []).map(p => ({id: p.id, name: p.name, kind: 'project'})),
        ...(state.workers || []).map(w => ({id: w.worker, name: w.worker, kind: 'worker', project: w.project})),
      ];
    } catch { this.control.state = null; this.control.targets = []; }
    return this.control.state;
  }

  async init() {
    let stored = null;
    try { stored = this.storage?.getItem('office.game.since'); } catch { /* storage unavailable */ }
    const doc = await this.api('/api/events?since=' + (stored ? Number(stored) : 0));
    if (stored) {
      for (const e of doc.events.slice(-5)) this.handleEvent(e);
      this.save(doc.last_id);
    } else this.save(doc.last_id); // never replay history on a fresh start
    return this;
  }

  save(id) {
    this.since = id;
    try { this.storage?.setItem('office.game.since', String(id)); } catch { /* ignore */ }
  }

  async poll() {
    const doc = await this.api('/api/events?since=' + (this.since ?? 0));
    for (const e of doc.events) this.handleEvent(e);
    if (doc.events.length || this.since === null) this.save(doc.last_id);
    return doc.events.length;
  }

  handleEvent(event) {
    if (event.kind === 'control_changed') {
      this.control.at = 0;
      const what = (event.payload?.changes || []).join('; ');
      this.ui.status?.((event.payload?.source === 'dashboard' ? 'Gewijzigd in het dashboard: ' : 'Gewijzigd: ') + what);
      this.ui.controlChanged?.(event.payload);
      return;
    }
    if (event.kind === 'review_failed' || event.kind === 'proposal_applied' || event.kind === 'proposal_failed') { this.ui.controlChanged?.(event.payload); return; }
    if (!['job_done', 'job_failed', 'deck_ready', 'deck_failed', 'review_ready'].includes(event.kind)) return;
    const q = this.queue[event.employee] ||= [];
    if (q.some(i => i.kind === event.kind && i.ref === event.ref)) return;
    q.push({kind: event.kind, ref: event.ref, payload: event.payload || {}});
    this.callIfIdle(event.employee);
  }

  callIfIdle(employee) {
    if (this.busy.has(employee) || this.waiting.includes(employee) || !(this.queue[employee] || []).length) return;
    this.busy.add(employee);
    clearTimeout(this.reminders[employee]);
    Promise.resolve(this.world.callToPlayer(employee)).then(ok => {
      this.busy.delete(employee);
      if (ok === false) return;
      if (!this.waiting.includes(employee)) this.waiting.push(employee);
      this.world.setAttention(employee, true);
      this.ui.waiting?.([...this.waiting]);
      this.ui.status?.(this.label(employee) + ' staat klaar. Zeg "zeg maar" of houd V ingedrukt.');
    }, () => this.busy.delete(employee));
  }

  label(id) { return id[0].toUpperCase() + id.slice(1); }
  context() { return {waiting: [...this.waiting], selected: this.selected, awaiting: this.awaiting && (this.awaiting.type === 'proposal' || this.now() - this.awaiting.at < 85000) ? this.awaiting : null, control: this.control}; }

  async say(employee, text) {
    this.world.speak(employee, true);
    try { await this.speech.speak(text, {employee}); } finally { this.world.speak(employee, false); }
  }

  /** The player started talking: stop whatever is being said (barge-in). */
  interrupt() { this.speech.cancel?.(); }

  async handleUtterance(text) {
    if (this.control.targets.length === 0 || this.now() - this.control.at > 20000) await this.refreshControl();
    const intent = parseIntent(text, this.context());
    this.ui.status?.('');
    switch (intent.type) {
      case 'yes': return this.answerYes(intent.employee);
      case 'no': return this.answerNo(intent.employee);
      case 'skip': return this.nextProposal(intent.employee);
      case 'control_status': return this.controlStatus(intent);
      case 'control_act': return this.controlAct(intent);
      case 'control_unclear': return this.reply(intent.employee, 'Op welke prioriteit? Hoog, normaal of achtergrond.');
      case 'review': {
        const r = await this.api('/api/experts/review', {employee: intent.employee, question: intent.question});
        this.selected = intent.employee;
        return this.reply(intent.employee, r.acknowledgement || 'Ik kijk even mee.');
      }
      case 'proposals': return this.startProposals(intent.employee);
      case 'stop': return this.stopPresentation();
      case 'dismiss': return this.dismiss(intent.employee);
      case 'speak': return this.deliver(intent.employee);
      case 'deck': {
        const r = await this.api('/api/decks/request', {employee: intent.employee, topic: intent.topic});
        this.selected = intent.employee;
        return this.reply(intent.employee, r.acknowledgement || 'Prima, ik ga aan de presentatie werken.');
      }
      case 'deck_missing_topic': return this.reply(intent.employee, 'Waarover moet de presentatie gaan?');
      case 'show_deck': return this.presentLatest(intent.employee);
      case 'task': {
        const r = await this.api('/api/jobs', {employee: intent.employee, kind: 'task', instruction: intent.text});
        this.selected = intent.employee;
        return this.reply(intent.employee, r.acknowledgement || 'Opdracht ontvangen.');
      }
      case 'chat': return this.chat(intent.employee, intent.text);
      case 'unknown':
        // Mid-conversation follow-ups without a name go to whoever we were just talking to.
        if (this.lastTalk.employee && this.now() - this.lastTalk.at < 90000 && normalize(text).split(' ').length >= 3) return this.chat(this.lastTalk.employee, String(text).trim().slice(0, 2000));
        return intent;
      default: return intent;
    }
  }

  async chat(employee, text) {
    let lastId = 0;
    try { lastId = Math.max(0, ...(await this.api('/api/messages?employee=' + employee)).messages.map(m => m.id)); } catch { /* first message */ }
    const r = await this.api('/api/chat', {employee, instruction: text});
    this.selected = employee; this.lastTalk = {employee, at: this.now()};
    const ack = await this.reply(employee, r.acknowledgement || 'Bericht ontvangen.');
    this.awaitChatReply(employee, lastId);
    return ack;
  }

  /** Answers come back through the relay: poll the conversation and read the new answer out loud. */
  async awaitChatReply(employee, afterId) {
    for (let i = 0; i < this.chatPollMax; i++) {
      await this.wait(this.chatPollMs);
      let list = [];
      try { list = (await this.api('/api/messages?employee=' + employee)).messages || []; } catch { continue; }
      const fresh = list.filter(m => m.id > afterId && m.role === 'assistant');
      if (fresh.length) {
        const text = fresh[fresh.length - 1].content;
        this.lastTalk = {employee, at: this.now()};
        this.ui.chatReply?.(employee, text);
        await this.say(employee, speakable(text));
        return {type: 'chat_reply', employee, text};
      }
    }
    return null;
  }

  async controlStatus({employee}) {
    const state = await this.refreshControl(true);
    if (!state?.online) return this.reply(employee, 'Ik kan zCloud nu niet bereiken, dus ik weet de stand van de workers niet.');
    const running = state.workers.filter(w => w.active && w.state === 'running'), paused = state.workers.filter(w => w.state === 'paused');
    const open = state.queue.filter(q => q.status !== 'done').length;
    const parts = [running.length + ' van de ' + state.workers.length + ' workers draaien'];
    if (paused.length) parts.push(paused.map(w => w.worker).slice(0, 3).join(' en ') + ' staat op pauze');
    parts.push('er staan ' + open + ' taken in de queue');
    return this.reply(employee, parts.join(', ') + '.');
  }

  async controlAct({employee, body}) {
    this.selected = employee;
    let prep;
    try { prep = await this.api('/api/control/prepare', body); } catch (e) { return this.reply(employee, 'Dat kan ik niet doen: ' + (e.message || 'onbekende fout')); }
    this.awaiting = {type: 'confirm', pending_id: prep.pending_id, employee, at: this.now()};
    this.ui.awaiting?.(this.awaiting, prep.summary);
    return this.reply(employee, prep.summary + '. Zal ik dat doen?');
  }

  async answerYes(employee) {
    const a = this.awaiting; this.awaiting = null; this.ui.awaiting?.(null);
    if (!a) return this.reply(employee, 'Ik wacht nergens op een antwoord.');
    if (a.type === 'confirm') {
      try { await this.api('/api/control/confirm', {pending_id: a.pending_id}); this.control.at = 0; return this.reply(a.employee, 'Gedaan. Het staat ook meteen in het dashboard.'); }
      catch (e) { return this.reply(a.employee, 'Dat is niet gelukt: ' + (e.message || 'onbekende fout')); }
    }
    let result;
    try { result = await this.api('/api/proposals/' + a.id + '/approve', {}); } catch (e) { result = {status: 'failed', detail: e.message}; }
    this.control.at = 0;
    await this.say(a.employee, result.status === 'applied' ? 'Gedaan.' : 'Dat is niet gelukt. ' + (result.detail || ''));
    return this.nextProposal(a.employee);
  }

  async answerNo(employee) {
    const a = this.awaiting; this.awaiting = null; this.ui.awaiting?.(null);
    if (!a) return this.dismiss(employee);
    if (a.type === 'confirm') return this.reply(a.employee, 'Prima, ik doe niets.');
    try { await this.api('/api/proposals/' + a.id + '/reject', {}); } catch { /* already handled elsewhere */ }
    await this.say(a.employee, 'Oké, laat ik zitten.');
    return this.nextProposal(a.employee);
  }

  async startProposals(employee) {
    const doc = await this.api('/api/proposals?status=pending');
    this.proposals = (doc.proposals || []).filter(p => !employee || p.employee === employee || !this.waiting.length);
    if (!this.proposals.length) return this.reply(employee, 'Ik heb op dit moment geen voorstellen die op jouw akkoord wachten.');
    await this.say(employee, 'Ik heb ' + this.proposals.length + (this.proposals.length === 1 ? ' voorstel.' : ' voorstellen.'));
    return this.nextProposal(employee);
  }

  async nextProposal(employee) {
    const p = this.proposals.shift();
    if (!p) { this.awaiting = null; this.ui.awaiting?.(null); return this.finishVisit(employee); }
    this.awaiting = {type: 'proposal', id: p.id, employee: p.employee || employee, at: this.now()};
    this.ui.awaiting?.(this.awaiting, p.summary);
    return this.reply(p.employee || employee, p.summary + '.' + (p.why ? ' ' + p.why + '.' : '') + ' Zal ik dat doen?');
  }

  async reply(employee, text) { this.ui.status?.(text); await this.say(employee, text); return {type: 'reply', employee, text}; }

  async deliver(employee) {
    const q = this.queue[employee] || [];
    if (!this.waiting.includes(employee) && !q.length) return this.reply(employee, 'Ik heb op dit moment niets nieuws voor je.');
    const item = q[0];
    if (!item) return this.reply(employee, 'Ik heb op dit moment niets nieuws voor je.');
    this.world.setAttention(employee, false);
    if (item.kind === 'review_ready') {
      q.shift();
      await this.say(employee, item.payload?.spoken || 'Ik heb naar de queue gekeken.');
      if (item.payload?.proposals > 0) return this.startProposals(employee);
      return this.finishVisit(employee);
    }
    if (item.kind === 'deck_ready') { q.shift(); return this.presentDeck(employee, item.ref); }
    if (item.kind === 'deck_failed') { q.shift(); await this.say(employee, 'Het is me niet gelukt om de presentatie netjes af te maken. Probeer het later nog eens.'); return this.finishVisit(employee); }
    let summary = await this.api('/api/jobs/' + item.ref + '/summary');
    for (let i = 0; summary.pending && i < 6; i++) { await this.wait(1000); summary = await this.api('/api/jobs/' + item.ref + '/summary'); }
    q.shift();
    await this.say(employee, summary.text);
    return this.finishVisit(employee);
  }

  async finishVisit(employee) {
    if ((this.queue[employee] || []).length) { this.world.setAttention(employee, true); return {type: 'more', employee}; }
    return this.dismiss(employee, true);
  }

  async dismiss(employee, silent = false) {
    this.waiting = this.waiting.filter(e => e !== employee);
    this.ui.waiting?.([...this.waiting]);
    this.world.setAttention(employee, false);
    await this.world.sendHome(employee);
    if ((this.queue[employee] || []).length) this.reminders[employee] = setTimeout(() => this.callIfIdle(employee), REMIND_AFTER_MS);
    return {type: 'dismissed', employee, silent};
  }

  async presentLatest(employee) {
    const list = await this.api('/api/decks');
    const id = this.lastDeckId || list.decks?.[0]?.id;
    if (!id) return this.reply(employee, 'Er staat nog geen presentatie klaar.');
    return this.presentDeck(employee, id);
  }

  async presentDeck(employee, deckId) {
    const deck = await this.api('/api/decks/' + deckId);
    this.lastDeckId = deckId;
    this.waiting = this.waiting.filter(e => e !== employee);
    this.ui.waiting?.([...this.waiting]);
    this.stopFlag = false; this.presenting = employee;
    try {
      this.ui.status?.(this.label(employee) + ' presenteert: ' + deck.title);
      await this.world.walkToBoard(employee);
      const slides = deck.slides || [];
      for (let i = 0; i < slides.length && !this.stopFlag; i++) {
        this.world.setSlide(slides[i], i, slides.length);
        await this.say(employee, slideSpeech(slides[i]));
        if (!this.stopFlag) await this.wait(600);
      }
    } finally { this.presenting = null; }
    this.ui.status?.('');
    await this.world.sendHome(employee);
    return {type: 'presented', employee, deck: deckId};
  }

  stopPresentation() {
    this.stopFlag = true;
    this.speech.cancel?.();
    return {type: 'stopped'};
  }
}

// ------------------------------------------------------------------ browser adapters
export function createSpeech(win = globalThis.window) {
  const synth = win?.speechSynthesis;
  const pick = (employee, tone) => {
    const voices = synth?.getVoices?.() || [], dutch = voices.filter(v => /^nl/i.test(v.lang));
    if (!dutch.length) return null;
    const female = dutch.filter(v => /(colette|fenna|female|vrouw|hanna)/i.test(v.name)), male = dutch.filter(v => /(frank|maarten|male|man)/i.test(v.name));
    const pool = tone.gender === 'm' ? (male.length ? male : dutch) : (female.length ? female : dutch);
    let h = 0; for (const c of employee || '') h = (h * 31 + c.charCodeAt(0)) >>> 0;   // stable voice per person
    return pool[h % pool.length];
  };
  return {
    available: !!synth,
    speak(text, {employee} = {}) {
      return new Promise(resolve => {
        if (!synth) return resolve();
        const u = new win.SpeechSynthesisUtterance(String(text).slice(0, 1800)), t = tones()[employee] || {pitch: 1, rate: 1, gender: 'f'};
        u.lang = 'nl-NL'; u.pitch = t.pitch; u.rate = t.rate;
        const v = pick(employee, t); if (v) u.voice = v;
        u.onend = u.onerror = () => resolve();
        synth.speak(u);
      });
    },
    cancel() { synth?.cancel(); },
  };
}

/** Push-to-talk. Desktop app: local Whisper via window.officeDesktop.voice. Browser: Web Speech API fallback. */
export function createVoice(win = globalThis.window) {
  const desktop = win?.officeDesktop?.voice;
  if (desktop) {
    return {kind: 'desktop', available: true, start: () => desktop.start(), stop: () => desktop.stop()};
  }
  const Rec = win?.SpeechRecognition || win?.webkitSpeechRecognition;
  if (!Rec) return {kind: 'none', available: false, start: async () => {}, stop: async () => ''};
  let rec = null, text = '', finish = null;
  return {
    kind: 'browser', available: true,
    async start() {
      rec = new Rec(); rec.lang = 'nl-NL'; rec.interimResults = false; rec.continuous = true; text = '';
      rec.onresult = e => { for (let i = e.resultIndex; i < e.results.length; i++) if (e.results[i].isFinal) text += ' ' + e.results[i][0].transcript; };
      rec.onend = () => finish?.(text.trim());
      rec.start();
    },
    stop() { return new Promise(resolve => { finish = resolve; try { rec.stop(); } catch { resolve(text.trim()); } }); },
  };
}
