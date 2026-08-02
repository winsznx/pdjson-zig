# Exported API coverage

"The differential passes" says nothing about *which parts of the API* the
differential exercises. This classifies all 22 exported functions, and the
classification is derived by reading the harness and test sources rather than
asserted.

```sh
python3 scripts/api-coverage.py     # regenerates artifacts/differential/api-coverage.json
```

**22 exported functions, 0 untested.** The script fails if any export is
untested, so this cannot silently regress.

## What the categories mean

| Category | Meaning |
| --- | --- |
| `differential` | Its result is compared between C and Zig on real inputs |
| `scenario` | Driven by specific drive modes rather than incidentally |
| `consumer` | Exercised by `tests/original/abi_consumer.c`, through the pinned header, linked against only the Zig archive |
| `upstream` | Used by upstream's own `tests.c` / `pretty.c` / `stream.c` |
| `unit` | Covered by Zig-native tests |
| `untested` | No evidence at all |

Seven functions are in the `differential` set by construction, because the
transcript records their value on **every record**: `json_get_string`,
`json_get_number`, `json_get_lineno`, `json_get_position`, `json_get_depth`,
`json_get_context`, `json_get_error`. Every one of the 6,104 corpus comparisons
compares all seven.

## Coverage

| Function | Coverage | How it is driven |
| --- | --- | --- |
| `json_open_buffer` | differential, scenario, consumer, upstream, unit | every buffer-source mode |
| `json_open_string` | differential, scenario, consumer, upstream, unit | `string:next`, `string:nostream`, `string:peek` |
| `json_open_stream` | differential, scenario, upstream | `stream:*` — a real `FILE*` via `tmpfile()` |
| `json_open_user` | differential, scenario | `user:*` — get/peek callbacks over the same bytes |
| `json_close` | differential, scenario, consumer, upstream, unit | end of every transcript |
| `json_set_allocator` | differential, scenario, unit | `oom:0/1/2/5` failure schedules |
| `json_set_streaming` | differential, scenario, consumer, upstream, unit | `nostream` (false) vs all others (true) |
| `json_next` | differential, scenario, consumer, upstream, unit | every mode |
| `json_peek` | differential, scenario, upstream, unit | `peek`, `stream:peek`, `user:peek` |
| `json_reset` | differential, scenario, consumer, upstream, unit | after every `DONE` in streaming modes |
| `json_get_string` | differential, consumer, upstream, unit | every record |
| `json_get_number` | differential, consumer, upstream, unit | every record |
| `json_skip` | differential, scenario, consumer, unit | `skip`, `user:skip` |
| `json_skip_until` | differential, scenario, unit | `skipuntil:4/6/7/8/11` |
| `json_get_lineno` | differential, consumer, upstream, unit | every record |
| `json_get_position` | differential, unit | every record |
| `json_get_depth` | differential, consumer, upstream, unit | every record |
| `json_get_context` | differential, consumer, unit | every record |
| `json_get_error` | differential, consumer, upstream, unit | every record |
| `json_source_get` | differential, scenario, consumer, upstream | `sep`, `stream:sep` |
| `json_source_peek` | differential, scenario, consumer, upstream | `sep`, `stream:sep` |
| `json_isspace` | differential, scenario, consumer, upstream | `sep`, `stream:sep` |

Machine-readable, with per-function notes:
[`artifacts/differential/api-coverage.json`](../artifacts/differential/api-coverage.json).

## What this analysis found

Running it for the first time reported **one untested export: `json_skip_until`.**

It was exported, present in the symbol check, and covered by the ABI layout
comparison — so every existing check said it was fine — but nothing ever compared
its *behaviour*. Upstream's own tests do not call it either, so the upstream
suite could not have caught a defect in it.

That is exactly the gap this analysis exists to find, and it was closed rather
than documented:

- **Five new drive modes**, `skipuntil:4/6/7/8/11`, targeting each container end
  and three scalar types. Both transcript producers implement them, so the
  function is now compared on every corpus input.
- **Five Zig-native tests** covering: reaching a container end, reaching a scalar
  type and continuing afterwards, running to `DONE` when the target never
  appears, surfacing a parse error rather than looping, and entering mid-object.

Writing those tests exposed a second thing worth recording. **Two of the five
initially failed, because my expectations were wrong about what
`json_skip_until` does.** `json_skip` consumes an *entire value* per call, so
searching for `ARRAY_END` inside `[1,[2,[3,4],5],6]` does not stop at the inner
array's close — it swallows each sibling whole and matches the *outer* close,
leaving depth 0. The expectations were replaced with values read out of the C
original, and the tests now say so in comments.

That is the useful failure mode of this kind of coverage work: it does not just
find untested code, it finds where the porter's mental model was wrong.

While adding `string:*`, `json_open_string` also moved from "incidental" to
directly driven. It derives its length with `strlen`, so an embedded NUL
truncates the input — both implementations must truncate identically, which the
`nul-*` fixtures now check through that path.

## Honest limits

- **Coverage is not the same as exhaustiveness.** Every export is exercised, but
  a function can be driven and still have untested edge cases.
- **`json_open_stream` and `json_open_user` are not in the `consumer` column.**
  `abi_consumer.c` builds on `json_open_string`; the other sources are covered by
  the differential and by upstream's `stream.c`, not by that consumer.
- **Classification is derived from source text.** It looks for call sites, so a
  function called through an alias the mapping does not know about would be
  misclassified. The `untested` list is the part that matters, and it is empty.
- **`json_set_allocator` is only driven under failure schedules** in the
  differential. Its success path is covered by the benchmark harnesses and by
  `tests/port/allocator_failure.zig`.
