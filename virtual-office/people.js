// One articulated character model used for seated, walking, speaking and presenting staff.
// Pure three.js primitives (capsules, spheres), authored for this project. Colours come from the staff record.
import * as THREE from 'three';

const HEAD_Y = 1.52;

export function badgeTexture(name, title) {
  const c = document.createElement('canvas'); c.width = 512; c.height = 110;
  const g = c.getContext('2d');
  g.fillStyle = 'rgba(24,39,46,.88)'; g.beginPath(); g.roundRect(4, 4, 504, 102, 18); g.fill();
  g.fillStyle = '#ead6b4'; g.font = '600 38px sans-serif'; g.textAlign = 'center'; g.fillText(name, 256, 52);
  g.fillStyle = '#9fb3b0'; g.font = '24px sans-serif'; g.fillText(String(title || '').slice(0, 34), 256, 88);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}

export function buildPerson(spec, {segments = 12} = {}) {
  const look = spec.look || {}, owned = {geo: [], mat: [], tex: []};
  const mat = (color, roughness = 0.85, extra = {}) => { const m = new THREE.MeshStandardMaterial({color, roughness, ...extra}); owned.mat.push(m); return m; };
  const geo = (g) => { owned.geo.push(g); return g; };
  const skin = new THREE.MeshPhysicalMaterial({color: look.skin || '#c99679', roughness: 0.52, sheen: 0.6, sheenColor: new THREE.Color('#ffb59a'), sheenRoughness: 0.6}); owned.mat.push(skin);
  const suit = mat(look.suit || '#414e50', 0.82), pants = mat(new THREE.Color(look.suit || '#414e50').multiplyScalar(0.7), 0.88), shirt = mat('#eee9df', 0.7),
        hair = mat(look.hair || '#302b28', 0.55, {metalness: 0.05}), shoe = mat('#14181c', 0.38, {metalness: 0.2}), white = mat('#f5f2ea', 0.4), dark = mat('#16191c', 0.5),
        lip = mat('#a8605a', 0.6), accent = mat(spec.color || '#d4b789', 0.6);
  const sphere = geo(new THREE.SphereGeometry(1, segments + 4, segments)), box = geo(new THREE.BoxGeometry(1, 1, 1));
  const capsule = (r, len) => geo(new THREE.CapsuleGeometry(r, len, 5, segments));
  const add = (parent, g, m, x = 0, y = 0, z = 0, sx = 1, sy = 1, sz = 1) => { const o = new THREE.Mesh(g, m); o.position.set(x, y, z); o.scale.set(sx, sy, sz); o.castShadow = true; o.receiveShadow = true; parent.add(o); return o; };
  const joint = (parent, x, y, z = 0) => { const j = new THREE.Group(); j.position.set(x, y, z); parent.add(j); return j; };

  const root = new THREE.Group(), body = joint(root, 0, 0.93), upper = joint(body, 0, 0);
  // pelvis + torso (blazer over shirt)
  add(body, capsule(0.15, 0.06), pants, 0, -0.02, 0, 1.15, 1, 0.78);
  add(upper, capsule(0.165, 0.26), suit, 0, 0.3, 0, 1.12, 1, 0.74);                          // chest
  add(upper, capsule(0.135, 0.12), suit, 0, 0.1, 0, 1.12, 1, 0.78);                           // waist
  add(upper, box, shirt, 0, 0.37, 0.118, 0.1, 0.34, 0.02);                                    // shirt front
  for (const side of [-1, 1]) { const lapel = add(upper, box, suit, side * 0.075, 0.38, 0.126, 0.07, 0.3, 0.02); lapel.rotation.z = side * 0.2; }
  if (spec.voice?.gender === 'm') add(upper, box, accent, 0, 0.34, 0.132, 0.034, 0.26, 0.012);  // tie
  else add(upper, sphere, accent, 0, 0.46, 0.115, 0.05, 0.012, 0.012);                       // necklace hint
  // neck + head
  add(upper, capsule(0.052, 0.05), skin, 0, 0.5, 0);
  const head = joint(upper, 0, HEAD_Y - 0.93);
  add(head, sphere, skin, 0, 0.13, 0, 0.092, 0.115, 0.1);                                      // skull
  add(head, sphere, skin, 0, 0.055, 0.018, 0.074, 0.075, 0.082);                               // jaw
  add(head, sphere, skin, 0, 0.1, 0.1, 0.017, 0.023, 0.026);                                   // nose
  for (const s of [-1, 1]) {
    add(head, sphere, skin, s * 0.093, 0.12, 0, 0.012, 0.03, 0.02);                            // ears
    add(head, sphere, white, s * 0.036, 0.137, 0.088, 0.016, 0.0105, 0.007);                   // eyes
    add(head, sphere, dark, s * 0.036, 0.137, 0.094, 0.0075, 0.0085, 0.004);
    add(head, box, hair, s * 0.036, 0.158, 0.09, 0.036, 0.0065, 0.008);                        // brows
  }
  add(head, box, lip, 0, 0.06, 0.094, 0.036, 0.0055, 0.006);
  const cap = new THREE.Mesh(geo(new THREE.SphereGeometry(1, 20, 14, 0, Math.PI * 2, 0, 1.22)), hair);
  cap.position.set(0, 0.13, -0.004); cap.scale.set(0.1, 0.112, 0.112); cap.castShadow = true; head.add(cap);
  add(head, sphere, hair, 0, 0.115, -0.04, 0.098, 0.105, 0.085);                               // back of the head
  const style = look.style || 'short';
  if (style === 'short') add(head, box, hair, 0, 0.176, 0.075, 0.1, 0.02, 0.03);
  if (style === 'long') { add(head, capsule(0.05, 0.16), hair, 0, 0.02, -0.07, 1.5, 1, 0.75); for (const s of [-1, 1]) add(head, capsule(0.02, 0.1), hair, s * 0.09, 0.07, 0.01, 1, 1, 1); }
  if (style === 'bun') add(head, sphere, hair, 0, 0.22, -0.07, 0.05, 0.05, 0.05);
  // legs and arms as jointed chains
  const legs = [-1, 1].map((s) => {
    const hip = joint(body, s * 0.095, -0.03), knee = joint(hip, 0, -0.45);
    add(hip, capsule(0.075, 0.33), pants, 0, -0.22, 0); add(knee, capsule(0.057, 0.3), pants, 0, -0.21, 0);
    add(knee, box, shoe, 0, -0.455, 0.055, 0.1, 0.07, 0.27);
    return {hip, knee};
  });
  const arms = [-1, 1].map((s) => {
    const shoulder = joint(upper, s * 0.215, 0.49), elbow = joint(shoulder, 0, -0.31);
    add(shoulder, sphere, suit, 0, 0, 0, 0.062, 0.062, 0.062);
    add(shoulder, capsule(0.05, 0.2), suit, 0, -0.16, 0); add(elbow, capsule(0.04, 0.2), suit, 0, -0.14, 0);
    add(elbow, sphere, skin, 0, -0.3, 0.005, 0.045, 0.058, 0.032);
    return {shoulder, elbow};
  });
  // name badge
  const tex = badgeTexture(spec.name, spec.title); owned.tex.push(tex);
  const spriteMat = new THREE.SpriteMaterial({map: tex, transparent: true, depthTest: true}); owned.mat.push(spriteMat);
  const badge = new THREE.Sprite(spriteMat); badge.position.set(0, 2.08, 0); badge.scale.set(1.0, 0.22, 1); root.add(badge);

  const SEATED = {y: 0.59, lean: 0.1, hip: -1.5, knee: 1.55, shoulder: -0.35, elbow: -1.4}, STANDING = {y: 0.93, lean: 0, hip: 0, knee: 0, shoulder: 0, elbow: -0.08};
  const mix = (a, b, k) => a + (b - a) * k;
  let blend = 1;
  const rig = {root, body, upper, head, legs, arms, badge, spriteMat};
  /** pose blend: 0 = seated at a desk, 1 = standing. */
  function applyBase(k) {
    body.position.y = mix(SEATED.y, STANDING.y, k); upper.rotation.x = mix(SEATED.lean, STANDING.lean, k);
    for (const l of legs) { l.hip.rotation.x = mix(SEATED.hip, STANDING.hip, k); l.knee.rotation.x = mix(SEATED.knee, STANDING.knee, k); }
    for (const [i, a] of arms.entries()) { a.shoulder.rotation.set(mix(SEATED.shoulder, STANDING.shoulder, k), 0, (i ? -1 : 1) * mix(-0.06, 0.05, k)); a.elbow.rotation.set(mix(SEATED.elbow, STANDING.elbow, k), 0, 0); }
    upper.rotation.y = 0; upper.rotation.z = 0; head.rotation.set(0, 0, 0);
  }
  function setPose(next) { blend = next === 'seated' ? 0 : 1; applyBase(blend); }
  /** blend: 0..1; moving: 0..1 walking intensity; phase: walk cycle phase (radians). */
  function animate({t = 0, blend: k = blend, moving = 0, phase = 0, speaking = false, presenting = false, working = false}) {
    blend = k; applyBase(k); badge.position.y = mix(1.72, 2.08, k);
    if (k < 0.02) {
      const type = working ? Math.sin(t * 0.018) * 0.05 : 0, calm = Math.sin(t * 0.0007 + (spec.slot || 0)) * 0.012;
      arms[0].elbow.rotation.x += type; arms[1].elbow.rotation.x -= type;
      if (speaking) { arms[1].shoulder.rotation.x = -0.7 + Math.sin(t * 0.006) * 0.15; arms[1].elbow.rotation.x = -1.1; }
      head.rotation.set(working ? 0.14 + Math.sin(t * 0.004) * 0.02 : 0.05, Math.sin(t * 0.0004 + (spec.slot || 0)) * 0.18, calm);
      upper.rotation.z = calm; return;
    }
    if (k < 1) return;
    const swing = Math.sin(phase) * 0.62 * moving, lift = Math.max(0, Math.sin(phase)) * 0.75 * moving, lift2 = Math.max(0, -Math.sin(phase)) * 0.75 * moving;
    legs[0].hip.rotation.x = swing; legs[1].hip.rotation.x = -swing; legs[0].knee.rotation.x = lift2; legs[1].knee.rotation.x = lift;
    arms[0].shoulder.rotation.x = -swing * 0.7; arms[1].shoulder.rotation.x = swing * 0.7;
    arms[0].elbow.rotation.x = -0.1 - lift2 * 0.4; arms[1].elbow.rotation.x = -0.1 - lift * 0.4;
    upper.rotation.y = swing * 0.12; body.position.y = STANDING.y + Math.abs(Math.cos(phase)) * 0.025 * moving;
    if (!moving) {
      if (presenting) { arms[1].shoulder.rotation.set(-0.2, 0, -1.25 + Math.sin(t * 0.002) * 0.08); arms[1].elbow.rotation.x = -0.35; }
      if (speaking) { arms[0].shoulder.rotation.set(-0.5 + Math.sin(t * 0.006) * 0.2, 0, 0.35); arms[0].elbow.rotation.x = -1.0 + Math.sin(t * 0.007) * 0.25; head.rotation.x = Math.sin(t * 0.011) * 0.05; }
    }
  }
  setPose('standing');
  return {rig, root, setPose, animate, get blend() { return blend; }, dispose() { owned.geo.forEach((x) => x.dispose()); owned.mat.forEach((x) => x.dispose()); owned.tex.forEach((x) => x.dispose()); }};
}
