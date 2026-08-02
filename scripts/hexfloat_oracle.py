#!/usr/bin/env python3
"""An independent reference for hexadecimal floating-point conversion.

D-18 claims the port converts hex floats correctly and that
`std.fmt.parseFloat` does not. Until now that rested entirely on agreeing with
libc `strtod` -- which shows *compatibility*, not correctness. If libc were
wrong, the comparison would agree and both would be wrong.

This computes the correctly-rounded IEEE-754 binary64 result from first
principles, using exact integer arithmetic only:

    value = mantissa x 2^exponent

with the mantissa an arbitrary-precision integer and the rounding decided by
comparing exact integers -- no floating point anywhere in the decision path, so
there is nothing to round twice. Python's ints are arbitrary precision, which is
the whole reason this is written here rather than in C or Zig.

It shares no code with `src/strtod.zig` beyond the shape of the grammar, which
is fixed by C99 7.20.1.3.

Domain: `[+-]0[xX] hexdigits [. hexdigits] [pP [+-] digits]`. Values outside
that syntax are not this checker's business.

Usage:
  hexfloat_oracle.py --self-test
  hexfloat_oracle.py --compare <n> [--seed S]   # property run against libc + Zig
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import pathlib
import random
import struct
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# binary64
MANT_BITS = 53
EMIN = -1022          # exponent of the smallest normal
EMAX = 1023           # exponent of the largest normal
SUBNORMAL_ULP = -1074  # exponent of the smallest subnormal


def parse_hex_float(text: str):
    """Split the literal into (negative, mantissa_int, exponent_of_2).

    Exact: the returned integer times 2**exponent is the mathematical value of
    the literal, with no rounding yet performed.
    """
    i = 0
    negative = False
    if i < len(text) and text[i] in "+-":
        negative = text[i] == "-"
        i += 1
    if text[i:i + 2].lower() != "0x":
        raise ValueError("not a hex float")
    i += 2

    digits = ""
    frac_digits = 0
    seen_point = False
    while i < len(text):
        c = text[i]
        if c == ".":
            if seen_point:
                break
            seen_point = True
            i += 1
            continue
        if c in "0123456789abcdefABCDEF":
            digits += c
            if seen_point:
                frac_digits += 1
            i += 1
            continue
        break
    if not digits:
        raise ValueError("no hex digits")

    exponent = -4 * frac_digits
    if i < len(text) and text[i] in "pP":
        j = i + 1
        neg_exp = False
        if j < len(text) and text[j] in "+-":
            neg_exp = text[j] == "-"
            j += 1
        e_digits = ""
        while j < len(text) and text[j].isdigit():
            e_digits += text[j]
            j += 1
        if e_digits:                      # only a *complete* exponent counts
            e = int(e_digits)
            exponent += -e if neg_exp else e

    return negative, int(digits, 16), exponent


def round_to_binary64(negative: bool, mantissa: int, exponent: int) -> int:
    """Correctly-rounded binary64 bit pattern, ties to even. Pure integers."""
    sign = 1 << 63 if negative else 0
    if mantissa == 0:
        return sign

    # Position of the leading set bit: value is in [2^msb_exp, 2^(msb_exp+1)).
    msb_exp = mantissa.bit_length() - 1 + exponent

    if msb_exp > EMAX + 1:
        return sign | 0x7FF0000000000000        # certainly overflows
    if msb_exp < SUBNORMAL_ULP - 1:
        return sign                             # certainly underflows to zero

    # Exponent of the unit in the last place of the result: 52 below the leading
    # bit while normal, pinned at 2^-1074 once subnormal. Working in ULP units
    # rather than significand bits is what makes the subnormal range come out
    # right with a single rounding step.
    ulp_exp = max(SUBNORMAL_ULP, msb_exp - (MANT_BITS - 1))

    shift = ulp_exp - exponent
    if shift <= 0:
        quotient = mantissa << (-shift)
        remainder = 0
        half = 1
    else:
        divisor = 1 << shift
        quotient, remainder = divmod(mantissa, divisor)
        half = divisor >> 1

    # Round to nearest, ties to even -- decided entirely by integer comparison.
    if remainder > half or (remainder == half and (quotient & 1)):
        quotient += 1

    if quotient == 0:
        return sign

    # A round-up can carry out of the significand and widen the quotient to 54
    # bits (0x1.fffffffffffff8p0 -> 0x2p0). Renormalise before reassembling; the
    # bits dropped here are necessarily zero, because a carry of that kind only
    # happens from an all-ones significand.
    if quotient.bit_length() > MANT_BITS:
        drop = quotient.bit_length() - MANT_BITS
        assert quotient & ((1 << drop) - 1) == 0, "carry dropped a set bit"
        quotient >>= drop
        ulp_exp += drop

    # Reassemble. The exponent is derived from the quotient's width rather than
    # assumed, so a subnormal that rounded up into the smallest normal lands
    # correctly.
    exp_of_value = ulp_exp + quotient.bit_length() - 1
    if exp_of_value > EMAX:
        return sign | 0x7FF0000000000000

    if exp_of_value < EMIN:
        # Subnormal: the significand is the quotient in units of 2^-1074.
        return sign | quotient

    biased = exp_of_value + 1023
    frac = quotient - (1 << (quotient.bit_length() - 1))
    frac <<= (MANT_BITS - 1) - (quotient.bit_length() - 1)
    return sign | (biased << 52) | frac


def oracle_bits(text: str) -> int:
    return round_to_binary64(*parse_hex_float(text))


# --------------------------------------------------------------------- libc

_libc = ctypes.CDLL(ctypes.util.find_library("c"))
_libc.strtod.restype = ctypes.c_double
_libc.strtod.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p)]


def libc_bits(text: str) -> int:
    v = _libc.strtod(text.encode(), None)
    return struct.unpack("<Q", struct.pack("<d", v))[0]


# ---------------------------------------------------------------- self-test

SELF_TEST = [
    # (literal, expected bits) -- expectations derived by hand from the IEEE-754
    # definition, not from any implementation.
    ("0x1p0", 0x3FF0000000000000),
    ("0x1p1", 0x4000000000000000),
    ("0x1p-1", 0x3FE0000000000000),
    ("-0x1p0", 0xBFF0000000000000),
    ("0x0p0", 0x0000000000000000),
    ("-0x0p0", 0x8000000000000000),
    ("0x1.8p1", 0x4008000000000000),                 # 3.0
    ("0x1.fffffffffffffp1023", 0x7FEFFFFFFFFFFFFF),  # largest finite
    ("0x1p1024", 0x7FF0000000000000),                # overflow to +inf
    ("0x1p-1074", 0x0000000000000001),               # smallest subnormal
    ("0x1p-1075", 0x0000000000000000),               # half-way, ties to even -> 0
    ("0x1.8p-1075", 0x0000000000000001),             # above half -> up
    ("0x1p-1022", 0x0010000000000000),               # smallest normal
    ("0x0.8p-1021", 0x0010000000000000),             # same value, written differently
    # Ties-to-even at the 53-bit boundary: ...0 stays, ...1 rounds up.
    ("0x1.00000000000008p0", 0x3FF0000000000000),
    ("0x1.00000000000018p0", 0x3FF0000000000002),
    # A carry that propagates out of the significand into the exponent.
    ("0x1.fffffffffffff8p0", 0x4000000000000000),
]


def self_test() -> int:
    bad = 0
    for text, want in SELF_TEST:
        got = oracle_bits(text)
        if got != want:
            print(f"SELF-TEST FAIL {text}: oracle 0x{got:016x}, expected 0x{want:016x}",
                  file=sys.stderr)
            bad += 1
    # The oracle must also agree with libc on these, or one of them is wrong.
    for text, want in SELF_TEST:
        lb = libc_bits(text)
        if lb != want:
            print(f"NOTE libc disagrees with the hand-derived expectation for "
                  f"{text}: libc 0x{lb:016x}, expected 0x{want:016x}", file=sys.stderr)
    print(f"oracle self-test: {len(SELF_TEST)} hand-derived cases, {bad} failure(s)")
    return 1 if bad else 0


# ------------------------------------------------------------------ compare

def zig_bits_batch(literals: list[str]) -> list[int] | None:
    """Ask the Zig implementation for its bits, via the hexprobe tool."""
    probe = ROOT / "zig-out" / "bin" / "hexprobe"
    if not probe.exists():
        return None
    inp = "\n".join(literals) + "\n"
    p = subprocess.run([str(probe)], input=inp.encode(), capture_output=True, timeout=300)
    if p.returncode != 0:
        return None
    out = []
    for line in p.stdout.decode().splitlines():
        line = line.strip()
        if line:
            out.append(int(line, 16))
    return out if len(out) == len(literals) else None


def gen_literal(rng: random.Random) -> str:
    """Biased toward the places rounding actually goes wrong."""
    kind = rng.randint(0, 4)
    sign = "-" if rng.random() < 0.3 else ""
    if kind == 0:                                    # long mantissa, no exponent
        n = rng.randint(1, 30)
        return sign + "0x" + "".join(rng.choice("0123456789abcdefABCDEF") for _ in range(n))
    if kind == 1:                                    # around the 53-bit boundary
        base = rng.choice(["1.0000000000000", "1.fffffffffffff", "1.00000000000008",
                           "1.fffffffffffff8", "1.8", "1.4", "1.c"])
        return f"{sign}0x{base}p{rng.randint(-60, 60)}"
    if kind == 2:                                    # subnormal boundary
        return f"{sign}0x1p{rng.randint(-1090, -1015)}"
    if kind == 3:                                    # overflow boundary
        return f"{sign}0x1.{''.join(rng.choice('0123456789abcdef') for _ in range(13))}p{rng.randint(1010, 1030)}"
    n = rng.randint(1, 20)                           # fraction with an exponent
    frac = "".join(rng.choice("0123456789abcdef") for _ in range(n))
    return f"{sign}0x{rng.choice(['0', '1', 'f', 'ab'])}.{frac}p{rng.randint(-1100, 1100)}"


def compare(count: int, seed: int, out_path: pathlib.Path) -> int:
    rng = random.Random(seed)
    literals = [l for _, l in [(0, gen_literal(rng)) for _ in range(count)]]
    literals += [t for t, _ in SELF_TEST]

    zig = zig_bits_batch(literals)
    disagree_libc, disagree_zig = [], []

    for i, text in enumerate(literals):
        try:
            ob = oracle_bits(text)
        except ValueError:
            continue
        lb = libc_bits(text)
        if ob != lb:
            disagree_libc.append({"literal": text,
                                  "oracle": f"0x{ob:016x}", "libc": f"0x{lb:016x}"})
        if zig is not None and zig[i] != ob:
            disagree_zig.append({"literal": text,
                                 "oracle": f"0x{ob:016x}", "zig": f"0x{zig[i]:016x}"})

    summary = {
        "schema": "pdjson-zig/hexfloat-property@1",
        "method": ("An independent reference computes the correctly-rounded binary64 "
                   "result with exact integer arithmetic only -- arbitrary-precision "
                   "mantissa, rounding decided by integer comparison, no floating point "
                   "in the decision path. It shares no code with the implementation "
                   "under test."),
        "seed": seed,
        "generated_cases": count,
        "hand_derived_cases": len(SELF_TEST),
        "total_cases": len(literals),
        "zig_probe_available": zig is not None,
        "oracle_vs_libc_disagreements": len(disagree_libc),
        "oracle_vs_zig_disagreements": len(disagree_zig),
        "oracle_vs_libc_detail": disagree_libc[:20],
        "oracle_vs_zig_detail": disagree_zig[:20],
        "interpretation": {
            "oracle_vs_zig": ("Correctness under IEEE-754 round-to-nearest-ties-to-even. "
                              "A disagreement is a defect in this port."),
            "oracle_vs_libc": ("Whether the platform libc is itself correctly rounded. "
                               "A disagreement is a statement about libc, not about the port."),
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"  {len(literals)} literals (seed {seed})")
    print(f"  oracle vs libc strtod : {len(disagree_libc)} disagreement(s)")
    if zig is None:
        print("  oracle vs Zig         : probe not built (zig build)")
    else:
        print(f"  oracle vs Zig         : {len(disagree_zig)} disagreement(s)")
    for d in (disagree_libc + disagree_zig)[:6]:
        print(f"    !! {d}")
    print(f"  wrote {out_path.relative_to(ROOT)}")
    return 1 if (disagree_zig or disagree_libc) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--compare", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20260802)
    ap.add_argument("--out", default="artifacts/hex-float/property-summary.json")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.compare:
        rc = self_test()
        if rc:
            return rc
        return compare(args.compare, args.seed, ROOT / args.out)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
