# Upstream defect: byte `0xFF` is read as end-of-input by the memory-buffer source

**Project:** [skeeto/pdjson](https://github.com/skeeto/pdjson)
**Pinned commit:** `78fe04b820dc8817f540bdd87fb22887e0ef3981` (master, 2024-02-22)
**Location:** `buffer_peek()`, `pdjson.c:94-100`
**Severity:** wrong diagnostic, frozen byte position, and a documented API that
cannot make progress. Not a parser-acceptance bug.
**Status:** reported upstream — see `artifacts/upstream-issues.json` for the URL
**Found by:** hand-written edge-case corpus, confirmed by differential transcripts

## Summary

`buffer_peek()` reads the input through a `const char *`:

```c
static int buffer_peek(struct json_source *source)
{
    if (source->position < source->source.buffer.length)
        return source->source.buffer.buffer[source->position];
    else
        return EOF;
}
```

Whether `char` is signed is implementation-defined. On every mainstream desktop
and server target — x86-64 Linux, macOS, Windows, and Apple arm64 — it is
signed, so the byte `0xFF` widens to `-1`, which is `EOF`.

The `FILE *` source does not have this problem, because `fgetc()` is specified to
return the byte "converted to an int" (i.e. as `unsigned char`), or `EOF`:

```c
static int stream_get(struct json_source *source)
{
    int c = fgetc(source->source.stream.stream);
    ...
```

So the same bytes parse differently depending only on which `json_open_*` the
caller used.

## Three observable consequences

Measured on Apple clang 21, arm64 macOS, where `char` is signed. Full output
from `tests/upstream-bugs/repro_0xff_eof.c`:

```
char is signed on this build
buffer source : event=1 position=1 error=unterminated string literal
FILE*  source : event=1 position=2 error=invalid UTF-8 character
json_source_get x5 : -1(pos=0) -1(pos=0) -1(pos=0) -1(pos=0) -1(pos=0)   <- expected the three 0xFF bytes to be consumed
```

**1. The two documented sources disagree.** For input `"\xFF"` the buffer source
reports `unterminated string literal`; the `FILE *` source reports
`invalid UTF-8 character`. The README presents these as interchangeable ways to
open the same input.

**2. `json_get_position()` stops advancing.** `buffer_get()` only increments
`position` when the byte is not `EOF`:

```c
static int buffer_get(struct json_source *source)
{
    int c = source->peek(source);
    if (c != EOF)
        source->position++;
    return c;
}
```

A caller using the reported position to locate the problem in the input is sent
to the wrong offset — `1` instead of `2` above.

**3. `json_source_get()` cannot advance past the byte.** This is the API the
README documents for validating separators between streamed values. Its example
loop happens to be safe because it stops on any non-whitespace, but the natural
generalisation — scan forward until a delimiter — never terminates, because the
call returns `-1` forever and `position` never moves. That is the third line of
output above: five calls, no progress.

## Scope, stated plainly

`0xFF` is never valid UTF-8, so this does **not** make the parser accept invalid
input. Every affected input is rejected either way. What changes is *which*
error is reported, *where* the parser says it happened, and whether a documented
accessor can move forward. That is why this is filed as a correctness and
API-contract defect rather than a security issue.

The same reasoning applies to `0xFE` and every other high byte on unsigned-`char`
targets — but only `0xFF` collides with `EOF`, so only `0xFF` is affected. The
reproducer includes `0xFE` as a control: it behaves identically on both sources.

## Reproducer

`tests/upstream-bugs/repro_0xff_eof.c` — public API only.

```sh
cc -std=c99 -I upstream/pdjson \
   -o repro tests/upstream-bugs/repro_0xff_eof.c upstream/pdjson/pdjson.c
./repro
```

## Suggested fix

One cast, in one place:

```c
 static int buffer_peek(struct json_source *source)
 {
     if (source->position < source->source.buffer.length)
-        return source->source.buffer.buffer[source->position];
+        return (unsigned char)source->source.buffer.buffer[source->position];
     else
         return EOF;
 }
```

This makes the buffer source agree with the `FILE *` source on every input, and
is what the rest of the file already assumes: `utf8_seq_length()` and
`is_legal_utf8()` both cast to `unsigned char` before inspecting bytes.

Relatedly, `read_utf8()` fills its continuation bytes without checking for `EOF`:

```c
    for (i = 1; i < count; ++i)
        buffer[i] = json->source.get(&json->source);
```

A short read stores `(char)EOF`, which `is_legal_utf8()` happens to reject, so
the outcome is correct but the diagnostic is `invalid UTF-8 text` rather than an
unterminated-input message. Fixing `buffer_peek` does not change that; it is
noted here only because the two interact.

## What this port does

The port **reproduces this deliberately**, because its claim is behavioural
equivalence with the pinned original, and silently fixing a bug would break that
claim in the direction that is hardest to notice.

It is reproduced portably rather than by hard-coding one platform's answer.
`c_char` in Zig carries the target's actual `char` signedness, so the port makes
the same choice the C compiler would make on the same target:

```zig
fn byteAsC(byte: u8) c_int {
    if (fix_0xff) return byte;
    return @as(c_char, @bitCast(byte));
}
```

The corrected behaviour is available as an opt-in build flag:

```sh
zig build -Dfix-0xff=true
```

which makes the buffer source report bytes as unsigned. That build **diverges
from upstream on `0xFF` input by design**, and the differential harness reports
it as a divergence — which is how the mutant `buffer-peek-unsigned` in
`scripts/mutation-test.py` demonstrates that the harness would catch this class
of change. Recorded as decisions **D-05** and **D-07** in `DECISIONS.md`, with
regression coverage in `tests/port/regressions.zig` and fixtures `ff-bare`,
`ff-in-string`, `ff-after-number`, `ff-in-array`, `ff-run`, `fe-bare`.
