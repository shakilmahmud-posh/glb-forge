# SPEC_GUIDE — forgeSpec v1

How to author an asset spec any vision-capable AI can write and the builder renders
deterministically. Formal schema: `schema/forge-spec.schema.json`. Enforcement:
`./forge validate` (also applies cross-field rules the schema can't express).

## Conventions (normative — geometry that ignores these fails gates)

- Units **metres**, angles **degrees** (XYZ Euler).
- Coordinates **Y-up, right-handed** (glTF/three.js). The builder handles Blender's Z-up
  internally; what you write is what the GLB contains.
- Origin = **base centre**: all geometry in `y ≥ 0`, lowest point at `y = 0`. Holds for
  ceiling assets too (the consumer lifts the base to the placed height; a hanging drape's
  lowest sag point is its `y = 0`). Gate: `|baseY| ≤ 0.01`.
- **+Z is the front** of the asset. Chairs: backrest at −Z,
  seat faces +Z.
- Flat part list, asset-local transforms. No parent nesting (`array` covers repeats).
- Slugs and part names kebab-case; slugs unique across your library.

## Top level

```jsonc
{
  "forgeSpec": 1,
  "slug": "x-banner", // kebab-case, unique
  "label": "X-banner",
  "detail": "low", // "low" ≤3k tris/1.5MB · "mid" ≤15k/4MB (no "high")
  "referenceImages": ["references/x-banner.jpg"], // optional, relative to the spec dir
  "consumerHints": {
    "eventos": {/* below */},
  },
  "materials": {/* 1..4 named materials */},
  "parts": [/* primitives */],
  "pictureSurface": { "part": "panel", "face": "front" }, // optional; component-kind only
}
```

`consumerHints.eventos`: `kind` `"decor"|"component"` · `componentType` (snake_case,
required when component) · `category` (décor picker group, required when decor) ·
`footprintM` (largest horizontal edge at scale 1 — the footprint **gate** checks built
bounds against this ±10%) · `heightM` · `defaultHeightM` (décor only: metres off floor at
drop) · `placement` `"floor"|"ceiling"|"wall"` (default floor).

`pictureSurface` marks where planners' uploaded pictures should default. It must target a
**box** part and only works for `kind: "component"`. This is consumer-specific: in the Event OS
consumer (the worked example throughout this guide), "looks" bind to floor-plan components and
never to décor items. Your own consumer can define whatever hints it needs — see
`consumerHints` below.

## Parts

Every part: `name` (kebab-case, unique), `type`, `position` `[x,y,z]` (required),
`rotationDeg` `[x,y,z]` (optional, default `[0,0,0]`), `material` (must reference a
declared material; `array` carries the material on its inline `of` part instead).

| type       | required fields                                                                                                                              | semantics                                                                                                                                                                                                                                                      |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `box`      | `size:[x,y,z]` (+`bevelM?`)                                                                                                                  | centred on its position; bevel = edge bevel width                                                                                                                                                                                                              |
| `cylinder` | `radiusTop`, `radiusBottom`, `heightM` (+`segments?` 16)                                                                                     | axis = local Y, centred on position; cone via `radiusTop: 0`                                                                                                                                                                                                   |
| `plane`    | `size:[w,h]`                                                                                                                                 | upright in local XY, facing +Z; material must be doubleSided                                                                                                                                                                                                   |
| `lathe`    | `profile:[[r,y],…]` ≥2 pts (+`segments?` 16)                                                                                                 | revolve profile around local Y — vases, legs, poles                                                                                                                                                                                                            |
| `drape`    | `from:[x,y,z]`, `to:[x,y,z]`, `sagM`, `widthM` (+`segmentsAlong?` 24, `segmentsAcross?` 4, `gatherEnds?`, `wrinkle?:{amplitudeM,seed}`)      | catenary **ribbon** between two points: fabric width across, sag down, pinched ends when gathered, seeded wrinkles. THE swag primitive. Material must be doubleSided. `from`/`to` are asset-local — position stays `[0,0,0]` unless offsetting the whole drape |
| `array`    | `of:` inline part (not array), `mode:"linear"\|"radial"`, `count` 2..256; linear: `spacing:[x,y,z]`; radial: `radiusM`, `startDeg`, `endDeg` | stamps repeats of `of` starting at the array part's transform; radial rotates each copy to face outward                                                                                                                                                        |

Radial array step: when `endDeg − startDeg` is a multiple of 360 the circle is divided by
`count` (no duplicate at the seam); otherwise the arc is inclusive — copies at
`startDeg + i·(span/(count−1))`. Positive angles rotate +Z toward +X around Y. Radial
copies are offset `radiusM` along their rotated +Z, so each copy's front faces outward.

Drape authoring: `from`/`to` are the ANCHOR points; the fabric hangs below them
(parabolic sag — visually a catenary at swag ratios). The lowest point lands roughly
`sagM + 1.9·amplitudeM + 0.12·widthM` below the anchor line — set anchor y near that sum,
build, then tune anchor y by the exact `report.baseY` (deterministic, so tuned once =
stable). Wrinkles are 4 longitudinal folds that meander along the span plus a scalloped
hem at the width edges, all phase-seeded and downward-biased; the drape is smooth-shaded
(everything else stays faceted). Bigger `amplitudeM` = deeper folds; ~0.15·widthM reads
well. Prefer 24–28 `segmentsAlong` and 8 `segmentsAcross` for hero swags.

NOT in v1 (decline, don't improvise): booleans, arbitrary curve sweeps, mirror,
image textures/UVs, particles/scatter (florals), parented hierarchies.

## Materials

Flat PBR, ≤ 4 per asset: `color` `#rrggbb` (required), `roughness` 0..1, `metalness` 0..1,
`emissive` `#rrggbb`, `doubleSided`. `doubleSided: true` is **required** on any material
used by a `plane` or `drape` (backfaces must render). No image textures — in the Event OS
consumer, pictures are applied by its look system at runtime rather than baked into the GLB.

## Budgets & gates (report.json)

`forge build` writes `staging/<slug>/report.json`; read it, fix, retry. Gates: `budget`
(tris), `footprint` (±10% of footprintM), `baseY` (≤ 0.01), `materials` (≤ 4; sets ≤ 12
after dedupe), `fileSize`, `geometry` (non-empty, no NaN). Exit 1 = gate fail with
`failures[]` naming each. Budgets: low 3 000 tris / 1.5 MB · mid 15 000 / 4 MB ·
high 60 000 / 10 MB (hero pieces and sets).
Keep `segments` low — 8–12 reads fine at event-mockup distance; 16 is plenty.

## Sets — compose committed specs into one asset

A set spec has `members` INSTEAD of `materials`/`parts` (mutually exclusive):

```json
{
  "forgeSpec": 1,
  "slug": "awards-stage-set",
  "label": "…",
  "detail": "high",
  "consumerHints": {
    "eventos": {
      "kind": "decor",
      "category": "structures",
      "footprintM": 14.0,
      "heightM": 5.2,
      "defaultHeightM": 0,
      "placement": "floor"
    }
  },
  "members": [
    { "spec": "stage-riser", "as": "deck-l", "position": [-4.04, 0, 0] },
    {
      "spec": "led-screen-side",
      "as": "screen-r",
      "position": [4.4, 0.9, -0.7],
      "rotationYDeg": -18
    }
  ]
}
```

- `spec` names a committed spec (your spec dir, or `examples/`); repeats need a unique `as`.
- `position` is metres in set space; `rotationYDeg` spins the member about Y.
  Members stack: something placed ON the deck gets `y = deck height`.
- ≤ 24 members, no nesting (a member cannot itself be a set).
- The CLI **flattens** before Blender: parts get namespaced (`<as>-<part>`), transforms
  composed, and byte-identical material defs **deduped across members** — author shared
  palettes with identical values so the set stays ≤ 12 materials. `report.specSha256`
  is the SET spec's sha (the committed source of truth); `staging/<slug>/flattened.json`
  is left beside the GLB for debugging.
- Decompose-a-photo workflow: author each part of a detailed scene (stage, booth,
  entrance…) as its own gated member spec — reusing committed ones where possible —
  then place them in one set. Golden example: `examples/demo-set.json`. In production
  this scales to sets of ~15 members decomposed from a single venue photograph.

## Worked example — x-banner (`examples/x-banner.json`)

Two crossed poles + a fabric panel. The maths that made it pass gates: pole length 1.78 m
tilted 21.5° → vertical extent 1.78·cos 21.5° ≈ 1.656 (centre y 0.828 puts base at 0,
top ≈ heightM 1.65 ✓) and horizontal extent 1.78·sin 21.5° ≈ 0.652 ≈ footprintM 0.65 ✓.
Poles at z −0.045/−0.03 so they don't z-fight at the crossing; panel is a thin **box**
(not plane) so `pictureSurface: {part: "panel", face: "front"}` works.

## Image → spec protocol (the discipline that makes weaker models productive)

Start with `./forge from-image <photo> --slug <slug>` — it files the reference under
`<spec-dir>/references/` and scaffolds a valid spec with `referenceImages` set. Then:

1. **Dimensions first.** Estimate real-world size from context (standee ≈ 1.6–2 m tall;
   banquet table 0.75 m high). STATE your assumptions in the spec's `label`/commit message.
2. **Decompose** into ≤ 10 primitives, biggest silhouette element first.
3. **Palette:** extract ≤ 4 colours from the image → materials.
4. Edit the spec → `./forge build <slug>` → compare `thumb.png` against the reference
   **side by side**.
5. Iterate ≤ 3, in this order: silhouette → proportions → palette.

When the batch is ready, `./forge sheet` renders `staging/contact-sheet.html` with the
reference beside each render — that's what the human approves from. In practice a plinth or
pedestal rebuilt from a single venue photograph converges in about two iterations.

Honest limit: stylized reconstruction at event-mockup fidelity, not photogrammetry.
Organic subjects (floral arches, plants) exceed v1 — decline and log; don't fake it.

## Visual self-review checklist (every build, before staging)

Open `thumb.png` and check: silhouette reads as the object at a glance · proportions
plausible vs real-world dims · palette matches intent/reference · no obvious z-fighting,
gaps, or floating parts · front (+Z) is actually the front. ≤ 3 revise-rebuild cycles,
then stage for the human sheet.

## Common validate failures

`fabric_material_must_be_double_sided` → add `"doubleSided": true` ·
`picture_surface_requires_component` / `_part_not_box` → see pictureSurface rules above ·
`unknown_field` → v1 is strict; you probably want a different primitive or it's not in v1 ·
`missing_field` on drape → `sagM`/`widthM` are required · `too_many_materials` → merge to ≤ 4.
