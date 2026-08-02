# Five-minute demo: script and recording checklist

Everything below is a real command against the real repository. Nothing is
staged, and no output is edited. If a command does not produce the output shown,
that is a bug in this document, not something to paper over in the edit.

**Read this first:** the video must not be recorded until the evidence exists.
As of writing, all of it does except the video itself — check
`docs/final-audit.md` for the current verdict before recording.

---

## Setup

Terminal at 120×36, plain prompt, `cd` into a fresh clone. Font large enough to
read at 720p. Two panes are useful for the 2:20 segment; one pane is fine
elsewhere.

Pre-warm before recording so nothing waits on a cold compile:

```sh
make build && python3 scripts/differential.py --quiet && make bench >/dev/null
```

Then `make clean && make build` once more if you want the build visible in the
`make verify` segment — it adds about 40 seconds, so the timings below assume
artifacts are warm.

---

## 0:00–0:30 — the original, and the claim

**Screen:** `upstream/pdjson/` open in the terminal, then `README.md` top table.

```sh
wc -l upstream/pdjson/pdjson.c upstream/pdjson/pdjson.h
git -C . log -1 --format='%H' 2>/dev/null; cat .port-mortem.toml | head -14
```

**Narration:**

> "pdjson is a streaming JSON parser in C — about 990 lines, no dependencies,
> public domain. It's pinned here at commit 78fe04b.
>
> The claim is not 'I rewrote it in Zig'. It's this: on the original test suite,
> a fixed corpus, an independent standards corpus, and a published fuzz run, the
> Zig implementation produces the *same defined observable behaviour* as the
> pinned C original — without using the original parser.
>
> Everything after this is me trying to falsify that."

---

## 0:30–1:00 — provenance, and proving the tests are untouched

**Screen:** hash verification, then deliberately break it.

```sh
sh scripts/verify-upstream-hashes.sh

echo "/* tampered */" >> upstream/pdjson/tests/tests.c
sh scripts/verify-upstream-hashes.sh          # fails
git checkout upstream/pdjson/tests/tests.c
sh scripts/verify-upstream-hashes.sh          # passes again
```

**Expected:** `upstream hash verification OK (9 files match ...)`, then
`FAIL drift: tests/tests.c` with both digests, then OK again.

**Narration:**

> "Nine files, hash-pinned. 'We didn't touch the original tests' is the kind of
> claim you'd normally have to take on trust — so it's checked instead. Watch
> what happens if I edit one byte of the test file.
>
> That check runs first in `make verify` and as a separate CI job, because if the
> baseline drifted, nothing downstream is measuring what it says it is."

---

## 1:00–1:45 — `make verify`

**Screen:** the full pipeline. Let it run; do not cut.

```sh
make verify
```

**Expected:** 23 numbered steps, ending in `VERIFY OK`. Roughly 3–4 minutes with
warm artifacts, so **speed the video up 3–4× through the middle**, holding real
time on step 4 (no C linkage), step 7 (upstream suite) and step 16 (claims).

**Narration (over the sped-up section):**

> "One command. It verifies the pin, builds both implementations, proves the Zig
> artifact contains no C parser code, checks the ABI, runs the untouched upstream
> suite against Zig, runs the differential corpus, fuzzes, scans for escape
> hatches, benchmarks, and finally validates every published claim against the
> files it just generated.
>
> It fails on the first thing that doesn't hold. The number in the README isn't
> typed in — it's read out of an artifact this produced."

---

## 1:45–2:20 — the original tests, linked against Zig

**Screen:** compile and run, then the symbol check.

```sh
cc -std=c99 -pedantic -Wall -Wextra -Wno-missing-field-initializers \
   -o /tmp/tests_zig upstream/pdjson/tests/tests.c zig-out/lib/libpdjson.a
/tmp/tests_zig

ar t zig-out/lib/libpdjson.a
nm -g zig-out/lib/libpdjson.a | grep ' T _\?json_' | wc -l
sh scripts/verify-no-c-linkage.sh
```

