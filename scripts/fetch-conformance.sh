#!/bin/sh
# Fetch nst/JSONTestSuite as an independent standards reference.
#
# Not vendored: it is a separate MIT-licensed project and this repository should
# not carry a copy of it. Not required by `make verify` either, since
# verification must work offline.
#
# What it is for: pdjson is a *behavioural* oracle for this port, not a
# *standards* oracle. When the two implementations agree, that says they behave
# the same -- it says nothing about whether either follows RFC 8259. An
# independent corpus is what separates "the port is wrong", "the original is
# wrong", and "the standard leaves this open".
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DEST="$ROOT/tests/conformance/JSONTestSuite"
URL="https://github.com/nst/JSONTestSuite"

if [ -d "$DEST" ]; then
    echo "already present at tests/conformance/JSONTestSuite"
    exit 0
fi

command -v git >/dev/null 2>&1 || { echo "FAIL: git not found" >&2; exit 1; }

mkdir -p "$(dirname "$DEST")"
git clone --quiet --depth 1 "$URL" "$DEST"
rm -rf "$DEST/.git"

COUNT=$(find "$DEST/test_parsing" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
echo "fetched JSONTestSuite: $COUNT parsing cases in tests/conformance/JSONTestSuite/"
echo "license preserved at tests/conformance/JSONTestSuite/LICENSE"
echo "run: sh scripts/conformance-suite.sh"
