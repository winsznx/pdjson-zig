# Upstream defect: `json_get_context()` reads a stack slot `push()` never allocated

**Project:** [skeeto/pdjson](https://github.com/skeeto/pdjson)
**Pinned commit:** `78fe04b820dc8817f540bdd87fb22887e0ef3981` (master, 2024-02-22)
**Location:** `pdjson.c:912`, reached from `push()` at `pdjson.c:52`
**Severity:** null-pointer dereference or out-of-bounds read, through the public API
**Status:** reported upstream — see `artifacts/upstream-issues.json` for the URL
**Found by:** differential testing under allocation-failure schedules

## Summary

`push()` increments `json->stack_top` *before* it grows the container stack.
When the allocation fails it reports `"out of memory"` and returns `JSON_ERROR`,
but leaves `stack_top` pointing at a slot that was never allocated — and, on the
very first push, pointing into a stack that is still `NULL`.

`json_get_context()` then indexes that slot with no null check and no bounds
check:

```c
enum json_type json_get_context(json_stream *json, size_t *count)
{
    if (json->stack_top == (size_t)-1)
        return JSON_DONE;

    if (count != NULL)
        *count = json->stack[json->stack_top].count;   /* pdjson.c:912 */

    return json->stack[json->stack_top].type;
}
```

The `stack_top == (size_t)-1` guard is the only protection, and a failed push
has already moved `stack_top` past it.

## Why this is reachable, not theoretical

`json_set_allocator()` is a documented part of the public API, and the README
presents it as the mechanism for environments where allocations should not come
from the system allocator. Those are exactly the environments where an
allocation can fail. The library's stated goals include a "minimal memory
footprint" for processing arbitrarily large input, which points at the same
constrained settings.

Nothing in `pdjson.h` or the README states a precondition on calling
`json_get_context()` after an error. A caller reporting *where* parsing stopped
is doing the obvious thing.

## Two manifestations

Both come from the same line, distinguished only by when the allocator gave out.

**Null dereference** — the first push fails, so `stack` is still `NULL` while
`stack_top` has advanced to 0:

```
pdjson.c:912:18: runtime error: member access within null pointer of type 'struct json_stack'
SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior pdjson.c:912:18
==...==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000008
    #0 ... in json_get_context pdjson.c:912
```

**Out-of-bounds read** — the stack grows in blocks of `PDJSON_STACK_INC` (4).
With the first block allocated and the fifth push failing, `stack_top == 4` and
`stack_size == 4`, so the read is one element past the allocation:

```
SUMMARY: AddressSanitizer: heap-buffer-overflow pdjson.c:912 in json_get_context
```

In a build without sanitizers the second form does not crash. It silently
returns whatever bytes follow the allocation, so `json_get_context()` reports a
container type and count that were never parsed.

## Reproducer

`tests/upstream-bugs/repro_oom_stack.c` — public API only, no internal headers.

```sh
cc -std=c99 -g -fsanitize=address,undefined -I upstream/pdjson \
   -o repro tests/upstream-bugs/repro_oom_stack.c upstream/pdjson/pdjson.c
./repro
```

It runs two scenarios: an allocator that fails immediately (null dereference)
and one that permits a single allocation before failing (out-of-bounds read).

## How it was found

Not by reading the code. The differential harness in this repository runs every
input through both implementations under nine drive modes, four of which inject
deterministic allocation failures (`oom:0`, `oom:1`, `oom:2`, `oom:5`). When the
two transcripts disagreed, the harness re-ran the case against an ASan+UBSan
build of the pinned original to decide which side was at fault
(`scripts/differential.py`). All 43 anomalies across the corpus resolved to this
one line:

```
  32  SUMMARY: AddressSanitizer: SEGV pdjson.c:912 in json_get_context
  32  SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior pdjson.c:912:18
  11  SUMMARY: AddressSanitizer: heap-buffer-overflow pdjson.c:912 in json_get_context
```

## Suggested fix

Either restore `stack_top` when the growth fails:

```c
 static enum json_type
 push(json_stream *json, enum json_type type)
 {
     json->stack_top++;
     ...
     if (json->stack_top >= json->stack_size) {
         struct json_stack *stack;
         size_t size = (json->stack_size + PDJSON_STACK_INC) * sizeof(*json->stack);
         stack = (struct json_stack *)json->alloc.realloc(json->stack, size);
         if (stack == NULL) {
             json_error(json, "%s", "out of memory");
+            json->stack_top--;
             return JSON_ERROR;
         }
```

or make the accessor defensive, which also covers any future path that leaves
the two fields inconsistent:

```c
 enum json_type json_get_context(json_stream *json, size_t *count)
 {
-    if (json->stack_top == (size_t)-1)
+    if (json->stack_top == (size_t)-1 || json->stack == NULL
+        || json->stack_top >= json->stack_size)
         return JSON_DONE;
```

Restoring `stack_top` additionally makes `json_get_depth()` report the depth
that was actually reached rather than one past it.

## What this port does

`src/parser.zig` routes every container-stack access through one accessor:

```zig
fn currentFrame(self: *Stream) ?*abi.Stack {
    if (self.stack_top == abi.stack_empty) return null;
    if (self.stack_top >= self.stack_size) return null;
    const stack = self.stack orelse return null;
    return &stack[self.stack_top];
}
```

`json_get_context()` returns `JSON_DONE` in the state where the original reads
unallocated memory. `json_get_depth()` still returns `stack_top + 1`, matching
the original's observable value, because that read is well defined in C — only
the stack indexing is not.

This is a deliberate divergence from the original on inputs where the original
has no defined behaviour, recorded as decision **D-06** in `DECISIONS.md`. The
differential harness counts these cases separately (`upstream_ub`) rather than
folding them into the equivalence result, so the headline "0 divergences" figure
covers only inputs where the original is well defined. Regression coverage is in
`tests/port/regressions.zig` and `tests/port/allocator_failure.zig`, the latter
walking every failure point for twelve inputs.