**Expected:** 18 `PASS` lines and `18 pass, 0 fail`; one object
(`libpdjson_zcu.o`); `22`; and the linkage check passing.

**Narration:**

> "That's upstream's own test file, compiled in place from the pinned tree, with
> nothing changed but the link line — it links `libpdjson.a` and no `pdjson.o`.
> Eighteen of eighteen.
>
> One object in the archive, and it's Zig's. Twenty-two exported symbols, matching
> the header. The linkage check also looks for pdjson.c's twenty-three internal
> symbols and for any undefined `json_*` import — because 'it doesn't secretly
> wrap the C' is exactly the sort of thing that should be checked, not asserted."

---

## 2:20–3:10 — matching transcripts on the hard cases

**Screen:** two panes side by side, C on the left, Zig on the right. Or run
sequentially and `diff`.

```sh
# Unicode: an escaped surrogate pair at the top of the range
./build/transcript_c        next tests/conformance/fixtures/uni-escaped-pair-max.json
./zig-out/bin/transcript_zig next tests/conformance/fixtures/uni-escaped-pair-max.json

# An embedded NUL, which truncates the diagnostic a C caller sees
./build/transcript_c        next tests/conformance/fixtures/nul-bare.json
./zig-out/bin/transcript_zig next tests/conformance/fixtures/nul-bare.json

# Positions and line numbers across a multi-line document
diff <(./build/transcript_c        next tests/conformance/fixtures/ws-multiline.json) \
     <(./zig-out/bin/transcript_zig next tests/conformance/fixtures/ws-multiline.json) \
  && echo "IDENTICAL"

# Streaming with reset between values, via the separator API
diff <(./build/transcript_c        sep tests/conformance/fixtures/stream-newline-separated.json) \
     <(./zig-out/bin/transcript_zig sep tests/conformance/fixtures/stream-newline-separated.json) \
  && echo "IDENTICAL"
```

**Narration:**

> "This is the actual proof mechanism. Two independent programs — one linking the
> C original, one using the Zig library — drive their parser through the same
> script and record everything observable: the event, the token bytes in hex, the
> number as raw IEEE-754 bits, the line, the byte position, the depth, the
> container context, the diagnostic.
>
> Tokens are hex because they legitimately contain NUL and invalid UTF-8. Numbers
> are bit patterns because negative zero and NaN payloads matter.
>
> Look at the NUL case — the error message is `unexpected byte '`, cut off
> mid-sentence. That's `printf`'s `%c` writing an actual NUL into a public field.
> The port reproduces that, because it's the behaviour.
>
> Equivalence here means the files are byte-identical. Not similar."

---

## 3:10–3:50 — fuzzing, and whether the harness has teeth

**Screen:** the published session log, then mutation testing.

```sh
python3 -c "import json;d=json.load(open('fuzz/logs/session-published.json'));print(json.dumps({k:d[k] for k in ['seed','elapsed_seconds','cases','cases_per_second','modes','divergences','zig_crashes','timeouts']},indent=2))"

cat artifacts/mutation-report.json | python3 -m json.tool | head -30
```

**Narration:**

> "The published fuzz session: exact seed, exact duration, exact case count.
> Mutation, grammar generation, and number and Unicode boundary generators, run
> through five drive modes. Zero divergences.
>
> But a comparison harness that never fails and one that *cannot* fail look
> identical from outside. So twelve deliberate defects get injected into the Zig
> implementation — wrong escape mapping, off-by-one surrogate range, dropped NUL
> terminator — and the differential has to catch each one.
>
> The first honest run caught eight of twelve. The four survivors were real gaps
> in my corpus, and it grew from 142 fixtures to 214 to close them.
>
> Before that, two runs scored a false twelve out of twelve. Both were 'catching'
> mutants on cases where the C original crashes — so every mutant differed, for
> reasons that had nothing to do with the mutation. That's in DECISIONS.md D-17.
> The tooling needed verifying too, and its failure mode was to flatter me."

