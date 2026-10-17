'use strict';
// Pure audio helpers for push-to-talk. Input: Float32 chunks from the microphone.
const MIN_SECONDS = 0.4, MAX_SECONDS = 60, SILENCE_RMS = 0.004;
const HALLUCINATIONS = [/amara\.org/i, /ondertitel/i, /untertitel/i, /thanks for watching/i, /bedankt voor het kijken/i, /subscribe/i, /^\W*$/];

function merge(chunks) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Float32Array(total);
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  return out;
}

function rms(samples) {
  if (!samples.length) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

/** Box-filter downsample (good enough for speech). */
function resample(samples, inRate, outRate = 16000) {
  if (!(inRate > 0)) throw new Error('Ongeldige samplefrequentie');
  if (inRate === outRate) return samples;
  const ratio = inRate / outRate, length = Math.floor(samples.length / ratio), out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio), end = Math.min(samples.length, Math.max(start + 1, Math.floor((i + 1) * ratio)));
    let sum = 0;
    for (let j = start; j < end; j++) sum += samples[j];
    out[i] = sum / (end - start);
  }
  return out;
}

/** Returns {samples, seconds, skip: ''|'te kort'|'stil'} ready for Whisper at 16 kHz. */
function prepare(chunks, inRate) {
  let samples = resample(merge(chunks), inRate, 16000);
  if (samples.length > MAX_SECONDS * 16000) samples = samples.subarray(samples.length - MAX_SECONDS * 16000);
  const seconds = samples.length / 16000;
  if (seconds < MIN_SECONDS) return {samples, seconds, skip: 'te kort'};
  if (rms(samples) < SILENCE_RMS) return {samples, seconds, skip: 'stil'};
  return {samples, seconds, skip: ''};
}

function cleanTranscript(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  return HALLUCINATIONS.some(re => re.test(t)) ? '' : t;
}

module.exports = {merge, rms, resample, prepare, cleanTranscript, MIN_SECONDS, MAX_SECONDS, SILENCE_RMS};
