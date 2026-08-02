#!/bin/sh
# Compare the C and Zig struct layouts on targets this machine cannot run.
#
# scripts/abi-check.sh proves the two agree on the host by executing both
# probes. That is the stronger check, but it only ever covers targets with a
# runner -- here, arm64 macOS and x86-64 Linux in CI. Both are LP64, so it says
# nothing about what happens when the pointer size or alignment changes.
#
# This closes that gap without needing to execute anything:
#
#   1. Ask Zig for its layout on the target, at compile time, via @compileLog.
#   2. Feed those numbers back to the C compiler as _Static_assert over the
#      *pinned upstream header*, compiled for the same target.
#
# If step 2 compiles, the two descriptions of the ABI agree on that target.
#
# Caveat, stated because it matters: both sides go through the Zig toolchain's
# clang, so this shows the Zig declarations match what *that* C frontend
# computes for the target. It is not a claim about gcc on the same target. The
# executed check in abi-check.sh is what covers the host compiler.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT="$ROOT/artifacts/abi-cross-report.json"

TARGETS=${TARGETS:-"x86_64-linux-gnu x86-linux-gnu aarch64-linux-gnu arm-linux-gnueabihf x86_64-windows-gnu riscv64-linux-gnu"}

TMP=${TMPDIR:-/tmp}/pdjson-abi-cross.$$
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/probe.zig" <<'EOF'
const abi = @import("abi");
comptime {
    @compileLog(
        @sizeOf(abi.Stream),
        @alignOf(abi.Stream),
        @sizeOf(abi.Source),
        @sizeOf(abi.Allocator),
        @sizeOf(abi.Type),
        @offsetOf(abi.Stream, "lineno"),
        @offsetOf(abi.Stream, "stack"),
        @offsetOf(abi.Stream, "stack_top"),
        @offsetOf(abi.Stream, "stack_size"),
        @offsetOf(abi.Stream, "next"),
        @offsetOf(abi.Stream, "flags"),
        @offsetOf(abi.Stream, "data"),
        @offsetOf(abi.Stream, "ntokens"),
        @offsetOf(abi.Stream, "source"),
        @offsetOf(abi.Stream, "alloc"),
        @offsetOf(abi.Stream, "errmsg"),
        // Field sizes as well as offsets. Offsets alone are not enough: a
        // shorter trailing array can be absorbed by padding, leaving sizeof and
        // every offset identical. That case slipped through the first version
        // of this check and is why these are here.
        @sizeOf(@FieldType(abi.Stream, "lineno")),
        @sizeOf(@FieldType(abi.Stream, "stack")),
        @sizeOf(@FieldType(abi.Stream, "stack_top")),
        @sizeOf(@FieldType(abi.Stream, "stack_size")),
        @sizeOf(@FieldType(abi.Stream, "next")),
        @sizeOf(@FieldType(abi.Stream, "flags")),
        @sizeOf(@FieldType(abi.Stream, "data")),
        @sizeOf(@FieldType(abi.Stream, "ntokens")),
        @sizeOf(@FieldType(abi.Stream, "source")),
        @sizeOf(@FieldType(abi.Stream, "alloc")),
        @sizeOf(@FieldType(abi.Stream, "errmsg")),
    );
}
EOF

FIELDS="lineno stack stack_top stack_size next flags data ntokens source alloc errmsg"

results=""
checked=0
failed=0

for target in $TARGETS; do
    # 1. Zig's view, extracted at compile time.
    log=$(zig build-obj -target "$target" \
              --dep abi -Mroot="$TMP/probe.zig" -Mabi="$ROOT/src/abi.zig" 2>&1 \
          | sed -n '/Compile Log Output:/,$p' | tail -n +2 | head -1 || true)

    values=$(printf '%s' "$log" | grep -oE '[0-9]+' | tr '\n' ' ' || true)
    set -- $values
    if [ "$#" -lt 27 ]; then
        echo "SKIP $target: Zig could not describe this target"
        results="$results{\"target\":\"$target\",\"status\":\"zig_unsupported\"},"
        continue
    fi

    sz=$1; al=$2; szsrc=$3; szal=$4; szenum=$5
    shift 5
    offsets=$(echo "$*" | cut -d' ' -f1-11)
    sizes=$(echo "$*" | cut -d' ' -f12-22)

    # 2. The same numbers asserted against the pinned header, for that target.
    {
        echo '#include "pdjson.h"'
        echo '#include <stddef.h>'
        echo "_Static_assert(sizeof(struct json_stream) == $sz, \"sizeof json_stream\");"
        echo "_Static_assert(_Alignof(struct json_stream) == $al, \"alignof json_stream\");"
        echo "_Static_assert(sizeof(struct json_source) == $szsrc, \"sizeof json_source\");"
        echo "_Static_assert(sizeof(struct json_allocator) == $szal, \"sizeof json_allocator\");"
        echo "_Static_assert(sizeof(enum json_type) == $szenum, \"sizeof enum json_type\");"
        i=1
        for f in $FIELDS; do
            off=$(printf '%s' "$offsets" | cut -d' ' -f$i)
            fsz=$(printf '%s' "$sizes" | cut -d' ' -f$i)
            echo "_Static_assert(offsetof(struct json_stream, $f) == $off, \"offset $f\");"
            echo "_Static_assert(sizeof(((struct json_stream *)0)->$f) == $fsz, \"size $f\");"
            i=$((i + 1))
        done
    } > "$TMP/assert.c"

    checked=$((checked + 1))
    if zig cc -target "$target" -std=c11 -c -o "$TMP/assert.o" \
              -I "$ROOT/upstream/pdjson" "$TMP/assert.c" 2>"$TMP/err.txt"; then
        echo "OK   $target  sizeof(json_stream)=$sz align=$al enum=$szenum"
        results="$results{\"target\":\"$target\",\"status\":\"match\",\"sizeof_json_stream\":$sz,\"alignof\":$al,\"sizeof_enum\":$szenum},"
    else
        echo "FAIL $target: the Zig layout does not match the pinned header"
        grep -E "static_assert|static assertion" "$TMP/err.txt" | head -4 >&2 || true
        results="$results{\"target\":\"$target\",\"status\":\"mismatch\"},"
        failed=$((failed + 1))
    fi
done

mkdir -p "$ROOT/artifacts"
cat > "$OUT" <<EOF
{
  "schema": "pdjson-zig/abi-cross-report@1",
  "method": "Zig's layout for each target is read at compile time via @compileLog, then asserted against the pinned upstream header with _Static_assert compiled for the same target. Neither side is executed.",
  "caveat": "Both sides use the Zig toolchain's clang, so this shows the Zig declarations agree with that C frontend for each target. It is not a claim about other compilers on those targets; scripts/abi-check.sh covers the host compiler by execution.",
  "targets_checked": $checked,
  "mismatches": $failed,
  "results": [ $(printf '%s' "$results" | sed 's/,$//') ]
}
EOF

echo
echo "cross-target ABI: $checked target(s) checked, $failed mismatch(es)"
echo "wrote artifacts/abi-cross-report.json"
[ "$failed" -eq 0 ]
