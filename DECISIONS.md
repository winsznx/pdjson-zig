# Decisions

Every non-obvious choice in this port, with what it cost and how it was checked.

Entries are ordered roughly by how much they shaped the result. Each states the
original behaviour, what was chosen instead, what else was considered, the
compatibility and performance impact, and the artifact that verifies it.

A recurring theme: the goal was **behavioural equivalence, not structural
imitation**. Where the two conflicted, behaviour won — including in the two
places where the original's behaviour is a bug.

---

## D-01 — Upstream is a committed verbatim copy, not a submodule

**Original architecture.** `skeeto/pdjson` is a separate repository with its own
history.

**Chosen design.** `upstream/pdjson/` holds the nine files from commit
`78fe04b` byte for byte, with `.git` removed. `artifacts/upstream-manifest.json`
records a SHA-256 for each, and `scripts/verify-upstream-hashes.sh` fails on any
drift, any added file, and any deletion.

**Alternatives considered.**
*Git submodule* — smaller repository, but a judge cloning without
`--recurse-submodules` gets an empty directory, and verification then needs the
network. *Subtree merge* — drags upstream history into this repository, which the
brief rules out. *Clone script only* — nothing to hash if GitHub is unreachable
or the branch moves.

**Reason.** The evidence has to be checkable offline, from the archive alone. A
committed copy plus a hash manifest makes "we did not touch the original tests"
a mechanically verified statement rather than a promise, and
`scripts/fetch-upstream.sh` still re-clones at the pinned commit and diffs, so
provenance can be re-established independently.

**Compatibility impact.** None.
**Performance impact.** None.
**Verification.** `sh scripts/verify-upstream-hashes.sh` — 9 files. Negative
tests: modifying a byte, and adding a file, both fail the gate.

---

## D-02 — `struct json_stream` is reproduced field for field, not made opaque

**Original architecture.** `pdjson.h` spells out every field of `json_stream`,
and the upstream tests declare it **by value on the stack**
(`struct json_stream json[1];`).

**Chosen design.** `src/abi.zig` declares the same layout as Zig `extern struct`s,
written independently from the header rather than machine-translated. Two probes
emit a layout table — `tools/abi_probe_c.c` from the C compiler's reading of the
pinned header, `tools/abi_probe.zig` from the Zig declarations — and
`scripts/abi-check.sh` diffs them field by field.

**Alternatives considered.**
*Opaque handle with an internal allocation* — much cleaner Zig, but breaks every
existing caller, including upstream's own tests, and `json_open_*` returns
`void`, so an allocation failure could not be reported. *`@cImport` of the
header* — would work, but makes the port a translation of the header rather than
an independent implementation, and produces less idiomatic Zig.

**Reason.** A drop-in replacement whose struct layout drifts corrupts callers
silently. Deriving both tables from the same header on each side means the check
tracks the platform's C ABI instead of hard-coding one target's answer.

**Compatibility impact.** `sizeof(struct json_stream) == 272`, alignment 8,
`enum json_type` 4 bytes unsigned — identical on both sides, every offset
matching. `include/pdjson.h` is byte-identical to upstream's
(`sha256:724f8ad9…dac6`).
**Performance impact.** None; it is the same state any implementation needs.
**Verification.** `artifacts/abi-report.json`. Beyond the table,
`tests/original/abi_consumer.c` includes the *pinned* header, declares the struct
by value, links against only `libpdjson.a`, and exercises events, depth, context,
number values, error text, line numbers, streaming, reset and skip.

---

## D-03 — `strtod` is implemented here, not taken from libc

**Original behaviour.** `json_get_number()` is `strtod(json->data.string, NULL)`.

**Chosen design.** `src/strtod.zig` implements the C grammar — leading
whitespace, sign, `inf`/`infinity`, `nan(...)`, hex floats, and the "longest
valid prefix, otherwise return 0" rule — and delegates the digits-to-double step
to `std.fmt.parseFloat`, which is correctly rounded.

