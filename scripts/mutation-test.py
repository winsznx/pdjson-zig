#!/usr/bin/env python3
"""Mutation testing for the differential harness.

A comparison harness that never fails proves nothing. This script deliberately
breaks the Zig implementation, one small change at a time, and asserts that the
fixed-corpus differential *notices*. A mutant that survives is a blind spot in
the harness, and is reported as a failure of this script -- not as a pass.

Each mutant is built from a throwaway copy of the tree, so the real sources are
never touched. A mutation whose pattern no longer matches the source is also a
failure, so this cannot silently rot into a no-op as the code changes.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
C_BIN = ROOT / "build" / "transcript_c"
C_SAN_BIN = ROOT / "build" / "transcript_c_asan"
FIXTURES = sorted((ROOT / "tests" / "conformance" / "fixtures").glob("*.json"))
MODES = ["next", "peek", "nostream", "skip", "sep", "oom:0", "oom:2"]

# name -> (file, old, new). Each change is small, plausible, and should be
# visible in at least one transcript field.
MUTANTS: dict[str, tuple[str, str, str]] = {
    "lineno-not-counted": (
        "src/parser.zig",
        "        if (c == '\\n') self.lineno +%= 1;\n",
        "",
    ),
    "depth-off-by-one": (
        "src/parser.zig",
        "    return self.stack_top +% 1;",
        "    return self.stack_top;",
    ),
    "oom-message-typo": (
        "src/parser.zig",
        '    errStr(self, "out of memory");',
        '    errStr(self, "out of memoryy");',
    ),
    "escape-b-wrong": (
        "src/parser.zig",
        "        'b' => 0x08,",
        "        'b' => 0x07,",
    ),
    "surrogate-high-range": (
        "src/parser.zig",
        "    if (cp >= 0xd800 and cp <= 0xdbff) {",
        "    if (cp >= 0xd800 and cp <= 0xdbfe) {",
    ),
    "utf8-accept-overlong": (
        "src/parser.zig",
        "        0xC0, 0xC1 => 0,",
        "        0xC0, 0xC1 => 2,",
    ),
    "number-no-terminator": (
        "src/parser.zig",
        "    return if (pushchar(self, 0)) .number else .err;\n}\n\nfn isMatch",
        "    return .number;\n}\n\nfn isMatch",
    ),
    "control-char-allowed": (
        "src/parser.zig",
        "    return c >= 0 and (c < 0x20 or c == 0x22 or c == 0x5c);",
        "    return c >= 0 and (c < 0x1f or c == 0x22 or c == 0x5c);",
    ),
    "position-not-advanced-on-peek": (
        "src/parser.zig",
        "    if (c != EOF) source.position +%= 1;\n    return c;\n}\n\npub fn streamGet",
        "    if (c != EOF and c != ' ') source.position +%= 1;\n    return c;\n}\n\npub fn streamGet",
    ),
    "strtod-no-conversion-value": (
        "src/strtod.zig",
        "    return .{ .value = 0, .consumed = 0 };",
        "    return .{ .value = 1, .consumed = 0 };",
    ),
    # An off-by-one at the 127-byte boundary would be an *equivalent* mutant:
    # the longest diagnostic the library can emit is 62 bytes, so that boundary
    # is unreachable and no observation could distinguish it. The limit is
    # lowered to 48 bytes instead, which real diagnostics do cross -- e.g.
    # "surrogate pair continuation \uXXXX out of range (dc00-dfff)".
    "errmsg-truncation": (
        "src/errmsg.zig",
        "        if (self.fill + 1 >= self.buf.len) return;",
        "        if (self.fill + 80 >= self.buf.len) return;",
    ),
    "buffer-peek-unsigned": (
        "src/parser.zig",
        "    return @as(c_char, @bitCast(byte));",
        "    return byte;",
    ),
}



# ---------------------------------------------------------------------------
# Detection.
#
# "12/12 mutants caught" is only meaningful if the comparison doing the catching
# is actually sensitive to what it claims to compare. A harness that compared
# only the event sequence would still catch most of these mutants, and would
# report the same 12/12 while being blind to token bytes, number values, line
# numbers, positions, depths and error text.
#
# So the comparison is a named, swappable function, and --self-test checks each
# one field by field: the real comparator must notice a change in *every*
# transcript field, and each deliberately weakened comparator must miss exactly
# the fields it ignores. A weakening that changes nothing would mean the
# strength was never there.
# ---------------------------------------------------------------------------

def _records(out: bytes) -> list[dict]:
    recs = []
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("{"):
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                recs.append({"unparsed": line})
    return recs


def detect_full(a: bytes, b: bytes) -> bool:
    """The real one: byte-identical or it is a difference."""
    return a != b


def detect_event_only(a: bytes, b: bytes) -> bool:
    """Deliberately weak: the event sequence and nothing else."""
    ea = [r.get("event") for r in _records(a) if "event" in r]
    eb = [r.get("event") for r in _records(b) if "event" in r]
    return ea != eb


def detect_record_count(a: bytes, b: bytes) -> bool:
    """Deliberately weak: how many records, and nothing about them."""
    return len(_records(a)) != len(_records(b))


def detect_first_record(a: bytes, b: bytes) -> bool:
    """Deliberately weak: only the first record."""
    ra, rb = _records(a), _records(b)
    return (ra[:1] or [None]) != (rb[:1] or [None])


DETECTORS = {
    "full": detect_full,
    "event-only": detect_event_only,
    "record-count": detect_record_count,
    "first-record": detect_first_record,
}

# Every field a transcript record carries, and a changed value for it. The real
# comparator has to notice each one; anything it does not notice is a field the
# differential is silently ignoring across all 6,104 comparisons.
FIELD_PERTURBATIONS = {
    "event": ("NUMBER", "STRING"),
    "tok": ("3132", "3133"),
    "toklen": (2, 3),
    "num": ("3ff0000000000000", "3ff0000000000001"),
    "line": (1, 2),
    "pos": (5, 6),
    "depth": (1, 2),
    "ctx": ("ARRAY", "OBJECT"),
    "ctxn": (1, 2),
    "err": (None, "6f6f7073"),
    "op": ("next", "peek"),
    "seq": (0, 1),
}


def detector_self_test() -> int:
    """Check the comparators against synthetic transcripts, one field at a time."""
    def transcript(**overrides) -> bytes:
        base = {"seq": 0, "op": "next", "event": "NUMBER", "tok": "3132",
                "toklen": 2, "num": "3ff0000000000000", "line": 1, "pos": 5,
                "depth": 1, "ctx": "ARRAY", "ctxn": 1, "err": None}
        base.update(overrides)
        head = json.dumps({"schema": "pdjson-zig/transcript@2", "mode": "next",
                           "bytes": 7})
        return (head + "\n" + json.dumps(base) + "\n"
                + json.dumps({"end": True, "records": 1}) + "\n").encode()

    baseline = transcript()
    failures = 0

    print("The real comparator must notice a change in every field:")
    for field, (_, changed) in FIELD_PERTURBATIONS.items():
        if detect_full(baseline, transcript(**{field: changed})):
            print(f"  ok    {field}")
        else:
            failures += 1
            print(f"  FAIL  {field} changed and the comparison did not notice it")

    print("\nA comparator must not fire when nothing changed:")
    for name, fn in DETECTORS.items():
        if fn(baseline, transcript()):
            failures += 1
            print(f"  FAIL  {name} reports a difference between identical output")
        else:
            print(f"  ok    {name}")

    # Each weakening must actually be weaker, or "12/12 under full detection"
    # would be an unearned number.
    print("\nEach deliberately weakened comparator must miss what it ignores:")
    weakenings = [
        ("event-only", "line", "line numbers"),
        ("event-only", "tok", "token bytes"),
        ("event-only", "num", "number values"),
        ("event-only", "err", "error text"),
        ("event-only", "pos", "byte positions"),
        ("event-only", "depth", "container depth"),
        ("record-count", "event", "the events themselves"),
        ("first-record", "line", "anything past the first record"),
    ]
    for name, field, what in weakenings:
        changed = transcript(**{field: FIELD_PERTURBATIONS[field][1]})
        if name == "first-record":
            # Put the change in a second record so "first record only" is the
            # thing being tested rather than the field.
            changed = (baseline.decode().replace(
                '{"end"', json.dumps({"seq": 1, "op": "next", "event": "DONE",
                                      "line": 9}) + "\n" + '{"end"')).encode()
        if DETECTORS[name](baseline, changed):
            failures += 1
            print(f"  FAIL  {name} noticed {what}; it is not the weakening it claims")
        else:
            print(f"  ok    {name} is blind to {what}")

    print(f"\ndetector self-test: {failures} failure(s)")

    out = ROOT / "artifacts" / "mutation" / "detector-selftest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "schema": "pdjson-zig/detector-selftest@1",
        "method": ("Each transcript field is perturbed in a synthetic record and "
                   "the comparison is required to notice. Each deliberately "
                   "weakened comparator is then required to be blind to what it "
                   "ignores -- a weakening that changed nothing would mean the "
                   "strength was never there."),
        "fields_checked": sorted(FIELD_PERTURBATIONS),
        "fields_detected": len(FIELD_PERTURBATIONS) - failures,
        "comparators": sorted(DETECTORS),
        "weakenings_checked": len(weakenings),
        "failures": failures,
        "limitation": ("Synthetic transcripts, not parser output: this proves the "
                       "comparison notices a changed field, not that the parser "
                       "can produce every such change."),
    }, indent=2) + "\n")
    print(f"  wrote {out.relative_to(ROOT)}")
    return 1 if failures else 0


# A mutant that hangs is still a detected mutant, but it must not hang *this*
# script. Every subprocess below is bounded.
RUN_TIMEOUT = 20
BUILD_TIMEOUT = 900


def upstream_has_ub(mode: str, path: pathlib.Path) -> bool:
    """Does an ASan+UBSan build of the pinned original report an error here?"""
    if not C_SAN_BIN.exists():
        return False
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=0:abort_on_error=0:exitcode=86"
    env["UBSAN_OPTIONS"] = "print_stacktrace=0:halt_on_error=0"
    try:
        p = subprocess.run([str(C_SAN_BIN), mode, str(path)],
                           capture_output=True, timeout=RUN_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return True
    err = p.stderr.decode("utf-8", "replace")
    return ("AddressSanitizer" in err or "runtime error:" in err
            or p.returncode == 86)


def oracle(mode: str, path: pathlib.Path):
    """Oracle output, or None when this case is not a valid comparison point.

    A case is excluded when the pinned C original invokes undefined behaviour
    on it -- either crashing outright or, as in the stack-growth failure path,
    reading past its allocation and carrying on with whatever it found. Both
    are documented in docs/upstream-bug-oom-stack.md.

    Excluding them matters. On those cases *every* mutant differs from the
    oracle, for reasons that have nothing to do with the mutation: the C side
    either died mid-transcript or emitted garbage from unallocated memory. The
    real pipeline classifies them as upstream UB rather than divergences, so
    counting them here would let this script certify itself. Whether a case is
    excluded is decided by a sanitizer, not by judgement."""
    p = subprocess.run([str(C_BIN), mode, str(path)],
                       capture_output=True, timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return None
    if upstream_has_ub(mode, path):
        return None
    return p.stdout


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="check the comparators field by field; builds nothing")
    ap.add_argument("--detector", default="full", choices=sorted(DETECTORS),
                    help="deliberately weaken detection, to show the strength "
                         "of the real comparison is doing the work")
    ap.add_argument("--out", default="artifacts/mutation-report.json")
    args = ap.parse_args()

    if args.self_test:
        return detector_self_test()

    differs_fn = DETECTORS[args.detector]

    if not C_BIN.exists():
        print(f"missing {C_BIN}; run 'make build' first", file=sys.stderr)
        return 2

    # Cache the oracle output once; it does not change between mutants.
    expected: dict[tuple[str, pathlib.Path], bytes] = {}
    excluded = 0
    for f in FIXTURES:
        for m in MODES:
            out = oracle(m, f)
            if out is None:
                excluded += 1
                continue
            expected[(m, f)] = out
    print(f"comparable cases: {len(expected)} "
          f"({excluded} excluded because the pinned C original crashes there)",
          flush=True)

    results = []
    caught = survived = broken = 0

    with tempfile.TemporaryDirectory(prefix="pdjson-zig-mutants-") as tmp:
        tmpdir = pathlib.Path(tmp)
        for name, (relfile, old, new) in MUTANTS.items():
            work = tmpdir / name
            work.mkdir()
            for item in ("build.zig", "src", "tools", "include"):
                src = ROOT / item
                dst = work / item
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

            target = work / relfile
            text = target.read_text()
            if text.count(old) != 1:
                print(f"MUTANT {name}: pattern matches {text.count(old)} times "
                      f"(expected exactly 1) -- stale mutation definition", flush=True)
                results.append({"mutant": name, "status": "pattern_stale"})
                broken += 1
                continue
            target.write_text(text.replace(old, new, 1))

            try:
                build = subprocess.run(
                    ["zig", "build", "--prefix", str(work / "out")],
                    cwd=work, capture_output=True, timeout=BUILD_TIMEOUT)
            except subprocess.TimeoutExpired:
                print(f"MUTANT {name}: build timed out", flush=True)
                results.append({"mutant": name, "status": "build_timeout"})
                broken += 1
                continue
            if build.returncode != 0:
                print(f"MUTANT {name}: did not compile -- not evidence either way",
                      flush=True)
                results.append({"mutant": name, "status": "build_failed"})
                broken += 1
                continue

            mutant_bin = work / "out" / "bin" / "transcript_zig"
            detected_by = None
            for f in FIXTURES:
                for m in MODES:
                    if (m, f) not in expected:
                        continue
                    try:
                        got = subprocess.run([str(mutant_bin), m, str(f)],
                                             capture_output=True,
                                             timeout=RUN_TIMEOUT).stdout
                    except subprocess.TimeoutExpired:
                        # A mutant that stops terminating is a behavioural
                        # difference the harness would also surface as a
                        # timeout on a real regression.
                        detected_by = {"fixture": f.name, "mode": m,
                                       "how": "timeout"}
                        break
                    if differs_fn(expected[(m, f)], got):
                        detected_by = {"fixture": f.name, "mode": m,
                                       "how": "transcript differs"}
                        break
                if detected_by:
                    break

            if detected_by:
                caught += 1
                print(f"MUTANT {name}: CAUGHT by {detected_by['fixture']} "
                      f"(mode {detected_by['mode']}, {detected_by['how']})",
                      flush=True)
                results.append({"mutant": name, "status": "caught",
                                "detected_by": detected_by})
            else:
                survived += 1
                print(f"MUTANT {name}: SURVIVED  <-- harness blind spot", flush=True)
                results.append({"mutant": name, "status": "survived"})

    report = {
        "schema": "pdjson-zig/mutation-report@2",
        "detector": args.detector,
        "detector_note": ("'full' is byte-identical transcripts, the comparison "
                          "the real differential uses. The other detectors are "
                          "deliberately weaker and exist to show that the "
                          "strength is doing the work: run with --detector "
                          "event-only and mutants survive. "
                          "--self-test checks every comparator field by field."),
        "description": ("Deliberate defects injected into the Zig implementation. "
                        "Each must be detected by comparing against the C oracle "
                        "over the fixed corpus."),
        "fixtures": len(FIXTURES),
        "modes": MODES,
        "comparable_cases": len(expected),
        "excluded_cases": excluded,
        "exclusion_reason": ("(mode, fixture) pairs where an ASan+UBSan build of "
                             "the pinned C original reports an error are excluded. "
                             "Every mutant trivially differs on those, because the "
                             "C side crashed or emitted bytes from unallocated "
                             "memory; counting them would let this script certify "
                             "itself. The decision is made by a sanitizer, not by "
                             "judgement."),
        "mutants_defined": len(MUTANTS),
        "caught": caught,
        "survived": survived,
        "not_evaluated": broken,
        "results": results,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"\nmutation testing: {caught} caught, {survived} survived, "
          f"{broken} not evaluated (of {len(MUTANTS)})")
    print(f"wrote {out.relative_to(ROOT)}")
    return 1 if (survived or broken) else 0


if __name__ == "__main__":
    sys.exit(main())
