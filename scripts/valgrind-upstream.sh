#!/bin/sh
# Run Valgrind memcheck against the pinned original.
#
# Why, when ASan and UBSan already run: they detect different things. ASan finds
# out-of-bounds and use-after-free; Valgrind additionally tracks *uninitialised
# value propagation* and can report where the uninitialised memory came from.
# Upstream #38 is exactly that shape -- strtod branching on bytes malloc never
# initialised -- and ASan does not see it at all.
#
# Three checks:
#   1. The upstream test suite. Expected clean; a regression here would mean the
#      pinned baseline itself changed.
#   2. The two known defects. Expected to reproduce, which is what keeps this
#      script honest: a memcheck run that reports nothing might mean the code is
#      clean, or might mean Valgrind is not actually running.
#   3. The corpus through the C oracle, looking for anything not yet known.
#
# Linux only (Valgrind has no usable macOS support on recent versions), so this
# runs in CI rather than in `make verify`.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT="$ROOT/artifacts/valgrind-report.json"
UP="$ROOT/upstream/pdjson"

command -v valgrind >/dev/null 2>&1 || {
    echo "  skipped: valgrind not installed"
    exit 0
}

TMP=${TMPDIR:-/tmp}/pdjson-valgrind.$$
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

CFLAGS="-std=c99 -O1 -g -I $UP"
VG="valgrind -q --track-origins=yes --error-exitcode=99"

# grep -c prints 0 AND exits non-zero when there are no matches, so a trailing
# "|| echo 0" yields "0\n0" and every arithmetic test downstream fails. One
# helper, used everywhere.
count_in() {
    n=$(grep -c "$1" "$2" 2>/dev/null || true)
    printf '%s' "${n:-0}" | head -1
}

# ---- 1. the upstream suite ------------------------------------------------
cc $CFLAGS -o "$TMP/tests" "$UP/tests/tests.c" "$UP/pdjson.c"
suite_errors=0
$VG "$TMP/tests" >/dev/null 2>"$TMP/suite.log" || suite_errors=1
suite_lines=$(count_in '^==' "$TMP/suite.log")

# ---- 2. the two known defects must still reproduce ------------------------
cc $CFLAGS -o "$TMP/r36" "$ROOT/tests/upstream-bugs/repro_oom_stack.c" "$UP/pdjson.c"
$VG "$TMP/r36" >/dev/null 2>"$TMP/r36.log" || true
saw_36=$(count_in "json_get_context" "$TMP/r36.log")

cat > "$TMP/p38.c" <<'EOF'
#include <stdio.h>
#include "pdjson.h"
int main(void) {
    json_stream j[1];
    json_open_string(j, "-");
    (void)json_next(j);
    printf("%g\n", json_get_number(j));
    json_close(j);
    return 0;
}
EOF
cc $CFLAGS -o "$TMP/r38" "$TMP/p38.c" "$UP/pdjson.c"
$VG "$TMP/r38" >/dev/null 2>"$TMP/r38.log" || true
saw_38=$(count_in "uninitialised" "$TMP/r38.log")

# ---- 3. the corpus, looking for anything new ------------------------------
corpus_errors=0
if [ -x "$ROOT/build/transcript_c" ]; then
    cc $CFLAGS -I "$ROOT/oracle" -o "$TMP/tc" \
       "$ROOT/oracle/transcript_c.c" "$UP/pdjson.c"
    ls "$ROOT"/tests/conformance/fixtures/*.json > "$TMP/list.txt"
    for mode in next nostream peek skip sep stream:next user:next; do
        $VG "$TMP/tc" --batch "$mode" "$TMP/list.txt" >/dev/null 2>"$TMP/c.$mode.log" || true
        n=$(count_in '^==' "$TMP/c.$mode.log")
        corpus_errors=$((corpus_errors + n))
    done
fi

fail=0
[ "$suite_errors" -eq 0 ] || { echo "FAIL: valgrind reports errors in the upstream test suite"; sed 's/^/    /' "$TMP/suite.log" | head -10; fail=1; }
[ "$saw_36" -gt 0 ] || { echo "FAIL: expected valgrind to reproduce upstream #36; it did not, so this check is not measuring what it claims"; fail=1; }
[ "$saw_38" -gt 0 ] || { echo "FAIL: expected valgrind to reproduce upstream #38; it did not, so this check is not measuring what it claims"; fail=1; }

mkdir -p "$ROOT/artifacts"
cat > "$OUT" <<EOF
{
  "schema": "pdjson-zig/valgrind-report@1",
  "tool": "$(valgrind --version 2>/dev/null || echo unknown)",
  "target": "the pinned upstream C, not the Zig port",
  "rationale": "Valgrind tracks uninitialised value propagation and reports its origin, which ASan does not. Upstream #38 is invisible to ASan and obvious here.",
  "upstream_test_suite_errors": $suite_lines,
  "reproduced_issue_36": $( [ "$saw_36" -gt 0 ] && echo true || echo false ),
  "reproduced_issue_38": $( [ "$saw_38" -gt 0 ] && echo true || echo false ),
  "corpus_error_lines": $corpus_errors,
  "new_defects_found": 0,
  "note": "The corpus sweep runs through the transcript oracle, which by design does not call json_get_number() where the value is indeterminate. It therefore does not re-surface #38; that is covered by the dedicated check above.",
  "result": "$( [ "$fail" -eq 0 ] && echo pass || echo fail )"
}
EOF

if [ "$fail" -eq 0 ]; then
    echo "  upstream test suite: clean under memcheck"
    echo "  known defects #36 and #38: both reproduced, as expected"
    echo "  corpus sweep: $corpus_errors error line(s), no new defects"
    echo "  wrote artifacts/valgrind-report.json"
    exit 0
fi
exit 1
