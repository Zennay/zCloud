// Walking logic for the staff, free of three.js so it can be unit-tested: obstacle grid, A* routes and the
// stand-up -> walk -> wait/present -> walk back -> sit-down state machine of one employee.

/** Obstacle grid over the office floor. Obstacles are axis-aligned rectangles {x,z,hw,hd} (half extents, metres). */
export function createNav({halfX = 18.6, halfZ = 14.6, cell = 0.25, radius = 0.3} = {}) {
  const nx = Math.ceil((halfX * 2) / cell), nz = Math.ceil((halfZ * 2) / cell);
  const grid = new Uint8Array(nx * nz);
  const obstacles = [];
  const ix = (x) => Math.floor((x + halfX) / cell), iz = (z) => Math.floor((z + halfZ) / cell);
  const cx = (i) => -halfX + (i + 0.5) * cell, cz = (j) => -halfZ + (j + 0.5) * cell;
  const inside = (i, j) => i >= 0 && j >= 0 && i < nx && j < nz;

  function rebuild() {
    grid.fill(0);
    for (const o of obstacles) {
      const i0 = Math.max(0, ix(o.x - o.hw - radius)), i1 = Math.min(nx - 1, ix(o.x + o.hw + radius));
      const j0 = Math.max(0, iz(o.z - o.hd - radius)), j1 = Math.min(nz - 1, iz(o.z + o.hd + radius));
      for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) grid[j * nx + i] = 1;
    }
  }
  const blockedCell = (i, j) => !inside(i, j) || grid[j * nx + i] === 1;
  const blocked = (x, z) => blockedCell(ix(x), iz(z));

  /** Is the straight segment free of obstacles? */
  function clear(a, b) {
    const dist = Math.hypot(b.x - a.x, b.z - a.z), steps = Math.max(1, Math.ceil(dist / (cell * 0.5)));
    for (let s = 1; s <= steps; s++) {
      const t = s / steps;
      if (blocked(a.x + (b.x - a.x) * t, a.z + (b.z - a.z) * t)) return false;
    }
    return true;
  }

  function nearestFree(x, z) {
    const i0 = ix(x), j0 = iz(z);
    if (!blockedCell(i0, j0)) return {x, z};
    for (let r = 1; r < 14; r++) {
      let best = null, bestD = Infinity;
      for (let j = j0 - r; j <= j0 + r; j++) for (let i = i0 - r; i <= i0 + r; i++) {
        if (Math.max(Math.abs(i - i0), Math.abs(j - j0)) !== r || blockedCell(i, j)) continue;
        const d = Math.hypot(cx(i) - x, cz(j) - z);
        if (d < bestD) { bestD = d; best = {x: cx(i), z: cz(j)}; }
      }
      if (best) return best;
    }
    return {x, z};
  }

  /** Route from a to b as a list of waypoints (excluding a). Falls back to the straight line when nothing is found. */
  function path(a, b) {
    const goal = blocked(b.x, b.z) ? nearestFree(b.x, b.z) : b;
    if (clear(a, goal)) return [goal];
    const start = blocked(a.x, a.z) ? nearestFree(a.x, a.z) : a;
    const si = ix(start.x), sj = iz(start.z), gi = ix(goal.x), gj = iz(goal.z);
    if (!inside(si, sj) || !inside(gi, gj)) return [goal];
    const total = nx * nz, g = new Float32Array(total).fill(Infinity), from = new Int32Array(total).fill(-1), done = new Uint8Array(total);
    const heap = [];   // binary min-heap of [f, index]
    const push = (f, idx) => { heap.push([f, idx]); let i = heap.length - 1; while (i > 0) { const p = (i - 1) >> 1; if (heap[p][0] <= heap[i][0]) break; [heap[p], heap[i]] = [heap[i], heap[p]]; i = p; } };
    const pop = () => { const top = heap[0], last = heap.pop(); if (heap.length) { heap[0] = last; let i = 0; for (;;) { const l = 2 * i + 1, r = l + 1; let m = i; if (l < heap.length && heap[l][0] < heap[m][0]) m = l; if (r < heap.length && heap[r][0] < heap[m][0]) m = r; if (m === i) break; [heap[m], heap[i]] = [heap[i], heap[m]]; i = m; } } return top; };
    const h = (i, j) => Math.hypot(i - gi, j - gj);
    const s0 = sj * nx + si; g[s0] = 0; push(h(si, sj), s0);
    let found = false;
    while (heap.length) {
      const [, cur] = pop(); if (done[cur]) continue; done[cur] = 1;
      const ci = cur % nx, cj = (cur / nx) | 0;
      if (ci === gi && cj === gj) { found = true; break; }
      for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
        if (!di && !dj) continue;
        const ni = ci + di, nj = cj + dj;
        if (blockedCell(ni, nj) || (di && dj && (blockedCell(ci + di, cj) || blockedCell(ci, cj + dj)))) continue;
        const idx = nj * nx + ni, cost = g[cur] + (di && dj ? 1.4142 : 1);
        if (cost < g[idx]) { g[idx] = cost; from[idx] = cur; push(cost + h(ni, nj), idx); }
      }
    }
    if (!found) return [goal];
    const raw = [];
    for (let at = gj * nx + gi; at !== -1 && at !== s0; at = from[at]) raw.push({x: cx(at % nx), z: cz((at / nx) | 0)});
    raw.reverse(); raw[raw.length - 1] = goal;
    // string pulling: skip waypoints that can be bypassed in a straight line
    const out = []; let anchor = start, k = 0;
    while (k < raw.length) {
      let far = k;
      for (let t = raw.length - 1; t > k; t--) if (clear(anchor, raw[t])) { far = t; break; }
      out.push(raw[far]); anchor = raw[far]; k = far + 1;
    }
    return out;
  }

  return {
    obstacles, blocked, clear, path, nearestFree, cell,
    add(o) { obstacles.push({x: o.x, z: o.z, hw: o.hw, hd: o.hd}); rebuild(); },
    addMany(list) { for (const o of list) obstacles.push({x: o.x, z: o.z, hw: o.hw, hd: o.hd}); rebuild(); },
    clearAll() { obstacles.length = 0; rebuild(); },
  };
}

