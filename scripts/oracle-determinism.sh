#!/bin/sh
# The whole comparison rests on the C oracle being deterministic. If the same
# input produced different transcripts across runs, "the two agree" would mean
# nothing. This runs the oracle repeatedly over the fixture corpus in every
# mode and requires byte-identical output every time.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
C_BIN="$ROOT/build/transcript_c"
Z_BIN="$ROOT/zig-out/bin/transcript_zig"
RUNS=${RUNS:-5}
MODES="next nostream peek skip sep"

[ -x "$C_BIN" ] || { echo "FAIL: missing $C_BIN (run 'make build')" >&2; exit 1; }

TMP=${TMPDIR:-/tmp}/pdjson-determinism.$$
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

ls "$ROOT"/tests/conformance/fixtures/*.json > "$TMP/list.txt"
COUNT=$(wc -l < "$TMP/list.txt" | tr -d ' ')

check_binary() {
    label=$1
    bin=$2
    for mode in $MODES; do
        "$bin" --batch "$mode" "$TMP/list.txt" > "$TMP/$label.$mode.0" 2>/dev/null
        i=1
        while [ "$i" -lt "$RUNS" ]; do
            "$bin" --batch "$mode" "$TMP/list.txt" > "$TMP/$label.$mode.$i" 2>/dev/null
            if ! cmp -s "$TMP/$label.$mode.0" "$TMP/$label.$mode.$i"; then
                echo "FAIL: $label transcripts differ between runs (mode $mode, run $i)" >&2
                exit 1
            fi
            i=$((i + 1))
        done
    done
}

check_binary c "$C_BIN"
echo "  C oracle: $RUNS runs x $COUNT fixtures x 5 modes, byte-identical every time"

if [ -x "$Z_BIN" ]; then
    check_binary zig "$Z_BIN"
    echo "  Zig:      $RUNS runs x $COUNT fixtures x 5 modes, byte-identical every time"
fi

mkdir -p "$ROOT/artifacts"
cat > "$ROOT/artifacts/determinism-report.json" <<EOF
{
  "schema": "pdjson-zig/determinism-report@1",
  "runs_per_mode": $RUNS,
  "fixtures": $COUNT,
  "modes": ["next", "nostream", "peek", "skip", "sep"],
  "c_oracle_deterministic": true,
  "zig_deterministic": $([ -x "$Z_BIN" ] && echo true || echo null),
  "note": "Transcripts deliberately exclude pointer values, heap addresses, timing, and the bytes of errmsg past its NUL terminator, all of which are not reproducible."
}
EOF
echo "  wrote artifacts/determinism-report.json"
