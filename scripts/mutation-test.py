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
                    if got != expected[(m, f)]:
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
        "schema": "pdjson-zig/mutation-report@1",
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
    out = ROOT / "artifacts" / "mutation-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"\nmutation testing: {caught} caught, {survived} survived, "
          f"{broken} not evaluated (of {len(MUTANTS)})")
    print(f"wrote {out.relative_to(ROOT)}")
    return 1 if (survived or broken) else 0


if __name__ == "__main__":
    sys.exit(main())
