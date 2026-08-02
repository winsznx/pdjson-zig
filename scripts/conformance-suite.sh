#!/bin/sh
# Run JSONTestSuite through both implementations, if it has been fetched.
#
# Two distinct questions are answered here and kept apart:
#
#   1. Do the two implementations agree on every case? That is the equivalence
#      claim, and it must hold. Handled by scripts/differential.py.
#
#   2. Does the *original* accept what RFC 8259 says it should? That is a
#      question about upstream, not about this port, and its answer is reported
#      as information -- an accept/reject disagreement with the suite's naming
#      convention is a fact about pdjson, not a defect in the port.
#
# The mode list is spelled out rather than defaulted, because this corpus is
# fetched on demand and is usually absent in CI -- so a drift between what this
# script passes and what the committed artifact records would go unnoticed. It
# did: the artifact recorded 11 modes while this line passed 5, and
# scripts/audit-claims.py is what surfaced it.
#
# Skips cleanly when the suite is absent, because `make verify` must work with
# no network.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SUITE="$ROOT/tests/conformance/JSONTestSuite/test_parsing"

if [ ! -d "$SUITE" ]; then
    echo "  skipped: JSONTestSuite not present"
    echo "  fetch it with: sh scripts/fetch-conformance.sh"
    exit 0
fi

echo "  running the equivalence comparison over JSONTestSuite"
python3 "$ROOT/scripts/differential.py" \
    --corpus tests/conformance/JSONTestSuite/test_parsing \
    --modes next,nostream,peek,skip,sep,after-end,stream:next,stream:nostream,stream:peek,user:next,user:nostream,user:peek \
    --label jsontestsuite \
    --out artifacts/differential-jsontestsuite.json \
    --quiet

echo "  classifying the pinned original against the suite's expectations"
python3 "$ROOT/scripts/conformance_report.py"
