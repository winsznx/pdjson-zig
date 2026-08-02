# Devfolio submission

## Status: prepared, not submitted — blocked on MCP authentication

The Devfolio MCP connection was **not available** during this build. `ToolSearch`
returns no Devfolio tools, and the session reports the server as requiring an
OAuth flow that cannot run in a non-interactive session.

This means two things, both stated plainly rather than worked around:

1. **The live submission form was never inspected.** The field names and limits
   below are a best-effort reconstruction from how Devfolio hackathon forms are
   normally structured, not a transcript of the real one. Field names may differ.
2. **Nothing has been submitted.** No draft was created, and no project exists on
   Devfolio for this entry yet.

Everything below is copy that is ready to paste, and every number in it comes
from an artifact that `make verify` regenerates. The remaining manual steps are
listed at the end.

---

## Project name

```
pdjson-zig
```

## Tagline

```
A Zig rewrite of a C JSON parser, proven equivalent by byte-identical behaviour transcripts — and it found two bugs in the original.
```

## The problem

```
pdjson is a good C library: ~990 lines, no dependencies, bounded memory, and a
streaming API that parses arbitrarily large documents in space proportional to
the largest token. It is also a parser for untrusted input written in a language
with no bounds checking, and it exposes a raw struct containing a heap pointer
and two indices that must stay consistent.

Both bugs this project found in the original are instances of that second
property: an allocation fails, two fields fall out of sync, and a public accessor
reads memory that was never allocated.

The harder problem, though, is not rewriting a JSON parser. It is proving that
your rewrite behaves the same. "The tests pass" is a sample, not a proof —
especially when the upstream suite is 18 assertions over 990 lines and never
touches byte positions, depth, json_skip, json_get_number, the allocator hooks,
invalid UTF-8, or any allocation-failure path.
```

## Why this migration

```
pdjson has a property that makes rigorous verification possible rather than
aspirational: it is completely observable through its public header. Every piece
of state a caller can reach — the event, the token bytes, the number, the
diagnostic string, the line number, the byte position, the depth, the container
context — has an accessor. Parsing is deterministic. There is no hidden state.

That means a behaviour transcript, recording every observable value after every
operation, is not a sample of the behaviour. It is the behaviour. Two
implementations producing byte-identical transcripts on an input are
indistinguishable on that input through the library's own interface.

Zig is a good target because it keeps what makes pdjson worth using — manual
memory control, C ABI compatibility, no runtime, no hidden allocation — while
turning the class of defect that produced upstream issue #36 from a silent
out-of-bounds read into a checked operation. The shipped library is built
ReleaseSafe, so bounds and overflow checks are active at runtime, and the
benchmark shows that costs about 2%.
```

## Source repository

```
https://github.com/skeeto/pdjson
Pinned at commit 78fe04b820dc8817f540bdd87fb22887e0ef3981 (master, 2024-02-22)
License: Unlicense (public domain)
```

## Source and target languages

```
C99 -> Zig 0.16.0
Track G
```

## Behavioural equivalence approach

```
Two independent programs drive the two implementations through the same script
and emit deterministic NDJSON behaviour transcripts. oracle/transcript_c.c links
the pinned upstream pdjson.c; tools/transcript_zig.zig uses the Zig library.
Neither shares a formatter with the other, deliberately — a shared emitter could
normalise a real difference away on both sides at once.

Each record captures everything reachable through pdjson.h: event, token bytes
(hex, because tokens legitimately contain NUL and invalid UTF-8), the number as
an IEEE-754 bit pattern (so negative zero and NaN payloads stay visible), line,
byte position, depth, container context, and the diagnostic string. Equivalence
on an input means the transcripts are byte-identical.

Every input runs through nine drive modes, because the same bytes exercise
different code depending on how the caller drives the parser: the plain event
loop, strict non-streaming mode, peek-before-next, json_skip, the README's
separator loop via json_source_get, and four deterministic allocation-failure
schedules.

Fault attribution is mechanical, not editorial. When the two disagree, the
harness re-runs the case against an ASan+UBSan build of the pinned original. If
the sanitizer reports an error, the case is classified as upstream undefined
behaviour, counted separately, and the sanitizer output is attached as evidence.
If it stays clean, it is a divergence and fails the run. That distinction is what
keeps "0 divergences" from quietly absorbing cases where the original is simply
broken.

Results:
  - 18/18 assertions in the UNMODIFIED upstream test suite, compiled in place
    from the pinned tree and linked against only the Zig static library
  - 0 divergences in 1,935 fixed-corpus comparisons (215 inputs x 9 modes)
  - 0 divergences in 1,590 JSONTestSuite comparisons (318 cases x 5 modes)
  - 43 cases where the pinned original invokes undefined behaviour, every one
    sanitizer-confirmed and all resolving to a single line, pdjson.c:912
```

