# pdjson-zig

**A clean-room Zig rewrite of [skeeto/pdjson](https://github.com/skeeto/pdjson), a C streaming JSON parser — verified against the original by byte-identical behaviour transcripts.**

<!-- SUMMARY:BEGIN -->
| | |
| --- | --- |
| **Migration** | C → Zig (Port Mortem 2026, Track G) |
| **Upstream** | `skeeto/pdjson` @ [`78fe04b`](https://github.com/skeeto/pdjson/commit/78fe04b820dc8817f540bdd87fb22887e0ef3981) (master, 2024-02-22, Unlicense) |
| **Dominant proof** | Two independent programs drive the C original and the Zig port through the same script and emit deterministic NDJSON behaviour transcripts. Equivalence means **byte-identical transcripts**. |
| **Upstream tests** | **18/18** assertions pass, sources unmodified and hash-pinned, linked against only the Zig library |
| **Differential** | **0 divergences** in 6,104 fixed-corpus + 3,816 JSONTestSuite comparisons, across all 4 input sources ([matrix](docs/differential-sources.md)) and 28 drive modes |
| **Fuzzing** | 30-minute published session, **11,720,000 cases, 0 divergences, 0 crashes, 0 timeouts** ([raw trace](fuzz/logs/session-published-raw.ndjson.gz), 29,302 rounds recorded as it ran) |
| **Harness self-test** | **12/12** injected defects caught; **54/54 specified state transitions** exercised ([`docs/state-machine.md`](docs/state-machine.md)) |
| **C ABI** | Identical layout on **6 targets** (32- and 64-bit, x86, ARM, RISC-V, Windows), asserted at compile time across 27 fields so a drift fails `zig build` ([`docs/abi.md`](docs/abi.md)) |
| **Safety** | 0 `@constCast`, 0 `unreachable`, 0 force-unwraps, 0 inline asm; **59 escape hatches, each justified individually** ([`docs/safety.md`](docs/safety.md)). Ships **ReleaseSafe** — checks on. |
| **Benchmark** | **Slower on 9 of 12** workload/mode pairs, faster on 3, and **2.42x larger** in a consumer's stripped binary. Both tables below, generated from the artifacts. |
| **Invariants** | 13,936 transcripts and 6,162,897 records checked against 13 rules that reference neither implementation: **0 violations** |
| **API coverage** | All 22 exported functions behaviourally compared; **0 untested** |
| **Upstream bugs found** | 3, all filed with minimal reproducers: [#36](https://github.com/skeeto/pdjson/issues/36), [#37](https://github.com/skeeto/pdjson/issues/37), [#38](https://github.com/skeeto/pdjson/issues/38). Two independently confirmed by Valgrind. |
<!-- SUMMARY:END -->

```sh
make verify
```

One command, <!-- STEPS:BEGIN -->24<!-- STEPS:END --> numbered steps. It pins upstream by hash, builds both
implementations, proves the Zig artifact contains no C parser code, checks the
ABI four ways *including that the check itself can fail*, runs the untouched
upstream suite against Zig, runs the differential corpus across four input
sources, measures state-transition coverage against a written specification,
checks transcript invariants that reference neither implementation, fuzzes,
inventories every escape hatch, benchmarks, measures the size a consumer pays,
and finally validates **and audits** every claim below against the files it just
generated. It fails on the first thing that does not hold.

The audit step is there because validating a claim's machine check is not the
same as checking its English: a claim can assert `divergences == 0` correctly
while its prose quotes a number that moved three commits ago. That happened, and
[`scripts/audit-claims.py`](scripts/audit-claims.py) is what now catches it.

- **Demo video:** _not recorded. Script ready in [`docs/demo-script.md`](docs/demo-script.md)._
- **Devfolio project:** _not submitted. Copy staged in [`docs/devfolio-submission.md`](docs/devfolio-submission.md); screenshots pending ([checklist](docs/screenshot-checklist.md))._

---

## The claim, stated so it can be falsified

> On the untouched original pdjson test suite, a fixed conformance corpus, an
> independent standards corpus, and a published differential fuzz run, the Zig
> implementation produces the same defined observable parser behaviour as the
> pinned C original — without using the original parser implementation.

"Observable" means everything reachable through `pdjson.h`: the event sequence,
the exact token bytes, the `double` from `json_get_number` compared as raw IEEE-754
bits, the diagnostic string, line number, byte position, depth, container context,
and the reset boundary. Not pointer values, not timing, not uninitialised padding.

"Defined" is doing real work in that sentence, and it is not decided by me. The
original invokes undefined behaviour on some inputs. When the two implementations
differ, the harness re-runs the case against an **ASan+UBSan build of the pinned
original**; if the sanitizer fires, the case is classified `upstream_ub` and
reported separately, with the sanitizer output attached as evidence. All 45 such
cases in this corpus resolve to a single line, `pdjson.c:912` — which is upstream
bug [#36](https://github.com/skeeto/pdjson/issues/36).

<!-- CLAIMS:BEGIN -->
| # | Claim | Status | Evidence |
| --- | --- | --- | --- |
| C-01 | All 18 assertions in the unmodified upstream test suite pass against the Zig library. | verified | [`artifacts/original-test-report.json`](artifacts/original-test-report.json) |
| C-02 | Zero assertions in the upstream suite are skipped, adapted, or marked unsupported. | verified | [`artifacts/original-test-report.json`](artifacts/original-test-report.json) |
| C-03 | The upstream source tree is byte-identical to commit 78fe04b across all 9 files. | verified | [`artifacts/upstream-manifest.json`](artifacts/upstream-manifest.json) |
| C-04 | Across 6,104 differential comparisons on the fixed corpus, spanning all four documented input sources and every exported function, the Zig implementation and the pinned C original produce byte-identical behaviour transcripts, with 0 divergences. | verified | [`artifacts/differential-summary.json`](artifacts/differential-summary.json) |
| C-05 | The differential comparison covers 28 drive modes: four input sources crossed with peek, skip, reset, the separator API and strict mode, plus five json_skip_until targets, four allocation-failure schedules, and a mode that calls json_next past the terminal event. | verified | [`artifacts/differential-summary.json`](artifacts/differential-summary.json) |
| C-06 | Differential testing and the oracle determinism gate found three defects in the pinned original, all reported upstream with minimal public-API reproducers: a null dereference and an out-of-bounds read at pdjson.c:912, a 0xFF/EOF confusion in the buffer source, and an uninitialised read in json_get_number. | verified | [`artifacts/upstream-issues.json`](artifacts/upstream-issues.json) |
| C-07 | The memory-buffer source in the pinned original treats byte 0xFF as end-of-input on signed-char targets, disagreeing with its own FILE* source on identical input. | verified | [`artifacts/upstream-issues.json`](artifacts/upstream-issues.json) |
| C-08 | The Zig static library is built only from Zig-produced objects, exports all 22 public symbols from the pinned header, and contains no upstream parser code. | verified | [`artifacts/linkage-report.json`](artifacts/linkage-report.json) |
| C-09 | The Zig declarations and the pinned C header agree on every struct offset, size, alignment and enumerator, with sizeof(struct json_stream) == 272 on this target. | verified | [`artifacts/abi/abi-report.json`](artifacts/abi/abi-report.json) |
| C-10 | A C program that includes the pinned public header and declares struct json_stream by value links against only the Zig archive and passes its checks. | verified | [`artifacts/abi/abi-report.json`](artifacts/abi/abi-report.json) |
| C-11 | The shipped library uses no @constCast, no inline assembly, no @setRuntimeSafety, no unreachable, and no force-unwraps; its 10 pointer casts are confined to the C allocator and char* boundaries and are individually enumerated. | verified | [`artifacts/safety-report.json`](artifacts/safety-report.json) |
| C-12 | The shipped artifact is built in ReleaseSafe, so bounds checks and overflow checks are active at runtime, including in the reported benchmark figures. | verified | [`artifacts/safety-report.json`](artifacts/safety-report.json) |
| C-13 | All 12 deliberately injected defects in the Zig implementation are detected by the fixed-corpus differential, with zero survivors. | verified | [`artifacts/mutation-report.json`](artifacts/mutation-report.json) |
| C-14 | Both transcript producers are deterministic: five runs over every fixture in five modes produce byte-identical output. | verified | [`artifacts/determinism-report.json`](artifacts/determinism-report.json) |
| C-15 | The Zig implementation's json_get_number matches C strtod bit for bit across a 661-point exponent sweep, powers of two, digit strings up to 500 digits, and 40,000 randomised lexemes -- 20,000 decimal and 20,000 hex floats. | verified | [`artifacts/number-torture.json`](artifacts/number-torture.json) |
| C-16 | The Zig implementation is slower than the C original on 9 of the 12 benchmark workload/mode pairs measured, and faster on 3. | verified | [`artifacts/benchmark-summary.json`](artifacts/benchmark-summary.json) |
| C-17 | A published 30-minute differential fuzz session of 11,720,000 cases across 12 drive modes and all three streaming input sources found zero divergences, zero crashes and zero timeouts, with a round-by-round raw trace committed alongside the summary. | verified | [`artifacts/verification-report.json`](artifacts/verification-report.json) |
| C-18 | Behavioural equivalence is demonstrated for all three documented input sources: json_open_buffer, json_open_stream (FILE*) and json_open_user. | verified | [`artifacts/differential-summary.json`](artifacts/differential-summary.json) |
| C-19 | On the independent nst/JSONTestSuite conformance corpus, the Zig port and the pinned C original agree on all 318 parsing cases across 12 drive modes and three input sources -- 3,816 comparisons -- and the original is fully conforming (95/95 must-accept, 188/188 must-reject). | verified | [`artifacts/conformance-report.json`](artifacts/conformance-report.json) |
| C-20 | No divergence has ever been observed on any input where the pinned original is well defined: 6,104 fixed-corpus comparisons plus 3,816 JSONTestSuite comparisons plus 11,720,000 published fuzz cases -- 11,729,920 in total, all at zero. | verified | [`artifacts/verification-report.json`](artifacts/verification-report.json) |
| C-21 | Verification found two real defects in this port -- a hex-float rounding error and an uninitialised read inherited from the original -- both fixed, regression-tested and documented. | verified | [`artifacts/differential-summary.json`](artifacts/differential-summary.json) |
| C-22 | Both transcript producers are deterministic on Linux and macOS: five runs over every fixture in five modes produce byte-identical output. | verified | [`artifacts/determinism-report.json`](artifacts/determinism-report.json) |
| C-23 | The Zig type declarations and the pinned C header describe the same ABI on 6 targets spanning 32- and 64-bit, little-endian ARM, x86, RISC-V and Windows. | verified | [`artifacts/abi/abi-cross-report.json`](artifacts/abi/abi-cross-report.json) |
| C-24 | Valgrind memcheck independently confirms both memory defects in the pinned original and finds no further ones; the upstream test suite itself is clean under memcheck. | verified | [`artifacts/valgrind-report.json`](artifacts/valgrind-report.json) |
| C-25 | An implementation-independent invariant checker validates 13,936 transcripts and 6,162,897 records from both implementations against 13 rules, with zero violations on either side. | verified | [`artifacts/invariants/summary.json`](artifacts/invariants/summary.json) |
| C-26 | Hex-float conversion in this port is correctly rounded under IEEE-754, verified against an exact-integer reference that shares no code with it, over 200,017 literals concentrated at the rounding boundaries. | verified | [`artifacts/hex-float/property-summary.json`](artifacts/hex-float/property-summary.json) |
| C-28 | All 22 exported functions have their behaviour compared between the two implementations; none is untested. | verified | [`artifacts/differential/api-coverage.json`](artifacts/differential/api-coverage.json) |
| C-29 | The struct layout the C compiler reads out of the pinned header is asserted against src/abi.zig at compile time, so a layout drift fails `zig build` itself: 27 field offsets and sizes, 11 enumerators, 7 struct size and alignment values. | verified | [`artifacts/abi/abi-report.json`](artifacts/abi/abi-report.json) |
| C-30 | The compile-time ABI contract is demonstrated to be capable of failing: 10 injected layout drifts -- 6 in the recorded C layout, 4 in the port's own declarations -- are each caught by the build, with an unmodified control that builds clean. | verified | [`artifacts/abi/contract-negative.json`](artifacts/abi/contract-negative.json) |
| C-31 | The Zig archive exports exactly the 22 functions the pinned header declares -- none missing, none extra -- compared as a set rather than as a count. | verified | [`artifacts/abi/abi-report.json`](artifacts/abi/abi-report.json) |
| C-32 | Every escape hatch in the shipped library is classified individually -- 59 occurrences across 8 categories, each matched to a rule keyed by enclosing function rather than by line number, with 0 unclassified. | verified | [`artifacts/safety/inventory.json`](artifacts/safety/inventory.json) |
| C-33 | The escape-hatch classifier passes 10 self-tests covering the ways it could silently report the wrong thing, including a hatch mentioned only in a comment, a // inside a string literal, and a test block's scratch being counted as shipped code. | verified | [`artifacts/safety/inventory.json`](artifacts/safety/inventory.json) |
| C-34 | The Zig port costs a consumer more space than the original: linking one identical C program against the Zig archive rather than the pinned original's object grows the stripped executable 2.42x and the machine-code section 3.29x. | verified | [`artifacts/size-report.json`](artifacts/size-report.json) |
| C-35 | The parser's transition relation is written out as a 10-state specification derived from RFC 8259 and pdjson.h, and the corpus exercises all 54 specified transitions, with 0 transitions observed that the specification does not contain. | verified | [`artifacts/state-machine/coverage.json`](artifacts/state-machine/coverage.json) |
| C-36 | Both implementations cover exactly the same set of state transitions on the same inputs: 0 transitions are reached by one and not the other. | verified | [`artifacts/state-machine/coverage.json`](artifacts/state-machine/coverage.json) |
| C-37 | All four documented input sources are exercised with real comparison counts, not asserted: json_open_buffer 3,270, json_open_stream 1,090 (a real FILE*), json_open_user 1,090 (caller callbacks), json_open_string 654 -- each at 0 divergences. | verified | [`artifacts/differential/source-matrix-fixed-corpus.json`](artifacts/differential/source-matrix-fixed-corpus.json) |
| C-38 | The differential's comparison is demonstrated to be sensitive to every field a transcript record carries -- event, token bytes, token length, number bits, line, position, depth, context, context count, error text, operation and sequence -- by perturbing each field and requiring the comparison to notice. | verified | [`artifacts/mutation/detector-selftest.json`](artifacts/mutation/detector-selftest.json) |
| C-39 | The differential's strength is shown to be load-bearing: the same 12 injected defects over the same 1,489 comparable cases go from 12 caught to 4 when only the comparison is weakened to the event sequence, so 8 are detected solely by the fields beyond it. | verified | [`artifacts/mutation-report-weakened.json`](artifacts/mutation-report-weakened.json) |
<!-- CLAIMS:END -->

Every row is checked against a generated artifact by
[`scripts/validate-claims.py`](scripts/validate-claims.py), which runs as the last
step of `make verify`. A claim whose check fails is a build failure. The table
itself is generated from [`CLAIMS.json`](CLAIMS.json) — a stale number here is a
diff, not something you have to catch.

---

## Judge path

Roughly ten minutes, in the order the evidence builds on itself.

```sh
make verify                                    # 1. everything, ~4 minutes
```

Then spot-check the parts that are easiest to fake:

```sh
# 2. The upstream tests really are untouched.
sh scripts/verify-upstream-hashes.sh
echo "tampered" >> upstream/pdjson/tests/tests.c
sh scripts/verify-upstream-hashes.sh           # fails, as it should
git checkout upstream/pdjson/tests/tests.c

# 3. The Zig library really contains no C parser.
sh scripts/verify-no-c-linkage.sh
ar t zig-out/lib/libpdjson.a                   # Zig objects only
nm -g zig-out/lib/libpdjson.a | grep ' T _\?json_' | wc -l   # 22 exports

# 4. The upstream suite, compiled in place, linked against only the Zig archive.
cc -std=c99 -o /tmp/t upstream/pdjson/tests/tests.c zig-out/lib/libpdjson.a && /tmp/t

# 5. The transcripts really are identical, on something awkward.
./build/transcript_c   next tests/conformance/fixtures/uni-escaped-pair-max.json
./zig-out/bin/transcript_zig next tests/conformance/fixtures/uni-escaped-pair-max.json

# 6. The harness would actually notice a defect.
make mutation                                  # ~15 min; 12/12 caught

# 7. The upstream bugs are real.
cc -std=c99 -g -fsanitize=address,undefined -I upstream/pdjson \
   -o /tmp/r tests/upstream-bugs/repro_oom_stack.c upstream/pdjson/pdjson.c && /tmp/r
```

Nothing above needs the network. `scripts/fetch-upstream.sh` re-clones from
GitHub at the pinned commit and diffs, if you want to confirm the pin
independently.

---

## Why this migration is worth doing

pdjson is a good C library: 992 lines, no dependencies, bounded memory, and a
streaming API that handles arbitrarily large documents in space proportional to
the largest token. It is also a parser for untrusted input written in a language
with no bounds checking, and it exposes a raw `struct` with a heap pointer and
two indices that must stay consistent. Both bugs found here are instances of that
second property: an allocation fails, two fields disagree, and an accessor reads
memory that was never allocated.

That is the migration case in miniature. The port keeps the design — the same
event-pull state machine, the same bounded memory profile, the same C ABI — and
moves the class of defect that produced [#36](https://github.com/skeeto/pdjson/issues/36)
from "silent memory read" to "checked at runtime".

## Architecture

```
src/abi.zig      C-layout types. Written independently of the header, then
                 proved equivalent to it by two probes and by a comptime
                 contract generated from the header (src/abi_contract.zig).
src/parser.zig   The state machine. Event-pull, iterative, heap container stack.
src/errmsg.zig   Byte-exact reconstruction of pdjson's diagnostics.
src/strtod.zig   A locale-independent strtod (upstream #27 is that libc's is not).
src/c_api.zig    The 22 exported json_* symbols. No parser logic.
src/api.zig      A Zig-native face: slices, error unions, defer.
```

Both faces drive the same state machine. `json_stream` is deliberately **not**
opaque, because upstream's header spells out every field and its own tests
declare the struct by value on the stack — so a drop-in replacement has to
reproduce the layout, not just the signatures.

## How equivalence is actually established

`oracle/transcript_c.c` links the pinned `pdjson.c`. `tools/transcript_zig.zig`
uses the Zig library. Neither shares a formatter with the other — a shared
emitter could normalise a real difference away on both sides at once. Each emits
one NDJSON record per operation:

```json
{"seq":3,"op":"next","event":"NUMBER","tok":"3100","toklen":2,
 "num":"3ff0000000000000","line":1,"pos":7,"depth":2,
 "ctx":"ARRAY","ctxn":1,"err":null}
```

Tokens are hex because they legitimately contain NUL, invalid UTF-8, and control
bytes. Numbers are IEEE-754 bit patterns because `-0.0`, infinities and NaN
payloads all matter. Schema and rationale: [`docs/transcript-schema.md`](docs/transcript-schema.md).

Every input runs through **19 drive modes** — three input sources crossed with
five ways of driving the parser, plus four allocation-failure schedules.

The *sources* are `json_open_buffer` (a byte array), `json_open_stream` (a
`FILE *`, so reads go through `fgetc`/`ungetc`) and `json_open_user` (caller
callbacks). Comparing all three matters more than it might look: upstream issue
[#37](https://github.com/skeeto/pdjson/issues/37) is precisely a case where two
of them disagree on identical bytes, so a single-source comparison would have
missed that entire class of difference.

The *drives* are `next`, `nostream` (strict), `peek`, `skip`, `sep` (the README's
separator loop via `json_source_get`/`json_source_peek`), and the deterministic
allocation-failure schedules `oom:0/1/2/5` that surfaced bug #36.

### Test preservation

The upstream test files are compiled **in place** from `upstream/pdjson/tests/`.
No copy is made, no line is edited, and only the link line differs from a C
build. [`scripts/verify-upstream-hashes.sh`](scripts/verify-upstream-hashes.sh)
enforces byte-identity across all nine pinned files and fails on drift, added
files, and deletions — so "we did not touch the tests" is checked, not promised.
Details: [`docs/test-preservation.md`](docs/test-preservation.md).

`tests.c` is an assertion suite, so its own PASS/FAIL lines become the per-test
report. `stream.c` and `pretty.c` are tools, not assertion suites, so they are
used differentially instead: built twice — once against C, once against Zig — and
their output compared byte for byte over every fixture. `pretty.c` matters here
because it leans on `json_peek` and `json_get_depth`, which the assertion suite
barely touches.

### Does the harness have teeth?

A comparison harness that never fails proves nothing.
[`scripts/mutation-test.py`](scripts/mutation-test.py) injects twelve deliberate
defects into the Zig implementation — wrong escape mapping, off-by-one surrogate
range, dropped NUL terminator, uncounted newline, altered diagnostic, unsigned
buffer reads — rebuilds, and requires the differential to catch each one.

Its first sound run caught 8 of 12. The four survivors were **real gaps**: no
fixture had an escaped surrogate pair at the top of the range, none had a raw
control byte at the 0x1F boundary, and allocation-failure diagnostics were not
covered at all. The corpus grew from 142 to 214 fixtures to close them (215 today, after a later fuzz finding).

Two earlier runs also produced **false 12/12 scores**, both caught and both
fixed. Details in [`DECISIONS.md` D-17](DECISIONS.md) — that entry is worth
reading before trusting this section.

## Fuzzing

[`fuzz/fuzz.py`](fuzz/fuzz.py) mutates a seed corpus and generates grammar-based
JSON, number-boundary and Unicode-boundary cases, then runs both implementations
over batches of inputs per process pair (the batch size is recorded in each
session log). That batching is what makes the
measured rate in `fuzz/logs/` possible; per-case process spawning would be
orders of magnitude slower.

Before measuring anything it runs a trivial document through both binaries and
refuses to start unless both produce a valid, identical transcript — because "the
implementations disagree" and "one binary is broken" produce the same diff, and
the second one has happened here (DECISIONS.md D-20).

Every finding is isolated to one input, minimized by delta debugging, and written
to `fuzz/minimized/` with both transcripts. Duration, seed, case count and rate
are recorded exactly in `fuzz/logs/`. Allocation-failure modes are excluded from
the fuzz defaults because the original crashes constantly there and swamps the
signal; they are covered exhaustively by the fixed corpus and by
`tests/port/allocator_failure.zig`, which walks every failure point for twelve
inputs and checks for leaks.

## Benchmark: mostly slower, honestly reported

Full methodology, including the profiles: [`bench/methodology.md`](bench/methodology.md).
Raw per-iteration samples: `bench/results/raw.json`.

Ratios are C median / Zig median, so **below 1.00 means Zig is slower**.

<!-- BENCH:BEGIN -->
| workload | mode | ReleaseSafe (shipped) | ReleaseFast |
| --- | --- | --- | --- |
| large-mixed | parse | 0.87x | 0.89x |
| large-mixed | strings | 1.04x | 1.07x |
| numbers | parse | 0.78x | 0.82x |
| numbers | strings | 0.96x | 1.00x |
| strings-ascii | strings | 0.91x | 0.93x |
| strings-unicode | strings | 1.02x | 1.06x |
| deep-nesting | parse | 0.84x | 0.85x |
| many-small-docs | parse | 0.89x | 0.91x |
| malformed-early | parse | 1.89x | 5.61x |
| malformed-late | parse | 0.87x | 0.89x |
| whitespace-heavy | parse | 0.93x | 0.94x |
| flat-ints | parse | 0.79x | 0.82x |

_9 of 12 workload/mode pairs are slower in Zig. Median ratios, 5 repetitions, raw samples in `bench/results/raw.json`._
<!-- BENCH:END -->

The table above is generated from `artifacts/benchmark-summary.json` by `scripts/report.py`; `make verify` fails if it is stale.

Three things worth saying plainly.

**The gap is not the cost of safety checks.** ReleaseSafe and ReleaseFast land
within about 2% of each other on every workload, so bounds and overflow checking
are close to free here. The shipped library keeps them on.

**The one large Zig win is not a throughput result.** `malformed-early` rejects
its input on the first byte, so it measures setup cost, not parsing.

**The port is bigger, and that is a real cost.** One identical C consumer,
linked twice with the same compiler and flags, differing only in which
implementation sits behind the pinned header:

<!-- SIZE:BEGIN -->
| | C original | pdjson-zig | |
| --- | ---: | ---: | --- |
| linked executable, stripped | 35,072 | 84,744 | 2.42x |
| machine code (`__text`) | 8,104 | 26,672 | 3.29x |
| read-only data | 448 | 3,104 | 6.93x |
| string data | 1,020 | 7,112 | 6.97x |

_One identical C consumer, same compiler and flags, linked twice; both binaries verified to produce the same output before any size was recorded. Archive against object (241,248 vs 16,360) is reported in the artifact but is not a fair comparison._
<!-- SIZE:END -->

ReleaseSafe carries the bounds and overflow checks the C build has no
equivalent of, and Zig emits unwind tables the C build does not. `src/root.zig`
replaces std's default panic handler with a `write`+`abort` precisely to keep
std's unwinder, DWARF reader and symbol tables out of the artifact, and the
report measures what that saves by building both: **2,461,816 bytes with std's
default handler against 241,248 with the custom one — 10.2×.** That figure used
to be quoted from memory as "4.6 MB"; it does not reproduce, which is why it is
now built rather than remembered.
Artifact: [`artifacts/size-report.json`](artifacts/size-report.json).

**The first optimization guess was wrong, and measuring is what caught it.**
I assumed the null checks the port adds on the source function pointers were the
cost, built a variant without them, and measured **0%** improvement.
Leaf-weighted profiles of both binaries then showed two different causes: clang
inlines `pushchar` into `read_digits` while Zig kept it as an out-of-line call,
and the `strchr` calls in the number lexer were being served by a generic slice
search.

Both fixes are still measurable, because both are still revertible:

<!-- OPTHISTORY:BEGIN -->
| workload | before both | today |
| --- | ---: | ---: |
| large-mixed/parse | 0.68x | 0.86x |
| flat-ints/parse | 0.57x | 0.78x |
| numbers/parse | 0.59x | 0.78x |

_C median / Zig median, so higher is better and below 1.00 means the port is slower. Artifact: [`artifacts/optimization-history.json`](artifacts/optimization-history.json)._
<!-- OPTHISTORY:END -->

[`scripts/optimization-history.py`](scripts/optimization-history.py) rebuilds
each "before" state as an exact revert of one change, in a throwaway copy of the
tree, and refuses to run if the pattern it reverts no longer matches exactly
once. These figures were originally quoted from memory of the development run;
[`scripts/audit-public-copy.py`](scripts/audit-public-copy.py) flagged them as
unbacked, which is what turned them into an artifact.

What remains is not fully explained, and is reported that way rather than
optimised against a single benchmark. Correctness gated every step: the full
differential corpus, the upstream suite, and the Zig test suite were re-run after
each change.

## C ABI

`sizeof(struct json_stream) == 272`, alignment 8, `enum json_type` 4 bytes
unsigned — and every field offset, size and enumerator agrees between the pinned
C header and the Zig declarations. Two probes emit the same table and
[`scripts/abi-check.sh`](scripts/abi-check.sh) diffs them.

The layout table alone would not prove much, so
[`tests/original/abi_consumer.c`](tests/original/abi_consumer.c) includes the
*pinned* header, declares `struct json_stream` by value on its own stack exactly
as upstream's tests do, links against only `libpdjson.a`, and exercises events,
depth, context, raw number lexemes, number values, error text, line numbers,
streaming with reset, and skip.

`include/pdjson.h` is byte-identical to upstream's (`sha256:724f8ad9…dac6`).

Executing both probes only covers targets with a runner — arm64 macOS and x86-64
Linux — and both are LP64, so they say nothing about what happens when the
pointer size changes. [`scripts/abi-cross-check.sh`](scripts/abi-cross-check.sh)
closes that without needing to execute anything: it reads Zig's layout for a
target at compile time via `@compileLog`, then asserts those numbers against the
pinned header with `_Static_assert` compiled for the same target.

| Target | `sizeof(struct json_stream)` | align |
| --- | --- | --- |
| x86-64 Linux, aarch64 Linux, x86-64 Windows, riscv64 Linux | 272 | 8 |
| i386 Linux, armhf Linux | 204 | 4 |

Both sides go through the Zig toolchain's clang there, so it is not a claim about
other compilers on those targets — the executed check covers the host compiler.
It asserts field *sizes* as well as offsets: a shorter trailing array can be
absorbed by padding, leaving `sizeof` and every offset identical. That gap was in
the first version of the check and was found by deliberately breaking it.

Both of those run from a script, though, and a consumer who runs `zig build` and
links the archive never invokes either. So the layout is also asserted **inside
the build**: a C probe emits what the pinned header dictates into
[`src/abi_generated.zig`](src/abi_generated.zig), and
[`src/abi_contract.zig`](src/abi_contract.zig) checks `src/abi.zig` against all
27 field offsets and sizes, 11 enumerators and 7 struct size and alignment
values at `comptime`. A drift fails `zig build`, naming the field.

Ten deliberate drifts — six in the recorded C layout, four in the port's own
declarations — confirm those assertions can fail, alongside a control that must
still build and a check that the 32-bit guard genuinely disengages rather than
the assertions being dead everywhere.

```
$ sh scripts/abi-contract-negative.sh
contract negative test: 10 detected, 0 missed
```

`zig build diagnose` reports the two target-dependent decisions that are
invisible in the source: whether this target's C `char` is signed (which decides
whether `0xFF` collides with `EOF`), and whether the compile-time contract is
active here or deferred to the cross-target check.

Full account: [`docs/abi.md`](docs/abi.md), including the one blind spot no
layout table can cover.

## Safety, with a definition attached

Zig has no `unsafe` keyword, so "no unsafe code" is not a claim until it has a
definition. Here it is: **no operation that can reinterpret memory, bypass a
runtime check, or read uninitialised storage** — except at a boundary that cannot
be expressed otherwise, justified individually.

`@constCast`, `unreachable`, `@setRuntimeSafety`, inline assembly, `volatile` and
force-unwraps are all at **zero**, and the check fails if one appears. The 59
that remain are each matched to a rule and explained:

| Category | # | What it is |
| --- | --- | --- |
| `checked-narrowing` | 15 | `@intCast`, which *aborts* on a wrong bound rather than corrupting memory |
| `deliberate-wraparound` | 14 | `+%`/`-%`, the reason untrusted input cannot panic this parser |
| `c-semantics-reproduction` | 12 | `char` signedness, printf `%c`, `long`→`size_t` — integer to integer, never a pointer |
| `c-allocator-boundary` | 8 | `json_allocator` speaks `void*` |
| `narrowing-to-byte` | 3 | `@truncate` on an already-bounded value |
| `public-header-boundary` | 3 | the pinned header promises `char*` |
| `ieee754-bit-pattern` | 2 | `f64` ↔ `u64`, same width |
| `write-before-read` | 2 | storage fully written before anything reads it |

Rules are keyed by **enclosing function, not line number** — the previous report
pointed at `parser.zig:1011` when the line had already become 1018, and a
justification that rots without saying so reads as verified. An occurrence with
no rule fails the build, so a new escape hatch cannot slide in under a budget
that happened to have room.

The classifier passes ten of its own tests, two of which exist because it got
those cases wrong first. Full per-occurrence account:
[`docs/safety.md`](docs/safety.md).

## Bugs found in the original

Both were found by the verification pipeline, not by reading the code. Both have
minimal public-API reproducers and were confirmed against the pinned commit
before filing.

**[#36](https://github.com/skeeto/pdjson/issues/36) — `json_get_context()` reads
an unallocated stack slot after a failed allocation.** `push()` increments
`stack_top` *before* growing the stack, so a failed `realloc` leaves it pointing
at a slot that does not exist — and, on the first push, into a `NULL` stack.
Manifests as a SEGV or a heap-buffer-overflow depending on when the allocator
gives out. Reachable through the documented `json_set_allocator` API.
Analysis: [`docs/upstream-bug-oom-stack.md`](docs/upstream-bug-oom-stack.md).

**[#38](https://github.com/skeeto/pdjson/issues/38) — `json_get_number()` reads
uninitialised bytes after a partial token.** A token that fails part way never
gets its terminating NUL, so `strtod` walks past what the parser wrote. On glibc
this makes the accessor return different values for identical input across runs
of the same binary — `0.0` then `-1.0` for the input `-`. Found not by the
differential comparison but by the gate that checks the *oracle itself* is
reproducible, which is the check that makes any of the other numbers mean
anything.
Analysis: [`docs/upstream-bug-uninit-number.md`](docs/upstream-bug-uninit-number.md).

**[#37](https://github.com/skeeto/pdjson/issues/37) — byte `0xFF` is read as EOF
by the memory-buffer source.** `buffer_peek()` reads through a `const char *`, so
on signed-`char` targets `0xFF` becomes `-1`, which is `EOF`. The `FILE *` source
uses `fgetc` and does not have this problem, so the two documented input sources
**disagree on identical bytes**; `json_get_position()` freezes; and
`json_source_get()` can never advance past the byte.
`0xFF` is never valid UTF-8, so this does not make the parser accept bad input —
it misreports where and why the input was rejected.
Analysis: [`docs/upstream-bug-0xff.md`](docs/upstream-bug-0xff.md).

Valgrind memcheck independently confirms #36 and #38 — and for #38 traces the
origin of the uninitialised bytes to `malloc` in `init_string` at `pdjson.c:186`,
which ASan cannot see at all. It finds no further defects, and the upstream test
suite is itself clean under memcheck.
[`scripts/valgrind-upstream.sh`](scripts/valgrind-upstream.sh) asserts that both
known defects still reproduce, so a silent run cannot be mistaken for a clean
one.

None is a duplicate: #31 is about the *execution* character set, #27 about
locale and `strtod`, #15 about `peek` and position. None has been triaged by
upstream at the time of writing, and the claim ledger records them as reported
rather than as independently confirmed.

Two defects were also found **in this port**, and are listed here rather than
quietly fixed: a hex-float rounding error (found by fuzzing at ~30M cases) and
the uninitialised read inherited from the original before #38 was understood.
Both are fixed and regression-tested. Verification that never finds anything in
its own subject is not evidence that anything was checked.

The port **reproduces #37 deliberately**, using Zig's `c_char` so it makes the
same signedness choice the C compiler makes on the same target. Silently fixing a
bug would break the equivalence claim in the least visible way possible. The fix
is available as `zig build -Dfix-0xff=true`, and that build is *expected* to
diverge — it is one of the twelve mutants.

## Build

Zig 0.16.0, a C99 compiler, Python 3 for the harnesses. No other dependencies.

```sh
zig build                 # ReleaseSafe static library + tools
zig build test            # 68 Zig-native tests
make verify               # the whole evidence pipeline
make docker-verify        # the same, in a container
```

Useful targets: `build`, `test`, `test-original`, `differential`, `fuzz`,
`mutation`, `bench`, `abi`, `safety`, `claims`, `report`, `release-gate`.
`make fuzz FUZZ_SECONDS=600` for a longer session.

Options: `-Dfix-0xff=true` (see above), `-Dstack-max=N` (nesting limit;
default unlimited, matching upstream).

### Using it from C

Drop-in: same header, same symbols, same struct layout.

```c
#include "pdjson.h"
struct json_stream json[1];
json_open_string(json, "{\"a\":[1,2]}");
while (json_next(json) != JSON_DONE) { /* ... */ }
json_close(json);
```

```sh
cc myprog.c -Izig-out/include zig-out/lib/libpdjson.a
```

### Using it from Zig

```zig
var p = pdjson.Parser.initBuffer(input);
defer p.deinit();
while (true) switch (try p.next()) {
    .done => break,
    .string => std.debug.print("{s}\n", .{p.tokenText()}),
    .number => std.debug.print("{d}\n", .{p.number()}),
    else => {},
};
```

## Repository map

| Path | What it is |
| --- | --- |
| `upstream/pdjson/` | The pinned original. Read-only evidence, hash-verified. |
| `src/` | The Zig implementation. |
| `include/pdjson.h` | Byte-identical to upstream's header. |
| `oracle/` | C harnesses that link the original: transcript oracle, benchmark. |
| `tools/` | Zig counterparts, the ABI probe, and `zig build diagnose`. |
| `tests/original/` | A C consumer using the pinned header. Upstream's own tests are used in place. |
| `tests/port/` | Zig-native tests: behaviour, number torture, allocator failure, regressions. |
| `tests/conformance/fixtures/` | 218 generated edge-case inputs. |
| `tests/upstream-bugs/` | Minimal reproducers for #36, #37, #38, and the Zig `parseFloat` defect. |
| `fuzz/` | Fuzzer, corpus, minimized findings, session logs. |
| `bench/` | Workloads, methodology, raw results. |
| `scripts/` | Provenance, differential, mutation, safety, claims, reports. |
| `artifacts/` | Everything `make verify` generates. |
| `docs/` | Assessment, schema, bug analyses, write-up, demo script, audit. |

The documents worth reading in their own right:

| Document | What it covers |
| --- | --- |
| [`docs/abi.md`](docs/abi.md) | The four ABI checks, and how the compile-time one was proven able to fail |
| [`docs/state-machine.md`](docs/state-machine.md) | The transition specification, and the two harness holes it exposed |
| [`docs/safety.md`](docs/safety.md) | Every escape hatch, justified individually |
| [`docs/mutation-testing.md`](docs/mutation-testing.md) | Testing the test harness, in two separate directions |
| [`docs/differential-sources.md`](docs/differential-sources.md) | The four input sources as a table rather than a sentence |
| [`docs/hex-float-proof.md`](docs/hex-float-proof.md) | Correctness under IEEE-754, not just agreement with libc |
| [`docs/transcript-invariants.md`](docs/transcript-invariants.md) | Rules that reference neither implementation |
| [`docs/write-up.md`](docs/write-up.md) | The narrative: what was hard, what went wrong, what was learned |
| [`docs/final-audit-v2.md`](docs/final-audit-v2.md) | The second adversarial audit: whether the *checks* hold, and the sixteen places they did not |

## Known limitations

<!-- LIMITS:BEGIN -->
Stated here rather than left to be discovered.

- **ABI equivalence is *executed* on two targets**, arm64 macOS and x86-64 Linux, and asserted at compile time on 6 more. Both executed targets are LP64. Three of the four findings in the first cold audit were platform-specific and invisible on the development machine, so a third executed target would likely find a fourth thing.
- **`nan(...)` payloads that overflow 64 bits are not matched.** C99 §7.20.1.3p4 makes them implementation-defined and libcs disagree. Reachable only by calling `json_get_number()` on a *string* token beginning `nan(`. ([D-09](DECISIONS.md))
- **The port is slower and larger.** Slower on 9 of 12 workload/mode pairs, and 2.42x the stripped binary in a consumer. Part of the remaining time gap is unexplained.
- **The 3 upstream issues are filed, not triaged.** No maintainer has confirmed them yet, and the ledger says so. A fourth defect, in Zig's own `std.fmt.parseFloat`, is reproduced but *not filed* -- `ziglang/zig` restricts issue creation to collaborators -- so it is embargoed from every public channel in `CLAIMS.json`.
- **Equivalence is demonstrated, not proven.** 11,729,920 compared cases and a 30-minute fuzz session is evidence, not a proof of behavioural equality. 100% state-transition coverage is not path coverage, and the hand-written specification agreeing with both implementations would not catch a shared misreading of the grammar.
- **The corpus is not adversarial to itself.** Fixtures were written by the same person who wrote the port. The independent checks against that are JSONTestSuite, the mutation harness, the invariant rules, and the state-transition specification -- each of which found something the fixtures had missed.
<!-- LIMITS:END -->

## Decisions

[`DECISIONS.md`](DECISIONS.md) — 17 entries covering the ones that actually
shaped the result: why the struct layout is pinned rather than made opaque, why
`strtod` is reimplemented, why one upstream bug is reproduced and another is not,
why counters use wrapping arithmetic, and why the harness's own first results
were wrong twice.

## License and attribution

pdjson-zig is released under the [Unlicense](UNLICENSE), matching upstream, so no
new restriction is introduced downstream of a public-domain work. Upstream's
`UNLICENSE` is preserved verbatim and hash-pinned. Full attribution and a
file-by-file account of what is reused and what is not:
[`LICENSES.md`](LICENSES.md).

pdjson is by Chris Wellons ([skeeto](https://github.com/skeeto)).
