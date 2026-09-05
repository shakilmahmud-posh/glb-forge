"""
Harvest a sourced .blend into the two-LOD shape the EventOS decor catalogue
wants — a light GLB the browser loads and a full one the renderer uses.

The judgment stays human: someone finds the asset, drags it in, deletes what
they don't want, saves. Everything from there is mechanical and lives here.

Deliberately NOT `workers/poc/convert_to_glb.py`. That one normalises to unit
height (right for the composer, wrong for a floor plan where a rose is 0.3 m
and an arch is 3 m) and has no decimation, because it was written for Poly
Haven hard-surface assets where Draco alone was enough. A rose is not that.

Order matters and is load-bearing: export the high LOD BEFORE downsizing
textures or decimating, because both of those mutate bpy.data in place.

Run:
    Blender --background --factory-startup -P builder/harvest.py -- \
        --source <in.blend> --slug red-rose-bouquet --out-dir staging/x \
        --budget 9000 [--keep om.cn_35] [--drop Cylinder] [--dry-run]
"""

import argparse
import json
import pathlib
import sys

import bpy
from mathutils import Vector


def parse_args():
    try:
        idx = sys.argv.index("--")
    except ValueError:
        idx = len(sys.argv)
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--budget", type=int, default=9000, help="low-LOD triangle ceiling")
    ap.add_argument("--keep", default="", help="comma-separated object names to keep")
    ap.add_argument("--drop", default="", help="comma-separated object names to remove")
    ap.add_argument("--hi-texture", type=int, default=2048)
    ap.add_argument("--lo-texture", type=int, default=512)
    ap.add_argument("--thumb-px", type=int, default=320)
    ap.add_argument(
        "--min-ratio", type=float, default=0.20,
        help="refuse a collapse below this fraction of the base cage (see decimate_to)",
    )
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args(sys.argv[idx + 1 :])


# ── scene ────────────────────────────────────────────────────────────────

def clear_scene():
    bpy.ops.wm.read_homefile(use_factory_startup=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)


def append_all(src: pathlib.Path):
    """Append every object, not a named collection.

    A sourced .blend may be a collection, loose objects, or someone's saved
    working scene with the parts they deleted already gone. Taking objects
    covers all three; requiring a collection name covers only the first.
    """
    with bpy.data.libraries.load(str(src), link=False) as (data_from, data_to):
        data_to.objects = list(data_from.objects)
    for obj in data_to.objects:
        if obj is not None:
            bpy.context.scene.collection.objects.link(obj)
    for img in bpy.data.images:
        if img.filepath and img.size[0] == 0:
            try:
                img.reload()
            except RuntimeError:
                pass


def meshes():
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def tri_count(obj) -> int:
    """Triangles after modifiers and geometry nodes — the number that ships.
    Counting `obj.data.polygons` would miss every procedural asset."""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    try:
        me = ev.to_mesh()
    except RuntimeError:
        return 0
    n = sum(max(0, len(p.vertices) - 2) for p in me.polygons)
    ev.to_mesh_clear()
    return n


def total_tris() -> int:
    return sum(tri_count(o) for o in meshes())


def apply_selection(keep: list[str], drop: list[str]):
    for obj in list(bpy.context.scene.objects):
        if drop and obj.name in drop:
            bpy.data.objects.remove(obj, do_unlink=True)
        elif keep and obj.type == "MESH" and obj.name not in keep:
            bpy.data.objects.remove(obj, do_unlink=True)


# ── normalise ────────────────────────────────────────────────────────────

def world_bounds():
    cs = [o.matrix_world @ Vector(c) for o in meshes() for c in o.bound_box]
    if not cs:
        return None, None
    return (
        Vector((min(c.x for c in cs), min(c.y for c in cs), min(c.z for c in cs))),
        Vector((max(c.x for c in cs), max(c.y for c in cs), max(c.z for c in cs))),
    )


