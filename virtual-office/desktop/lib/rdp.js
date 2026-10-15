'use strict';
// "VPS desktop": a bare remote desktop on the VPS, reached through the SSH tunnel. No password is ever written to disk.
const RDP_USER_RE = /^[A-Za-z0-9_.\\-]{0,64}$/;

/** Text of a .rdp file that connects to the loopback end of the tunnel. */
function buildRdp({port, username = '', fullscreen = true}) {
  const p = Number(port);
  if (!Number.isInteger(p) || p < 1 || p > 65535) throw new Error('Ongeldige RDP-poort');
  if (!RDP_USER_RE.test(username)) throw new Error('Ongeldige gebruikersnaam voor het bureaublad');
  return [
    'full address:s:127.0.0.1:' + p,
    'username:s:' + username,
    'prompt for credentials:i:1',            // Windows asks for the VPS desktop password; we never store it
    'screen mode id:i:' + (fullscreen ? 2 : 1),
    'use multimon:i:0',
    'smart sizing:i:1',
    'dynamic resolution:i:1',
    'authentication level:i:0',              // the loopback certificate is self-signed; the SSH tunnel is what is authenticated
    'enablecredsspsupport:i:1',
    'redirectclipboard:i:1',
    'redirectprinters:i:0', 'redirectdrives:i:0', 'redirectcomports:i:0', 'redirectsmartcards:i:0',   // keep the desktop isolated
    'audiomode:i:0',
    'compression:i:1',
    'networkautodetect:i:1',
    'bandwidthautodetect:i:1',
    'displayconnectionbar:i:1',
    'disable wallpaper:i:1',
    'disable themes:i:0',
    '',
  ].join('\r\n');
}

/** Which program opens it on this OS. */
function launcherFor(platform, file, port) {
  if (platform === 'win32') return {cmd: 'mstsc.exe', args: [file]};
  if (platform === 'darwin') return {cmd: 'open', args: [file]};
  return {cmd: 'xfreerdp', args: ['/v:127.0.0.1:' + port, '/dynamic-resolution', '+clipboard']};
}

module.exports = {buildRdp, launcherFor, RDP_USER_RE};
