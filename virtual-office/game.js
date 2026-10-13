// Game layer for The Penthouse: voice intents, "finished work" calls, spoken summaries and self-presenting decks.
// DOM-free on purpose (speech/voice adapters touch `window` lazily), so the logic is unit-testable in Node.
export const EMPLOYEES = ['maya', 'noah', 'lina'];
const ALIASES = {
  maya: ['maya', 'maja', 'maia', 'mija', 'maaya'],
  noah: ['noah', 'noa', 'noach', 'noë'],
  lina: ['lina', 'lena', 'leena', 'liena', 'lien a'],
};
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
  return '';
}

function stripName(text) {
  let out = String(text || '').trim();
  for (const names of Object.values(ALIASES)) {
    const re = new RegExp('^\\s*(hey |hoi |dag |ok |oke |okay )?(' + names.join('|') + ')\\b[\\s,:;.!-]*', 'i');
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

/**
 * Turn a spoken sentence into an intent. ctx.waiting = employees currently standing at the player (FIFO),
 * ctx.selected = employee the player last interacted with.
 */
export function parseIntent(text, ctx = {}) {
  const norm = normalize(text), raw = String(text || '').trim();
  const named = findEmployee(norm), waiting = ctx.waiting || [];
  const employee = named || waiting[0] || ctx.selected || 'maya';
  if (!norm) return {type: 'none'};
  if (STOP.some(re => re.test(norm))) return {type: 'stop', employee};
  if (DISMISS.some(re => re.test(norm))) return {type: 'dismiss', employee};
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

const sleep = ms => new Promise(r => setTimeout(r, ms));

export class Game {
  /** deps: api(path, body?), world, speech{speak(text,{employee}),cancel()}, ui{status(text),waiting(list)}, storage, sleep */
  constructor({api, world, speech, ui = {}, storage = null, wait = sleep}) {
    Object.assign(this, {api, world, speech, ui, storage, wait});
    this.since = null; this.queue = {}; this.waiting = []; this.selected = 'maya'; this.busy = new Set();
    this.reminders = {}; this.stopFlag = false; this.presenting = null; this.lastDeckId = '';
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
    if (!['job_done', 'job_failed', 'deck_ready', 'deck_failed'].includes(event.kind)) return;
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
  context() { return {waiting: [...this.waiting], selected: this.selected}; }

  async say(employee, text) {
    this.world.speak(employee, true);
    try { await this.speech.speak(text, {employee}); } finally { this.world.speak(employee, false); }
  }

  async handleUtterance(text) {
    const intent = parseIntent(text, this.context());
    this.ui.status?.('');
    switch (intent.type) {
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
      case 'chat': {
        const r = await this.api('/api/chat', {employee: intent.employee, instruction: intent.text});
        this.selected = intent.employee;
        return this.reply(intent.employee, r.acknowledgement || 'Bericht ontvangen.');
      }
      default: return intent;
    }
  }

  async reply(employee, text) { this.ui.status?.(text); await this.say(employee, text); return {type: 'reply', employee, text}; }

  async deliver(employee) {
    const q = this.queue[employee] || [];
    if (!this.waiting.includes(employee) && !q.length) return this.reply(employee, 'Ik heb op dit moment niets nieuws voor je.');
    const item = q[0];
    if (!item) return this.reply(employee, 'Ik heb op dit moment niets nieuws voor je.');
    this.world.setAttention(employee, false);
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
  const tone = {maya: {pitch: 1.1, rate: 1.0}, noah: {pitch: 0.85, rate: 1.0}, lina: {pitch: 1.0, rate: 1.05}};
  const pick = employee => {
    const voices = synth?.getVoices?.() || [], dutch = voices.filter(v => /^nl/i.test(v.lang));
    if (!dutch.length) return null;
    const female = dutch.filter(v => /(colette|fenna|female|vrouw)/i.test(v.name)), male = dutch.filter(v => /(frank|maarten|male|man)/i.test(v.name));
    const pool = employee === 'noah' ? (male.length ? male : dutch) : (female.length ? female : dutch);
    return pool[(employee === 'lina' ? 1 : 0) % pool.length];
  };
  return {
    available: !!synth,
    speak(text, {employee} = {}) {
      return new Promise(resolve => {
        if (!synth) return resolve();
        const u = new win.SpeechSynthesisUtterance(String(text).slice(0, 1800)), t = tone[employee] || tone.maya;
        u.lang = 'nl-NL'; u.pitch = t.pitch; u.rate = t.rate;
        const v = pick(employee); if (v) u.voice = v;
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
