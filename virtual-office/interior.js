// The furnished penthouse floor: architecture, furniture, lighting fixtures and the screens (desks, ops monitor, boardroom).
// Everything is generated from primitives and procedural textures; there are no downloaded assets.
import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';
import {artCanvas, makeRng} from './materials.js';

import {MAX_SLOTS, DESK_W, DESK_D, SEAT_OFFSET, deskForSlot, seatForSlot, BOARD_SPOT} from './layout.js';
export {MAX_SLOTS, DESK_W, DESK_D, SEAT_OFFSET, deskForSlot, seatForSlot, BOARD_SPOT};
export const ROOM = {halfX: 19, halfZ: 15, height: 4.9};

export function buildInterior({mats}) {
  const group = new THREE.Group(), rand = makeRng(64);
  const colliders = [], targets = [], lights = [], owned = {geo: [], tex: [], mat: []};
  const cache = new Map();
  const geo = (key, make) => { let g = cache.get(key); if (!g) { g = make(); cache.set(key, g); owned.geo.push(g); } return g; };
  const boxG = (w, h, d) => geo('b' + [w, h, d], () => new THREE.BoxGeometry(w, h, d));
  const rboxG = (w, h, d, r) => geo('r' + [w, h, d, r], () => new RoundedBoxGeometry(w, h, d, 3, Math.min(r, w / 2 - 0.001, h / 2 - 0.001, d / 2 - 0.001)));
  const cylG = (rt, rb, h, seg = 28) => geo('c' + [rt, rb, h, seg], () => new THREE.CylinderGeometry(rt, rb, h, seg));
  const sphG = (r, ws = 20, hs = 14) => geo('s' + [r, ws, hs], () => new THREE.SphereGeometry(r, ws, hs));
  const add = (g, m, x, y, z, parent = group, cast = true) => { const o = new THREE.Mesh(g, m); o.position.set(x, y, z); o.castShadow = cast; o.receiveShadow = true; parent.add(o); return o; };
  const B = (x, y, z, w, h, d, m, parent, cast) => add(boxG(w, h, d), m, x, y, z, parent, cast);
  const R = (x, y, z, w, h, d, r, m, parent, cast) => add(rboxG(w, h, d, r), m, x, y, z, parent, cast);
  const C = (x, y, z, r, h, m, parent, cast, rb = r) => add(cylG(r, rb, h), m, x, y, z, parent, cast);
  const S = (x, y, z, r, m, parent, cast = true, sx = 1, sy = 1, sz = 1) => { const o = add(sphG(r), m, x, y, z, parent, cast); o.scale.set(sx, sy, sz); return o; };
  const col = (x, z, w, d) => colliders.push({x, z, hw: w / 2, hd: d / 2});
  const basic = (params) => { const m = new THREE.MeshBasicMaterial(params); owned.mat.push(m); return m; };
  const std = (params) => { const m = new THREE.MeshStandardMaterial(params); owned.mat.push(m); return m; };
  const tex = (canvas, srgb = true) => { const t = new THREE.CanvasTexture(canvas); if (srgb) t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; owned.tex.push(t); return t; };
  const canvas = (w, h, draw) => { const c = document.createElement('canvas'); c.width = w; c.height = h; if (draw) draw(c.getContext('2d'), w, h); return c; };
  const act = (object, id) => { object.userData.action = id; targets.push(object); return object; };

  const lampGlow = std({color: '#ffe8c4', roughness: 0.6, emissive: new THREE.Color('#ffcf94'), emissiveIntensity: 1.3});
  const {marble, plank, stone, walnut, lightWood, ink, brass, black, white, plaster, leather, darkLeather, fabric, green, rug, glass, glow, glowCool} = mats;

  // ------------------------------------------------------------------ shell
  const floor = B(0, -0.14, 0, 38, 0.28, 30, marble, group, false);
  B(-2.2, 0.006, -9.6, 34.6, 0.012, 10.4, plank, group, false);                      // work-floor boards
  for (const x of [-19, 19]) { B(x, 0.12, 0, 0.24, 0.24, 30, stone, group, false); }
  for (const z of [-15, 15]) { B(0, 0.12, z, 38, 0.24, 0.24, stone, group, false); }
  // ceiling: plaster slab, walnut beams, recessed LED coves and downlights
  B(0, 5.15, 0, 38.4, 0.3, 30.4, plaster, group, false);
  for (let x = -16; x <= 16; x += 6.4) B(x, 4.9, 0, 0.2, 0.28, 30, walnut, group, false);
  const ledStrips = [];
  for (let z = -12; z <= 12; z += 6) for (let x = -16; x <= 16; x += 6.4) ledStrips.push([x + 3.2, 4.99, z]);
  const ledMesh = new THREE.InstancedMesh(boxG(5.2, 0.03, 0.06), glow, ledStrips.length), m4 = new THREE.Matrix4();
  ledStrips.forEach((p, i) => { m4.makeTranslation(...p); ledMesh.setMatrixAt(i, m4); }); group.add(ledMesh);
  const spots = [];
  for (let x = -17; x <= 17; x += 3.4) for (let z = -13; z <= 13; z += 3.2) spots.push([x, 4.97, z]);
  const spotMesh = new THREE.InstancedMesh(cylG(0.09, 0.09, 0.03, 16), glowCool, spots.length);
  spots.forEach((p, i) => { m4.makeTranslation(...p); spotMesh.setMatrixAt(i, m4); }); group.add(spotMesh);

  // curtain wall: slim mullions, transom rails, one sheet of glass per side
  for (const z of [-15, 15]) {
    for (let x = -19; x <= 19.01; x += 3.8) B(x, 2.45, z, 0.07, 4.9, 0.12, ink, group, false);
    B(0, 4.86, z, 38, 0.12, 0.16, ink, group, false); B(0, 2.9, z, 38, 0.04, 0.09, ink, group, false);
    B(0, 2.5, z, 38, 4.5, 0.012, glass, group, false);
  }
  for (const x of [-19, 19]) {
    for (let z = -15; z <= 15.01; z += 3) B(x, 2.45, z, 0.12, 4.9, 0.07, ink, group, false);
    B(x, 4.86, 0, 0.16, 0.12, 30, ink, group, false); B(x, 2.9, 0, 0.09, 0.04, 30, ink, group, false);
    B(x, 2.5, 0, 0.012, 4.5, 30, glass, group, false);
  }

  // ------------------------------------------------------------------ small builders
  const labelCache = new Map();
  function label(text, x, y, z, width, ry = 0, color = '#d9c39b', font = '600 54px Georgia') {
    const c = canvas(1024, 180, (g) => { g.fillStyle = color; g.font = font; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text, 512, 92); });
    const m = basic({map: tex(c), transparent: true, depthWrite: false});
    const p = new THREE.Mesh(geo('pl' + width, () => new THREE.PlaneGeometry(width, width * 180 / 1024)), m);
    p.position.set(x, y, z); p.rotation.y = ry; group.add(p); labelCache.set(text, p); return p;
  }
  function frame(x, y, z, w, h, seed, ry = 0) {
    const g = new THREE.Group(); g.position.set(x, y, z); g.rotation.y = ry; group.add(g);
    R(0, 0, 0, w + 0.12, h + 0.12, 0.05, 0.01, ink, g);
    const art = new THREE.Mesh(geo('pa' + w + h, () => new THREE.PlaneGeometry(w, h)), basic({map: tex(artCanvas(seed))}));
    art.position.z = 0.03; g.add(art); return g;
  }
  function pendant(x, z, radius, y = 3.55, intensity = 7, color = '#ffd9a0') {
    const rod = C(x, (y + 5) / 2, z, 0.012, 5 - y, brass, group, false);
    const ring = add(geo('tor' + radius, () => new THREE.TorusGeometry(radius, 0.022, 10, 72)), glow, x, y, z, group, false); ring.rotation.x = Math.PI / 2;
    const light = new THREE.PointLight(color, intensity, 11, 2); light.position.set(x, y - 0.25, z); group.add(light);
    lights.push({light, base: intensity}); return rod;
  }
  const leafGeo = (() => {
    const parts = [], dummy = new THREE.Object3D();
    for (let i = 0; i < 46; i++) {
      const a = i * 2.399, h = 0.25 + rand() * 0.75, r = 0.1 + h * 0.34;
      const g = new THREE.SphereGeometry(1, 10, 8); dummy.position.set(Math.cos(a) * r, 0.5 + h * 1.15, Math.sin(a) * r);
      dummy.rotation.set(0.5 + rand() * 0.6, -a, 0.35); dummy.scale.set(0.2 + rand() * 0.06, 0.024, 0.27 + rand() * 0.1); dummy.updateMatrix();
      g.applyMatrix4(dummy.matrix); parts.push(g);
    }
    const merged = mergeGeometries(parts); parts.forEach((p) => p.dispose()); owned.geo.push(merged); return merged;
  })();
  function plant(x, z, size = 1) {
    const g = new THREE.Group(); g.position.set(x, 0, z); group.add(g);
    add(cylG(0.3 * size, 0.23 * size, 0.55 * size), stone, 0, 0.275 * size, 0, g);
    C(0, 0.72 * size, 0, 0.022 * size, 0.9 * size, walnut, g, false);
    const leaves = new THREE.Mesh(leafGeo, green); leaves.scale.setScalar(size); leaves.castShadow = true; leaves.receiveShadow = true; g.add(leaves);
    col(x, z, 0.62 * size, 0.62 * size); return g;
  }
  function chair(x, z, ry = 0, mat = darkLeather) {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = ry; group.add(g);
    R(0, 0.5, 0, 0.58, 0.1, 0.56, 0.04, mat, g); R(0, 0.98, -0.27, 0.56, 0.74, 0.09, 0.045, mat, g);
    for (const s of [-1, 1]) { B(s * 0.33, 0.72, -0.02, 0.05, 0.035, 0.38, ink, g, false); B(s * 0.33, 0.62, 0.15, 0.03, 0.2, 0.03, ink, g, false); }
    C(0, 0.27, 0, 0.03, 0.42, brass, g, false);
    for (let i = 0; i < 5; i++) {
      const a = i * Math.PI * 2 / 5, leg = B(Math.sin(a) * 0.15, 0.1, Math.cos(a) * 0.15, 0.045, 0.035, 0.32, ink, g, false); leg.rotation.y = a;
      S(Math.sin(a) * 0.3, 0.04, Math.cos(a) * 0.3, 0.035, black, g, false);
    }
    return g;
  }
  function screenCanvas(w, h) { const c = canvas(w, h); const t = tex(c); const m = basic({map: t, toneMapped: false}); return {canvas: c, tex: t, mat: m}; }
  const monitorFrame = (g, w, h, y, z) => {
    R(0, y, z, w + 0.05, h + 0.05, 0.035, 0.012, black, g);
    C(0, 0.82, z + 0.04, 0.09, 0.025, ink, g, false); B(0, 0.92, z + 0.05, 0.04, 0.2, 0.03, ink, g, false);
  };
  function desk(x, z, {w = DESK_W, d = DESK_D, screen = null, action = null} = {}) {
    const g = new THREE.Group(); g.position.set(x, 0, z); group.add(g);
    R(0, 0.75, 0, w, 0.06, d, 0.02, walnut, g);
    for (const s of [-1, 1]) R(s * (w / 2 - 0.1), 0.36, 0, 0.05, 0.72, d - 0.2, 0.01, ink, g);
    B(0, 0.45, d / 2 - 0.08, w - 0.3, 0.55, 0.02, ink, g, false);
    monitorFrame(g, 1.1, 0.64, 1.25, 0.2);
    if (screen) { const p = new THREE.Mesh(geo('scr', () => new THREE.PlaneGeometry(1.06, 0.6)), screen.mat); p.position.set(0, 1.25, 0.2 - 0.02); p.rotation.y = Math.PI; g.add(p); if (action) act(p, action); }
    R(0, 0.79, -0.5, 0.44, 0.02, 0.14, 0.008, black, g, false); R(0.38, 0.79, -0.5, 0.06, 0.02, 0.1, 0.01, ink, g, false);
    C(w / 2 - 0.4, 0.82, 0.05, 0.04, 0.09, white, g, false);
    R(-w / 2 + 0.45, 0.785, -0.1, 0.3, 0.02, 0.4, 0.004, lightWood, g, false);
    col(x, z, w, d); return g;
  }
  function lamp(x, z, ry = 0, height = 1.55) {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = ry; group.add(g);
    C(0, 0.02, 0, 0.18, 0.04, brass, g, false); C(0, height / 2, 0, 0.012, height, brass, g, false);
    add(cylG(0.14, 0.2, 0.3, 24), lampGlow, 0, height + 0.05, 0, g, false);
    const l = new THREE.PointLight('#ffd9a8', 1.6, 7, 2); l.position.set(x, height, z); group.add(l); lights.push({light: l, base: 1.6}); return g;
  }
  function sofa(x, z, ry, len = 3.0, mat = leather) {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = ry; group.add(g);
    R(0, 0.3, 0, len, 0.3, 1.0, 0.07, mat, g); R(0, 0.68, -0.4, len, 0.55, 0.22, 0.09, mat, g);
    for (const s of [-1, 1]) R(s * (len / 2 - 0.1), 0.52, 0, 0.2, 0.52, 1.0, 0.08, mat, g);
    const n = Math.max(2, Math.round((len - 0.4) / 0.85)), cw = (len - 0.4) / n;
    for (let i = 0; i < n; i++) { const cx = -((len - 0.4) / 2) + cw * (i + 0.5); R(cx, 0.52, 0.06, cw - 0.03, 0.17, 0.8, 0.07, mat, g); R(cx, 0.78, -0.22, cw - 0.06, 0.4, 0.17, 0.08, mat, g).rotation.x = -0.12; }
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) C(sx * (len / 2 - 0.15), 0.07, sz * 0.4, 0.025, 0.14, brass, g, false);
    R(len * 0.28, 0.7, 0.18, 0.4, 0.36, 0.12, 0.05, fabric, g).rotation.set(-0.3, 0.3, 0.1);
    return g;
  }
  function armchair(x, z, ry, mat = darkLeather) {
    const g = new THREE.Group(); g.position.set(x, 0, z); g.rotation.y = ry; group.add(g);
    R(0, 0.3, 0, 0.95, 0.3, 0.9, 0.08, mat, g); R(0, 0.66, -0.38, 0.95, 0.55, 0.2, 0.09, mat, g);
    for (const s of [-1, 1]) R(s * 0.4, 0.5, 0, 0.16, 0.45, 0.9, 0.07, mat, g);
    R(0, 0.5, 0.05, 0.62, 0.16, 0.72, 0.07, mat, g);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) C(sx * 0.38, 0.06, sz * 0.36, 0.02, 0.12, brass, g, false);
    col(x, z, 1.1, 1.1); return g;
  }

  // ------------------------------------------------------------------ core wall, partitions, signage
  R(-0.5, 2.3, 1.2, 5.4, 4.6, 0.3, 0.02, walnut); col(-0.5, 1.2, 5.4, 0.3);
  const slats = new THREE.InstancedMesh(boxG(0.035, 4.6, 0.04), brass, 34);
  for (let i = 0; i < 34; i++) { m4.makeTranslation(-3.1 + i * 0.16, 2.3, 1.39); slats.setMatrixAt(i, m4); } group.add(slats);
  label('THE PENTHOUSE', -0.5, 3.0, 1.43, 3.6);
  frame(-2.35, 1.9, 1.4, 0.9, 1.15, 1, 0); frame(1.35, 1.9, 1.4, 0.9, 1.15, 2, 0);
  const monitorFrameWall = R(-0.5, 2.3, 1.02, 3.5, 2.2, 0.06, 0.02, black);
  const ops = screenCanvas(1024, 640);
  const opsPlane = new THREE.Mesh(geo('ops', () => new THREE.PlaneGeometry(3.3, 2.06)), ops.mat); opsPlane.position.set(-0.5, 2.3, 0.985); opsPlane.rotation.y = Math.PI; group.add(opsPlane);
  act(opsPlane, 'monitor'); act(monitorFrameWall, 'monitor');
  label('WORKFLOOR', 8.6, 3.55, 1.04, 2.4, Math.PI, '#d9c39b', '600 50px Georgia');
  label('EXECUTIVE OFFICE', -12.5, 3.55, 14.78, 5, Math.PI);
  for (const x of [-5, 5]) {
    for (const seg of [{z: 6, w: 6}, {z: 13, w: 4}]) { B(x, 2.1, seg.z, 0.018, 4.2, seg.w, glass, group, false); B(x, 4.2, seg.z, 0.07, 0.07, seg.w, ink, group, false); B(x, 1.05, seg.z, 0.022, 0.04, seg.w, brass, group, false); col(x, seg.z, 0.2, seg.w); }
    for (const z of [3, 9, 11, 15]) B(x, 2.1, z, 0.05, 4.2, 0.06, ink, group, false);
    B(x, 4.2, 10, 0.08, 0.08, 2, brass, group, false); B(x, 0.02, 10, 0.32, 0.025, 2, brass, group, false);
  }

  // ------------------------------------------------------------------ work floor: eight desks, the team sits at them
  const deskSlots = [];
  for (let slot = 0; slot < MAX_SLOTS; slot++) {
    const p = deskForSlot(slot), screen = screenCanvas(512, 288);
    desk(p.x, p.z, {screen}); chair(p.x, p.z + SEAT_OFFSET - 0.02, 0);
    deskSlots.push({slot, x: p.x, z: p.z, screen});
  }
  B(-2.2, 0.03, -3.4, 34, 0.04, 0.05, brass, group, false);                         // brass threshold between marble and boards
  for (const x of [-17.2, 17.2]) plant(x, -13.8, 1.4);
  plant(-17.2, -5.2, 1.1); plant(17.2, -5.2, 1.1);

  // ------------------------------------------------------------------ lounge
  const loungeRug = new THREE.Mesh(geo('lrug', () => new THREE.PlaneGeometry(7.6, 5.2)), rug); loungeRug.rotation.x = -Math.PI / 2; loungeRug.position.set(-0.3, 0.012, 8); loungeRug.receiveShadow = true; group.add(loungeRug);
  sofa(-2.0, 10.4, Math.PI, 3.2); col(-2.0, 10.4, 3.2, 1.1);
  sofa(2.9, 7.6, -Math.PI / 2, 2.6); col(2.9, 7.6, 1.1, 2.6);
  armchair(-3.6, 6.0, Math.PI / 2 * 0.65); armchair(0.8, 5.5, -0.15);
  add(cylG(1.0, 1.0, 0.045, 48), stone, -0.4, 0.4, 7.9); add(cylG(0.4, 0.5, 0.38, 32), brass, -0.4, 0.2, 7.9); col(-0.4, 7.9, 2.2, 2.2);
  R(-0.75, 0.46, 7.8, 0.34, 0.05, 0.46, 0.01, walnut, group, false); R(-0.75, 0.5, 7.8, 0.3, 0.035, 0.4, 0.01, white, group, false);
  C(0.1, 0.56, 8.2, 0.06, 0.2, white, group, false);
  for (let i = 0; i < 5; i++) { const s = B(0.1 + (i - 2) * 0.03, 0.88 + i * 0.07, 8.2 + (i - 2) * 0.02, 0.008, 0.5 + i * 0.05, 0.008, walnut, group, false); s.rotation.set((i - 2) * 0.15, 0, (i - 2) * 0.12); }
  pendant(-0.4, 7.9, 1.1, 3.4, 5);
  lamp(4.4, 4.4, 0, 1.7);
  C(3.7, 1.2, 1.9, 0.3, 2.4, stone, group, true, 0.34); col(3.7, 1.9, 0.7, 0.7);
  const knot = new THREE.Mesh(geo('knot', () => new THREE.TorusKnotGeometry(0.31, 0.07, 120, 12)), brass); knot.position.set(3.7, 2.72, 1.9); knot.castShadow = true; group.add(knot);
  plant(-4.2, 13.8, 1.5); plant(4.2, 13.8, 1.5); plant(-4.2, 2.6, 1.1);
  // reception credenza behind the lounge sofa
  R(-1.0, 0.45, 14.5, 4.4, 0.9, 0.5, 0.02, walnut); col(-1.0, 14.5, 4.4, 0.5); B(-1.0, 0.92, 14.5, 4.5, 0.03, 0.55, brass, group, false);
  frame(-1.0, 2.3, 14.88, 1.5, 1.8, 3, Math.PI);

  // ------------------------------------------------------------------ executive office (the owner's workplace)
  const execScreen = screenCanvas(512, 288);
  desk(-11, 6, {w: 3.8, d: 1.6, screen: execScreen, action: 'computer'}); chair(-11, 4.78, 0, leather);
  const execRug = new THREE.Mesh(geo('erug', () => new THREE.PlaneGeometry(7, 5)), rug); execRug.rotation.x = -Math.PI / 2; execRug.position.set(-11.5, 0.012, 8.4); execRug.receiveShadow = true; group.add(execRug);
  R(-11.5, 0.7, 14.45, 8, 1.4, 0.6, 0.02, walnut); col(-11.5, 14.45, 8, 0.6);
  for (let k = 0; k <= 4; k++) { B(-15.5 + k * 2, 0.7, 14.14, 0.015, 1.3, 0.01, black, group, false); if (k < 4) for (const s of [-0.12, 0.12]) B(-14.5 + k * 2 + s, 0.8, 14.13, 0.02, 0.22, 0.02, brass, group, false); }
  const books = new THREE.InstancedMesh(boxG(1, 1, 1), std({color: '#ffffff', roughness: 0.7}), 18);
  for (let i = 0; i < 18; i++) { const h = 0.26 + rand() * 0.16; m4.compose(new THREE.Vector3(-15.1 + i * 0.1, 1.46 + h / 2, 14.4), new THREE.Quaternion(), new THREE.Vector3(0.07, h, 0.26)); books.setMatrixAt(i, m4); books.setColorAt(i, new THREE.Color().setHSL(rand(), 0.25 + rand() * 0.2, 0.28 + rand() * 0.3)); }
  group.add(books);
  for (let x = -9.9; x < -7; x += 1.2) R(x, 1.46 + 0.03, 14.35, 0.7, 0.06, 0.38, 0.02, white, group, false);
  armchair(-12.7, 9.4, Math.PI + 0.25, leather); armchair(-9.4, 9.4, Math.PI - 0.25, leather);
  frame(-16.2, 2.0, 11.0, 1.2, 1.5, 4, Math.PI / 2); lamp(-17.0, 6.6, 0); plant(-17, 13.3, 1.5);
  pendant(-11, 6, 0.9, 3.4, 4.5);

  // ------------------------------------------------------------------ boardroom
  R(11, 0.76, 7, 4.9, 0.12, 6.5, 0.04, walnut); col(11, 7, 4.9, 6.5);
  R(11, 0.39, 7, 2.6, 0.7, 4.4, 0.03, ink, group, false);
  for (const z of [4.9, 7.0, 9.1]) { chair(7.95, z, -Math.PI / 2); chair(14.05, z, Math.PI / 2); }
  chair(11, 10.7, Math.PI);
  for (const z of [5, 7, 9]) { R(11, 0.835, z, 0.28, 0.012, 0.4, 0.004, white, group, false); C(11.65, 0.9, z, 0.06, 0.16, white, group, false); }
  const board = screenCanvas(1536, 864), boardPlane = new THREE.Mesh(geo('board', () => new THREE.PlaneGeometry(6.6, 3.71)), board.mat);
  R(11, 2.6, 1.9, 6.9, 4.04, 0.2, 0.03, black); boardPlane.position.set(11, 2.6, 2.012); group.add(boardPlane); act(boardPlane, 'board'); col(11, 1.9, 6.9, 0.3);
  B(11, 0.45, 1.9, 6.9, 0.9, 0.5, walnut, group, false);
  pendant(11, 7, 1.5, 3.5, 5.5);
  frame(18.8, 2.4, 11.0, 1.4, 1.7, 5, -Math.PI / 2); plant(17.4, 14, 1.5); plant(6.2, 13.8, 1.3);

  // ------------------------------------------------------------------ north strip: library walls and the creative corner
  for (const x of [-17.7, 17.7]) {
    R(x, 1.35, -1, 0.9, 2.7, 4.0, 0.02, walnut); col(x, -1, 0.9, 4);
    for (let i = 0; i < 3; i++) B(x + (x < 0 ? 0.04 : -0.04), 0.75 + i * 0.75, -1, 0.85, 0.035, 3.9, brass, group, false);
    const shelf = new THREE.InstancedMesh(boxG(1, 1, 1), std({color: '#ffffff', roughness: 0.7}), 54);
    for (let r = 0; r < 3; r++) for (let i = 0; i < 18; i++) {
      const h = 0.3 + rand() * 0.18; m4.compose(new THREE.Vector3(x + (x < 0 ? 0.1 : -0.1), 0.775 + r * 0.75 + h / 2, -2.85 + i * 0.21), new THREE.Quaternion(), new THREE.Vector3(0.24, h, 0.12 + rand() * 0.05));
      shelf.setMatrixAt(r * 18 + i, m4); shelf.setColorAt(r * 18 + i, new THREE.Color().setHSL(rand(), 0.2 + rand() * 0.3, 0.25 + rand() * 0.35));
    }
    group.add(shelf);
  }
  const creativeScreen = screenCanvas(512, 288);
  desk(14.6, -2.6, {w: 2.8, d: 1.3, screen: creativeScreen, action: 'customize'}); chair(14.6, -3.78, 0, fabric);
  const decor = {plush: new THREE.Group(), garden: new THREE.Group()};
  group.add(decor.plush, decor.garden);
  const plushMats = [std({color: '#f1d3c0', roughness: 0.96}), std({color: '#c5b3df', roughness: 0.96})];
  [[13.5, -2.35, 0], [15.6, -2.4, 1]].forEach(([px, pz, i]) => {
    const g = new THREE.Group(); g.position.set(px, 0.78, pz); decor.plush.add(g);
    S(0, 0.12, 0, 0.17, plushMats[i], g, true, 1, 1.05, 0.9); for (const s of [-1, 1]) { S(s * 0.1, 0.3, 0, 0.065, plushMats[i], g, true, 1, 1.3, 0.8); S(s * 0.06, 0.15, 0.14, 0.018, black, g, false); }
    S(0, 0.09, 0.15, 0.03, std({color: '#d58f8b', roughness: 0.9}), g, false);
  });
  for (const [px, pz, s] of [[12.2, -2.2, 0.5], [17.0, -2.0, 0.55], [16.6, -4.2, 0.9]]) { const p = plant(px, pz, s); decor.garden.add(p); }
  decor.garden.visible = false;
  lamp(17.3, -1.0, 0, 1.35);

  // wall art along the glass at the work floor is replaced by city views: only the two end walls get art
  frame(-18.8, 2.4, -9.0, 1.4, 1.7, 6, Math.PI / 2);

  // ------------------------------------------------------------------ API
  function customize(style) {
    decor.plush.visible = style !== 'minimal'; decor.garden.visible = style === 'garden';
    plushMats[0].color.set(style === 'garden' ? '#bbc9a1' : '#f1d3c0'); plushMats[1].color.set(style === 'garden' ? '#98b48a' : '#c5b3df');
  }
  function dispose() {
    owned.geo.forEach((g) => g.dispose()); owned.tex.forEach((t) => t.dispose()); owned.mat.forEach((m) => m.dispose());
    group.traverse((o) => { if (o.isInstancedMesh) o.dispose(); });
  }
  return {group, colliders, targets, lights, deskSlots, ops, exec: execScreen, creative: creativeScreen, board, boardPlane, customize, dispose, canvasTexture: tex, screenCanvas};
}
