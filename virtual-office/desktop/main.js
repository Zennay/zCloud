'use strict';
// The Penthouse desktop client: opens an SSH tunnel to the VPS and shows the Virtual Office in a native window.
// All work (workers, relays, queue, decks) stays on the VPS. This app only adds: the tunnel, microphone + local speech recognition.
const {app, BrowserWindow, ipcMain, dialog, session, safeStorage, Menu, shell} = require('electron');
const fs = require('fs');
const path = require('path');
const {Worker} = require('worker_threads');
const {validate, isComplete, publicView} = require('./lib/settings');
const {openTunnel} = require('./lib/tunnel');
const pcm = require('./lib/pcm');
const {buildRdp, launcherFor} = require('./lib/rdp');
const {spawn} = require('child_process');

let win = null, tunnel = null, settings = null, passphrase = '', sessionToken = '', reconnecting = false, quitting = false;
let worker = null, workerReady = false, pending = new Map(), nextId = 1;
const settingsFile = () => path.join(app.getPath('userData'), 'settings.json');

function loadSettings() {
  try { return {...require('./lib/settings').DEFAULTS, ...JSON.parse(fs.readFileSync(settingsFile(), 'utf8'))}; }
  catch { return {...require('./lib/settings').DEFAULTS}; }
}
function saveSettings(next) {
  fs.mkdirSync(path.dirname(settingsFile()), {recursive: true});
  fs.writeFileSync(settingsFile(), JSON.stringify(next, null, 2), {mode: 0o600});
}
function getToken() {
  if (sessionToken) return sessionToken;
  if (settings.tokenEnc && safeStorage.isEncryptionAvailable()) {
    try { return safeStorage.decryptString(Buffer.from(settings.tokenEnc, 'base64')); } catch { return ''; }
  }
  return '';
}

const isSetupSender = e => String(e.senderFrame?.url || '').startsWith('file://');
const officeOrigin = () => (tunnel ? 'http://127.0.0.1:' + tunnel.port : '');
const isOfficeSender = e => !!tunnel && String(e.senderFrame?.url || '').startsWith(officeOrigin() + '/');

function showSetup(message = '') {
  if (!win) return;
  win.loadFile(path.join(__dirname, 'setup.html'), {query: {error: message}});
}

async function confirmFingerprint(fp) {
  const {response} = await dialog.showMessageBox(win, {
    type: 'question', buttons: ['Vertrouwen en onthouden', 'Annuleren'], defaultId: 1, cancelId: 1,
    title: 'Nieuwe VPS vertrouwen?',
    message: 'Dit is de eerste keer dat je met deze VPS verbindt.',
    detail: 'Vingerafdruk van de VPS:\n' + fp + '\n\nVergelijk deze met `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` op de VPS als je zeker wilt zijn.',
  });
  return response === 0;
}

async function connect() {
  if (tunnel) { try { tunnel.close('opnieuw verbinden'); } catch {} tunnel = null; }
  let privateKey;
  try { privateKey = fs.readFileSync(settings.keyPath); }
  catch (err) { return showSetup('Je sleutelbestand kon niet worden gelezen: ' + err.message); }
  try {
    tunnel = await openTunnel({
      host: settings.host, port: settings.port, username: settings.username, privateKey, passphrase,
      expectedFingerprint: settings.hostFingerprint, confirmFingerprint,
      localPort: settings.localPort, remotePort: settings.remotePort, extraForwards: [{name: 'rdp', remotePort: settings.rdpRemotePort}],
      onClose: reason => { tunnel = null; if (!quitting) scheduleReconnect(reason); },
    });
  } catch (err) { return showSetup(err.message); }
  if (!settings.hostFingerprint && tunnel.fingerprint) { settings = {...settings, hostFingerprint: tunnel.fingerprint}; saveSettings(settings); }
  if (tunnel.port !== settings.localPort && settings.localPort) { /* a different origin than before: browser storage starts fresh, that is fine */ }
  reconnecting = false;
  win.loadURL('http://127.0.0.1:' + tunnel.port + '/');
}

