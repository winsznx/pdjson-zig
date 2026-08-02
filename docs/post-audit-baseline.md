# Post-audit baseline

State of the repository at the point an external adversarial audit was received,
recorded before any of its recommendations were acted on. Everything below was
read out of the repository, the artifacts, or GitHub — none of it is restated
from memory.

**Commit:** `8d6b1fe` · **Tag:** `v1.0.0` · **Branch:** `main`, clean, fully
pushed · **Remote:** <https://github.com/winsznx/pdjson-zig>

## Verified figures

| | |
| --- | --- |
| Upstream suite | 18/18 assertions, 0 failed, 0 skipped, 0 unsupported, sources unmodified |
| `stream.c` / `pretty.c` differential | 430 fixture runs, 0 mismatches |
| Fixed corpus | 215 inputs × 19 modes = **4,085 comparisons, 0 divergences**, 43 sanitizer-confirmed upstream UB |
| JSONTestSuite | **3,498 comparisons, 0 divergences**; original conforming 95/95 accept, 188/188 reject |
| Published fuzz | 1800.02 s, **11,812,800 cases**, 11 modes, 0 divergences / crashes / timeouts |
| Mutation | **12/12** caught, 0 survived, 1,470 comparable cases (35 excluded as upstream UB) |
| ABI (host) | layout identical, C consumer `linked_and_ran`, `sizeof(json_stream)` 272 |
| ABI (cross) | 6 targets, 0 mismatches |
| Safety scan | pass — `@constCast` 0, `unreachable` 0, force-unwrap 0, asm 0, `@ptrCast` 10, `@alignCast` 1 |
| Benchmark | 9 of 12 workload/mode pairs slower than C, 3 faster |
| Zig-native tests | 74 passing |
| Claim ledger | 24 claims, all `verified`, all checked against artifacts |
| CI | green on ubuntu-latest and macos-latest, plus provenance and sanitizer jobs |

## Upstream issues filed

| Issue | Defect |
| --- | --- |
| [#36](https://github.com/skeeto/pdjson/issues/36) | `json_get_context()` reads an unallocated stack slot after a failed allocation |
| [#37](https://github.com/skeeto/pdjson/issues/37) | byte `0xFF` read as EOF by the buffer source, disagreeing with its own `FILE*` source |
| [#38](https://github.com/skeeto/pdjson/issues/38) | `json_get_number()` reads uninitialised bytes after a partial token |

None triaged upstream. The ledger records them as reported, not confirmed.

## Defects found in this port

| Defect | Found by | Status |
| --- | --- | --- |
| Hex-float rounding 1 ULP below libc | fuzzing, ~30M cases | fixed, D-18 |
| Reading past `string_fill` in `json_get_number` | oracle determinism gate on Linux | fixed, D-19 |

## Long fuzz session

Not running at snapshot time — the published session completed and its log is
committed at `fuzz/logs/session-published.json`. No session was killed or
duplicated to produce this baseline.

## Open limitations at snapshot

1. Equivalence is demonstrated, not proven.
2. ABI executed-check covers two targets; the other four are compile-time only,
   through one C frontend.
3. `nan(...)` payloads overflowing 64 bits are implementation-defined and not
   matched.
4. Port slower than C on 9 of 12 workloads; part of the gap unexplained.
5. Three upstream issues filed, none triaged.
6. Demo video not recorded; Devfolio not submitted.

## Audit recommendations: validation before acting

Each recommendation checked against the repository rather than accepted.

| Recommendation | Actual state | Action |
| --- | --- | --- |
| A. Clean-clone / Docker reproducibility | `make verify` passes from a fresh clone (checked during the first audit) but **no committed log**, and Docker was **never built or run** | Do it, and commit the evidence |
| B. Extend differential to FILE\* and user callbacks | **Already done** before the audit — 4,085 comparisons across all three sources. The audit's premise ("covers only buffer input") is stale | Produce the per-source matrix report it asks for; correct the premise |
| C. Exported API coverage classification | `json_skip`/`skip_until`/`peek`/streaming are exercised, but **no document classifies all 22 exports** | Do it; add scenarios for genuine gaps |
| D. Transcript invariant checker | **Does not exist.** Comparison is C-vs-Zig only, with no internal validation | Build it — this is the strongest new idea in the audit |
| E. ABI as compile-time contract | Cross-check exists but **generates no Zig assertion file**; layouts not committed as artifacts | Extend. Audit suggested `@compileLog` for the char diagnostic — rejected, it fails the build; use a `zig build diagnose` step instead |
| F. Independent hex-float oracle | D-18 rests on comparison with libc only; **no independent integer reference** | Build one. File a Zig issue only if independently proven |
| G. State-transition specification | **Does not exist** as a document | Build it, with honest coverage rather than a claim of completeness |
| H. Raw fuzz evidence + mutation rigor | Both exist; **raw logs are summarised**, and the mutation harness has no self-test | Preserve raw output; add a harness self-test |
| I. Benchmark completeness | Artifact already has p50/p95/p99, RSS, alloc counts; **README shows only ratios**, and library size is missing | Extend rather than rebuild |
| J. Safety classification | Report **enumerates every occurrence** already, but classification is prose, not per-occurrence data | Convert to structured per-occurrence classification |
| K/L. README + ledger | Both exist and are generated; needs the new evidence folded in | Update after the evidence exists |
| M. Devfolio + screenshots | Copy staged, **nothing submitted**; rendered PNGs were produced and then deleted | Keep staged; provide capture commands only |

Two audit premises were **wrong** and are corrected rather than acted on:
the FILE\*/user-callback gap (B) was closed before the audit arrived, and
`@compileLog` (E) cannot be used as an advisory diagnostic because it aborts
compilation.
