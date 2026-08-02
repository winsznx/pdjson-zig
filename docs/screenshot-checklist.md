# Screenshot capture checklist

Devfolio requires **1–6 gallery images** and explicitly requires them to be real
screenshots of the running project, not generated stand-ins. Capture these
yourself; nothing in this repository will produce them for you.

Six shots, in priority order — if you only capture three, take 1, 2 and 4.

Shot 4 is the only one that is a browser rather than a terminal, and it is the one a reader with three seconds will read fastest.

Every command below was run before this list was written, and the "what you
should see" notes describe the output that actually came back. If one of them
produces something different on your machine, that is a finding, not a typo.

## Before you start

```sh
cd pdjson-zig
make build          # warm, so nothing waits on a cold compile
```

Terminal at roughly 120×40, a readable monospace font, dark or light both fine.
Clear the scrollback between shots so each image starts at the top.

Check nothing sensitive is on screen: no tokens, no unrelated windows, and
consider `cd` into a path without your username visible if you care about that.

---

## 1. `make verify` — the whole pipeline passing

```sh
clear && make verify 2>&1 | tail -45
```

Capture the tail showing the last few numbered steps and the `VERIFY OK` banner.
This is the single most useful image: it shows the claim validation step passing
right above the banner.

## 2. The upstream test suite running against the Zig library

```sh
clear
cc -std=c99 -pedantic -Wall -Wextra -Wno-missing-field-initializers \
   -o /tmp/t upstream/pdjson/tests/tests.c zig-out/lib/libpdjson.a
/tmp/t
ar t zig-out/lib/libpdjson.a
```

Shows 18 `PASS` lines, `18 pass, 0 fail`, and that the archive contains one Zig
object — the "no C parser inside" evidence, in one frame.

## 3. An upstream bug, under sanitizers

```sh
clear
cc -std=c99 -g -fsanitize=address,undefined -I upstream/pdjson \
   -o /tmp/repro tests/upstream-bugs/repro_oom_stack.c upstream/pdjson/pdjson.c
ASAN_OPTIONS=detect_leaks=0 /tmp/repro 2>&1 | head -14
```

The UBSan "member access within null pointer" line and the ASan SEGV at
`json_get_context pdjson.c:912`. Concrete evidence for the Bug Catcher claim.

## 4. Upstream confirmed and fixed all three

Browser, not a terminal. Open the three issues and arrange so the **closed**
state is visible on each, ending on #38 where the maintainer's comment is:

- <https://github.com/skeeto/pdjson/issues/36>
- <https://github.com/skeeto/pdjson/issues/37>
- <https://github.com/skeeto/pdjson/issues/38>

A single browser window with the three tabs and #38 in front is the strongest
frame; a tiled view of all three works too. What must be legible: the closed
badge, and "Solid findings, excellent analysis."

If a browser shot is awkward, the generated README section is an acceptable
substitute and has clickable links to all three issues and all three fix commits:

```sh
clear && sed -n '/UPSTREAM:BEGIN/,/UPSTREAM:END/p' README.md | grep -v '<!--'
```

This shot replaced the provenance tamper check in the six, because external
confirmation of the findings is the stronger evidence for a reader who has three
seconds. The tamper check is still worth capturing if you have room — it is in
the optional list below.

## 5. Two transcripts, byte-identical

```sh
clear
./build/transcript_c        next tests/conformance/fixtures/uni-escaped-pair-max.json
./zig-out/bin/transcript_zig next tests/conformance/fixtures/uni-escaped-pair-max.json
diff <(./build/transcript_c        next tests/conformance/fixtures/nul-bare.json) \
     <(./zig-out/bin/transcript_zig next tests/conformance/fixtures/nul-bare.json) \
  && echo IDENTICAL
```

Shows the actual proof mechanism, on a surrogate pair and on an embedded NUL.

## 6. The honest costs

```sh
clear
sed -n '/BENCH:BEGIN/,/BENCH:END/p' README.md | grep -v '<!--'
sed -n '/SIZE:BEGIN/,/SIZE:END/p'   README.md | grep -v '<!--'
```

Both generated tables in one frame: slower on 9 of 12 workloads, and 2.42x the
stripped binary in a consumer. Including these rather than hiding them is the
point, and both are spliced from artifacts so they cannot drift.

---

## Optional extras, if you have room

### Provenance, including the tamper check failing

```sh
clear
sh scripts/verify-upstream-hashes.sh
echo "/* tampered */" >> upstream/pdjson/tests/tests.c
sh scripts/verify-upstream-hashes.sh
git checkout upstream/pdjson/tests/tests.c
sh scripts/verify-upstream-hashes.sh
```

OK, then a red `FAIL drift` with both digests, then OK again. Proves the
"untouched tests" claim is checked rather than asserted.

**Remember the `git checkout`** — leaving the file modified will fail
`make verify`, the release gate, and shot 1.

What you should see: `OK (9 files match ...)`, then the two digests and
`FAILED (1 problem(s), 9 file(s) checked)`, then `OK` again.

### More

Pick whichever of these reads best on your terminal:

```sh
# The compile-time ABI contract, proven able to fail
clear && sh scripts/abi-contract-negative.sh 2>&1 | tail -22
```

Ten injected layout drifts, each caught by `zig build`, with a control that must
still build and a 32-bit deferral check. This is the clearest single image of the
project's habit of testing its own checks.

```sh
# Or: what this build decided about the target
clear && zig build diagnose
```

`char` signedness, the 0xFF/EOF mode, and whether the compile-time ABI contract
applies here — the two target-dependent decisions that are invisible in the
source.

---

## After capturing

Save as PNG or JPG. Then either hand them to me and I will attach them with
`getSignedUploadUrl` + `createHackathonProject`, or upload them yourself through
the Devfolio web form.

The rest of the submission copy is staged in
[`devfolio-submission.md`](devfolio-submission.md) — every field, within the
platform's length limits, with every figure traceable to an artifact that
`make verify` regenerates.
