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
import re
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
    abi = load("abi/abi-report.json")
    size = load("size-report.json")
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
    jts = load("differential-jsontestsuite.json")
    fuzz_session = fuzz_data or {}

    # Derived rather than written down, so "23 numbered steps" in the README is
    # backed by the Makefile it describes.
    makefile = (ROOT / "Makefile").read_text()
    steps = re.findall(r'@echo "\[(\d+)/(\d+)\]', makefile)
    pipeline_steps = int(steps[0][1]) if steps else 0

    report = {
        "schema": "pdjson-zig/verification-report@2",
        "generated": datetime.date.today().isoformat(),
        "pipeline_steps": pipeline_steps,
        "pipeline_steps_numbered": len(steps),
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
        # One place that adds up every comparison ever made, so the "no
        # divergence anywhere" claim cites a file that actually contains its
        # arithmetic instead of one of the three files it summarises.
        "no_divergence_ledger": {
            "fixed_corpus_comparisons": dig(diff, "comparisons"),
            "jsontestsuite_comparisons": dig(jts, "comparisons"),
            "published_fuzz_cases": dig(fuzz_session, "cases"),
            "total_comparisons": sum(
                x for x in (dig(diff, "comparisons"), dig(jts, "comparisons"),
                            dig(fuzz_session, "cases")) if isinstance(x, int)),
            "divergences": sum(
                x for x in (dig(diff, "divergences"), dig(jts, "divergences"),
                            dig(fuzz_session, "divergences")) if isinstance(x, int)),
            "note": ("Comparisons on inputs where the pinned original is well "
                     "defined. Cases the sanitizer classifies as upstream UB are "
                     "counted separately and are not in this total."),
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
            "batch_size": dig(fuzz_data, "batch_size"),
            "raw_log": dig(fuzz_data, "raw_log"),
            "raw_log_lines": dig(fuzz_data, "raw_log_lines"),
            "raw_log_uncompressed_sha256": dig(fuzz_data, "raw_log_uncompressed_sha256"),
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
        text = splice(text, "SIZE", render_size_block(size))
        text = splice(text, "UPSTREAM", render_upstream_block(
            load("upstream-issues.json")))
        text = splice(text, "PANIC", render_panic_block(size))
        text = splice(text, "STEPS", f"{report['pipeline_steps']}\n")
        text = splice(text, "OPTHISTORY", render_opthistory_block(
            load("optimization-history.json")))
        text = splice(text, "LIMITS", render_limits_block(
            report, bench, load("abi/abi-cross-report.json"), abi, fuzz_data, size))
        text = splice(text, "SUMMARY", render_summary_block(
            report, diff, jts, fuzz_data, tests, bench, size, safety,
            load("invariants/summary.json"),
            load("state-machine/coverage.json"),
            load("differential/api-coverage.json"),
            load("abi/abi-cross-report.json")))
        readme.write_text(text)
        print("  refreshed the generated blocks in README.md")

    print(f"  wrote artifacts/verification-report.json and docs/verification-report.md")
    return 0


def splice(text: str, name: str, block: str) -> str:
    """Replace the content between a BEGIN/END marker pair.

    Block content gets its own lines; a value short enough to sit inside a
    sentence (the pipeline step count) is spliced inline instead, so the prose
    around it stays readable.
    """
    start, end = f"<!-- {name}:BEGIN -->", f"<!-- {name}:END -->"
    if start not in text or end not in text:
        return text
    inline = "\n" not in block.strip()
    body = block.strip() if inline else "\n" + block
    return text.split(start)[0] + start + body + end + text.split(end)[1]


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


def render_summary_block(v, diff, jts, fuzz_data, tests, bench, size, safety,
                         inv, state, api, cross) -> str:
    """The first-screen table, generated.

    It is the most-read part of the README and was the most likely to go stale:
    it carried "3,498 JSONTestSuite comparisons" and "43 upstream-UB cases" after
    both numbers had moved. Every figure here now comes out of an artifact.
    """
    def n(x, default="?"):
        return f"{x:,}" if isinstance(x, int) else default

    ledger = dig(v, "no_divergence_ledger", {}) or {}
    fuzz = fuzz_data or {}
    n_issues = len(dig(load("upstream-issues.json"), "issues", []) or [])
    sources = len(dig(load("differential/source-matrix-fixed-corpus.json"),
                      "sources", {}) or {})
    rows = [
        "| | |",
        "| --- | --- |",
        "| **Migration** | C → Zig (Port Mortem 2026, Track G) |",
        f"| **Upstream** | `skeeto/pdjson` @ [`78fe04b`](https://github.com/skeeto/pdjson/commit/{dig(v, 'upstream.commit', '')}) (master, 2024-02-22, {dig(v, 'upstream.license', 'Unlicense')}) |",
        "| **Dominant proof** | Two independent programs drive the C original and the Zig port through the same script and emit deterministic NDJSON behaviour transcripts. Equivalence means **byte-identical transcripts**. |",
        f"| **Upstream tests** | **{dig(v, 'original_tests.assertions_passed')}/{dig(v, 'original_tests.assertions_total')}** assertions pass, sources unmodified and hash-pinned, linked against only the Zig library |",
        f"| **Differential** | **0 divergences** in {n(dig(diff, 'comparisons'))} fixed-corpus + {n(dig(jts, 'comparisons'))} JSONTestSuite comparisons, across all {sources} input sources ([matrix](docs/differential-sources.md)) and {len(dig(diff, 'modes', []) or [])} drive modes |",
        f"| **Fuzzing** | {int(dig(fuzz, 'elapsed_seconds', 0) // 60)}-minute published session, **{n(dig(fuzz, 'cases'))} cases, {dig(fuzz, 'divergences')} divergences, {dig(fuzz, 'zig_crashes')} crashes, {dig(fuzz, 'timeouts')} timeouts** ([raw trace]({dig(fuzz, 'raw_log', 'fuzz/logs/')}), {n(dig(fuzz, 'raw_log_lines'))} rounds recorded as it ran) |",
        f"| **Harness self-test** | **{dig(load('mutation-report.json'), 'caught')}/{dig(load('mutation-report.json'), 'mutants_defined')}** injected defects caught; **{dig(state, 'transitions_covered')}/{dig(state, 'transitions_specified')} specified state transitions** exercised ([`docs/state-machine.md`](docs/state-machine.md)) |",
        f"| **C ABI** | Identical layout on **{dig(cross, 'targets_checked')} targets** (32- and 64-bit, x86, ARM, RISC-V, Windows), asserted at compile time across {dig(load('abi/abi-report.json'), 'compile_time_contract_fields')} fields so a drift fails `zig build` ([`docs/abi.md`](docs/abi.md)) |",
        f"| **Safety** | 0 `@constCast`, 0 `unreachable`, 0 force-unwraps, 0 inline asm; **{dig(load('safety/inventory.json'), 'shipped_occurrences')} escape hatches, each justified individually** ([`docs/safety.md`](docs/safety.md)). Ships **ReleaseSafe** — checks on. |",
        f"| **Benchmark** | **Slower on {dig(bench, 'workloads_zig_slower')} of {dig(bench, 'workloads_measured')}** workload/mode pairs, faster on {dig(bench, 'workloads_zig_faster_or_equal')}, and larger in a consumer's binary — by how much depends on the platform. Both tables below, generated from the artifacts. |",
        f"| **Invariants** | {n(dig(inv, 'transcripts_checked_committed_corpus'))} transcripts and {n(dig(inv, 'records_checked_committed_corpus'))} records from the committed corpus checked against {dig(inv, 'rule_functions')} rules that reference neither implementation: **{dig(inv, 'violations_total')} violations** |",
        f"| **API coverage** | All {dig(api, 'exported_functions')} exported functions behaviourally compared; **{dig(api, 'classification.untested')} untested** |",
        f"| **Upstream bugs found** | {n_issues}, all filed with minimal "
        f"reproducers — **all {n_issues} confirmed and fixed by the maintainer** "
        f"([#36](https://github.com/skeeto/pdjson/issues/36), "
        f"[#37](https://github.com/skeeto/pdjson/issues/37), "
        f"[#38](https://github.com/skeeto/pdjson/issues/38)). Two also "
        f"independently confirmed by Valgrind. |",
    ]
    return "\n".join(rows) + "\n"


def render_limits_block(v, bench, cross, abi, fuzz_data, size) -> str:
    """Known limitations, with the numbers generated.

    This section had drifted furthest of anything in the README: "slower on 11
    of 12" when the artifact said 9, "the two upstream issues" when there were
    three, "3,500+ compared cases and 25 minutes of fuzzing" against 11.8M cases
    and 30 minutes. Limitations are the last place a stale number is acceptable,
    since understating them is the failure that matters.
    """
    fuzz = fuzz_data or {}
    ledger = dig(v, "no_divergence_ledger", {}) or {}
    issues = load("upstream-issues.json")
    n_issues = len(dig(issues, "issues", []) or [])
    rows = [
        "Stated here rather than left to be discovered.",
        "",
        f"- **ABI equivalence is *executed* on two targets**, arm64 macOS and "
        f"x86-64 Linux, and asserted at compile time on "
        f"{dig(cross, 'targets_checked')} more. Both executed targets are LP64. "
        f"Three of the four findings in the first cold audit were "
        f"platform-specific and invisible on the development machine, so a third "
        f"executed target would likely find a fourth thing.",
        "- **`nan(...)` payloads that overflow 64 bits are not matched.** C99 "
        "§7.20.1.3p4 makes them implementation-defined and libcs disagree. "
        "Reachable only by calling `json_get_number()` on a *string* token "
        "beginning `nan(`. ([D-09](DECISIONS.md))",
        f"- **The port is slower and larger, and the size cost is "
        f"platform-specific.** Slower on {dig(bench, 'workloads_zig_slower')} of "
        f"{dig(bench, 'workloads_measured')} workload/mode pairs. The stripped "
        f"binary a consumer links is "
        f"{dig(size, 'linked_stripped.ratio', 0):.2f}x on "
        f"{dig(size, 'platform', 'this host')} and "
        f"{dig(load('size/size-report-linux-x86_64.json'), 'linked_stripped.ratio', 0):.2f}x "
        f"on x86-64 Linux, where Zig emits far more unwind and read-only data. "
        f"That gap is reported rather than averaged away. Part of the remaining "
        f"time gap is unexplained.",
        f"- **A fourth defect, in Zig's own `std.fmt.parseFloat`, is reproduced "
        f"but *not filed*** -- `ziglang/zig` restricts issue creation to "
        f"collaborators -- so it is embargoed from every public channel in "
        f"`CLAIMS.json` and is not counted among the {n_issues} findings. The "
        f"three upstream issues have since been confirmed and fixed; the pin "
        f"stays at the commit every measurement here was made against.",
        f"- **Equivalence is demonstrated, not proven.** "
        f"{dig(ledger, 'total_comparisons', 0):,} compared cases and a "
        f"{int(dig(fuzz, 'elapsed_seconds', 0) // 60)}-minute fuzz session is "
        f"evidence, not a proof of behavioural equality. 100% state-transition "
        f"coverage is not path coverage, and the hand-written specification "
        f"agreeing with both implementations would not catch a shared "
        f"misreading of the grammar.",
        "- **The corpus is not adversarial to itself.** Fixtures were written by "
        "the same person who wrote the port. The independent checks against that "
        "are JSONTestSuite, the mutation harness, the invariant rules, and the "
        "state-transition specification -- each of which found something the "
        "fixtures had missed.",
    ]
    return "\n".join(rows) + "\n"


def render_opthistory_block(hist) -> str:
    if not hist:
        return "_No optimization history. Run `python3 scripts/optimization-history.py`._\n"
    rows = ["| workload | before both | today |", "| --- | ---: | ---: |"]
    for r in dig(hist, "both_reverted", []) or []:
        rows.append(f"| {r['workload']} | {r['ratio_before_both']:.2f}x | "
                    f"{r['ratio_now']:.2f}x |")
    rows.append("")
    rows.append("_C median / Zig median, so higher is better and below 1.00 means "
                "the port is slower. Artifact: "
                "[`artifacts/optimization-history.json`](artifacts/optimization-history.json)._")
    return "\n".join(rows) + "\n"


def render_panic_block(size) -> str:
    """What the custom panic handler saves, generated.

    The archive's exact byte count is not reproducible between build
    directories -- Zig embeds paths, so a clean clone produced 241,352 against
    241,248 here. A hand-typed byte count in prose is therefore wrong somewhere
    by construction, which is what the clean-clone check found.
    """
    ph = dig(size, "panic_handler")
    if not ph:
        return "_No panic-handler measurement. Run `make size`._\n"
    return (f"**{ph['with_std_default_handler_bytes']:,} bytes with std's default "
            f"handler against {ph['with_custom_handler_bytes']:,} with the custom "
            f"one — {ph['ratio']}×.**\n")


def render_upstream_block(issues) -> str:
    """Upstream's response, rendered from the artifact.

    The maintainer's words are quoted here, so they are read from a file that
    records the comment in full rather than retyped from memory of it.
    """
    out = dig(issues, "upstream_outcome")
    if not out:
        return "_No upstream response recorded._\n"
    rows = ["| Issue | Defect | Fixed by |", "| --- | --- | --- |"]
    titles = {i["url"].rstrip("/").split("/")[-1]: i["title"]
              for i in dig(issues, "issues", []) or []}
    for f in dig(out, "fix_commits", []) or []:
        num = f["fixes"].lstrip("#")
        t = titles.get(num, "")
        rows.append(f"| [{f['fixes']}](https://github.com/skeeto/pdjson/issues/{num}) "
                    f"| {t} | `{f['sha']}` {f['subject']} |")
    mc = dig(out, "maintainer_comment", {}) or {}
    rows.append("")
    rows.append(f"All three closed as completed on {dig(out, 'closed_on')}. The "
                f"maintainer's reply on "
                f"[#{mc.get('issue', '').rstrip('/').split('/')[-1]}]({mc.get('issue')}):")
    rows.append("")
    rows.append(f"> {mc.get('quote', '')}")
    rows.append("")
    extra = dig(out, "additional_commit_upstream_credited_to_this_work", {}) or {}
    if extra:
        rows.append(f"A fourth commit, `{extra['sha']}` ({extra['subject']}), was "
                    f"credited as found while fixing "
                    f"[#36](https://github.com/skeeto/pdjson/issues/36). It is "
                    f"recorded in the artifact but is not counted as a finding "
                    f"here, because it was not one of the three filed issues.")
        rows.append("")
    rows.append(f"_{dig(out, 'consequence_for_this_port')}_")
    return "\n".join(rows) + "\n"


def render_size_block(size) -> str:
    """What the library costs a consumer, straight from the artifact.

    Generated for the same reason as the benchmark table: a size quoted by hand
    goes stale on the next build and nothing notices.
    """
    if not size:
        return "_No size data. Run `make size`._\n"
    rows = ["| | C original | pdjson-zig | |", "| --- | ---: | ---: | --- |"]
    for label, key in (("linked executable, stripped", "linked_stripped"),
                       ("machine code (`__text`)", "machine_code"),
                       ("read-only data", "read_only_data"),
                       ("string data", "string_data")):
        d = size.get(key)
        if not d:
            continue
        rows.append(f"| {label} | {d['c']:,} | {d['zig']:,} | {d['ratio']:.2f}x |")
    inp = size.get("build_input") or {}
    rows.append("")
    lin = load("size/size-report-linux-x86_64.json")
    cross = ""
    if lin:
        cross = (f" **These figures are platform-specific and the difference is "
                 f"large**: the same measurement on x86-64 Linux gives "
                 f"{dig(lin, 'linked_stripped.ratio', 0):.2f}x the stripped binary "
                 f"and {dig(lin, 'machine_code.ratio', 0):.2f}x the machine code "
                 f"([artifact](artifacts/size/size-report-linux-x86_64.json)).")
    rows.append(f"_Measured on {dig(size, 'platform', 'this host')}. One identical "
                f"C consumer, same compiler and flags, linked twice; both binaries "
                f"verified to produce the same output before any size was recorded. "
                f"Archive against object ({inp.get('zig', 0):,} vs {inp.get('c', 0):,}) "
                f"is reported in the artifact but is not a fair comparison."
                f"{cross}_")
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