def normalise():
    """Real-world scale is KEPT — only the origin moves.

    The catalogue positions by `footprintM`/`heightM`, so a rose must arrive
    0.3 m tall and an arch 3 m. Rescaling everything to unit height (what the
    composer converter does) would throw away the one measurement the floor
    plan needs.
    """
    lo, hi = world_bounds()
    if lo is None:
        return None
    cx, cy = (lo.x + hi.x) / 2, (lo.y + hi.y) / 2
    for obj in meshes():
        if obj.parent is None:
            obj.location.x -= cx
            obj.location.y -= cy
            obj.location.z -= lo.z
    lo2, hi2 = world_bounds()
    return {
        "widthM": round(hi2.x - lo2.x, 4),
        "depthM": round(hi2.y - lo2.y, 4),
        "heightM": round(hi2.z - lo2.z, 4),
    }


def downsize_textures(max_edge: int):
    """Halve until <= max_edge, and force 8-bit PNG/JPEG.

    Straight from the composer converter, including the reason: source assets
    ship roughness/normal maps as 32-bit EXR, Blender's AUTO mode re-exports
    them as 16-bit PNG, and GLTFLoader rejects those with "couldn't load
    texture blob:". Coercing the format is what makes them load at all.
    """
    for img in bpy.data.images:
        if img.size[0] == 0 or img.size[1] == 0:
            continue
        has_alpha = img.depth in (32, 64) and img.channels == 4
        img.file_format = "PNG" if has_alpha else "JPEG"
        if max(img.size) > max_edge:
            s = max_edge / max(img.size)
            img.scale(max(1, int(img.size[0] * s)), max(1, int(img.size[1] * s)))


def set_subsurf(level: int) -> bool:
    """Set every SUBSURF modifier's VIEWPORT level.

    Viewport, not render: Blender's glTF exporter evaluates the viewport
    depsgraph, so `levels` is what ships and `render_levels` is ignored.

    This is the finding the whole script turns on. This rose is a 113k-tri
    base cage under a subsurf that quadruples it to 459k. Collapse-decimating
    the SUBDIVIDED result to 29,860 triangles destroyed every flower — only
    stems survived. Decimating the BASE CAGE to 28,318 was indistinguishable
    from the 459k original. Same triangle count, opposite outcome: what
    matters is which mesh you decimate.
    """
    found = False
    for obj in meshes():
        for m in obj.modifiers:
            if m.type == "SUBSURF":
                m.levels = level
                found = True
    return found


def decimate_to(budget: int, min_ratio: float) -> dict:
    """Collapse-decimate every mesh by one shared ratio.

    COLLAPSE, not PLANAR: petals are curved thin surfaces with no coplanar
    regions to merge, so PLANAR would do nothing until it suddenly destroyed
    the silhouette. One shared ratio rather than per-object budgets keeps the
    parts in proportion — decimating a stem and a petal to the same absolute
    count makes the stem a stick figure.
    """
    before = total_tris()
    if before <= budget:
        return {"before": before, "after": before, "ratio": 1.0,
                "applied": False, "floored": False, "tooAggressive": False}
    ratio = budget / before
    # Ratio, not triangle count, is what decides whether a collapse survives.
    #
    # Measured on the red rose (2026-08-20), same asset, same cage, budget the
    # only variable: 0.106 destroyed it — petals collapse to flat facets and
    # leaves to dark shards; 0.148 reads correctly; 0.247 is indistinguishable
    # from the 113k original. The first of those SHIPPED, passed every gate,
    # and was caught only by a human looking at it in a viewer, because the
    # floor gate below asks "did the decimator reach the number I asked for",
    # never "is this still the thing".
    #
    # 0.20 is safe for the lane as it stands: every asset that shipped
    # successfully decimated at 0.68 or above (bud-vase-small and
    # golden-balloons needed none at all; floating-balloons took 0.68). The
    # rose at 0.106 was six times more aggressive than anything that worked.
    if ratio < min_ratio:
        return {"before": before, "after": before, "ratio": round(ratio, 5),
                "applied": False, "floored": False, "tooAggressive": True}
    for obj in meshes():
        mod = obj.modifiers.new("HarvestDecimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
    after = total_tris()
    # Collapse cannot merge across disconnected islands, so every mesh has a
    # floor. Asking for less than the floor does not fail — it silently
    # collapses thin geometry to slivers on the way down. Measured on this
    # rose: 11,327 tris was perfect, 10,491 (8% fewer, at the floor) was
    # destroyed. It is a cliff, not a gradient, so the only safe reading is
    # "did we actually reach what we asked for?"
    floored = after > budget * 1.05
    return {"before": before, "after": after, "ratio": round(ratio, 5),
            "applied": True, "floored": floored, "tooAggressive": False}


def export_glb(path: pathlib.Path, draco: bool):
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        export_apply=True,          # bakes modifiers, so the decimate ships
        export_yup=True,
        export_draco_mesh_compression_enable=draco,
        export_draco_mesh_compression_level=6 if draco else 0,
        export_image_format="AUTO",
        export_jpeg_quality=85,
    )


