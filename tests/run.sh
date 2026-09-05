#!/usr/bin/env bash
# Blender Forge test suite.
# P0: doctor, golden validate, schema negatives.
# P1: golden build vs committed expectations (±0 tris), determinism (byte-identical GLB),
#     doctor version-mismatch simulation (FORGE_BLENDER override).
# P3: contact sheet content, from-image scaffold round-trip.
# P4: import décor lane round-trip against a temp consumer (env overrides).
# Review UI: server smoke — staged listing, decision round-trip, path safety.
# P6: the harvest collapse-ratio guard, on a sphere this suite generates itself.
set -u
cd "$(dirname "$0")/.."
FAIL=0

echo "[1/10] doctor"
if ./forge doctor > /dev/null 2>&1; then
  echo "  ✓ doctor exit 0"
else
  echo "  ✗ doctor failed:"; ./forge doctor; FAIL=$((FAIL + 1))
fi

echo "[2/10] golden specs validate"
for f in examples/*.json specs/*.json; do
  [ -f "$f" ] || continue
  if ./forge validate "$f" > /dev/null 2>&1; then
    echo "  ✓ $f"
  else
    echo "  ✗ $f should validate:"; ./forge validate "$f"; FAIL=$((FAIL + 1))
  fi
done

echo "[3/10] schema negatives (exit 2 + expected failure code)"
python3 - <<'PY' || FAIL=$((FAIL + 1))
import json, pathlib, subprocess, sys

expected = json.loads(pathlib.Path("tests/negative/expected.json").read_text())
bad = 0
for fname, want in expected.items():
    p = subprocess.run(["./forge", "validate", f"tests/negative/{fname}"],
                       capture_output=True, text=True)
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        out = {}
    codes = [x["code"] for x in out.get("failures", [])]
    if p.returncode == 2 and want in codes:
        print(f"  ✓ {fname} → exit 2, {want}")
    else:
        print(f"  ✗ {fname}: exit {p.returncode}, codes {codes} (want {want})")
        bad += 1
sys.exit(1 if bad else 0)
PY

echo "[4/10] golden builds vs committed report expectations (+ thumbnails exist)"
for exp in tests/golden/*.expected.json; do
  slug=$(basename "$exp" .expected.json)
  rm -rf "staging/$slug"
  if ! ./forge build "examples/$slug.json" > /dev/null 2>&1; then
    echo "  ✗ $slug build failed:"; ./forge build "examples/$slug.json"; FAIL=$((FAIL + 1))
    continue
  fi
  SLUG="$slug" python3 - <<'PY' || FAIL=$((FAIL + 1))
import json, os, pathlib, sys
slug = os.environ["SLUG"]
rep = json.loads(pathlib.Path(f"staging/{slug}/report.json").read_text())
exp = json.loads(pathlib.Path(f"tests/golden/{slug}.expected.json").read_text())
bad = 0
for k, v in exp.items():
    got = rep.get(k)
    mark = "✓" if got == v else "✗"
    print(f"  {mark} {slug}: report.{k} = {got}" + ("" if got == v else f" (want {v})"))
    bad += got != v
for png in ("thumb.png", "front.png"):
    p = pathlib.Path(f"staging/{slug}/{png}")
    ok = p.is_file() and p.stat().st_size > 1000
    print(f"  {'✓' if ok else '✗'} {slug}: {png}" + ("" if ok else " missing/empty"))
    bad += not ok
sys.exit(1 if bad else 0)
PY
done

echo "[5/10] determinism (rebuild → byte-identical GLB, incl. seeded wrinkles)"
for exp in tests/golden/*.expected.json; do
  slug=$(basename "$exp" .expected.json)
  if [ -f "staging/$slug/$slug.glb" ]; then
    cp "staging/$slug/$slug.glb" "staging/$slug/.first.glb"
    ./forge build "examples/$slug.json" > /dev/null 2>&1
    if cmp -s "staging/$slug/$slug.glb" "staging/$slug/.first.glb"; then
      echo "  ✓ $slug byte-identical across rebuilds"
    else
      echo "  ✗ $slug GLB differs between rebuilds"; FAIL=$((FAIL + 1))
    fi
    rm -f "staging/$slug/.first.glb"
  else
    echo "  ✗ $slug: no GLB from group 4"; FAIL=$((FAIL + 1))
  fi
done

echo "[6/10] doctor version-mismatch simulation"
FORGE_BLENDER=/usr/bin/true ./forge doctor > /dev/null 2>&1
if [ $? -eq 3 ]; then
  echo "  ✓ wrong blender → exit 3"
else
  echo "  ✗ mismatch not detected"; FAIL=$((FAIL + 1))
fi

echo "[7/10] contact sheet + from-image round-trip"
./forge sheet > /dev/null 2>&1
python3 - <<'PY' || FAIL=$((FAIL + 1))
import pathlib, sys
html = pathlib.Path("staging/contact-sheet.html")
if not html.is_file():
    print("  ✗ contact-sheet.html not generated"); sys.exit(1)
doc = html.read_text()
bad = 0
for slug in ("x-banner", "swag-single"):
    ok = slug in doc
    print(f"  {'✓' if ok else '✗'} sheet lists {slug}")
    bad += not ok
sys.exit(1 if bad else 0)
PY
python3 - <<'PY' || FAIL=$((FAIL + 1))
import json, pathlib, struct, subprocess, sys, zlib

# minimal 1x1 grey PNG, no external deps
def chunk(tag, data):
    c = tag + data
    return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
png = (b"\x89PNG\r\n\x1a\n"
       + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(b"\x00\x80"))
       + chunk(b"IEND", b""))
tmp = pathlib.Path("staging/.fi-test.png")
tmp.write_bytes(png)
slug = "zz-from-image-test"
spec = pathlib.Path(f"specs/{slug}.json")
ref = pathlib.Path(f"specs/references/{slug}.png")
bad = 0
try:
    p = subprocess.run(["./forge", "from-image", str(tmp), "--slug", slug],
                       capture_output=True, text=True)
    ok = p.returncode == 0 and spec.is_file() and ref.is_file()
    print(f"  {'✓' if ok else '✗'} from-image scaffolds spec + reference")
    bad += not ok
    v = subprocess.run(["./forge", "validate", str(spec)], capture_output=True, text=True)
    print(f"  {'✓' if v.returncode == 0 else '✗'} scaffold validates out of the box")
    bad += v.returncode != 0
    dup = subprocess.run(["./forge", "from-image", str(tmp), "--slug", slug],
                         capture_output=True, text=True)
    print(f"  {'✓' if dup.returncode == 2 else '✗'} refuses to clobber an existing spec")
    bad += dup.returncode != 2
finally:
    for f in (tmp, spec, ref):
        f.unlink(missing_ok=True)
sys.exit(1 if bad else 0)
PY

echo "[8/10] import décor lane round-trip (temp consumer, env overrides)"
python3 - <<'PY' || FAIL=$((FAIL + 1))
import json, os, pathlib, shutil, subprocess, sys

tmp = pathlib.Path("staging/.import-test")
shutil.rmtree(tmp, ignore_errors=True)
(tmp / "models/decor/thumbs").mkdir(parents=True)
(tmp / "catalog.json").write_text("[]")
(tmp / "consumers.json").write_text(json.dumps({"eventos": {
    "root": str(tmp.resolve()),
    "decor": {"glbDir": "models/decor", "thumbDir": "models/decor/thumbs",
              "catalogJson": "catalog.json"},
    "component": {"glbDir": "models"}}}))
(tmp / "library.json").write_text(json.dumps({"assets": []}))
env = {**os.environ, "FORGE_CONSUMERS": str(tmp / "consumers.json"),
       "FORGE_LIBRARY": str(tmp / "library.json")}
bad = 0

def check(label, ok):
    global bad
    print(f"  {'✓' if ok else '✗'} {label}")
    bad += not ok

p = subprocess.run(["./forge", "import", "--consumer", "eventos",
                    "--slugs", "swag-single", "--authored-by", "test"],
                   env=env, capture_output=True, text=True)
check("import exits 0", p.returncode == 0)
check("GLB copied", (tmp / "models/decor/swag-single.glb").is_file())
check("thumb copied", (tmp / "models/decor/thumbs/swag-single.png").is_file())
entries = json.loads((tmp / "catalog.json").read_text())
check("catalog entry appended", len(entries) == 1 and entries[0]["slug"] == "swag-single"
      and entries[0]["placement"] == "ceiling" and entries[0]["category"] == "ceiling")
lib = json.loads((tmp / "library.json").read_text())
check("ledger entry with sha + author", len(lib["assets"]) == 1
      and len(lib["assets"][0]["specSha256"]) == 64
      and lib["assets"][0]["authoredBy"] == "test")
dup = subprocess.run(["./forge", "import", "--consumer", "eventos",
                      "--slugs", "swag-single"], env=env, capture_output=True, text=True)
check("re-import refused (exit 2)", dup.returncode == 2)
shutil.rmtree(tmp)
sys.exit(1 if bad else 0)
PY

echo "[9/10] review server (staged listing, decision round-trip, path safety)"
./forge review --port 8199 --no-open > /dev/null 2>&1 &
REVIEW_PID=$!
python3 - <<'PY' || FAIL=$((FAIL + 1))
import http.client, json, pathlib, sys, time

conn = None
for _ in range(50):  # wait for the server to come up
    try:
        conn = http.client.HTTPConnection("127.0.0.1", 8199, timeout=2)
        conn.request("GET", "/api/staged")
        resp = conn.getresponse()
        break
    except OSError:
        time.sleep(0.1)
else:
    print("  ✗ review server never came up"); sys.exit(1)

bad = 0
def check(label, ok):
    global bad
    print(f"  {'✓' if ok else '✗'} {label}")
    bad += not ok

staged = json.loads(resp.read())
check("GET /api/staged returns staged assets",
      resp.status == 200 and isinstance(staged, list) and len(staged) >= 1
      and all("slug" in a and "report" in a for a in staged))
slug = staged[0]["slug"]

def post(payload):
    c = http.client.HTTPConnection("127.0.0.1", 8199, timeout=5)
    body = json.dumps(payload)
    c.request("POST", "/api/decision", body, {"Content-Type": "application/json"})
    r = c.getresponse()
    return r.status, json.loads(r.read())

dec_p = pathlib.Path(f"staging/{slug}/decision.json")
had = dec_p.read_text() if dec_p.is_file() else None  # preserve a real decision
status, rec = post({"slug": slug, "decision": "changes", "note": "test-suite probe"})
check("POST decision=changes writes decision.json",
      status == 200 and dec_p.is_file()
      and json.loads(dec_p.read_text())["note"] == "test-suite probe")
status, _ = post({"slug": slug, "decision": "changes", "note": ""})
check("change request without a note refused (400)", status == 400)
status, _ = post({"slug": slug, "decision": "shipit"})
check("unknown decision refused (400)", status == 400)
status, _ = post({"slug": "../evil", "decision": "approved"})
check("bad slug refused (400)", status == 400)
status, _ = post({"slug": slug, "decision": "pending"})
check("decision=pending clears decision.json", status == 200 and not dec_p.is_file())
if had is not None:
    dec_p.write_text(had)

c = http.client.HTTPConnection("127.0.0.1", 8199, timeout=5)
c.request("GET", f"/staging/{slug}/thumb.png")
r = c.getresponse(); r.read()
check("staged thumb served", r.status == 200 and r.getheader("Content-Type") == "image/png")
c = http.client.HTTPConnection("127.0.0.1", 8199, timeout=5)
c.putrequest("GET", "/staging/../forge")  # raw path — no client-side normalisation
c.endheaders()
r = c.getresponse(); r.read()
check("path traversal blocked (404)", r.status == 404)
c = http.client.HTTPConnection("127.0.0.1", 8199, timeout=5)
c.request("GET", "/vendor/three.module.min.js")
r = c.getresponse(); r.read()
check("vendored three.js served", r.status == 200)
sys.exit(1 if bad else 0)
PY
kill $REVIEW_PID 2>/dev/null
wait $REVIEW_PID 2>/dev/null

echo "[10/10] harvest collapse-ratio guard"
# Regression for the red rose (2026-08-20): it shipped at 10.6% of its base
# cage, passed every count-based gate, and was destroyed — petals flattened
# into facets. The guard refuses below --min-ratio. Built on a sphere this
# suite generates, so the test owns its input and reaches outside nothing.
HTMP="$(mktemp -d)"
BLENDER_BIN="$(./forge doctor 2>/dev/null | sed -n 's/.*blender pinned [^ ]* . \(.*\) . .*/\1/p')"
if [ -n "$BLENDER_BIN" ] && [ -x "$BLENDER_BIN" ]; then
  printf '%s\n' \
    'import bpy' \
    'bpy.ops.wm.read_factory_settings(use_empty=True)' \
    'bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=6, radius=0.5)' \
    "bpy.ops.wm.save_as_mainfile(filepath='$HTMP/ball.blend')" > "$HTMP/gen.py"
  "$BLENDER_BIN" --background --factory-startup --python "$HTMP/gen.py" > /dev/null 2>&1

  # 20,480 tris; 1,500 is 7.3% — below the floor, must refuse with exit 1.
  ./forge harvest --source "$HTMP/ball.blend" --slug harvest-guard-tmp \
    --label "Guard tmp" --budget 1500 > "$HTMP/lo.txt" 2>&1
  if [ $? -eq 1 ] && grep -q "Raise --budget" "$HTMP/lo.txt"; then
    echo "  ✓ refuses a collapse below the ratio floor, and says what to raise"
  else
    echo "  ✗ should have refused:"; cat "$HTMP/lo.txt"; FAIL=$((FAIL + 1))
  fi

  # Same asset, a ratio the lane actually ships at — must proceed.
  ./forge harvest --source "$HTMP/ball.blend" --slug harvest-guard-tmp \
    --label "Guard tmp" --budget 8000 > "$HTMP/hi.txt" 2>&1
  if [ $? -eq 0 ]; then
    echo "  ✓ a ratio above the floor still harvests"
  else
    echo "  ✗ should have harvested:"; cat "$HTMP/hi.txt"; FAIL=$((FAIL + 1))
  fi

  # The escape hatch, so a human who HAS looked is not blocked.
  ./forge harvest --source "$HTMP/ball.blend" --slug harvest-guard-tmp \
    --label "Guard tmp" --budget 1500 --min-ratio 0.05 > "$HTMP/esc.txt" 2>&1
  if [ $? -eq 0 ]; then
    echo "  ✓ --min-ratio overrides it deliberately"
  else
    echo "  ✗ escape hatch should have harvested:"; cat "$HTMP/esc.txt"; FAIL=$((FAIL + 1))
  fi
  # forge harvest stages a manifest as well as a build — clear both, or the
  # suite leaves a slug behind in the repo on every run.
  rm -rf staging/harvest-guard-tmp harvested/harvest-guard-tmp.json
else
  echo "  ✗ blender not found for the harvest guard test"; FAIL=$((FAIL + 1))
fi
rm -rf "$HTMP"


echo
if [ "$FAIL" -eq 0 ]; then echo "ALL PASS"; else echo "$FAIL group(s) FAILED"; exit 1; fi
