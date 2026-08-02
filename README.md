# pdjson-zig

**A clean-room Zig rewrite of [skeeto/pdjson](https://github.com/skeeto/pdjson), a C streaming JSON parser — verified against the original by byte-identical behaviour transcripts.**

| | |
| --- | --- |
| **Migration** | C → Zig (Port Mortem 2026, Track G) |
| **Upstream** | `skeeto/pdjson` @ [`78fe04b`](https://github.com/skeeto/pdjson/commit/78fe04b820dc8817f540bdd87fb22887e0ef3981) (master, 2024-02-22, Unlicense) |
| **Dominant proof** | Two independent programs drive the C original and the Zig port through the same script and emit deterministic NDJSON behaviour transcripts. Equivalence means **byte-identical transcripts**. |
| **Upstream tests** | **18/18** assertions pass, sources unmodified and hash-pinned, linked against only the Zig library |
| **Differential** | **0 divergences** in 4,085 fixed-corpus + 3,498 JSONTestSuite comparisons, across all three input sources |
| **Fuzzing** | 30-minute published session, **0 divergences, 0 crashes, 0 timeouts** |
| **Harness self-test** | **12/12** injected defects caught (its first sound run found 4 real gaps in the corpus) |
| **Safety** | 0 `@constCast`, 0 `unreachable`, 0 force-unwraps, 0 inline asm; 10 pointer casts, each enumerated. Ships **ReleaseSafe** — checks on. |
| **Benchmark** | **Slower on 9 of 12** workload/mode pairs, faster on 3. Full table below, generated from the artifact. |
| **Upstream bugs found** | 3, all filed with minimal reproducers: [#36](https://github.com/skeeto/pdjson/issues/36), [#37](https://github.com/skeeto/pdjson/issues/37), [#38](https://github.com/skeeto/pdjson/issues/38). Two independently confirmed by Valgrind. |

```sh
make verify
```

One command. It pins upstream by hash, builds both implementations, proves the
Zig artifact contains no C parser code, checks the ABI, runs the untouched
upstream suite against Zig, runs the differential corpus, fuzzes, scans for
escape hatches, benchmarks, and validates every claim below against the files it
just generated. It fails on the first thing that does not hold.

- **Demo video:** _placeholder — see [`docs/demo-script.md`](docs/demo-script.md)_
- **Devfolio project:** _placeholder — draft in [`docs/devfolio-submission.md`](docs/devfolio-submission.md)_

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
reported separately, with the sanitizer output attached as evidence. All 43 such
cases in this corpus resolve to a single line, `pdjson.c:912` — which is upstream
bug [#36](https://github.com/skeeto/pdjson/issues/36).

<!-- CLAIMS:BEGIN -->
| # | Claim | Status | Evidence |
| --- | --- | --- | --- |
| C-01 | All 18 assertions in the unmodified upstream test suite pass against the Zig library. | verified | [`artifacts/original-test-report.json`](artifacts/original-test-report.json) |
| C-02 | Zero assertions in the upstream suite are skipped, adapted, or marked unsupported. | verified | [`artifacts/original-test-report.json`](artifacts/original-test-report.json) |
| C-03 | The upstream source tree is byte-identical to commit 78fe04b across all 9 files. | verified | [`artifacts/upstream-manifest.json`](artifacts/upstream-manifest.json) |
| C-04 | Across 4,085 differential comparisons on the fixed corpus, spanning all three documented input sources, the Zig implementation and the pinned C original produce byte-identical behaviour transcripts, with 0 divergences. | verified | [`artifacts/differential-summary.json`](artifacts/differential-summary.json) |
| C-05 | The differential comparison covers 19 drive modes: three input sources crossed with peek, skip, reset, the separator API and strict mode, plus 4 deterministic allocation-failure schedules. | verified | [`artifacts/differential-summary.json`](artifacts/differential-summary.json) |
| C-06 | Differential testing and the oracle determinism gate found three defects in the pinned original, all reported upstream with minimal public-API reproducers: a null dereference and an out-of-bounds read at pdjson.c:912, a 0xFF/EOF confusion in the buffer source, and an uninitialised read in json_get_number. | verified | [`artifacts/upstream-issues.json`](artifacts/upstream-issues.json) |
| C-07 | The memory-buffer source in the pinned original treats byte 0xFF as end-of-input on signed-char targets, disagreeing with its own FILE* source on identical input. | verified | [`artifacts/upstream-issues.json`](artifacts/upstream-issues.json) |
| C-08 | The Zig static library is built only from Zig-produced objects, exports all 22 public symbols from the pinned header, and contains no upstream parser code. | verified | [`artifacts/linkage-report.json`](artifacts/linkage-report.json) |
| C-09 | The Zig declarations and the pinned C header agree on every struct offset, size, alignment and enumerator, with sizeof(struct json_stream) == 272 on this target. | verified | [`artifacts/abi-report.json`](artifacts/abi-report.json) |
| C-10 | A C program that includes the pinned public header and declares struct json_stream by value links against only the Zig archive and passes its checks. | verified | [`artifacts/abi-report.json`](artifacts/abi-report.json) |
| C-11 | The shipped library uses no @constCast, no inline assembly, no @setRuntimeSafety, no unreachable, and no force-unwraps; its 10 pointer casts are confined to the C allocator and char* boundaries and are individually enumerated. | verified | [`artifacts/safety-report.json`](artifacts/safety-report.json) |
| C-12 | The shipped artifact is built in ReleaseSafe, so bounds checks and overflow checks are active at runtime, including in the reported benchmark figures. | verified | [`artifacts/safety-report.json`](artifacts/safety-report.json) |
| C-13 | All 12 deliberately injected defects in the Zig implementation are detected by the fixed-corpus differential, with zero survivors. | verified | [`artifacts/mutation-report.json`](artifacts/mutation-report.json) |
| C-14 | Both transcript producers are deterministic: five runs over every fixture in five modes produce byte-identical output. | verified | [`artifacts/determinism-report.json`](artifacts/determinism-report.json) |
| C-15 | The Zig implementation's json_get_number matches C strtod bit for bit across a 661-point exponent sweep, powers of two, digit strings up to 500 digits, 20,000 randomised decimal lexemes and 20,000 randomised hex floats. | verified | [`artifacts/original-test-report.json`](artifacts/original-test-report.json) |
| C-16 | The Zig implementation is slower than the C original on 9 of the 12 benchmark workload/mode pairs measured, and faster on 3. | verified | [`artifacts/benchmark-summary.json`](artifacts/benchmark-summary.json) |
| C-17 | A published differential fuzz session found zero divergences, zero crashes and zero timeouts. | verified | [`artifacts/verification-report.json`](artifacts/verification-report.json) |
| C-18 | Behavioural equivalence is demonstrated for all three documented input sources: json_open_buffer, json_open_stream (FILE*) and json_open_user. | verified | [`artifacts/differential-summary.json`](artifacts/differential-summary.json) |
| C-19 | On the independent nst/JSONTestSuite conformance corpus, the Zig port and the pinned C original agree on all 318 parsing cases across 11 drive modes and all three input sources, and the original is fully conforming (95/95 must-accept, 188/188 must-reject). | verified | [`artifacts/conformance-report.json`](artifacts/conformance-report.json) |
| C-20 | No divergence has ever been observed on any input where the pinned original is well defined: 4,085 fixed-corpus comparisons plus 3,498 JSONTestSuite comparisons plus a 30-minute 34.6-million-case fuzz session, all at zero. | verified | [`artifacts/differential-jsontestsuite.json`](artifacts/differential-jsontestsuite.json) |
| C-21 | Verification found two real defects in this port -- a hex-float rounding error and an uninitialised read inherited from the original -- both fixed, regression-tested and documented. | verified | [`artifacts/differential-summary.json`](artifacts/differential-summary.json) |
| C-22 | Both transcript producers are deterministic on Linux and macOS: five runs over every fixture in five modes produce byte-identical output. | verified | [`artifacts/determinism-report.json`](artifacts/determinism-report.json) |
| C-23 | The Zig type declarations and the pinned C header describe the same ABI on 6 targets spanning 32- and 64-bit, little-endian ARM, x86, RISC-V and Windows. | verified | [`artifacts/abi-cross-report.json`](artifacts/abi-cross-report.json) |
| C-24 | Valgrind memcheck independently confirms both memory defects in the pinned original and finds no further ones; the upstream test suite itself is clean under memcheck. | verified | [`artifacts/valgrind-report.json`](artifacts/valgrind-report.json) |
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

pdjson is a good C library: ~990 lines, no dependencies, bounded memory, and a
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
                 proved equivalent to it by two probes that emit the same table.
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
over batches of ~400 inputs per process pair. That batching is what makes
~9,000 cases/second possible; per-case process spawning would be ~200× slower.

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

**The first optimization guess was wrong, and measuring is what caught it.**
The initial gap was 0.57×–0.70×. I assumed the null checks the port adds on the
source function pointers were the cost, built a variant without them, and
measured **0%** improvement. Leaf-weighted profiles of both binaries then showed
the real causes: clang inlines `pushchar` into `read_digits` while Zig kept it
as a separate call (16.6% + 18.2% against C's combined 23%), and the `strchr`
calls in the number lexer were being served by a generic slice search. Splitting
`pushchar` into an inline fast path and specialising those two comparisons moved
`large-mixed` from 0.70× to 0.87× and `flat-ints` from 0.67× to 0.80×.

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
| `tools/` | Zig counterparts, plus the ABI probe. |
| `tests/original/` | A C consumer using the pinned header. Upstream's own tests are used in place. |
| `tests/port/` | Zig-native tests: behaviour, number torture, allocator failure, regressions. |
| `tests/conformance/fixtures/` | 215 generated edge-case inputs. |
| `tests/upstream-bugs/` | Minimal reproducers for #36 and #37. |
| `fuzz/` | Fuzzer, corpus, minimized findings, session logs. |
| `bench/` | Workloads, methodology, raw results. |
| `scripts/` | Provenance, differential, mutation, safety, claims, reports. |
| `artifacts/` | Everything `make verify` generates. |
| `docs/` | Assessment, schema, bug analyses, write-up, demo script, audit. |

## Known limitations

Stated here rather than left to be discovered.

- **ABI equivalence is verified on two targets**, arm64 macOS and x86-64 Linux,
  not asserted universally. Three of the four findings in the final audit were
  platform-specific and invisible on the development machine, so a third target
  would likely find a fourth thing.
- **`nan(...)` payloads that overflow 64 bits are not matched.** C99 §7.20.1.3p4
  makes them implementation-defined and libcs disagree. Reachable only by calling
  `json_get_number()` on a *string* token beginning `nan(`. ([D-09](DECISIONS.md))
- **Zig is slower than C** on 11 of 12 workloads, and part of the remaining gap
  is unexplained.
- **The two upstream issues are filed, not triaged.** No maintainer has confirmed
  them yet, and the ledger says so.
- **Equivalence is demonstrated, not proven.** 3,500+ compared cases and 25
  minutes of fuzzing is evidence, not a proof of behavioural equality.

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
