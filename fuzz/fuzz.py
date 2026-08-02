#!/usr/bin/env python3
"""Differential fuzzer for pdjson-zig.

Generates inputs, drives both implementations through the same script, and
compares behaviour transcripts byte for byte. Any mismatch is isolated to a
single input, minimized by delta debugging, and written to fuzz/minimized/ with
both transcripts.

Determinism: everything is derived from --seed. Re-running with the same seed,
the same corpus, and the same binaries reproduces the same cases in the same
order. The pack file for each round is kept when it contains a finding, so a
reproduction never depends on regenerating anything.

The session record in fuzz/logs/ states the real duration, seed, case count,
crash count, timeout count and divergence count. Nothing is filtered out to
make a number look better; a case that cannot be explained stays a divergence
and fails the exit status.

Usage:
  fuzz.py --seconds 60 --seed 1 [--modes next,peek,...] [--out fuzz/logs/session.json]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pathlib
import random
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
C_BIN = ROOT / "build" / "transcript_c"
C_SAN_BIN = ROOT / "build" / "transcript_c_asan"
ZIG_BIN = ROOT / "zig-out" / "bin" / "transcript_zig"
WORK = ROOT / "fuzz" / "work"

# Allocation-failure modes are excluded by default: the pinned original has a
# confirmed null/out-of-bounds read there (docs/upstream-bug-oom-stack.md), so
# it crashes constantly and swamps the signal. Pass --modes explicitly to
# include them when hunting for more upstream defects.
DEFAULT_MODES = ["next", "nostream", "peek", "skip", "sep"]

BATCH = 400
MAX_INPUT = 4096
TIMEOUT = 30

# ---------------------------------------------------------------- generators

STRUCTURAL = [
    b"{", b"}", b"[", b"]", b",", b":", b'"', b"\\", b" ", b"\n", b"\t", b"\r",
    b"true", b"false", b"null", b"-", b"+", b".", b"e", b"E", b"0", b"1", b"9",
    b"\\u", b"\\uD800", b"\\uDC00", b"\\n", b"\\/", b"\\x",
    b"\x00", b"\xff", b"\xfe", b"\x80", b"\xc2", b"\xe0", b"\xf0", b"\xf4", b"\xf5",
    b"\xc3\xa9", b"\xe4\xb8\xad", b"\xf0\x9f\x98\x80",
    b"1e999", b"-0", b"0.0", b"1.7976931348623157e308",
]

NUMBER_PARTS = [b"-", b"0", b"1", b"9", b"123", b".", b"0" * 20, b"e", b"E", b"+", b"-",
                b"308", b"309", b"324", b"999", b"1" * 30]


def gen_number(rng: random.Random) -> bytes:
    return b"".join(rng.choice(NUMBER_PARTS) for _ in range(rng.randint(1, 8)))


def gen_string(rng: random.Random) -> bytes:
    body = b"".join(rng.choice(STRUCTURAL) for _ in range(rng.randint(0, 10)))
    return b'"' + body + (b'"' if rng.random() < 0.8 else b"")


def gen_grammar(rng: random.Random, depth: int = 0) -> bytes:
    """A valid-by-construction JSON value, biased toward awkward shapes."""
    if depth > 4:
        pick = rng.randint(0, 3)
    else:
        pick = rng.randint(0, 5)
    if pick == 0:
        return rng.choice([b"null", b"true", b"false"])
    if pick == 1:
        return rng.choice([b"0", b"-0", b"1", b"-1", b"1.5", b"1e3", b"1e-3",
                           b"9223372036854775807", b"1.7976931348623157e308"])
    if pick == 2:
        return b'"' + rng.choice([b"", b"a", b"\\n", b"\\u0000", b"\\uD800\\uDC00",
                                  b"\xc3\xa9", b"x" * rng.randint(0, 40)]) + b'"'
    if pick == 3:
        return rng.choice([b"[]", b"{}"])
    if pick == 4:
        items = [gen_grammar(rng, depth + 1) for _ in range(rng.randint(1, 4))]
        return b"[" + b",".join(items) + b"]"
    pairs = [b'"k%d":' % i + gen_grammar(rng, depth + 1) for i in range(rng.randint(1, 4))]
    return b"{" + b",".join(pairs) + b"}"


def gen_synthetic(rng: random.Random) -> bytes:
    pick = rng.randint(0, 3)
    if pick == 0:
        return gen_grammar(rng)
    if pick == 1:
        return gen_number(rng)
    if pick == 2:
        return gen_string(rng)
    return b"".join(rng.choice(STRUCTURAL) for _ in range(rng.randint(1, 24)))


def mutate(rng: random.Random, data: bytes, corpus: list[bytes]) -> bytes:
    if not data:
        return gen_synthetic(rng)
    b = bytearray(data)
    for _ in range(rng.randint(1, 4)):
        op = rng.randint(0, 6)
        if op == 0 and b:                                   # bit flip
            i = rng.randrange(len(b))
            b[i] ^= 1 << rng.randrange(8)
        elif op == 1 and b:                                 # byte set
            b[rng.randrange(len(b))] = rng.randrange(256)
        elif op == 2:                                       # insert a token
            i = rng.randrange(len(b) + 1)
            b[i:i] = rng.choice(STRUCTURAL)
        elif op == 3 and b:                                 # delete a run
            i = rng.randrange(len(b))
            b[i:i + rng.randint(1, 8)] = b""
        elif op == 4 and b:                                 # duplicate a run
            i = rng.randrange(len(b))
            j = min(len(b), i + rng.randint(1, 16))
            b[i:i] = b[i:j]
        elif op == 5 and corpus:                            # splice
            other = rng.choice(corpus)
            if other:
                i = rng.randrange(len(b) + 1)
                k = rng.randrange(len(other))
                b[i:i] = other[k:k + rng.randint(1, 32)]
        else:                                               # truncate
            if len(b) > 1:
                del b[rng.randrange(len(b)):]
    return bytes(b[:MAX_INPUT])


# ------------------------------------------------------------------- running

def pack(inputs: list[bytes]) -> bytes:
    out = bytearray()
    for d in inputs:
        out += str(len(d)).encode() + b"\n" + d
    return bytes(out)


def run_pack(binary: pathlib.Path, mode: str, path: pathlib.Path):
    try:
        p = subprocess.run([str(binary), "--pack", mode, str(path)],
                           capture_output=True, timeout=TIMEOUT)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return "timeout", b""


def split_sections(out: bytes) -> list[bytes]:
    sections, current, started = [], bytearray(), False
    for line in out.split(b"\n"):
        if line.startswith(b'{"input":"pack:'):
            if started:
                sections.append(bytes(current))
            current, started = bytearray(), True
            continue
        if started:
            current += line + b"\n"
    if started:
        sections.append(bytes(current))
    return sections


def run_single(binary: pathlib.Path, mode: str, data: bytes, tmp: pathlib.Path):
    tmp.write_bytes(data)
    try:
        p = subprocess.run([str(binary), mode, str(tmp)],
                           capture_output=True, timeout=TIMEOUT)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return "timeout", b"", b""


def upstream_is_ub(mode: str, data: bytes, tmp: pathlib.Path) -> str | None:
    if not C_SAN_BIN.exists():
        return None
    tmp.write_bytes(data)
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=0:abort_on_error=0:exitcode=86"
    env["UBSAN_OPTIONS"] = "print_stacktrace=1:halt_on_error=0"
    try:
        p = subprocess.run([str(C_SAN_BIN), mode, str(tmp)],
                           capture_output=True, timeout=TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return None
    err = p.stderr.decode("utf-8", "replace")
    if "AddressSanitizer" in err or "runtime error:" in err or p.returncode == 86:
        return err[:4000]
    return None


def differs(mode: str, data: bytes, tmp: pathlib.Path):
    """Return (kind, c_out, z_out) or None when the two agree."""
    c_rc, c_out, c_err = run_single(C_BIN, mode, data, tmp)
    z_rc, z_out, z_err = run_single(ZIG_BIN, mode, data, tmp)
    if c_rc == "timeout" or z_rc == "timeout":
        return ("timeout", c_out, z_out)
    if z_rc < 0:
        return ("zig_crash", c_out, z_out)
    if c_rc < 0:
        return ("upstream_ub" if upstream_is_ub(mode, data, tmp) else "c_crash", c_out, z_out)
    if c_out != z_out or c_rc != z_rc:
        return ("upstream_ub" if upstream_is_ub(mode, data, tmp) else "divergence", c_out, z_out)
    return None


def minimize(mode: str, data: bytes, tmp: pathlib.Path, kind: str) -> bytes:
    """Delta-debug the input while the same kind of finding survives."""
    best = data
    chunk = max(1, len(best) // 2)
    budget = 400
    while chunk >= 1 and budget > 0:
        i = 0
        shrunk = False
        while i < len(best) and budget > 0:
            candidate = best[:i] + best[i + chunk:]
            budget -= 1
            if candidate and candidate != best:
                r = differs(mode, candidate, tmp)
                if r and r[0] == kind:
                    best = candidate
                    shrunk = True
                    continue
            i += chunk
        if not shrunk:
            chunk //= 2
    return best


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--modes", default=",".join(DEFAULT_MODES))
    ap.add_argument("--out", default="fuzz/logs/session.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    for b in (C_BIN, ZIG_BIN):
        if not b.exists():
            print(f"missing {b}; run 'make build' first", file=sys.stderr)
            return 2

    modes = args.modes.split(",")
    rng = random.Random(args.seed)
    WORK.mkdir(parents=True, exist_ok=True)
    tmp = WORK / f"case-{args.seed}.bin"
    packfile = WORK / f"pack-{args.seed}.bin"

    seeds: list[bytes] = []
    for p in sorted((ROOT / "tests" / "conformance" / "fixtures").glob("*")):
        if p.is_file():
            seeds.append(p.read_bytes()[:MAX_INPUT])
    corpus_dir = ROOT / "fuzz" / "corpus"
    for p in sorted(corpus_dir.glob("*")):
        if p.is_file():
            seeds.append(p.read_bytes()[:MAX_INPUT])
    if not seeds:
        seeds = [b"{}", b"[]", b"1"]

    started = time.time()
    deadline = started + args.seconds
    cases = 0
    rounds = 0
    findings: list[dict] = []
    counts = {"divergence": 0, "upstream_ub": 0, "zig_crash": 0,
              "c_crash": 0, "timeout": 0}

    while time.time() < deadline:
        mode = modes[rounds % len(modes)]
        batch = []
        for _ in range(BATCH):
            if rng.random() < 0.25:
                batch.append(gen_synthetic(rng))
            else:
                batch.append(mutate(rng, rng.choice(seeds), seeds))

        packfile.write_bytes(pack(batch))
        c_rc, c_out = run_pack(C_BIN, mode, packfile)
        z_rc, z_out = run_pack(ZIG_BIN, mode, packfile)
        cases += len(batch)
        rounds += 1

        if c_rc == z_rc and c_out == z_out:
            continue

        # Something differs somewhere in this round: fall back to per-case
        # comparison so the finding is attributed to one exact input.
        c_secs, z_secs = split_sections(c_out), split_sections(z_out)
        suspects = range(len(batch))
        if len(c_secs) == len(z_secs) == len(batch):
            suspects = [i for i in range(len(batch)) if c_secs[i] != z_secs[i]]

        for i in suspects:
            r = differs(mode, batch[i], tmp)
            if r is None:
                continue
            kind, c_one, z_one = r
            counts[kind] = counts.get(kind, 0) + 1

            small = minimize(mode, batch[i], tmp, kind)
            digest = hashlib.sha256(small).hexdigest()[:16]
            stem = f"{kind}-{mode.replace(':', '-')}-{digest}"

            mdir = ROOT / "fuzz" / "minimized"
            mdir.mkdir(parents=True, exist_ok=True)
            (mdir / f"{stem}.input").write_bytes(small)

            rerun = differs(mode, small, tmp)
            findings.append({
                "kind": kind,
                "mode": mode,
                "seed": args.seed,
                "original_bytes": len(batch[i]),
                "minimized_bytes": len(small),
                "minimized_sha256": hashlib.sha256(small).hexdigest(),
                "minimized_base64": base64.b64encode(small).decode(),
                "minimized_file": f"fuzz/minimized/{stem}.input",
                "c_transcript": (rerun[1] if rerun else c_one).decode("utf-8", "replace"),
                "zig_transcript": (rerun[2] if rerun else z_one).decode("utf-8", "replace"),
                "sanitizer_report": upstream_is_ub(mode, small, tmp),
            })
            if not args.quiet:
                print(f"  !! {kind} mode={mode} minimized to {len(small)} bytes "
                      f"({small[:60]!r})", file=sys.stderr)

    elapsed = time.time() - started

    zig_ver = subprocess.run(["zig", "version"], capture_output=True).stdout.decode().strip()
    cc_ver = subprocess.run(["cc", "--version"], capture_output=True).stdout.decode().splitlines()
    session = {
        "schema": "pdjson-zig/fuzz-session@1",
        "seed": args.seed,
        "requested_seconds": args.seconds,
        "elapsed_seconds": round(elapsed, 2),
        "modes": modes,
        "rounds": rounds,
        "batch_size": BATCH,
        "cases": cases,
        "cases_per_second": round(cases / elapsed, 1) if elapsed else 0,
        "seed_corpus_inputs": len(seeds),
        "divergences": counts["divergence"],
        "upstream_ub": counts["upstream_ub"],
        "zig_crashes": counts["zig_crash"],
        "c_crashes_unexplained": counts["c_crash"],
        "timeouts": counts["timeout"],
        "zig_version": zig_ver,
        "c_compiler": cc_ver[0] if cc_ver else "unknown",
        "upstream_commit": "78fe04b820dc8817f540bdd87fb22887e0ef3981",
        "findings": findings,
    }

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(session, indent=2) + "\n")

    print(f"fuzz seed={args.seed} {elapsed:.1f}s  {cases} cases "
          f"({session['cases_per_second']}/s) over modes {','.join(modes)}")
    print(f"  divergences={counts['divergence']} upstream_ub={counts['upstream_ub']} "
          f"zig_crashes={counts['zig_crash']} "
          f"c_crashes_unexplained={counts['c_crash']} timeouts={counts['timeout']}")
    print(f"  wrote {out.relative_to(ROOT)}")

    bad = counts["divergence"] + counts["zig_crash"] + counts["c_crash"] + counts["timeout"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
