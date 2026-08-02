# Porting a JSON parser and proving you did it right

*A write-up of pdjson-zig — a Zig rewrite of skeeto/pdjson, and the verification
pipeline that was most of the actual work.*

---

## Why pdjson

Most C libraries are bad port candidates for a proof-first project, not because
they are hard to rewrite but because they are hard to *check*. If a library's
behaviour depends on the filesystem, on time, on a network, or on internal state
you cannot observe, then "equivalent" degrades into "passes the same tests" —
and tests are a sample, not a proof.

pdjson has an unusual property: it is **completely observable through its public
header**. Every scrap of state a caller can reach — the event, the token bytes,
the number, the diagnostic string, the line, the byte position, the depth, the
container context — is exposed by an accessor. Parsing is deterministic. There
is no hidden state that could differ without being visible.

That means a *behaviour transcript* — a record of every observable value after
every operation — is not a sample. If two implementations produce byte-identical
transcripts on an input, they are indistinguishable on that input through the
library's own interface. That is a much stronger statement than "the tests pass",
and it is what the whole project is built around.

It is also 990 lines, dependency-free, public domain, and a parser for untrusted
input written in a language with no bounds checking. The migration case writes
itself.

## Parser ports are deceptively difficult

The state machine is the easy part. A JSON parser is a weekend exercise. What
takes the time is that a *port* is not judged on being a good parser; it is
judged on being **the same parser**, and "the same" turns out to have a lot of
edges.

Some of the ones that bit:

**`json_get_string` returns a length that includes the trailing NUL.** For the
string `"v"` it reports 2. Every reasonable instinct says 1. Getting this wrong
would pass most tests and fail the corpus.

**Diagnostics are a public field.** `errmsg` lives inside `struct json_stream`,
so the exact bytes are observable. `printf`'s `%c` converts through
`unsigned char`, so byte `0xE9` appears raw in the message. And `%c` with the
argument `0` writes an actual NUL — so for the input `"\x00"`, the message a C
caller sees is `unexpected byte '`, truncated mid-sentence. That is not a bug to
be tidied up; it is the behaviour, and the port reproduces it.

**`strchr` matches the terminating NUL.** The number lexer does
`strchr(".eE", c)` where `c` came from a peek. If `c` is 0 — an embedded NUL
after a number — `strchr` returns a pointer to the string's own terminator, which
is non-null, so the control flow takes the "there is a fraction or exponent"
branch. It happens to reach the same event anyway. Replacing it with a slice
search silently drops that, and the fixture `nul-after-number` catches it.

**`json_reset` does not reset everything.** It clears the container stack, the
token counter and the error flag — but deliberately not the buffered peek and not
the token buffer. Both are observable afterwards.

