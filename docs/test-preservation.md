# Test preservation

The claim is that the upstream test suite runs **unmodified** against the Zig
library. This document says exactly what that means, and how it is enforced
rather than promised.

## What "unmodified" means here

The upstream test sources are compiled **in place**, from
`upstream/pdjson/tests/`. No copy is made into this repository's own test tree,
no line is edited, and no wrapper is inserted between the test and the library.

The only difference between a C build and a Zig build of the same test is the
link line:

```sh
# against the original
cc -std=c99 -pedantic -Wall -Wextra -Wno-missing-field-initializers -O2 \
   -o build/tests_c   upstream/pdjson/tests/tests.c upstream/pdjson/pdjson.c

# against the port
cc -std=c99 -pedantic -Wall -Wextra -Wno-missing-field-initializers -O2 \
   -o build/tests_zig upstream/pdjson/tests/tests.c zig-out/lib/libpdjson.a
```

Same source file, same flags, same include path. `tests.c` does
`#include "../pdjson.h"`, which resolves to the pinned upstream header — not to
`include/pdjson.h`, though those two files are byte-identical anyway.

Both commands are recorded in `artifacts/original-test-report.json` under
`build_commands`, so this is checkable rather than described.

## How it is enforced

`scripts/verify-upstream-hashes.sh` compares every file in `upstream/pdjson/`
against SHA-256 digests recorded in `artifacts/upstream-manifest.json`. It fails
on:

- a modified file,
- a file added inside the pinned tree,
- a file removed from it.

It runs as step 2 of `make verify`, before anything is built, and as a standalone
first job in CI so that a drifted baseline fails loudly rather than quietly
changing what downstream steps are measuring.

Both failure modes are demonstrable:

```sh
$ echo "/* tampered */" >> upstream/pdjson/tests/tests.c
$ sh scripts/verify-upstream-hashes.sh
FAIL drift:   tests/tests.c
     expected 8e4fcf075aa48582573e4816c17ec2f23a570675725840ea933981e364770a40
     actual   59cf35b2df02f0abbf8b82b20be2f463cb247b3cf856760daa6751303c948e88
upstream hash verification FAILED (1 problem(s), 9 file(s) checked)

$ touch upstream/pdjson/tests/sneaky.c
$ sh scripts/verify-upstream-hashes.sh
FAIL untracked files inside pinned upstream tree:
     tests/sneaky.c
```

CI additionally runs `git diff --exit-code -- upstream/pdjson`, so an
uncommitted local edit cannot pass either.

## Adaptations

**There are none.** No test was skipped, rewritten, wrapped, marked unsupported,
or split. `artifacts/original-test-report.json` records
`assertions_skipped: 0` and `assertions_unsupported: 0`, and the release gate
fails if either becomes non-zero.

If an adaptation had been necessary, the brief for this project required it to be
isolated, minimal, hashed and explained. That mechanism was never needed, so no
adaptation layer exists to inspect.

## The three upstream programs, used differently

`upstream/pdjson/tests/` contains three programs, and only one of them is an
assertion suite. Treating all three as "tests" would overstate the coverage.

### `tests.c` — 18 assertions

A real test suite. It builds `struct json_stream` by value on the stack, drives
`json_next`, and compares each event type and token string against an expected
sequence. Its own `PASS`/`FAIL` output lines are parsed into the per-test report,
so the result is the suite's own verdict, not a re-derived one.

```
18/18 passed, 0 failed, 0 skipped, 0 unsupported
```

Per-test entries with C and Zig status side by side are in
`artifacts/original-test-report.json` under `tests`.

What it covers: literals, strings with escapes, one object, one array, streaming
with reset, custom separator validation, one truncation case, and five Unicode
cases including surrogate pairs and three invalid-surrogate cases.

What it does **not** cover: byte positions, line numbers, depth, container
context, `json_skip`, `json_get_number`, the allocator hooks, invalid UTF-8,
control characters, and every allocation-failure path. That gap is the entire
reason the differential corpus exists — passing 18 assertions is necessary, not
sufficient.

### `stream.c` and `pretty.c` — used differentially

These are tools, not assertion suites: `stream.c` prints the event stream for
stdin, `pretty.c` re-indents a document. Neither asserts anything, so "it passes"
would be meaningless.

They are used as differential oracles instead. Each is built **twice** — once
against the pinned C, once against Zig — and run over all 215 fixtures with
their output compared byte for byte, including exit status and stderr.

```
stream.c differential over 215 fixtures: 0 mismatches
pretty.c differential over 215 fixtures: 0 mismatches
```

`pretty.c` earns its place here: it drives `json_peek` and `json_get_depth`
heavily, which `tests.c` barely touches, and its indentation output is a direct
function of depth. A depth off-by-one that the assertion suite would miss shows
up immediately as a whitespace diff — and does: the `depth-off-by-one` mutant in
`scripts/mutation-test.py` is caught.

## What this does and does not prove

It proves the Zig library is a **drop-in replacement at the source and link
level** for the programs upstream ships: they compile against the same header,
link against only the Zig archive, and behave identically.

It does not prove behavioural equivalence in general. 18 assertions over ~990
lines of parser is thin coverage, and the two tools exercise a wide but shallow
path. The transcript differential (1,935 fixed-corpus comparisons + 1,590
JSONTestSuite comparisons + the fuzz session) is what carries that claim; the
upstream suite is the part a reader can check in ten seconds without trusting
any of this project's own tooling.
