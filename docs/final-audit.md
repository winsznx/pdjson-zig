# Final adversarial audit

Performed as a skeptical reader who assumes every README sentence is marketing
until a command proves otherwise. Each check below was executed; the commands are
included so they can be re-run rather than trusted.

**Verdict: CONDITIONAL PASS** — every technical claim has executable evidence
and CI is green on Linux and macOS. The outstanding conditions are the demo
video and the Devfolio submission; see [Verdict](#verdict).

---

## 1. Is the Zig implementation actually standalone?

The central claim collapses if the library quietly wraps the C parser.

```
$ ar t zig-out/lib/libpdjson.a
__.SYMDEF
libpdjson_zcu.o
```

One object, produced by Zig. `nm -u` shows the archive imports only libc and
system symbols (`malloc`, `realloc`, `free`, `fgetc`, `ungetc`, `clock_gettime`,
platform stubs) — **no `json_*` imports**, so it defines its own API rather than
expecting the original to be linked alongside. `scripts/verify-no-c-linkage.sh`
additionally checks for 23 of `pdjson.c`'s file-static symbols and for any Zig
build input referencing `pdjson.c`; all clean.

Notably, `strtod` is absent from the imports — the port implements its own.

**PASS.**

## 2. Are the original tests genuinely unmodified?

```
$ sh scripts/verify-upstream-hashes.sh
upstream hash verification OK (9 files match artifacts/upstream-manifest.json)
$ git diff --exit-code -- upstream/pdjson     # clean
$ cmp -s include/pdjson.h upstream/pdjson/pdjson.h && echo YES
YES
```

The verifier was negative-tested: appending one byte to `tests/tests.c` fails it
with both digests printed, and adding a file inside the pinned tree fails it too.

`tests/original/` contains exactly one file, `abi_consumer.c` — a *new* C
consumer, not a modified upstream test. There is no adaptation layer, no wrapper,
and no copy of the upstream tests. They are compiled in place; only the link line
differs.

**PASS.**

## 3. Can the differential harness actually fail?

A harness that never fails and one that cannot fail look identical from outside.

A mutant was built in a scratch copy of the tree (`byteAsC` returning unsigned,
i.e. the `0xFF` fix) and compared against the C oracle:

```
ff-in-string: DETECTED
ff-bare:      DETECTED
bad-utf8-ff:  DETECTED
```

More systematically, `artifacts/mutation-report.json` records 12/12 injected
defects caught, each by a fixture actually related to the defect (the
surrogate-range mutant by `uni-escaped-pair-hi-end`, the control-character mutant
by `ctrl-raw-1f`, the NUL-terminator mutant by `nul-after-number`).

The history here matters more than the score, and is documented in DECISIONS.md
D-17: the first sound run caught only **8/12**, and two earlier runs produced a
**false 12/12** by counting cases where the C oracle crashes. Both traps were
found and fixed.

**PASS.**

## 4. Is "0 divergences" hiding anything?

Three ways it could be dishonest, checked individually.

**Are excluded cases evidenced?**

```
43 upstream_ub findings, 0 without sanitizer evidence
```

Every exclusion carries the ASan/UBSan output from the pinned original, and all
43 resolve to one line, `pdjson.c:912`. The classification is made by a
sanitizer, not by the author (D-16), and the release gate fails if any
`upstream_ub` finding lacks a report.

**Is anything silently filtered or retried?** `scripts/differential.py` contains
no skip list, no allow list, and no retry. The only `except` clauses are
`TimeoutExpired`, which is reported as a `timeout` finding and fails the run.

**Is the corpus stacked to avoid hard cases?** The fixtures include every raw
control byte 0x00–0x20 individually, invalid UTF-8 across all the boundary forms,
escaped surrogate pairs at both ends of the range, embedded NULs, deep nesting to
1000 levels, and four allocation-failure schedules. The generator is committed and
the fixtures are regenerable.

**PASS**, with the scope correctly narrowed: 0 divergences means 0 on inputs
where the original has defined behaviour.

## 4b. Is the *oracle* itself trustworthy?

The comparison is worthless if either side is nondeterministic, so
`scripts/oracle-determinism.sh` runs each producer five times over every fixture
in five modes and requires byte-identical output.

**This gate failed**, and the failure was real. On macOS it passed; in CI on
x86-64 Linux the C reference produced different transcripts between runs of the
same binary. Reproduced in a Debian container and tracked to a single field on
input `-`: the number token pushes one byte and errors before pushing its
terminator, so `strtod` reads into uninitialised heap — `0.0` on one run, `-1.0`
on the next.

That is upstream issue [#38](https://github.com/skeeto/pdjson/issues/38), filed
with a deterministic reproducer. The port now reads only `string_fill` bytes, and
both transcript producers record `num` as null where the original's value is
indeterminate — by a rule computed from the public API alone and applied
identically on both sides, so it cannot mask a real difference.

Two things worth noting about this. First, it means the differential result
before this point was resting on an assumption that happened to hold on one
platform. Second, it is the only check in the project that would have caught it —
neither the upstream suite, nor the fixed corpus, nor 30 million fuzz cases on
macOS did.

**PASS**, after a real failure.

## 5. Does the fuzzing prove anything?

This is the check that changed the project. The published session found a **real
defect in the port** at ~30 million cases — a one-ULP hex-float rounding error in
`json_get_number`, root-caused to `std.fmt.parseFloat` truncating past 53 bits of
mantissa. It is fixed, the 22-byte minimized case is a committed fixture, and two
regression tests pin it (D-18).

A fuzzer that has never found anything is not evidence that anything was checked.
This one has.

The session log records exact seed, duration, rounds, case count, rate, mode list,
compiler versions and the upstream commit. Findings are minimized by delta
debugging and written with both transcripts.

**Honest limitation:** allocation-failure modes are excluded from the fuzz
defaults, because the original crashes constantly there and swamps the signal.
They are covered exhaustively by the fixed corpus and by
`tests/port/allocator_failure.zig` instead. This is stated in the fuzzer's own
docstring, not just here.

**PASS.**

## 6. Is the benchmark fair?

The most common way a port benchmark lies is by having the two harnesses do
different amounts of work. Checked directly — both harnesses print a checksum
derived from every event and token length:

```
60 workload/mode/rep groups compared
groups where C and Zig event checksums differ: 0
```

They do identical work. Other controls: same `CLOCK_MONOTONIC` source on both
sides, same counting allocator, same warm-up, binaries interleaved within each
repetition, all raw samples committed.

Two self-corrections are recorded rather than buried: the first run measured a
Debug build (6.8× slower, meaningless), and the first explanation of the
remaining gap was wrong and disproved by measurement (0% improvement).

The result reported is that **the port is slower on 9 of 12 workloads**. Nobody
fabricates that.

**PASS.**

## 7. Are the ABI claims real?

`artifacts/abi-report.json` shows `"comparison": "identical"` across every
offset, size, alignment and enumerator, plus `"c_consumer_link":
"linked_and_ran"` — a C program including the *pinned* header and declaring
`struct json_stream` by value on its own stack, linked against only the Zig
archive.

**Scope, after hardening.** Executing both probes only covers targets with a
runner, and both CI targets are LP64 — so the original claim said nothing about
what happens when the pointer size changes.
`scripts/abi-cross-check.sh` now compares the layouts at compile time on six
targets: x86-64, aarch64, riscv64 and x86-64 Windows (all 272 bytes, align 8),
plus i386 and armhf (204 bytes, align 4). Zero mismatches.

That check was itself negative-tested, and **the first version failed the test**:
changing `errmsg_len` from 128 to 127 survived it, because the missing byte was
absorbed by padding and left `sizeof` and every offset identical. It now asserts
field sizes as well as offsets, and the same mutation is caught.

Caveat stated in the artifact and the README: both sides go through the Zig
toolchain's clang there, so it is not a claim about other compilers on those
targets. The executed check covers the host compiler.

**PASS.**

## 8. Are the escape-hatch counts meaningful?

"Zero unsafe" needs a definition in a language with no `unsafe` keyword, and
`artifacts/safety-report.json` gives one, then enumerates **every occurrence with
file and line** rather than only counting.

```
ptrCast 10, alignCast 1, constCast 0, unreachable 0,
force_unwrap 0, setRuntimeSafety 0, inline_asm 0, volatile 0
```

Each of the 10 `@ptrCast` sites was inspected: 7 in `parser.zig` at the C
allocator boundary (`malloc`/`realloc`/`free` return `void *`), 3 in `c_api.zig`
where the header's contract requires a `char *`. None reinterprets one object
type as another.

All `= undefined` sites were inspected too. During this audit one in the parser
was found avoidable and **removed**, so the shipped parser now has none; the
remainder are in the diagnostic builder (a scratch digit buffer fully written
before read), the Zig-native constructor (immediately filled by `init`), and one
test.

The shipped artifact is ReleaseSafe, so these checks are live at runtime, and the
benchmark measures that same binary.

**PASS.**

## 8b. Is there a defect class the existing tools cannot see?

ASan and UBSan run throughout, but neither detects uninitialised *value
propagation*. That is precisely the shape of upstream #38, and it is why that
defect survived 30 million fuzz cases and a full ASan sweep on macOS — it was
caught by a determinism gate, not by a sanitizer.

`scripts/valgrind-upstream.sh` closes that gap. Memcheck independently confirms
both memory defects, and for #38 traces the origin of the uninitialised bytes to
`malloc` in `init_string` at `pdjson.c:186` — information ASan cannot produce.
It finds no further defects, and the upstream test suite is clean under memcheck.

The script asserts that both known defects still reproduce. Without that, a
memcheck run reporting nothing would be ambiguous between "the code is clean" and
"Valgrind is not actually running" — the same failure mode that produced the
false 12/12 mutation scores.

**PASS.**

## 9. Does it reproduce from a clean checkout, offline?

```
$ git clone . /tmp/fresh && cd /tmp/fresh
  cloned 322 files
$ sh scripts/verify-upstream-hashes.sh
upstream hash verification OK (9 files match ...)
  JSONTestSuite absent, as intended
  build OK from clean clone
stream.c differential over 215 fixtures: 0 mismatches
pretty.c differential over 215 fixtures: 0 mismatches
  skipped: JSONTestSuite not present
```

No network needed. The optional conformance step skips **and says it skipped**,
rather than silently passing.

**PASS.**

## 10. Do the public claims match the artifacts?

`scripts/validate-claims.py`: 21 claims, 21 checked against generated artifacts,
all `verified`. The README's claim table and benchmark table are both generated
from artifacts, so a stale figure is a diff.

The ledger earned its keep during the build: it failed because `make verify`'s
**smoke** benchmark was overwriting the full benchmark artifact, which would have
silently replaced a published figure with a single-workload sample.

It also enforces that a claim not marked `verified` cannot appear in the README,
on Devfolio, in the video, or in social copy.

**PASS.**

## 11. Are the upstream bug claims overstated?

Both have minimal public-API reproducers that run against the pinned commit, and
both were checked for duplication against every open and closed issue and PR
first (#31 is execution character set, #27 locale/`strtod`, #15 peek/position —
none overlap).

The `0xFF` issue is explicitly scoped down in its own report: it does **not**
make the parser accept invalid input, and the report says so before saying
anything else.

`artifacts/upstream-issues.json` records
`"independently_verified_by_upstream": false` for both. The claim ledger says
"reported", not "confirmed".

**PASS.**

## 12. Is the port idiomatic Zig, or C transliterated?

Mixed, and the README should not pretend otherwise. `struct json_stream` is
reproduced field for field because the header dictates it (D-02), so the state
shape is C's. Within that constraint the code uses optionals and error unions
instead of sentinel ints, exhaustive `switch` over a real enum instead of
fallthrough chains, explicit wrapping operators where C relies on unsigned
overflow, and a bounds-checked accessor for the container stack. `src/api.zig`
provides a genuinely Zig-native face with slices, an error set, and `defer`.

A reviewer could reasonably call `parser.zig` "C-shaped". That is a consequence of
the ABI requirement, and it is argued for in D-02 rather than glossed over.

**PASS**, with the caveat stated.

---

## Findings from this audit

Four things were changed as a direct result:

1. **1,242 JSONTestSuite files had been committed**, contradicting `LICENSES.md`,
   which states the corpus is fetched rather than vendored. Removed from the
   index and from history, and added to `.gitignore`. The repository went from a
   push that timed out to 1.43 MiB.
2. **An avoidable `undefined` local in the parser** was removed, so the shipped
   parser now has zero.
3. **The static archive could not be linked by a system C compiler on Linux.**
   `zig cc` supplies `compiler_rt` itself and hid the problem, which is exactly
   why the ABI check has to use a system compiler to mean anything.
   `bundle_compiler_rt` fixes it; a C-appropriate panic handler also took the
   archive from 4.6 MB to 2.2 MB with 11 standard libc imports.
4. **The C oracle was nondeterministic on Linux** — upstream #38, above.

One process failure worth recording: during audit check 3, the mutant binary was
copied over `zig-out/bin/transcript_zig` while a fuzz session was running against
it, which killed the session with an exec-format error. The check was redone in a
scratch directory and the session restarted. Auditing must not mutate the thing
being audited.

---

## Verdict

**CONDITIONAL PASS.**

Every central claim has executable evidence, and `make verify` passes end to end
from a clean checkout with no network.

The conditions are the things that are genuinely not done yet, and they are not
evidence problems:

- [x] **CI is green on Linux and macOS.** Getting there took three real fixes,
      all of them findings rather than paperwork: the third-party `setup-zig`
      action hung for 20+ minutes on both runners (replaced with a pinned direct
      download); the static archive would not link against a system `cc` on
      Linux because Zig's `compiler_rt` was not bundled; and the determinism
      gate exposed upstream #38.
- [ ] **The demo video does not exist.** `docs/demo-script.md` is written with
      exact commands and expected output, but nothing has been recorded. The
      README carries a placeholder, not a link.
- [ ] **The Devfolio submission is not filed.** The MCP connection was
      unavailable for the whole build, so the live form was never inspected.
      A complete draft with the exact remaining manual steps is in
      `docs/devfolio-submission.md`.

Nothing on that list can be resolved by writing more prose, and none of it is
claimed as done anywhere in the repository.

### What a judge should distrust most

If I were trying to break this submission, I would attack here:

0. **Three of the four audit findings were platform-specific**, and all three
   were invisible on the development machine. That is a warning about how much
   single-platform evidence is worth, and the reason CI runs two targets. A
   third target would probably find a fourth thing.
1. **"Equivalent" is demonstrated, not proven.** ~3,500 compared cases plus a
   fuzz session is strong evidence about a deterministic, fully observable
   parser — but it is evidence.
2. **Three upstream issues are filed, not triaged.** No maintainer has looked at
   them.
3. **Part of the performance gap is unexplained**, and is left that way rather
   than chased.
4. **Every check here has a scope**, and the scopes are where the remaining risk
   lives: the cross-target ABI check uses one C frontend; Valgrind ran on the
   fixture corpus, not exhaustively; the fuzz session is 30 minutes, not 30
   hours.