function scheduleReconnect(reason, attempt = 1) {
  if (reconnecting && attempt === 1) return;
  reconnecting = true;
  win?.webContents.executeJavaScript("document.getElementById('connection') && (document.getElementById('connection').textContent='Verbinding onderbroken, opnieuw verbinden…')").catch(() => {});
  setTimeout(async () => {
    if (quitting) return;
    try {
      let key; key = fs.readFileSync(settings.keyPath);
      tunnel = await openTunnel({
        host: settings.host, port: settings.port, username: settings.username, privateKey: key, passphrase,
        expectedFingerprint: settings.hostFingerprint, confirmFingerprint: async () => false,
        localPort: settings.localPort, remotePort: settings.remotePort, extraForwards: [{name: 'rdp', remotePort: settings.rdpRemotePort}],
        onClose: r => { tunnel = null; if (!quitting) scheduleReconnect(r); },
      });
      reconnecting = false;
      if (!win.webContents.getURL().startsWith('http://127.0.0.1:' + tunnel.port)) win.loadURL('http://127.0.0.1:' + tunnel.port + '/');
      else win.webContents.reload();
    } catch (err) {
      if (/vingerafdruk|wachtwoordzin|Inloggen/.test(err.message)) { reconnecting = false; return showSetup(err.message); }
      scheduleReconnect(err.message, Math.min(attempt + 1, 6));
    }
  }, Math.min(30000, 3000 * attempt));
}

// ---------------------------------------------------------------- voice (local Whisper in a worker thread)
function startWorker() {
  if (worker) return;
  const file = path.join(__dirname, 'voice-worker.js').replace('app.asar' + path.sep, 'app.asar.unpacked' + path.sep);
  worker = new Worker(file, {workerData: {cacheDir: path.join(app.getPath('userData'), 'models'), model: settings.whisperModel}});
  worker.on('message', m => {
    if (m.type === 'status') { workerReady = m.ready === true || workerReady; win?.webContents.send('voice:status', m.text); }
    else if (m.type === 'result' || m.type === 'error') {
      const p = pending.get(m.id); pending.delete(m.id);
      if (p) m.type === 'result' ? p.resolve(m.text) : p.reject(new Error(m.message));
    }
  });
  worker.on('error', err => { win?.webContents.send('voice:status', 'Spraakherkenning faalde: ' + err.message); worker = null; workerReady = false; for (const p of pending.values()) p.reject(err); pending.clear(); });
  worker.on('exit', () => { worker = null; workerReady = false; });
}

ipcMain.handle('voice:transcribe', async (event, {samples, rate}) => {
  if (!isOfficeSender(event)) throw new Error('Niet toegestaan');
  const prepared = pcm.prepare([new Float32Array(samples)], Number(rate) || 16000);
  if (prepared.skip) return '';
  startWorker();
  const id = nextId++;
  const text = await new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    worker.postMessage({type: 'transcribe', id, audio: prepared.samples}, [prepared.samples.buffer]);
    setTimeout(() => { if (pending.delete(id)) reject(new Error('Spraakherkenning duurde te lang')); }, 120000);
  });
  return pcm.cleanTranscript(text);
});
/** Open the bare VPS desktop (RDP) through the SSH tunnel. The owner types the desktop password in Windows' own dialog. */
function openVpsDesktop() {
  if (!tunnel?.forwards?.rdp) throw new Error('Het VPS-bureaublad is nog niet verbonden. Wacht tot de tunnel staat.');
  const file = path.join(app.getPath('userData'), 'vps-desktop.rdp');
  fs.writeFileSync(file, buildRdp({port: tunnel.forwards.rdp, username: settings.rdpUsername}), {mode: 0o600});
  const {cmd, args} = launcherFor(process.platform, file, tunnel.forwards.rdp);
  const child = spawn(cmd, args, {detached: true, stdio: 'ignore'});
  child.on('error', err => dialog.showErrorBox('VPS-bureaublad', 'Kon ' + cmd + ' niet starten: ' + err.message + '\n\nHandmatig verbinden kan met 127.0.0.1:' + tunnel.forwards.rdp + ' zolang deze app verbonden is.'));
  child.unref();
  return true;
}
ipcMain.handle('rdp:open', e => { if (!isOfficeSender(e)) throw new Error('Niet toegestaan'); return openVpsDesktop(); });
ipcMain.handle('token:get', e => (isOfficeSender(e) ? getToken() : ''));