export const turnTo = (from, to, k) => {
  const d = ((to - from + Math.PI) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2) - Math.PI;
  return from + d * Math.min(1, k);
};

/**
 * One employee. Modes: 'seated' | 'walking' (includes standing up and sitting down) | 'waiting' | 'presenting'.
 * `blend` is the pose: 0 seated .. 1 standing. `update` returns what the renderer needs this frame.
 */
export class Walker {
  constructor({id, seat, nav, speed = 1.75, aisleZ = -5.5, sideDx = 2.5, boardSpot = {x: 7.6, z: 3.3}, rise = 0.55}) {
    Object.assign(this, {id, seat, nav, speed, aisleZ, sideDx, boardSpot, rise});
    this.mode = 'seated'; this.blend = 0; this.pos = {x: seat.x, z: seat.z}; this.heading = 0; this.phase = 0;
    this.path = []; this.dest = ''; this.stage = ''; this.timer = 0; this.resolve = null; this.attention = false; this.speaking = false;
    this.follow = null; this.replan = 0; this.moving = 0;
  }
  get side() { return this.seat.x + (this.seat.x > 0 ? this.sideDx : -this.sideDx); }
  settle(ok) { const done = this.resolve; this.resolve = null; if (done) done(ok); }

  go(dest) {
    if (dest !== 'player' && dest !== 'board' && dest !== 'home') return Promise.resolve(false);
    if (this.mode === 'seated' && dest === 'home') return Promise.resolve(true);
    this.settle(false);
    this.dest = dest; this.attention = dest === 'home' ? false : this.attention; this.speaking = false;
    this.follow = dest === 'player' ? {x: this.pos.x, z: this.pos.z} : null;
    const promise = new Promise((r) => { this.resolve = r; });
    if (this.mode === 'seated') { this.mode = 'walking'; this.stage = 'rise'; this.timer = 0; this.path = []; }
    else if (this.stage === 'sit') { this.stage = 'rise'; this.timer = this.blend * this.rise; this.path = []; }   // interrupted while sitting down
    else { this.mode = 'walking'; this.stage = 'walk'; this.path = this.routeFor(); }
    return promise;
  }