## Differential fuzzing evidence

```
fuzz/fuzz.py mutates a seed corpus and generates grammar-based JSON plus number-
and Unicode-boundary cases, running both implementations over batches of ~400
inputs per process pair — batching is what makes ~9,000 cases/second possible.
Findings are isolated to a single input, minimized by delta debugging, and
written to fuzz/minimized/ with both transcripts.

The published session's exact seed, duration, case count, rate, and mode list are
recorded in fuzz/logs/session-published.json. Zero divergences, zero crashes,
zero timeouts.

Separately, the harness itself is tested. scripts/mutation-test.py injects twelve
deliberate defects into the Zig implementation — wrong escape mapping, off-by-one
surrogate range, dropped NUL terminator, uncounted newline, altered diagnostic —
and requires the differential to catch each one. It currently catches 12/12.

That number is only meaningful because two earlier runs produced a FALSE 12/12,
both caught and both documented in DECISIONS.md D-17: they were "catching"
mutants on cases where the C original crashes or reads out of bounds, so every
mutant differed for reasons unrelated to the mutation. And the first sound run
caught only 8/12 — the four survivors were real gaps in the corpus, which grew
from 142 to 214 fixtures to close them.
```

## Benchmark evidence

```
The Zig port is SLOWER than the C original on 9 of 12 workload/mode pairs, and
faster on 3. This is reported as a result rather than buried.

Ratios are C median / Zig median, so below 1.00 means Zig is slower. Most
workloads land between 0.79 and 0.93. Full table, raw per-iteration samples, and
methodology are in the repository; the README table is generated from the
artifact so it cannot drift.

Fairness controls, all enforced in code rather than asserted: identical workload
files, identical parse loop, identical counting allocator, identical warm-up
(1 cold + 4 warm iterations, unrecorded), binaries interleaved within each
repetition, the same CLOCK_MONOTONIC source on both sides, and every raw sample
committed. Both Zig optimisation modes are reported — ReleaseSafe (what ships)
and ReleaseFast (like-for-like against C -O2).

Two findings worth stating. ReleaseSafe and ReleaseFast are within ~2% of each
other, so the gap is not the cost of safety checks. And my first explanation for
the gap was wrong: I assumed the null checks the port adds on the source
callbacks were the cost, removed them, and measured 0% improvement. Profiling
both binaries showed the real cause — clang inlines a byte-append helper that Zig
kept out of line — and fixing it moved large-mixed from 0.70x to 0.87x. The
remainder is unexplained and reported as such, rather than optimised against one
benchmark.
```

## Bugs found in the original

```
Both were found by the verification pipeline, not by reading the code. Both have
minimal public-API reproducers, were confirmed against the pinned commit before
filing, and were checked against all open and closed issues and PRs for
duplication.

1. https://github.com/skeeto/pdjson/issues/36
   json_get_context() reads an unallocated stack slot after a failed allocation.
   push() increments stack_top BEFORE growing the container stack, so a failed
   realloc leaves it pointing at a slot that does not exist — and on the first
   push, into a NULL stack. Manifests as a SEGV or a heap-buffer-overflow
   depending on when the allocator gives out. Reachable through the documented
   json_set_allocator API. Confirmed by ASan and UBSan.

2. https://github.com/skeeto/pdjson/issues/37
   Byte 0xFF is read as end-of-input by the memory-buffer source. buffer_peek()
   reads through a const char*, so on signed-char targets 0xFF becomes -1, which
   is EOF. The FILE* source uses fgetc and does not have this problem, so the
   library's two documented input sources disagree on identical bytes;
   json_get_position() freezes; and json_source_get() can never advance past the
   byte. 0xFF is never valid UTF-8, so this does not make the parser accept bad
   input — it misreports where and why the input was rejected.

The port reproduces bug 2 deliberately, using Zig's c_char so it makes the same
signedness choice the C compiler makes on the same target. Silently fixing a bug
would break the equivalence claim in the least visible way possible. The fix
ships as an opt-in build flag, and that build is expected to diverge — it is one
of the twelve mutants.

Neither issue has been triaged by upstream at the time of writing, and the claim
ledger records them as reported rather than confirmed.
```

