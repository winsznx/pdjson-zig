# The state machine, specified and then measured

"0 divergences across 6,104 comparisons" counts *inputs*. It says nothing about
which parts of the parser those inputs drive. A corpus can be large and still
never once reach, say, a boolean immediately after a key inside an object.

So the transition relation is written out — from RFC 8259's grammar and
`pdjson.h`'s contract, not from either implementation — and the corpus is
measured against it.

```sh
python3 scripts/state-machine.py --self-test   # the state derivation
python3 scripts/state-machine.py               # the coverage measurement
```

**10 states, 54 specified transitions, 54 covered (100%), 0 observed that the
specification does not contain, 0 reached by one implementation and not the
other.**

## The state is observable, not internal

The state is computed from what a caller can see through the public API —
`json_get_context`'s type and count — and nothing else. Reading a private
variable would make the specification a restatement of the implementation.

The count does more work than it looks like it does. `json_next` increments it
per event, so inside an object an **odd** count means a key was just returned and
an **even** one means a value was. That is what lets the specification say "a key
must be followed by a value" rather than collapsing both into "a STRING".

```
{"a":1,"b":[2,3]}

 0 OBJECT       depth=1 ctx=OBJECT   ctxn=0    → OBJECT.EMPTY
 1 STRING       depth=1 ctx=OBJECT   ctxn=1    → OBJECT.AFTER_KEY     (a key)
 2 NUMBER       depth=1 ctx=OBJECT   ctxn=2    → OBJECT.AFTER_VALUE
 3 STRING       depth=1 ctx=OBJECT   ctxn=3    → OBJECT.AFTER_KEY     (a key)
 4 ARRAY        depth=2 ctx=ARRAY    ctxn=0    → ARRAY.EMPTY
 ...
```

## The specification

| State | Meaning | Allowed next events |
| --- | --- | --- |
| `TOP.START` | nothing parsed yet | any value, `DONE`, `ERROR` |
| `TOP.AFTER_VALUE` | a complete top-level value was returned | `DONE`, `ERROR` |
| `TOP.DONE` | `DONE` was returned and no reset since | `DONE` |
| `TOP.DONE_RESET` | `json_reset` was called | any value, `DONE`, `ERROR` |
| `ERROR_LATCHED` | the error flag is set | `ERROR` |
| `OBJECT.EMPTY` | `{` returned, no member yet | `STRING`, `OBJECT_END`, `ERROR` |
| `OBJECT.AFTER_KEY` | a key was returned | any value, `ERROR` |
| `OBJECT.AFTER_VALUE` | a member is complete | `STRING`, `OBJECT_END`, `ERROR` |
| `ARRAY.EMPTY` | `[` returned, no element yet | any value, `ARRAY_END`, `ERROR` |
| `ARRAY.AFTER_VALUE` | an element is complete | any value, `ARRAY_END`, `ERROR` |

"any value" is `OBJECT`, `ARRAY`, `STRING`, `NUMBER`, `TRUE`, `FALSE`, `NULL`.

Three things fall out of comparing this against the corpus, and all three are
findings:

- a **specified transition never observed** is a coverage gap;
- an **observed transition the specification does not contain** is either a
  parser bug or a wrong specification, and has to be resolved either way;
- a **transition reached by one implementation and not the other** would mean the
  transcripts are not byte-identical after all.

The second and third came out at zero on the first run — the specification
matched reality exactly. The first did not.

## What the first run found

**46 of 54 (85.2%).** Eight gaps, of two different kinds.

### Two were gaps in the harness, not the corpus

`ERROR_LATCHED → ERROR` and `TOP.DONE → DONE` were unreachable *by any drive
mode*. Both transcript producers had:

```c
if (type == JSON_ERROR) break;
```

and a matching `break` after `DONE` when not resetting. So no transcript ever
contained two consecutive `ERROR` records, or two `DONE` records without a
`json_reset` between them.

That matters more than a missing fixture. Both are documented API behaviours a
real caller hits: the error flag **latches**, and `DONE` is **idempotent**. And
the invariant checker has a rule named `error-is-latched` — which had therefore
never once fired against real data. It passed its own self-test against
synthetic malformed transcripts, so nothing looked wrong.

Closed by adding an `after-end` drive mode to **both** producers independently,
which calls `json_next` twice more past the terminal event without resetting.
Both now emit byte-identical output for it:

```
$ ./build/transcript_c       after-end err.json | tail -3
$ ./zig-out/bin/transcript_zig after-end err.json | tail -3
{"seq":3,...,"event":"ERROR",...,"err":"756e657870656374656420656e64206f662074657874"}
{"seq":4,...,"event":"ERROR",...,"err":"756e657870656374656420656e64206f662074657874"}
{"seq":5,...,"event":"ERROR",...,"err":"756e657870656374656420656e64206f662074657874"}
```

The mode is now part of the differential corpus too, not only of this analysis,
so the latch and the idempotence are *compared* rather than merely reached.

### Six were gaps in the corpus

`false` and `null` immediately after an object key. `false` after an array
element. `true`, `false` and `null` as the second or later document in a
streaming sequence. Three fixtures closed all six:

| Fixture | Contents |
| --- | --- |
| `arr-literals-after-value.json` | `[1,false,true,null,2]` |
| `obj-literal-values.json` | `{"a":false,"b":null,"c":true}` |
| `stream-literals.json` | `1 true false null "x"` |

The last one had to be written carefully: an earlier version began with `true`,
which covers `TOP.START → TRUE` — already covered — and left
`TOP.DONE_RESET → TRUE` still at zero. Putting a number first is what actually
closes it. A coverage number that moves for the wrong reason is exactly what this
analysis is supposed to catch, including when the mistake is mine.

## The state derivation is itself tested

14 cases pinning each state to the observable values that produce it, plus a
check that **every state named in the specification is one the derivation can
actually produce** — otherwise a typo in a state name would make its transitions
permanently uncoverable for a silly reason, and the coverage number would just
look bad rather than wrong.

```
$ python3 scripts/state-machine.py --self-test
state-derivation self-test: 14 cases, 0 failure(s)
```

## Limits, stated plainly

- **Single-step transitions under `json_next` only.** `json_peek` does not
  advance the parser and `json_skip` consumes a whole value, so neither forms a
  single-step successor. Their coverage is
  [`docs/api-coverage.md`](api-coverage.md)'s job.
- **100% transition coverage is not path coverage.** Reaching every edge says
  nothing about every *sequence* of edges. Deeply nested containers, long
  streaming runs and allocation failure interleaved with nesting are covered by
  the differential and the fuzzer, not by this.
- **`ERROR` is one event regardless of cause.** The state is derived from the
  public API, which does not distinguish an unterminated string from a bad
  escape. The error *text* is compared on every record by the differential; this
  analysis does not partition on it.
- **The specification is hand-written.** It agreeing with both implementations is
  evidence, not proof; a shared misunderstanding of the grammar would be
  invisible here. The independent standards corpus (JSONTestSuite, 318 parsing
  cases) is what covers that.

Artifact, with every transition and the input it was first seen in:
[`artifacts/state-machine/coverage.json`](../artifacts/state-machine/coverage.json).