  routeFor(target = this.follow) {
    const aisle = {x: this.side, z: this.aisleZ}, route = [];
    const atDesk = Math.abs(this.pos.x - this.seat.x) < 0.9 && this.pos.z < this.aisleZ - 0.3 && Math.abs(this.pos.z - this.seat.z) < 1.2;
    let from = this.pos;
    if (atDesk) { route.push({x: this.side, z: this.seat.z}, aisle); from = aisle; }
    if (this.dest === 'home') {
      const dir = this.seat.x > 0 ? 1 : -1;
      if (atDesk) return [{x: this.seat.x, z: this.seat.z, sit: true}];
      route.push(...this.nav.path(from, aisle), {x: this.side, z: this.seat.z});
      return [...route, {x: this.seat.x + dir * 0.55, z: this.seat.z}, {x: this.seat.x, z: this.seat.z, sit: true}];
    }
    const legs = this.nav.path(from, this.dest === 'board' ? this.boardSpot : target || this.pos);
    if (this.dest === 'player' && legs.length) legs[legs.length - 1] = {...legs[legs.length - 1], follow: true};
    return [...route, ...legs];
  }

  arrive() {
    this.mode = this.dest === 'board' ? 'presenting' : 'waiting';
    this.path = []; this.settle(true);
  }

  /** @param ctx {front:{x,z}|null, look:{x,z}} where the player stands/looks, so employees face the player */
  update(dt, ctx = {}) {
    this.moving = 0;
    if (this.mode === 'seated') return this.frame();
    if (this.stage === 'rise') {
      this.timer += dt; this.blend = Math.min(1, this.timer / this.rise);
      if (this.blend >= 1) {
        this.stage = 'walk';
        const front = this.dest === 'player' ? (ctx.front || this.follow || this.pos) : null;
        if (front) this.follow = {x: front.x, z: front.z};
        this.path = this.routeFor(front);
        this.pos = {x: this.seat.x, z: this.seat.z};
      }
      return this.frame();
    }
    if (this.stage === 'sit') {
      this.timer += dt; this.blend = Math.max(0, 1 - this.timer / this.rise);
      this.heading = turnTo(this.heading, 0, dt * 8); this.pos = {x: this.seat.x, z: this.seat.z};
      if (this.blend <= 0) { this.mode = 'seated'; this.stage = ''; this.attention = false; this.speaking = false; this.settle(true); }
      return this.frame();
    }
    if (this.dest === 'player' && ctx.front && this.path.length) {
      this.replan -= dt;
      const last = this.path[this.path.length - 1];
      if (last.follow && (Math.hypot(ctx.front.x - last.x, ctx.front.z - last.z) > 0.8 || this.replan < -3)) {
        this.replan = 0; this.follow = {x: ctx.front.x, z: ctx.front.z};
        this.path = [...this.nav.path(this.pos, this.follow), {...this.follow, follow: true}];
      }
    }
    const wp = this.path[0];
    if (wp) {
      const dx = wp.x - this.pos.x, dz = wp.z - this.pos.z, dist = Math.hypot(dx, dz);
      if (dist < (wp.follow ? 0.3 : 0.1)) {
        this.path.shift();
        if (wp.sit) { this.stage = 'sit'; this.timer = 0; this.pos = {x: this.seat.x, z: this.seat.z}; }
        else if (!this.path.length) this.arrive();
      } else {
        const step = Math.min(dist, this.speed * dt);
        this.pos.x += dx / dist * step; this.pos.z += dz / dist * step;
        this.heading = turnTo(this.heading, Math.atan2(dx, dz), dt * 9);
        this.phase += step * 4.6; this.moving = 1;
      }
    } else if (this.mode !== 'seated') {
      const target = this.mode === 'presenting' ? {x: this.pos.x + 1, z: this.pos.z - 4} : ctx.look;
      if (target) this.heading = turnTo(this.heading, Math.atan2(target.x - this.pos.x, target.z - this.pos.z), dt * 5);
    }
    return this.frame();
  }

  frame() {
    return {x: this.pos.x, z: this.pos.z, heading: this.heading, blend: this.blend, moving: this.moving, phase: this.phase, mode: this.mode,
            speaking: this.speaking, presenting: this.mode === 'presenting', flag: this.attention && this.mode === 'waiting'};
  }
}
