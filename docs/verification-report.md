# Verification report

Generated 2026-08-02 by `scripts/report.py` from the
artifacts in `artifacts/`. Every number here is read out of a file that
`make verify` regenerates; nothing is typed in by hand.

## Provenance

- Upstream: https://github.com/skeeto/pdjson
- Commit: `78fe04b820dc8817f540bdd87fb22887e0ef3981`
- License: Unlicense
- Files pinned and hash-verified: 9

## Original test suite, unmodified, against the Zig library

- Assertions: 18/18 passed, 0 failed, 0 skipped, 0 unsupported
- `stream.c` and `pretty.c` output mismatches vs the C build: 0
- Upstream sources modified: False

## Differential (fixed corpus)

- 218 inputs x 28 modes = 6104 comparisons
- Divergences: **0**
- Upstream undefined behaviour (sanitizer-confirmed): 45
- Zig crashes: 0, timeouts: 0

## Differential fuzzing

- Session: `fuzz/logs/session-published.json` (seed 20260802)
- Duration: 1800.02s, 11720000 cases (6511.0/s)
- Divergences: **0**, crashes: 0, timeouts: 0

## Harness self-test (mutation)

- Mutants: 12/12 caught, 0 survived
- Comparable cases: 1489 (37 excluded as upstream UB)

## C ABI

- Layout tables: identical
- C consumer using the pinned header: linked_and_ran
- `sizeof(struct json_stream)`: 272
- Public symbols exported: 22/22
- Archive objects: 1 (linkage check: pass)

## Safety

- Scan result: pass
- Shipped mode: ReleaseSafe -- bounds checks, overflow checks and illegal-behaviour detection are active in the artifact this project distributes, including in the benchmark numbers reported as 'zig-safe'.
- Counts: `{"ptrCast": 10, "alignCast": 1, "constCast": 0, "intCast": 15, "bitCast": 13, "truncate": 4, "force_unwrap": 0, "undefined_initializers": 7, "unreachable": 0, "setRuntimeSafety": 0, "inline_asm": 0, "volatile": 0}`

## Benchmark

- 12 workload/mode pairs, 5 repetitions each
- Zig (ReleaseSafe) vs C -O2, median: slowest 0.78x, fastest 1.89x (ratio > 1 means Zig is faster)

| workload | mode | ReleaseSafe vs C | ReleaseFast vs C |
| --- | --- | --- | --- |
| large-mixed | parse | 0.869 | 0.887 |
| large-mixed | strings | 1.041 | 1.075 |
| numbers | parse | 0.784 | 0.819 |
| numbers | strings | 0.958 | 1.001 |
| strings-ascii | strings | 0.908 | 0.929 |
| strings-unicode | strings | 1.018 | 1.064 |
| deep-nesting | parse | 0.841 | 0.848 |
| many-small-docs | parse | 0.887 | 0.91 |
| malformed-early | parse | 1.886 | 5.608 |
| malformed-late | parse | 0.87 | 0.886 |
| whitespace-heavy | parse | 0.932 | 0.935 |
| flat-ints | parse | 0.794 | 0.821 |

