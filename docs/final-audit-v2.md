# Second adversarial audit

The [first audit](final-audit.md) read the project as a skeptical reader: it
assumed every README sentence was marketing until a command proved otherwise, and
it found four things.

This one asks a harder question. The first audit checked whether the *claims*
hold. This one checks whether the *checks* hold — whether each mechanism that
produces a number is capable of producing a different one, and whether the
numbers in outward-facing copy still match the artifacts they came from.

Everything below was executed. Where something was wrong, it says so and says
what it is now.

---

## Method

Three passes, in order of how uncomfortable they are:

1. **Can each check fail?** For every check that reports a clean number, inject
   the defect it is supposed to catch and require it to catch it.
2. **Does the prose match the artifacts?** Mechanically, not by reading.
3. **Cold read of the tree.** Which scripts does anything run, which artifacts
   does anything regenerate, which claims cite what.

---

## Pass 1 — can each check fail?

| Check | Made to fail by | Result |
| --- | --- | --- |
| Compile-time ABI contract | 10 injected layout drifts, plus a control | 10 caught, 0 missed, control still builds |
| Differential comparison | perturbing each of 12 transcript fields | all 12 noticed |
| Differential comparison, weakened | same 12 mutants, event-sequence only | 12 → 4 caught; 8 depend on the rest |
| Mutation harness | 12 injected parser defects | 12 caught, 0 survived |
| Invariant checker | 14 synthetic malformed transcripts | 14 rules provably able to fire |
| Safety inventory classifier | 10 cases it could misreport | all 10 correct |
| State-derivation | 14 cases + a check every specified state is producible | all correct |
| Hex-float reference | 200,017 literals against exact integer arithmetic | 0 disagreements |

Three of these did not exist before this audit, and building them found things.

### The ABI contract had a blind spot that a field-size check closes

The cross-target check asserted field *offsets*. A field that shrinks at the end
of a struct can have the loss absorbed by padding, leaving `sizeof` and every
offset identical — so the drift passes. Sizes are asserted now, and the negative
test includes that exact case.

A blind spot that remains, and is recorded rather than glossed: two adjacent
fields of the same width swapped with each other leave every offset and size
unchanged. No layout table can catch that. The behavioural differential is what
covers it.

### The safety inventory's own rules were wrong on first run

A wildcard rule for `= undefined` in `api.zig` read "the json_stream is filled
completely by parser init() before any field is read". True of `initBuffer`. It
was also being applied to `var seen: [3]f64 = undefined` inside a **test**, which
it does not describe at all.

A justification that sounds right and describes something else is worse than no
justification, because it reads as verified. Test blocks are attributed
separately now and excluded from the shipped totals; two of the classifier's ten
self-tests exist because of this.

### The state machine exposed a hole in the harness, not the parser

Writing the transition specification and measuring coverage gave 46 of 54. Six
gaps were ordinary corpus gaps. Two were not:

`ERROR_LATCHED → ERROR` and `TOP.DONE → DONE` were unreachable **by any drive
mode**, because both transcript producers `break` at the first terminal event. No
transcript had ever contained two consecutive `ERROR` records.

Which means the invariant checker's `error-is-latched` rule had **never fired
against real data**. It passed its own self-test against synthetic transcripts,
so nothing looked wrong from inside. An `after-end` mode, written independently
in both producers, closes it — and it is in the differential's mode list, so the
latch is now compared rather than merely reached.

Coverage is 54/54, with 0 transitions observed that the specification does not
contain and 0 reached by one implementation and not the other.

---

## Pass 2 — does the prose match the artifacts?

`scripts/validate-claims.py` checks each claim's machine-readable assertion. It
never reads the claim's English. So a claim could correctly assert
`divergences == 0` while its prose quoted a number that had moved three commits
earlier — and that is exactly what had happened.

Two audits now close that:

- [`scripts/audit-claims.py`](../scripts/audit-claims.py) — every number in a
  claim's text must appear in the artifact it cites.
- [`scripts/audit-public-copy.py`](../scripts/audit-public-copy.py) — every
  number in the README, the staged Devfolio copy and the demo script must appear
  as a JSON value under `artifacts/`.

### What they found

