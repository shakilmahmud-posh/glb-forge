"""builder.py — runs INSIDE Blender (bpy + stdlib only). Invoked by `forge build`:

  Blender --factory-startup -b --python-exit-code 3 --python builder/builder.py -- \
      --spec <path> --out <staging/<slug>> --repo <repo-root>

Coordinate strategy: geometry is authored DIRECTLY in spec space (metres, Y-up,
right-handed, +Z front) and exported with export_yup=False, so spec coordinates and
rotations pass through to the GLB verbatim — no axis-conversion maths anywhere.
Blender doesn't care that "up" is Y here; only the exporter flag matters.

Determinism: empty factory scene, fixed construction order (spec order), no unseeded
randomness, fixed export settings → same spec = byte-identical GLB (tests enforce).
glTF scene extras carry {forgeVersion, specSha256}; builtAt lives in report.json ONLY
(a timestamp inside the GLB would break byte-identical rebuilds).

Exit codes: 0 pass · 1 gate fail · 3 environment/internal error.
"""

import argparse
import hashlib
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

FORGE_VERSION = "0.1.0"
TAU = 2.0 * math.pi


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- colours

def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear(hex_str):
    return tuple(_srgb_to_linear(int(hex_str[i:i + 2], 16) / 255.0) for i in (1, 3, 5))


def make_material(name, mdef):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*hex_to_linear(mdef["color"]), 1.0)
    bsdf.inputs["Roughness"].default_value = float(mdef.get("roughness", 0.8))
    bsdf.inputs["Metallic"].default_value = float(mdef.get("metalness", 0.0))
    if mdef.get("emissive"):
        key = "Emission Color" if "Emission Color" in bsdf.inputs else "Emission"
        bsdf.inputs[key].default_value = (*hex_to_linear(mdef["emissive"]), 1.0)
        bsdf.inputs["Emission Strength"].default_value = 1.0
    # glTF doubleSided = not use_backface_culling; default single-sided
    mat.use_backface_culling = not mdef.get("doubleSided", False)
    return mat


# ---------------------------------------------------------------- geometry

def local_matrix(part):
    pos = Vector(part.get("position", [0.0, 0.0, 0.0]))
    rx, ry, rz = (math.radians(a) for a in part.get("rotationDeg", [0.0, 0.0, 0.0]))
    return Matrix.Translation(pos) @ Euler((rx, ry, rz), "XYZ").to_matrix().to_4x4()


def geometry_bmesh(part):
    """Part-local geometry per SPEC_GUIDE semantics (Y-up, +Z front)."""
    t = part["type"]
    bm = bmesh.new()

    if t == "box":
        bmesh.ops.create_cube(bm, size=1.0)
        sx, sy, sz = part["size"]
        bm.transform(Matrix.Diagonal((sx, sy, sz, 1.0)))
        bevel = part.get("bevelM", 0)
        if bevel > 0:
            off = min(bevel, 0.45 * min(sx, sy, sz))
            bmesh.ops.bevel(bm, geom=list(bm.edges), offset=off, offset_type="OFFSET",
                            segments=1, profile=0.5, affect="EDGES", clamp_overlap=True)

    elif t == "cylinder":
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False,
                              segments=part.get("segments", 16),
                              radius1=part["radiusBottom"], radius2=part["radiusTop"],
                              depth=part["heightM"])
        bm.transform(Matrix.Rotation(-math.pi / 2.0, 4, "X"))  # native Z axis → local Y
        if part["radiusTop"] == 0 or part["radiusBottom"] == 0:
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-7)

    elif t == "plane":
        w, h = part["size"]
        vs = [bm.verts.new(v) for v in ((-w / 2, -h / 2, 0.0), (w / 2, -h / 2, 0.0),
                                        (w / 2, h / 2, 0.0), (-w / 2, h / 2, 0.0))]
        bm.faces.new(vs)  # CCW from +Z → normal faces front; keep authored orientation

    elif t == "lathe":
        prof = part["profile"]
        verts = [bm.verts.new((r, y, 0.0)) for r, y in prof]
        edges = [bm.edges.new((verts[i], verts[i + 1])) for i in range(len(verts) - 1)]
        bmesh.ops.spin(bm, geom=verts + edges, cent=(0.0, 0.0, 0.0), axis=(0.0, 1.0, 0.0),
                       dvec=(0.0, 0.0, 0.0), angle=TAU, steps=part.get("segments", 16),
                       use_merge=True, use_normal_flip=False, use_duplicate=False)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)  # weld r=0 poles

    elif t == "drape":
        # Catenary-style ribbon (parabolic sag — visually equivalent at swag ratios and
        # robust for unequal-height anchors). Lowest point lands roughly
        # sagM + wrinkle.amplitudeM + 0.12·widthM below the anchor line; authors tune
        # anchor y against report.baseY (deterministic, so tuned once = stable).
        fr = Vector(part["from"])
        to = Vector(part["to"])
        sag = part["sagM"]
        width = part["widthM"]
        na = part.get("segmentsAlong", 24)
        nc = part.get("segmentsAcross", 4)
        gather = part.get("gatherEnds", False)
        wr = part.get("wrinkle")
        amp = wr["amplitudeM"] if wr else 0.0
        rng = random.Random(wr["seed"]) if wr else None
        ph_fold = rng.uniform(0.0, TAU) if rng else 0.0
        ph_meander = rng.uniform(0.0, TAU) if rng else 0.0
        ph_hem = rng.uniform(0.0, TAU) if rng else 0.0
        folds = 4
        horiz = Vector((to.x - fr.x, 0.0, to.z - fr.z))
        wdir = (horiz.normalized().cross(Vector((0.0, 1.0, 0.0))).normalized()
                if horiz.length > 1e-9 else Vector((1.0, 0.0, 0.0)))
        rows = []
        for i in range(na + 1):
            tt = i / na
            centre = fr.lerp(to, tt) + Vector((0.0, -4.0 * sag * tt * (1.0 - tt), 0.0))
            taper = math.sin(math.pi * tt)  # anchors stay clean, effects peak mid-span
            pinch = (0.18 + 0.82 * taper ** 0.6) if gather else 1.0
            row = []
            for j in range(nc + 1):
                u = 2.0 * j / nc - 1.0
                p = centre + wdir * (u * width * 0.5 * pinch)
                dy = -0.12 * width * (1.0 - u * u) * taper  # cross-section belly
                if amp:
                    # longitudinal folds that meander along the span (downward-biased)
                    fold = math.sin(u * math.pi * folds + ph_fold
                                    + 0.9 * math.sin(TAU * tt + ph_meander))
                    dy -= amp * (0.5 + 0.5 * fold) * taper
                    # scalloped hem: width edges droop in waves along the length
                    dy -= amp * 0.9 * abs(u) ** 3 * taper * \
                        (0.5 + 0.5 * math.sin(TAU * tt * 3.0 + ph_hem))
                row.append(bm.verts.new((p.x, p.y + dy, p.z)))
            rows.append(row)
        for i in range(na):
            for j in range(nc):
                f = bm.faces.new((rows[i][j], rows[i + 1][j], rows[i + 1][j + 1], rows[i][j + 1]))
                f.smooth = True  # fabric shades soft; everything else stays faceted low-poly

    else:
        raise BuildError(f"unhandled primitive '{t}'")

    if t not in ("plane", "drape"):  # open meshes keep authored orientation
        bm.normal_update()
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    return bm


