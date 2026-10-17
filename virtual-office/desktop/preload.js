'use strict';
const {contextBridge, ipcRenderer} = require('electron');

// ---- push-to-talk recording (runs in the renderer, sends raw PCM to the main process for local Whisper)
let ctx = null, stream = null, node = null, chunks = [];
async function start() {
  if (ctx) return;
  stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
  ctx = new AudioContext({sampleRate: 16000});
  const source = ctx.createMediaStreamSource(stream);
  node = ctx.createScriptProcessor(4096, 1, 1);
  chunks = [];
  node.onaudioprocess = e => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  source.connect(node); node.connect(ctx.destination);
}
async function stop() {
  if (!ctx) return '';
  const rate = ctx.sampleRate, parts = chunks; chunks = [];
  try { node.disconnect(); } catch {}
  stream.getTracks().forEach(t => t.stop());
  try { await ctx.close(); } catch {}
  ctx = stream = node = null;
  let total = 0; for (const c of parts) total += c.length;
  const samples = new Float32Array(total); let at = 0;
  for (const c of parts) { samples.set(c, at); at += c.length; }
  return ipcRenderer.invoke('voice:transcribe', {samples, rate});
}

// Exposed to the Office page (served from the VPS through the tunnel).
contextBridge.exposeInMainWorld('officeDesktop', {
  version: 1,
  getToken: () => ipcRenderer.invoke('token:get'),
  voice: {start, stop},
  openVpsDesktop: () => ipcRenderer.invoke('rdp:open'),
  onVoiceStatus: cb => ipcRenderer.on('voice:status', (_e, text) => cb(String(text))),
});
// Exposed to the local setup page only; main re-checks the sender URL on every call.
contextBridge.exposeInMainWorld('setupApi', {
  get: () => ipcRenderer.invoke('setup:get'),
  pickKey: () => ipcRenderer.invoke('setup:pick-key'),
  save: form => ipcRenderer.invoke('setup:save', form),
});
