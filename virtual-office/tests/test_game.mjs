import test from 'node:test';
import assert from 'node:assert/strict';
import {parseIntent, normalize, Game, slideSpeech} from '../game.js';

const waiting = ['maya'];
test('normalize strips accents and punctuation', () => assert.equal(normalize('Oké, zég maar!'), 'oke zeg maar'));

test('speak intents (only meaningful when someone waits)', () => {
  for (const t of ['zeg maar', 'Ja, zeg maar!', 'ga je gang', 'vertel maar', 'wat wilde je zeggen', 'ja', 'oké', 'Maya, zeg maar'])
    assert.equal(parseIntent(t, {waiting}).type, 'speak', t);
  assert.equal(parseIntent('Noah zeg maar', {waiting}).employee, 'noah');
  assert.equal(parseIntent('zeg maar', {waiting: ['lina', 'maya']}).employee, 'lina');
});

test('dismiss and stop intents', () => {
  for (const t of ['nu niet', 'later', 'ga maar zitten', 'dankjewel', 'bedankt']) assert.equal(parseIntent(t, {waiting}).type, 'dismiss', t);
  for (const t of ['stop', 'genoeg', 'stop de presentatie']) assert.equal(parseIntent(t, {}).type, 'stop', t);
});

test('deck requests carry the topic and win over task verbs', () => {
  const i = parseIntent('Maya maak een presentatie over de voortgang van zCloud', {});
  assert.deepEqual([i.type, i.employee, i.topic], ['deck', 'maya', 'de voortgang van zCloud']);
  assert.equal(parseIntent('presenteer iets over security', {selected: 'lina'}).topic, 'security');
  assert.equal(parseIntent('maak een presentatie', {}).type, 'deck_missing_topic');
  assert.equal(parseIntent('laat de presentatie maar zien', {}).type, 'show_deck');
});

test('tasks need a verb and enough words; chit-chat is not a task', () => {
  const t = parseIntent('Noah bouw een loginpagina voor Supa', {});
  assert.deepEqual([t.type, t.employee, t.text], ['task', 'noah', 'bouw een loginpagina voor Supa']);
  assert.notEqual(parseIntent('bouw', {}).type, 'task');
  assert.equal(parseIntent('Lina hoe gaat het met je', {}).type, 'chat');
  assert.equal(parseIntent('blabla', {}).type, 'unknown');
  assert.equal(parseIntent('', {}).type, 'none');
});

test('recognition variants of names resolve', () => {
  assert.equal(parseIntent('Maja zeg maar', {}).employee, 'maya');
  assert.equal(parseIntent('Noa ga je gang', {}).employee, 'noah');
  assert.equal(parseIntent('Lena vertel maar', {}).employee, 'lina');
});

test('slideSpeech prefers speaker text, falls back to title+body', () => {
  assert.equal(slideSpeech({title: 'T', body: 'a\nb', speaker: 'Hallo'}), 'Hallo');
  assert.equal(slideSpeech({title: 'T', body: 'a\nb'}), 'T. a. b');
});

function harness({events = [], summaries = {}, decks = {}} = {}) {
  const log = [];
  const api = async (path, body) => {
    log.push(['api', path, body]);
    if (path.startsWith('/api/events')) return {events: events.splice(0), last_id: 7};
    const m = path.match(/^\/api\/jobs\/(.+)\/summary$/);
    if (m) return summaries[m[1]] || {text: 'geen', pending: false};
    if (path === '/api/decks') return {decks: Object.keys(decks).map(id => ({id}))};
    if (path.startsWith('/api/decks/') && path !== '/api/decks/request') return decks[path.split('/').pop()];
    if (path === '/api/decks/request') return {acknowledgement: 'Prima, ik ga aan de presentatie werken.'};
    if (path === '/api/jobs') return {acknowledgement: 'Opdracht ontvangen.'};
    return {};
  };
  const world = {
    callToPlayer: async id => { log.push(['call', id]); return true; },
    sendHome: async id => { log.push(['home', id]); return true; },
    walkToBoard: async id => { log.push(['board', id]); return true; },
    setAttention: (id, on) => log.push(['attention', id, on]),
    speak: (id, on) => log.push(['mouth', id, on]),
    setSlide: (s, i, n) => log.push(['slide', s.title, i, n]),
  };
  const speech = {speak: async (text, o) => { log.push(['say', o.employee, text]); }, cancel: () => log.push(['cancel'])};
  const store = new Map();
  const storage = {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v)};
  const game = new Game({api, world, speech, ui: {status: () => {}, waiting: () => {}}, storage, wait: async () => {}});
  return {game, log, events, store};
}
const tick = () => new Promise(r => setImmediate(r));

test('fresh start never replays history', async () => {
  const h = harness({events: [{id: 1, employee: 'maya', kind: 'job_done', ref: 'old'}]});
  await h.game.init();
  assert.equal(h.log.filter(l => l[0] === 'call').length, 0);
  assert.equal(h.store.get('office.game.since'), '7');
});

