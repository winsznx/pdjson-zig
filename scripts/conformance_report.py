#!/usr/bin/env python3
"""Classify both implementations against JSONTestSuite's expectations.

JSONTestSuite names each case by what a conforming parser should do:

  y_*  must be accepted
  n_*  must be rejected
  i_*  implementation-defined; either answer is conforming

This produces two separate results, and conflating them would be the mistake:

  * agreement between the Zig port and the pinned C original -- the equivalence
    claim, which must hold on every case;
  * agreement between the *original* and RFC 8259 -- a fact about upstream. A
    y_ case that pdjson rejects is a property of pdjson that this port
    faithfully reproduces, not a defect introduced by porting.

The suite is used to tell those apart. It is not a pass/fail gate on the port.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = ROOT / "tests" / "conformance" / "JSONTestSuite" / "test_parsing"
C_BIN = ROOT / "build" / "transcript_c"
ZIG_BIN = ROOT / "zig-out" / "bin" / "transcript_zig"


def accepted(binary: pathlib.Path, path: pathlib.Path) -> bool | None:
    """True if the parser consumed the document without error.

    Uses strict (non-streaming) mode, which is what maps onto "is this a valid
    JSON document" -- streaming mode deliberately allows trailing values.
    """
    try:
        p = subprocess.run([str(binary), "nostream", str(path)],
                           capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None
    if p.returncode != 0:
        return None
    saw_error = False
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        if '"event":"ERROR"' in line:
            saw_error = True
    return not saw_error


def main() -> int:
    if not SUITE.is_dir():
        print("  JSONTestSuite not present; skipping")
        return 0
    for b in (C_BIN, ZIG_BIN):
        if not b.exists():
            print(f"missing {b}; run 'make build' first", file=sys.stderr)
            return 2

    cases = sorted(SUITE.glob("*.json"))
    rows = []
    agree = disagree = 0
    counts = {"y": [0, 0], "n": [0, 0], "i": [0, 0]}  # [conforming, total]

    for path in cases:
        kind = path.name[0]
        if kind not in ("y", "n", "i"):
            continue
        c_ok = accepted(C_BIN, path)
        z_ok = accepted(ZIG_BIN, path)

        same = c_ok == z_ok
        agree += same
        disagree += not same

        counts[kind][1] += 1
        if kind == "y" and c_ok:
            counts[kind][0] += 1
        elif kind == "n" and c_ok is False:
            counts[kind][0] += 1
        elif kind == "i":
            counts[kind][0] += 1  # either answer conforms

        if not same or (kind == "y" and not c_ok) or (kind == "n" and c_ok):
            rows.append({
                "case": path.name,
                "expected": {"y": "accept", "n": "reject", "i": "either"}[kind],
                "c_accepted": c_ok,
                "zig_accepted": z_ok,
                "implementations_agree": same,
                "classification": (
                    "PORT DEFECT: the port and the original disagree" if not same
                    else "upstream behaviour, faithfully reproduced by the port"
                ),
            })

    # Fold in the differential's own figures so the whole conformance story
    # lives in one artifact. Splitting them meant a claim about "N drive modes"
    # cited a file that did not contain N, which is how the mode list drifted
    # from 11 to 5 without anyone noticing.
    diff_path = ROOT / "artifacts" / "differential-jsontestsuite.json"
    diff = {}
    if diff_path.exists():
        try:
            diff = json.loads(diff_path.read_text())
        except (json.JSONDecodeError, OSError):
            diff = {}

    report = {
        "schema": "pdjson-zig/conformance-report@2",
        "corpus": "nst/JSONTestSuite test_parsing",
        "cases": len(cases),
        "drive_modes": len(diff.get("modes", [])),
        "modes": diff.get("modes", []),
        "comparisons": diff.get("comparisons", 0),
        "input_sources": len(diff.get("by_source", {})),
        "divergences": diff.get("divergences"),
        "implementations_agree": agree,
        "implementations_disagree": disagree,
        "note": ("'disagree' is the only number that reflects on this port. The "
                 "y_/n_ columns describe how the pinned original relates to "
                 "RFC 8259; the port reproduces that relationship by design."),
        "upstream_vs_rfc8259": {
            "must_accept": {"conforming": counts["y"][0], "total": counts["y"][1]},
            "must_reject": {"conforming": counts["n"][0], "total": counts["n"][1]},
            "implementation_defined": {"total": counts["i"][1]},
        },
        "notable_cases": rows[:200],
    }

    out = ROOT / "artifacts" / "conformance-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"  {len(cases)} cases: implementations agree on {agree}, "
          f"disagree on {disagree}")
    print(f"  upstream vs RFC 8259: "
          f"must-accept {counts['y'][0]}/{counts['y'][1]}, "
          f"must-reject {counts['n'][0]}/{counts['n'][1]}")
    print(f"  wrote artifacts/conformance-report.json")
    return 1 if disagree else 0


if __name__ == "__main__":
    sys.exit(main())
