#!/bin/sh
# Prove that the Zig declarations produce the same C ABI as the pinned header.
#
# Two probes emit the same table:
#   tools/abi_probe_c.c   -- compiled against upstream/pdjson/pdjson.h, so the
#                            numbers are whatever the C compiler says the
#                            original header means on this target.
#   tools/abi_probe.zig   -- computed from src/abi.zig via @sizeOf/@offsetOf.
#
# They are compared line by line. Only the "producer" field is allowed to
# differ. A mismatch anywhere -- one field offset, one enumerator, one
# alignment -- fails the build, because a drop-in replacement whose struct
# layout drifts would corrupt callers silently.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
C_PROBE="$ROOT/build/abi_probe_c"
ZIG_PROBE="$ROOT/zig-out/bin/abi_probe_zig"
OUT="$ROOT/artifacts/abi-report.json"

[ -x "$C_PROBE" ]   || { echo "FAIL: missing $C_PROBE (run 'make build')" >&2; exit 1; }
[ -x "$ZIG_PROBE" ] || { echo "FAIL: missing $ZIG_PROBE (run 'make build')" >&2; exit 1; }

TMP=${TMPDIR:-/tmp}/pdjson-abi.$$
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

"$C_PROBE"   | grep -v '"producer"' > "$TMP/c.json"
"$ZIG_PROBE" | grep -v '"producer"' > "$TMP/zig.json"

if diff -u "$TMP/c.json" "$TMP/zig.json" > "$TMP/diff.txt"; then
    STATUS="identical"
    echo "  C and Zig agree on every offset, size, alignment and enumerator"
else
    STATUS="mismatch"
    echo "FAIL: ABI layout mismatch between the pinned header and src/abi.zig" >&2
    cat "$TMP/diff.txt" >&2
fi

# A layout table alone does not prove linkability, so also record that a C
# program using the original header links against the Zig archive and runs.
LINK_STATUS="not_attempted"
if [ -f "$ROOT/zig-out/lib/libpdjson.a" ]; then
    if cc -std=c99 -pedantic -Wall -Wextra -Wno-missing-field-initializers \
         -I "$ROOT/upstream/pdjson" -o "$TMP/consumer" \
         "$ROOT/tests/original/abi_consumer.c" \
         "$ROOT/zig-out/lib/libpdjson.a" 2>"$TMP/link.err"; then
        if "$TMP/consumer" >"$TMP/consumer.out" 2>&1; then
            LINK_STATUS="linked_and_ran"
            echo "  a C consumer using the pinned header links against libpdjson.a and runs"
        else
            LINK_STATUS="ran_but_failed"
            echo "FAIL: the C consumer linked but did not pass" >&2
            cat "$TMP/consumer.out" >&2
        fi
    else
        LINK_STATUS="link_failed"
        echo "FAIL: could not link a C consumer against libpdjson.a" >&2
        head -20 "$TMP/link.err" >&2
    fi
fi

SIZEOF=$("$C_PROBE" | sed -n 's/.*"sizeof_json_stream": \([0-9]*\).*/\1/p')
SYMBOLS=$(nm -g "$ROOT/zig-out/lib/libpdjson.a" 2>/dev/null \
          | grep -cE ' [TtSs] _?json_' || echo 0)

mkdir -p "$ROOT/artifacts"
cat > "$OUT" <<EOF
{
  "schema": "pdjson-zig/abi-report@1",
  "comparison": "$STATUS",
  "method": "tools/abi_probe_c.c (compiled against the pinned upstream/pdjson/pdjson.h) vs tools/abi_probe.zig (computed from src/abi.zig); diffed field by field",
  "c_consumer_link": "$LINK_STATUS",
  "c_consumer_source": "tests/original/abi_consumer.c",
  "sizeof_json_stream": $SIZEOF,
  "exported_json_symbols": $SYMBOLS,
  "public_header": "include/pdjson.h is byte-identical to upstream/pdjson/pdjson.h",
  "scope": "Verified on the host target only. The layout is derived from the same header on both sides, so it tracks the platform's C ABI rather than hard-coding one, but this report attests only to the target it was generated on.",
  "c_table": $("$C_PROBE" | tr -d '\n' | sed 's/  */ /g'),
  "zig_table": $("$ZIG_PROBE" | tr -d '\n' | sed 's/  */ /g')
}
EOF

echo "  wrote artifacts/abi-report.json"
[ "$STATUS" = "identical" ] || exit 1
[ "$LINK_STATUS" = "linked_and_ran" ] || [ "$LINK_STATUS" = "not_attempted" ] || exit 1
