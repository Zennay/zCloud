'use strict';
// Pure settings helpers (no Electron imports) so they can be unit-tested in Node.
const DEFAULTS = Object.freeze({
  host: '', port: 22, username: '', keyPath: '', hostFingerprint: '',
  localPort: 38422, remotePort: 3422, rdpRemotePort: 3389, rdpUsername: '', whisperModel: 'Xenova/whisper-base', tokenEnc: '',
});
const MODEL_RE = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;
const HOST_RE = /^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$|^\d{1,3}(?:\.\d{1,3}){3}$/;

function toPort(value, fallback, name, allowZero = false) {
  const n = Number(value === '' || value === undefined || value === null ? fallback : value);
  if (!Number.isInteger(n) || n < (allowZero ? 0 : 1) || n > 65535) throw new Error(name + ' moet een poortnummer tussen 1 en 65535 zijn');
  return n;
}

/** Validate user input from the setup form. Returns a clean settings object or throws an Error with a Dutch message. */
function validate(input, previous = {}) {
  const host = String(input.host || '').trim();
  if (!host || host.length > 253 || !HOST_RE.test(host)) throw new Error('Vul het adres van je VPS in (bijvoorbeeld een IP-adres of hostnaam)');
  const username = String(input.username || '').trim();
  if (!/^[a-z_][a-z0-9_.-]{0,31}$/i.test(username)) throw new Error('Vul je SSH-gebruikersnaam in');
  const keyPath = String(input.keyPath || previous.keyPath || '').trim();
  if (!keyPath) throw new Error('Kies je SSH-sleutelbestand (de privésleutel)');
  if (/\.pub$/i.test(keyPath)) throw new Error('Dat is de publieke sleutel (.pub). Kies de privésleutel zonder .pub');
  const model = String(input.whisperModel || previous.whisperModel || DEFAULTS.whisperModel).trim();
  if (!MODEL_RE.test(model)) throw new Error('Ongeldige naam voor het spraakmodel');
  const hostChanged = host !== previous.host || toPort(input.port, 22, 'SSH-poort') !== (previous.port || 22);
  return {
    ...DEFAULTS, ...previous,
    host, username, keyPath, whisperModel: model,
    port: toPort(input.port, 22, 'SSH-poort'),
    localPort: toPort(input.localPort, previous.localPort || DEFAULTS.localPort, 'Lokale poort', true),
    remotePort: toPort(input.remotePort, previous.remotePort || DEFAULTS.remotePort, 'Office-poort'),
    rdpRemotePort: toPort(input.rdpRemotePort, previous.rdpRemotePort || DEFAULTS.rdpRemotePort, 'Bureaublad-poort'),
    rdpUsername: (() => { const u = String(input.rdpUsername ?? previous.rdpUsername ?? '').trim(); if (!/^[A-Za-z0-9_.\\-]{0,64}$/.test(u)) throw new Error('Ongeldige gebruikersnaam voor het VPS-bureaublad'); return u; })(),
    hostFingerprint: hostChanged ? '' : (previous.hostFingerprint || ''),
  };
}

function isComplete(s) { return !!(s && s.host && s.username && s.keyPath); }

/** What the setup page may see: never the encrypted token itself. */
function publicView(s) {
  const {tokenEnc, ...rest} = s || {};
  return {...DEFAULTS, ...rest, hasToken: !!tokenEnc, tokenEnc: undefined};
}

module.exports = {DEFAULTS, validate, isComplete, publicView};