**Alternatives considered.** *Call libc `strtod`* — free bit-exact parity, and
tempting. Rejected because it inherits upstream issue #27: libc's `strtod`
honours `LC_NUMERIC`, so under `sv_SE.UTF-8` it stops at the `.` and reads
`123.45` as `123`. That bug is still open upstream. *Handle only JSON number
syntax* — wrong, because `json_get_number()` can be called after a `JSON_STRING`
event, which makes the full C grammar observable.

**Reason.** Owning the conversion makes the port locale-independent by
construction. Owning it also means owning its correctness, which is why it is
tested against libc as an oracle rather than assumed.

**Compatibility impact.** Bit-identical to libc `strtod` on everything tested,
with one documented exception (D-09).
**Performance impact.** Not measurable at the workload level; `json_get_number`
is called once per number token and is not the hot path.
**Verification.** `tests/port/number_torture.zig` compares raw f64 bit patterns
against libc `strtod` over: 60 hand-picked lexemes, a 661-point exponent sweep
across the whole double range, powers of two and their neighbours, long digit
strings up to 500 digits, and 20,000 random lexemes drawn from `0-9.eE+-`. Bit
comparison rather than `==`, so `-0.0` and NaN are not silently accepted.

---

## D-04 — Diagnostics are composed, not formatted through a printf interpreter

**Original behaviour.** Every message is built with
`snprintf(json->errmsg, sizeof(json->errmsg), fmt, ...)`. `errmsg` is a public
field, so the bytes are observable.

**Chosen design.** `src/errmsg.zig` provides three primitives matching the only
conversions the original uses — `%s`, `%c`, `%0Nlx` — plus snprintf's truncation
rule. Each call site composes its message explicitly.

**Alternatives considered.** *Zig's `std.fmt`* — different padding and escaping
rules, so messages would not match byte for byte. *A runtime printf
interpreter* — more code, more to get wrong, and no benefit for a fixed set of
messages.

**Reason.** Two details are easy to lose and both are observable. `%c` converts
its `int` argument to `unsigned char`, so byte `0xE9` appears in the message as
the raw byte `0xE9`. And `%c` with argument `0` writes an actual NUL, so input
`"\x00"` really does produce the visible message `unexpected byte '` — truncated
mid-sentence, because `json_get_error()` returns a `char *`.

**Compatibility impact.** Byte-identical messages, including the NUL truncation.
**Performance impact.** Faster than `snprintf`; only on error paths.
**Verification.** Unit tests in `src/errmsg.zig`; every message text asserted in
`tests/port/behaviour.zig`; the NUL truncation pinned in
`tests/port/regressions.zig`; and the whole set compared byte for byte against
the C oracle across the corpus. The mutant `oom-message-typo` confirms a
one-character change is caught.

---

## D-05 — The `char`-signedness bug is reproduced, portably

**Original behaviour.** `buffer_peek()` reads through a `const char *`. Where
`char` is signed, byte `0xFF` becomes `-1`, which is `EOF`. Full analysis in
[docs/upstream-bug-0xff.md](docs/upstream-bug-0xff.md).

**Chosen design.** Reproduce it, using `c_char` so the port makes the same
choice the C compiler makes on the same target:

```zig
fn byteAsC(byte: u8) c_int {
    if (fix_0xff) return byte;
    return @as(c_char, @bitCast(byte));
}
```

**Alternatives considered.** *Fix it silently* — breaks the equivalence claim in
the least visible way possible. *Hard-code signed* — correct on x86-64 and Apple
arm64, wrong on Linux arm64 where `char` is unsigned, so the port would diverge
from the C it is being compared against.

**Reason.** The claim is equivalence with the pinned original. A port that
quietly disagrees with its reference is not a port. The bug is instead reported
upstream and made available as an opt-in fix (D-07).

**Compatibility impact.** Matches the original on every target, including ones
where the bug does not manifest.
**Performance impact.** None.
**Verification.** Fixtures `ff-bare`, `ff-in-string`, `ff-after-number`,
`ff-in-array`, `ff-run`, and `fe-bare` as a control;
`tests/port/regressions.zig` asserts the platform-appropriate message.

---

## D-06 — Container-stack access is bounds-checked, diverging from the original's UB