// ---------------------------------------------------------------- setup page
ipcMain.handle('setup:get', e => { if (!isSetupSender(e)) throw new Error('Niet toegestaan'); return {...publicView(settings), encryptionAvailable: safeStorage.isEncryptionAvailable()}; });
ipcMain.handle('setup:pick-key', async e => {
  if (!isSetupSender(e)) throw new Error('Niet toegestaan');
  const r = await dialog.showOpenDialog(win, {title: 'Kies je SSH-privésleutel', defaultPath: path.join(app.getPath('home'), '.ssh'), properties: ['openFile', 'showHiddenFiles']});
  return r.canceled ? '' : r.filePaths[0];
});
ipcMain.handle('setup:save', async (e, form) => {
  if (!isSetupSender(e)) throw new Error('Niet toegestaan');
  const next = validate(form, settings);
  passphrase = String(form.passphrase || '');
  const code = String(form.token || '').trim();
  if (code) {
    if (safeStorage.isEncryptionAvailable()) { next.tokenEnc = safeStorage.encryptString(code).toString('base64'); sessionToken = ''; }
    else { sessionToken = code; next.tokenEnc = ''; }
  }
  if (form.clearFingerprint) next.hostFingerprint = '';
  settings = next; saveSettings(settings);
  setImmediate(connect);
  return {ok: true};
});

function buildMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {label: 'Kantoor', submenu: [
      {label: 'Verbinding en instellingen…', click: () => showSetup()},
      {label: 'Opnieuw verbinden', accelerator: 'F5', click: () => (isComplete(settings) ? connect() : showSetup())},
      {label: 'Volledig scherm', accelerator: 'F11', click: () => win.setFullScreen(!win.isFullScreen())},
      {label: 'VPS-bureaublad openen (RDP)', accelerator: 'F9', click: () => { try { openVpsDesktop(); } catch (e) { dialog.showErrorBox('VPS-bureaublad', e.message); } }},
      {type: 'separator'}, {role: 'quit', label: 'Afsluiten'}]},
    {label: 'Beeld', submenu: [{role: 'zoomIn'}, {role: 'zoomOut'}, {role: 'resetZoom'}]},
  ]));
}

function createWindow() {
  win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 900, minHeight: 600, backgroundColor: '#152026', title: 'The Penthouse',
    webPreferences: {preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true},
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (!(url.startsWith('file://') || (tunnel && url.startsWith(officeOrigin() + '/')))) { event.preventDefault(); if (/^https:\/\//.test(url)) shell.openExternal(url); }
  });
  win.webContents.setWindowOpenHandler(({url}) => { if (/^https:\/\//.test(url)) shell.openExternal(url); return {action: 'deny'}; });
  win.on('closed', () => { win = null; });
}

app.whenReady().then(() => {
  settings = loadSettings();
  session.defaultSession.setPermissionRequestHandler((wc, permission, callback, details) => {
    const origin = (details.requestingUrl || '');
    callback(permission === 'media' && !!tunnel && origin.startsWith(officeOrigin() + '/'));
  });
  session.defaultSession.setPermissionCheckHandler((wc, permission, origin) => permission === 'media' && !!tunnel && String(origin).startsWith(officeOrigin()));
  buildMenu(); createWindow();
  if (isComplete(settings)) showSetupOrConnect(); else showSetup();
});
function showSetupOrConnect() {
  // The key may be passphrase protected: we only know after trying. connect() falls back to the setup page with a clear message.
  connect();
}
app.on('before-quit', () => { quitting = true; try { tunnel?.close('afsluiten'); } catch {} try { worker?.terminate(); } catch {} });
app.on('window-all-closed', () => app.quit());
