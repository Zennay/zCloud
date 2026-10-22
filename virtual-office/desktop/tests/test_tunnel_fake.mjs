// Tunnel logic tested with an injected fake ssh2 Client and a real local TCP "office" server.
import test from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import http from 'node:http';
import {EventEmitter} from 'node:events';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {openTunnel, fingerprint} = require('../lib/tunnel.js');

const HOST_KEY = Buffer.from('fake-host-key-bytes');

function makeClient({officePort, authError = null, key = HOST_KEY, connectError = null} = {}) {
  const instances = [];
  class FakeClient extends EventEmitter {
    constructor() { super(); instances.push(this); this.ended = false; }
    connect(opts) {
      this.opts = opts;
      setImmediate(() => {
        if (connectError) return this.emit('error', new Error(connectError));
        const proceed = ok => { if (!ok) return this.emit('error', new Error('Host denied')); if (authError) return this.emit('error', new Error(authError)); this.emit('ready'); };
        opts.hostVerifier(key, proceed);
      });
    }
    forwardOut(srcIP, srcPort, dstIP, dstPort, cb) {
      this.forwarded = {dstIP, dstPort};
      const s = net.connect(officePort, '127.0.0.1');
      s.on('connect', () => cb(null, s));
      s.on('error', cb);
    }
    end() { this.ended = true; this.emit('close'); }
  }
  FakeClient.instances = instances;
  return FakeClient;
}

function office() {
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => { res.setHeader('x-token', req.headers['x-office-token'] || ''); res.end('penthouse ok'); });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}
const get = (port, headers = {}) => new Promise((resolve, reject) => http.get({host: '127.0.0.1', port, headers}, res => { let b = ''; res.on('data', d => b += d); res.on('end', () => resolve({body: b, headers: res.headers})); }).on('error', reject));

test('first connection asks the owner to trust the fingerprint, then forwards traffic', async () => {
  const srv = await office(); const Client = makeClient({officePort: srv.address().port}); const asked = [];
  const t = await openTunnel({Client, host: 'h', username: 'u', privateKey: 'k', remotePort: 3422, confirmFingerprint: async fp => { asked.push(fp); return true; }});
  assert.deepEqual(asked, [fingerprint(HOST_KEY)]);
  assert.equal(t.fingerprint, fingerprint(HOST_KEY));
  const r = await get(t.port, {'X-Office-Token': 'abc'});
  assert.equal(r.body, 'penthouse ok');
  assert.equal(r.headers['x-token'], 'abc');
  assert.equal(Client.instances[0].forwarded.dstPort, 3422);
  assert.equal(Client.instances[0].forwarded.dstIP, '127.0.0.1');
  t.close(); srv.close();
});

test('only binds to loopback and reports the chosen port', async () => {
  const srv = await office(); const Client = makeClient({officePort: srv.address().port});
  const t = await openTunnel({Client, host: 'h', username: 'u', privateKey: 'k', expectedFingerprint: fingerprint(HOST_KEY)});
  const addr = await new Promise(res => { const c = net.connect(t.port, '127.0.0.1', () => { res(c.localAddress); c.destroy(); }); });
  assert.equal(addr, '127.0.0.1');
  t.close(); srv.close();
});

test('a changed host key is refused before anything is forwarded', async () => {
  const Client = makeClient({officePort: 1, key: Buffer.from('another-key')});
  await assert.rejects(openTunnel({Client, host: 'h', username: 'u', privateKey: 'k', expectedFingerprint: fingerprint(HOST_KEY)}), /vingerafdruk van de VPS is veranderd/);
});

test('declining the fingerprint cancels the connection', async () => {
  const Client = makeClient({officePort: 1});
  await assert.rejects(openTunnel({Client, host: 'h', username: 'u', privateKey: 'k', confirmFingerprint: async () => false}), /niet vertrouwd/);
});

test('unknown fingerprint without a confirm callback is never trusted silently', async () => {
  const Client = makeClient({officePort: 1});
  await assert.rejects(openTunnel({Client, host: 'h', username: 'u', privateKey: 'k'}), /niet vertrouwd/);
});

test('friendly Dutch errors for the common failures', async () => {
  const cases = [['All configured authentication methods failed', /Inloggen met je SSH-sleutel is mislukt/],
    ['Encrypted private key detected, but no passphrase given', /wachtwoordzin/], ['connect ECONNREFUSED 1.2.3.4:22', /weigert/],
    ['getaddrinfo ENOTFOUND nope.example', /niet gevonden/], ['Timed out while waiting for handshake', /time-out/]];
  for (const [message, re] of cases) {
    const Client = makeClient({officePort: 1, connectError: message});
    await assert.rejects(openTunnel({Client, host: 'h', username: 'u', privateKey: 'k', expectedFingerprint: 'x'}), re, message);
  }
});

test('onClose fires when the SSH connection drops after a successful start', async () => {
  const srv = await office(); const Client = makeClient({officePort: srv.address().port}); let reason = '';
  const t = await openTunnel({Client, host: 'h', username: 'u', privateKey: 'k', expectedFingerprint: fingerprint(HOST_KEY), onClose: r => { reason = r; }});
  Client.instances[0].emit('close');
  assert.match(reason, /verbroken/);
  t.close(); srv.close();
});

test('busy preferred port falls back to a free one instead of failing', async () => {
  const blocker = net.createServer().listen(0, '127.0.0.1'); await new Promise(r => blocker.on('listening', r));
  const srv = await office(); const Client = makeClient({officePort: srv.address().port});
  const t = await openTunnel({Client, host: 'h', username: 'u', privateKey: 'k', expectedFingerprint: fingerprint(HOST_KEY), localPort: blocker.address().port});
  assert.notEqual(t.port, blocker.address().port);
  assert.equal((await get(t.port)).body, 'penthouse ok');
  t.close(); srv.close(); blocker.close();
});

test('an extra forward (VPS desktop over RDP) gets its own loopback port and reaches the chosen remote port', async () => {
  const srv = await office(); const Client = makeClient({officePort: srv.address().port});
  const t = await openTunnel({Client, host: 'h', username: 'u', privateKey: 'k', expectedFingerprint: fingerprint(HOST_KEY), remotePort: 3422, extraForwards: [{name: 'rdp', remotePort: 3389}]});
  assert.ok(t.forwards.rdp > 0 && t.forwards.rdp !== t.port);
  const r = await get(t.forwards.rdp);
  assert.equal(r.body, 'penthouse ok');
  assert.equal(Client.instances[0].forwarded.dstPort, 3389);
  const again = await get(t.port); assert.equal(again.body, 'penthouse ok');
  assert.equal(Client.instances[0].forwarded.dstPort, 3422);
  t.close(); srv.close();
});
