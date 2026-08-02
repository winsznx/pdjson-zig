# Progress log

What was done, in order, with what each phase actually produced. Kept because
the order matters: several results only mean what they mean because of what came
before them.

## Phase 0 — source and feasibility lock

Environment inspected, Zig 0.16.0 installed (not previously present), upstream
cloned and pinned at `78fe04b`. Baseline established: the original builds warning-free
under `-Wall -Wextra -pedantic` and passes 18/18 of its own assertions, clean under
ASan+UBSan.

Searched GitHub repositories, code search and the web for existing Zig ports.
The only Zig hits are `build.zig` files in `urbit/vere` and its forks, which
compile the **vendored C source** — build wrappers, not rewrites. No prior Port
Mortem submission targets pdjson. No disqualifying prior art.

Six kill criteria were defined in advance and all evaluated: none met. The
predefined pivot to `getdnsapi/yxml` was not required.

Written up in [`phase-0-assessment.md`](phase-0-assessment.md).

**Blocker recorded here and unresolved:** the Devfolio MCP connection is not
available in this session, so the submission form could not be inspected.

## Phase 1 — provenance

`artifacts/upstream-manifest.json` (SHA-256 for all 9 files, categorised),
`scripts/verify-upstream-hashes.sh`, `LICENSES.md`, `.port-mortem.toml`.

The verifier was negative-tested before being trusted: modifying one byte of
`tests/tests.c` fails it, and adding a file inside the pinned tree fails it.

Upstream is carried as a committed verbatim copy with `.git` removed, so
verification works offline; `scripts/fetch-upstream.sh` re-clones at the pinned
commit and diffs, for independent confirmation. Reasoning in DECISIONS.md D-01.

## Phase 3 — ABI spike (run before the implementation)

Deliberately before Phase 2 and 4, because it was the one thing that could have
killed the whole approach: `struct json_stream` is fully declared in the public
header and upstream's own tests declare it **by value on the stack**.

Two probes emit the same layout table — one from the C compiler's reading of the
pinned header, one from the Zig declarations. They agreed on every offset, size,
alignment and enumerator (`sizeof == 272`, align 8, enum 4 bytes unsigned).

## Phase 4 — implementation

`src/abi.zig`, `src/parser.zig`, `src/errmsg.zig`, `src/strtod.zig`,
`src/c_api.zig`, `src/api.zig`.

First real milestone: upstream's `tests/tests.c`, compiled in place from the
pinned tree and linked against only `libpdjson.a`, printed `18 pass, 0 fail`.
Symbol inspection confirmed one Zig object, 22 exports, no C parser symbols, and
no `strtod` import.

## Phase 2 — transcript oracle

Two independent transcript producers, nine drive modes, a versioned NDJSON
schema. First comparison on a real document was byte-identical.

## Phase 6 — differential testing

142-fixture corpus × 9 modes. **The first run was not clean:** 32 crashes and 8
divergences.

All of them were the C side. That produced the most important design decision in
the project: fault attribution had to be **mechanical**, not editorial. The
harness now re-runs any disagreement against an ASan+UBSan build of the pinned
original and classifies by what the sanitizer says. All 43 anomalies resolved to
one line, `pdjson.c:912`.

## Phase 7 — upstream bugs

Two filed, both with minimal public-API reproducers, both checked against every
open and closed issue and PR for duplication first:

- [#36](https://github.com/skeeto/pdjson/issues/36) — `json_get_context()` reads
  an unallocated stack slot after a failed allocation. Found by the differential.
- [#37](https://github.com/skeeto/pdjson/issues/37) — byte `0xFF` is read as EOF
  by the buffer source, so it disagrees with the library's own `FILE *` source.
  Found by the hand-written edge-case corpus.

## Phase 5 — formal test harness

Per-test machine-readable report. `stream.c` and `pretty.c` promoted from
"unused" to differential oracles, built twice and compared byte for byte —
`pretty.c` in particular exercises `json_peek` and `json_get_depth`, which the
assertion suite barely touches.

## Harness self-test — mutation testing

This phase changed the corpus and caught two bugs in my own methodology.

- First sound run: **8/12 caught.** The four survivors were real gaps; the corpus
  grew 142 → 214 fixtures.
- Two earlier runs had scored a **false 12/12**, both by "catching" mutants on
  cases where the C original crashes or reads out of bounds. Comparability is now
  decided by the same sanitizer test used for divergence classification.
- Two mutants turned out to be **equivalent** (unobservable), and were replaced
  with ones that cross a reachable boundary.

Written up as DECISIONS.md D-17.

## Phase 8 — benchmarking

**First run reported the port 6.8× slower.** It was measuring a Debug build —
Zig's `standardOptimizeOption` returns Debug unless `--release` is passed. The
build now defaults to ReleaseSafe (D-10).

**Second finding: my explanation for the remaining gap was wrong.** I assumed the
null checks on the source callbacks were the cost, built a variant without them,
and measured 0% improvement. Profiling both binaries found the real causes;
fixing them moved `large-mixed` from 0.70× to 0.87×. Full account in
[`../bench/methodology.md`](../bench/methodology.md).

## Phase 6 (again) — JSONTestSuite

318 cases × 5 modes, 0 divergences. The pinned original is fully conforming
(95/95 must-accept, 188/188 must-reject), which is a fact about upstream rather
than about this port — and having an independent standards corpus is what lets
the two be told apart.

## Phase 6 (again) — the published fuzz session found a real bug in the port

At about 30 million cases, a one-ULP divergence in `json_get_number`.

The route is worth recording: a number token leaves its bytes in the buffer, a
following unterminated string starting `0x` errors, and `json_get_number()` then
reads `0x` plus the previous token's tail — a 19-hex-digit float.
`std.fmt.parseFloat` truncates rather than rounds hex floats past 53 bits of
mantissa.

Fixed by implementing hex-float conversion with correct rounding. A randomised
hex-float test added alongside the fix immediately caught a **second** edge in
the first version of that fix, in the subnormal range. Both are regression-tested
and the minimized 22-byte case is a committed fixture. DECISIONS.md D-18.

This is the entry that justifies the fuzzer existing.

## Phases 9–12 — pipeline, CI, README, claim ledger

`make verify` (16 steps), CI on Linux and macOS, release gate, `CLAIMS.json` with
21 claims each checked against a generated artifact.

The ledger immediately earned its place: it failed the build because the
**smoke** benchmark in `make verify` was overwriting the full benchmark artifact,
so a published figure would have silently become a single-workload sample. Smoke
runs now write to separate files.

## Phases 13–15 — submission material

Devfolio draft prepared offline (MCP unavailable — see the blocker above), demo
script with exact commands and expected output, and a technical write-up.

## Phases 16–17 — audit and publication

See [`final-audit.md`](final-audit.md) and [`final-status.md`](final-status.md).

## Recurring theme

Three times, a result that looked good was wrong, and in each case the thing that
caught it was an independent check rather than review:

1. The benchmark said 6.8× slower — it was measuring Debug.
2. Mutation testing said 12/12 — it was counting crashes as detections.
3. The differential said 0 divergences — until 30 million fuzz cases found one.

Every headline number in this project should be read with that in mind, which is
why each is tied to a regenerable artifact rather than to prose.
