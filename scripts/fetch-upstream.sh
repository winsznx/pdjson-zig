#!/bin/sh
# Independently re-establish provenance.
#
# upstream/pdjson/ is a committed verbatim copy (DECISIONS.md D-01), which makes
# verification offline and hash-checkable. This script closes the remaining
# question -- "is that copy actually what upstream published?" -- by cloning
# from GitHub at the pinned commit into a scratch directory and diffing.
#
# It is not part of `make verify`, because verification must not require the
# network. Run it when you want to confirm the pin against the real repository.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
URL="https://github.com/skeeto/pdjson"
COMMIT="78fe04b820dc8817f540bdd87fb22887e0ef3981"
DEST=${1:-${TMPDIR:-/tmp}/pdjson-upstream-check.$$}

command -v git >/dev/null 2>&1 || { echo "FAIL: git not found" >&2; exit 1; }

echo "cloning $URL at $COMMIT into $DEST"
rm -rf "$DEST"
git clone --quiet "$URL" "$DEST"
git -C "$DEST" checkout --quiet "$COMMIT"

ACTUAL=$(git -C "$DEST" rev-parse HEAD)
if [ "$ACTUAL" != "$COMMIT" ]; then
    echo "FAIL: checked out $ACTUAL, expected $COMMIT" >&2
    exit 1
fi

rm -rf "$DEST/.git"

if diff -r "$DEST" "$ROOT/upstream/pdjson" >/dev/null 2>&1; then
    echo "OK: upstream/pdjson matches $URL at $COMMIT byte for byte"
    echo "    (also verified against artifacts/upstream-manifest.json by"
    echo "     scripts/verify-upstream-hashes.sh, which needs no network)"
    rm -rf "$DEST"
    exit 0
fi

echo "FAIL: the committed copy differs from upstream at the pinned commit:" >&2
diff -r "$DEST" "$ROOT/upstream/pdjson" >&2 || true
exit 1
