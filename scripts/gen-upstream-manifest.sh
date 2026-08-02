#!/bin/sh
# Regenerate artifacts/upstream-manifest.json from the pinned tree in upstream/pdjson.
#
# Run this only when intentionally re-pinning upstream. Routine verification uses
# scripts/verify-upstream-hashes.sh, which compares against the committed manifest.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$ROOT/scripts/lib-sha256.sh"

UPSTREAM_DIR="$ROOT/upstream/pdjson"
OUT="$ROOT/artifacts/upstream-manifest.json"

# Pinned identity. These are recorded facts about the clone, not derived values.
URL="https://github.com/skeeto/pdjson"
COMMIT="78fe04b820dc8817f540bdd87fb22887e0ef3981"
BRANCH="master"
COMMIT_DATE="2024-02-22T13:12:52+02:00"
LICENSE="Unlicense"

# category: source | header | test | build | documentation | license | ignore
categorize() {
    case "$1" in
        pdjson.c)        echo source ;;
        pdjson.h)        echo header ;;
        tests/*.c)       echo test ;;
        Makefile)        echo build ;;
        README.md)       echo documentation ;;
        UNLICENSE)       echo license ;;
        .gitignore)      echo ignore ;;
        *)               echo other ;;
    esac
}

files=$(cd "$UPSTREAM_DIR" && find . -type f | sed 's|^\./||' | LC_ALL=C sort)

mkdir -p "$ROOT/artifacts"
{
    printf '{\n'
    printf '  "schema": "pdjson-zig/upstream-manifest@1",\n'
    printf '  "upstream_url": "%s",\n' "$URL"
    printf '  "commit": "%s",\n' "$COMMIT"
    printf '  "branch": "%s",\n' "$BRANCH"
    printf '  "commit_date": "%s",\n' "$COMMIT_DATE"
    printf '  "license": "%s",\n' "$LICENSE"
    printf '  "hash_algorithm": "sha256",\n'
    printf '  "files": [\n'
    first=1
    for f in $files; do
        digest=$(sha256_of "$UPSTREAM_DIR/$f")
        bytes=$(wc -c < "$UPSTREAM_DIR/$f" | tr -d ' ')
        lines=$(wc -l < "$UPSTREAM_DIR/$f" | tr -d ' ')
        cat=$(categorize "$f")
        [ "$first" -eq 1 ] || printf ',\n'
        first=0
        printf '    {"path": "%s", "sha256": "%s", "category": "%s", "bytes": %s, "lines": %s}' \
            "$f" "$digest" "$cat" "$bytes" "$lines"
    done
    printf '\n  ]\n'
    printf '}\n'
} > "$OUT"

echo "wrote $OUT"
