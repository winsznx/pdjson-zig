#!/bin/sh
# Prove the compile-time ABI contract can fail.
#
# src/abi_contract.zig asserts src/abi.zig against the layout the C compiler
# reads out of the pinned header. A contract that passes tells you nothing until
# you have seen it fail: an assertion that is silently vacuous -- a mistyped
# guard, an empty table, a comparison that always holds -- looks exactly like a
# contract that is satisfied.
#
# So: introduce a layout drift, from both directions, and require the build to
# stop. Then build the unmodified tree and require it to succeed, so a
# permanently-broken build cannot be mistaken for good detection.
#
# Everything happens in a copy of the tree under $TMPDIR. This script never
# writes to the working tree, zig-out or build/ -- an earlier version of a
# different harness overwrote a tool binary and killed a running fuzz session.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
WORK=${TMPDIR:-/tmp}/pdjson-abi-negative.$$
OUT="$ROOT/artifacts/abi/contract-negative.json"

mkdir -p "$WORK" "$ROOT/artifacts/abi"
trap 'rm -rf "$WORK"' EXIT

cp -R "$ROOT/build.zig" "$ROOT/src" "$ROOT/tools" "$ROOT/upstream" "$WORK/"
mkdir -p "$WORK/include" && cp "$ROOT/include/pdjson.h" "$WORK/include/"

PRISTINE_GEN="$WORK/pristine_abi_generated.zig"
PRISTINE_ABI="$WORK/pristine_abi.zig"
cp "$ROOT/src/abi_generated.zig" "$PRISTINE_GEN"
cp "$ROOT/src/abi.zig" "$PRISTINE_ABI"

results=""
caught=0
missed=0

restore () {
    cp "$PRISTINE_GEN" "$WORK/src/abi_generated.zig"
    cp "$PRISTINE_ABI" "$WORK/src/abi.zig"
}

# $1 = description, $2 = file under src/, $3 = sed expression
mutate () {
    desc=$1
    file=$2
    expr=$3
    restore
    # BSD and GNU sed disagree on in-place; write through a temp file instead.
    sed "$expr" "$WORK/src/$file" > "$WORK/mutant.tmp"
    if cmp -s "$WORK/mutant.tmp" "$WORK/src/$file"; then
        echo "SETUP FAIL  $desc: the mutation did not change anything" >&2
        exit 1
    fi
    cp "$WORK/mutant.tmp" "$WORK/src/$file"

    msg=$(cd "$WORK" && zig build install \
              --cache-dir "$WORK/.zig-cache" --prefix "$WORK/out" 2>&1 \
          | grep -m1 "C ABI drift" || true)

    if [ -n "$msg" ]; then
        caught=$((caught + 1))
        echo "CAUGHT  $desc"
        echo "        ${msg##*error: }"
        detail=$(printf '%s' "${msg##*error: }" | sed 's/"/\\"/g')
        results="$results{\"case\":\"$desc\",\"mutated\":\"src/$file\",\"detected\":true,\"message\":\"$detail\"},"
    else
        missed=$((missed + 1))
        echo "MISSED  $desc"
        results="$results{\"case\":\"$desc\",\"mutated\":\"src/$file\",\"detected\":false},"
    fi
}

echo "Drifting the recorded C layout (simulates the header changing under us):"
mutate "field offset moved: json_stream.ntokens" abi_generated.zig \
    's/.path = "ntokens", .offset = 64/.path = "ntokens", .offset = 72/'
mutate "field size shrank: json_stream.errmsg" abi_generated.zig \
    's/.path = "errmsg", .offset = 144, .size = 128/.path = "errmsg", .offset = 144, .size = 120/'
mutate "nested union arm moved: json_source.source.user.peek" abi_generated.zig \
    's/.path = "source.user.peek", .offset = 40/.path = "source.user.peek", .offset = 32/'
mutate "struct size changed: sizeof(json_stream)" abi_generated.zig \
    's/sizeof_json_stream = 272/sizeof_json_stream = 280/'
mutate "struct alignment changed: alignof(json_stream)" abi_generated.zig \
    's/alignof_json_stream = 8/alignof_json_stream = 16/'
mutate "enumerator renumbered: JSON_NULL" abi_generated.zig \
    's/.name = "JSON_NULL", .value = 11/.name = "JSON_NULL", .value = 12/'

echo
echo "Drifting the port's own declarations (the direction that actually happens):"
mutate "error buffer shortened in src/abi.zig" abi.zig \
    's/^pub const errmsg_len = 128;/pub const errmsg_len = 120;/'
mutate "counter narrowed: stack_top usize -> c_uint" abi.zig \
    's/^    stack_top: usize,/    stack_top: c_uint,/'
mutate "enum tag signedness flipped: c_uint -> c_int" abi.zig \
    's/^pub const Type = enum(c_uint) {/pub const Type = enum(c_int) {/'
mutate "field inserted: extra member before ntokens" abi.zig \
    's/^    ntokens: usize,/    injected_padding: usize,\n    ntokens: usize,/'

echo
restore
if (cd "$WORK" && zig build install --cache-dir "$WORK/.zig-cache" --prefix "$WORK/out" >/dev/null 2>&1); then
    echo "CONTROL builds clean with the tree unmodified"
    control="pass"
else
    echo "CONTROL FAIL: the unmodified tree does not build, so the results above prove nothing" >&2
    control="fail"
fi

# The contract is guarded by ABI class. Confirm the guard genuinely disengages
# off-class rather than the whole thing being dead code everywhere.
cp "$PRISTINE_GEN" "$WORK/src/abi_generated.zig"
sed 's/.path = "ntokens", .offset = 64/.path = "ntokens", .offset = 72/' \
    "$PRISTINE_GEN" > "$WORK/src/abi_generated.zig"
if (cd "$WORK" && zig build install -Dtarget=x86-linux-gnu \
        --cache-dir "$WORK/.zig-cache" --prefix "$WORK/out32" >/dev/null 2>&1); then
    echo "DEFERRAL a 32-bit target builds through the same bad table, confirming the"
    echo "         ABI-class guard disengages off-class instead of asserting nonsense"
    deferral="pass"
else
    deferral="fail"
    echo "DEFERRAL FAIL: the off-class guard did not disengage" >&2
fi
restore

mkdir -p "$ROOT/artifacts/abi"
cat > "$OUT" <<EOF
{
  "schema": "pdjson-zig/abi-contract-negative@1",
  "method": "Each case introduces one layout drift into a throwaway copy of the tree and requires 'zig build' to stop with a named field. Six drift the recorded C layout; four drift src/abi.zig itself.",
  "cases": $((caught + missed)),
  "detected": $caught,
  "missed": $missed,
  "control_unmodified_build": "$control",
  "off_class_deferral": "$deferral",
  "known_blind_spot": "Two adjacent fields of the same width swapped with each other leave every offset and size unchanged, so no layout table can see it. That case is covered by behaviour instead: the differential compares json_get_lineno, depth, context and error text on every record.",
  "results": [ $(printf '%s' "$results" | sed 's/,$//') ]
}
EOF

echo
echo "contract negative test: $caught detected, $missed missed"
echo "wrote artifacts/abi/contract-negative.json"
[ "$missed" -eq 0 ] && [ "$control" = "pass" ] && [ "$deferral" = "pass" ]
