# Phase 0: source and feasibility assessment

Written before any implementation work, from measurements rather than
impressions. Recommendation at the end.

## Environment

| | |
| --- | --- |
| OS / arch | macOS 27.0 (Darwin 27.0.0), arm64, Apple M1 Pro, 8 cores, 32 GB |
| Zig | 0.16.0 (installed for this project; not previously present) |
| C compiler | Apple clang 21.0.0 (clang-2100.1.1.101), target `arm64-apple-darwin27.0.0` |
| make | GNU Make 3.81 |
| Sanitizers | ASan and UBSan available via clang |
| Fuzzing tools | no AFL, honggfuzz or radamsa; libFuzzer unavailable for this workflow |
| Docker | 29.6.2 |
| GitHub CLI | 2.96.0, authenticated as `winsznx`, scopes `gist, read:org, repo` |
| Devfolio MCP | **not connected** — see "Blockers" |
| cloc / tokei | not installed; line counts taken with `wc` |

`char` is **signed** on this target. That turned out to matter (see "Hard parts").

## Upstream, pinned

| | |
| --- | --- |
| URL | https://github.com/skeeto/pdjson |
| Commit | `78fe04b820dc8817f540bdd87fb22887e0ef3981` |
| Branch | `master` |
| Date | 2024-02-22T13:12:52+02:00 |
| Subject | "Don't attempt to print EOF characters in diagnostics" |
| License | **Unlicense** (public domain dedication) |

Nine files, all hash-pinned in `artifacts/upstream-manifest.json`:

| File | Lines | Non-blank | Category |
| --- | --- | --- | --- |
| `pdjson.c` | 992 | 885 | source |
| `pdjson.h` | 117 | 95 | header |
| `tests/tests.c` | 318 | 287 | test |
| `tests/pretty.c` | 141 | 130 | test |
| `tests/stream.c` | 74 | 72 | test |
| `Makefile` | 30 | — | build |
| `README.md` | 140 | — | documentation |
| `UNLICENSE` | 24 | — | license |
| `.gitignore` | 4 | — | ignore |

Build: `make`. Test: `make check` (runs `tests/tests`).

## Baseline: does the original work?

Clean clone, unmodified:

```
$ make && ./tests/tests
18 pass, 0 fail
```

Zero warnings under `-std=c99 -pedantic -Wall -Wextra`.

Under ASan + UBSan, the suite is also clean:

```
$ cc -fsanitize=address,undefined ... && ./tests
18 pass, 0 fail          (no sanitizer output)
```

The suite is small — 18 assertions over ~990 lines of parser — and covers
literals, strings, one object, one array, streaming, one truncation case, and
five Unicode cases. It does not test positions, depth, context, `json_skip`,
`json_get_number`, the allocator hooks, invalid UTF-8, or control characters.
**Passing it is necessary but nowhere near sufficient**, which is the main
argument for building the transcript oracle rather than relying on it.

## Existing Zig ports

Searched GitHub repositories, GitHub code search, and the web.

- `gh search repos pdjson` → only `skeeto/pdjson` and three unrelated projects.
- `gh search code "pdjson language:zig"` → hits in `urbit/vere` and forks. All are
  `ext/pdjson/build.zig`, which **compiles the vendored C source** with
  `addCSourceFiles`. A build-system wrapper, not a rewrite.
- `gh search code "json_open_buffer language:zig"` → no results.
- Prior Port Mortem submissions: several exist (semver, tinycolor, natsort, cJSON,
  textdistance, tinyexpr), **none targeting pdjson**.

**No disqualifying prior art.** Nothing to be accused of copying, and nothing
that makes the work redundant.

## Public API surface

22 exported functions, one public enum, three public structs.

```c
enum json_type { JSON_ERROR = 1, JSON_DONE, JSON_OBJECT, JSON_OBJECT_END,
                 JSON_ARRAY, JSON_ARRAY_END, JSON_STRING, JSON_NUMBER,
                 JSON_TRUE, JSON_FALSE, JSON_NULL };
```

Note `(enum json_type)0` is not an enumerator but *is* used internally as the
"nothing buffered" sentinel in `json_stream.next`, so it is a real inhabitant.

- **Open/close:** `json_open_buffer`, `json_open_string`, `json_open_stream`
  (`FILE *`), `json_open_user` (callbacks), `json_close`
- **Configuration:** `json_set_allocator`, `json_set_streaming`
- **Events:** `json_next`, `json_peek`, `json_reset`, `json_skip`, `json_skip_until`
- **Data:** `json_get_string` (pointer + length), `json_get_number` (`double`)
- **Position:** `json_get_lineno`, `json_get_position`, `json_get_depth`,
  `json_get_context`, `json_get_error`
- **Raw source:** `json_source_get`, `json_source_peek`, `json_isspace`

Findings that shaped the design:

- **`FILE *` dependency** is confined to two functions (`stream_get`,
  `stream_peek`) using `fgetc`/`ungetc`. Everything else is source-agnostic
  through two function pointers in `json_source`.
- **Custom allocator** is a struct of three C function pointers, settable after
  open. Allocation failure is a supported, reachable scenario.
- **Numbers** are kept as the raw lexeme in the token buffer; `json_get_number`
  is `strtod` over it. So the raw text is available for precision, and `strtod`'s
  full grammar is observable.
