// Smoke test: load scene.js with a minimal fake THREE + DOM and drive the walking staff through a full visit.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, mkdtempSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

function deep() {
  const f = () => d;
  const d = new Proxy(f, {get: (t, k) => (k === Symbol.toPrimitive ? () => 0 : k === 'then' ? undefined : k in t ? t[k] : (t[k] = deep())), set: (t, k, v) => { t[k] = v; return true; }, apply: () => deep()});
  return d;
}

function fakeThree() {
  const proxyClass = (extra = {}) => class {
    constructor(...a) { this.args = a; this.position = {x: 0, y: 0, z: 0, set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; }, clone() { return {...this}; }};
      this.rotation = {x: 0, y: 0, z: 0, set() {}}; this.scale = {x: 1, y: 1, z: 1, set() {}, multiplyScalar() { return this; }};
      this.children = []; this.userData = {}; this.visible = true; this.quaternion = {setFromUnitVectors() {}};
      this.material = deep(); this.geometry = deep(); this.color = {set() {}}; this.intensity = 1; Object.assign(this, extra); return new Proxy(this, {get: (t, k) => (k in t ? t[k] : typeof k === 'symbol' || k === 'then' ? undefined : (t[k] = deep())), set: (t, k, v) => { t[k] = v; return true; }}); }
    add(o) { this.children.push(o); o.parent = this; } traverse() {} dispose() {} set() { return this; }
  };
  const V3 = class { constructor(x = 0, y = 0, z = 0) { Object.assign(this, {x, y, z}); } set(x, y, z) { Object.assign(this, {x, y, z}); return this; } clone() { return new V3(this.x, this.y, this.z); }
    add() { return this; } sub() { return this; } multiplyScalar() { return this; } normalize() { return this; } length() { return 1; } copy() { return this; } };
  return new Proxy({Vector3: V3, MathUtils: {clamp: (v, a, b) => Math.min(b, Math.max(a, v))}, SRGBColorSpace: 'srgb', PCFSoftShadowMap: 1, ACESFilmicToneMapping: 1,
    WebGLRenderer: class { constructor() { this.domElement = {setAttribute() {}, addEventListener() {}, removeEventListener() {}, getBoundingClientRect: () => ({left: 0, top: 0, width: 1, height: 1}), setPointerCapture() {}};
      this.shadowMap = {}; this.capabilities = {getMaxAnisotropy: () => 4}; } setPixelRatio() {} setSize() {} setAnimationLoop(fn) { globalThis.__loop = fn; } render() {} dispose() {} },
    Raycaster: class { setFromCamera() {} intersectObjects() { return []; } }, TextureLoader: class { load(u, cb) { const t = {colorSpace: '', repeat: {set() {}}, dispose() {}}; cb && cb(t); return t; } }, Vector2: class { set() {} }, CanvasTexture: class { constructor() { this.colorSpace = ''; this.repeat = {set() {}}; this.offset = {set() {}}; } dispose() {} } },
    {get: (t, k) => (k in t ? t[k] : proxyClass())});
}

test('a finished worker stands up, walks to the player, waits, walks home and sits down', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'scene-'));
  let src = readFileSync(join(here, '..', 'scene.js'), 'utf8');
  src = src.replace(/^import \* as THREE from .*$/m, "const THREE = globalThis.__THREE;").replace("import {drawSlide} from './slides.js';", "import {drawSlide} from '" + pathToFileURL(join(here, '..', 'slides.js')).href + "';");
  writeFileSync(join(dir, 'scene.mjs'), src);
  const noop = () => {};
  const el = () => ({getContext: () => new Proxy({}, {get: (t, k) => (k === 'measureText' ? () => ({width: 10}) : k === 'createLinearGradient' || k === 'createRadialGradient' ? () => ({addColorStop: noop}) : noop)}), width: 0, height: 0, style: {}, append: noop, addEventListener: noop, removeEventListener: noop, setAttribute: noop});
  Object.assign(globalThis, {__THREE: fakeThree(), devicePixelRatio: 1, performance: {now: () => 0},
    matchMedia: () => ({matches: true}), ResizeObserver: class { observe() {} disconnect() {} },
    document: {createElement: el, addEventListener: noop, removeEventListener: noop, hidden: false, activeElement: null, pointerLockElement: null},
    window: {addEventListener: noop, removeEventListener: noop}});
  const {startOffice} = await import(pathToFileURL(join(dir, 'scene.mjs')).href);
  const mount = {append: noop, clientWidth: 100, clientHeight: 100};
  const world = globalThis.__world = await startOffice({mount, interact: noop, room: noop, hint: noop, locked: noop, isUIOpen: () => false});
  assert.deepEqual(world.actorIds().sort(), ['lina', 'maya', 'noah']);
  assert.equal(world.actorMode('maya'), 'seated');
  assert.equal(await world.sendHome('maya'), true, 'already seated: nothing to do');

  const frame = n => { for (let i = 0; i < n; i++) globalThis.__loop(1000 + (globalThis.__t = (globalThis.__t || 0) + 33)); };
  const arrived = world.callToPlayer('maya');
  assert.equal(world.actorMode('maya'), 'walking');
  frame(40);
  assert.equal(world.actorMode('maya'), 'walking', 'still on the way after ~1s');
  frame(500);
  assert.equal(await arrived, true);
  assert.equal(world.actorMode('maya'), 'waiting');
  world.setAttention('maya', true); world.speak('maya', true); frame(5); world.speak('maya', false);
  const home = world.sendHome('maya');
  frame(900);
  assert.equal(await home, true);
  assert.equal(world.actorMode('maya'), 'seated');

  const board = world.walkToBoard('noah'); frame(900);
  assert.equal(await board, true);
  assert.equal(world.actorMode('noah'), 'presenting');
  world.setSlide({title: 'x', body: 'y'}, 0, 1);
});

test('a new walk order cancels the previous promise instead of leaving it hanging', async () => {
  const world = globalThis.__world;
  const first = world.callToPlayer('lina');
  const second = world.walkToBoard('lina');
  assert.equal(await first, false);
  for (let i = 0; i < 900; i++) globalThis.__loop(1000 + (globalThis.__t = (globalThis.__t || 0) + 33));
  assert.equal(await second, true);
  assert.equal(world.actorMode('lina'), 'presenting');
  assert.equal(world.walkToBoard('nobody') instanceof Promise, true);
  assert.equal(await world.walkToBoard('nobody'), false);
});