**Original behaviour.** `json_get_context()` indexes `json->stack[json->stack_top]`
with only a `stack_top == (size_t)-1` guard. After a failed allocation in
`push()`, `stack_top` has already advanced past that guard, so the read is a null
dereference or an out-of-bounds read.
Full analysis in [docs/upstream-bug-oom-stack.md](docs/upstream-bug-oom-stack.md).

**Chosen design.** One accessor, used everywhere:

```zig
fn currentFrame(self: *Stream) ?*abi.Stack {
    if (self.stack_top == abi.stack_empty) return null;
    if (self.stack_top >= self.stack_size) return null;
    const stack = self.stack orelse return null;
    return &stack[self.stack_top];
}
```

**Alternatives considered.** *Reproduce the out-of-bounds read* — impossible to
do faithfully, because what the original returns is whatever happens to follow
the allocation. There is no behaviour to match, only undefined behaviour to
imitate. *Reset `stack_top` on failure* — arguably the better fix, but it would
change `json_get_depth()`, which **is** well defined in C, and so would be a real
divergence on a defined observable.

**Reason.** Where C has no defined behaviour there is nothing to be equivalent
to. The port stays memory-safe and deterministic instead, and the divergence is
counted separately rather than hidden.

**Compatibility impact.** `json_get_context()` returns `JSON_DONE` where the
original reads unallocated memory. `json_get_depth()` still returns
`stack_top + 1`, matching the original exactly. The differential harness
classifies these 43 cases as `upstream_ub`, not as divergences, so the headline
equivalence figure covers only inputs where the original is well defined.
**Performance impact.** Two extra comparisons per event; below measurement noise.
**Verification.** `tests/port/regressions.zig`, `tests/port/allocator_failure.zig`
(every failure point for twelve inputs, plus a leak check),
`artifacts/differential-summary.json`.

---

## D-07 — The 0xFF fix ships as an opt-in flag, off by default

**Chosen design.** `zig build -Dfix-0xff=true` makes the buffer source report
bytes as unsigned, matching the `FILE *` source.

**Alternatives considered.** *On by default* — contradicts D-05. *Not offering
it* — leaves users of the port stuck with a bug we have already diagnosed.

**Reason.** The default answers "is this equivalent to the original?"; the flag
answers "can I have the fixed behaviour?". Both are legitimate questions and
they have different answers, so they get different builds.

**Compatibility impact.** The flag **is** a divergence, by design, and the
differential harness reports it as one. That is the point: the mutant
`buffer-peek-unsigned` in `scripts/mutation-test.py` is exactly this change, and
it is caught by fixture `bad-utf8-ff`, which demonstrates the harness would
notice this class of drift.
**Performance impact.** None.
**Verification.** `tests/port/regressions.zig` branches on the flag;
`artifacts/mutation-report.json`.

---

## D-08 — Counters use explicit wrapping arithmetic

**Original behaviour.** `source.position`, `lineno` and `ntokens` are `size_t`;
overflow wraps, which is defined in C. `stack_top` uses `(size_t)-1` as its
empty sentinel, so `++` from the sentinel is a deliberate wrap. Container counts
are `long`, where overflow is undefined.

**Chosen design.** `+%=` and `-%=` at each of those sites, and
`stack_top +% 1` in `json_get_depth`.

**Alternatives considered.** *Checked arithmetic* — in ReleaseSafe an overflow
panics. A `json_open_user` callback that never reports EOF would eventually
overflow `position`, turning untrusted input into a crash. That directly
contradicts "no panic on untrusted input". *Saturating* — silently diverges from
C at the boundary.

**Reason.** Wrapping is exactly what C does for the unsigned counters, so this is
equivalence rather than a concession. For the signed container count, C has
undefined behaviour and wrapping is what every real compiler produces.

**Compatibility impact.** None. **Performance impact.** None.
**Verification.** `artifacts/safety-report.json` records the reasoning; the
random-bytes test in `tests/port/regressions.zig` drives 20,000 arbitrary inputs
through the parser without a panic.

---

## D-09 — NaN payloads that overflow 64 bits are left implementation-defined

**Original behaviour.** Inherited from libc `strtod`.

**Chosen design.** `nan(...)` payloads are parsed as base-0 integers and placed
in the mantissa, matching glibc and Apple libc. Sequences that overflow 64 bits
yield a plain quiet NaN instead.

