#!/usr/bin/env python3
"""Collect the generated artifacts into one report, and refresh the numbers
that appear in README.md.

Two outputs:

  artifacts/verification-report.json  -- everything, machine readable
  docs/verification-report.md         -- the same, for humans

It also rewrites the block between <!-- CLAIMS:BEGIN --> and <!-- CLAIMS:END -->
in README.md from CLAIMS.json. That block is the only place in the README where
headline numbers live, and because it is generated, a stale metric there is a
diff rather than something a reader has to catch.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ART = ROOT / "artifacts"


def load(name: str):
    p = ART / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        print(f"warning: {name} is not valid JSON ({e})", file=sys.stderr)
        return None


def dig(obj, path: str, default=None):
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return default
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def newest_fuzz_session():
    """The longest completed session in fuzz/logs/, which is the one quoted."""
    best = None
    for p in sorted((ROOT / "fuzz" / "logs").glob("*.json")):
        try:
            s = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if s.get("schema") != "pdjson-zig/fuzz-session@1":
            continue
        if best is None or s.get("cases", 0) > best[1].get("cases", 0):
            best = (p, s)
    return best


def main() -> int:
    tests = load("original-test-report.json")
    diff = load("differential-summary.json")
    abi = load("abi-report.json")
    safety = load("safety-report.json")
    linkage = load("linkage-report.json")
    bench = load("benchmark-summary.json")
    bench_smoke = load("benchmark-smoke.json")
    mutation = load("mutation-report.json")
    determinism = load("determinism-report.json")
    toolchain = load("toolchain.json")
    manifest = load("upstream-manifest.json")

    fuzz = newest_fuzz_session()
    fuzz_path, fuzz_data = (None, None) if fuzz is None else fuzz

    report = {
        "schema": "pdjson-zig/verification-report@1",
        "generated": datetime.date.today().isoformat(),
        "upstream": {
            "url": dig(manifest, "upstream_url"),
            "commit": dig(manifest, "commit"),
            "license": dig(manifest, "license"),
            "files_pinned": len(dig(manifest, "files", []) or []),
        },
        "toolchain": toolchain,
        "original_tests": {
            "assertions_total": dig(tests, "summary.assertions_total"),
            "assertions_passed": dig(tests, "summary.assertions_passed_against_zig"),
            "assertions_failed": dig(tests, "summary.assertions_failed_against_zig"),
            "skipped": dig(tests, "summary.assertions_skipped"),
            "unsupported": dig(tests, "summary.assertions_unsupported"),
            "tool_differential_mismatches": dig(tests, "summary.tool_differential_mismatches"),
            "sources_modified": dig(tests, "modified"),
        },
        "differential": {
            "inputs": dig(diff, "inputs"),
            "comparisons": dig(diff, "comparisons"),
            "divergences": dig(diff, "divergences"),
            "upstream_ub": dig(diff, "upstream_ub"),
            "zig_crashes": dig(diff, "zig_crashes"),
            "timeouts": dig(diff, "timeouts"),
            "modes": dig(diff, "modes"),
        },
        "fuzz": {
            "log": str(fuzz_path.relative_to(ROOT)) if fuzz_path else None,
            "seed": dig(fuzz_data, "seed"),
            "elapsed_seconds": dig(fuzz_data, "elapsed_seconds"),
            "cases": dig(fuzz_data, "cases"),
            "cases_per_second": dig(fuzz_data, "cases_per_second"),
            "divergences": dig(fuzz_data, "divergences"),
            "zig_crashes": dig(fuzz_data, "zig_crashes"),
            "timeouts": dig(fuzz_data, "timeouts"),
            "modes": dig(fuzz_data, "modes"),
        },
        "abi": {
            "comparison": dig(abi, "comparison"),
            "c_consumer_link": dig(abi, "c_consumer_link"),
            "sizeof_json_stream": dig(abi, "sizeof_json_stream"),
            "exported_symbols": dig(abi, "exported_json_symbols"),
        },
        "linkage": {
            "result": dig(linkage, "result"),
            "objects": dig(linkage, "objects_in_archive"),
            "public_symbols_exported": dig(linkage, "public_symbols_exported"),
        },
        "safety": {
            "result": dig(safety, "result"),
            "counts": dig(safety, "counts"),
            "shipped_mode": dig(safety, "shipped_optimize_mode"),
        },
        "mutation": {
            "defined": dig(mutation, "mutants_defined"),
            "caught": dig(mutation, "caught"),
            "survived": dig(mutation, "survived"),
            "comparable_cases": dig(mutation, "comparable_cases"),
            "excluded_cases": dig(mutation, "excluded_cases"),
        },
        "determinism": {
            "runs_per_mode": dig(determinism, "runs_per_mode"),
            "c_oracle_deterministic": dig(determinism, "c_oracle_deterministic"),
            "zig_deterministic": dig(determinism, "zig_deterministic"),
        },
        "benchmark_smoke": {
            "ran": bench_smoke is not None,
            "workloads": dig(bench_smoke, "workloads_measured"),
        },
        "benchmark": {
            "smoke": dig(bench, "smoke"),
            "workloads_zig_slower": dig(bench, "workloads_zig_slower"),
            "workloads_zig_faster_or_equal": dig(bench, "workloads_zig_faster_or_equal"),
            "repetitions": dig(bench, "repetitions"),
            "cases": [
                {
                    "workload": c.get("workload"),
                    "mode": c.get("mode"),
                    "speedup_safe_vs_c": c.get("speedup_zig-safe_vs_c"),
                    "speedup_fast_vs_c": c.get("speedup_zig-fast_vs_c"),
                }
                for c in (dig(bench, "cases", []) or [])
            ],
        },
    }

    ART.mkdir(parents=True, exist_ok=True)
    (ART / "verification-report.json").write_text(json.dumps(report, indent=2) + "\n")

    # ---- human-readable ---------------------------------------------------
    b = report["benchmark"]["cases"]
    ratios = [c["speedup_safe_vs_c"] for c in b if c.get("speedup_safe_vs_c")]
    md = [
        "# Verification report",
        "",
        f"Generated {report['generated']} by `scripts/report.py` from the",
        "artifacts in `artifacts/`. Every number here is read out of a file that",
        "`make verify` regenerates; nothing is typed in by hand.",
        "",
        "## Provenance",
        "",
        f"- Upstream: {report['upstream']['url']}",
        f"- Commit: `{report['upstream']['commit']}`",
        f"- License: {report['upstream']['license']}",
        f"- Files pinned and hash-verified: {report['upstream']['files_pinned']}",
        "",
        "## Original test suite, unmodified, against the Zig library",
        "",
        f"- Assertions: {report['original_tests']['assertions_passed']}"
        f"/{report['original_tests']['assertions_total']} passed, "
        f"{report['original_tests']['assertions_failed']} failed, "
        f"{report['original_tests']['skipped']} skipped, "
        f"{report['original_tests']['unsupported']} unsupported",
        f"- `stream.c` and `pretty.c` output mismatches vs the C build: "
        f"{report['original_tests']['tool_differential_mismatches']}",
        f"- Upstream sources modified: {report['original_tests']['sources_modified']}",
        "",
        "## Differential (fixed corpus)",
        "",
        f"- {report['differential']['inputs']} inputs x "
        f"{len(report['differential']['modes'] or [])} modes = "
        f"{report['differential']['comparisons']} comparisons",
        f"- Divergences: **{report['differential']['divergences']}**",
        f"- Upstream undefined behaviour (sanitizer-confirmed): "
        f"{report['differential']['upstream_ub']}",
        f"- Zig crashes: {report['differential']['zig_crashes']}, "
        f"timeouts: {report['differential']['timeouts']}",
        "",
        "## Differential fuzzing",
        "",
        f"- Session: `{report['fuzz']['log']}` (seed {report['fuzz']['seed']})",
        f"- Duration: {report['fuzz']['elapsed_seconds']}s, "
        f"{report['fuzz']['cases']} cases "
        f"({report['fuzz']['cases_per_second']}/s)",
        f"- Divergences: **{report['fuzz']['divergences']}**, "
        f"crashes: {report['fuzz']['zig_crashes']}, "
        f"timeouts: {report['fuzz']['timeouts']}",
        "",
        "## Harness self-test (mutation)",
        "",
        f"- Mutants: {report['mutation']['caught']}/{report['mutation']['defined']} caught, "
        f"{report['mutation']['survived']} survived",
        f"- Comparable cases: {report['mutation']['comparable_cases']} "
        f"({report['mutation']['excluded_cases']} excluded as upstream UB)",
        "",
        "## C ABI",
        "",
        f"- Layout tables: {report['abi']['comparison']}",
        f"- C consumer using the pinned header: {report['abi']['c_consumer_link']}",
        f"- `sizeof(struct json_stream)`: {report['abi']['sizeof_json_stream']}",
        f"- Public symbols exported: {report['linkage']['public_symbols_exported']}/22",
        f"- Archive objects: {report['linkage']['objects']} "
        f"(linkage check: {report['linkage']['result']})",
        "",
        "## Safety",
        "",
        f"- Scan result: {report['safety']['result']}",
        f"- Shipped mode: {report['safety']['shipped_mode']}",
        f"- Counts: `{json.dumps(report['safety']['counts'])}`",
        "",
        "## Benchmark",
        "",
    ]
    if ratios:
        md += [
            f"- {len(ratios)} workload/mode pairs, "
            f"{report['benchmark']['repetitions']} repetitions each",
            f"- Zig (ReleaseSafe) vs C -O2, median: "
            f"slowest {min(ratios):.2f}x, fastest {max(ratios):.2f}x "
            f"(ratio > 1 means Zig is faster)",
            "",
            "| workload | mode | ReleaseSafe vs C | ReleaseFast vs C |",
            "| --- | --- | --- | --- |",
        ]
        for c in b:
            md.append(f"| {c['workload']} | {c['mode']} | "
                      f"{c.get('speedup_safe_vs_c')} | {c.get('speedup_fast_vs_c')} |")
    else:
        md.append("- No benchmark data (run `make bench`).")
    md.append("")

    (ROOT / "docs" / "verification-report.md").write_text("\n".join(md) + "\n")

    # ---- refresh the generated README blocks -------------------------------
    claims_path = ROOT / "CLAIMS.json"
    readme = ROOT / "README.md"
    if readme.exists():
        text = readme.read_text()
        if claims_path.exists():
            text = splice(text, "CLAIMS",
                          render_claim_block(json.loads(claims_path.read_text())))
        text = splice(text, "BENCH", render_bench_block(bench))
        readme.write_text(text)
        print("  refreshed the generated blocks in README.md")

    print(f"  wrote artifacts/verification-report.json and docs/verification-report.md")
    return 0


def splice(text: str, name: str, block: str) -> str:
    start, end = f"<!-- {name}:BEGIN -->", f"<!-- {name}:END -->"
    if start not in text or end not in text:
        return text
    return text.split(start)[0] + start + "\n" + block + end + text.split(end)[1]


def render_bench_block(bench) -> str:
    """The benchmark table, straight from the artifact.

    Generated rather than hand-written so a number here cannot drift away from
    the measurement it came from. Ratios are C median / Zig median, so below
    1.00 means the Zig port is slower.
    """
    cases = dig(bench, "cases", []) or []
    if not cases:
        return "_No benchmark data. Run `make bench`._\n"
    rows = [
        "| workload | mode | ReleaseSafe (shipped) | ReleaseFast |",
        "| --- | --- | --- | --- |",
    ]
    for c in cases:
        safe = c.get("speedup_zig-safe_vs_c")
        fast = c.get("speedup_zig-fast_vs_c")
        if safe is None and fast is None:
            continue
        rows.append(f"| {c['workload']} | {c['mode']} | "
                    f"{safe:.2f}x | {fast:.2f}x |")
    ratios = [c["speedup_zig-safe_vs_c"] for c in cases
              if c.get("speedup_zig-safe_vs_c")]
    slower = [r for r in ratios if r < 1.0]
    rows.append("")
    rows.append(f"_{len(slower)} of {len(ratios)} workload/mode pairs are slower "
                f"in Zig. Median ratios, {dig(bench, 'repetitions')} repetitions, "
                f"raw samples in `bench/results/raw.json`._")
    return "\n".join(rows) + "\n"


def render_claim_block(claims: dict) -> str:
    rows = ["| # | Claim | Status | Evidence |", "| --- | --- | --- | --- |"]
    for c in claims.get("claims", []):
        if "readme" not in c.get("allowed_in", []):
            continue
        rows.append(f"| {c['id']} | {c['text']} | {c['status']} | "
                    f"[`{c['artifact']}`]({c['artifact']}) |")
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    sys.exit(main())