# ── thumbnail ────────────────────────────────────────────────────────────

def render_thumb(path: pathlib.Path, px: int):
    lo, hi = world_bounds()
    if lo is None:
        return
    size = max(0.05, max(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z))
    mid = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, (lo.z + hi.z) / 2))

    cam_data = bpy.data.cameras.new("ThumbCam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = size * 1.45
    cam = bpy.data.objects.new("ThumbCam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = mid + Vector((1.0, -1.4, 0.85)).normalized() * (size * 3)
    cam.rotation_euler = (cam.location - mid).to_track_quat("Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    # Raking key, not camera-axis: a light down the camera axis flattens
    # relief, which on layered petals reads as a grey blob (Forge P2 finding).
    for name, off, energy in [
        ("Key", (1.6, -1.2, 2.0), 90),
        ("Fill", (-1.8, -0.8, 0.9), 30),
        ("Rim", (0.0, 2.0, 1.6), 60),
    ]:
        ld = bpy.data.lights.new(name, type="AREA")
        ld.energy = energy * (size ** 2) * 12
        ld.size = size * 1.5
        lt = bpy.data.objects.new(name, ld)
        lt.location = mid + Vector(off) * size * 2
        lt.rotation_euler = (lt.location - mid).to_track_quat("Z", "Y").to_euler()
        bpy.context.scene.collection.objects.link(lt)

    # A vertical gradient, not a flat colour. The world IS the environment in
    # EEVEE, so a flat world gives a metal nothing to reflect and it renders as
    # its base colour: "Golden Balloons" (metallic 0.8) came out flat orange.
    # A bright top and darker bottom is enough of a horizon for metal to read
    # as metal, and it still looks like a clean studio backdrop.
    world = bpy.context.scene.world or bpy.data.worlds.new("W")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.35
    ramp.color_ramp.elements[0].color = (0.82, 0.82, 0.84, 1.0)
    ramp.color_ramp.elements[1].position = 0.85
    ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 1.0
    nt.links.new(coord.outputs["Generated"], sep.inputs["Vector"])
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], nt.nodes.new("ShaderNodeOutputWorld").inputs["Surface"])

    scn = bpy.context.scene
    engines = [e.identifier for e in scn.render.bl_rna.properties["engine"].enum_items]
    scn.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    scn.view_settings.view_transform = "Standard"
    scn.render.resolution_x = scn.render.resolution_y = px
    scn.render.image_settings.file_format = "PNG"
    scn.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)

    for o in (cam, *[o for o in bpy.context.scene.objects if o.type == "LIGHT"]):
        bpy.data.objects.remove(o, do_unlink=True)


