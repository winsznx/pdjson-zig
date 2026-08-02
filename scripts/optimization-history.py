#!/usr/bin/env python3
"""Re-measure the two optimizations, so the narrative about them is backed.

The README tells a story: the first guess about why the port was slow was wrong,
0% was measured, and profiling found two different causes. The story was true and
the figures in it were real -- but they were measured during development and
nothing in the repository reproduced them, so they were assertions.
`scripts/audit-public-copy.py` flagged them, which is what it is for.

This rebuilds the "before" state of each optimization in a throwaway copy of the
tree and benchmarks it against the current one. Both variants are exact reverts
of one change:

  no-inline-pushchar   `inline fn pushchar` -> `fn pushchar`, so the hot store
                       goes back to being an out-of-line call, as it was before
                       the split into a fast path and pushcharSlow.
  generic-scan         the two specialised byte predicates in the number lexer
                       go back to a generic slice search, as `strchr` was
                       originally translated.

Both are built ReleaseSafe, the mode the project ships, and benchmarked with the
same harness and workloads as everything else.

  python3 scripts/optimization-history.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "optimization-history.json"

# (name, what it reverts, file, old, new)
VARIANTS = [
    ("no-inline-pushchar",
     "the token-buffer store is an out-of-line call again",
     "src/parser.zig",
     "inline fn pushchar(self: *Stream, c: c_int) bool {",
     "fn pushchar(self: *Stream, c: c_int) bool {"),
    ("generic-scan",
     "the number lexer's two byte tests go back to a generic slice search",
     "src/parser.zig",
     """fn isNonZeroDigitOrNul(c: c_int) bool {
    const ch: u8 = @truncate(@as(c_uint, @bitCast(c)));
    return ch == 0 or (ch >= '1' and ch <= '9');
}

fn isFractionOrExponentOrNul(c: c_int) bool {
    const ch: u8 = @truncate(@as(c_uint, @bitCast(c)));
    return ch == 0 or ch == '.' or ch == 'e' or ch == 'E';
}""",
     """fn isNonZeroDigitOrNul(c: c_int) bool {
    const ch: u8 = @truncate(@as(c_uint, @bitCast(c)));
    return std.mem.indexOfScalar(u8, "123456789", ch) != null or ch == 0;
}

