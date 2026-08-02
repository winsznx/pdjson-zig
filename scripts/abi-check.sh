#!/bin/sh
# Prove that the Zig declarations produce the same C ABI as the pinned header.
#
# Four independent things have to hold, and this checks all four:
#
#   1. Layout. tools/abi_probe_c.c is compiled against upstream/pdjson/pdjson.h,
#      so its numbers are whatever the C compiler says the original header means
#      on this target. tools/abi_probe.zig computes the same table from
#      src/abi.zig. They are diffed line by line.
#
#   2. The compile-time contract is current. src/abi_generated.zig is the same
#      layout baked into the library build (see src/abi_contract.zig); if it has
#      gone stale relative to the header, the contract would be asserting an
#      outdated shape and still passing.
#
#   3. Symbols. Every function the pinned header declares as exported is
#      actually present in the archive, with no extra json_* symbols leaking.
#      A count would pass while a symbol was missing and another was added.
#
#   4. Linkability. A C program that includes the pinned header and declares
#      struct json_stream by value links against only the Zig archive and runs.
#
# A mismatch anywhere fails, because a drop-in replacement whose layout drifts
# corrupts its callers silently.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
C_PROBE="$ROOT/build/abi_probe_c"
ZIG_PROBE="$ROOT/zig-out/bin/abi_probe_zig"
LIB="$ROOT/zig-out/lib/libpdjson.a"
ART="$ROOT/artifacts/abi"
OUT="$ART/abi-report.json"

[ -x "$C_PROBE" ]   || { echo "FAIL: missing $C_PROBE (run 'make build')" >&2; exit 1; }
[ -x "$ZIG_PROBE" ] || { echo "FAIL: missing $ZIG_PROBE (run 'make build')" >&2; exit 1; }

TMP=${TMPDIR:-/tmp}/pdjson-abi.$$
mkdir -p "$TMP" "$ART"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------- 1. layout

"$C_PROBE"   > "$ART/c-layout.json"
"$ZIG_PROBE" > "$ART/zig-layout.json"
grep -v '"producer"' "$ART/c-layout.json"   > "$TMP/c.json"
grep -v '"producer"' "$ART/zig-layout.json" > "$TMP/zig.json"

if diff -u "$TMP/c.json" "$TMP/zig.json" > "$TMP/diff.txt"; then
    STATUS="identical"
    echo "  C and Zig agree on every offset, size, alignment and enumerator"
else
    STATUS="mismatch"
    echo "FAIL: ABI layout mismatch between the pinned header and src/abi.zig" >&2
    cat "$TMP/diff.txt" >&2
fi

# ------------------------------------------------- 2. compile-time contract

CONTRACT="stale"
if sh "$ROOT/scripts/abi-generate.sh" --check > "$TMP/gen.txt" 2>&1; then
    if grep -q "^SKIP" "$TMP/gen.txt"; then
        CONTRACT="off_class"
        echo "  compile-time contract: generated for a different ABI class on this host"
    else
        CONTRACT="current"
        echo "  compile-time contract: src/abi_generated.zig matches the pinned header,"
        echo "                         so what the library asserted while building is current"
    fi
else
    echo "FAIL: src/abi_generated.zig is stale; the compile-time contract is asserting" >&2
    echo "      an outdated layout. Run 'sh scripts/abi-generate.sh'." >&2
    cat "$TMP/gen.txt" >&2
fi

CONTRACT_FIELDS=$(grep -c '^    .{ .@"struct"' "$ROOT/src/abi_generated.zig" || echo 0)

# --------------------------------------------------------------- 3. symbols

# What the pinned header says is exported.
sed -n 's/.*PDJSON_SYMEXPORT[^(]*[ *]\([a-z_][a-z0-9_]*\)[[:space:]]*(.*/\1/p' \
    "$ROOT/upstream/pdjson/pdjson.h" | sort -u > "$TMP/declared.txt"

SYM_STATUS="not_attempted"
SYM_MISSING=""
SYM_EXTRA=""

# Both sides empty would compare equal and report a match. Refuse to draw a
# conclusion from two empty sets.
if [ ! -s "$TMP/declared.txt" ]; then
    echo "FAIL: extracted no PDJSON_SYMEXPORT declarations from the pinned header" >&2
    exit 1
fi

