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
| Fixed corpus differential | **0 divergences** in 1,935 comparisons |
| JSONTestSuite differential | **0 divergences** in 1,590 comparisons |
| Oracle determinism | byte-identical over 5 runs × 5 modes, both producers |
| C ABI layout | identical on every offset, size, alignment, enumerator |
| C consumer against the pinned header | links and runs |
| No upstream parser code in the artifact | 1 Zig object, 22/22 exports, 0 `json_*` imports |
| Escape-hatch scan | 0 `@constCast`, 0 `unreachable`, 0 force-unwraps, 0 asm |
| Harness mutation testing | **12/12** injected defects caught |
| Zig-native tests | 72 passing |
| Claim ledger | 22/22 validate against artifacts |

## CI

Green on both targets, from a clean checkout:

| Job | Result |
| --- | --- |
| pinned upstream hashes | success |
| full verification (ubuntu-latest, x86-64) | success |
| full verification (macos-latest, arm64) | success |
| sanitizers on the C oracle | success |

Latest run: <https://github.com/winsznx/pdjson-zig/actions>

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
fix: read only the bytes the parser wrote in json_get_number
docs: record the determinism finding and CI-green audit verdict
```

## Outstanding

Two items, neither of which is an evidence problem, and neither claimed as done
anywhere in the repository.

### 1. Demo video — not recorded

`docs/demo-script.md` contains the full five-minute script with exact commands,
expected output, narration, a fallback plan, and YouTube metadata. Nothing has
been recorded. The README carries a placeholder, not a link.

### 2. Devfolio submission — draft prepared, not published

The Devfolio MCP connection was unavailable for most of this build and came back
late. The live form has now been inspected:

- `name` 2–50, `tagline` 2–50, `hashtags` 1–10, `pictures` **1–6 required**,
  `links` 0–5, plus two required long-form organizer fields
  ("The problem it solves", "Challenges we ran into").
- `getHackathonTracksAndPrizes` returns an empty list for this event, so there
  are no track applications to file.

Copy for every field is prepared in `docs/devfolio-submission.md`, and every
figure in it comes from an artifact `make verify` regenerates.

**The blocker is `pictures`.** Devfolio requires 1–6 real screenshots of the
running project and explicitly forbids generated stand-ins. Renderings of
genuinely captured terminal output exist in `/tmp/shots/png/` — the *content* is
byte-for-byte what the commands printed — but they are renderings, not screen
captures, and that distinction is the user's call to make, not mine.

Remaining manual steps:

1. Decide on gallery images (capture real screenshots, or approve the renderings
   of real output).
2. Create the project via `createHackathonProject` with the prepared copy.
3. Record the demo video, add `video_url`, update the README placeholder.
4. Publish, and paste the project URL into the README and this file.

## Reproducing any of the above

```sh
git clone https://github.com/winsznx/pdjson-zig && cd pdjson-zig
make verify          # everything, offline
make mutation        # does the harness catch injected defects?
make bench           # the honest benchmark
sh scripts/fetch-upstream.sh   # confirm the pin against GitHub (needs network)
```