**`json_peek` advances the input.** It calls `json_next` underneath, so
`json_get_position` moves. That is surprising enough that it is an open upstream
issue (#15). It is also behaviour someone may depend on, so it is preserved.

None of these are hard once you know about them. The problem is that you do not
know about them, and no amount of careful reading reliably finds them all. Which
is the argument for the oracle.

## How the transcript oracle works

Two programs. `oracle/transcript_c.c` links the pinned `pdjson.c`.
`tools/transcript_zig.zig` uses the Zig library. Both drive their parser through
the same script and emit one NDJSON record per operation:

```json
{"seq":3,"op":"next","event":"NUMBER","tok":"3100","toklen":2,
 "num":"3ff0000000000000","line":1,"pos":7,"depth":2,
 "ctx":"ARRAY","ctxn":1,"err":null}
```

Equivalence on an input means the two files are byte-identical.

Three encoding decisions mattered more than expected:

**Tokens are hex.** They legitimately contain NUL, invalid UTF-8, and control
bytes — none of which survive being placed inside a JSON string. Hex is lossless
and comparison-friendly.

**Numbers are IEEE-754 bit patterns, not decimal.** `%.17g` would collapse `-0.0`
and `0.0`, and would hide NaN payloads. Sixteen hex digits does not.

**The two emitters are deliberately separate implementations.** A shared
formatter linked into both would be less code and would be a mistake: a bug in
shared normalisation would cancel out on both sides and hide a real difference.

And one thing deliberately *not* recorded: the bytes of `errmsg` past its NUL
terminator. `snprintf` writes the message and stops, leaving the rest of the
128-byte field untouched — and since callers declare `json_stream` on the stack,
those bytes are whatever was there before. Comparing them would compare
uninitialised stack memory and produce divergences that mean nothing.

Then each input runs through **nine drive modes**, because the same bytes
exercise different code depending on how the caller drives the parser: the plain
event loop, strict mode, peek-before-next, `json_skip`, the README's separator
loop via `json_source_get`, and four deterministic allocation-failure schedules.

That last category is where things got interesting.

## The hardest divergence: when "different" means "the original is wrong"

The first full differential run reported 8 divergences and 32 crashes.

The crashes were all on the same side: the **C original** was dying, and the Zig
port was not. Under ASan:

```
SUMMARY: AddressSanitizer: SEGV pdjson.c:912 in json_get_context
```

`push()` increments `stack_top` *before* it grows the container stack. When the
allocation fails it reports `"out of memory"` and returns `JSON_ERROR` — but
leaves `stack_top` pointing at a slot that was never allocated, and on the very
first push, into a stack that is still `NULL`. `json_get_context()` then indexes
it with only a `stack_top == (size_t)-1` guard, which the failed push has already
moved past.

The eight non-crashing divergences were the same bug wearing a different hat.
With one allocation permitted, the stack holds four frames and the fifth push
fails, so `stack_top == 4 == stack_size` and the read is one element past the
allocation. No crash — just whatever bytes follow, reported as a container type
and count that were never parsed. My port read the same out-of-bounds slot and
found different garbage. That is what the "divergence" was.

This is the interesting part of the project, because at that moment there are
three tempting moves and two of them are dishonest:

1. Reproduce the out-of-bounds read. Impossible — there is no behaviour to match,
   only undefined behaviour to imitate.
2. Quietly add the bounds check and let the case fall off the report. This is the
   dishonest one, and it is *easy*, because the number goes to zero and everyone
   is happy.
3. Add the bounds check, and account for the cases separately with evidence.

The third requires deciding which side is at fault — and "that one is upstream's
fault" is exactly where a port deceives itself. So the decision was made
mechanical: when the two implementations disagree, the harness **re-runs the case
against an ASan+UBSan build of the pinned original**. If the sanitizer fires,
the case is classified `upstream_ub`, the sanitizer output is attached to the
report as evidence, and it is counted separately from divergences. If it stays
clean, it is a divergence and it fails the run.

All 43 anomalies resolved to one line. The port routes every stack access through
one bounds-checked accessor, `json_get_depth()` still returns exactly what the
original returns because *that* read is well defined, and the bug became upstream
issue [#36](https://github.com/skeeto/pdjson/issues/36).

The headline "0 divergences" figure therefore means something narrower and more
defensible than it might: zero differences **on inputs where the original has
defined behaviour**, with the exclusions enumerated and evidenced rather than
asserted.

## An architectural decision that changed

The plan was for `json_stream` to be opaque — an internal allocation behind a
handle, which is the obvious Zig design and much cleaner.

That collapsed within an hour of reading the header. `pdjson.h` spells out every
field, and upstream's own tests declare the struct **by value on the stack**:

```c
struct json_stream json[1];
```

So the layout is not an implementation detail; it is part of the contract. An
opaque handle would break every existing caller, and `json_open_*` returns `void`
so there is nowhere to report an allocation failure anyway.

The port therefore reproduces the layout exactly — 272 bytes, alignment 8, every
field at the offset the header dictates. But rather than trusting that, two
probes emit the same layout table (one asking the C compiler what the pinned
header means, one computing it from the Zig declarations) and the build diffs
them field by field. A `tests/original/abi_consumer.c` then includes the *pinned*
header, declares the struct by value, links against only the Zig archive, and
exercises the API for real.

The upside of the constraint: `struct json_stream` really is the natural state
for this parser — a container stack, a token buffer, a source, an allocator, a
message. Being forced into it produced a more faithful port than the design I
would have chosen.

## Unicode and the signedness trap

The Unicode handling ported cleanly. Surrogate pairs, overlong encodings, the
`0xED`/`0xF0`/`0xF4` continuation-byte special cases — all mechanical once the
table is transcribed, and all covered by fixtures.

The interesting Unicode-adjacent bug was not in the decoder at all. It was here:

```c
static int buffer_peek(struct json_source *source)
{
    if (source->position < source->source.buffer.length)
        return source->source.buffer.buffer[source->position];
```

`buffer` is a `const char *`. Whether `char` is signed is implementation-defined,
and on x86-64 Linux, macOS, Windows and Apple arm64 it is. So the byte `0xFF`
widens to `-1`, which is `EOF`.

The `FILE *` source uses `fgetc`, which is specified to return an `unsigned char`
converted to `int`. So the **two documented input sources disagree on identical
bytes**:

```
buffer source : event=1 position=1 error=unterminated string literal
FILE*  source : event=1 position=2 error=invalid UTF-8 character
```

Worse, `buffer_get` only advances `position` when the byte is not `EOF`, so
`json_get_position()` freezes — and `json_source_get()`, the API the README
documents for inspecting separators between streamed values, can never consume
the byte. Five calls, no progress.

`0xFF` is never valid UTF-8, so this does not make the parser accept bad input.
It misreports where and why the input was rejected, and it breaks one documented
accessor. That became issue [#37](https://github.com/skeeto/pdjson/issues/37).

What to do about it in the port is the mirror of the earlier decision. Here the
original's behaviour is *well defined* — just wrong — so there is something to be
equivalent to, and silently fixing it would break the claim in the least visible
way possible. The port reproduces it, portably, using Zig's `c_char` so it makes
the same signedness choice the C compiler makes on the same target. The fix ships
as `-Dfix-0xff=true`, and that build is *expected* to diverge — it is one of the
twelve mutants used to prove the harness has teeth.

## The harness that lied to me twice

Zero divergences is a suspicious result. A comparison harness that never fails
and a comparison harness that cannot fail look identical from the outside.

So: mutation testing. Inject a deliberate defect into the Zig implementation —
wrong escape mapping, off-by-one surrogate range, dropped NUL terminator,
uncounted newline — rebuild, and require the differential to catch it.

The first sound run caught **8 of 12**. The four survivors were real gaps: no
fixture had an escaped surrogate pair at the top of the range, none had a raw
control byte at the `0x1F` boundary, and allocation-failure diagnostics were not
covered at all. The corpus grew from 142 to 214 fixtures (215 today, after a later fuzz finding).

But before that run, there were two that scored a false 12/12.

**The first** "caught" every mutant on the same case, `arr-close-mismatch.json`
in mode `oom:0`. Which should have been obviously wrong: twelve unrelated defects
do not all first manifest on the same input. They did not. The C oracle *crashes*
there — the upstream bug — so its transcript was truncated, and every mutant
differed from a truncated transcript. The harness was detecting upstream's bug
and crediting itself.

**The second** excluded crashing cases, and still scored 12/12 with several
caught on `deep-array-32` in mode `oom:2`. Same trap one level down: in that mode
the original does not crash, it reads *out of bounds* and keeps going, emitting
bytes from unallocated memory that every mutant also differed from.

The fix is the same mechanism as the divergence classifier: a case is comparable
only if an ASan+UBSan build of the original reports nothing on it. With that in
place, each mutant is caught by a fixture that is actually related to it — the
surrogate-range mutant by `uni-escaped-pair-hi-end`, the control-character mutant
by `ctrl-raw-1f`, the NUL-terminator mutant by `nul-after-number`.

Two of the twelve mutants also turned out to be **equivalent** — an off-by-one in
the diagnostic truncation limit is unobservable, because the longest message the
library can emit is 62 bytes and the buffer is 128. That is a fact about the code,
not a gap in the corpus, and it was replaced with a mutant that crosses a
reachable boundary.

If there is one thing to take from this project, it is that: **the verification
tooling needs verifying, and its failure mode is to flatter you.**

## Benchmark surprises

Two, both instructive.

**The first benchmark said the port was 6.8× slower.** It was measuring a Debug
build. Zig's `standardOptimizeOption` returns Debug unless `--release` is passed,
and a bare `zig build` had been silently producing an unoptimised library. The
build now defaults to ReleaseSafe, because the mode that ships is the mode that
should be measured — and it means the published figures include bounds and
overflow checking.

**The second: my explanation for the remaining gap was wrong.** The port adds a
null check on each source function pointer that C does not have, and that is
obviously the cost, and it is per byte, and it is in the hottest loop. I built a
variant without them and measured the improvement at **0%**.

Profiling both binaries showed the real causes. C's profile had no `pushchar`
frame at all — clang inlined it into `read_digits` (23%). Zig's had `readDigits`
at 16.6% *and* `pushchar` at 18.2%, as separate calls. And C was spending 7% in
`memchr` for `strchr(".eE", c)`, which the port was serving with a generic slice
search. Splitting `pushchar` into an inline fast path and specialising those two
comparisons moved `large-mixed` from 0.70× to 0.87×.

The port is still slower on 9 of 12 workloads. The remaining gap is not
explained, and is reported that way rather than chased, because tuning further
against this specific workload set is how a benchmark stops describing anything.

One genuinely pleasant result: ReleaseSafe and ReleaseFast land within ~2% of
each other. The safety checks are close to free here.

## What remains imperfect

- **~~The differential corpus drives `json_open_buffer` only.~~** Closed. All
  three sources — buffer, `FILE *`, and user callbacks — are now compared
  transcript by transcript, 4,085 comparisons, zero divergences. This was the
  largest hole, and it mattered specifically because the `0xFF` bug *is* a
  disagreement between two sources.
- **ABI equivalence is verified on two targets**, not asserted universally.
- **`nan(...)` payloads that overflow 64 bits are not matched**, because C99
  makes them implementation-defined and libcs disagree.
- **The two upstream issues are filed, not triaged.** No maintainer has confirmed
  them, and the claim ledger says "reported", not "confirmed".
- **The port is slower**, and part of why is unknown.
- **Equivalence is demonstrated, not proven.** ~3,500 compared cases and 25
  minutes of fuzzing is evidence. It is not a proof of behavioural equality, and
  calling it one would undo the point of the exercise.

## Reproducing all of it

```sh
git clone <repo> && cd pdjson-zig
make verify
```

No network needed — upstream is committed and hash-pinned. The pipeline verifies
the pin, builds both implementations, proves the Zig artifact contains no C
parser code, checks the ABI both structurally and by linking a C consumer, runs
the untouched upstream suite against Zig, runs the differential corpus, fuzzes,
scans for escape hatches, benchmarks, and validates every published claim against
the artifacts it just produced.

The parts worth checking yourself, because they are the ones easiest to fake:

```sh
# The upstream tests really are untouched — break one and watch it fail.
echo "/* x */" >> upstream/pdjson/tests/tests.c
sh scripts/verify-upstream-hashes.sh
git checkout upstream/pdjson/tests/tests.c

# The Zig library really contains no C parser.
ar t zig-out/lib/libpdjson.a
nm -g zig-out/lib/libpdjson.a | grep -c ' T _\?json_'

# The harness would actually notice a defect.
make mutation

# The upstream bugs are real.
cc -fsanitize=address,undefined -I upstream/pdjson \
   -o /tmp/r tests/upstream-bugs/repro_oom_stack.c upstream/pdjson/pdjson.c && /tmp/r
```

The decisions, including the ones that changed and the ones that were wrong
first, are in [`DECISIONS.md`](../DECISIONS.md).
