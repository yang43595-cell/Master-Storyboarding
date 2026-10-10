# Three.js local preview dependency

- Version: **0.180.0** (r180), pinned; no CDN or runtime install.
- Source: https://registry.npmjs.org/three/-/three-0.180.0.tgz
- Upstream: https://github.com/mrdoob/three.js/tree/r180
- License: MIT; original notice preserved in `LICENSE`.
- Included unchanged: `three.module.js`, `three.core.js`, GLTFLoader,
  OrbitControls and BufferGeometryUtils. Only these files are served locally.

Used to display Blender-derived white-model previews. Blender remains responsible
for evaluating actor motion, camera matrices, verification and production exports.
