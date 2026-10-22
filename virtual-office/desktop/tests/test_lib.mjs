import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {validate, isComplete, publicView, DEFAULTS} = require('../lib/settings.js');
const pcm = require('../lib/pcm.js');

test('settings validation accepts a normal setup and normalises ports', () => {
  const s = validate({host: ' 198.244.191.182 ', username: 'ubuntu', keyPath: 'C:\\Users\\z\\.ssh\\id_ed25519', port: '22'});
  assert.equal(s.host, '198.244.191.182');
  assert.equal(s.port, 22);
  assert.equal(s.localPort, DEFAULTS.localPort);
  assert.equal(s.remotePort, 3422);
  assert.ok(isComplete(s));
});

test('settings validation rejects the common mistakes with a Dutch message', () => {
  const ok = {host: 'vps.example.nl', username: 'ubuntu', keyPath: '/k/id'};
  assert.throws(() => validate({...ok, host: ''}), /adres van je VPS/);
  assert.throws(() => validate({...ok, host: 'bad host!'}), /adres van je VPS/);
  assert.throws(() => validate({...ok, username: 'a b'}), /gebruikersnaam/);
  assert.throws(() => validate({...ok, keyPath: ''}), /sleutelbestand/);
  assert.throws(() => validate({...ok, keyPath: '/k/id.pub'}), /publieke sleutel/);
  assert.throws(() => validate({...ok, port: 70000}), /poortnummer/);
  assert.throws(() => validate({...ok, whisperModel: '../../etc'}), /spraakmodel/);
});

test('changing the VPS clears the pinned fingerprint, same VPS keeps it', () => {
  const base = validate({host: 'a.example.nl', username: 'u', keyPath: '/k'});
  const pinned = {...base, hostFingerprint: 'SHA256:abc'};
  assert.equal(validate({host: 'a.example.nl', username: 'u', keyPath: '/k'}, pinned).hostFingerprint, 'SHA256:abc');
  assert.equal(validate({host: 'b.example.nl', username: 'u', keyPath: '/k'}, pinned).hostFingerprint, '');
  assert.equal(validate({host: 'a.example.nl', username: 'u', keyPath: '/k', port: 2222}, pinned).hostFingerprint, '');
});

test('the setup page never sees the encrypted token', () => {
  const view = publicView({...DEFAULTS, host: 'h', tokenEnc: 'SECRET=='});
  assert.equal(view.hasToken, true);
  assert.equal(JSON.stringify(view).includes('SECRET'), false);
});

test('pcm: merge, resample and silence/short detection', () => {
  const merged = pcm.merge([new Float32Array([1, 2]), new Float32Array([3])]);
  assert.deepEqual([...merged], [1, 2, 3]);
  const tone = n => Float32Array.from({length: n}, (_, i) => Math.sin(i / 10) * 0.3);
  assert.equal(pcm.resample(tone(48000), 48000, 16000).length, 16000);
  assert.equal(pcm.prepare([tone(1000)], 16000).skip, 'te kort');
  assert.equal(pcm.prepare([new Float32Array(16000)], 16000).skip, 'stil');
  const good = pcm.prepare([tone(48000), tone(48000)], 48000);
  assert.equal(good.skip, '');
  assert.ok(Math.abs(good.seconds - 2) < 0.01);
  assert.throws(() => pcm.resample(tone(10), 0), /samplefrequentie/);
});

test('pcm: long recordings keep the most recent 60 seconds', () => {
  const long = new Float32Array(16000 * 90).fill(0.2);
  assert.equal(pcm.prepare([long], 16000).samples.length, 16000 * 60);
});

test('pcm: known Whisper silence hallucinations are dropped', () => {
  for (const t of ['Ondertitels ingediend door de Amara.org gemeenschap', 'Thanks for watching!', '  ...  ', '']) assert.equal(pcm.cleanTranscript(t), '', t);
  assert.equal(pcm.cleanTranscript('  Maya   zeg maar '), 'Maya zeg maar');
});
