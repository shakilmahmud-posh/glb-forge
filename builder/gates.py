"""gates.py — geometry/budget checks → report.json (plan §Quality gates).

Pure python (no bpy): builder.py collects raw metrics inside Blender and this module
evaluates the gate contract, so gate logic is unit-testable outside Blender.

Gate values: "pass" | "fail" | "skip" (skip = not applicable, e.g. no eventos hints).
Watertightness NOT required — panels/fabric are open meshes by design.
"""

BUDGETS = {"low": {"tris": 3000, "bytes": 1_500_000}, "mid": {"tris": 15000, "bytes": 4_000_000},
           "high": {"tris": 60000, "bytes": 10_000_000}}
FOOTPRINT_TOLERANCE = 0.10
BASE_Y_TOLERANCE_M = 0.01
MAX_MATERIALS = 4
MAX_SET_MATERIALS = 12  # flattened sets (spec carries `_set`) — after dedupe


def run_gates(spec, metrics, file_bytes, forge_version, spec_sha256, built_at):
    """metrics: {triCount, vertCount, boundsM:[x,y,z], baseY, hasNaN, materialCount}.

    Returns the report.json dict — machine-readable; the driving AI reads it and retries.
    """
    detail = spec["detail"]
    budget = BUDGETS[detail]
    gates = {}
    failures = []

    def gate(name, ok, fail_msg="", skip=False):
        gates[name] = "skip" if skip else ("pass" if ok else "fail")
        if not skip and not ok:
            failures.append(fail_msg)

    tri = metrics["triCount"]
    gate("budget", tri <= budget["tris"],
         f"budget: {tri} tris exceeds {detail} budget {budget['tris']}")

    hints = (spec.get("consumerHints") or {}).get("eventos")
    if isinstance(hints, dict) and "footprintM" in hints:
        horiz = max(metrics["boundsM"][0], metrics["boundsM"][2])
        target = hints["footprintM"]
        gate("footprint", abs(horiz - target) <= FOOTPRINT_TOLERANCE * target,
             f"footprint: horizontal extent {horiz:.3f}m outside footprintM "
             f"{target}m ±{int(FOOTPRINT_TOLERANCE * 100)}%")
    else:
        gate("footprint", True, skip=True)

    base = metrics["baseY"]
    gate("baseY", abs(base) <= BASE_Y_TOLERANCE_M,
         f"baseY: lowest point y={base:.4f}m — origin must be base centre "
         f"(|baseY| ≤ {BASE_Y_TOLERANCE_M})")

    mat_cap = MAX_SET_MATERIALS if spec.get("_set") else MAX_MATERIALS
    gate("materials", metrics["materialCount"] <= mat_cap,
         f"materials: {metrics['materialCount']} > {mat_cap}")

    gate("fileSize", file_bytes <= budget["bytes"],
         f"fileSize: {file_bytes} bytes exceeds {detail} budget {budget['bytes']}")

    gate("geometry",
         metrics["vertCount"] > 0 and tri > 0 and not metrics["hasNaN"],
         "geometry: empty mesh or NaN vertices")

    return {
        "slug": spec["slug"],
        "pass": not failures,
        "triCount": tri,
        "budget": budget["tris"],
        "boundsM": [round(x, 4) for x in metrics["boundsM"]],
        "baseY": round(base, 4),
        "materialCount": metrics["materialCount"],
        "fileBytes": file_bytes,
        "gates": gates,
        "failures": failures,
        "forgeVersion": forge_version,
        "specSha256": spec_sha256,
        "builtAt": built_at,
    }