def dominant_color(thumb: pathlib.Path) -> str:
    """Average the asset's pixels out of its own thumbnail.

    The catalogue's `fallbackColor` is what a placement shows while the GLB
    streams in, and it is also what renders if the GLB never loads. Leaving it
    at the default beige makes a red rose flash grey-brown on every plan open.

    Read back as Non-Color: the thumbnail was written through the Standard view
    transform, so it is already display-referred, and letting Blender apply
    sRGB→linear a second time would wash the sample out.
    """
    try:
        img = bpy.data.images.load(str(thumb))
    except RuntimeError:
        return "#c8c0b4"
    img.colorspace_settings.name = "Non-Color"
    px = list(img.pixels)
    w, h = img.size
    tot = [0.0, 0.0, 0.0]
    n = 0
    step = max(1, (w * h) // 20000)      # subsample; exactness buys nothing here
    for i in range(0, w * h, step):
        j = i * 4
        r, g, b, a = px[j], px[j + 1], px[j + 2], px[j + 3]
        if a < 0.5:
            continue
        # The backdrop is a light neutral GRADIENT, so it cannot be matched
        # against one colour any more. Reject bright near-greys instead:
        # anything pale and unsaturated is backdrop, not asset.
        mx, mn = max(r, g, b), min(r, g, b)
        if mx > 0.78 and (mx - mn) < 0.05:
            continue
        tot[0] += r; tot[1] += g; tot[2] += b; n += 1
    bpy.data.images.remove(img)
    if n == 0:
        return "#c8c0b4"
    return "#" + "".join(f"{min(255, max(0, int(c / n * 255))):02x}" for c in tot)


# ── main ─────────────────────────────────────────────────────────────────

def main() -> int:
    a = parse_args()
    src = pathlib.Path(a.source).resolve()
    out = pathlib.Path(a.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    clear_scene()
    append_all(src)
    apply_selection(
        [s for s in a.keep.split(",") if s],
        [s for s in a.drop.split(",") if s],
    )
    if not meshes():
        print("harvest: nothing to export — every mesh was filtered out", file=sys.stderr)
        return 2

    per_object = {o.name: tri_count(o) for o in meshes()}
    print("OBJECTS:", json.dumps(per_object))
    if a.dry_run:
        print("TOTAL_TRIS:", sum(per_object.values()))
        return 0

    dims = normalise()
    print("DIMS:", json.dumps(dims))

    # HIGH first — downsizing and decimating both mutate bpy.data in place.
    #
    # High LOD keeps the artist's full base cage and does NOT re-subdivide.
    # Subsurf at level 1 costs 4x the triangles for smoothing that is invisible
    # on a 0.5 m prop in a venue-wide shot, and pushed the export to 20 MB.
    had_subsurf = set_subsurf(0)
    print("SUBSURF:", json.dumps({"present": had_subsurf, "level": 0}))
    downsize_textures(a.hi_texture)
    hi = out / f"{a.slug}.hi.glb"
    export_glb(hi, draco=False)
    render_thumb(out / f"{a.slug}.hi.png", a.thumb_px)

    downsize_textures(a.lo_texture)
    dec = decimate_to(a.budget, a.min_ratio)
    print("DECIMATE:", json.dumps(dec))
    if dec.get("tooAggressive"):
        print(
            f"harvest: {a.slug}: --budget {a.budget:,} is {dec['ratio']:.1%} of the "
            f"{dec['before']:,}-triangle base cage. Below {a.min_ratio:.0%} a collapse "
            f"flattens curved detail into facets — it does not fail, it just stops "
            f"looking like the thing. Raise --budget to at least "
            f"{int(dec['before'] * a.min_ratio) + 1:,}, or pass --min-ratio if you "
            f"have looked at the result in a viewer and it is genuinely fine.",
            file=sys.stderr,
        )
        return 1
    if dec["floored"]:
        print(
            f"harvest: {a.slug}: asked for {a.budget:,} tris, the mesh floors at "
            f"{dec['after']:,}. Collapse near the floor shreds thin geometry — "
            f"raise --budget to at least {int(dec['after'] * 1.15):,}.",
            file=sys.stderr,
        )
        return 1
    lo = out / f"{a.slug}.glb"
    export_glb(lo, draco=True)
    thumb = out / f"{a.slug}.png"
    render_thumb(thumb, a.thumb_px)
    colour = dominant_color(thumb)
    print("COLOR:", colour)

    report = {
        "slug": a.slug,
        "source": str(src),
        "dims": dims,
        "objects": per_object,
        "decimate": dec,
        "subsurf_flattened": had_subsurf,
        "bytes": {"hi": hi.stat().st_size, "lo": lo.stat().st_size},
        "budget": a.budget,
        "minRatio": a.min_ratio,
        "dominantColor": colour,
    }
    (out / "harvest.json").write_text(json.dumps(report, indent=2))
    print("REPORT:", json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
