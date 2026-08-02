# Behaviour transcript schema

`pdjson-zig/transcript@2`

A transcript is the complete record of what a caller can observe while driving a
`json_stream` through a fixed script. It is NDJSON — one JSON object per line —
and it is the unit of comparison in this project: two implementations are
considered equivalent on an input when their transcripts are **byte-identical**.

Produced by two independent programs:

| Producer | Parser under test |
| --- | --- |
| `oracle/transcript_c.c` | the pinned upstream `pdjson.c` |
| `tools/transcript_zig.zig` | the Zig library |

They are deliberately **not** sharing a formatter. A shared emitter could
normalise a real difference away on both sides at once.

## Record types

### Header (first line)

```json
{"schema":"pdjson-zig/transcript@2","mode":"next","bytes":27}
```

### Event record

Emitted after every parser operation.

```json
{"seq":3,"op":"next","event":"NUMBER","tok":"3100","toklen":2,
 "num":"3ff0000000000000","line":1,"pos":7,"depth":2,
 "ctx":"ARRAY","ctxn":1,"err":null}
```

| Field | Source | Notes |
| --- | --- | --- |
| `seq` | harness | Operation counter, from 0. |
| `op` | harness | `next`, `peek`, `skip`, or `reset`. |
| `event` | return value | `ERROR`, `DONE`, `OBJECT`, `OBJECT_END`, `ARRAY`, `ARRAY_END`, `STRING`, `NUMBER`, `TRUE`, `FALSE`, `NULL`, or `NONE` for the `(enum json_type)0` sentinel. |
| `tok` | `json_get_string` | The token bytes, hex encoded. Hex because tokens legitimately contain NUL, invalid UTF-8, and control bytes, none of which survive being placed in a JSON string. |
| `toklen` | `json_get_string` | The length out-parameter. Note this **includes** the trailing NUL that upstream pushes, so `"v"` reports 2. |
| `num` | `json_get_number` | The IEEE-754 bit pattern, 16 hex digits. Bits rather than a decimal rendering so the comparison is exact, and so `-0.0`, infinities and NaN payloads stay visible. Recorded after every event, not just numbers, because the API permits the call at any time. |
| `line` | `json_get_lineno` | |
| `pos` | `json_get_position` | Bytes consumed from the source. |
| `depth` | `json_get_depth` | |
| `ctx` | `json_get_context` | Enclosing container type. |
| `ctxn` | `json_get_context` | The count out-parameter. |
| `err` | `json_get_error` | `null`, or the message hex encoded, **up to its NUL terminator**. |

### Source-byte record

Emitted in `sep` mode, which drives the separator-inspection API.

```json
{"seq":12,"op":"peek_byte","byte":10,"line":2,"pos":18}
```

`op` is `peek_byte` (`json_source_peek`) or `get_byte` (`json_source_get`).
`byte` is the raw `int` return value, so `-1` for EOF is distinguishable from a
`0xFF` byte — which matters, because the original conflates them
(`docs/upstream-bug-0xff.md`).

### Terminator (last line)

```json
{"end":true,"records":13}
```

or, if the operation cap was reached:

```json
{"truncated":true}
```

Truncation is reported rather than silently returning a short transcript, so a
pathological input cannot masquerade as a completed comparison.

### Batch and pack framing

For bulk runs, each input's transcript is preceded by
`{"input":"<path>"}` (`--batch`) or `{"input":"pack:<n>"}` (`--pack`). The pack
format — `<decimal length>\n<raw bytes>`, repeated — lets the fuzzer hand over
thousands of cases per process, which is what makes ~9,000 cases/second possible.

## What is deliberately *not* recorded

Recording these would produce differences that mean nothing:

- **Pointer and heap addresses.** Not behaviour.
- **`errmsg` bytes past the NUL.** `snprintf` leaves the rest of the 128-byte
  field untouched, and callers declare `json_stream` on the stack, so those bytes
  are uninitialised. See DECISIONS.md D-13.
- **Timing.** Nondeterministic by nature; covered separately by the benchmark.
- **Allocation sizes.** An implementation detail. Allocation *counts* and *bytes*
  are measured by the benchmark instead.

Everything else reachable through `pdjson.h` is recorded.

## Input sources

A mode may be prefixed with the source the parser reads from. The three are
documented as interchangeable, and upstream issue
[#37](https://github.com/skeeto/pdjson/issues/37) is precisely a case where two
of them disagree on identical bytes -- so comparing only one would leave the most
interesting class of difference untested.

| Prefix | Opened with | Notes |
| --- | --- | --- |
| *(none)* | `json_open_buffer` | A byte array. Where the `0xFF`/EOF confusion lives. |
| `stream:` | `json_open_stream` | A `FILE *`; reads go through `fgetc`/`ungetc`, which report bytes as `unsigned char`. |
| `user:` | `json_open_user` | Caller-supplied `get`/`peek` callbacks over the same bytes, matching `fgetc` conventions. |

Both producers create the `FILE *` with `tmpfile()` and write the input to it, so
the byte stream is identical to the buffer case.

## Drive modes

One input yields 19 transcripts -- three sources crossed with five drives, plus
four allocation-failure schedules -- because the same bytes exercise different
paths depending on how the caller drives the parser.

| Mode | What it exercises |
| --- | --- |
| `next` | The streaming event loop with `json_reset` between values — upstream's own `tests/stream.c` pattern. |
| `nostream` | `json_set_streaming(false)`: trailing non-whitespace is an error. |
| `peek` | `json_peek` before every `json_next`, exercising the buffered-event path and the position side effect of upstream issue #15. |
| `skip` | `json_skip` over whole values instead of stepping through them. |
| `sep` | The separator-validation loop from upstream's README, via `json_source_get`/`json_source_peek`. |
| `oom:0` | A custom allocator where every allocation fails. |
| `oom:1`, `oom:2`, `oom:5` | The first N allocations succeed, then all fail — walking the failure point through the parse. |

The `oom:*` modes are how `docs/upstream-bug-oom-stack.md` was found.

## Determinism

The comparison is worthless unless both sides are reproducible.
`scripts/oracle-determinism.sh` runs each binary five times over every fixture in
five modes and requires byte-identical output every time. Result in
`artifacts/determinism-report.json`.

## Versioning

The schema string is embedded in every transcript's header line. Any change to
the record shape — a new field, a renamed field, a different encoding — requires
bumping it, so old artifacts cannot be silently compared against new ones.