**Alternatives considered.** *Match Apple libc exactly* — its overflow behaviour
differs from glibc's, so matching one would diverge on the other, and CI runs on
Linux. *Ignore payloads entirely* — would diverge on the common, reachable cases
like `nan(123)`.

**Reason.** C99 §7.20.1.3p4 makes the meaning of the n-char-sequence
implementation-defined, so on overflow there is no correct answer to match.

**Compatibility impact.** Reachable only by calling `json_get_number()` on a
*string* token whose text begins `nan(` — never from JSON number syntax. Stated
as a limitation in `README.md` and `CLAIMS.json` rather than buried here.
**Performance impact.** None.
**Verification.** `tests/port/number_torture.zig` asserts the matching cases
(`nan`, `nan()`, `nan(123)`, `nan(0x10)`, `nan(010)`, `nan(abc)`, `-nan(5)`)
against libc and pins the overflow case explicitly as implementation-defined.

---

## D-10 — ReleaseSafe is the default build, including for benchmarks

**Chosen design.** A bare `zig build` produces ReleaseSafe. `--release=fast`
still selects ReleaseFast.

**Alternatives considered.** Zig's `standardOptimizeOption` defaults to **Debug**
unless `--release` is passed. That was the initial setup, and it made the first
benchmark run report the Zig side as 6.8× slower than C — a meaningless number,
because nobody ships a Debug parser.

**Reason.** If the shipped artifact keeps bounds and overflow checks on, then
that is the artifact to benchmark and to make safety claims about. Reporting
ReleaseFast numbers while shipping ReleaseSafe, or vice versa, would be
measuring one thing and claiming another.

**Compatibility impact.** None.
**Performance impact.** Both modes are reported side by side in
`artifacts/benchmark-summary.json`. On this workload set the difference between
them is small — the gap against C is structural, not safety-check overhead.
**Verification.** `artifacts/benchmark-summary.json` carries both columns;
`artifacts/safety-report.json` records the shipped mode.

---

## D-11 — `read_number`'s recursion is flattened

**Original architecture.** `read_number()` handles a leading `-` by recursing
into itself once.

**Chosen design.** A single pass: consume the sign, then the integer part, then
the shared fraction/exponent tail that the recursive call would have run.

**Alternatives considered.** Keeping the recursion. It is only one level deep, so
it is not a stack-depth risk; it is just harder to read than the loop it stands
in for.

**Compatibility impact.** None — the recursive call's body is exactly the
integer-part handling plus the shared tail.
**Performance impact.** Negligible; the original's call is easily inlined too.
**Verification.** Every number fixture, plus the differential corpus.

---

## D-12 — Two hot-path changes, both behaviour-preserving

The first benchmark showed the port 30–45% behind C. Profiling both binaries
with `sample` (leaf-weighted) showed why, and neither cause was where I guessed.

**Wrong guess, recorded because it was measured:** the port adds a null check on
each source function pointer that C does not have. Removing them closed **0%** of
the gap.

**Actual cause 1.** C's profile spent 23% in `read_digits` with no `pushchar`
frame — clang had inlined it. The Zig profile showed `readDigits` at 16.6% *and*
`pushchar` at 18.2%, as separate frames. Fix: split `pushchar` into an `inline`
fast path and an out-of-line growth path.

**Actual cause 2.** The original calls `strchr` on two short literals in the
number lexer; C's profile spent 7% in `memchr`. The port used
`std.mem.indexOfScalar`. Both were replaced with direct comparisons — keeping the
detail that C's `strchr` also matches the terminating NUL, which the original
relies on by accident when a number is followed by an embedded NUL byte.

**Compatibility impact.** None. Verified by re-running the full differential
corpus, the upstream suite, and the Zig test suite after each change.
**Performance impact.** Combined, roughly 0.57→0.79 on `flat-ints` and
0.70→0.87 on `large-mixed` relative to C.
**Verification.** `bench/methodology.md` records the profiles;
`artifacts/differential-summary.json` confirms equivalence held throughout.

---

## D-13 — Transcripts exclude `errmsg` bytes past the NUL

**Chosen design.** The behaviour transcript records the diagnostic only up to its
terminator — what `json_get_error()` returns.

