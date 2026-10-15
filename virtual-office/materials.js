// Procedural PBR textures and materials for the penthouse. Everything is generated at start-up; no image downloads.
import * as THREE from 'three';

export function makeRng(seed) { return () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; }; }

// value-noise helpers for texture synthesis (CPU, run once)
function noiseField(size, cells, rand) {
  const g = new Float32Array((cells + 1) * (cells + 1)).map(() => rand());
  const at = (x, y) => g[(y % (cells + 1)) * (cells + 1) + (x % (cells + 1))];
  return (u, v) => {
    const x = u * cells, y = v * cells, xi = Math.floor(x), yi = Math.floor(y), fx = x - xi, fy = y - yi;
    const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
    return (at(xi, yi) * (1 - sx) + at(xi + 1, yi) * sx) * (1 - sy) + (at(xi, yi + 1) * (1 - sx) + at(xi + 1, yi + 1) * sx) * sy;
  };
}

function canvasOf(w, h, draw) {
  const c = document.createElement('canvas'); c.width = w; c.height = h; draw(c.getContext('2d'), w, h); return c;
}

function toTexture(canvas, {srgb = true, repeat = [1, 1], aniso = 4} = {}) {
  const t = new THREE.CanvasTexture(canvas);
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeat[0], repeat[1]); t.anisotropy = aniso;
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** Polished marble slabs with veining; returns colour + roughness canvases. 2x2 slabs per texture. */
export function marbleCanvases(size = 1024, seed = 7) {
  const rand = makeRng(seed), n1 = noiseField(size, 6, rand), n2 = noiseField(size, 14, rand), n3 = noiseField(size, 40, rand);
  const color = canvasOf(size, size, (g, w, h) => {
    const img = g.createImageData(w, h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const u = x / w, v = y / h, i = (y * w + x) * 4;
      const cloud = n1(u, v) * 0.6 + n2(u, v) * 0.3 + n3(u, v) * 0.1;
      const warp = n1((u + 0.31) % 1, (v + 0.17) % 1) * 0.12, ridge = 1 - Math.abs(2 * n2((u + warp) % 1, (v + warp * 0.7) % 1) - 1);
      const ridge2 = 1 - Math.abs(2 * n3((u + warp * 0.5) % 1, (v + 0.4) % 1) - 1);
      const veinLine = Math.pow(ridge, 22) * (0.45 + 0.55 * n3(v, u)) + Math.pow(ridge2, 40) * 0.35;
      const slab = (Math.floor(u * 2) + Math.floor(v * 2)) % 2 ? 0.015 : -0.01;
      const base = 0.74 + cloud * 0.1 + slab - veinLine * 0.3;
      img.data[i] = 236 * base; img.data[i + 1] = 230 * base; img.data[i + 2] = 218 * base; img.data[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    g.strokeStyle = 'rgba(70,64,56,.55)'; g.lineWidth = 3;     // grout between slabs
    for (let i = 0; i <= 2; i++) { g.beginPath(); g.moveTo(i * w / 2, 0); g.lineTo(i * w / 2, h); g.stroke(); g.beginPath(); g.moveTo(0, i * h / 2); g.lineTo(w, i * h / 2); g.stroke(); }
  });
  const rough = canvasOf(size, size, (g, w, h) => {
    const img = g.createImageData(w, h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const u = x / w, v = y / h, i = (y * w + x) * 4, edge = Math.min(Math.abs((u * 2) % 1 - 0), Math.abs((v * 2) % 1)) < 0.004 ? 1 : 0;
      const r = 0.1 + n2(u, v) * 0.12 + n3(u, v) * 0.06 + edge * 0.5;
      img.data[i] = img.data[i + 1] = img.data[i + 2] = Math.min(255, 255 * r); img.data[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
  });
  return {color, rough};
}

/** Wide walnut planks with grain; roughness is satin. */
export function plankCanvases(size = 1024, seed = 11, tone = ['#5d4130', '#82604a']) {
  const rand = makeRng(seed), grain = noiseField(size, 90, rand);
  const rows = 8, rowH = size / rows;
  const color = canvasOf(size, size, (g, w, h) => {
    for (let r = 0; r < rows; r++) {
      let x = -rand() * 400;
      while (x < w) {
        const len = 360 + rand() * 420, shade = rand(), c0 = tone[0], c1 = tone[1];
        const grad = g.createLinearGradient(0, r * rowH, 0, (r + 1) * rowH);
        grad.addColorStop(0, c0); grad.addColorStop(1, c1);
        g.fillStyle = grad; g.fillRect(x, r * rowH, len, rowH - 1.5);
        g.fillStyle = 'rgba(' + (shade < 0.5 ? '20,10,4' : '255,220,170') + ',' + (0.06 + shade * 0.1) + ')'; g.fillRect(x, r * rowH, len, rowH - 1.5);
        g.strokeStyle = 'rgba(15,8,4,.35)'; g.lineWidth = 1.5; g.strokeRect(x, r * rowH, len, rowH - 1.5);
        for (let k = 0; k < 14; k++) {          // grain lines
          const yy = r * rowH + 3 + rand() * (rowH - 8);
          g.strokeStyle = 'rgba(' + (rand() < 0.5 ? '25,12,5' : '210,160,110') + ',' + (0.05 + rand() * 0.12) + ')'; g.lineWidth = 0.6 + rand();
          g.beginPath(); g.moveTo(x, yy); for (let xx = 0; xx <= len; xx += 40) g.lineTo(x + xx, yy + (grain(((x + xx) % size) / size, (yy % size) / size) - 0.5) * 6); g.stroke();
        }
        x += len;
      }
    }
  });
  const rough = canvasOf(size, size, (g, w, h) => { g.fillStyle = '#777'; g.fillRect(0, 0, w, h); for (let i = 0; i < 400; i++) { g.fillStyle = 'rgba(' + (rand() < 0.5 ? '255,255,255' : '0,0,0') + ',.06)'; g.fillRect(rand() * w, rand() * h, 80 + rand() * 200, 2); } });
  return {color, rough};
}

export function fabricCanvas(size = 256, base = '#7a6652') {
  return canvasOf(size, size, (g, w, h) => {
    g.fillStyle = base; g.fillRect(0, 0, w, h);
    for (let y = 0; y < h; y += 2) for (let x = 0; x < w; x += 2) {
      const shade = ((x + y) / 2) % 2 ? 14 : -10;
      g.fillStyle = shade > 0 ? 'rgba(255,255,255,.07)' : 'rgba(0,0,0,.08)'; g.fillRect(x, y, 2, 2);
    }
  });
}

export function rugCanvas(size = 1024, a = '#2c3a40', b = '#b9a37a', seed = 3) {
  const rand = makeRng(seed);
  return canvasOf(size, size, (g, w, h) => {
    g.fillStyle = a; g.fillRect(0, 0, w, h);
    g.strokeStyle = b; g.lineWidth = 10; g.strokeRect(46, 46, w - 92, h - 92);
    g.lineWidth = 3; g.strokeRect(78, 78, w - 156, h - 156);
    for (let i = 0; i < 26; i++) {   // geometric inlay
      g.strokeStyle = 'rgba(185,163,122,' + (0.15 + rand() * 0.25) + ')'; g.lineWidth = 2 + rand() * 4;
      g.beginPath(); g.arc(w / 2 + (rand() - 0.5) * w * 0.5, h / 2 + (rand() - 0.5) * h * 0.5, 30 + rand() * 180, 0, Math.PI * 2); g.stroke();
    }
    for (let i = 0; i < 9000; i++) { g.fillStyle = 'rgba(' + (rand() < 0.5 ? '255,255,255' : '0,0,0') + ',.05)'; g.fillRect(rand() * w, rand() * h, 2, 2); }
  });
}

export function artCanvas(seed, w = 512, h = 640) {
  const rand = makeRng(seed), palettes = [['#1f3a4d', '#d9b26a', '#e9e2d3', '#a84c3c'], ['#2d4a3e', '#e8d9b8', '#c98b5a', '#16262d'], ['#3b2c4f', '#e6a4a0', '#f3ead9', '#2f6e78']];
  const pal = palettes[seed % palettes.length];
  return canvasOf(w, h, (g) => {
    g.fillStyle = pal[2]; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 6; i++) { g.fillStyle = pal[Math.floor(rand() * 4)]; g.globalAlpha = 0.55 + rand() * 0.45; const rw = 80 + rand() * 300, rh = 60 + rand() * 260; g.fillRect(rand() * (w - rw), rand() * (h - rh), rw, rh); }
    g.globalAlpha = 1; g.strokeStyle = pal[3]; g.lineWidth = 5; g.beginPath(); g.arc(w * (0.3 + rand() * 0.4), h * (0.3 + rand() * 0.4), 60 + rand() * 90, 0, Math.PI * 2); g.stroke();
  });
}

export function makeMaterials(renderer) {
  const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy?.() || 1);
  const disposables = [];
  const tex = (canvas, opts) => { const t = toTexture(canvas, {aniso, ...opts}); disposables.push(t); return t; };
  const std = (params) => { const m = new THREE.MeshStandardMaterial(params); disposables.push(m); return m; };
  const phys = (params) => { const m = new THREE.MeshPhysicalMaterial(params); disposables.push(m); return m; };

  const marble = marbleCanvases(1024);
  const marbleMap = tex(marble.color, {repeat: [38 / 3.4, 30 / 3.4]}), marbleRough = tex(marble.rough, {srgb: false, repeat: [38 / 3.4, 30 / 3.4]});
  const planks = plankCanvases(1024);
  const plankMap = tex(planks.color, {repeat: [8, 2.5]}), plankRough = tex(planks.rough, {srgb: false, repeat: [8, 2.5]});
  const darkMarble = marbleCanvases(512, 19);
  const stoneMap = tex(darkMarble.color, {repeat: [3, 3]});
  const rugMap = tex(rugCanvas(), {repeat: [1, 1]});
  const fabricA = tex(fabricCanvas(256, '#8a7358'), {repeat: [4, 4]}), fabricB = tex(fabricCanvas(256, '#2b3a40'), {repeat: [4, 4]});

  return {
    marble: phys({map: marbleMap, roughnessMap: marbleRough, roughness: 1, metalness: 0, clearcoat: 0.55, clearcoatRoughness: 0.12, envMapIntensity: 0.9}),
    plank: phys({map: plankMap, roughnessMap: plankRough, roughness: 1, clearcoat: 0.25, clearcoatRoughness: 0.35, envMapIntensity: 0.6}),
    stone: std({color: '#cfcac0', map: stoneMap, roughness: 0.28, metalness: 0.05, envMapIntensity: 0.8}),
    walnut: std({color: '#5a4030', roughness: 0.46, metalness: 0.0}),
    lightWood: std({color: '#b08d63', roughness: 0.5}),
    ink: std({color: '#1b262c', roughness: 0.34, metalness: 0.75}),
    brass: std({color: '#c7a566', roughness: 0.24, metalness: 1.0, envMapIntensity: 1.3}),
    black: std({color: '#0e1419', roughness: 0.35, metalness: 0.2}),
    white: std({color: '#ebe6da', roughness: 0.6}),
    plaster: std({color: '#e4dfd3', roughness: 0.92}),
    leather: phys({color: '#7d6048', roughness: 0.55, sheen: 0.4, sheenColor: new THREE.Color('#c9a67c'), sheenRoughness: 0.5, map: fabricA}),
    darkLeather: phys({color: '#34484f', roughness: 0.5, sheen: 0.5, sheenColor: new THREE.Color('#5b7580'), sheenRoughness: 0.45, map: fabricB}),
    fabric: std({color: '#d9d0c0', roughness: 0.95, map: fabricA}),
    green: (() => { const g = std({color: '#4a6f4e', roughness: 0.7}); g.emissive = new THREE.Color('#1f4026'); g.emissiveIntensity = 0.55; g.side = THREE.DoubleSide; return g; })(),
    rug: std({map: rugMap, roughness: 1}),
    glass: phys({color: '#cfe3e6', transparent: true, opacity: 0.12, roughness: 0.04, metalness: 0.0, depthWrite: false, envMapIntensity: 1.6, clearcoat: 1, clearcoatRoughness: 0.03}),
    glow: (() => { const m = std({color: '#ffe8c4', roughness: 0.3}); m.emissive = new THREE.Color('#ffd49a'); m.emissiveIntensity = 3.2; return m; })(),
    glowCool: (() => { const m = std({color: '#e6f1ff', roughness: 0.3}); m.emissive = new THREE.Color('#cfe4ff'); m.emissiveIntensity = 2.6; return m; })(),
    std, phys, tex, dispose() { disposables.forEach((d) => d.dispose()); },
  };
}