- **String storage** is one growable buffer, initially 1024 bytes, doubling.
  `string_fill` **includes the trailing NUL**, so `json_get_string`'s length for
  `"v"` is 2, not 1. Easy to get wrong.
- **Embedded NUL** is explicitly supported in strings — it is the library's
  headline argument against other C JSON parsers.
- **Positions:** `position` counts bytes consumed; `lineno` counts newlines the
  whitespace skipper walked over, so it lags the token end.
- **Reset** clears the stack, `ntokens` and the error flag — but deliberately
  *not* the buffered peek or the token buffer. Both are observable.
- **Depth limit:** `PDJSON_STACK_MAX` exists but is undefined by default, so
  nesting is bounded only by memory. The stack grows 4 frames at a time.
- **Errors latch:** only the first error is recorded; later ones do not overwrite.
- **UTF-8:** input is validated with an explicit table; `\uXXXX` escapes are
  decoded and re-encoded, and surrogate pairs are combined.

## License

Unlicense — public domain dedication, no conditions, no copyleft, no attribution
requirement. Compatible with anything. Attribution is given anyway.
Full analysis in `LICENSES.md`.

## ABI feasibility

**The critical question**, because `struct json_stream` is fully declared in the
public header and upstream's own tests declare it *by value on the stack*:

```c
struct json_stream json[1];
```

So a drop-in replacement must reproduce the layout exactly, not just the
function signatures. Measured from the pinned header on this target:

```
sizeof(struct json_stream)  = 272   alignof = 8
sizeof(struct json_source)  = 48    alignof = 8
sizeof(struct json_allocator) = 24  alignof = 8
sizeof(enum json_type)      = 4     unsigned
```

Verified feasible before writing the parser: a Zig `extern struct` reproduces all
14 field offsets, the union layout, and the enum representation exactly. Proof
strategy chosen — two probes emitting the same table, one from the C compiler's
reading of the header, one from the Zig declarations, diffed field by field.

**Can the original tests link against a Zig artifact without changing their
meaning?** Yes, and this was confirmed in Phase 3 before committing to the port:
`tests/tests.c` compiled in place and linked against `libpdjson.a` alone,
producing `18 pass, 0 fail`. Only the link line differs from a C build.

## Likely hard parts

Identified up front; all of these turned out to be real.

1. **`char` signedness in `buffer_peek`.** The original reads input through a
   `const char *`, so on this target byte `0xFF` becomes `-1` = `EOF`. Reproducing
   this faithfully *and portably* needs `c_char`, not a hard-coded choice. Became
   upstream issue #37 and decisions D-05/D-07.
2. **Byte-exact diagnostics.** `errmsg` is a public field. `%c` converts through
   `unsigned char`, and `%c` with 0 embeds a NUL that truncates what a C caller
   sees. Became D-04.
3. **`strtod` semantics.** Not just JSON numbers: the API permits calling
   `json_get_number` after a string event, exposing leading whitespace, `inf`,
   `nan(...)`, hex floats and the longest-valid-prefix rule. Locale dependence is
   already an open upstream issue (#27). Became D-03.
4. **Allocation-failure paths.** `json_set_allocator` is public, so OOM is
   reachable. Needs deterministic failure injection to test. This is where both
   the port's memory-safety divergence and upstream issue #36 came from.
5. **`strchr` on a NUL argument.** C's `strchr` matches the terminating NUL, and
   the number lexer relies on this by accident for input like `1\x00`. Easy to
   lose when porting to a slice search.
6. **Not accidentally fixing bugs.** The most dangerous failure mode: a port that
   silently improves on its reference cannot claim equivalence.

## Kill criteria

Defined before starting, so the decision could not be rationalised afterwards:

1. License not permissive → **not met** (Unlicense).
2. Upstream tests do not run reproducibly → **not met** (18/18, deterministic).
3. A meaningful pre-existing Zig port exists → **not met** (only C build wrappers).
4. A standalone Zig implementation is not feasible → **not met** (ABI spike
   passed before implementation).
5. Source out of scope — too large, or needing a runtime → **not met**
   (990 lines, libc only).
6. No credible oracle strategy → **not met** (the parser is deterministic and
   fully observable through its header, so transcripts work).

None met. The predefined pivot to `getdnsapi/yxml` was not required.

## Recommendation

**Proceed with pdjson.** It is unusually well suited to a proof-first port:

- Small enough to reimplement completely rather than partially.
- Deterministic and fully observable through a public header, so behavioural
  transcripts are a *complete* equivalence check rather than a sampling one.
- A public struct layout, which turns "drop-in replacement" into something
  mechanically checkable rather than a claim.
- A memory-unsafe parser for untrusted input with a heap pointer and two indices
  that must stay consistent — exactly the migration case Zig addresses, and
  exactly where the bugs turned out to be.

The main risk is the opposite of the usual one: not that the port will be wrong,
but that it will be quietly *better* than the original and call that equivalence.
The mitigation is to make fault attribution mechanical (D-16) and to test the
harness itself with injected defects (D-17).

## Blockers

**Devfolio MCP is not connected.** `ToolSearch` returns no Devfolio tools, and the
session reports the server as requiring an OAuth flow that cannot run
non-interactively. The submission form therefore could not be inspected
programmatically. A complete draft is prepared offline in
`docs/devfolio-submission.md`, with the exact remaining manual steps recorded.
