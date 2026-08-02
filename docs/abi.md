# The C ABI contract

`struct json_stream` is not opaque. The original `pdjson.h` spells out every
field, and upstream's own tests declare it **by value on the stack**:

```c
struct json_stream json[1];
json_open_string(json, "[1,2,3]");
```

A caller compiled against that header hard-codes the struct's size and every
field offset into its own code. A drop-in replacement therefore has to reproduce
the layout exactly — matching the function signatures is not enough. On this
target that means **272 bytes, 8-byte aligned**, with `errmsg` starting at 144.

[`src/abi.zig`](../src/abi.zig) is written by hand rather than translated from
the header. That is deliberate: a machine translation would make the match true
by construction and prove nothing about whether the port understood the layout.
The cost of the choice is that the two can drift, so this is the most heavily
checked part of the repository.

## Four checks, in order of how early they fail

| # | Check | Runs when | Covers |
| --- | --- | --- | --- |
| 1 | [`src/abi_contract.zig`](../src/abi_contract.zig) | every `zig build` | the build target, if it is in the recorded ABI class |
| 2 | [`scripts/abi-check.sh`](../scripts/abi-check.sh) | `make abi`, `make verify` | the host, by executing two probes |
| 3 | [`scripts/abi-cross-check.sh`](../scripts/abi-cross-check.sh) | `make abi`, `make verify` | 6 further targets, at compile time |
| 4 | [`scripts/abi-contract-negative.sh`](../scripts/abi-contract-negative.sh) | `make abi`, `make verify` | whether check 1 can fail at all |

### 1. The compile-time contract

Checks 2 and 3 are both *external*: they run from a script a consumer of this
repository might never invoke. A layout drift would produce a library that
builds and installs cleanly and only fails later, somewhere else.

So the layout is also baked into the build. A C probe that includes **only** the
pinned header emits what the C compiler says that header means:

```sh
sh scripts/abi-generate.sh          # regenerate src/abi_generated.zig
sh scripts/abi-generate.sh --check  # fail if the committed copy is stale
```

The result is [`src/abi_generated.zig`](../src/abi_generated.zig) — 27 field
offsets and sizes, 11 enumerators, and 7 struct size and alignment values. It is
committed, so it can be read without running anything.
[`src/abi_contract.zig`](../src/abi_contract.zig) then asserts `src/abi.zig`
against every entry at `comptime`, and `src/root.zig` imports it. A drift now
fails `zig build`, naming the field:

```
error: C ABI drift: offsetof(struct json_stream, ntokens) is 72 in the pinned
pdjson.h but 64 in src/abi.zig. Fix src/abi.zig, or if the header itself
changed, re-run scripts/abi-generate.sh.
```

Field **sizes** are asserted as well as offsets. Offsets alone are not enough: a
field that shrinks at the end of a struct can have the loss absorbed by padding,
leaving `sizeof` and every offset identical. That exact case slipped past an
earlier version of the cross-target check, which is why both are recorded now.

Nested members and union arms are covered too, by dotted path —
`json_source.source.user.peek` is asserted, not just `json_source.source`.

**Two things the generated file deliberately does not contain.** The compiler
triple, and `char` signedness. Neither is a layout property, and both differ
between hosts that share a layout; including them would make macOS CI call the
Linux copy stale and vice versa. The file is keyed by *ABI class* — pointer and
`size_t` width — so every LP64 host regenerates byte-identical content.