def array_bmesh(part):
    """Stamp copies of the inline `of` part into one mesh (one draw call per array)."""
    base = geometry_bmesh(part["of"])
    base.transform(local_matrix(part["of"]))
    count = part["count"]
    acc = bmesh.new()
    tmp = bpy.data.meshes.new("forge-tmp")
    for i in range(count):
        if part["mode"] == "linear":
            m = Matrix.Translation(Vector(part["spacing"]) * i)
        else:
            span = part["endDeg"] - part["startDeg"]
            rem = abs(span) % 360.0
            full = math.isclose(rem, 0.0, abs_tol=1e-9) or math.isclose(rem, 360.0, abs_tol=1e-9)
            step = span / count if full else span / (count - 1)
            ang = math.radians(part["startDeg"] + step * i)
            # rotate-then-offset: copy sits at angle around Y, +Z pointing outward
            m = Matrix.Rotation(ang, 4, "Y") @ Matrix.Translation((0.0, 0.0, part["radiusM"]))
        copy = base.copy()
        copy.transform(m)
        copy.to_mesh(tmp)
        copy.free()
        acc.from_mesh(tmp)
    base.free()
    bpy.data.meshes.remove(tmp)
    return acc


# ---------------------------------------------------------------- build + metrics

def build(spec, out_dir, spec_sha):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    mats = {n: make_material(n, d) for n, d in spec["materials"].items()}

    objs = []
    for part in spec["parts"]:
        if part["type"] == "array":
            bm = array_bmesh(part)
            mat = mats[part["of"]["material"]]
        else:
            bm = geometry_bmesh(part)
            mat = mats[part["material"]]
        me = bpy.data.meshes.new(part["name"])
        bm.to_mesh(me)
        bm.free()
        me.materials.append(mat)
        obj = bpy.data.objects.new(part["name"], me)
        obj.location = Vector(part.get("position", [0.0, 0.0, 0.0]))
        obj.rotation_euler = Euler(
            [math.radians(a) for a in part.get("rotationDeg", [0.0, 0.0, 0.0])], "XYZ")
        scene.collection.objects.link(obj)
        objs.append(obj)

    bpy.context.view_layer.update()

    scene["forgeVersion"] = FORGE_VERSION
    scene["specSha256"] = spec_sha
    glb = out_dir / f"{spec['slug']}.glb"
    bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB",
                              export_yup=False, export_apply=True, export_extras=True)
    return objs, glb


