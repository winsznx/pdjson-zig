# Upstream defect: `json_get_number()` reads uninitialised bytes after a partial token

**Project:** [skeeto/pdjson](https://github.com/skeeto/pdjson)
**Pinned commit:** `78fe04b820dc8817f540bdd87fb22887e0ef3981` (master, 2024-02-22)
**Location:** `json_get_number()`, `pdjson.c:871-875`
**Severity:** uninitialised read; makes a public accessor nondeterministic
**Status:** reported upstream — [#38](https://github.com/skeeto/pdjson/issues/38)
**Found by:** the C oracle's own determinism gate, running on x86-64 Linux in CI

## Summary

`read_number()` and `read_string()` push the terminating NUL only when the token
completes. A token that fails part way — input `-` is enough — leaves the buffer
holding the bytes pushed so far and no terminator.

`json_get_number()` is `strtod()` over that buffer:

```c
double json_get_number(json_stream *json)
{
    char *p = json->data.string;
    return p == NULL ? 0 : strtod(p, NULL);
}
```

so `strtod` keeps reading until it finds a NUL somewhere in the uninitialised
remainder of the 1 KiB `init_string()` allocation. The returned value is
composed partly of bytes the parser never wrote.

## How it was found

Not by reading the code, and not by the differential comparison — by the
**determinism gate that exists to keep the comparison honest**.

`scripts/oracle-determinism.sh` runs each transcript producer five times over
every fixture in five modes and requires byte-identical output every time. The
premise of this whole project is that two implementations agreeing means
something; that is only true if each is reproducible on its own.

On macOS it passed. In CI on x86-64 Linux it failed:

```
FAIL: c transcripts differ between runs (mode next, run 1)
```

Reproduced in a Debian container, the diff was a single field on a single
fixture (`num-lone-minus`, whose entire content is `-`):

```
< {"seq":0,"op":"peek","event":"ERROR","tok":"2d","toklen":1,"num":"0000000000000000", ...}
> {"seq":0,"op":"peek","event":"ERROR","tok":"2d","toklen":1,"num":"bff0000000000000", ...}
```

`0.0` on one run, `-1.0` on the next. The parser had written one byte, `-`, and
`strtod` read whatever followed it in the heap — which on the second run was a
leftover `1` from a previous stream's token buffer.

## Deterministic demonstration

The natural symptom is nondeterministic, which makes it unconvincing on its own.
`tests/upstream-bugs/repro_uninit_number.c` pins it down using a custom
allocator — `json_set_allocator` is documented public API — that fills fresh
blocks with `'9'`:

```
$ cc -std=c99 -I upstream/pdjson \
     -o repro tests/upstream-bugs/repro_uninit_number.c upstream/pdjson/pdjson.c
$ ./repro

B. allocator that fills fresh blocks with '9'
  input -       string_fill=1  bytes=2d    json_get_number() = -inf
  input "12     string_fill=2  bytes=3132  json_get_number() = inf

Expected: 0 and 12 -- strtod should see only "-" and "12".
Actual:   -inf and inf
```

`json_get_string()` reports `string_fill` of 1 and 2, so the parser knows
exactly how much it wrote. `json_get_number()` ignores that and reads ~1023
bytes further.

## Scope, stated carefully

**Demonstrated:** a read of uninitialised bytes *inside* the allocation, and the
resulting nondeterminism across runs.

**Not demonstrated:** a read past the end of the allocation. Whether `strtod`
runs off the end depends on whether a NUL happens to appear in the remaining
bytes. Running the reproducer under ASan with *only* `json_get_number()` called
reports nothing. An earlier version of this analysis claimed an out-of-bounds
read on the strength of an ASan report that turned out to be triggered by the
reproducer's own diagnostic `strlen`, not by the library — corrected here.

Any caller that inspects the number after a parse error is affected, and that is
a reasonable thing to do: `json_get_string()` returns the partial token with a
correct length, so `json_get_number()` looks equally usable.

## Suggested fix

Bound the conversion by `string_fill` so it can only see written bytes:

```diff
 double json_get_number(json_stream *json)
 {
     char *p = json->data.string;
-    return p == NULL ? 0 : strtod(p, NULL);
+    if (p == NULL)
+        return 0;
+    if (memchr(p, '\0', json->data.string_fill) == NULL)
+        return 0;
+    return strtod(p, NULL);
 }
```

Or push the terminator on the error paths too.

## What this port does

`src/parser.zig` scans only the region the parser wrote:

```zig
pub fn getNumber(self: *Stream) f64 {
    const s = self.data.string orelse return 0;
    const written = s[0..self.data.string_fill];
    const end = std.mem.indexOfScalar(u8, written, 0) orelse written.len;
    return strtod_mod.value(written[0..end]);
}
```

Deterministic, and it reads nothing the parser did not write. Where the original
is well defined — a NUL inside the written region — both find the same
terminator and agree.

## Effect on the equivalence claim

`json_get_number()` has no defined value when the token buffer holds no NUL
within the written region, so there is nothing there for the port to be
equivalent to. Both transcript producers now record `"num": null` in exactly
that case, using a rule computed from the public API alone
(`memchr(token, 0, string_fill) != NULL`), so the identical test is applied on
both sides and cannot mask a real difference.

This is the same principle already applied to the bytes of `errmsg` past its NUL
(DECISIONS.md D-13): values that are indeterminate in the original are excluded
from comparison and the exclusion is documented, rather than compared and
explained away.

Recorded as decision **D-19**. Regression coverage in
`tests/port/regressions.zig`; the fixture is `num-lone-minus`, which was already
in the corpus and had been passing only because macOS happened to hand back
zeroed pages.
