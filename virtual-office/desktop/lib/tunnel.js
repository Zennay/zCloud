'use strict';
// SSH local port forward: 127.0.0.1:<localPort> on this PC -> 127.0.0.1:<remotePort> on the VPS.
// The Office token never travels in clear over the internet: everything goes through the SSH connection.
const net = require('net');
const crypto = require('crypto');

function fingerprint(hostKey) {
  const buf = Buffer.isBuffer(hostKey) ? hostKey : Buffer.from(String(hostKey), 'hex');
  return 'SHA256:' + crypto.createHash('sha256').update(buf).digest('base64').replace(/=+$/, '');
}

/**
 * opts: {host, port, username, privateKey, passphrase, expectedFingerprint, confirmFingerprint(fp)->Promise<bool>,
 *        localPort, remoteHost, remotePort, extraForwards: [{name, remotePort, remoteHost?, localPort?}], onClose(reason), Client (injectable for tests)}
 * Resolves {port, forwards: {name: localPort}, fingerprint, close()}. Rejects with a Dutch Error message.
 */
function openTunnel(opts) {
  const {Client} = opts.Client ? {Client: opts.Client} : require('ssh2');
  const remoteHost = opts.remoteHost || '127.0.0.1', remotePort = opts.remotePort || 3422;
  return new Promise((resolve, reject) => {
    const conn = new Client();
    const servers = []; let settled = false, seenFingerprint = '', closed = false;
    const fail = err => { if (settled) return; settled = true; for (const sv of servers) { try { sv.close(); } catch {} } try { conn.end(); } catch {} reject(err); };
    const close = reason => {
      if (closed) return; closed = true;
      for (const sv of servers) { try { sv.close(); } catch {} }
      try { conn.end(); } catch {}
      if (settled && opts.onClose) opts.onClose(reason || 'gesloten');
    };
    conn.on('error', err => {
      if (!settled) {
        const msg = String(err && err.message || err);
        if (/encrypted|passphrase/i.test(msg)) return fail(new Error('Je sleutel is beveiligd met een wachtwoordzin. Vul die in en probeer opnieuw.'));
        if (/authentication/i.test(msg)) return fail(new Error('Inloggen met je SSH-sleutel is mislukt. Controleer gebruikersnaam en sleutel.'));
        if (/timed out|ETIMEDOUT/i.test(msg)) return fail(new Error('De VPS reageert niet (time-out). Controleer het adres en je internetverbinding.'));
        if (/ENOTFOUND|EAI_AGAIN/.test(msg)) return fail(new Error('Dit adres is niet gevonden. Controleer de hostnaam of het IP-adres.'));
        if (/ECONNREFUSED/.test(msg)) return fail(new Error('De VPS weigert de verbinding op deze SSH-poort.'));
        return fail(new Error('SSH-fout: ' + msg));
      }
      close('fout: ' + (err && err.message));
    });
    conn.on('close', () => close('verbinding verbroken'));
    conn.on('ready', async () => {
      const makeServer = fw => net.createServer(sock => {
        conn.forwardOut('127.0.0.1', sock.remotePort || 0, fw.remoteHost, fw.remotePort, (err, stream) => {
          if (err) { sock.destroy(); return; }
          sock.pipe(stream).pipe(sock);
          const end = () => { sock.destroy(); try { stream.close ? stream.close() : stream.end(); } catch {} };
          stream.on('close', end); sock.on('close', end); sock.on('error', end); stream.on('error', end);
        });
      });
      const listen = (srv, port) => new Promise((ok, no) => { srv.once('error', no); srv.listen(port || 0, '127.0.0.1', () => { srv.removeListener('error', no); ok(srv.address().port); }); });
      try {
        const primary = makeServer({remoteHost, remotePort}); servers.push(primary);
        let port;
        try { port = await listen(primary, opts.localPort); } catch (err) { if (opts.localPort && err.code === 'EADDRINUSE') port = await listen(primary, 0); else throw err; }
        const forwards = {};   // extra, named forwards (e.g. the VPS desktop over RDP), each on its own free local port
        for (const fw of opts.extraForwards || []) {
          const srv = makeServer({remoteHost: fw.remoteHost || '127.0.0.1', remotePort: fw.remotePort}); servers.push(srv);
          forwards[fw.name] = await listen(srv, fw.localPort);
        }
        if (settled) return;
        settled = true;
        resolve({port, forwards, fingerprint: seenFingerprint, close});
      } catch (err) { fail(new Error('Lokale poort niet beschikbaar: ' + err.message)); }
    });
    try {
      conn.connect({
        host: opts.host, port: opts.port || 22, username: opts.username,
        privateKey: opts.privateKey, passphrase: opts.passphrase || undefined,
        readyTimeout: 20000, keepaliveInterval: 20000, keepaliveCountMax: 3,
        hostVerifier: (key, verify) => {
          const fp = fingerprint(key); seenFingerprint = fp;
          const done = ok => { if (typeof verify === 'function') verify(ok); return ok; };
          if (opts.expectedFingerprint) {
            if (opts.expectedFingerprint === fp) return done(true);
            fail(new Error('De vingerafdruk van de VPS is veranderd (' + fp + '). Dit kan een aanval zijn of een nieuwe VPS-installatie. Wis de opgeslagen vingerafdruk in de instellingen als je dit verwachtte.'));
            return done(false);
          }
          Promise.resolve(opts.confirmFingerprint ? opts.confirmFingerprint(fp) : false).then(ok => {
            if (!ok) fail(new Error('Je hebt de VPS niet vertrouwd. Verbinding geannuleerd.'));
            done(!!ok);
          }, () => { fail(new Error('Vertrouwen bevestigen mislukt.')); done(false); });
          return undefined;
        },
      });
    } catch (err) { fail(new Error('Je sleutelbestand kon niet worden gelezen: ' + err.message)); }
  });
}

module.exports = {openTunnel, fingerprint};
