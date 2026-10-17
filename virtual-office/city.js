// Procedural 3D city + dynamic sky for the penthouse view. No images, no external assets: everything is generated.
// The city is real geometry (instanced buildings with shader windows, streets, river, park, moving traffic), so it has
// parallax, depth fog, lit windows at night and reflections in the glass, instead of being a flat backdrop.
import * as THREE from 'three';

export const GROUND_Y = -232;          // street level, metres below the penthouse floor
const CELL_X = 96, CELL_Z = 216, STREET = 20, ANGLE = 0.5;   // Manhattan-like blocks, grid rotated ~29 degrees

export const PRESETS = {
  golden: {night: 0.18, sunElev: 0.105, sunAz: -2.25, sunColor: '#ffb067', sunIntensity: 3.1, zenith: '#27466f', horizon: '#f2a46a', ground: '#b98562',
           fog: '#d9a07b', fogDensity: 0.00105, hemiSky: '#cfdcf5', hemiGround: '#a08a74', hemi: 1.6, exposure: 0.92, cloud: '#ffb98a', cloudShade: '#6c5a73'},
  day: {night: 0.0, sunElev: 0.82, sunAz: -2.0, sunColor: '#fff1dd', sunIntensity: 4.2, zenith: '#2f6fb7', horizon: '#bcd6ec', ground: '#9fb2b8',
        fog: '#b9cfdc', fogDensity: 0.00085, hemiSky: '#e3efff', hemiGround: '#a39c8c', hemi: 1.7, exposure: 1.0, cloud: '#ffffff', cloudShade: '#a9b9c8'},
  night: {night: 1.0, sunElev: -0.2, sunAz: -2.0, sunColor: '#6d86c9', sunIntensity: 0.35, zenith: '#04070f', horizon: '#1a2438', ground: '#0c111a',
          fog: '#121a2a', fogDensity: 0.0009, hemiSky: '#4a5f9a', hemiGround: '#2a2a32', hemi: 0.8, exposure: 1.25, cloud: '#3a4668', cloudShade: '#0c1220'},
};

const rng = (seed) => () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };

