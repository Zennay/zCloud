// The Penthouse scene: renderer, lighting and time of day, first-person movement, and the living team.
// Heavy parts live in modules: city.js (sky + 3D skyline), interior.js (furnished floor), people.js (staff model),
// post.js (ambient occlusion + bloom), materials.js (procedural PBR), walk.js (routes and the walking state machine).
import * as THREE from 'three';
import {drawSlide} from './slides.js';
import {createCity, PRESETS} from './city.js';
import {createPost} from './post.js';
import {makeMaterials} from './materials.js';
import {buildInterior} from './interior.js';
import {MAX_SLOTS, seatForSlot, BOARD_SPOT} from './layout.js';
import {buildPerson} from './people.js';
import {createNav, Walker} from './walk.js';

const DEFAULT_STAFF = [
  {id: 'maya', name: 'Maya', title: 'Projectmanager · zCloud', project_id: 'cloud', color: '#f0a878', slot: 0, look: {skin: '#c99679', suit: '#414e50', hair: '#302b28', style: 'long'}, voice: {gender: 'f'}},
  {id: 'noah', name: 'Noah', title: 'Developer · Supa', project_id: 'supa', color: '#78b8d6', slot: 1, look: {skin: '#ae805f', suit: '#344452', hair: '#302b28', style: 'short'}, voice: {gender: 'm'}},
  {id: 'lina', name: 'Lina', title: 'QA · LightUp', project_id: 'lightup', color: '#b6a0e5', slot: 2, look: {skin: '#d6b497', suit: '#796c61', hair: '#aa8650', style: 'long'}, voice: {gender: 'f'}},
];
const QUALITY = {
  high: {pixelRatio: 1.6, shadow: 4096, post: 'high'},
  balanced: {pixelRatio: 1.25, shadow: 2048, post: 'mid'},
  low: {pixelRatio: 1, shadow: 0, post: 'off'},
};
const TIME_ORDER = ['golden', 'day', 'night'];
const TIME_LABEL = {golden: 'golden hour', day: 'daglicht', night: 'nacht'};
const busyStatus = ['claimed', 'running', 'verifying'], waitingStatus = ['queued', 'dispatching', 'dispatched'];

