#!/bin/sh
# The release gate.
#
# `make verify` answers "does the evidence hold right now?". This answers
# "is this tree fit to be tagged and submitted?", which is a stricter question:
# it also refuses stale artifacts, undeclared edits to the pinned original,
# uncommitted changes, and public copy that has drifted from the ledger.
#
# Run via `make release-gate`, which runs `make verify` first.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

fail=0
note() { echo "  $1"; }
bad()  { echo "GATE FAIL: $1" >&2; fail=1; }

echo "=============================================================="
echo " release gate"
echo "=============================================================="

# ---------------------------------------------------------------- provenance
echo
echo "[1/10] pinned upstream is untouched"
if sh scripts/verify-upstream-hashes.sh >/dev/null 2>&1; then
    note "9 files match artifacts/upstream-manifest.json"
else
    bad "upstream hash verification failed"
fi

if command -v git >/dev/null 2>&1 && [ -d .git ]; then
    if [ -n "$(git status --porcelain upstream/ 2>/dev/null)" ]; then
        bad "upstream/ has uncommitted modifications"
    else
        note "upstream/ is clean in git"
    fi
fi

# ------------------------------------------------------------------ artifacts
echo
echo "[2/10] required artifacts are present"
for a in upstream-manifest.json original-test-report.json differential-summary.json \
         abi-report.json linkage-report.json safety-report.json \
         benchmark-summary.json mutation-report.json determinism-report.json \
         verification-report.json upstream-issues.json toolchain.json; do
    if [ -f "artifacts/$a" ]; then
        note "artifacts/$a"
    else
        bad "missing artifacts/$a"
    fi
done

echo
echo "[3/10] benchmark raw data is committed"
if [ -f bench/results/raw.json ]; then
    SAMPLES=$(python3 -c "import json;d=json.load(open('bench/results/raw.json'));print(sum(len(r['samples_ns']) for r in d))" 2>/dev/null || echo 0)
    if [ "$SAMPLES" -gt 100 ]; then
        note "$SAMPLES raw per-iteration samples"
    else
        bad "bench/results/raw.json has too few samples ($SAMPLES) to support a claim"
    fi
else
    bad "missing bench/results/raw.json"
fi

echo
echo "[4/10] a published fuzz session exists and is long enough"
PUBLISHED=$(python3 - <<'PY' 2>/dev/null || echo "0 0 0"
import json, pathlib
best = (0, 0, 0)
for p in pathlib.Path("fuzz/logs").glob("*.json"):
    try:
        s = json.loads(p.read_text())
    except Exception:
        continue
    if s.get("schema") != "pdjson-zig/fuzz-session@1":
        continue
    if s.get("cases", 0) > best[1]:
        best = (s.get("elapsed_seconds", 0), s.get("cases", 0), s.get("divergences", 0))
print(int(best[0]), best[1], best[2])
PY
)
SECS=$(echo "$PUBLISHED" | cut -d' ' -f1)
CASES=$(echo "$PUBLISHED" | cut -d' ' -f2)
FDIV=$(echo "$PUBLISHED" | cut -d' ' -f3)
if [ "$SECS" -ge 60 ]; then
    note "longest session: ${SECS}s, ${CASES} cases, ${FDIV} divergences"
else
    bad "no fuzz session of at least 60s found in fuzz/logs/"
fi
[ "$FDIV" = "0" ] || bad "the published fuzz session reports $FDIV divergence(s)"

# ------------------------------------------------------------------- results
echo
echo "[5/10] no unexplained divergences"
python3 - <<'PY' || fail=1
import json, pathlib, sys
bad = 0
for name in ("differential-summary.json", "differential-jsontestsuite.json"):
    p = pathlib.Path("artifacts") / name
    if not p.exists():
        continue
    d = json.loads(p.read_text())
    for key in ("divergences", "zig_crashes", "c_crashes_unexplained", "timeouts"):
        if d.get(key, 0):
            print(f"GATE FAIL: {name} reports {d[key]} {key}", file=sys.stderr)
            bad = 1
    # Every excluded case must carry sanitizer evidence, or the exclusion is
    # just an assertion.
    for f in d.get("findings", []):
        if f.get("kind") == "upstream_ub" and not f.get("sanitizer_report"):
            print(f"GATE FAIL: {name}: an upstream_ub finding has no sanitizer "
                  f"evidence ({f.get('input')})", file=sys.stderr)
            bad = 1
    print(f"  {name}: {d.get('divergences')} divergences, "
          f"{d.get('upstream_ub')} sanitizer-confirmed upstream UB")
