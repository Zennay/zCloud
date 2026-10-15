// Static batching: many small meshes that share a material become one mesh, so the frame costs a handful of draw
// calls instead of hundreds (each draw is paid again in the shadow pass and the ambient-occlusion normal pass).
// Only meshes that never move, change visibility or get clicked may be merged; callers pass the rest in `keep`.
import * as THREE from 'three';
import {mergeGeometries} from 'three/addons/utils/BufferGeometryUtils.js';

const ATTRS = ['position', 'normal', 'uv'];

function prepared(mesh, matrix) {
  let g = mesh.geometry.index ? mesh.geometry.toNonIndexed() : mesh.geometry.clone();
  for (const name of Object.keys(g.attributes)) if (!ATTRS.includes(name)) g.deleteAttribute(name);
  if (!g.attributes.normal) g.computeVertexNormals();
  if (!g.attributes.uv) g.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array(g.attributes.position.count * 2), 2));
  g.morphAttributes = {}; g.clearGroups();
  g.applyMatrix4(matrix);
  return g;
}

/**
 * Merge the static meshes below `root` per (material, shadow flags) into meshes parented to `root`.
 * deep: true walks the whole subtree (furniture groups included); false only takes direct children (rig joints).
 * keep: objects whose whole subtree must stay untouched. Returns the number of meshes removed.
 */
export function mergeStatic(root, {deep = true, keep = new Set()} = {}) {
  root.updateMatrixWorld(true);
  const inverse = new THREE.Matrix4().copy(root.matrixWorld).invert();
  const buckets = new Map();
  const visit = (o) => {
    for (const child of o.children) {
      if (keep.has(child) || child.visible === false || child.userData.action) continue;
      if (child.isMesh && !child.isInstancedMesh && !child.isSkinnedMesh && child.children.length === 0 && !Array.isArray(child.material)
          && !child.material.transparent && !child.onBeforeRender.length && child.matrixWorld.determinant() > 0) {
        const key = child.material.uuid + (child.castShadow ? 'c' : '') + (child.receiveShadow ? 'r' : '');
        let b = buckets.get(key); if (!b) buckets.set(key, b = {material: child.material, cast: child.castShadow, receive: child.receiveShadow, meshes: []});
        b.meshes.push(child);
      } else if (deep && !child.isLight && !child.isCamera) visit(child);
    }
  };
  visit(root);
  let removed = 0;
  const m = new THREE.Matrix4();
  for (const b of buckets.values()) {
    if (b.meshes.length < 2) continue;
    const merged = mergeGeometries(b.meshes.map((mesh) => prepared(mesh, m.multiplyMatrices(inverse, mesh.matrixWorld))), false);
    if (!merged) continue;
    const mesh = new THREE.Mesh(merged, b.material); mesh.castShadow = b.cast; mesh.receiveShadow = b.receive;
    mesh.userData.merged = b.meshes.length;
    for (const old of b.meshes) old.removeFromParent();
    root.add(mesh); removed += b.meshes.length - 1;
  }
  return removed;
}

/** Remove groups that ended up empty after merging (keeps the scene graph walk short). */
export function pruneEmpty(root, keep = new Set()) {
  for (const child of [...root.children]) {
    pruneEmpty(child, keep);
    if (child.type === 'Group' && child.children.length === 0 && !keep.has(child) && !child.userData.action) child.removeFromParent();
  }
}