const SKY_VERT = `varying vec3 vDir; void main(){ vDir = normalize(position); vec4 p = modelViewMatrix * vec4(position,1.0); gl_Position = projectionMatrix * p; gl_Position.z = gl_Position.w; }`;
const SKY_FRAG = `
precision highp float;
varying vec3 vDir;
uniform vec3 uZenith, uHorizon, uGround, uSunDir, uSunColor, uCloud, uCloudShade;
uniform float uNight, uTime;
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
float noise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);
  return mix(mix(hash(i),hash(i+vec2(1,0)),f.x), mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x), f.y); }
float fbm(vec2 p){ float v=0.0, a=0.5; for(int i=0;i<5;i++){ v+=a*noise(p); p=p*2.03+vec2(7.1,3.7); a*=0.5; } return v; }
void main(){
  vec3 d = normalize(vDir);
  float up = d.y;
  vec3 col = mix(uHorizon, uZenith, pow(clamp(up, 0.0, 1.0), 0.45));
  col = mix(col, uGround, smoothstep(0.0, -0.25, up));
  float sd = max(dot(d, normalize(uSunDir)), 0.0);
  col += uSunColor * (pow(sd, 900.0) * 14.0 + pow(sd, 40.0) * 0.55 + pow(sd, 6.0) * 0.28);
  // soft clouds on a projected plane; they catch the sun colour near the sun
  if (up > 0.015) {
    vec2 p = d.xz / (up + 0.18) * 1.15 + vec2(uTime * 0.004, uTime * 0.0017);
    float c = smoothstep(0.46, 0.78, fbm(p) * 0.78 + fbm(p * 2.7 + 3.0) * 0.32);
    float lit = pow(sd, 3.0) * 0.8 + 0.25;
    vec3 cc = mix(uCloudShade, uCloud, clamp(lit + fbm(p * 1.6) * 0.3, 0.0, 1.0));
    col = mix(col, cc, c * smoothstep(0.015, 0.16, up) * 0.82);
  }
  // stars
  if (uNight > 0.3 && up > 0.0) {
    vec3 q = floor(d * 330.0);
    float s = step(0.9965, hash(q.xy + q.z * 17.0));
    col += vec3(0.9, 0.95, 1.0) * s * (uNight - 0.3) * smoothstep(0.0, 0.2, up) * (0.5 + 0.5 * hash(q.xy));
  }
  gl_FragColor = vec4(col, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

function streetTextures(maxAniso) {
  const mk = (draw, srgb) => {
    const c = document.createElement('canvas'); c.width = 512; c.height = 1024;
    draw(c.getContext('2d'), c.width, c.height);
    const t = new THREE.CanvasTexture(c); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = maxAniso;
    if (srgb) t.colorSpace = THREE.SRGBColorSpace; return t;
  };
  const sx = 512 * STREET / CELL_X / 2, sz = 1024 * STREET / CELL_Z / 2;   // half street widths in pixels
  const map = mk((g, w, h) => {
    g.fillStyle = '#34363a'; g.fillRect(0, 0, w, h);                            // sidewalk / lots
    g.fillStyle = '#16181c';                                                    // asphalt: cross-shaped streets on the tile border
    g.fillRect(0, 0, w, sz); g.fillRect(0, h - sz, w, sz); g.fillRect(0, 0, sx, h); g.fillRect(w - sx, 0, sx, h);
    g.fillStyle = '#a8975a';                                                    // lane dashes
    for (let y = 8; y < h; y += 40) { g.fillRect(0, y, 2, 20); g.fillRect(w - 2, y, 2, 20); }
    for (let x = 8; x < w; x += 40) { g.fillRect(x, 0, 20, 2); g.fillRect(x, h - 2, 20, 2); }
    g.fillStyle = '#26282c'; g.fillRect(w * .35, h * .25, w * .3, h * .5);      // block interior courtyards
  }, true);
  const emissive = mk((g, w, h) => {
    g.fillStyle = '#000'; g.fillRect(0, 0, w, h);
    g.fillStyle = '#ffb45a';
    for (let y = 10; y < h; y += 52) { g.fillRect(sx + 3, y, 3, 3); g.fillRect(w - sx - 6, y, 3, 3); }
    for (let x = 10; x < w; x += 52) { g.fillRect(x, sz + 3, 3, 3); g.fillRect(x, h - sz - 6, 3, 3); }
    g.fillStyle = '#ff9a3c'; g.fillRect(0, 0, 5, 5);
  }, true);
  return {map, emissive};
}

function buildingMaterial(night, fadeNear, fadeFar) {
  const m = new THREE.MeshStandardMaterial({color: '#ffffff', roughness: 0.62, metalness: 0.12, vertexColors: false});
  m.onBeforeCompile = (shader) => {
    shader.uniforms.uNight = night; shader.uniforms.uFadeNear = {value: fadeNear}; shader.uniforms.uFadeFar = {value: fadeFar};
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWPos; varying vec3 vWNor; varying float vSeed;')
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        mat4 cim = mat4(1.0);
        #ifdef USE_INSTANCING
          cim = instanceMatrix;
        #endif
        vWPos = (modelMatrix * cim * vec4(transformed, 1.0)).xyz;
        vWNor = normalize(mat3(modelMatrix) * mat3(cim) * objectNormal);
        vSeed = fract(sin(dot(vec2(cim[3].x, cim[3].z), vec2(12.9898, 78.233))) * 43758.5453);`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>
        varying vec3 vWPos; varying vec3 vWNor; varying float vSeed;
        uniform float uNight, uFadeNear, uFadeFar;
        float h21(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
        {
          vec3 n = normalize(vWNor);
          float side = 1.0 - step(0.5, abs(n.y));
          vec2 uv = abs(n.x) > abs(n.z) ? vec2(vWPos.z, vWPos.y) : vec2(vWPos.x, vWPos.y);
          vec2 cs = vec2(3.2, 3.7);
          vec2 cell = floor(uv / cs), f = fract(uv / cs);
          float win = step(0.13, f.x) * step(f.x, 0.87) * step(0.18, f.y) * step(f.y, 0.80);
          float hh = h21(cell + vSeed * 91.0);
          float litProb = mix(0.025, 0.58, uNight) * (0.55 + 0.9 * vSeed);
          float lit = step(1.0 - litProb, hh);
          vec3 warm = mix(vec3(1.0, 0.62, 0.30), vec3(0.72, 0.86, 1.0), step(0.72, h21(cell * 1.7 + 3.0)));
          float fade = 1.0 - smoothstep(uFadeNear, uFadeFar, length(vViewPosition));
          float pattern = mix(0.0, win, fade);
          diffuseColor.rgb = mix(diffuseColor.rgb, diffuseColor.rgb * vec3(0.42, 0.55, 0.7) + vec3(0.02, 0.04, 0.07), pattern * side * (0.9 - 0.5 * uNight));
          float glow = mix(litProb * 0.5, win * lit, fade) * side;
          totalEmissiveRadiance += warm * glow * (1.0 + 1.0 * uNight) * (0.25 + 0.75 * uNight);
        }`);
  };
  return m;
}

export function createCity({scene, renderer, group = new THREE.Group()}) {
  const night = {value: 0.18};
  const maxAniso = Math.min(8, renderer.capabilities.getMaxAnisotropy?.() || 1);
  const rand = rng(20261013);
  const city = new THREE.Group(); city.rotation.y = ANGLE; group.add(city);

  // ---- sky dome (renders behind everything, no fog)
  const skyUniforms = {
    uZenith: {value: new THREE.Color()}, uHorizon: {value: new THREE.Color()}, uGround: {value: new THREE.Color()},
    uSunDir: {value: new THREE.Vector3(0, 1, 0)}, uSunColor: {value: new THREE.Color()}, uCloud: {value: new THREE.Color()},
    uCloudShade: {value: new THREE.Color()}, uNight: night, uTime: {value: 0},
  };
  const skyMat = new THREE.ShaderMaterial({uniforms: skyUniforms, vertexShader: SKY_VERT, fragmentShader: SKY_FRAG, side: THREE.BackSide, depthWrite: false, depthTest: false, fog: false});
  const dome = new THREE.Mesh(new THREE.SphereGeometry(2500, 48, 24), skyMat);
  dome.renderOrder = -10; dome.frustumCulled = false; group.add(dome);

  // ---- ground with streets
  const {map, emissive} = streetTextures(maxAniso);
  const repeatX = 8000 / CELL_X, repeatZ = 8000 / CELL_Z;
  map.repeat.set(repeatX, repeatZ); emissive.repeat.set(repeatX, repeatZ);
  const groundMat = new THREE.MeshStandardMaterial({map, emissiveMap: emissive, emissive: '#ffffff', emissiveIntensity: 0.2, roughness: 0.85, metalness: 0.05});
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(8000, 8000), groundMat);
  ground.rotation.x = -Math.PI / 2; ground.position.y = GROUND_Y; city.add(ground);
  // texture tile origin: shift so streets line up with cell borders (tile border = street centre)
  const mod1 = (v) => ((v % 1) + 1) % 1;
  map.offset.set(mod1(-(0.5 + 0.5 * repeatX)), mod1(0.5 - 0.5 * repeatZ)); emissive.offset.copy(map.offset);

  // ---- river + park (cells left empty)
  const riverCol = -6;
  const river = new THREE.Mesh(new THREE.PlaneGeometry(CELL_X * 0.96, 8000), new THREE.MeshStandardMaterial({color: '#0d1c2b', roughness: 0.08, metalness: 0.9}));
  river.rotation.x = -Math.PI / 2; river.position.set(riverCol * CELL_X, GROUND_Y + 0.4, 0); city.add(river);
  const parkCells = new Set(['4,0', '4,1', '5,0', '5,1']);
  const park = new THREE.Mesh(new THREE.PlaneGeometry(CELL_X * 2 - STREET, CELL_Z * 2 - STREET), new THREE.MeshStandardMaterial({color: '#1d3a24', roughness: 1}));
  park.rotation.x = -Math.PI / 2; park.position.set(4.5 * CELL_X - CELL_X / 2 + CELL_X / 2, GROUND_Y + 0.3, 0.5 * CELL_Z); city.add(park);

  // ---- buildings (instanced boxes; towers get setbacks and spires)
  const peaks = [[360, -480, 380, 310], [-640, 280, 300, 260], [60, 900, 360, 210], [820, 360, 320, 230]];
  const heightAt = (x, z) => {
    let h = 28 + rand() * 38;
    for (const [px, pz, sigma, peak] of peaks) h += peak * Math.exp(-((x - px) ** 2 + (z - pz) ** 2) / (2 * sigma * sigma)) * (0.35 + 0.65 * rand());
    return h;
  };
  const boxes = [];   // {x,y,z,w,h,d,tint,landmark}
  const cosA = Math.cos(ANGLE), sinA = Math.sin(ANGLE);
  const toWorld = (x, z) => [x * cosA + z * sinA, -x * sinA + z * cosA];
  const palette = ['#8c8a84', '#a39a8c', '#706d69', '#b3a99a', '#7d6f66', '#5f6a70', '#948d85', '#c2b8a6', '#6b7a84', '#8a6a58'];
  for (let i = -15; i <= 15; i++) {
    for (let j = -10; j <= 10; j++) {
      const cx = i * CELL_X, cz = j * CELL_Z;
      if (i === riverCol || parkCells.has(i + ',' + j)) continue;
      const [wx, wz] = toWorld(cx, cz);
      const dist = Math.hypot(wx, wz);
      if (dist < 105) continue;            // our own tower's footprint
      const lotsX = rand() < 0.45 ? 2 : 1, lotsZ = 2 + Math.floor(rand() * 3);
      const lw = (CELL_X - STREET) / lotsX, ld = (CELL_Z - STREET) / lotsZ;
      for (let a = 0; a < lotsX; a++) for (let b = 0; b < lotsZ; b++) {
        const lx = cx - (CELL_X - STREET) / 2 + lw * (a + 0.5), lz = cz - (CELL_Z - STREET) / 2 + ld * (b + 0.5);
        const [wx2, wz2] = toWorld(lx, lz);
        let h = heightAt(wx2, wz2);
        const d = Math.hypot(wx2, wz2);
        if (d < 260) h = Math.min(h, 70 + rand() * 50);   // keep the foreground low so the view stays open
        const w = lw - 3 - rand() * 6, dd = ld - 3 - rand() * 6, tint = palette[Math.floor(rand() * palette.length)];
        boxes.push({x: lx, z: lz, y0: 0, w, d: dd, h, tint});
        if (h > 110) {   // setback tier
          const h2 = h * (0.18 + rand() * 0.18);
          boxes.push({x: lx, z: lz, y0: h, w: w * 0.68, d: dd * 0.68, h: h2, tint});
          if (h > 190) boxes.push({x: lx, z: lz, y0: h + h2, w: w * 0.3, d: dd * 0.3, h: h2 * 0.9, tint});
        }
        if (rand() < 0.5) boxes.push({x: lx + (rand() - 0.5) * w * 0.4, z: lz + (rand() - 0.5) * dd * 0.4, y0: h, w: w * 0.18, d: dd * 0.12, h: 3 + rand() * 6, tint: '#55595c'});
      }
    }
  }
  // landmarks: a stepped art-deco tower with a spire and a slender glass needle
  const addLandmark = (wx, wz, base, tiers, spire, tint) => {
    const [lx, lz] = [wx * cosA - wz * sinA, wx * sinA + wz * cosA];
    let y = 0;
    for (const [wFrac, hh] of tiers) { boxes.push({x: lx, z: lz, y0: y, w: base * wFrac, d: base * wFrac * 0.9, h: hh, tint}); y += hh; }
    boxes.push({x: lx, z: lz, y0: y, w: 5, d: 5, h: spire, tint: '#c9c4b6'});
    return y + spire;
  };
  const topA = addLandmark(-380, -540, 74, [[1, 120], [0.82, 90], [0.62, 70], [0.42, 40], [0.26, 36]], 62, '#b7ad98');
  const topB = addLandmark(520, -300, 46, [[1, 210], [0.7, 60], [0.38, 90]], 70, '#6f8aa0');
  const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), buildingMaterial(night, 160, 620), boxes.length + 1);
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3(), color = new THREE.Color();
  boxes.forEach((bx, i) => {
    p.set(bx.x, GROUND_Y + bx.y0 + bx.h / 2, bx.z); s.set(bx.w, bx.h, bx.d);
    m4.compose(p, q, s); mesh.setMatrixAt(i, m4); mesh.setColorAt(i, color.set(bx.tint).multiplyScalar(0.8 + rand() * 0.35));
  });
  // our own tower below the penthouse (so looking down shows a facade, not the void)
  p.set(0, GROUND_Y / 2 - 0.3, 0); s.set(40, -GROUND_Y - 0.4, 32); m4.compose(p, new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -ANGLE), s);
  mesh.setMatrixAt(boxes.length, m4); mesh.setColorAt(boxes.length, color.set('#7b7f83'));
  mesh.instanceMatrix.needsUpdate = true; mesh.instanceColor.needsUpdate = true; mesh.frustumCulled = false;
  city.add(mesh);

  // aviation lights on the highest tops + moving traffic
  const redPts = [[topA, -380, -540], [topB, 520, -300]].map(([top, wx, wz]) => { const [lx, lz] = [wx * cosA - wz * sinA, wx * sinA + wz * cosA]; return new THREE.Vector3(lx, GROUND_Y + top + 3, lz); });
  const dot = (() => { const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d'), r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(0.25, 'rgba(255,255,255,.55)'); r.addColorStop(1, 'rgba(255,255,255,0)'); g.fillStyle = r; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c); })();
  const redGeo = new THREE.BufferGeometry().setFromPoints(redPts);
  const redMat = new THREE.PointsMaterial({color: '#ff3020', size: 26, map: dot, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true});
  city.add(new THREE.Points(redGeo, redMat));

  const CARS = 1400, carPos = new Float32Array(CARS * 3), carCol = new Float32Array(CARS * 3), cars = [];
  for (let i = 0; i < CARS; i++) {
    const alongZ = rand() < 0.5, dir = rand() < 0.5 ? 1 : -1;
    const lane = (alongZ ? Math.round((rand() - 0.5) * 30) * CELL_X : Math.round((rand() - 0.5) * 20) * CELL_Z) + (alongZ ? 1 : 1) * dir * (3 + rand() * 4);
    cars.push({alongZ, dir, lane, t: (rand() - 0.5) * 3200, v: 7 + rand() * 9});
    (dir > 0 ? color.set('#fff0cc') : color.set('#ff4030')).toArray(carCol, i * 3);
  }
  const carGeo = new THREE.BufferGeometry(); carGeo.setAttribute('position', new THREE.BufferAttribute(carPos, 3)); carGeo.setAttribute('color', new THREE.BufferAttribute(carCol, 3));
  const carMat = new THREE.PointsMaterial({size: 9, map: dot, vertexColors: true, transparent: true, opacity: 0.9, depthWrite: false, blending: THREE.AdditiveBlending});
  const carPoints = new THREE.Points(carGeo, carMat); carPoints.frustumCulled = false; city.add(carPoints);
  const placeCars = (dt) => {
    for (let i = 0; i < CARS; i++) {
      const c = cars[i]; c.t += c.v * c.dir * dt; if (c.t > 1600) c.t = -1600; else if (c.t < -1600) c.t = 1600;
      carPos[i * 3] = c.alongZ ? c.lane : c.t; carPos[i * 3 + 1] = GROUND_Y + 1.5; carPos[i * 3 + 2] = c.alongZ ? c.t : c.lane;
    }
    carGeo.attributes.position.needsUpdate = true;
  };
  placeCars(0);

  let current = 'golden', lastBlink = 0;
  const sunDir = new THREE.Vector3();
  function apply(name) {
    const P = PRESETS[name] || PRESETS.golden; current = name;
    night.value = P.night;
    sunDir.set(Math.cos(P.sunElev) * Math.cos(P.sunAz), Math.sin(P.sunElev), Math.cos(P.sunElev) * Math.sin(P.sunAz));
    skyUniforms.uZenith.value.set(P.zenith); skyUniforms.uHorizon.value.set(P.horizon); skyUniforms.uGround.value.set(P.ground);
    skyUniforms.uSunDir.value.copy(sunDir); skyUniforms.uSunColor.value.set(P.sunColor);
    skyUniforms.uCloud.value.set(P.cloud); skyUniforms.uCloudShade.value.set(P.cloudShade);
    groundMat.emissiveIntensity = 0.12 + 1.9 * P.night; carMat.opacity = 0.25 + 0.7 * Math.max(P.night, 0.4);
    return {...P, sunDir: sunDir.clone()};
  }
  function update(t) {
    skyUniforms.uTime.value = t / 1000;
    placeCars(Math.min(0.05, (t - lastBlink) / 1000 || 0)); lastBlink = t;
    redMat.opacity = 0.4 + 0.6 * (Math.sin(t / 420) > 0 ? 1 : 0.15);
  }
  function dispose() {
    for (const o of [mesh, ground, river, park, dome, carPoints]) { o.geometry.dispose(); o.material.dispose?.(); }
    map.dispose(); emissive.dispose(); dot.dispose(); redGeo.dispose(); redMat.dispose();
  }
  const initial = apply('golden');
  return {group, dome, apply, update, dispose, night, get preset() { return current; }, initial, sunDir};
}
