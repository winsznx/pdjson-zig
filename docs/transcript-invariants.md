# Transcript invariants

Everything else in this project compares the C original against the Zig port.
That catches the two **disagreeing**. It cannot catch both being wrong in the
same way, and it says nothing about a transcript only one side produced.

`scripts/invariants.py` reads a transcript on its own terms and asks whether it
describes a parser behaving sanely — no reference to the other implementation.

```sh
python3 scripts/invariants.py --self-test    # every rule must be able to fail
python3 scripts/invariants.py --sweep        # generate and check the whole corpus
make invariants                              # both, and write the artifact
```

Current result: **12,792 transcripts, 5,645,337 records, 0 violations** on either
implementation. Artifact: [`artifacts/invariants/summary.json`](../artifacts/invariants/summary.json).

## Why Python

The checker must not share code with either implementation. Writing it in Zig
would tempt reuse of `src/`; using the port to parse its own transcripts would be
circular. Python keeps it independent and auditable, and the corpus is small
enough that speed does not matter.

## Methodology: rules are developed against the C original first

The rule that a rule must satisfy: **if it fires on unmodified upstream output,
the rule is wrong.** Upstream is the reference; a checker that disagrees with it
has encoded an assumption, not a promise.

This is not hypothetical. The first sweep produced 14 violations, all firing on
**both** implementations — which is the signature of a bad rule rather than a
defect. Both were rule bugs:

**`depth-matches-container-events` in skip modes.** `json_skip` consumes an
entire value and returns the event that *started* it, so the depth reported
alongside an `OBJECT` event is the depth *after* the whole object was consumed.
Unchanged depth is correct there. The rule now exempts skip modes.

**`containers-balanced` on truncated transcripts.** JSONTestSuite's
`n_structure_100000_opening_arrays.json` has 100,000 `[` characters; in peek mode
that is 200,000 records, which hits the harness's record cap. The transcript ends
`{"truncated":true}` and legitimately has unclosed containers. The rule now
declines to judge a transcript that did not run to completion.

Both corrections are recorded here rather than quietly applied, because "we
adjusted the checker until it passed" and "we found two wrong rules" look
identical from the outside unless the reasoning is written down.

## The rules

13 rule functions covering 14 violation classes. Each is a property the pdjson
API actually promises.

| Rule | What it asserts | Why the API promises it |
| --- | --- | --- |
| `sequence-contiguous` | Sequence numbers start at 0 and step by 1 | A gap means the harness dropped a record, silently weakening every comparison built on it |
| `token-hex-even` | Token bytes are valid hex pairs | Encoding integrity |
| `token-length-matches-bytes` | `toklen` equals the number of token bytes | `json_get_string`'s out-parameter and the bytes it points at must agree, or a caller reads the wrong amount |
| `position-monotonic` | Byte position never decreases | `json_get_position` reports bytes consumed; nothing in the API rewinds |
| `line-positive` / `line-monotonic` | Line starts at 1 and never decreases | `lineno` is initialised to 1 and only ever incremented |
| `error-is-latched` | After an ERROR, later events stay ERROR until a reset | `JSON_FLAG_ERROR` short-circuits `json_next`; the README says the stream cannot be used again until reset |
| `error-has-message` | An ERROR event carries a diagnostic | `json_get_error` returns the message whenever the flag is set, and the flag is what produces the event |
| `depth-matches-container-events` | Depth moves ±1 on container events, 0 on scalars | Depth is the container-stack height |
| `containers-balanced` | Opens and closes balance over a completed transcript | Structural well-formedness of the event stream |
| `context-agrees-with-depth` | `ctx == DONE` exactly when `depth == 0` | `json_get_context` returns `JSON_DONE` at the top level, where `json_get_depth` returns 0 |
| `reset-clears-state` | After reset: depth 0, context DONE, no error | `json_reset` clears the stack, token count and error flag |
| `peek-then-next-agree` | In peek mode, a peek and the following next report the same event | `json_peek` stores the event; `json_next` returns the stored one |
| `number-defined-when-terminated` | `num` is recorded exactly when the token bytes contain a NUL | This project's own exclusion rule for upstream #38 — checking it means the rule is verified rather than assumed |
| `well-formed-ndjson` | Every line parses as JSON | Encoding integrity |

## Each rule is proved able to fail

A checker that cannot fail is worth nothing, so every violation class has a
deliberately malformed fixture, and `--self-test` requires each to fire:

```
self-test: 14 rules, 14 provably able to fail, 0 problem(s)
```

`--sweep` runs the self-test first and refuses to proceed if any rule has gone
inert. This is the same discipline as the mutation testing in
[DECISIONS.md D-17](../DECISIONS.md) — and it exists because that check
previously produced a false pass twice.

## Scope

Run over: the 215-fixture corpus, the 318-case JSONTestSuite corpus, and any
minimized fuzz findings, across 13 drive modes covering all three input sources
plus allocation-failure schedules.

**Allocation-failure modes are not run against the C binary.** It crashes there
(upstream [#36](https://github.com/skeeto/pdjson/issues/36)), so its output is
truncated by a signal rather than invalid — running the checker on it would
report a harness artifact as a violation. The Zig side *is* checked in those
modes, and passes.

## Deliberately not encoded

Properties that look like invariants but are not promises, listed so their
absence is a decision rather than an oversight:

- **"peek does not advance the input."** It does — `json_peek` calls
  `json_next` underneath, so `json_get_position` moves. That is upstream
  issue #15, and the port preserves it.
- **"reset restores the token buffer."** It does not. `json_reset` deliberately
  leaves both the token buffer and any buffered peek intact, which is observable
  and is pinned by `tests/port/regressions.zig`.
- **"depth returns to 0 at the end of a value."** Not after a failed allocation:
  `push()` advances the stack index before growing the stack, so depth can be
  left non-zero with no matching container event. That is upstream #36, and
  encoding it as a rule would report a known upstream defect as an invariant
  violation on every allocation-failure case.
- **"an ERROR event means no further input is consumed."** The position can still
  advance across a latched error in some drive modes, because the harness
  continues calling accessors.