test('finished work: worker walks over and waits silently until told to speak', async () => {
  const h = harness({summaries: {j1: {text: 'De build is groen en de loginpagina staat live.', pending: false}}});
  await h.game.init();
  h.events.push({id: 8, employee: 'maya', kind: 'job_done', ref: 'j1', payload: {}});
  await h.game.poll(); await tick();
  assert.deepEqual(h.log.filter(l => l[0] === 'call'), [['call', 'maya']]);
  assert.equal(h.log.some(l => l[0] === 'say'), false, 'must not speak before the owner asks');
  assert.deepEqual(h.game.waiting, ['maya']);
  await h.game.handleUtterance('zeg maar');
  const said = h.log.filter(l => l[0] === 'say');
  assert.deepEqual(said, [['say', 'maya', 'De build is groen en de loginpagina staat live.']]);
  assert.deepEqual(h.log.filter(l => l[0] === 'home'), [['home', 'maya']]);
  assert.deepEqual(h.game.waiting, []);
});

test('duplicate events do not queue twice; second item keeps her waiting', async () => {
  const h = harness({summaries: {a: {text: 'Een.', pending: false}, b: {text: 'Twee.', pending: false}}});
  await h.game.init();
  for (const e of [{employee: 'maya', kind: 'job_done', ref: 'a'}, {employee: 'maya', kind: 'job_done', ref: 'a'}, {employee: 'maya', kind: 'job_done', ref: 'b'}]) h.game.handleEvent(e);
  await tick();
  assert.equal(h.game.queue.maya.length, 2);
  assert.equal(h.log.filter(l => l[0] === 'call').length, 1);
  await h.game.handleUtterance('ja');
  assert.deepEqual(h.log.filter(l => l[0] === 'home'), [], 'still has something to tell');
  await h.game.handleUtterance('ja');
  assert.deepEqual(h.log.filter(l => l[0] === 'say').map(l => l[2]), ['Een.', 'Twee.']);
  assert.equal(h.log.filter(l => l[0] === 'home').length, 1);
});

test('"nu niet" sends her home but keeps the news queued', async () => {
  const h = harness({summaries: {a: {text: 'Klaar.', pending: false}}});
  await h.game.init();
  h.game.handleEvent({employee: 'noah', kind: 'job_done', ref: 'a'}); await tick();
  await h.game.handleUtterance('nu niet');
  assert.equal(h.log.some(l => l[0] === 'say'), false);
  assert.equal(h.game.queue.noah.length, 1);
  clearTimeout(h.game.reminders.noah);
});

test('summary still being phrased: waits briefly, then speaks what the server has', async () => {
  const seq = [{text: 'voorlopig', pending: true}, {text: 'voorlopig', pending: true}, {text: 'definitief', pending: false}];
  const h = harness(); let n = 0;
  h.game.api = async p => p.endsWith('/summary') ? seq[Math.min(n++, 2)] : {events: [], last_id: 0};
  h.game.queue.lina = [{kind: 'job_done', ref: 'x'}]; h.game.waiting = ['lina'];
  await h.game.handleUtterance('Lina zeg maar');
  assert.deepEqual(h.log.filter(l => l[0] === 'say').map(l => l[2]), ['definitief']);
});

test('employee-made deck: walks to the screen, presents slide by slide, then returns', async () => {
  const deck = {title: 'Status', slides: [{title: 'Een', body: 'a', speaker: 'Dit is slide een.'}, {title: 'Twee', body: 'b'}]};
  const h = harness({decks: {d1: deck}});
  await h.game.init();
  h.game.handleEvent({employee: 'lina', kind: 'deck_ready', ref: 'd1'}); await tick();
  await h.game.handleUtterance('zeg maar');
  const order = h.log.filter(l => ['call', 'board', 'slide', 'say', 'home'].includes(l[0])).map(l => l[0] === 'say' ? 'say:' + l[2] : l[0] === 'slide' ? 'slide:' + l[1] : l[0]);
  assert.deepEqual(order, ['call', 'board', 'slide:Een', 'say:Dit is slide een.', 'slide:Twee', 'say:Twee. b', 'home']);
});

test('stop interrupts a presentation', async () => {
  const deck = {title: 'S', slides: [1, 2, 3].map(i => ({title: 'S' + i, body: '', speaker: 'x' + i}))};
  const h = harness({decks: {d1: deck}});
  let count = 0;
  h.game.speech.speak = async () => { if (++count === 1) h.game.stopPresentation(); };
  h.game.queue.maya = [{kind: 'deck_ready', ref: 'd1'}]; h.game.waiting = ['maya'];
  await h.game.handleUtterance('zeg maar');
  assert.equal(h.log.filter(l => l[0] === 'slide').length, 1);
  assert.equal(h.log.filter(l => l[0] === 'home').length, 1);
});

test('spoken orders reach the right API with the right employee', async () => {
  const h = harness();
  await h.game.handleUtterance('Noah bouw een loginpagina voor Supa');
  await h.game.handleUtterance('Lina maak een presentatie over securitytests');
  const posts = h.log.filter(l => l[0] === 'api' && l[2]);
  assert.deepEqual(posts[0].slice(1), ['/api/jobs', {employee: 'noah', kind: 'task', instruction: 'bouw een loginpagina voor Supa'}]);
  assert.deepEqual(posts[1].slice(1), ['/api/decks/request', {employee: 'lina', topic: 'securitytests'}]);
});

test('nothing to say: honest answer instead of inventing news', async () => {
  const h = harness();
  await h.game.handleUtterance('Maya zeg maar');
  assert.match(h.log.find(l => l[0] === 'say')[2], /niets nieuws/);
});