def collect_metrics(objs):
    tris = verts = 0
    has_nan = False
    mins = [math.inf] * 3
    maxs = [-math.inf] * 3
    mat_names = set()
    for obj in objs:
        me = obj.data
        me.calc_loop_triangles()
        tris += len(me.loop_triangles)
        verts += len(me.vertices)
        mw = obj.matrix_world
        for v in me.vertices:
            co = mw @ v.co
            for i in range(3):
                if not math.isfinite(co[i]):
                    has_nan = True
                else:
                    mins[i] = min(mins[i], co[i])
                    maxs[i] = max(maxs[i], co[i])
        for m in me.materials:
            if m:
                mat_names.add(m.name)
    if verts == 0 or math.inf in mins:
        mins = maxs = [0.0, 0.0, 0.0]
    bounds = [maxs[i] - mins[i] for i in range(3)]
    return {"triCount": tris, "vertCount": verts, "boundsM": bounds,
            "baseY": mins[1], "hasNaN": has_nan, "materialCount": len(mat_names),
            "minsM": list(mins), "maxsM": list(maxs)}


BG_HEX = "#f2ede4"
THUMB_DIR = (0.9, 0.55, 1.25)   # three-quarter: front-right, elevated
FRONT_DIR = (0.0, 0.0, 1.0)     # straight-on +Z
FIT_MARGIN = 1.15
RENDER_SAMPLES = 32


def render_thumbs(out_dir, metrics):
    """thumb.png + front.png in the same Blender run — fixed rig so sheets compare."""
    scene = bpy.context.scene
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = RENDER_SAMPLES
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.view_settings.view_transform = "Standard"  # keep bg/palette colours true

    world = bpy.data.worlds.new("forge-bg")
    world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs[0].default_value = (*hex_to_linear(BG_HEX), 1.0)
    bg.inputs[1].default_value = 1.0
    scene.world = world

    def sun(name, from_dir, energy):
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        ob = bpy.data.objects.new(name, light)
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = Vector(from_dir).to_track_quat("Z", "Y")
        scene.collection.objects.link(ob)

    # key rakes from upper-left ACROSS the thumb camera axis (front-lighting from the
    # camera direction flattens all surface relief — folds/bevels vanish)
    sun("forge-key", (-1.0, 1.3, 0.5), 1.5)
    sun("forge-fill", (1.2, 0.4, 1.0), 0.35)
    sun("forge-rim", (0.3, 1.0, -1.2), 0.55)

    cam_data = bpy.data.cameras.new("forge-cam")
    cam = bpy.data.objects.new("forge-cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    mins, maxs = Vector(metrics["minsM"]), Vector(metrics["maxsM"])
    centre = (mins + maxs) / 2.0
    radius = max((maxs - mins).length / 2.0, 0.05)

    def look_at_quat(cam_pos, target):
        # explicit basis: to_track_quat aligns "up" toward world +Z (Blender-native),
        # but our scenes are Y-up-authored — that rolled the camera 180°
        f = (target - cam_pos).normalized()
        r = f.cross(Vector((0.0, 1.0, 0.0)))
        if r.length < 1e-6:
            r = Vector((1.0, 0.0, 0.0))
        r.normalize()
        u = r.cross(f)
        return Matrix(((r.x, u.x, -f.x),
                       (r.y, u.y, -f.y),
                       (r.z, u.z, -f.z))).to_quaternion()

    def shoot(direction, path):
        d = Vector(direction).normalized()
        dist = radius / math.sin(cam_data.angle / 2.0) * FIT_MARGIN
        cam.location = centre + d * dist
        cam.rotation_mode = "QUATERNION"
        cam.rotation_quaternion = look_at_quat(cam.location, centre)
        cam_data.clip_start = 0.01
        cam_data.clip_end = dist + radius * 4.0
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)

    shoot(THUMB_DIR, out_dir / "thumb.png")
    shoot(FRONT_DIR, out_dir / "front.png")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", required=True)
    # sets: the CLI passes the sha of the COMMITTED set spec (the file we read is
    # the derived flattened.json — its sha is not the source of truth)
    ap.add_argument("--spec-sha", default=None)
    a = ap.parse_args(argv)

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = Path(a.spec).read_bytes()
    spec = json.loads(raw)
    spec_sha = a.spec_sha or hashlib.sha256(raw).hexdigest()

    sys.path.insert(0, str(Path(a.repo) / "builder"))
    import gates

    try:
        objs, glb = build(spec, out_dir, spec_sha)
    except BuildError as e:
        print(f"forge-builder: ERROR {e}", file=sys.stderr)
        sys.exit(3)

    metrics = collect_metrics(objs)
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = gates.run_gates(spec, metrics, glb.stat().st_size,
                             FORGE_VERSION, spec_sha, built_at)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")

    render_thumbs(out_dir, metrics)  # after report + export: rig never touches the GLB

    status = "PASS" if report["pass"] else "FAIL — " + "; ".join(report["failures"])
    print(f"forge-builder: {spec['slug']} → {glb.name} · {report['triCount']} tris · "
          f"{report['fileBytes']} B · {status}")
    sys.exit(0 if report["pass"] else 1)


main()
