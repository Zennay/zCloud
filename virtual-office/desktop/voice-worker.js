'use strict';
// Local speech recognition (Whisper via transformers.js / ONNX) in a worker thread, so the window never freezes.
const {parentPort, workerData} = require('worker_threads');
let asr = null, loading = null;

async function load() {
  if (asr) return asr;
  if (!loading) loading = (async () => {
    const {pipeline, env} = await import('@huggingface/transformers');
    env.cacheDir = workerData.cacheDir; env.allowLocalModels = true;
    parentPort.postMessage({type: 'status', text: 'Spraakmodel laden… (de eerste keer wordt het gedownload, ongeveer 150 MB)'});
    let last = 0;
    asr = await pipeline('automatic-speech-recognition', workerData.model, {
      dtype: 'q8', device: 'cpu',
      progress_callback: p => { if (p.status === 'progress' && p.progress - last >= 10) { last = p.progress; parentPort.postMessage({type: 'status', text: 'Spraakmodel downloaden… ' + Math.round(p.progress) + '%'}); } },
    });
    parentPort.postMessage({type: 'status', text: 'Spraakherkenning klaar', ready: true});
    return asr;
  })();
  return loading;
}

parentPort.on('message', async m => {
  if (m.type !== 'transcribe') return;
  try {
    const recognizer = await load();
    const out = await recognizer(m.audio, {language: 'dutch', task: 'transcribe', chunk_length_s: 30});
    parentPort.postMessage({type: 'result', id: m.id, text: String(out.text || '')});
  } catch (err) {
    loading = null; asr = null;
    parentPort.postMessage({type: 'error', id: m.id, message: 'Spraakherkenning mislukt: ' + (err && err.message || err)});
  }
});
