# Third-party code

`glb-forge` itself is MIT (see [LICENSE](LICENSE)). The local review viewer vendors two
third-party libraries so it works with no network fetch and no package manager. Both are
redistributed under their own licences, reproduced below by reference.

## three.js — r180 (`three@0.180.0`) — MIT

Copyright © 2010-2025 three.js authors · <https://github.com/mrdoob/three.js>

Copied verbatim from an npm install of `three@0.180.0` (`build/` + `examples/jsm/`), no
modifications. Files:

```
review/vendor/three.module.min.js
review/vendor/three.core.min.js
review/vendor/loaders/GLTFLoader.js
review/vendor/controls/OrbitControls.js
review/vendor/utils/BufferGeometryUtils.js
```

The layout preserves upstream's relative imports, and `review/index.html` maps the bare `three`
specifier via an importmap. To upgrade, re-copy the same files from a newer three release.

three.js is MIT-licensed; its licence text travels with the upstream distribution at
<https://github.com/mrdoob/three.js/blob/dev/LICENSE>.

## Draco — Apache License 2.0

Copyright © Google LLC · <https://github.com/google/draco>

The decoder is vendored so the viewer can open DRACO-compressed GLBs offline:

```
review/vendor/draco/draco_decoder.js
review/vendor/draco/draco_decoder.wasm
review/vendor/draco/draco_wasm_wrapper.js
```

Distributed as-is under Apache-2.0. Full licence:
<https://github.com/google/draco/blob/main/LICENSE>

Apache-2.0 requires that you retain the licence and any NOTICE when redistributing; keeping this
file alongside the vendored decoder is how this repository does that.

## Blender

`glb-forge` drives Blender as an external program via `--python`. It does not link Blender, embed
it, or redistribute it, so Blender's GPL does not extend to this repository. The generated GLB
files are your data, not derivative works of Blender.