fn isFractionOrExponentOrNul(c: c_int) bool {
    const ch: u8 = @truncate(@as(c_uint, @bitCast(c)));
    return std.mem.indexOfScalar(u8, ".eE", ch) != null or ch == 0;
}"""),
]

WORKLOADS = [("large-mixed", "parse"), ("flat-ints", "parse"),
             ("numbers", "parse")]
ITERS = 40
REPS = 3


def bench(binary: pathlib.Path, workload: str, mode: str) -> float:
    """Median nanoseconds per parse, over REPS interleaved repetitions."""
    samples: list[float] = []
    wf = ROOT / "bench" / "workloads" / f"{workload}.json"
    for _ in range(REPS):
        p = subprocess.run([str(binary), str(wf), str(ITERS), mode, "1"],
                           capture_output=True, timeout=900)
        if p.returncode != 0:
            raise SystemExit(f"{binary} failed on {workload}: "
                             f"{p.stderr.decode()[:300]}")
        samples.extend(json.loads(p.stdout.decode())["samples_ns"])
    return statistics.median(samples)


def build_variant(work: pathlib.Path, name: str, relfile: str,
                  old: str, new: str) -> pathlib.Path:
    for item in ("build.zig", "src", "tools", "include", "bench"):
        src, dst = ROOT / item, work / item
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "results", "*.tmp"))
        else:
            shutil.copy2(src, dst)
    target = work / relfile
    text = target.read_text()
    if text.count(old) != 1:
        raise SystemExit(
            f"variant {name}: the pattern it reverts matches {text.count(old)} "
            f"times in {relfile} (expected exactly 1). The optimization has "
            f"changed shape; this script would be measuring something else.")
    target.write_text(text.replace(old, new, 1))
    build = subprocess.run(["zig", "build", "--prefix", str(work / "out")],
                           cwd=work, capture_output=True, timeout=1800)
    if build.returncode != 0:
        raise SystemExit(f"variant {name} did not build:\n"
                         f"{build.stderr.decode()[:800]}")
    return work / "out" / "bin" / "bench_zig"


def main() -> int:
    current = ROOT / "zig-out" / "bin" / "bench_zig"
    c_bin = ROOT / "build" / "bench_c"
    for b in (current, c_bin):
        if not b.exists():
            print(f"missing {b}; run 'make build' first", file=sys.stderr)
            return 2

    baseline = {f"{w}/{m}": bench(c_bin, w, m) for w, m in WORKLOADS}
    now = {f"{w}/{m}": bench(current, w, m) for w, m in WORKLOADS}

    results = []
    with tempfile.TemporaryDirectory(prefix="pdjson-optvariants-") as tmp:
        for name, reverts, relfile, old, new in VARIANTS:
            work = pathlib.Path(tmp) / name
            work.mkdir()
            binary = build_variant(work, name, relfile, old, new)
            before = {f"{w}/{m}": bench(binary, w, m) for w, m in WORKLOADS}
            rows = []
            for key in baseline:
                rows.append({
                    "workload": key,
                    "ratio_before": round(baseline[key] / before[key], 3),
                    "ratio_after": round(baseline[key] / now[key], 3),
                    "improvement": round(before[key] / now[key], 3),
                })
            results.append({"variant": name, "reverts": reverts,
                            "workloads": rows})
            print(f"  {name} -- {reverts}")
            for r in rows:
                print(f"    {r['workload']:<22} {r['ratio_before']:.2f}x -> "
                      f"{r['ratio_after']:.2f}x  "
                      f"({r['improvement']:.2f}x faster than the variant)")

    # Both reverted at once: the state the port was actually in before either
    # optimization, which is the figure the README's narrative refers to.
    with tempfile.TemporaryDirectory(prefix="pdjson-optboth-") as tmp:
        work = pathlib.Path(tmp) / "both"
        work.mkdir()
        for item in ("build.zig", "src", "tools", "include", "bench"):
            src, dst = ROOT / item, work / item
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                    "results", "*.tmp"))
            else:
                shutil.copy2(src, dst)
        target = work / "src" / "parser.zig"
        text = target.read_text()
        for _, _, _, old, new in VARIANTS:
            text = text.replace(old, new, 1)
        target.write_text(text)
        build = subprocess.run(["zig", "build", "--prefix", str(work / "out")],
                               cwd=work, capture_output=True, timeout=1800)
        if build.returncode != 0:
            raise SystemExit("the combined variant did not build")
        binary = work / "out" / "bin" / "bench_zig"
        both = {f"{w}/{m}": bench(binary, w, m) for w, m in WORKLOADS}

    combined = [{
        "workload": key,
        "ratio_before_both": round(baseline[key] / both[key], 3),
        "ratio_now": round(baseline[key] / now[key], 3),
    } for key in baseline]

    print("  both reverted (the state before either optimization):")
    for r in combined:
        print(f"    {r['workload']:<22} {r['ratio_before_both']:.2f}x -> "
              f"{r['ratio_now']:.2f}x")

    report = {
        "schema": "pdjson-zig/optimization-history@1",
        "method": ("Each variant is an exact revert of one optimization, built "
                   "ReleaseSafe in a throwaway copy of the tree and benchmarked "
                   "with the same harness and workloads as everything else. A "
                   "revert pattern that no longer matches exactly once is a hard "
                   "failure, so this cannot silently measure something other "
                   "than the change it names."),
        "ratio_definition": ("C median / Zig median, so below 1.00 means the "
                             "Zig build is slower. 'improvement' is the "
                             "variant's time divided by the current build's."),
        "iterations_per_sample": ITERS,
        "repetitions": REPS,
        "variants": results,
        "both_reverted": combined,
        "limitation": ("Measured on the machine and at the moment this ran; the "
                       "figures in bench/results/ are the authoritative "
                       "benchmark. This exists so the README's account of what "
                       "the optimizations achieved is reproducible rather than "
                       "remembered, not as a second benchmark."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n")
    print(f"  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
