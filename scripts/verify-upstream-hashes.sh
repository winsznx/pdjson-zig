#!/bin/sh
# Verify that upstream/pdjson still matches artifacts/upstream-manifest.json byte for byte.
#
# This is the provenance gate. It fails if any pinned upstream file was modified,
# added, or removed. Original tests are covered by it, so "we did not touch the
# original tests" is a mechanically checked statement rather than a promise.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$ROOT/scripts/lib-sha256.sh"

UPSTREAM_DIR="$ROOT/upstream/pdjson"
MANIFEST="$ROOT/artifacts/upstream-manifest.json"

[ -f "$MANIFEST" ] || { echo "FAIL: missing $MANIFEST" >&2; exit 1; }
[ -d "$UPSTREAM_DIR" ] || { echo "FAIL: missing $UPSTREAM_DIR" >&2; exit 1; }

fail=0
checked=0

# Extract "path sha256" pairs without requiring jq.
pairs=$(tr -d ' \n' < "$MANIFEST" \
    | tr '{' '\n' \
    | grep '"path":' \
    | sed -e 's/.*"path":"\([^"]*\)".*"sha256":"\([^"]*\)".*/\1 \2/')

for line in $(echo "$pairs" | tr ' ' '@'); do
    path=$(echo "$line" | cut -d@ -f1)
    want=$(echo "$line" | cut -d@ -f2)
    checked=$((checked + 1))
    if [ ! -f "$UPSTREAM_DIR/$path" ]; then
        echo "FAIL missing: $path"
        fail=$((fail + 1))
        continue
    fi
    got=$(sha256_of "$UPSTREAM_DIR/$path")
    if [ "$got" != "$want" ]; then
        echo "FAIL drift:   $path"
        echo "     expected $want"
        echo "     actual   $got"
        fail=$((fail + 1))
    fi
done

# Detect files present on disk but absent from the manifest.
manifest_paths=$(echo "$pairs" | cut -d' ' -f1 | LC_ALL=C sort)
disk_paths=$(cd "$UPSTREAM_DIR" && find . -type f | sed 's|^\./||' | LC_ALL=C sort)
extra=$(echo "$disk_paths" | grep -vxF "$manifest_paths" || true)
if [ -n "$extra" ]; then
    echo "FAIL untracked files inside pinned upstream tree:"
    echo "$extra" | sed 's/^/     /'
    fail=$((fail + 1))
fi

if [ "$fail" -ne 0 ]; then
    echo "upstream hash verification FAILED ($fail problem(s), $checked file(s) checked)" >&2
    exit 1
fi

echo "upstream hash verification OK ($checked files match artifacts/upstream-manifest.json)"
