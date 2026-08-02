#!/bin/sh
# Enumerate and justify every escape hatch in the shipped library.
#
# Zig has no `unsafe` keyword, so "zero unsafe" needs a concrete definition.
# Here it means: no operation that can silently reinterpret memory, bypass a
# runtime check, or read uninitialised storage -- except at a boundary that
# cannot be expressed otherwise, documented at the site.
#
# The report lists every occurrence with file and line, not just a count, so a
# reader can audit them individually instead of trusting a number. Budgets are
# exact: adding one more requires editing this file, which shows up in review.
#
# Scope is src/ -- the library that ships. Tests and tools are not scanned.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SRC="$ROOT/src"
OUT="$ROOT/artifacts/safety-report.json"

TMP=${TMPDIR:-/tmp}/pdjson-safety.$$
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

# Strip // comments (including doc comments) before scanning, so a mention of
# an operation in prose is not counted as a use of it.
for f in "$SRC"/*.zig; do
    sed 's://.*::' "$f" | grep -n . | sed "s|^|$(basename "$f"):|"
done > "$TMP/code.txt"

occurrences() {
    grep -- "$1" "$TMP/code.txt" || true
}

count_of() {
    occurrences "$1" | grep -c . || true
}

json_list() {
    # Emit the matching lines as a JSON array of "file:line" strings.
    printf '['
    first=1
    occurrences "$1" | while IFS= read -r line; do
        loc=$(printf '%s' "$line" | cut -d: -f1-2)
        [ "$first" -eq 1 ] || printf ', '
        first=0
        printf '"%s"' "$loc"
    done
    printf ']'
}

PTRCAST=$(count_of '@ptrCast')
ALIGNCAST=$(count_of '@alignCast')
CONSTCAST=$(count_of '@constCast')
INTCAST=$(count_of '@intCast')
BITCAST=$(count_of '@bitCast')
TRUNCATE=$(count_of '@truncate')
UNREACHABLE=$(count_of 'unreachable')
UNDEFINED=$(count_of '= undefined')
NO_SAFETY=$(count_of '@setRuntimeSafety')
VOLATILE=$(count_of 'volatile')
ASM=$(count_of 'asm ')
FORCE_UNWRAP=$(occurrences '\.?' | grep -cE '[a-zA-Z_)\]]\.\?' || true)

fail=0
limit() {
    name=$1; value=$2; max=$3; pattern=$4
    if [ "$value" -gt "$max" ]; then
        echo "FAIL: $name used $value time(s), budget is $max" >&2
        occurrences "$pattern" >&2
        fail=1
    fi
}

# Pointer casts live only at the two C boundaries: the untyped allocator
# interface, and the char* the public header promises callers.
limit "@ptrCast"          "$PTRCAST"      10 '@ptrCast'
limit "@alignCast"        "$ALIGNCAST"     1 '@alignCast'
# Each of these would be a genuine problem in a parser for untrusted input.
limit "@constCast"        "$CONSTCAST"     0 '@constCast'
limit "@setRuntimeSafety" "$NO_SAFETY"     0 '@setRuntimeSafety'
limit "inline assembly"   "$ASM"           0 'asm '
limit "unreachable"       "$UNREACHABLE"   0 'unreachable'
limit "volatile"          "$VOLATILE"      0 'volatile'
limit "force unwrap (.?)" "$FORCE_UNWRAP"  0 '\.?'

mkdir -p "$ROOT/artifacts"
{
cat <<EOF
{
  "schema": "pdjson-zig/safety-report@2",
  "scope": "src/ only (the shipped library). Tests and tools are not scanned.",
  "definition": "Zig has no 'unsafe' keyword. This counts operations that can reinterpret memory, bypass a runtime check, or read uninitialised storage. Comment text is stripped before scanning.",
  "shipped_optimize_mode": "ReleaseSafe -- bounds checks, overflow checks and illegal-behaviour detection are active in the artifact this project distributes, including in the benchmark numbers reported as 'zig-safe'.",
  "counts": {
    "ptrCast": $PTRCAST,
    "alignCast": $ALIGNCAST,
    "constCast": $CONSTCAST,
    "intCast": $INTCAST,
    "bitCast": $BITCAST,
    "truncate": $TRUNCATE,
    "force_unwrap": $FORCE_UNWRAP,
    "undefined_initializers": $UNDEFINED,
    "unreachable": $UNREACHABLE,
    "setRuntimeSafety": $NO_SAFETY,
    "inline_asm": $ASM,
    "volatile": $VOLATILE
  },
  "budgets": {
    "ptrCast": 10, "alignCast": 1, "constCast": 0, "setRuntimeSafety": 0,
    "inline_asm": 0, "unreachable": 0, "volatile": 0, "force_unwrap": 0
  },
  "occurrences": {
EOF
printf '    "ptrCast": '   ; json_list '@ptrCast'   ; printf ',\n'
printf '    "alignCast": ' ; json_list '@alignCast' ; printf ',\n'
printf '    "bitCast": '   ; json_list '@bitCast'   ; printf ',\n'
printf '    "intCast": '   ; json_list '@intCast'   ; printf ',\n'
printf '    "truncate": '  ; json_list '@truncate'  ; printf ',\n'
printf '    "undefined": ' ; json_list '= undefined'; printf '\n'
cat <<'EOF'
  },
  "justifications": {
    "ptrCast": "Two boundaries only. (a) json_allocator is a C interface returning void*, so every malloc/realloc/free crossing needs a cast; these are confined to cMalloc/cRealloc/cFree and close() in src/parser.zig. (b) json_get_string and json_get_error must return a char* into buffers the parser guarantees are NUL terminated, and json_open_buffer receives a const void*; these are in src/c_api.zig. No cast reinterprets one object type as another.",
    "alignCast": "One use. realloc's result is specified to be suitably aligned for any object type; this converts it to the container-stack element type.",
    "bitCast": "Integer to integer only, never pointers: reproducing the platform's 'char' signedness when reading the input buffer, printf's %c conversion, and the long->size_t conversion json_get_context performs.",
    "truncate": "printf %c semantics and byte extraction from c_int. Well defined for all inputs.",
    "undefined_initializers": "Local storage fully written before any read. The 4-byte UTF-8 staging buffer uses @splat(0) rather than undefined specifically so a short read cannot expose stack bytes; json_stream values are filled completely by the parser's own init().",
    "no_panic_on_untrusted_input": "Counters that C allows to wrap (source.position, lineno, ntokens, container counts) use explicit wrapping operators, so no input can trigger an overflow panic. Multiplications that could overflow a size computation are checked and reported as 'out of memory' rather than wrapping into a short allocation.",
    "bounds": "Every container-stack access goes through currentFrame(), which checks both the empty sentinel and stack_top < stack_size. This is what keeps the port memory-safe in the state where the original reads an unallocated slot (docs/upstream-bug-oom-stack.md)."
  },
EOF
printf '  "result": "%s"\n}\n' "$(if [ "$fail" -eq 0 ]; then echo pass; else echo fail; fi)"
} > "$OUT"

if [ "$fail" -eq 0 ]; then
    echo "  @ptrCast=$PTRCAST (allocator + char* boundaries), @alignCast=$ALIGNCAST"
    echo "  @constCast=$CONSTCAST  unreachable=$UNREACHABLE  force-unwrap=$FORCE_UNWRAP  @setRuntimeSafety=$NO_SAFETY  asm=$ASM"
    echo "  every occurrence enumerated in artifacts/safety-report.json"
    exit 0
fi
exit 1
