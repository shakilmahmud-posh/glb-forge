# glb-forge

[![check](https://github.com/shakilmahmud-posh/glb-forge/actions/workflows/check.yml/badge.svg)](https://github.com/shakilmahmud-posh/glb-forge/actions/workflows/check.yml)

**Deterministic, gated GLB builds from a JSON spec.** Blender runs headless, the same spec produces
a byte-identical file, and geometry quality gates fail the build before a bad asset reaches your app.

```bash
./forge build examples/x-banner.json
# staging/x-banner/model.glb      84 tris, 2 materials
# staging/x-banner/report.json    gates: all pass
```

Build it again and you get **the same bytes**. Not equivalent geometry — the same file.

---

## Why byte-identical matters

Asset pipelines are famously non-reproducible. Rebuild yesterday's model and you get a file that
looks identical and hashes differently, so you cannot cache on content, cannot diff a change, and
cannot tell "the spec changed" from "Blender felt different today."

Four things make it hold here:

- an empty factory-startup scene, so nothing leaks in from your Blender config
- fixed construction order, taken from the spec
- no unseeded randomness (wrinkles and scatter are seeded from the spec)
- fixed export settings

And one trap worth naming, because it is the one everybody hits: **the build timestamp cannot go
in the file.** `builtAt` lives in `report.json` only. A timestamp inside the GLB makes every
rebuild differ and quietly destroys the property you were trying to have. The test suite asserts
byte-identity on rebuild, so this cannot silently regress.

The glTF scene extras carry `forgeVersion` and `specSha256`, so a built asset can always be traced
back to the exact spec that produced it.

## Quality gates

A build does not just succeed or crash — it is measured, and the measurements are a contract:

| Gate        | Checks                                                                  |
| ----------- | ----------------------------------------------------------------------- |
| `budget`    | triangle count against the LOD tier (`low` 3k · `mid` 15k · `high` 60k) |
| `fileSize`  | bytes against the same tier (1.5 MB · 4 MB · 10 MB)                     |
| `footprint` | built bounds match the declared size, within 10%                        |
| `baseY`     | the asset actually sits on the floor, within 10 mm                      |
| `materials` | at most 4 per part, 12 per flattened set                                |
| `geometry`  | no NaN coordinates                                                      |

Exit codes: **0** pass · **1** gate fail · **3** environment or internal error. A gate failure is a
non-zero exit, so this drops into CI without a wrapper.

**`builder/gates.py` has no `bpy` import.** Gate logic is pure Python that runs outside Blender,
which is what makes it unit-testable — and CI proves it by evaluating gates on a machine with no
Blender installed at all.

Watertightness is deliberately _not_ checked. Panels and fabric are open meshes on purpose.

## Quickstart

```bash
git clone https://github.com/shakilmahmud-posh/glb-forge
cd glb-forge
./forge doctor                        # checks python, Blender, schema, writability
./forge build examples/x-banner.json
bash tests/run.sh                     # the whole suite
```

## Commands

|                         |                                                                                  |
| ----------------------- | -------------------------------------------------------------------------------- |
| `forge doctor`          | environment and pinned-Blender check                                             |
| `forge validate <spec>` | schema plus semantic validation, before you spend a build                        |
| `forge build <spec>`    | spec → `staging/<slug>/{model.glb, thumb.png, front.png, report.json}`           |
| `forge sheet`           | contact sheet of everything staged                                               |
| `forge from-image`      | scaffold a spec from a reference image                                           |
| `forge harvest`         | decimate an existing mesh into a spec-able asset, with a collapse-ratio floor    |
| `forge import`          | copy a built asset into a downstream repo, appending to its catalog and a ledger |
| `forge review`          | local review server with a three.js viewer and approve/reject                    |

## The spec

A spec is JSON validated against [`schema/forge-spec.schema.json`](schema/forge-spec.schema.json).
Geometry is authored **directly in spec space** — metres, Y-up, right-handed, +Z front — and
exported with `export_yup=False`, so spec coordinates pass through to the GLB verbatim. There is no
axis-conversion arithmetic anywhere in the codebase, which is the single largest source of quiet
bugs in Blender-to-glTF pipelines.

Full field reference: [`SPEC_GUIDE.md`](SPEC_GUIDE.md). Three worked examples in
[`examples/`](examples), each with a committed golden report the suite checks against.

`consumerHints.<name>` is an open extension point for whatever consumes your assets — declare your
own keys and `forge import` will carry them through.

## Requirements

**Blender 5.1.1**, pinned, and `forge doctor` fails on a mismatch. That is deliberate: a different
Blender build can produce different procedural geometry, and the golden reports commit exact
triangle counts. Override the binary with `FORGE_BLENDER`; change the pin only if you are prepared
to regenerate the goldens.

Python 3.9+. `npx` is optional (`gltf-transform inspect`). No pip install, no node_modules — the
viewer's dependencies are vendored, see [THIRD-PARTY.md](THIRD-PARTY.md).

**Verified:** the full suite, 60 assertions, on macOS arm64 with Blender 5.1.1. CI runs the same
suite on Linux, which tests something a single laptop cannot — that the committed golden triangle
counts hold on a different OS and CPU. If the badge above is green, cross-platform agreement holds.

**Not verified:** Windows.

## Prior art

I looked and did not find a comparable tool — a deterministic spec-to-GLB builder with geometry
budget gates. The nearest things are one-off Blender scripts inside game pipelines and commercial
asset-pipeline products.

If something similar exists, an issue pointing at it is welcome; I would rather cite it than
pretend this space is emptier than it is.

## Where it came from

Extracted from the asset pipeline behind [EventOS](https://eventos.best), which generates AR event
mockups: a client picks a set, the server builds the GLB and the USDZ, and it appears in AR Quick
Look on their phone. Byte-identical rebuilds and hard triangle budgets are not academic there —
they are the difference between a model that loads on a mid-range phone at a venue and one that
does not.

The catalogue of event decor stays private. This is the harness, which is more useful to everyone
else than the furniture is.

## Maintenance

Best-effort, and I would rather say so than imply otherwise. Issues are welcome and I read them;
responses are not guaranteed and may be slow. Fork freely — that is what the licence is for.

## Licence

MIT — see [LICENSE](LICENSE). Vendored three.js (MIT) and Draco (Apache-2.0) are documented in
[THIRD-PARTY.md](THIRD-PARTY.md). Blender is driven as an external program, never linked or
redistributed, so its GPL does not reach this repository or the assets you build with it.
