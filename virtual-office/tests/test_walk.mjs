// Walking logic of the staff: routes around furniture and the full stand-up -> walk -> wait -> walk home -> sit-down cycle.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createNav, Walker, turnTo} from '../walk.js';
import {deskForSlot, seatForSlot, MAX_SLOTS, DESK_W, DESK_D, BOARD_SPOT} from '../layout.js';

const obstacles = () => {
  const list = [{x: -0.5, z: 1.2, hw: 2.7, hd: 0.15}, {x: 11, z: 7, hw: 2.45, hd: 3.25}, {x: -0.4, z: 7.9, hw: 1.1, hd: 1.1}, {x: -2, z: 10.4, hw: 1.6, hd: 0.55}, {x: 2.9, z: 7.6, hw: 0.55, hd: 1.3},
    {x: -5, z: 6, hw: 0.1, hd: 3}, {x: -5, z: 13, hw: 0.1, hd: 2}, {x: 5, z: 6, hw: 0.1, hd: 3}, {x: 5, z: 13, hw: 0.1, hd: 2}, {x: 11, z: 1.9, hw: 3.45, hd: 0.15}];
  for (let s = 0; s < MAX_SLOTS; s++) { const d = deskForSlot(s); list.push({x: d.x, z: d.z, hw: DESK_W / 2, hd: DESK_D / 2}); }
  return list;
};
const makeNav = () => { const nav = createNav(); nav.addMany(obstacles()); return nav; };
const run = (w, seconds, ctx = {front: {x: 0, z: 3.5}, look: {x: 0, z: 6}}) => { for (let t = 0; t < seconds; t += 1 / 30) w.update(1 / 30, ctx); };

test('routes go around the core wall and never cut through furniture', () => {
  const nav = makeNav();
  const route = nav.path({x: 0, z: -5.5}, {x: 0, z: 3.5});
  assert.ok(route.length >= 2, 'a straight line would run into the core wall');
  let from = {x: 0, z: -5.5};
  for (const p of route) { assert.ok(nav.clear(from, p), 'segment ' + JSON.stringify(from) + ' -> ' + JSON.stringify(p) + ' is blocked'); from = p; }
  assert.deepEqual(route.at(-1), {x: 0, z: 3.5});
  assert.deepEqual(nav.path({x: -8, z: -5}, {x: -8, z: -3}), [{x: -8, z: -3}], 'open floor stays a straight line');
});

test('a goal inside furniture is moved to the nearest free spot instead of failing', () => {
  const nav = makeNav();
  const route = nav.path({x: 0, z: -5.5}, {x: -0.5, z: 1.2});
  assert.equal(route.length >= 1, true);
  assert.equal(nav.blocked(route.at(-1).x, route.at(-1).z), false);
});

test('boardroom is reachable through the doorway of the glass partition', () => {
  const nav = makeNav();
  const route = nav.path({x: 0, z: 12}, BOARD_SPOT);
  let from = {x: 0, z: 12};
  for (const p of route) { assert.ok(nav.clear(from, p)); from = p; }
});

test('every desk slot has an aisle to the lounge', () => {
  const nav = makeNav();
  for (let s = 0; s < MAX_SLOTS; s++) {
    const seat = seatForSlot(s), side = seat.x + (seat.x > 0 ? 2.5 : -2.5), aisle = {x: side, z: -5.5};
    assert.equal(nav.blocked(aisle.x, aisle.z), false, 'aisle point of slot ' + s);
    assert.ok(nav.clear({x: side, z: seat.z}, aisle), 'side corridor of slot ' + s);
  }
});

test('turnTo takes the short way round', () => {
  assert.ok(Math.abs(turnTo(3, -3, 1) - (-3 + 2 * Math.PI)) < 1e-9 || Math.abs(turnTo(3, -3, 1) + 3) < 1e-9);
  assert.ok(turnTo(0, 1, 0.5) > 0 && turnTo(0, 1, 0.5) < 1);
});

test('employee stands up, walks to the player, waits, walks home and sits down', async () => {
  const w = new Walker({id: 'maya', seat: seatForSlot(0), nav: makeNav()});
  assert.equal(w.mode, 'seated'); assert.equal(w.blend, 0);
  assert.equal(await w.go('home'), true, 'already seated');

  const arrived = w.go('player');
  assert.equal(w.mode, 'walking');
  run(w, 0.3); assert.ok(w.blend > 0 && w.blend < 1, 'still standing up');
  run(w, 1); assert.equal(w.blend, 1);
  run(w, 3); assert.equal(w.mode, 'walking', 'on the way');
  assert.ok(w.moving === 1);
  run(w, 25);
  assert.equal(await arrived, true); assert.equal(w.mode, 'waiting');
  assert.ok(Math.hypot(w.pos.x - 0, w.pos.z - 3.5) < 0.5, 'stands in front of the player');

  w.attention = true; w.speaking = true;
  const home = w.go('home');
  assert.equal(w.attention, false);
  run(w, 40);
  assert.equal(await home, true); assert.equal(w.mode, 'seated'); assert.equal(w.blend, 0);
  assert.deepEqual({x: w.pos.x, z: w.pos.z}, seatForSlot(0));
});

test('a new order cancels the previous promise and the walker follows a moving player', async () => {
  const w = new Walker({id: 'noah', seat: seatForSlot(1), nav: makeNav()});
  const first = w.go('player');
  const second = w.go('board');
  assert.equal(await first, false);
  run(w, 40);
  assert.equal(await second, true); assert.equal(w.mode, 'presenting');
  assert.ok(Math.hypot(w.pos.x - BOARD_SPOT.x, w.pos.z - BOARD_SPOT.z) < 0.3);

  const follow = w.go('player');
  run(w, 3, {front: {x: -8, z: 3}, look: {x: -8, z: 5}});
  run(w, 20, {front: {x: -8, z: -3}, look: {x: -8, z: -1}});
  assert.equal(await follow, true);
  assert.ok(Math.hypot(w.pos.x + 8, w.pos.z + 3) < 0.5, 'follows the player to the new spot');
  assert.equal(await w.go('nobody'), false);
});

test('back-row employees walk out through their own side corridor', async () => {
  const slot = 5, w = new Walker({id: 'ravi', seat: seatForSlot(slot), nav: makeNav()});
  const arrived = w.go('player');
  const seat = seatForSlot(slot), nav = makeNav();
  for (let t = 0; t < 40; t += 1 / 30) {
    w.update(1 / 30, {front: {x: -3, z: 9}, look: {x: -3, z: 11}});
    if (w.stage === 'walk' && Math.abs(w.pos.x - seat.x) > 2.2) assert.equal(nav.blocked(w.pos.x, w.pos.z), false, 'never inside furniture at ' + JSON.stringify(w.pos));
  }
  assert.equal(await arrived, true);
  assert.ok(seat.z < -12);
});
