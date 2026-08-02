# Testing the test harness

Every equivalence number in this project comes from one mechanism: run both
implementations, compare the transcripts, require them to be identical. That
mechanism is load-bearing for **every** claim, and it has an obvious failure
mode — if the comparison is too weak, or the corpus too narrow, it reports
success without doing any work.

A harness that never fails proves nothing. So this asks two separate questions:

| Question | Answered by |
| --- | --- |
| Is the **comparison** sensitive to what it claims to compare? | `--self-test` — 12 fields perturbed, no builds |
| Is the **corpus** wide enough to reach a real defect? | the mutant run — 12 defects injected, 12 builds |

```sh
python3 scripts/mutation-test.py --self-test   # fast, runs in `make verify`
make mutation                                  # the mutants; ~12 Zig builds
make mutation-weakened                         # the same mutants, weaker comparison
```

## 1. Is the comparison sensitive to every field?

"12/12 mutants caught" is only meaningful if the comparison doing the catching is
actually looking at everything it says it is. A harness that compared **only the
event sequence** would still catch most of these mutants and report the same
12/12 — while being blind to token bytes, number values, line numbers, byte
positions, container depths and error text.

So the comparison is a named, swappable function, and the self-test perturbs
every field a transcript record carries, one at a time, and requires it to
notice:

```
$ python3 scripts/mutation-test.py --self-test
The real comparator must notice a change in every field:
  ok    event      ok    tok        ok    toklen     ok    num
  ok    line       ok    pos        ok    depth      ok    ctx
  ok    ctxn       ok    err        ok    op         ok    seq

A comparator must not fire when nothing changed:
  ok    full   ok    event-only   ok    record-count   ok    first-record

Each deliberately weakened comparator must miss what it ignores:
  ok    event-only is blind to line numbers
  ok    event-only is blind to token bytes
  ok    event-only is blind to number values
  ok    event-only is blind to error text
  ok    event-only is blind to byte positions
  ok    event-only is blind to container depth
  ok    record-count is blind to the events themselves
  ok    first-record is blind to anything past the first record

detector self-test: 0 failure(s)
```

The third block matters as much as the first. If a "weakened" comparator turned
out to detect everything anyway, the weakening would be fictional — and so would
any conclusion drawn from comparing against it.

This runs inside `make verify` because it builds nothing.

Artifact:
[`artifacts/mutation/detector-selftest.json`](../artifacts/mutation/detector-selftest.json).

## 2. Is the corpus wide enough to reach a real defect?

Twelve deliberate defects are injected into the Zig implementation, one at a
time, each built from a throwaway copy of the tree so the real sources are never
touched. Each must be caught by comparing against the C oracle over the fixed
corpus.

| Mutant | What it breaks |
| --- | --- |
| `lineno-not-counted` | newlines stop advancing `json_get_lineno` |
| `depth-off-by-one` | `json_get_depth` reports one too few |
| `oom-message-typo` | the allocation-failure diagnostic gains a letter |
| `escape-b-wrong` | `\b` decodes to 0x07 instead of 0x08 |
| `surrogate-high-range` | the high-surrogate range loses its top value |
| `utf8-accept-overlong` | overlong 2-byte sequences become acceptable |
| `number-no-terminator` | the number token stops being NUL-terminated |
| `control-char-allowed` | 0x1F stops being rejected inside a string |
| `position-not-advanced-on-peek` | spaces stop advancing `json_get_position` |
| `strtod-no-conversion-value` | a failed conversion returns 1 rather than 0 |
| `errmsg-truncation` | the diagnostic buffer truncates 80 bytes early |
| `buffer-peek-unsigned` | the 0xFF/EOF confusion is "fixed", diverging from C |

A mutant that fails to compile, or whose pattern no longer matches the source, is
reported as a **failure of this script**, not as a pass. It cannot rot into a
no-op as the code changes.

### What it found the first time

Four mutants **survived**. Those were real blind spots in the corpus: no fixture
had an escaped surrogate pair at the top of the range, none had a raw control
byte at the 0x1F boundary, and nothing covered allocation-failure messages. The
fixture corpus grew from 142 to 214 to close them, and is 218 today.

### Two false-positive traps, both hit

An early version scored **12/12** — and was wrong. Every mutant was "caught" on
the same `oom:0` case, because the pinned C original *crashes* there
(upstream [#36](https://github.com/skeeto/pdjson/issues/36)), so every mutant
differed from it for reasons that had nothing to do with the mutation.

Excluding crashing cases was not enough either: on `oom:2` the original reads out
of bounds *without* crashing, so it emits bytes from unallocated memory that
every mutant also differs from. The exclusion criterion is now "an ASan+UBSan
build of the original reports an error on this case", which is the same
mechanical test the real pipeline uses to classify upstream UB — decided by a
sanitizer, not by judgement.

Two mutants also turned out to be **equivalent**: an off-by-one in the
diagnostic truncation limit is unobservable, because the longest message the
library can emit is 62 bytes and the buffer is 128. That is a fact about the
code, not a gap in the corpus, so the mutant was replaced with one that crosses a
boundary real diagnostics actually reach.

Artifact, with every mutant and the exact fixture and mode that caught it:
[`artifacts/mutation-report.json`](../artifacts/mutation-report.json).

## Why both questions are needed

They fail in opposite directions.

A weak *comparison* with a wide corpus reports success because it is not looking.
A strong comparison with a narrow *corpus* reports success because the defect is
never reached. Checking only one leaves the other free to be wrong, and both
produce the same clean output.

Running the same mutants under `--detector event-only` shows the split
concretely: the corpus is unchanged, only the comparison is weakened, and mutants
that the full comparison catches survive.

## Limits

- **Twelve mutants is twelve mutants.** They were chosen to span the parser's
  distinct concerns — lexing, escapes, UTF-8, numbers, diagnostics, positions,
  the allocator path — not sampled from a generator. A defect unlike all twelve
  could still slip through.
- **The self-test uses synthetic transcripts.** It proves the comparison notices
  a changed field; it does not prove the parser can produce every such change.
- **Equivalent mutants are unavoidable.** Two were found by hand here. A mutant
  that cannot be observed through the public API is not evidence about the
  harness either way, and is replaced rather than counted.
- **This measures the fixed-corpus differential only.** The fuzzer, the invariant
  checker and the state-machine coverage are separately self-tested; see
  [`docs/transcript-invariants.md`](transcript-invariants.md) and
  [`docs/state-machine.md`](state-machine.md).
