// Post-processing chain: MSAA scene -> ambient occlusion -> bloom -> tone mapping/sRGB. Falls back to plain rendering.
import * as THREE from 'three';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {GTAOPass} from 'three/addons/postprocessing/GTAOPass.js';
import {UnrealBloomPass} from 'three/addons/postprocessing/UnrealBloomPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';

export function createPost({renderer, scene, camera}) {
  let composer = null, ao = null, bloom = null, level = 'off', w = 2, h = 2;
  const build = () => {
    composer?.dispose?.();
    const size = renderer.getDrawingBufferSize(new THREE.Vector2());
    w = size.x; h = size.y;
    const target = new THREE.WebGLRenderTarget(w, h, {type: THREE.HalfFloatType, samples: level === 'high' ? 4 : 2});
    composer = new EffectComposer(renderer, target);
    composer.setPixelRatio(1); composer.setSize(w, h);
    composer.addPass(new RenderPass(scene, camera));
    ao = null;
    if (level === 'high') {
      ao = new GTAOPass(scene, camera, w, h);
      ao.output = GTAOPass.OUTPUT.Default;
      ao.updateGtaoMaterial({radius: 0.9, distanceExponent: 1.4, thickness: 1.2, scale: 1.1, samples: 12, distanceFallOff: 1, screenSpaceRadius: false});
      ao.updatePdMaterial({lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, radiusExponent: 1, rings: 2, samples: 12});
      ao.blendIntensity = 0.85;
      composer.addPass(ao);
    }
    bloom = new UnrealBloomPass(new THREE.Vector2(w, h), 0.55, 0.55, 0.82);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());
  };
  return {
    get level() { return level; },
    set(next) {
      if (next === level) return;
      level = next;
      renderer.toneMapping = next === 'off' ? THREE.ACESFilmicToneMapping : THREE.ACESFilmicToneMapping;
      if (next === 'off') { composer?.dispose?.(); composer = null; ao = null; bloom = null; } else build();
    },
    setNight(night) { if (bloom) { bloom.strength = 0.5 + 0.55 * night; bloom.threshold = 0.82 - 0.2 * night; } },
    resize() { if (composer) { const size = renderer.getDrawingBufferSize(new THREE.Vector2()); w = size.x; h = size.y; composer.setSize(w, h); } },
    render(dt) { if (composer) composer.render(dt); else renderer.render(scene, camera); },
    dispose() { composer?.dispose?.(); },
  };
}
