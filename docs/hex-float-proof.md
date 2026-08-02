# Hex-float conversion: correctness, not just compatibility

`json_get_number()` is `strtod` over the token buffer, and C's `strtod` grammar
includes hex floats. [D-18](../DECISIONS.md) records that this project
implements that conversion itself because `std.fmt.parseFloat` gets it wrong.

That claim originally rested on **agreeing with libc**. Agreeing with libc shows
*compatibility*. It does not show *correctness*: if libc were wrong, the
comparison would agree and both would be wrong together.

This document is the independent proof.

## The reference

[`scripts/hexfloat_oracle.py`](../scripts/hexfloat_oracle.py) computes the
correctly-rounded binary64 result from first principles:

```
value = mantissa × 2^exponent
```

with the mantissa an arbitrary-precision integer, and the rounding decided by
**integer comparison only**:

```python
quotient, remainder = divmod(mantissa, 1 << shift)
half = (1 << shift) >> 1
if remainder > half or (remainder == half and (quotient & 1)):
    quotient += 1
```

No floating point appears anywhere in the decision path, so there is nothing to
round twice. It shares no code with `src/strtod.zig` beyond the shape of the
grammar, which is fixed by C99 §7.20.1.3. Python is the implementation language
precisely because its integers are arbitrary precision.

It rounds in units of the **result's ULP** rather than in significand bits. That
is what makes the subnormal range come out right in a single rounding step: below
2^-1022 the ULP is pinned at 2^-1074 regardless of how many significant bits the
input had.

## The reference is itself tested

17 cases with expectations derived by hand from the IEEE-754 definition, not
copied from any implementation — powers of two, the largest finite value,
overflow to infinity, the smallest subnormal, the exact half-way case that must
tie to even, a value written two different ways that must produce identical bits,
and a rounding carry that propagates out of the significand into the exponent.

```
$ python3 scripts/hexfloat_oracle.py --self-test
oracle self-test: 17 hand-derived cases, 0 failure(s)
```

Writing those by hand found a real bug in the reference: a round-up can widen the
quotient to 54 bits (`0x1.fffffffffffff8p0` → `0x2p0`), which the first version
mishandled. The self-test caught it before the reference was used to judge
anything.

## The result

```
$ python3 scripts/hexfloat_oracle.py --compare 200000 --seed 20260802
  200017 literals (seed 20260802)
  oracle vs libc strtod : 0 disagreement(s)
  oracle vs Zig         : 0 disagreement(s)
```

200,017 literals — 200,000 generated plus the 17 hand-derived — biased toward the
places rounding actually goes wrong: long mantissas, the 53-bit boundary, the
subnormal boundary, the overflow boundary.

**pdjson-zig agrees with the exact-integer reference on every one.** So does libc.

The claim is now *correct under IEEE-754 round-to-nearest-ties-to-even*, verified
independently — not merely *compatible with this platform's libc*.

Artifact: [`artifacts/hex-float/property-summary.json`](../artifacts/hex-float/property-summary.json).

## The Zig standard library defect

The same reference judges `std.fmt.parseFloat`, and it fails:

```
$ zig run tests/upstream-bugs/repro_zig_parsefloat.zig

literal                  parseFloat         expected
0xfffffffffffffffffp0    442ffffffffffffe   4430000000000000   MISMATCH
0x123456789abcdef01p0    43f23456789abcde   43f23456789abcdf   MISMATCH
0x634922337286237e3p0    4418d2488cdca188   4418d2488cdca189   MISMATCH
0x1.fffffffffffff8p0     4000000000000000   4000000000000000   ok        (control)
0x1p-1074                0000000000000001   0000000000000001   ok        (control)
0x1fffffffffffffffp0     43c0000000000000   43c0000000000000   ok        (control)

3 of 6 cases mismatch
```

Worked through for the first case: `0xfffffffffffffffffp0` is 2^68 − 1. binary64
has a 53-bit significand, so around 2^68 the representable neighbours are
2^68 − 2^15 and 2^68. The value sits 2^15 − 1 above the lower and 1 below the
upper, so the nearest is 2^68 exactly. `parseFloat` returns the lower neighbour —
the discarded bits are truncated rather than rounded.

Checks made before calling this a defect:

| Question | Answer |
| --- | --- |
| Is it unsupported syntax? | No. Every failing case has an explicit `p` exponent, so it is not the optional-exponent extension. |
| Is it only the old release? | No. Identical on `0.16.0` and on master `0.17.0-dev.1516+8a4b5424d`. |
| Is libc the odd one out? | No. libc and the exact-integer reference agree; `parseFloat` is alone. |
| Is it a known issue? | Related but distinct. #10737 (f128, fixed 2022), #20287 (large hex-float parsing, merged 2024, same fallback path), #2083 and #11477 (older, fixed). All closed; these cases still fail on master. |
| Do controls pass? | Yes — three controls round correctly, so a clean run could not be mistaken for the bug being absent. |

All failures have significands needing more than 53 bits: the fallback path taken
when the mantissa exceeds the fast-path digit limit.

### Reporting status: proven, not filed

**The issue has not been filed.** `ziglang/zig` restricts issue creation to
collaborators:

```
$ gh issue create -R ziglang/zig ...
GraphQL: could not be created. Interactions on this repository have been
restricted to collaborators only. (createIssue)
```

So this is recorded as a **reproduced and independently verified defect with no
upstream report**, not as a filed issue. The standalone reproducer is committed
at [`tests/upstream-bugs/repro_zig_parsefloat.zig`](../tests/upstream-bugs/repro_zig_parsefloat.zig)
and depends on nothing in this repository, so it can be submitted through
whatever channel the project is currently accepting.

`CLAIMS.json` records this as a target-language defect that is *demonstrated*,
with no issue URL — deliberately distinct from the three pdjson issues, which
were filed and do have URLs.

## What this does and does not establish

**Established:** within the hex-float grammar of C99 §7.20.1.3, this project's
conversion is correctly rounded under IEEE-754, verified against an exact-integer
reference that shares no code with it, over 200,017 literals concentrated at the
boundaries where rounding fails.

**Not established:** anything about the decimal path, which delegates to
`std.fmt.parseFloat` and is correct there. Anything outside that grammar.
Anything about `f32`, `f80` or `f128`. And the reference is a reference, not a
proof — it is tested, but it is code.