| Finding | Was | Is |
| --- | --- | --- |
| Hex-float artifact overwritten by the verify smoke run | claimed 200,017, artifact said 20,017 | smoke run writes elsewhere; 200,017 re-run |
| Source matrix overwritten by the JSONTestSuite run | wrong per-source counts | filenames are label-scoped |
| `conformance-suite.sh` drifted | script passed 5 modes, artifact recorded 11 | 12 modes, re-running reproduces it |
| C-15's numbers | cited an artifact containing none of them | `number-torture.json`, derived from the loop bounds |
| C-19, C-20 | quoted figures from artifacts they did not cite | cite artifacts that contain them |
| "the archive was 4.6 MB" | did not reproduce | built both ways: 2,461,816 vs 241,248 |
| Optimization ratios `0.57x–0.70x` | quoted from memory | re-measured by reverting each change: 0.57x, 0.68x |
| "~990 lines", "~9,000 cases/second" | roundings matching no measurement | 992; the session log's own rate |
| 3,498 / 43 / 214 / 215 | stale by one corpus growth each | 3,816 / 45 / 218 |
| Zig `parseFloat` defect in the README | embargoed from every public channel | explicit `disclosure_in`, see below |

Three of these were the same failure: a short or differently-labelled run
overwriting a published artifact. **Two of the three made a number look better
than it was.**

### The embargo needed a distinction, not an exception

`CLAIMS.json` bars the unfiled Zig `std.fmt.parseFloat` defect from every public
channel, and the README described it anyway — in the known-limitations section,
saying it was unfiled, unconfirmed and not counted.

Deleting a real limitation to satisfy a check would be the wrong fix. So the
ledger now separates two speech acts: `allowed_in` (may be presented as a result)
from `disclosure_in` (may be mentioned as something found and explicitly not
counted). The defect is disclosable in the README and claimable nowhere.

### Both audits were vacuous before they were useful

The public-copy audit passed on its first run. It was wrong twice:

1. It matched a figure against the **raw text** of every artifact. With megabytes
   of sha256 and base64 under `artifacts/`, almost any digit string matched. Four
   unbacked profiling figures sailed straight through. It matches parsed JSON
   *values* now.
2. It stripped fenced code blocks, reasoning that a shell command's flags are not
   claims. True of the README. In `docs/devfolio-submission.md` the fenced blocks
   **are** the submission text — they are fenced so they can be pasted into the
   form. It reported "0 unbacked" for a file whose every figure it had discarded.
   Six stale figures surfaced the moment it started reading them.

A check that passes for the wrong reason is the failure mode this whole project
is about. Finding it twice in the check built to find it elsewhere is worth
stating plainly.

---

## Pass 3 — cold read of the tree

- `artifacts/number-torture.json` is cited by C-15 and was regenerated by
  **nothing**. It could have gone stale indefinitely. It is a verify step now.
- `artifacts/differential-defined.json` was an orphan — no script wrote it, no
  claim cited it. Removed. Deleting it immediately broke the figure `142` in
  three documents that had been leaning on it for backing, which the public-copy
  audit caught within a minute.
- The pipeline had grown from 16 steps to 24 by accretion, with `5b`/`9c`/`12b`
  suffixes and two steps out of order. Renumbered, and the README's count is
  spliced from the Makefile rather than typed.

### The clean clone caught what the development tree could not

`make verify` passed in the working tree at 54/54 transitions. In a fresh
`git clone` it **failed** at 48/54.

The six missing transitions were all first-element-of-array (`[false,…]`,
`[null,…]`, `[{…},…]`, `["x",…]`, `[true,…]`) and a string value directly after
an object key. The fixture corpus had never contained `{"key":"value"}` — the
most common shape in JSON — and nobody noticed because JSONTestSuite covered it.
JSONTestSuite is fetched on demand and is absent in a clean checkout.

Coverage that depends on a corpus a fresh clone does not have is not coverage. So
six fixtures close the gap, and the analysis now reports and gates on the
fixtures-only figure separately from the full one. Claim C-40 exists solely
because this failure mode is invisible from inside a working tree.

That is the argument for running the clean-clone check at all, and it only came
up because it was run last rather than assumed.

**The second clean-clone run found a second instance of the same class.** With
the coverage gap closed, `make verify` failed in a fresh clone again — this time
on C-25, whose text quoted "14,092 transcripts and 6,164,316 records". Those are
the figures from a tree that has fetched JSONTestSuite. A fresh clone produces
5,824 and 203,433, so the claim was true where it was written and false where it
would be read.

The invariant checker now reports the committed-corpus figures separately, the
claim quotes those, and the README row is generated from them — so the number a
judge sees is the number their own clone produces. `make clean-clone-verify`
makes the check a target rather than something to remember.

**A third instance, subtler than both.** With C-25 fixed, the clean clone failed
again on the archive's byte count. `libpdjson.a` is 241,248 bytes here and
241,352 in a fresh clone — Zig embeds build paths, so the size is not
reproducible between directories at all. Any hand-typed byte count in prose is
wrong somewhere by construction. That sentence is generated from the artifact
now.

