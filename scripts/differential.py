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

# Drive modes, optionally prefixed with an input source.
#
# The three sources are documented as interchangeable, and upstream issue #37 is
# precisely a case where two of them disagree on identical bytes, so comparing
# only one would leave the most interesting class of difference untested.
DEFAULT_MODES = [
    # json_open_buffer -- a byte array
    "next",      # the plain streaming event loop with reset between values
    "nostream",  # strict mode: trailing data is an error
    "peek",      # peek before every next, exercising the buffered-event path
    "skip",      # json_skip over whole values
    "sep",       # the README separator pattern via json_source_get/peek
    "oom:0",     # every allocation fails
    "oom:1",     # the first allocation succeeds, the rest fail
    "oom:2",
    "oom:5",
    # json_open_stream -- a FILE*, so reads go through fgetc/ungetc
    "stream:next", "stream:nostream", "stream:peek", "stream:skip", "stream:sep",
    # json_open_user -- caller-supplied get/peek callbacks
    "user:next", "user:nostream", "user:peek", "user:skip", "user:sep",
    # json_open_string -- length from strlen, so embedded NUL truncates
    "string:next", "string:nostream", "string:peek",
    # json_skip_until, targeting each container-end and each scalar event.
    # Without these the function is exported and ABI-checked but never has its
    # behaviour compared; scripts/api-coverage.py reported it as untested.
    "skipuntil:4",   # JSON_OBJECT_END
    "skipuntil:6",   # JSON_ARRAY_END
    "skipuntil:7",   # JSON_STRING
    "skipuntil:8",   # JSON_NUMBER
    "skipuntil:11",  # JSON_NULL
    # Calling json_next twice past the terminal event, without a reset. Every
    # other mode stops there, so the error latch and DONE's idempotence were
    # never compared; scripts/state-machine.py reported both transitions as
    # unreachable by the harness.
    "after-end",
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
    except OSError as e:
        return "exec_error", b"", str(e).encode()


def sanity_check() -> str | None:
    """Both binaries must actually work before any comparison is meaningful.

    "The implementations disagree" and "one binary is broken" produce the same
    diff. This is not hypothetical: a container touched build/transcript_c, the
    OS then refused to exec it, and a fuzz session reported 101 minimized
    "findings" that were an empty file on one side. Cheap to check, so check.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(b'{"a":[1,2,null,true,"x"]}')
        probe = pathlib.Path(f.name)
    try:
        for mode in ("next", "stream:next", "user:next"):
            for name, b in (("C oracle", C_BIN), ("Zig", ZIG_BIN)):
                rc, out, err = run(b, mode, probe)
                if rc == "exec_error":
                    return f"{name} could not be executed: {err.decode('utf-8', 'replace')[:200]}"
                if rc == "timeout":
                    return f"{name} timed out on a trivial document (mode {mode})"
                if rc != 0 or not out.strip() or b'"schema"' not in out:
                    return (f"{name} did not produce a valid transcript for a "
                            f"trivial document (mode {mode}, exit {rc})")
        return None
    finally:
        probe.unlink(missing_ok=True)


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


# ---------------------------------------------------------------------------
# The source matrix.
#
# "all four documented input sources" is a sentence. This turns it into a table:
# how many comparisons ran through json_open_buffer, json_open_string,
# json_open_stream (a real FILE*) and json_open_user (caller callbacks), and how
# many divergences each produced. A source whose row reads 0 comparisons would
# be a claim with nothing behind it.
# ---------------------------------------------------------------------------

SOURCE_OF_PREFIX = {
    "stream:": ("json_open_stream", "a real FILE* from tmpfile(), read through fgetc/ungetc"),
    "user:": ("json_open_user", "caller-supplied get/peek callbacks over the same bytes"),
    "string:": ("json_open_string", "length derived with strlen, so an embedded NUL truncates"),
}
DEFAULT_SOURCE = ("json_open_buffer", "an explicit pointer and length")


def source_of(mode: str):
    for prefix, info in SOURCE_OF_PREFIX.items():
        if mode.startswith(prefix):
            return info
    return DEFAULT_SOURCE


def source_matrix(modes, inputs, divergences):
    """Per-source and per-mode breakdown, derived from the run, not asserted."""
    by_source: dict = {}
    for mode in modes:
        name, how = source_of(mode)
        row = by_source.setdefault(name, {
            "opener": name,
            "how": how,
            "modes": [],
            "comparisons": 0,
            "divergences": 0,
            "upstream_ub": 0,
            "timeouts": 0,
        })
        row["modes"].append(mode)
        row["comparisons"] += inputs

    per_mode = {m: {"comparisons": inputs, "divergences": 0, "upstream_ub": 0,
                    "timeouts": 0, "source": source_of(m)[0]} for m in modes}

    for d in divergences:
        kind = d["kind"]
        key = {"divergence": "divergences", "upstream_ub": "upstream_ub",
               "timeout": "timeouts"}.get(kind)
        if key is None:
            continue
        m = d["mode"]
        if m in per_mode:
            per_mode[m][key] += 1
            by_source[source_of(m)[0]][key] += 1

    return by_source, per_mode


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

    problem = sanity_check()
    if problem is not None:
        print(f"refusing to compare: {problem}", file=sys.stderr)
        print("a broken binary and a real divergence look identical, so this "
              "will not run", file=sys.stderr)
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

    by_source, per_mode = source_matrix(modes, len(inputs), divergences)
    summary["sources_exercised"] = len(by_source)
    summary["by_source"] = by_source

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n")

    matrix_dir = ROOT / "artifacts" / "differential"
    matrix_dir.mkdir(parents=True, exist_ok=True)
    # Label-scoped, because a fixed path meant the JSONTestSuite run silently
    # overwrote the fixed-corpus matrix and every number a claim quoted from it.
    # Same failure the benchmark smoke run and the hex-float smoke run both had.
    label = args.label.replace("/", "-")
    (matrix_dir / f"source-matrix-{label}.json").write_text(json.dumps({
        "schema": "pdjson-zig/source-matrix@1",
        "label": args.label,
        "method": ("Derived from the run that produced "
                   f"{args.out}, not asserted. Each drive mode is attributed to "
                   "the json_open_* function it uses; a source with 0 "
                   "comparisons would be a claim with nothing behind it."),
        "inputs": len(inputs),
        "sources": by_source,
        "by_mode": per_mode,
    }, indent=2) + "\n")

    # The two sources the original's own tests never exercise get their own
    # files, because they are the ones a reader is most likely to doubt.
    for opener, fname in (("json_open_stream", f"file-source-summary-{label}.json"),
                          ("json_open_user", f"user-source-summary-{label}.json")):
        row = by_source.get(opener)
        if row is None:
            continue
        (matrix_dir / fname).write_text(json.dumps({
            "schema": "pdjson-zig/source-summary@1",
            "label": args.label,
            "opener": opener,
            "how": row["how"],
            "modes": row["modes"],
            "inputs": len(inputs),
            "comparisons": row["comparisons"],
            "divergences": row["divergences"],
            "upstream_ub": row["upstream_ub"],
            "timeouts": row["timeouts"],
            "note": ("Upstream's own tests do not drive this source, so nothing "
                     "in tests/original covers it. These comparisons are the "
                     "only evidence for it."),
        }, indent=2) + "\n")

    total_bad = (summary["divergences"] + summary["zig_crashes"]
                 + summary["c_crashes_unexplained"] + summary["timeouts"])
    print(f"[{args.label}] {len(inputs)} inputs x {len(modes)} modes = "
          f"{len(jobs)} comparisons in {elapsed:.1f}s")
    for name, row in sorted(by_source.items()):
        print(f"    {name:<18} {row['comparisons']:>5} comparisons  "
              f"{row['divergences']} divergence(s)  "
              f"{row['upstream_ub']} upstream-UB  ({len(row['modes'])} modes)")
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
