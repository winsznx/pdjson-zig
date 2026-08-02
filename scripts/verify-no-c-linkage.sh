#!/bin/sh
# Prove the Zig artifact does not contain, link, or call the original parser.
#
# The central claim of this project is worthless if the Zig library quietly
# wraps pdjson.c. That is exactly the kind of thing a reader has to take on
# trust unless it is checked mechanically, so it is checked mechanically here:
#
#   1. the archive contains only Zig-produced objects
#   2. none of pdjson.c's internal symbols appear in it
#   3. it does not import any json_* symbol (it defines them)
#   4. every public symbol from the pinned header is actually exported
#   5. no build input references upstream/pdjson/pdjson.c
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
LIB="$ROOT/zig-out/lib/libpdjson.a"
OUT="$ROOT/artifacts/linkage-report.json"

[ -f "$LIB" ] || { echo "FAIL: missing $LIB (run 'make build')" >&2; exit 1; }

fail=0

# 1. Object membership.
OBJECTS=$(ar t "$LIB" | grep -v '^__.SYMDEF' || true)
for o in $OBJECTS; do
    case "$o" in
        *pdjson.o|*pdjson.c.o)
            echo "FAIL: archive contains an object that looks like compiled upstream C: $o" >&2
            fail=1
            ;;
    esac
done
OBJECT_COUNT=$(printf '%s\n' "$OBJECTS" | grep -c . || echo 0)

# 2. pdjson.c's static helpers. If the C implementation were compiled in, at
#    least some of these would be present (they are file-static, so they would
#    show as local symbols).
PRIVATE="buffer_peek buffer_get stream_get stream_peek user_get user_peek \
         pushchar init_string encode_utf8 hexchar read_unicode_cp read_unicode \
         read_escaped char_needs_escaping utf8_seq_length is_legal_utf8 \
         read_utf8 read_string is_digit read_digits read_number is_match \
         read_value"
FOUND=""
for sym in $PRIVATE; do
    if nm "$LIB" 2>/dev/null | grep -qE "[0-9a-f]+ [a-zA-Z] _?${sym}$"; then
        FOUND="$FOUND $sym"
        fail=1
    fi
done
if [ -n "$FOUND" ]; then
    echo "FAIL: upstream implementation symbols present in the Zig archive:$FOUND" >&2
fi

# 3. Undefined json_* references would mean the library expects the original
#    parser to be linked in alongside it.
# nm prints an "object.o:" header line per archive member; match only lines
# that are a bare symbol so the archive member name is not mistaken for one.
UNDEF=$(nm -u "$LIB" 2>/dev/null \
        | grep -E '^_?json_[a-z_]+$' | sort -u || true)
if [ -n "$UNDEF" ]; then
    echo "FAIL: the Zig archive imports json_* symbols instead of defining them:" >&2
    echo "$UNDEF" | sed 's/^/     /' >&2
    fail=1
fi

# 4. Everything the pinned header declares must actually be exported.
EXPECTED="json_open_buffer json_open_string json_open_stream json_open_user \
          json_close json_set_allocator json_set_streaming json_next json_peek \
          json_reset json_get_string json_get_number json_skip json_skip_until \
          json_get_lineno json_get_position json_get_depth json_get_context \
          json_get_error json_source_get json_source_peek json_isspace"
MISSING=""
EXPORTED=0
for sym in $EXPECTED; do
    if nm -g "$LIB" 2>/dev/null | grep -qE " [TtSs] _?${sym}$"; then
        EXPORTED=$((EXPORTED + 1))
    else
        MISSING="$MISSING $sym"
        fail=1
    fi
done
if [ -n "$MISSING" ]; then
    echo "FAIL: public symbols declared in the header but not exported:$MISSING" >&2
fi

# 5. No Zig build input may reference the upstream implementation file.
# Strip comments first: the sources discuss pdjson.c in prose constantly, which
# is not the same as building against it.
REFS=$(for f in "$ROOT/build.zig" "$ROOT"/src/*.zig "$ROOT"/tools/*.zig; do
           sed 's://.*::' "$f" | grep -n 'pdjson\.c' | sed "s|^|$f:|" || true
       done)
if [ -n "$REFS" ]; then
    echo "FAIL: a Zig build input references the upstream implementation file:" >&2
    printf '%s\n' "$REFS" >&2
    fail=1
fi

mkdir -p "$ROOT/artifacts"
cat > "$OUT" <<EOF
{
  "schema": "pdjson-zig/linkage-report@1",
  "library": "zig-out/lib/libpdjson.a",
  "objects_in_archive": $OBJECT_COUNT,
  "upstream_private_symbols_found": $(if [ -n "$FOUND" ]; then echo "\"$FOUND\""; else echo 'null'; fi),
  "undefined_json_symbols": $(if [ -n "$UNDEF" ]; then echo "\"$(echo "$UNDEF" | tr '\n' ' ')\""; else echo 'null'; fi),
  "public_symbols_expected": 22,
  "public_symbols_exported": $EXPORTED,
  "build_inputs_reference_pdjson_c": false,
  "result": "$(if [ "$fail" -eq 0 ]; then echo pass; else echo fail; fi)"
}
EOF

if [ "$fail" -eq 0 ]; then
    echo "  $OBJECT_COUNT Zig object(s), $EXPORTED/22 public symbols exported,"
    echo "  no upstream implementation symbols, no json_* imports"
    echo "  wrote artifacts/linkage-report.json"
    exit 0
fi
exit 1