**And a fifth, from CI rather than the clean clone.** The Linux job failed with
eleven unbacked size figures — caused by the CI change made an hour earlier to
*run* the audit. `size-report.py` had been placed after `report.py`, so the
report spliced the previous platform's committed figures into the README and the
size report then overwrote the artifact with this platform's, leaving the two
disagreeing. The Makefile already had the right order; only CI did not.

The audit catching a defect in the change made to run the audit is the clearest
evidence available that it is doing work rather than confirming what was already
believed.

Five findings, one shape: a number true in the tree it was written in and false
in the tree it will be read in. A corpus a fresh clone lacks, a build directory
that differs, a platform that differs, a step ordering that differs. None is
visible without actually cloning and running.

**Sixth: the audit was under-reading its own inputs.** The Linux job kept
failing on "the figure 26", which appears nowhere in the README. It came from
`26.07x` — the number regex ended in `\b`, and there is no word boundary between
`7` and `x`, so it backtracked and matched `26`. On macOS every ratio truncated
the same way to an exempt single digit (`2.42x` → `2`), so the check had been
passing by luck. Fixing it made the audit read **104 figures in the README where
it had been reading 77** — it had been blind to a quarter of them.

**Seventh, and the one that matters most to a reader: the size cost is
platform-specific and the difference is large.** The same measurement that gives
2.42× the stripped binary on arm64 macOS gives **12.51× on x86-64 Linux**, and
3.29× against 23.42× for machine code. The README had been stating the macOS
figure as though it were universal. It now names the platform, quotes both, and
the Linux measurement is committed as an artifact so the comparison is backed
rather than asserted.

**CI had the same blind spot, for a different reason.** It runs the pipeline's
steps individually rather than through `make verify`, and it had never run
`scripts/state-machine.py` at all — so it was green while a clean clone failed.
It also fetched JSONTestSuite *before* the checks that must not depend on it, and
its hex-float run wrote to the published artifact path rather than a smoke path,
the same overwrite defect the Makefile had. All three are fixed: the
state-transition check now runs deliberately before the fetch step, and both
ledger audits run alongside claim validation.

---

## What a hostile reviewer still gets

Stated because they are real, not because they are comfortable.

- **The specification is hand-written.** The transition relation agreeing with
  both implementations is evidence, not proof. A shared misreading of RFC 8259
  would be invisible to it. JSONTestSuite is what covers that, and it is an
  external corpus, which is the only reason it counts.
- **100% transition coverage is not path coverage.** Every edge is reached; no
  claim is made about every sequence of edges.
- **Twelve mutants is twelve mutants.** They span the parser's distinct concerns
  by construction, not by sampling. A defect unlike all twelve could survive.
- **Escape-hatch justifications are arguments.** What is machine-checked is that
  every occurrence has one, that none is forbidden, and that the inventory
  matches the source. The soundness of each argument is not.
- **Both executed ABI targets are LP64.** Six more are compile-time only, and all
  eight go through the Zig toolchain's clang on the cross path.
- **The fixture corpus was written by the same person who wrote the port.** The
  independent checks against that bias are JSONTestSuite, the mutation harness,
  the invariant rules and the transition specification — each of which found
  something the fixtures had missed, which is the argument for their existing.
- **`@intCast` bounds are argued, not proven.** They are checked at runtime in
  ReleaseSafe, so a wrong bound aborts rather than corrupting memory; that they
  hold on untrusted input rests on the fuzz session and the random-input
  regression test.
- **Three upstream issues are filed, not triaged.** No maintainer has confirmed
  them.
- **The port is slower and larger.** 9 of 12 workload/mode pairs slower, 2.42×
  the stripped binary in a consumer. Both are generated into the README from
  artifacts so neither can quietly improve.

---

## Verdict

**PASS on the technical work.** Every claim in `CLAIMS.json` has an executable
artifact behind it, every number in outward-facing copy is mechanically checked
against those artifacts, and every check that reports a clean number has been
made to report a dirty one.

The conditions that remain are not technical: the demo video is scripted but not
recorded, and the Devfolio submission is staged but not filed. Both are
deliberate — see the README's status lines.

What changed between the two audits is worth naming. The first asked whether the
claims were true. This one asked whether the machinery producing them could tell
the difference — and the three tables above are the places where it could not:
three checks that were wrong or absent, ten figures that had drifted from their
artifacts, two occasions where the audit built to catch drift passed vacuously,
and three gaps in what the pipeline regenerates.

None of the sixteen was a defect in the parser. All sixteen were defects in the
evidence, which is the more dangerous kind, because the parser has a differential
watching it and until this audit the evidence did not.
