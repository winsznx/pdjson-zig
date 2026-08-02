#!/usr/bin/env python3
"""Record what the number-conversion torture tests actually cover.

C-15 claimed "a 661-point exponent sweep, digit strings up to 500 digits, 20,000
randomised decimal lexemes and 20,000 randomised hex floats" and cited
`artifacts/original-test-report.json`, which contains none of those numbers. The
figures lived only in the test source, where nothing checked them and nothing
would have noticed them drifting.

So they are derived from the source here, and the tests are run, and both go into
one artifact. The counts are *computed from the loop bounds the test actually
uses*, not copied alongside them: change `while (e <= 320)` and this number
changes with it.

  python3 scripts/number-torture-report.py
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST = ROOT / "tests" / "port" / "number_torture.zig"
OUT = ROOT / "artifacts" / "number-torture.json"


def find(pattern: str, text: str, what: str, problems: list) -> list[str]:
    m = re.search(pattern, text)
    if not m:
        problems.append(f"could not find {what} in {TEST.name}; the pattern this "
                        f"script derives it from no longer matches")
        return []
    return list(m.groups())


def main() -> int:
    if not TEST.exists():
        print(f"missing {TEST}", file=sys.stderr)
        return 2
    text = TEST.read_text()
    problems: list[str] = []

    lo, hi = find(r"var e: i32 = (-?\d+);\s*\n\s*while \(e <= (-?\d+)\)",
                  text, "the exponent sweep bounds", problems) or ["0", "-1"]
    sweep_points = int(hi) - int(lo) + 1

    digit_lengths = find(r"for \(\[_\]usize\{ ([\d, ]+) \}\) \|n\|",
                         text, "the digit-string lengths", problems)
    lengths = [int(x) for x in digit_lengths[0].split(",")] if digit_lengths else []

    randomised = [int(n) for n in re.findall(r"for \(0\.\.(\d+)\) \|_\|", text)]

    # Every `test "..."` block, so the artifact says what was run rather than
    # only how much.
    names = re.findall(r'^test "([^"]+)"', text, flags=re.M)

    run = subprocess.run(["zig", "build", "test"], cwd=ROOT,
                         capture_output=True, timeout=1800)
    passed = run.returncode == 0

    report = {
        "schema": "pdjson-zig/number-torture@1",
        "source": "tests/port/number_torture.zig",
        "method": ("Counts are derived from the loop bounds the tests actually "
                   "use, not written down beside them, so a bound that changes "
                   "changes this artifact. Every case is compared against C's "
                   "strtod bit for bit, including the number of bytes consumed."),
        "tests": len(names),
        "test_names": names,
        "exponent_sweep_points": sweep_points,
        "exponent_sweep_range": [int(lo), int(hi)],
        "exponent_sweep_lexemes": sweep_points * 2,
        "digit_string_lengths": lengths,
        "max_digit_string": max(lengths) if lengths else 0,
        "randomised_case_counts": randomised,
        "randomised_total": sum(randomised),
        "suite_passed": passed,
        "derivation_problems": problems,
        "limitation": ("This records coverage of the *conversion*, compared "
                       "against the platform's strtod. Agreement with libc is "
                       "compatibility; correctness under IEEE-754 is a separate "
                       "question, answered for the hex-float grammar by "
                       "scripts/hexfloat_oracle.py against an exact-integer "
                       "reference."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n")

    print(f"  {len(names)} number-conversion tests")
    print(f"    exponent sweep      {sweep_points} points "
          f"({int(lo)}..{int(hi)}), {sweep_points * 2} lexemes")
    print(f"    digit strings       up to {report['max_digit_string']} digits")
    print(f"    randomised          {sum(randomised)} cases "
          f"({' + '.join(str(n) for n in randomised)})")
    print(f"    suite               {'passed' if passed else 'FAILED'}")
    for p in problems:
        print(f"    PROBLEM: {p}")
    print(f"  wrote {OUT.relative_to(ROOT)}")
    return 1 if (problems or not passed) else 0


if __name__ == "__main__":
    sys.exit(main())
