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
    --modes next,nostream,peek,skip,sep \
    --label jsontestsuite \
    --out artifacts/differential-jsontestsuite.json \
    --quiet

echo "  classifying the pinned original against the suite's expectations"
python3 "$ROOT/scripts/conformance_report.py"
