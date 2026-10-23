// Run against a local fixture server serving /fixture/{landscape,portrait}/.
// Uses the real GLTFLoader and Three skin evaluation, rather than inspecting JSON.
import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const [url, reportPath] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
try {
  const page = await browser.newPage();
  await page.goto(`${url}/check`);
  const result = await page.evaluate(async () => {
    const THREE = await import('/vendor/three/three.module.js');
    const { GLTFLoader } = await import('/vendor/three/addons/loaders/GLTFLoader.js');
    const results = [];
    for (const layout of ['landscape', 'portrait']) {
      const base = `/fixture/${layout}/`;
      const manifest = await (await fetch(base + 'preview_manifest.json')).json();
      const expected = await (await fetch(base + 'expected.json')).json();
      const gltf = await new GLTFLoader().loadAsync(base + 'preview.glb');
      if (gltf.animations.length !== 1) throw Error('Expected one baked scene animation');
      const mixer = new THREE.AnimationMixer(gltf.scene);
      const action = mixer.clipAction(gltf.animations[0]);
      action.setLoop(THREE.LoopOnce, 1); action.clampWhenFinished = true; action.play();
      if (Math.abs(gltf.animations[0].duration - manifest.duration_seconds) > 1e-5) throw Error('wrong clock/range');
      const camera = new THREE.PerspectiveCamera();
      let maxPositionError = 0, maxPixelError = 0;
      for (const sample of expected) {
        action.paused = false; action.enabled = true;
        mixer.setTime(sample.time);
        gltf.scene.updateMatrixWorld(true);
        gltf.scene.traverse(o => { if (o.isSkinnedMesh) o.skeleton.update(); });
        const frame = manifest.frames[Math.round(sample.time * manifest.fps)];
        camera.matrixWorld.fromArray(frame.matrix_world);
        camera.matrixWorldInverse.copy(camera.matrixWorld).invert();
        camera.projectionMatrix.fromArray(frame.projection_matrix);
        const roots = {}, probes = {};
        gltf.scene.traverse(o => {
          if (o.userData.previs_actor_id) roots[o.userData.previs_actor_id] = o;
          if (o.userData.preview_probe && o.isMesh) probes[o.userData.preview_probe] = o;
        });
        for (const [id, position] of Object.entries(sample.roots)) {
          if (!roots[id]) throw Error('missing stable actor id: ' + id);
          maxPositionError = Math.max(maxPositionError, roots[id].getWorldPosition(new THREE.Vector3()).distanceTo(new THREE.Vector3(...position)));
        }
        for (const [id, bounds] of Object.entries(sample.meshes)) {
          if (!probes[id]) throw Error('missing mesh: ' + id);
          const box = new THREE.Box3().setFromObject(probes[id], true);
          const error = Math.max(box.min.distanceTo(new THREE.Vector3(...bounds.min)), box.max.distanceTo(new THREE.Vector3(...bounds.max)));
          if (error > .001) throw Error(JSON.stringify({layout, id, time: sample.time, error,
            expected: bounds, actual: {min: box.min.toArray(), max: box.max.toArray()}}));
          maxPositionError = Math.max(maxPositionError, error);
        }
        for (const [id, pixel] of Object.entries(sample.pixels)) {
          const obj = roots[id] || probes[id];
          const projected = obj.getWorldPosition(new THREE.Vector3()).project(camera);
          const actual = [(projected.x + 1) / 2 * manifest.resolution[0], (1 - projected.y) / 2 * manifest.resolution[1]];
          maxPixelError = Math.max(maxPixelError, Math.hypot(actual[0] - pixel[0], actual[1] - pixel[1]));
        }
      }
      if (maxPositionError > .001 || maxPixelError > 1) throw Error(JSON.stringify({layout, maxPositionError, maxPixelError}));
      results.push({layout, samples: expected.length, maxPositionError, maxPixelError});
    }
    return {ok: true, results};
  });
  if (reportPath) await writeFile(reportPath, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally { await browser.close(); }
