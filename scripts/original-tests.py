#!/usr/bin/env python3
"""Run the upstream test suite against the Zig library and report per test.

Three programs live in upstream/pdjson/tests/. They are used unmodified, from
their pinned location -- no copy is made and no line is edited, which
scripts/verify-upstream-hashes.sh enforces independently.

  tests.c    18 assertions. Compiled against the Zig static library and run;
             its own PASS/FAIL lines become the per-test report.
  stream.c   Prints the event stream for stdin. Not an assertion suite, so it
             is used differentially: built twice, once against the pinned C
             and once against Zig, and its output compared over every fixture.
  pretty.c   Same treatment. It exercises json_peek/json_get_depth heavily,
             which the assertion suite barely touches.

Only the link line differs between the two builds. The Zig build links
zig-out/lib/libpdjson.a and no pdjson.o.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
UP = ROOT / "upstream" / "pdjson"
BUILD = ROOT / "build"
ZIG_LIB = ROOT / "zig-out" / "lib" / "libpdjson.a"
FIXTURES = sorted((ROOT / "tests" / "conformance" / "fixtures").glob("*.json"))

CFLAGS = ["-std=c99", "-pedantic", "-Wall", "-Wextra",
          "-Wno-missing-field-initializers", "-O2"]

PROGRAMS = ["tests", "stream", "pretty"]


def compile_against(program: str, backend: str) -> tuple[pathlib.Path, list[str]]:
    """Build upstream/pdjson/tests/<program>.c against 'c' or 'zig'."""
    out = BUILD / f"{program}_{backend}"
    src = [str(UP / "tests" / f"{program}.c")]
    if backend == "c":
        src.append(str(UP / "pdjson.c"))
    else:
        src.append(str(ZIG_LIB))
    cmd = ["cc", *CFLAGS, "-o", str(out), *src]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        print(p.stderr.decode(), file=sys.stderr)
        raise SystemExit(f"failed to build {program} against {backend}")
    return out, cmd


def main() -> int:
    BUILD.mkdir(exist_ok=True)
    if not ZIG_LIB.exists():
        print(f"missing {ZIG_LIB}; run 'zig build' first", file=sys.stderr)
        return 2

    report: dict = {
        "schema": "pdjson-zig/original-test-report@1",
        "upstream_commit": "78fe04b820dc8817f540bdd87fb22887e0ef3981",
        "test_sources": [
            "upstream/pdjson/tests/tests.c",
            "upstream/pdjson/tests/stream.c",
            "upstream/pdjson/tests/pretty.c",
        ],
        "modified": False,
        "note": ("Upstream test sources are compiled in place from the pinned "
                 "tree. Byte-identity is enforced by "
                 "scripts/verify-upstream-hashes.sh."),
        "suites": {},
    }

    binaries = {}
    for prog in PROGRAMS:
        for backend in ("c", "zig"):
            binaries[(prog, backend)], cmd = compile_against(prog, backend)
            if prog == "tests":
                report.setdefault("build_commands", {})[backend] = " ".join(cmd)

    # ---- tests.c: parse its own per-test output -----------------------------
    cases = []
    for backend in ("c", "zig"):
        started = time.time()
        p = subprocess.run([str(binaries[("tests", backend)])], capture_output=True)
        elapsed = time.time() - started
        text = p.stdout.decode("utf-8", "replace")
        # Strip the ANSI colour the suite emits.
        plain = text.replace("\033[31;1m", "").replace("\033[32;1m", "")
        plain = plain.replace("\033[1m", "").replace("\033[0m", "")
        results = {}
        for line in plain.splitlines():
            if line.startswith("PASS "):
                results[line[5:].strip()] = "passed"
            elif line.startswith("FAIL "):
                name = line[5:].split(":")[0].strip()
                results[name] = "failed"
        report["suites"][f"tests.c[{backend}]"] = {
            "binary": str(binaries[("tests", backend)].relative_to(ROOT)),
            "returncode": p.returncode,
            "passed": sum(1 for v in results.values() if v == "passed"),
            "failed": sum(1 for v in results.values() if v == "failed"),
            "duration_seconds": round(elapsed, 4),
            "raw_tail": plain.strip().splitlines()[-1] if plain.strip() else "",
        }
        cases.append((backend, results))

    c_results = dict(cases[0][1])
    z_results = dict(cases[1][1])
    per_test = []
    for name in c_results:
        per_test.append({
            "test": name,
            "suite": "upstream/pdjson/tests/tests.c",
            "c_status": c_results.get(name, "missing"),
            "zig_status": z_results.get(name, "missing"),
            "status": ("passed" if z_results.get(name) == "passed" else "failed"),
            "parity": c_results.get(name) == z_results.get(name),
        })
    report["tests"] = sorted(per_test, key=lambda t: t["test"])

    # ---- stream.c / pretty.c: differential over the fixture corpus ----------
    for prog in ("stream", "pretty"):
        mismatches = []
        started = time.time()
        for f in FIXTURES:
            data = f.read_bytes()
            outs = {}
            for backend in ("c", "zig"):
                p = subprocess.run([str(binaries[(prog, backend)])],
                                   input=data, capture_output=True, timeout=30)
                outs[backend] = (p.returncode, p.stdout, p.stderr)
            if outs["c"] != outs["zig"]:
                mismatches.append({
                    "fixture": f.name,
                    "c_returncode": outs["c"][0],
                    "zig_returncode": outs["zig"][0],
                })
        report["suites"][f"{prog}.c[differential]"] = {
            "kind": "differential",
            "description": (f"upstream/pdjson/tests/{prog}.c built against C and "
                            "against Zig, output compared byte for byte"),
            "fixtures": len(FIXTURES),
            "mismatches": len(mismatches),
            "detail": mismatches,
            "duration_seconds": round(time.time() - started, 3),
        }

    total_pass = sum(1 for t in report["tests"] if t["status"] == "passed")
    total_fail = len(report["tests"]) - total_pass
    diffmis = sum(report["suites"][f"{p}.c[differential]"]["mismatches"]
                  for p in ("stream", "pretty"))
    report["summary"] = {
        "assertions_total": len(report["tests"]),
        "assertions_passed_against_zig": total_pass,
        "assertions_failed_against_zig": total_fail,
        "assertions_skipped": 0,
        "assertions_unsupported": 0,
        "tool_differential_fixtures": len(FIXTURES) * 2,
        "tool_differential_mismatches": diffmis,
    }

    out = ROOT / "artifacts" / "original-test-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"tests.c against Zig: {total_pass}/{len(report['tests'])} passed, "
          f"{total_fail} failed")
    for p in ("stream", "pretty"):
        s = report["suites"][f"{p}.c[differential]"]
        print(f"{p}.c differential over {s['fixtures']} fixtures: "
              f"{s['mismatches']} mismatches")
    print(f"wrote {out.relative_to(ROOT)}")

    return 1 if (total_fail or diffmis) else 0


if __name__ == "__main__":
    sys.exit(main())