**Reason.** `snprintf` writes the message and its NUL and leaves the rest of the
128-byte field untouched. Since callers declare `json_stream` on the stack, those
bytes are whatever was there before. Comparing them would compare uninitialised
stack contents and produce spurious divergences.

Also excluded, for the same reason: pointer values, heap addresses, timing, and
allocation sizes.

**Verification.** `scripts/oracle-determinism.sh` runs both binaries five times
over every fixture in five modes and requires byte-identical output each time.

---

## D-14 — The depth limit is disabled by default, matching upstream

**Original behaviour.** `PDJSON_STACK_MAX` is not defined, so nesting is bounded
only by memory. The `#ifdef` branch exists but is off.

**Chosen design.** `-Dstack-max=N` enables a limit and emits the original's
message verbatim (`maximum depth of nesting reached`). Default 0 = unlimited.

**Reason.** Enabling a limit by default would diverge on deeply nested input.
Offering none would leave users without the knob the original documents.

**Compatibility impact.** Default matches upstream exactly.
**Verification.** Fixtures `deep-array-512`, `deep-object-256`,
`deep-unclosed-1000`; `tests/port/regressions.zig` parses 100,000 nesting levels
to confirm the parser is iterative, not recursive.

---

## D-15 — A null source callback returns EOF instead of dereferencing

**Original behaviour.** `json_next()` on a `json_stream` that was never opened
calls through a null function pointer — undefined behaviour.

**Chosen design.** `orelse return EOF`.

**Reason.** This cannot diverge on any defined program: reaching it already
requires undefined behaviour in C. It makes the port total.

**Compatibility impact.** None on defined inputs.
**Performance impact.** Measured at 0% (see D-12).

---

## D-16 — Fault attribution is decided by a sanitizer, not by the author

**Chosen design.** When the two implementations disagree, `scripts/differential.py`
re-runs the case against an ASan+UBSan build of the pinned original. If the
sanitizer reports an error, the case is classified `upstream_ub` and the report
carries the sanitizer output as evidence. Otherwise it is a `divergence` and
fails the run.

**Alternatives considered.** An allow-list of "known acceptable differences" —
which is exactly the mechanism by which a real divergence gets waved through.

**Reason.** "That one is upstream's fault" is the single easiest place for a port
to deceive itself. Making the decision mechanical, with the evidence attached,
removes the author's judgement from the loop.

**Verification.** All 43 exclusions in `artifacts/differential-summary.json`
carry a sanitizer report, and all resolve to one line: `pdjson.c:912`.

---

## D-17 — The harness is itself tested, and its first results were wrong

**Chosen design.** `scripts/mutation-test.py` injects deliberate defects into the
Zig implementation — wrong escape mapping, off-by-one surrogate range, dropped
NUL terminator, uncounted newline, altered diagnostic — rebuilds, and requires
the fixed-corpus differential to catch each one.

**What it found.** On its first sound run, four mutants **survived**: the corpus
had no fixture with an escaped surrogate pair at the top of the range, none with
a raw control byte at the 0x1F boundary, and no coverage of allocation-failure
messages. Those gaps were real, and the fixture corpus grew from 142 to 214 to
close them.

**Two false-positive traps, both hit and both fixed.** An earlier version scored
12/12 — every mutant "caught" on the same `oom:0` case, because the C oracle
*crashes* there, so every mutant differed for reasons unrelated to the mutation.
Excluding crashing cases was not enough either: on `oom:2` the original reads out
of bounds *without* crashing, so it emits garbage that every mutant also differs
from. The exclusion criterion is now "an ASan+UBSan build of the original reports
an error here", which is the same mechanical test as D-16.

Two mutants also turned out to be **equivalent** — an off-by-one in the
diagnostic truncation limit is unobservable, because the longest message the
library can emit is 62 bytes and the buffer is 128. That is a fact about the
code, not a gap in the corpus, so the mutant was replaced with one that crosses a
reachable boundary.

**Reason.** A comparison harness that never fails proves nothing, and a mutation
score assembled from invalid comparisons is worse than none.
**Verification.** `artifacts/mutation-report.json` records every mutant, how it
was caught, and how many cases were excluded and why.