sys.exit(bad)
PY

echo
echo "[6/10] test pass rate has not regressed"
python3 - <<'PY' || fail=1
import json, pathlib, sys
p = pathlib.Path("artifacts/original-test-report.json")
d = json.loads(p.read_text())
s = d["summary"]
ok = True
if s["assertions_failed_against_zig"]:
    print(f"GATE FAIL: {s['assertions_failed_against_zig']} upstream assertion(s) fail",
          file=sys.stderr); ok = False
if s["assertions_passed_against_zig"] < 18:
    print(f"GATE FAIL: only {s['assertions_passed_against_zig']}/18 upstream "
          f"assertions pass", file=sys.stderr); ok = False
if s["assertions_skipped"] or s["assertions_unsupported"]:
    print("GATE FAIL: upstream assertions were skipped or marked unsupported",
          file=sys.stderr); ok = False
if s["tool_differential_mismatches"]:
    print(f"GATE FAIL: {s['tool_differential_mismatches']} stream.c/pretty.c "
          f"output mismatches", file=sys.stderr); ok = False
if d.get("modified"):
    print("GATE FAIL: upstream test sources are marked modified", file=sys.stderr)
    ok = False
print(f"  {s['assertions_passed_against_zig']}/{s['assertions_total']} upstream "
      f"assertions, {s['tool_differential_mismatches']} tool mismatches")
sys.exit(0 if ok else 1)
PY

echo
echo "[7/10] the Zig artifact links no upstream parser code"
if sh scripts/verify-no-c-linkage.sh >/dev/null 2>&1; then
    note "linkage check passes"
else
    bad "the Zig artifact appears to contain or import upstream parser code"
    sh scripts/verify-no-c-linkage.sh || true
fi

echo
echo "[8/10] escape-hatch budget and formatting"
if sh scripts/safety-scan.sh >/dev/null 2>&1; then
    note "safety scan passes"
else
    bad "safety scan failed"
fi
if zig fmt --check build.zig src tools tests/port >/dev/null 2>&1; then
    note "formatting clean"
else
    bad "zig fmt reports unformatted files"
fi

echo
echo "[9/10] the harness still catches injected defects"
python3 - <<'PY' || fail=1
import json, pathlib, sys
p = pathlib.Path("artifacts/mutation-report.json")
if not p.exists():
    print("GATE FAIL: missing artifacts/mutation-report.json", file=sys.stderr)
    sys.exit(1)
d = json.loads(p.read_text())
if d.get("survived") or d.get("not_evaluated"):
    print(f"GATE FAIL: mutation testing has {d.get('survived')} survivor(s) and "
          f"{d.get('not_evaluated')} not evaluated", file=sys.stderr)
    sys.exit(1)
print(f"  {d['caught']}/{d['mutants_defined']} mutants caught, "
      f"{d.get('excluded_cases', 0)} cases excluded as upstream UB")
PY

echo
echo "[10/10] public copy matches the claim ledger"
if python3 scripts/validate-claims.py >/dev/null 2>&1; then
    note "CLAIMS.json validates and README blocks are current"
else
    bad "claim validation failed"
    python3 scripts/validate-claims.py || true
fi

if command -v git >/dev/null 2>&1 && [ -d .git ]; then
    if [ -n "$(git status --porcelain)" ]; then
        echo
        echo "  note: the working tree has uncommitted changes:"
        git status --short | sed 's/^/    /'
        bad "commit everything before tagging a release"
    fi
fi

echo
echo "=============================================================="
if [ "$fail" -eq 0 ]; then
    echo " RELEASE GATE PASSED"
    echo "=============================================================="
    exit 0
fi
echo " RELEASE GATE FAILED"
echo "=============================================================="
exit 1