The contract asserts only when the build target is in that class. A 32-bit
target skips it and is covered by check 3 instead;
[`zig build diagnose`](#zig-build-diagnose) reports which case applies. That
guard is itself tested: check 4 confirms a 32-bit build passes through a
deliberately corrupted table, so the guard genuinely disengages rather than the
assertions being dead everywhere.

### 2. The host check

Two probes emit the same table — `tools/abi_probe_c.c` compiled against the
pinned header, and `tools/abi_probe.zig` computed from `src/abi.zig` — and the
outputs are diffed line by line. Beyond the layout it checks three more things:

- **The contract is current.** A stale `abi_generated.zig` would have the
  build asserting an outdated shape and still passing.
- **Symbols, as a set.** Every `PDJSON_SYMEXPORT` declaration in the pinned
  header is compared against `nm -g` on the archive, in both directions. A count
  would pass while one symbol went missing and another appeared. Both sides are
  required to be non-empty, because two empty sets compare equal.
- **Linkability.** `tests/original/abi_consumer.c` includes the pinned header,
  declares `struct json_stream` by value, links against *only* the Zig archive,
  and runs.

Artifacts: [`artifacts/abi/abi-report.json`](../artifacts/abi/abi-report.json),
[`c-layout.json`](../artifacts/abi/c-layout.json),
[`zig-layout.json`](../artifacts/abi/zig-layout.json),
[`exported-symbols.txt`](../artifacts/abi/exported-symbols.txt).

That the archive contains no upstream parser code is a separate check,
[`scripts/verify-no-c-linkage.sh`](../scripts/verify-no-c-linkage.sh), reported
in [`artifacts/linkage-report.json`](../artifacts/linkage-report.json).

### 3. The cross-target check

Both executed checks run on LP64 hosts only — arm64 macOS and x86-64 Linux in
CI — so neither says anything about what happens when the pointer size changes.
`scripts/abi-cross-check.sh` reads Zig's layout for a target at compile time and
feeds those numbers back to the C compiler as `_Static_assert` over the pinned
header for the same target. Six targets, spanning 32- and 64-bit, ARM, x86,
RISC-V and Windows. Neither side executes.

Caveat, stated because it matters: both sides go through the Zig toolchain's
clang, so this shows the Zig declarations agree with *that* C frontend. It is
not a claim about gcc on those targets. Check 2 is what covers the host
compiler.

### 4. Proving check 1 can fail

An assertion that is silently vacuous — a mistyped guard, an empty table, a
comparison that always holds — looks exactly like an assertion that is
satisfied. So ten layout drifts are injected into a throwaway copy of the tree
and the build is required to stop:

```
$ sh scripts/abi-contract-negative.sh
Drifting the recorded C layout (simulates the header changing under us):
CAUGHT  field offset moved: json_stream.ntokens
CAUGHT  field size shrank: json_stream.errmsg
CAUGHT  nested union arm moved: json_source.source.user.peek
CAUGHT  struct size changed: sizeof(json_stream)
CAUGHT  struct alignment changed: alignof(json_stream)
CAUGHT  enumerator renumbered: JSON_NULL

Drifting the port's own declarations (the direction that actually happens):
CAUGHT  error buffer shortened in src/abi.zig
CAUGHT  counter narrowed: stack_top usize -> c_uint
CAUGHT  enum tag signedness flipped: c_uint -> c_int
CAUGHT  field inserted: extra member before ntokens

CONTROL builds clean with the tree unmodified
DEFERRAL a 32-bit target builds through the same bad table, confirming the
         ABI-class guard disengages off-class instead of asserting nonsense

contract negative test: 10 detected, 0 missed
```

The control matters as much as the cases: without it, a permanently broken build
would score 10/10.

Artifact:
[`artifacts/abi/contract-negative.json`](../artifacts/abi/contract-negative.json).

**Known blind spot, recorded rather than glossed over.** Two adjacent fields of
the same width swapped with each other leave every offset and every size
unchanged, so no layout table — this one, `_Static_assert`, or otherwise — can
see it. `next` and `flags` are exactly such a pair. That case is covered by
behaviour instead: the differential compares `json_get_lineno`,
`json_get_depth`, `json_get_context` and the error text on every one of the
6,104 corpus records, and reading a flag word as an event type does not survive
that.

## `zig build diagnose`

Two decisions this build makes are invisible in the source and change observable
behaviour. `zig build diagnose` reports both:

```
$ zig build diagnose
pdjson-zig build diagnostics

  target                aarch64-macos-none
  optimize              ReleaseSafe

0xFF / EOF compatibility (upstream issue #37)
  C `char` on target    signed
  mode selected         as-C (0xFF widens through c_char, reproducing upstream)
  build option used     -Dfix-0xff=false  (default)
  peek of byte 0xFF     -1
  effect                0xFF is indistinguishable from EOF, exactly as the
                        original library behaves on this target. A document
                        containing 0xFF truncates rather than erroring.
                        Build with -Dfix-0xff=true to separate them, at the
                        cost of deliberately diverging from upstream.

Compile-time C ABI contract (src/abi_contract.zig)
  status                active: 27 field offsets and sizes, 11 enumerators,
                        and 7 struct size/alignment values were asserted
                        against the pinned header while building this binary
  sizeof(json_stream)   272

Other build options
  -Dstack-max           0  (unlimited, matching upstream)
```

`zig build diagnose -- --json` emits the same facts as
`pdjson-zig/diagnose@1` for a machine to read.

The `peek of byte 0xFF` line is *observed*, not restated: the tool feeds a
one-byte buffer containing `0xFF` through `parser.bufferPeek` and prints what
comes back. On a target where `char` is unsigned it prints 255 and says there is
nothing to reconcile, without any of this being hard-coded.

**Why a build step and not `@compileLog`.** Every fact here is comptime-known,
so `@compileLog` would surface it with far less machinery. It is the wrong tool:
`@compileLog` *fails the compilation it reports on*. A build could then be
described or produce a library, never both. Running a built executable reports
and exits 0.

## Regenerating after an upstream header change

The pinned header is read-only evidence and is not expected to move. If it ever
does:

```sh
sh scripts/fetch-upstream.sh          # re-pin, hashes recorded in artifacts/upstream-manifest.json
sh scripts/abi-generate.sh            # re-derive the contract from the new header
zig build                             # fails here, naming every field that moved
# fix src/abi.zig to match
make abi                              # host + cross + negative test
```

`include/pdjson.h` is byte-identical to the pinned upstream header and is
checked as such by `scripts/verify-upstream-hashes.sh`, so the public surface
cannot drift independently of it.