export async function startOffice(ui) {
  const params = new URLSearchParams(globalThis.location?.search || '');
  const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(65, 1, 0.06, 3600);
  const renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: 'high-performance'});
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace; renderer.toneMapping = THREE.ACESFilmicToneMapping;
  ui.mount.append(renderer.domElement);
  const canvas = renderer.domElement; canvas.setAttribute('aria-label', 'Loop door je penthousekantoor'); canvas.tabIndex = 0;
  const keys = new Set(), listeners = [], on = (el, event, fn) => { el.addEventListener(event, fn); listeners.push(() => el.removeEventListener(event, fn)); };
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ---- world
  const city = createCity({scene, renderer}); scene.add(city.group);
  const mats = makeMaterials(renderer);
  const interior = buildInterior({mats}); scene.add(interior.group);
  const post = createPost({renderer, scene, camera});
  const hemi = new THREE.HemisphereLight('#ffffff', '#555555', 1); scene.add(hemi);
  const sun = new THREE.DirectionalLight('#ffffff', 3); sun.castShadow = true; sun.shadow.bias = -0.0002; sun.shadow.normalBias = 0.04; scene.add(sun, sun.target);
  const interiorLights = interior.lights;

  // ---- environment capture: the real city + sky (without the room) becomes the reflection/ambient probe
  const cubeRT = new THREE.WebGLCubeRenderTarget(256, {type: THREE.HalfFloatType, generateMipmaps: true, minFilter: THREE.LinearMipmapLinearFilter});
  const cubeCam = new THREE.CubeCamera(0.5, 3600, cubeRT), pmrem = new THREE.PMREMGenerator(renderer);
  let envTarget = null;
  function captureEnvironment() {
    interior.group.visible = false; scene.environment = null;
    cubeCam.position.set(0, 3, 0); cubeCam.update(renderer, scene);
    interior.group.visible = true;
    const next = pmrem.fromCubemap(cubeRT.texture); envTarget?.dispose(); envTarget = next; scene.environment = next.texture;
  }

  // ---- time of day
  let timeName = '', sunElev = 0;
  function setTime(name) {
    if (!PRESETS[name]) name = 'golden';
    const P = city.apply(name); timeName = name;
    const dir = P.sunDir; sunElev = P.sunElev;
    sun.color.set(P.sunColor); sun.intensity = P.sunIntensity; sun.position.copy(dir).multiplyScalar(70);
    const ext = 9 + 22 * Math.min(1, Math.max(0, Math.sin(P.sunElev) * 3));
    Object.assign(sun.shadow.camera, {left: -28, right: 28, top: ext, bottom: -ext, near: 1, far: 170}); sun.shadow.camera.updateProjectionMatrix();
    hemi.color.set(P.hemiSky); hemi.groundColor.set(P.hemiGround); hemi.intensity = P.hemi * 0.55;
    scene.fog = new THREE.FogExp2(P.fog, P.fogDensity);
    renderer.toneMappingExposure = P.exposure;
    for (const l of interiorLights) l.light.intensity = l.base * (0.45 + 1.15 * P.night);
    interior.group.traverse((o) => { if (o.material === mats.glow) o.material.emissiveIntensity = 1.6 + 2.6 * P.night; });
    scene.environmentIntensity = 0.55 + 0.35 * (1 - P.night);
    post.setNight(P.night);
    captureEnvironment();
    return timeName;
  }
  const cycleTime = () => { const next = TIME_ORDER[(TIME_ORDER.indexOf(timeName) + 1) % TIME_ORDER.length]; setTime(next); return next; };

  // ---- quality ladder
  let qualityName = '', forcedQuality = false;
  function setQuality(name) {
    if (!QUALITY[name]) name = 'balanced';
    const Q = QUALITY[name]; qualityName = name;
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, Q.pixelRatio));
    renderer.shadowMap.enabled = Q.shadow > 0; sun.castShadow = Q.shadow > 0;
    if (Q.shadow && sun.shadow.mapSize.x !== Q.shadow) { sun.shadow.mapSize.set(Q.shadow, Q.shadow); sun.shadow.map?.dispose(); sun.shadow.map = null; }
    post.set(Q.post); resize();
    scene.traverse((o) => { if (o.material) { const list = Array.isArray(o.material) ? o.material : [o.material]; list.forEach((m) => { m.needsUpdate = true; }); } });
    ui.quality?.(name);
    return name;
  }

  // ---- screens
  const rr = (c, x, y, w, h, r) => { c.beginPath(); c.roundRect ? c.roundRect(x, y, w, h, r) : c.rect(x, y, w, h); c.fill(); };
  function drawStaffScreen(slotScreen, spec, working, t = 0) {
    const c = slotScreen.canvas.getContext('2d'), w = 512, h = 288;
    c.fillStyle = spec ? '#0f1e27' : '#0b1217'; c.fillRect(0, 0, w, h);
    if (!spec) { c.fillStyle = '#4d6670'; c.font = '600 22px sans-serif'; c.fillText('VRIJ BUREAU', 28, 120); c.font = '14px sans-serif'; c.fillText('Voeg een expert toe in het kantoor', 28, 148); }
    else {
      c.fillStyle = spec.color || '#d4b789'; c.fillRect(0, 0, 8, h);
      c.fillStyle = '#e9dfcb'; c.font = '600 26px sans-serif'; c.fillText(spec.name, 28, 46);
      c.fillStyle = '#8fa6a6'; c.font = '14px sans-serif'; c.fillText(String(spec.title || '').slice(0, 40), 28, 70);
      c.fillStyle = working ? '#9ed1ad' : '#c9ab79'; c.beginPath(); c.arc(34, 106, 6, 0, 7); c.fill();
      c.fillStyle = '#d4dcd6'; c.font = '16px sans-serif'; c.fillText(working ? 'Aan het werk' : 'Beschikbaar', 50, 112);
      for (let i = 0; i < 9; i++) { c.fillStyle = i % 3 === 0 ? '#c9ab7966' : '#7d9ea055'; c.fillRect(28, 140 + i * 15, 90 + ((i * 53 + spec.slot * 31) % 260), 5); }
    }
    slotScreen.tex.needsUpdate = true;
  }
  function drawGenericScreen(screen, title, sub) {
    const c = screen.canvas.getContext('2d'); c.fillStyle = '#13232e'; c.fillRect(0, 0, 512, 288);
    c.fillStyle = '#c7ac7f'; c.font = '600 20px sans-serif'; c.fillText(title, 28, 44); c.fillStyle = '#8fa6a6'; c.font = '13px sans-serif'; c.fillText(sub, 28, 68);
    c.fillStyle = '#273943'; c.fillRect(28, 96, 110, 170);
    for (let i = 0; i < 8; i++) { c.fillStyle = i % 3 === 0 ? '#bda482' : '#7d9ea0'; c.fillRect(156, 108 + i * 19, 90 + ((i * 71) % 190), 4); }
    screen.tex.needsUpdate = true;
  }
  drawGenericScreen(interior.exec, 'ZCLOUD / JOUW WERKPLEK', 'Klik om je VPS-computer te openen');
  drawGenericScreen(interior.creative, 'CREATIEF BUREAU', 'Klik om de inrichting te kiezen');
  const slotWorking = new Map();

  let opsJobs = [], opsOnline = false, opsControl = null;
  function drawOps() {
    const c = interior.ops.canvas.getContext('2d'), W = 1024, H = 640;
    c.fillStyle = '#0f1e26'; c.fillRect(0, 0, W, H);
    c.fillStyle = '#e8decd'; c.font = '600 40px sans-serif'; c.fillText('The Penthouse · live', 48, 74);
    c.fillStyle = opsOnline ? '#9ed1ad' : '#d3b991'; c.font = '20px sans-serif'; c.fillText(opsOnline ? 'zCloud bereikbaar' : 'zCloud niet bevestigd', 48, 108);
    const list = [...actors.values()].sort((a, b) => a.spec.slot - b.spec.slot);
    list.slice(0, 8).forEach((a, i) => {
      const col = i % 2, row = Math.floor(i / 2), x = 48 + col * 250, y = 140 + row * 74, mine = opsJobs.filter((j) => j.employee === a.spec.id), busy = mine.some((j) => busyStatus.includes(j.status));
      c.fillStyle = '#1b3038'; rr(c, x, y, 236, 62, 10);
      c.fillStyle = busy ? '#9ed1ad' : mine.some((j) => waitingStatus.includes(j.status)) ? '#e1c07e' : '#58717a'; c.beginPath(); c.arc(x + 20, y + 31, 7, 0, 7); c.fill();
      c.fillStyle = '#ede4d5'; c.font = '600 20px sans-serif'; c.fillText(a.spec.name, x + 38, y + 28);
      c.fillStyle = '#9fb3b0'; c.font = '14px sans-serif'; c.fillText(busy ? 'werkt aan een opdracht' : mine.some((j) => waitingStatus.includes(j.status)) ? 'werk staat klaar' : 'beschikbaar', x + 38, y + 49);
    });
    c.fillStyle = '#1b3038'; rr(c, 570, 140, 406, 440, 12);
    c.fillStyle = '#c7ac7f'; c.font = '600 18px sans-serif'; c.fillText('WORKERS & QUEUE', 594, 178);
    const ctl = opsControl;
    if (ctl?.online) {
      const workers = ctl.workers || [], running = workers.filter((w) => w.active && w.state !== 'paused').length, paused = workers.filter((w) => w.state === 'paused').length;
      const lines = [[running + ' actief', '#9ed1ad'], [paused + ' gepauzeerd', '#e1c07e'], [(ctl.queue || []).length + ' in de queue', '#cfd8d4'], [(ctl.pending || 0) + ' voorstellen wachten', ctl.pending ? '#f0a878' : '#8fa6a6']];
      lines.forEach(([text, color], i) => { c.fillStyle = color; c.font = '26px sans-serif'; c.fillText(text, 594, 232 + i * 46); });
      c.fillStyle = '#8fa6a6'; c.font = '15px sans-serif';
      workers.slice(0, 5).forEach((wk, i) => { c.fillText((wk.active ? '● ' : '○ ') + (wk.name || wk.project) + ' · ' + (wk.state || ''), 594, 440 + i * 24); });
    } else { c.fillStyle = '#8fa6a6'; c.font = '16px sans-serif'; c.fillText('Klik om de projecten en workers', 594, 232); c.fillText('te beheren (synchroon met het dashboard).', 594, 256); }
    c.fillStyle = '#4d6670'; c.font = '13px sans-serif'; c.fillText('Klik op dit scherm voor werk & beheer', 48, H - 28);
    interior.ops.tex.needsUpdate = true;
  }
  const setMonitor = (jobs, online) => { opsJobs = jobs || []; opsOnline = !!online; drawOps(); };
  const setControl = (state) => { opsControl = state || null; drawOps(); };
  function setSlide(slide, index, total) { drawSlide(interior.board.canvas.getContext('2d'), slide, index, total); interior.board.tex.needsUpdate = true; }
  setSlide({title: 'A higher perspective.', body: 'Your company. Above the city.'}, 0, 1);

  // ---- navigation grid + collisions
  const nav = createNav(); nav.addMany(interior.colliders);
  const targets = interior.targets;
  const canMove = (x, z) => Math.abs(x) < 18.5 && Math.abs(z) < 14.5 && !interior.colliders.some((c) => Math.abs(x - c.x) < c.hw + 0.28 && Math.abs(z - c.z) < c.hd + 0.28);

  // ---- camera
  let yaw = 0.65, pitch = -0.015, last = performance.now(), drag = null, lastHint = '';
  const rooms = {
    lounge: {p: [3.5, 1.68, 12.6], look: [-3, 1.7, 1], label: 'THE LOUNGE', title: 'A higher perspective.', desc: 'Je team, je projecten, je verdieping.'},
    team: {p: [-1.5, 1.9, -3.9], look: [-2, 1.1, -10.5], label: 'THE WORKFLOOR', title: 'Good things take a team.', desc: 'Je experts aan hun bureau. Het team groeit mee.'},
    office: {p: [-8.3, 1.68, 3.6], look: [-11, 1.45, 6], label: 'EXECUTIVE OFFICE', title: 'Make yourself at home.', desc: 'Je eigen kantoor. Een echte verbinding met je VPS.'},
    boardroom: {p: [11, 1.68, 12.5], look: [11, 2.5, 2], label: 'THE BOARDROOM', title: 'Ideas deserve a stage.', desc: 'Van een eerste slide naar je volgende grote plan.'},
    creative: {p: [11.2, 1.68, 0.4], look: [14.6, 1.1, -2.8], label: 'CREATIEF BUREAU', title: 'Jouw eigen plek.', desc: 'Planten, plushjes of een rustig bureau.'},
  };
  function setLook(p, look) { camera.position.set(...p); const d = new THREE.Vector3(...look).sub(camera.position); yaw = Math.atan2(-d.x, -d.z); pitch = Math.atan2(d.y, Math.hypot(d.x, d.z)); camera.rotation.set(pitch, yaw, 0, 'YXZ'); }

  // ---- staff
  const actors = new Map();
  const flagTex = (() => { const c = document.createElement('canvas'); c.width = c.height = 128; const g = c.getContext('2d'); g.fillStyle = '#d9b26a'; g.beginPath(); g.arc(64, 64, 52, 0, Math.PI * 2); g.fill(); g.fillStyle = '#1b2b31'; g.font = 'bold 84px sans-serif'; g.textAlign = 'center'; g.fillText('!', 64, 92); const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t; })();
  const flagMat = new THREE.SpriteMaterial({map: flagTex, transparent: true, depthTest: false});
  const specKey = (s) => JSON.stringify([s.name, s.title, s.color, s.look, s.voice, s.slot]);
  function removeActor(a) {
    a.walker.settle(false); scene.remove(a.person.root); const i = targets.indexOf(a.person.root); if (i >= 0) targets.splice(i, 1);
    a.person.dispose(); a.flag.material !== flagMat && a.flag.material.dispose(); actors.delete(a.spec.id);
  }
  function addActor(spec) {
    const person = buildPerson(spec), seat = seatForSlot(spec.slot);
    const walker = new Walker({id: spec.id, seat, nav, boardSpot: BOARD_SPOT, aisleZ: -5.5});
    person.root.userData.action = spec.id; targets.push(person.root); scene.add(person.root);
    const flag = new THREE.Sprite(flagMat); flag.position.set(0, 2.55, 0); flag.scale.set(0.42, 0.42, 1); flag.visible = false; person.root.add(flag);
    const a = {spec, key: specKey(spec), person, walker, flag, working: false};
    actors.set(spec.id, a); person.animate({blend: 0}); person.root.position.set(seat.x, 0, seat.z); return a;
  }
  function setStaff(list) {
    const wanted = new Map();
    for (const raw of (list || DEFAULT_STAFF)) {
      if (raw.active === false) continue;
      const spec = {...raw, slot: Math.min(MAX_SLOTS - 1, Math.max(0, raw.slot | 0))};
      if (wanted.size < MAX_SLOTS && !wanted.has(spec.id)) wanted.set(spec.id, spec);
    }
    for (const a of [...actors.values()]) { const next = wanted.get(a.spec.id); if (!next || specKey(next) !== a.key) removeActor(a); }
    for (const [id, spec] of wanted) if (!actors.has(id)) addActor(spec);
    interior.deskSlots.forEach((d) => { const a = [...actors.values()].find((x) => x.spec.slot === d.slot); slotWorking.set(d.slot, null); drawStaffScreen(d.screen, a?.spec || null, !!a?.working); });
    drawOps();
    return [...actors.keys()];
  }
  function setJobs(jobs) {
    for (const a of actors.values()) {
      const busy = jobs.some((j) => j.employee === a.spec.id && busyStatus.includes(j.status));
      if (busy !== a.working) { a.working = busy; const d = interior.deskSlots[a.spec.slot]; if (d) drawStaffScreen(d.screen, a.spec, busy); }
    }
  }

  const playerFront = () => {
    const fx = -Math.sin(yaw), fz = -Math.cos(yaw);
    for (const d of [1.9, 1.5, 2.4, 1.1]) { const x = camera.position.x + fx * d, z = camera.position.z + fz * d; if (canMove(x, z)) return {x, z}; }
    return {x: camera.position.x, z: camera.position.z + 1.2};
  };
  const actorCmd = (id, dest) => { const a = actors.get(id); return a ? a.walker.go(dest) : Promise.resolve(false); };

  function selectActor(id) {
    const a = actors.get(id); if (!a) return;
    const w = a.walker, p = w.mode === 'seated' ? w.seat : w.pos;
    if (w.mode === 'seated') setLook([p.x + (p.x > 0 ? -1.9 : 1.9), 1.62, p.z + 1.9], [p.x, 1.25, p.z]);
    else setLook([p.x + 1.7, 1.6, p.z + 0.4], [p.x, 1.45, p.z]);
    ui.room('JE TEAM', a.spec.name + ' · ' + (a.spec.title || 'expert'), 'Geef werk, stel een vraag en volg het echte resultaat.');
  }
  function go(name) {
    if (name === 'assistant') return selectActor(actors.has('maya') ? 'maya' : [...actors.keys()][0]);
    const r = rooms[name]; if (!r) return;
    setLook(r.p, r.look); keys.clear(); ui.room(r.label, r.title, r.desc);
  }
  go('lounge');

  // ---- interaction
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2();
  function hitAt(x = 0, y = 0) {
    mouse.set(x, y); ray.setFromCamera(mouse, camera);
    for (const h of ray.intersectObjects(targets, true)) {
      if (h.distance > 18) continue;
      let o = h.object; while (o && !o.userData.action) o = o.parent;
      if (o && o.visible !== false) return o.userData.action;
    }
    return '';
  }
  on(canvas, 'pointerdown', (e) => { if (e.button !== 0) return; drag = {x: e.clientX, y: e.clientY, startX: e.clientX, startY: e.clientY, moved: false}; canvas.setPointerCapture(e.pointerId); });
  on(canvas, 'pointermove', (e) => {
    if (document.pointerLockElement === canvas || drag) {
      const dx = document.pointerLockElement === canvas ? e.movementX : e.clientX - drag.x, dy = document.pointerLockElement === canvas ? e.movementY : e.clientY - drag.y;
      yaw -= dx * 0.003; pitch = THREE.MathUtils.clamp(pitch - dy * 0.0025, -1.05, 1.1);
      if (drag) { drag.moved ||= Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) > 5; drag.x = e.clientX; drag.y = e.clientY; }
    }
  });
  on(canvas, 'pointerup', (e) => {
    if (drag && !drag.moved) { const r = canvas.getBoundingClientRect(), id = hitAt((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1); if (id) ui.interact(id); }
    drag = null;
  });
  on(canvas, 'pointercancel', () => { drag = null; });
  on(document, 'pointerlockchange', () => ui.locked(document.pointerLockElement === canvas));
  const editable = () => /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
  on(document, 'keydown', (e) => {
    if (editable() || ui.isUIOpen()) return;
    const key = e.key.toLowerCase();
    if (['w', 'a', 's', 'd', 'arrowleft', 'arrowright', 'arrowup', 'arrowdown', 'shift', 'e'].includes(key)) {
      e.preventDefault(); keys.add(key);
      if (key === 'e' && !e.repeat) { const id = hitAt(); if (id) ui.interact(id); }
    }
  });
  on(document, 'keyup', (e) => keys.delete(e.key.toLowerCase())); on(window, 'blur', () => keys.clear());

  function resize() {
    const w = ui.mount.clientWidth || 1, h = ui.mount.clientHeight || 1;
    camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h, false); post.resize();
  }
  const observer = new ResizeObserver(resize); observer.observe(ui.mount);

  // ---- boot
  setTime(params.get('time') || 'golden');
  forcedQuality = params.has('q');
  setQuality(params.get('q') || (matchMedia('(pointer: coarse)').matches ? 'balanced' : 'high'));
  setStaff(DEFAULT_STAFF); setMonitor([], false);
  resize();

  // adaptive quality: when frames stay slow the scene steps down a level instead of stuttering
  let frames = 0, acc = 0, slowWindows = 0;
  function adapt(dt) {
    if (forcedQuality) return;
    frames++; acc += dt;
    if (frames < 90) return;
    const avg = acc / frames; frames = 0; acc = 0;
    slowWindows = avg > 1 / 26 ? slowWindows + 1 : 0;
    if (slowWindows >= 2) { slowWindows = 0; const next = qualityName === 'high' ? 'balanced' : qualityName === 'balanced' ? 'low' : ''; if (next) setQuality(next); }
  }

  renderer.setAnimationLoop((t) => {
    const raw = Math.max(0, (t - last) / 1000), dt = Math.min(0.035, raw); last = t;
    if (document.hidden) return;
    if (!ui.isUIOpen()) {
      const f = (keys.has('w') || keys.has('arrowup') ? 1 : 0) - (keys.has('s') || keys.has('arrowdown') ? 1 : 0), s = (keys.has('d') ? 1 : 0) - (keys.has('a') ? 1 : 0);
      const norm = Math.hypot(f, s) || 1, pace = (keys.has('shift') ? 5.8 : 3) * dt;
      const dx = (-Math.sin(yaw) * f + Math.cos(yaw) * s) / norm * pace, dz = (-Math.cos(yaw) * f - Math.sin(yaw) * s) / norm * pace;
      if (canMove(camera.position.x + dx, camera.position.z)) camera.position.x += dx;
      if (canMove(camera.position.x, camera.position.z + dz)) camera.position.z += dz;
      if (keys.has('arrowleft')) yaw += dt; if (keys.has('arrowright')) yaw -= dt;
    }
    camera.rotation.set(pitch, yaw, 0, 'YXZ');
    const ctx = {front: playerFront(), look: {x: camera.position.x, z: camera.position.z}};
    for (const a of actors.values()) {
      const f = a.walker.update(dt, ctx);
      a.person.root.position.set(f.x, 0, f.z); a.person.root.rotation.y = f.heading;
      a.person.animate({t, blend: f.blend, moving: f.moving, phase: f.phase, speaking: f.speaking, presenting: f.presenting, working: a.working});
      a.flag.visible = f.flag; if (f.flag) { const k = 0.42 + Math.sin(t * 0.006) * 0.05; a.flag.scale.set(k, k, 1); }
    }
    interior.group.userData.t = t; city.update(t);
    const id = ui.isUIOpen() ? '' : hitAt();
    const hint = id ? (id === 'computer' ? 'E · Gebruik je VPS-computer' : id === 'board' ? 'E · Open presentaties' : id === 'monitor' ? 'E · Werk & workers beheren' : id === 'customize' ? 'E · Bureau inrichten' : actors.has(id) ? 'E · Praat met ' + actors.get(id).spec.name : '') : '';
    if (hint !== lastHint) { ui.hint(hint); lastHint = hint; }
    post.render(dt); adapt(raw);
  });

  return {
    go, setView: (p, look) => setLook(p, look), setSlide, setMonitor, setControl, setJobs, setStaff, setTime, cycleTime, select: selectActor,
    callToPlayer: (id) => actorCmd(id, 'player'), sendHome: (id) => actorCmd(id, 'home'), walkToBoard: (id) => actorCmd(id, 'board'),
    speak: (id, on) => { const a = actors.get(id); if (a) a.walker.speaking = !!on; },
    setAttention: (id, on) => { const a = actors.get(id); if (a) a.walker.attention = !!on; },
    actorMode: (id) => actors.get(id)?.walker.mode || '', actorIds: () => [...actors.keys()],
    customize: interior.customize, clearKeys: () => keys.clear(), key: (k, pressed) => (pressed ? keys.add(k) : keys.delete(k)),
    // debugging/testing aid: advance the walking staff by a number of seconds without rendering
    step: (seconds) => { const ctx = {front: playerFront(), look: {x: camera.position.x, z: camera.position.z}}; for (let t = 0; t < seconds; t += 1 / 30) for (const a of actors.values()) { const f = a.walker.update(1 / 30, ctx); a.person.root.position.set(f.x, 0, f.z); a.person.root.rotation.y = f.heading; a.person.animate({t: t * 1000, blend: f.blend, moving: f.moving, phase: f.phase, speaking: f.speaking, presenting: f.presenting}); } },
    present: () => {}, quality: setQuality, timeLabel: () => TIME_LABEL[timeName], get timeName() { return timeName; }, get qualityName() { return qualityName; },
    toggleDay: () => cycleTime() === 'day', stats: () => ({...renderer.info.render, geometries: renderer.info.memory.geometries, textures: renderer.info.memory.textures, quality: qualityName, time: timeName}),
    lock: () => { try { const p = canvas.requestPointerLock(); p?.catch(() => {}); } catch { /* not supported */ } },
    dispose: () => {
      renderer.setAnimationLoop(null); observer.disconnect(); listeners.forEach((f) => f());
      for (const a of [...actors.values()]) removeActor(a);
      city.dispose(); interior.dispose(); mats.dispose(); post.dispose(); envTarget?.dispose(); cubeRT.dispose(); pmrem.dispose(); flagTex.dispose(); flagMat.dispose(); renderer.dispose();
    },
  };
}
