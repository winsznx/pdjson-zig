# Final status

## Repository

| | |
| --- | --- |
| URL | https://github.com/winsznx/pdjson-zig |
| Branch | `main` |
| Visibility | public |
| License | Unlicense (matching upstream) |

## Upstream

| | |
| --- | --- |
| Project | https://github.com/skeeto/pdjson |
| Commit | `78fe04b820dc8817f540bdd87fb22887e0ef3981` (master, 2024-02-22) |
| License | Unlicense |
| Carried as | committed verbatim copy, `.git` removed, 9 files hash-pinned |

## Verification

One command, no network required:

```sh
make verify
```

Sixteen steps, failing on the first thing that does not hold. Current results,
each from a regenerated artifact:

| Check | Result |
| --- | --- |
| Pinned upstream hashes | 9/9 files match |
| Upstream test suite against the Zig library | **18/18**, unmodified, 0 skipped |
| `stream.c` / `pretty.c` differential | 0 mismatches over 215 fixtures |
| Fixed corpus differential | **0 divergences** in 4,085 comparisons, all 3 input sources |
| JSONTestSuite differential | **0 divergences** in 3,498 comparisons |
| Oracle determinism | byte-identical over 5 runs × 5 modes, both producers, Linux and macOS |
| C ABI layout | identical on the host, plus 6 cross targets (32/64-bit, x86, ARM, RISC-V, Windows) |
| C consumer against the pinned header | links and runs |
| No upstream parser code in the artifact | 1 Zig object, 22/22 exports, 0 `json_*` imports |
| Escape-hatch scan | 0 `@constCast`, 0 `unreachable`, 0 force-unwraps, 0 asm |
| Harness mutation testing | **12/12** injected defects caught |
| Published fuzz session | 1800s, **11,812,800 cases**, 0 divergences |
| Valgrind memcheck vs the original | both known defects reproduced, no new ones |
| Zig-native tests | 74 passing |
| Claim ledger | 24/24 validate against artifacts |

## CI

Green on both targets, from a clean checkout:

| Job | Result |
| --- | --- |
| pinned upstream hashes | success |
| full verification (ubuntu-latest, x86-64) | success |
| full verification (macos-latest, arm64) | success |
| sanitizers on the C oracle (ASan, UBSan, Valgrind) | success |

Latest run: <https://github.com/winsznx/pdjson-zig/actions/runs/30749888524> (commit `6b71420`)

## Bugs found in the original

All three filed during the hackathon, each with a minimal public-API reproducer
confirmed against the pinned commit, and each checked against every open and
closed issue and PR for duplication first.

| Issue | Defect | Found by |
| --- | --- | --- |
| [#36](https://github.com/skeeto/pdjson/issues/36) | `json_get_context()` reads an unallocated stack slot after a failed allocation — null dereference or out-of-bounds read | differential testing under allocation-failure schedules |
| [#37](https://github.com/skeeto/pdjson/issues/37) | byte `0xFF` read as EOF by the buffer source, disagreeing with the library's own `FILE*` source | hand-written edge-case corpus |
| [#38](https://github.com/skeeto/pdjson/issues/38) | `json_get_number()` reads uninitialised bytes after a partial token, nondeterministic on glibc | the oracle determinism gate, on Linux in CI |

None triaged by upstream at the time of writing; the claim ledger records them as
reported, not confirmed.

## Bugs found in this port

Listed because verification that never finds anything in its own subject is not
evidence that anything was checked.

| Defect | Found by | Status |
| --- | --- | --- |
| Hex-float rounding one ULP below libc (`std.fmt.parseFloat` truncates past 53 bits) | published fuzz session at ~30M cases | fixed, D-18, 2 regression tests |
| Reading past `string_fill` in `json_get_number` (inherited from the original) | oracle determinism gate on Linux | fixed, D-19, 3 regression tests |

## Benchmark

The port is **slower than C on 9 of 12** workload/mode pairs and faster on 3.
Reported as a result rather than buried. ReleaseSafe and ReleaseFast land within
about 2% of each other, so the gap is not the cost of safety checks — the shipped
library keeps bounds and overflow checking on.

Raw per-iteration samples: `bench/results/raw.json`.
Methodology, including the profiles and one disproved hypothesis:
`bench/methodology.md`.

## Commits

Coherent milestones, all authored by the repository owner:

```
chore: initialize provenance and upstream lock
feat: establish Zig C ABI surface
feat: implement parser state machine in Zig
test: add deterministic C oracle and differential harness
test: add mutation testing, conformance suite, and safety scans
fix: correct hex-float rounding in json_get_number
docs: README judge path, DECISIONS, claim ledger, and verification pipeline
chore: stop tracking the fetched JSONTestSuite corpus
ci: install Zig by pinned download instead of a marketplace action
fix: make the static archive linkable by a system C compiler
fix: make the static archive linkable by a system C compiler
fix: read only the bytes the parser wrote in json_get_number
docs: record the determinism finding and CI-green audit verdict
test: compare all three input sources, not just the byte buffer
test: verify the ABI on six targets, including 32-bit and Windows
test: add valgrind memcheck against the pinned original
fuzz: refuse to run against binaries that do not work
docs: record the four measurement artifacts and their guards
test: publish an 11.8M-case fuzz session across all three input sources
```


## Outstanding

Two items, neither of which is an evidence problem, and neither claimed as done
anywhere in the repository.

### 1. Demo video — not recorded

`docs/demo-script.md` contains the full five-minute script with exact commands,
expected output, narration, a fallback plan, and YouTube metadata. Nothing has
been recorded. The README carries a placeholder, not a link.

### 2. Devfolio submission — staged locally, deliberately not created

Nothing exists on Devfolio: no draft, no project. This is by instruction — the
submission waits until the video exists and the final release gate passes.

The live form **has** been inspected through the MCP, so the staged copy is
written against the real constraints rather than a guess: `name` 2–50,
`tagline` 2–50, `hashtags` 1–10, `pictures` **1–6 required**, `links` 0–5, plus
two required long-form organizer fields. `getHackathonTracksAndPrizes` returns an
empty list, so there are no track applications to file.

Every field's copy is in [`devfolio-submission.md`](devfolio-submission.md), and
every figure in it traces to an artifact `make verify` regenerates.

**The one thing that cannot be produced here is `pictures`.** Devfolio requires
real screenshots of the running project. Six shots with exact commands are listed
in [`screenshot-checklist.md`](screenshot-checklist.md).

Remaining steps, in order:

1. Capture screenshots (checklist above).
2. Record the demo video (`demo-script.md`).
3. `make release-gate`, and confirm CI green.
4. Create and publish via `createHackathonProject` with the staged copy,
   screenshots and `video_url`.
5. Paste the project URL into the README and this file.

## Reproducing any of the above

```sh
git clone https://github.com/winsznx/pdjson-zig && cd pdjson-zig
make verify          # everything, offline
make mutation        # does the harness catch injected defects?
make bench           # the honest benchmark
sh scripts/fetch-upstream.sh   # confirm the pin against GitHub (needs network)
```
