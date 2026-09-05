# Vendored viewer dependencies

three.js **r180** (`three@0.180.0`), MIT — copied verbatim from
`eventos/node_modules/three` (build + examples/jsm), no network fetch, no pip.
Layout preserves the upstream relative imports (`loaders/` → `../utils/`,
`three.module.min.js` → `./three.core.min.js`); `index.html` maps the bare
`three` specifier via an importmap. Upgrade = re-copy the same four files +
`three.core.min.js` from a newer three release.

Files: `three.module.min.js`, `three.core.min.js`, `loaders/GLTFLoader.js`,
`controls/OrbitControls.js`, `utils/BufferGeometryUtils.js`.