## GitHub repository

```
https://github.com/winsznx/pdjson-zig
```

## Build / verification command

```
make verify
```

```
One command, no network required — upstream is committed and hash-pinned. It
verifies the pin, builds both implementations, proves the Zig artifact contains
no C parser code, checks the C ABI both structurally and by linking a C consumer
against the archive, runs the untouched upstream suite against Zig, runs the
differential corpus, fuzzes, scans for escape hatches, benchmarks, and validates
every published claim against the artifacts it just generated. It fails on the
first thing that does not hold.

Also available: make docker-verify, for a pinned container on a different target.
```

## Decisions document

```
https://github.com/winsznx/pdjson-zig/blob/main/DECISIONS.md

17 entries covering the choices that actually shaped the result: why the struct
layout is pinned rather than made opaque, why strtod is reimplemented, why one
upstream bug is reproduced and another is not, why counters use wrapping
arithmetic, and why the verification harness's own first results were wrong
twice.
```

## Limitations

```
Stated rather than left to be discovered:

- The differential corpus drives json_open_buffer only. json_open_stream (FILE*)
  and json_open_user are implemented, exported, and exercised by the upstream
  suite and the ABI consumer, but are not compared transcript by transcript.
  Given that bug #37 is precisely a disagreement between two sources, this is the
  hole most likely to hold something, and it is the obvious next step.
- ABI equivalence is verified on two targets (arm64 macOS, x86-64 Linux), not
  asserted universally.
- nan(...) payloads that overflow 64 bits are not matched. C99 7.20.1.3p4 makes
  them implementation-defined and libcs disagree. Reachable only by calling
  json_get_number() on a string token beginning "nan(".
- The Zig port is slower than C on 9 of 12 workloads, and part of the remaining
  gap is unexplained.
- The two upstream issues are filed, not triaged.
- This is demonstrated equivalence, not proven equivalence. ~3,500 compared cases
  plus a fuzz session is evidence, not a proof of behavioural equality.
```

## Demo video

```
<pending — see docs/demo-script.md for the script and recording checklist>
```

Field copy once recorded:

```
Five-minute walkthrough: the falsifiable claim, provenance checks, the untouched
upstream suite running against the Zig library, byte-identical behaviour
transcripts on Unicode and embedded-NUL cases, the fuzz session, mutation testing
of the harness itself, the two upstream bugs found (including a
sanitizer-confirmed null dereference), and honest benchmark results showing the
port is slower.
```

## Screenshots to attach

Only real terminal output and generated reports. No mockups.

1. `make verify` completing, showing all 16 steps and `VERIFY OK`.
2. `upstream/pdjson/tests/tests.c` compiled against `libpdjson.a`, showing
   `18 pass, 0 fail`.
3. Two transcripts side by side for `uni-escaped-pair-max.json`, identical.
4. `scripts/verify-upstream-hashes.sh` failing on a tampered test file, then
   passing after `git checkout`.
5. The ASan/UBSan output from `repro_oom_stack.c` at `pdjson.c:912`.
6. `artifacts/mutation-report.json` showing 12/12 with the excluded-case count.
7. The generated benchmark table, showing the port is slower.

---

## Remaining manual steps

Because the MCP connection was unavailable, these must be done by hand in a
browser or an authenticated session:

1. **Authorize the Devfolio MCP server** (`claude mcp` or `/mcp` in an
   interactive session), or open Devfolio directly.
2. **Inspect the actual Port Mortem 2026 submission form** and record its real
   fields and limits in this file, replacing the reconstruction above.
3. **Create the project entry** and paste the copy above into the matching
   fields. Do not invent values for fields not covered here; if a required field
   has no verified answer, say so in the submission rather than filling it.
4. **Record the demo video** per `docs/demo-script.md`, upload it, and paste the
   URL into both the Devfolio video field and the README placeholder.
5. **Attach the screenshots** listed above.
6. **Confirm the release gate passes** (`make release-gate`) and CI is green
   before final submission.
7. **Paste the Devfolio project URL** into the README placeholder and into
   `docs/final-status.md`.

Nothing in the copy above should be edited to sound stronger. Every figure is
checked against an artifact by `scripts/validate-claims.py`, and claims not
marked `verified` in `CLAIMS.json` are not permitted on Devfolio at all — the
validator enforces that.