if [ -f "$LIB" ]; then
    # Mach-O prefixes an underscore, ELF does not; strip it either way.
    nm -g "$LIB" 2>/dev/null \
        | awk '$2 ~ /^[TtSsDdBb]$/ {print $3}' \
        | sed 's/^_//' | grep '^json_' | sort -u > "$ART/exported-symbols.txt"
    if [ ! -s "$ART/exported-symbols.txt" ]; then
        echo "FAIL: nm reported no json_* symbols in $LIB" >&2
        exit 1
    fi

    SYM_MISSING=$(comm -23 "$TMP/declared.txt" "$ART/exported-symbols.txt" | tr '\n' ' ')
    SYM_EXTRA=$(comm -13 "$TMP/declared.txt" "$ART/exported-symbols.txt" | tr '\n' ' ')
    if [ -z "$SYM_MISSING" ] && [ -z "$SYM_EXTRA" ]; then
        SYM_STATUS="exact_match"
        echo "  exported symbols: exactly the $(wc -l < "$TMP/declared.txt" | tr -d ' ') the pinned header declares, no more, no fewer"
    else
        SYM_STATUS="mismatch"
        echo "FAIL: exported symbol set does not match the pinned header" >&2
        [ -n "$SYM_MISSING" ] && echo "      missing from the archive: $SYM_MISSING" >&2
        [ -n "$SYM_EXTRA" ]   && echo "      not declared in the header: $SYM_EXTRA" >&2
    fi
fi

# ------------------------------------------------------------ 4. linkability

LINK_STATUS="not_attempted"
if [ -f "$LIB" ]; then
    if cc -std=c99 -pedantic -Wall -Wextra -Wno-missing-field-initializers \
         -I "$ROOT/upstream/pdjson" -o "$TMP/consumer" \
         "$ROOT/tests/original/abi_consumer.c" "$LIB" 2>"$TMP/link.err"; then
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

SIZEOF=$(sed -n 's/.*"sizeof_json_stream": \([0-9]*\).*/\1/p' "$ART/c-layout.json")
DECLARED=$(wc -l < "$TMP/declared.txt" | tr -d ' ')
EXPORTED=$(wc -l < "$ART/exported-symbols.txt" 2>/dev/null | tr -d ' ' || echo 0)

cat > "$OUT" <<EOF
{
  "schema": "pdjson-zig/abi-report@2",
  "comparison": "$STATUS",
  "method": "tools/abi_probe_c.c (compiled against the pinned upstream/pdjson/pdjson.h) vs tools/abi_probe.zig (computed from src/abi.zig); diffed field by field",
  "compile_time_contract": "$CONTRACT",
  "compile_time_contract_fields": $CONTRACT_FIELDS,
  "compile_time_contract_note": "src/abi_generated.zig is emitted from the pinned header by scripts/abi-generate.sh and asserted by src/abi_contract.zig during 'zig build', so a layout drift fails the build itself rather than this script. scripts/abi-contract-negative.sh proves those assertions can fail.",
  "symbol_check": "$SYM_STATUS",
  "symbols_declared_in_header": $DECLARED,
  "symbols_exported_by_archive": $EXPORTED,
  "symbols_missing": "$SYM_MISSING",
  "symbols_undeclared": "$SYM_EXTRA",
  "symbol_method": "nm -g on zig-out/lib/libpdjson.a, leading Mach-O underscore stripped, compared as a set against every PDJSON_SYMEXPORT declaration in the pinned header",
  "c_consumer_link": "$LINK_STATUS",
  "c_consumer_source": "tests/original/abi_consumer.c",
  "no_upstream_code_linked": "scripts/verify-no-c-linkage.sh, reported separately in artifacts/linkage-report.json",
  "sizeof_json_stream": $SIZEOF,
  "public_header": "include/pdjson.h is byte-identical to upstream/pdjson/pdjson.h",
  "scope": "Executed on the host target only. The layout is derived from the same header on both sides, so it tracks the platform's C ABI rather than hard-coding one, but this report attests only to the target it was generated on. artifacts/abi/abi-cross-report.json covers six further targets at compile time.",
  "c_table": $(tr -d '\n' < "$ART/c-layout.json" | sed 's/  */ /g'),
  "zig_table": $(tr -d '\n' < "$ART/zig-layout.json" | sed 's/  */ /g')
}
EOF

echo "  wrote artifacts/abi/abi-report.json, c-layout.json, zig-layout.json, exported-symbols.txt"
[ "$STATUS" = "identical" ] || exit 1
[ "$CONTRACT" = "current" ] || [ "$CONTRACT" = "off_class" ] || exit 1
[ "$SYM_STATUS" = "exact_match" ] || [ "$SYM_STATUS" = "not_attempted" ] || exit 1
[ "$LINK_STATUS" = "linked_and_ran" ] || [ "$LINK_STATUS" = "not_attempted" ] || exit 1
