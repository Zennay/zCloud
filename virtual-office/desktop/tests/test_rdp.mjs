import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {buildRdp, launcherFor} = require('../lib/rdp.js');
const {validate, DEFAULTS} = require('../lib/settings.js');

test('the .rdp file targets the loopback end of the tunnel and never holds a password', () => {
  const text = buildRdp({port: 40123, username: 'zennay'});
  assert.match(text, /full address:s:127\.0\.0\.1:40123\r\n/);
  assert.match(text, /username:s:zennay/);
  assert.match(text, /prompt for credentials:i:1/);
  assert.doesNotMatch(text, /password/i);
  assert.match(text, /redirectdrives:i:0/);
});

test('bad ports and user names are rejected', () => {
  assert.throws(() => buildRdp({port: 0}), /poort/);
  assert.throws(() => buildRdp({port: 3389, username: 'a\nfull address:s:evil'}), /gebruikersnaam/);
});

test('launcher per platform', () => {
  assert.deepEqual(launcherFor('win32', 'C:/x.rdp', 1), {cmd: 'mstsc.exe', args: ['C:/x.rdp']});
  assert.equal(launcherFor('darwin', 'x', 1).cmd, 'open');
  assert.equal(launcherFor('linux', 'x', 4000).args[0], '/v:127.0.0.1:4000');
});

test('settings carry the desktop port and user, with validation', () => {
  const base = {host: 'vps.example.com', username: 'u', keyPath: '/k'};
  const ok = validate({...base, rdpRemotePort: '3390', rdpUsername: 'zennay'});
  assert.equal(ok.rdpRemotePort, 3390); assert.equal(ok.rdpUsername, 'zennay');
  assert.equal(validate(base).rdpRemotePort, DEFAULTS.rdpRemotePort);
  assert.throws(() => validate({...base, rdpUsername: 'x y;z'}), /gebruikersnaam/);
  assert.throws(() => validate({...base, rdpRemotePort: '99999'}), /poort/i);
});
