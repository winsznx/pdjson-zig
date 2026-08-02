#!/usr/bin/env python3
"""Differential harness: run every input through both transcribers and compare.

Each (input, mode) pair is executed twice -- once against the C oracle built
from the pinned upstream pdjson.c, once against the Zig library -- and the two
NDJSON transcripts are compared byte for byte. Any difference is a divergence
and is saved in full.

Nothing here filters, normalises, or retries. A divergence that cannot be
explained stays in the report and fails the exit status.

Usage:
  differential.py --corpus tests/conformance/fixtures [--corpus DIR ...]
                  [--modes next,peek,...] [--out artifacts/differential-summary.json]
                  [--label NAME] [--jobs N] [--quiet]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
C_BIN = ROOT / "build" / "transcript_c"
C_SAN_BIN = ROOT / "build" / "transcript_c_asan"
ZIG_BIN = ROOT / "zig-out" / "bin" / "transcript_zig"

DEFAULT_MODES = [
    "next",      # the plain streaming event loop with reset between values
    "nostream",  # strict mode: trailing data is an error
    "peek",      # peek before every next, exercising the buffered-event path
    "skip",      # json_skip over whole values
    "sep",       # the README separator pattern via json_source_get/peek
    "oom:0",     # every allocation fails
    "oom:1",     # the first allocation succeeds, the rest fail
    "oom:2",
    "oom:5",
]

TIMEOUT = 20


def run(binary: pathlib.Path, mode: str, path: pathlib.Path):
    try:
        p = subprocess.run(
            [str(binary), mode, str(path)],
            capture_output=True,
            timeout=TIMEOUT,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return "timeout", b"", b""


def upstream_sanitizer_report(mode: str, path: pathlib.Path) -> str | None:
    """Re-run the case against an ASan+UBSan build of the ORIGINAL pdjson.

    This is how a finding gets classified without anyone's say-so: if the
    sanitized upstream build reports an error on the same input, the original
    invoked undefined behaviour there, so there is no defined behaviour for the
    port to match. If it stays clean, the difference is ours to fix.
    """
    if not C_SAN_BIN.exists():
        return None
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=0:abort_on_error=0:exitcode=86"
    env["UBSAN_OPTIONS"] = "print_stacktrace=1:halt_on_error=0"
    try:
        p = subprocess.run([str(C_SAN_BIN), mode, str(path)],
                           capture_output=True, timeout=TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return None
    err = p.stderr.decode("utf-8", "replace")
    if "AddressSanitizer" in err or "runtime error:" in err or p.returncode == 86:
        return err[:4000]
    return None


def compare(path: pathlib.Path, mode: str):
    c_rc, c_out, c_err = run(C_BIN, mode, path)
    z_rc, z_out, z_err = run(ZIG_BIN, mode, path)

    base = {
        "input": str(path.relative_to(ROOT)),
        "mode": mode,
        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "input_bytes": path.stat().st_size,
    }

    if c_rc == "timeout" or z_rc == "timeout":
        return {**base, "kind": "timeout",
                "c_timeout": c_rc == "timeout", "zig_timeout": z_rc == "timeout"}

    # A negative return code means killed by a signal on POSIX.
    if z_rc < 0:
        return {**base, "kind": "zig_crash", "zig_returncode": z_rc,
                "zig_stderr": z_err.decode("utf-8", "replace")[:2000]}

    if c_rc < 0:
        report = upstream_sanitizer_report(mode, path)
        return {**base,
                "kind": "upstream_ub" if report else "c_crash",
                "c_returncode": c_rc,
                "zig_returncode": z_rc,
                "detail": "the pinned C original crashed; the Zig port did not",
                "sanitizer_report": report}

    if c_out == z_out and c_rc == z_rc:
        return None

    c_lines = c_out.decode("utf-8", "replace").splitlines()
    z_lines = z_out.decode("utf-8", "replace").splitlines()
    first = None
    for i in range(max(len(c_lines), len(z_lines))):
        a = c_lines[i] if i < len(c_lines) else "<missing>"
        b = z_lines[i] if i < len(z_lines) else "<missing>"
        if a != b:
            first = {"line": i, "c": a, "zig": b}
            break

    report = upstream_sanitizer_report(mode, path)
    return {
        **base,
        "kind": "upstream_ub" if report else "divergence",
        "c_returncode": c_rc,
        "zig_returncode": z_rc,
        "first_difference": first,
        "c_transcript": c_out.decode("utf-8", "replace"),
        "zig_transcript": z_out.decode("utf-8", "replace"),
        "sanitizer_report": report,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", action="append", default=[])
    ap.add_argument("--modes", default=",".join(DEFAULT_MODES))
    ap.add_argument("--out", default="artifacts/differential-summary.json")
    ap.add_argument("--label", default="fixed-corpus")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    for b in (C_BIN, ZIG_BIN):
        if not b.exists():
            print(f"missing {b}; run 'make build' first", file=sys.stderr)
            return 2

    corpora = args.corpus or ["tests/conformance/fixtures"]
    inputs: list[pathlib.Path] = []
    for c in corpora:
        d = ROOT / c
        if d.is_file():
            inputs.append(d)
        else:
            inputs.extend(sorted(p for p in d.rglob("*") if p.is_file()))
    modes = args.modes.split(",")

    jobs = [(p, m) for p in inputs for m in modes]
    started = time.time()
    divergences = []
    done = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = {ex.submit(compare, p, m): (p, m) for p, m in jobs}
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            r = fut.result()
            if r is not None:
                divergences.append(r)
            if not args.quiet and done % 500 == 0:
                print(f"  {done}/{len(jobs)} comparisons, {len(divergences)} divergences",
                      file=sys.stderr)

    elapsed = time.time() - started
    divergences.sort(key=lambda d: (d["input"], d["mode"]))

    # Kept apart from fuzz/logs/, which holds fuzz *sessions*. These are
    # per-case findings from the fixed corpus, and mixing the two made it
    # ambiguous which artifact a claim was quoting.
    log_dir = ROOT / "artifacts" / "differential-cases"
    log_dir.mkdir(parents=True, exist_ok=True)

    for old in log_dir.glob("*.json"):
        old.unlink()

    for d in divergences:
        stem = (d["input"].replace("/", "_") + "." + d["mode"].replace(":", "-"))
        (log_dir / f"{stem}.json").write_text(json.dumps(d, indent=2))

    def count(kind):
        return len([d for d in divergences if d["kind"] == kind])

    summary = {
        "schema": "pdjson-zig/differential-summary@2",
        "label": args.label,
        "corpora": corpora,
        "modes": modes,
        "inputs": len(inputs),
        "comparisons": len(jobs),
        # A real behavioural difference on input where the original is
        # well-defined. This is the number that must stay at zero.
        "divergences": count("divergence"),
        # Cases where an ASan/UBSan build of the pinned original reports an
        # error, so there is no defined behaviour to match. Counted and kept,
        # never silently dropped.
        "upstream_ub": count("upstream_ub"),
        "zig_crashes": count("zig_crash"),
        "c_crashes_unexplained": count("c_crash"),
        "timeouts": count("timeout"),
        "elapsed_seconds": round(elapsed, 2),
        "c_oracle": str(C_BIN.relative_to(ROOT)),
        "zig_binary": str(ZIG_BIN.relative_to(ROOT)),
        "findings": [
            {k: v for k, v in d.items() if k not in ("c_transcript", "zig_transcript")}
            for d in divergences
        ],
    }

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n")

    total_bad = (summary["divergences"] + summary["zig_crashes"]
                 + summary["c_crashes_unexplained"] + summary["timeouts"])
    print(f"[{args.label}] {len(inputs)} inputs x {len(modes)} modes = "
          f"{len(jobs)} comparisons in {elapsed:.1f}s")
    print(f"  divergences={summary['divergences']} "
          f"upstream_ub={summary['upstream_ub']} "
          f"zig_crashes={summary['zig_crashes']} "
          f"c_crashes_unexplained={summary['c_crashes_unexplained']} "
          f"timeouts={summary['timeouts']}")
    print(f"  wrote {out.relative_to(ROOT)}")
    if total_bad:
        for d in [x for x in divergences if x["kind"] != "upstream_ub"][:10]:
            print(f"  !! {d['kind']}: {d['input']} mode={d['mode']}")
            if d.get("first_difference"):
                print(f"       C  : {d['first_difference']['c'][:200]}")
                print(f"       ZIG: {d['first_difference']['zig'][:200]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