---

## 3:50–4:20 — the divergence that turned out to be an upstream bug

**Screen:** the reproducer under sanitizers, then the issue.

```sh
cc -std=c99 -g -fsanitize=address,undefined -I upstream/pdjson \
   -o /tmp/repro tests/upstream-bugs/repro_oom_stack.c upstream/pdjson/pdjson.c
ASAN_OPTIONS=detect_leaks=0 /tmp/repro 2>&1 | grep -E "runtime error|SUMMARY|#0 " | head -5

python3 -c "import json;d=json.load(open('artifacts/differential-summary.json'));print('divergences:',d['divergences'],' sanitizer-confirmed upstream UB:',d['upstream_ub'])"
```

**Expected:** UBSan "member access within null pointer", ASan SEGV at
`json_get_context pdjson.c:912`, then `divergences: 0  ... upstream UB: 43`.

**Narration:**

> "The first differential run reported eight divergences and thirty-two crashes —
> and the crashes were on the C side.
>
> `push()` increments the stack index *before* it grows the stack. When the
> allocation fails, it leaves the index pointing at a slot that was never
> allocated. `json_get_context` then reads it. Null dereference, or an
> out-of-bounds read if the stack already had a block.
>
> Here's where a port can lie to itself. I could quietly add a bounds check and
> watch the number go to zero. Instead: when the two disagree, the harness re-runs
> the case against an ASan and UBSan build of the *original*. If the sanitizer
> fires, it's classified as upstream undefined behaviour and counted separately,
> with the sanitizer output attached.
>
> All 43 resolved to one line. That's upstream issue #36. The zero divergences
> means zero on inputs where the original is actually defined — which is a
> narrower claim, and a true one.
>
> There's a second bug too — issue #37, where byte 0xFF is read as end-of-input
> by the buffer source, so it disagrees with the library's own FILE* source. The
> port *reproduces* that one deliberately, because it's well-defined behaviour,
> just wrong — and silently fixing it would break the equivalence claim in the
> least visible way possible."

---

## 4:20–4:45 — honest benchmark

**Screen:** the generated table.

```sh
sed -n '/BENCH:BEGIN/,/BENCH:END/p' README.md
```

**Narration:**

> "The port is slower. Nine of twelve workloads, ratios mostly between 0.79 and
> 0.93 — that's C being 7 to 21 percent faster.
>
> Two things worth saying. ReleaseSafe and ReleaseFast are within two percent of
> each other, so the gap isn't the cost of safety checks — the shipped library
> keeps bounds and overflow checking on and pays almost nothing for it.
>
> And my first explanation for the gap was wrong. I assumed it was the null
> checks the port adds on the source callbacks, removed them, and measured zero
> percent improvement. Profiling showed the real cause: clang inlines a
> byte-append helper that Zig kept out of line. Fixing that got large-mixed from
> 0.70 to 0.87.
>
> The rest is unexplained, and it's reported that way rather than chased. Tuning
> further against this exact workload set is how a benchmark stops meaning
> anything."

---

## 4:45–5:00 — decisions, limits, reproduction

**Screen:** `DECISIONS.md` headings, then the README limitations section.

```sh
grep '^## D-' DECISIONS.md
sed -n '/## Known limitations/,/## Decisions/p' README.md | head -20
```

**Narration:**

> "Seventeen decisions, including the two that changed and the ones I got wrong
> first.
>
> The limitations are in the README, not hidden: the differential corpus drives
> the buffer source only, so the FILE* and callback sources aren't compared
> transcript by transcript — and given that bug #37 is precisely a disagreement
> between two sources, that's the hole most likely to hold something. ABI is
> verified on two targets, not universally. The upstream issues are filed, not
> triaged. And this is demonstrated equivalence, not proven equivalence.
>
> `make verify`. One command, no network. Repository link's below."

---

## Recording checklist

