# The four input sources, as a table rather than a sentence

`pdjson.h` documents four ways to feed the parser. They are not thin wrappers
around one another — each has its own read path, and one of them is never
touched by anything upstream ships.

"All four sources are covered" is a sentence. This is the table it stands on,
derived from the run rather than asserted:

```sh
python3 scripts/differential.py --label fixed-corpus
```

| Opener | How the bytes arrive | Modes | Comparisons | Divergences |
| --- | --- | ---: | ---: | ---: |
| `json_open_buffer` | an explicit pointer and length | 15 | 3,270 | 0 |
| `json_open_stream` | a real `FILE*` from `tmpfile()`, read through `fgetc`/`ungetc` | 5 | 1,090 | 0 |
| `json_open_user` | caller-supplied `get`/`peek` callbacks over the same bytes | 5 | 1,090 | 0 |
| `json_open_string` | length derived with `strlen`, so an embedded NUL truncates | 3 | 654 | 0 |

**218 inputs × 28 drive modes = 6,104 comparisons, 0 divergences.**

A source whose row read 0 comparisons would be a claim with nothing behind it,
which is the failure mode this table exists to make impossible.

## Why the sources are not interchangeable

They differ in ways the parser can observe:

- **`json_open_stream`** goes through `fgetc` and `ungetc`. `ungetc` is only
  guaranteed for one character of pushback, and a `FILE*` returns `EOF` as an
  `int` — so the byte `0xFF` arrives here as `255`, not as `-1`. That is
  precisely the asymmetry behind upstream
  [#37](https://github.com/skeeto/pdjson/issues/37): the buffer source indexes a
  `const char *` and confuses `0xFF` with `EOF`, while this source does not.
  The two disagree with each other on identical input.
- **`json_open_user`** calls back into caller code for every byte. A port that
  buffered ahead, or that called `peek` a different number of times, would
  diverge here and nowhere else.
- **`json_open_string`** takes no length. It calls `strlen`, so a document
  containing a NUL is truncated at it. The `nul-*` fixtures check that both
  implementations truncate at the same byte.
- **`json_open_buffer`** is the only one that can carry an explicit length past
  a NUL, and the only one upstream's own assertion suite (`tests/tests.c`) uses.

## What upstream's own tests cover

| Opener | `tests/tests.c` | `tests/pretty.c` | `tests/stream.c` | this differential |
| --- | :---: | :---: | :---: | ---: |
| `json_open_buffer` | yes | — | — | 3,270 |
| `json_open_string` | — | yes | — | 654 |
| `json_open_stream` | — | yes | yes | 1,090 |
| `json_open_user` | — | — | — | 1,090 |

`json_open_user` is exercised by **nothing** upstream ships. Its 1,090
comparisons here are the only behavioural evidence for it that exists in either
project.

Per-source artifacts, kept separately because these are the two rows a reader is
most likely to doubt:

- [`artifacts/differential/file-source-summary-fixed-corpus.json`](../artifacts/differential/file-source-summary-fixed-corpus.json)
- [`artifacts/differential/user-source-summary-fixed-corpus.json`](../artifacts/differential/user-source-summary-fixed-corpus.json)
- [`artifacts/differential/source-matrix-fixed-corpus.json`](../artifacts/differential/source-matrix-fixed-corpus.json) — the full matrix, plus a per-mode breakdown

## The 45 upstream-UB cases are all in one row

Every case classified `upstream_ub` sits under `json_open_buffer`, and every one
of them is an allocation-failure mode (`oom:*`). That is not a property of the
buffer source: it is upstream [#36](https://github.com/skeeto/pdjson/issues/36),
a null dereference and out-of-bounds read at `pdjson.c:912` when the container
stack fails to grow. The other three sources have no `oom:*` modes in the matrix,
so the row is where the modes are, not where the bug is.

Classification is decided by an ASan+UBSan build of the pinned original, not by
judgement — see [`DECISIONS.md`](../DECISIONS.md) D-16.

## Limits

- **Attribution is by mode prefix.** Each drive mode is attributed to the
  `json_open_*` function its name says it uses, and multiplied by the input
  count. A mode that silently fell back to a different opener would be
  misattributed. That the functions themselves are reached is checked separately
  by [`docs/api-coverage.md`](api-coverage.md), which derives coverage from the
  harness sources.
- **Equal comparison counts do not mean equal thoroughness.** The buffer source
  has 15 modes because allocation-failure schedules and `json_skip_until` targets
  only exist there; the other sources are covered across fewer axes.
- **The JSONTestSuite corpus adds 3,816 more comparisons** across 12 modes and
  three sources, with its own matrix in
  [`artifacts/differential/source-matrix-jsontestsuite.json`](../artifacts/differential/source-matrix-jsontestsuite.json)
  and its conformance classification in
  [`artifacts/conformance-report.json`](../artifacts/conformance-report.json).
