#!/usr/bin/env python3
"""Run the C-vs-Zig benchmark and summarise the distribution.

Fairness rules, all of them enforced here rather than asserted in prose:

  * Identical workload files, identical parse loop, identical counting
    allocator, identical warm-up (1 cold + 4 warm iterations, unrecorded).
  * The two binaries are interleaved within each repetition, so thermal drift
    and background load affect both roughly equally instead of penalising
    whichever ran second.
  * Every per-iteration sample is kept in bench/results/raw.json. The summary
    is derived from that file and can be recomputed without re-running.
  * Both Zig optimisation modes are reported. ReleaseSafe is what the project
    ships and keeps bounds and overflow checks on; ReleaseFast is the closest
    like-for-like against C's -O2. Quoting only the faster one would be
    dishonest, so the summary carries both.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import platform
import statistics
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKLOADS = ROOT / "bench" / "workloads"
RESULTS = ROOT / "bench" / "results"

# (workload, mode, iterations, inner).
#
# `inner` is how many parses each recorded sample covers. CLOCK_MONOTONIC has
# 1us granularity on macOS, so a workload that finishes in well under that
# would record nothing but 0 and 1000. Batching keeps every sample far above
# the clock's resolution; the summariser divides `inner` back out, so the
# reported figures are always per parse.
PLAN = [
    ("large-mixed", "parse", 60, 1),
    ("large-mixed", "strings", 60, 1),
    ("numbers", "parse", 40, 1),
    ("numbers", "strings", 40, 1),
    ("strings-ascii", "strings", 60, 1),
    ("strings-unicode", "strings", 60, 1),
    ("deep-nesting", "parse", 60, 1),
    ("many-small-docs", "parse", 30, 1),
    ("malformed-early", "parse", 60, 5000),
    ("malformed-late", "parse", 60, 1),
    ("whitespace-heavy", "parse", 60, 1),
    ("flat-ints", "parse", 40, 1),
]


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def run(binary: pathlib.Path, workload: pathlib.Path, iters: int, mode: str,
        inner: int) -> dict:
    p = subprocess.run([str(binary), str(workload), str(iters), mode, str(inner)],
                       capture_output=True, timeout=1800)
    if p.returncode != 0:
        raise SystemExit(f"{binary} failed on {workload}: {p.stderr.decode()[:400]}")
    return json.loads(p.stdout.decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repetitions", type=int, default=5)
    ap.add_argument("--smoke", action="store_true",
                    help="one workload, few iterations, for the verify pipeline")
    args = ap.parse_args()

    bins = {
        "c": ROOT / "build" / "bench_c",
        "zig-safe": ROOT / "zig-out" / "bin" / "bench_zig",
        "zig-fast": ROOT / "build" / "zig-fast" / "bin" / "bench_zig",
    }
    for name, b in list(bins.items()):
        if not b.exists():
            if name == "zig-fast":
                print(f"note: {b} missing, skipping the ReleaseFast column",
                      file=sys.stderr)
                del bins[name]
            else:
                print(f"missing {b}; run 'make build' first", file=sys.stderr)
                return 2

    plan = PLAN[:1] if args.smoke else PLAN
    reps = 1 if args.smoke else args.repetitions
    if args.smoke:
        plan = [(plan[0][0], plan[0][1], 3, plan[0][3])]

    raw: list[dict] = []
    started = time.time()

    for workload, mode, iters, inner in plan:
        wf = WORKLOADS / f"{workload}.json"
        if not wf.exists():
            print(f"missing workload {wf}; run bench/workloads/gen.py", file=sys.stderr)
            return 2
        for rep in range(reps):
            # Interleaved within the repetition.
            for impl, b in bins.items():
                r = run(b, wf, iters, mode, inner)
                r["impl"] = impl
                r["repetition"] = rep
                r["workload_name"] = workload
                raw.append(r)
        print(f"  {workload} [{mode}] done", file=sys.stderr)

    # A smoke run measures one workload and is only there to prove the
    # benchmark harness still executes. It must never overwrite the
    # authoritative full-run artifacts, or `make verify` would quietly replace
    # published figures with a single-workload sample.
    RESULTS.mkdir(parents=True, exist_ok=True)
    raw_name = "raw-smoke.json" if args.smoke else "raw.json"
    (RESULTS / raw_name).write_text(json.dumps(raw, indent=1) + "\n")

    # ---- summarise -------------------------------------------------------
    cases = []
    for workload, mode, _, _inner in plan:
        entry: dict = {"workload": workload, "mode": mode}
        by_impl = {}
        for impl in bins:
            # Divide out the inner batch so every figure below is per parse.
            samples = [s / r.get("inner", 1) for r in raw
                       if r["workload_name"] == workload and r["mode"] == mode
                       and r["impl"] == impl
                       for s in r["samples_ns"]]
            if not samples:
                continue
            rs = [r for r in raw if r["workload_name"] == workload
                  and r["mode"] == mode and r["impl"] == impl]
            size = rs[0]["bytes"]
            med = statistics.median(samples)
            by_impl[impl] = {
                "samples": len(samples),
                "median_ns": round(med, 1),
                "mean_ns": round(statistics.fmean(samples), 1),
                "stdev_ns": round(statistics.pstdev(samples), 1) if len(samples) > 1 else 0,
                "p95_ns": round(pct(samples, 0.95), 1),
                "p99_ns": round(pct(samples, 0.99), 1),
                "min_ns": min(samples),
                "throughput_mb_s": round(size / med * 1000 if med else 0, 1),
                "cold_ns_median": round(statistics.median([r["cold_ns"] for r in rs]), 1),
                "alloc_count": rs[0]["alloc_count"],
                "alloc_bytes": rs[0]["alloc_bytes"],
                "peak_rss_kb": max(r["peak_rss_kb"] for r in rs),
            }
            entry["bytes"] = size
            entry["inner_batch"] = rs[0].get("inner", 1)
        entry["impls"] = by_impl
        if "c" in by_impl:
            for impl in by_impl:
                if impl == "c" or by_impl[impl]["median_ns"] == 0:
                    continue
                # >1 means Zig is faster.
                entry[f"speedup_{impl}_vs_c"] = round(
                    by_impl["c"]["median_ns"] / by_impl[impl]["median_ns"], 3)
        cases.append(entry)

    # Counted here rather than in prose, so CLAIMS.json can check the number
    # instead of a human remembering to update it.
    safe_ratios = [c["speedup_zig-safe_vs_c"] for c in cases
                   if c.get("speedup_zig-safe_vs_c")]
    slower = sum(1 for r in safe_ratios if r < 1.0)
    faster = sum(1 for r in safe_ratios if r >= 1.0)

    cc = subprocess.run(["cc", "--version"], capture_output=True).stdout.decode().splitlines()
    summary = {
        "schema": "pdjson-zig/benchmark-summary@1",
        "generated_by": "scripts/bench.py",
        "raw_data": f"bench/results/{raw_name}",
        "methodology": "bench/methodology.md",
        "repetitions": reps,
        "smoke": args.smoke,
        "elapsed_seconds": round(time.time() - started, 1),
        "workloads_measured": len(safe_ratios),
        "workloads_zig_slower": slower,
        "workloads_zig_faster_or_equal": faster,
        "ratio_definition": "C median / Zig median; below 1.00 means Zig is slower",
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True).stdout.decode().strip() or platform.processor(),
            "cpu_count": subprocess.run(["sysctl", "-n", "hw.ncpu"],
                                        capture_output=True).stdout.decode().strip(),
            "c_compiler": cc[0] if cc else "unknown",
            "c_flags": "-O2 -std=c99",
            "zig_version": subprocess.run(["zig", "version"],
                                          capture_output=True).stdout.decode().strip(),
            "zig_safe_mode": "ReleaseSafe (shipped: bounds and overflow checks enabled)",
            "zig_fast_mode": "ReleaseFast (checks disabled; like-for-like against C -O2)",
        },
        "confounders": [
            "Run on a laptop, not an isolated machine: other processes and "
            "thermal behaviour affect absolute numbers.",
            "The two binaries are interleaved within each repetition to spread "
            "drift across both rather than concentrating it in one.",
            "Both harnesses install the same counting allocator, which adds a "
            "small constant cost to both.",
            "malloc/realloc come from the system allocator in both cases, so "
            "allocator behaviour is shared and not part of the comparison.",
            "Timer is CLOCK_MONOTONIC in C and std.time.nanoTimestamp in Zig; "
            "both resolve well below the smallest measurement here.",
        ],
        "cases": cases,
    }

    out = ROOT / "artifacts" / (
        "benchmark-smoke.json" if args.smoke else "benchmark-summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"\n{'workload':<22} {'mode':<8} {'C med':>10} {'Zig safe':>10} "
          f"{'Zig fast':>10} {'safe/C':>8} {'fast/C':>8}")
    for c in cases:
        i = c["impls"]
        if "c" not in i:
            continue
        print(f"{c['workload']:<22} {c['mode']:<8} "
              f"{i['c']['median_ns']/1e6:>9.3f}m "
              f"{i.get('zig-safe',{}).get('median_ns',0)/1e6:>9.3f}m "
              f"{i.get('zig-fast',{}).get('median_ns',0)/1e6:>9.3f}m "
              f"{c.get('speedup_zig-safe_vs_c',0):>8.2f} "
              f"{c.get('speedup_zig-fast_vs_c',0):>8.2f}")
    print(f"\n(values are median milliseconds per iteration; "
          f"ratio > 1 means Zig is faster)")
    print(f"wrote {out.relative_to(ROOT)} and bench/results/raw.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
