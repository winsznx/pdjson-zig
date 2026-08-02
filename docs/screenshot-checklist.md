# Screenshot capture checklist

Devfolio requires **1–6 gallery images** and explicitly requires them to be real
screenshots of the running project, not generated stand-ins. Capture these
yourself; nothing in this repository will produce them for you.

Six shots, in priority order — if you only capture three, take 1, 2 and 4.

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

## 3. Provenance, including the tamper check failing

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
`make verify`.

## 4. An upstream bug, under sanitizers

```sh
clear
cc -std=c99 -g -fsanitize=address,undefined -I upstream/pdjson \
   -o /tmp/repro tests/upstream-bugs/repro_oom_stack.c upstream/pdjson/pdjson.c
ASAN_OPTIONS=detect_leaks=0 /tmp/repro 2>&1 | head -14
```

The UBSan "member access within null pointer" line and the ASan SEGV at
`json_get_context pdjson.c:912`. Concrete evidence for the Bug Catcher claim.

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

## 6. The honest benchmark

```sh
clear && sed -n '/BENCH:BEGIN/,/BENCH:END/p' README.md
```

The generated table, showing the port is slower on 9 of 12 workloads. Including
this rather than hiding it is the point.

---

## Optional seventh, if you have room

```sh
clear && python3 -m json.tool artifacts/mutation-report.json | head -40
```

12/12 injected defects caught — evidence that the comparison harness can fail.

---

## After capturing

Save as PNG or JPG. Then either hand them to me and I will attach them with
`getSignedUploadUrl` + `createHackathonProject`, or upload them yourself through
the Devfolio web form.

The rest of the submission copy is staged in
[`devfolio-submission.md`](devfolio-submission.md) — every field, within the
platform's length limits, with every figure traceable to an artifact that
`make verify` regenerates.