- [ ] `make verify` passes end to end immediately before recording
- [ ] `docs/final-audit.md` says PASS
- [ ] Working tree clean; `git status` shows nothing
- [ ] `fuzz/logs/session-published.json` is the run being quoted
- [ ] Benchmark table in the README regenerated from the current artifact
- [ ] Terminal 120×36, readable at 720p, scrollback cleared between segments
- [ ] No credentials, tokens, home paths or unrelated windows on screen
- [ ] Audio checked on the first 30 seconds before recording the rest

### Fallback plan

If a live command misbehaves during recording:

1. **Do not re-take with a doctored command.** Stop, fix the underlying issue,
   re-run `make verify`, start the segment again.
2. If `make verify` is too slow for the 1:00 segment, record it separately at
   real speed and cut in a sped-up version, keeping the final `VERIFY OK` frame
   at real time. Say on camera that it is sped up.
3. If the network is unavailable, skip the JSONTestSuite step — `make verify`
   already skips it cleanly and says so. Mention it rather than hiding the skip.
4. If mutation testing is too slow to show live (~15 minutes), show
   `artifacts/mutation-report.json` instead and say it was generated by
   `make mutation`.

---

## YouTube metadata

**Title**

> Rewriting a C JSON parser in Zig — and proving it behaves identically (Port Mortem 2026)

**Description**

```
pdjson-zig is a clean-room Zig rewrite of skeeto/pdjson, a public-domain C
streaming JSON parser, built for Port Mortem 2026 (Track G, C -> Zig).

The interesting part isn't the parser. It's the proof.

Two independent programs drive the C original and the Zig port through the same
script and emit deterministic behaviour transcripts covering every value
reachable through the public header: event, token bytes, IEEE-754 number bits,
diagnostic string, line, byte position, depth, container context. Equivalence
means the transcripts are byte-identical.

Results, all reproducible with one command:
  - 18/18 assertions in the UNMODIFIED upstream test suite, linked against only
    the Zig library
  - 0 divergences across 6,104 fixed-corpus + 3,498 JSONTestSuite comparisons,
    covering all three documented input sources
  - 0 divergences in a published differential fuzz session
  - 12/12 deliberately injected defects caught by the harness
  - 3 real bugs found in the original, filed with minimal reproducers
  - Honest benchmark: the Zig port is SLOWER on 9 of 12 workloads

Chapters:
0:00  The original, and the falsifiable claim
0:30  Provenance: proving the upstream tests are untouched
1:00  make verify
1:45  The original test suite, linked against Zig
2:20  Matching transcripts on Unicode, NUL bytes, positions, streaming
3:10  Fuzzing, and testing the test harness with injected defects
3:50  The divergence that turned out to be an upstream memory-safety bug
4:20  Honest benchmark results
4:45  Decisions and limitations

Upstream issues filed:
  https://github.com/skeeto/pdjson/issues/36
  https://github.com/skeeto/pdjson/issues/37

Repository: <REPO_URL>
Decisions:  <REPO_URL>/blob/main/DECISIONS.md
Write-up:   <REPO_URL>/blob/main/docs/write-up.md

pdjson is by Chris Wellons (skeeto), released into the public domain.
```

**Tags**

`zig`, `c`, `json parser`, `differential testing`, `porting`, `memory safety`,
`fuzzing`, `mutation testing`, `abi compatibility`, `port mortem`,
`systems programming`, `sanitizers`, `verification`

**Thumbnail text**

> `0 DIVERGENCES` / `2 BUGS FOUND IN THE ORIGINAL` / small: `C → Zig, proven`

Avoid claiming a speedup on the thumbnail. There isn't one.

**Devfolio video field**

> Five-minute walkthrough: the falsifiable claim, provenance checks, the
> untouched upstream suite running against the Zig library, byte-identical
> behaviour transcripts on Unicode and embedded-NUL cases, the fuzz session,
> mutation testing of the harness itself, the two upstream bugs found (including
> a sanitizer-confirmed null dereference), and honest benchmark results showing
> the port is slower.
