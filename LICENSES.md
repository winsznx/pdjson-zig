# Licenses and attribution

## Upstream: skeeto/pdjson

* Repository: <https://github.com/skeeto/pdjson>
* Pinned commit: `78fe04b820dc8817f540bdd87fb22887e0ef3981` (master, 2024-02-22)
* License: **Unlicense** (public domain dedication)
* License text: [`upstream/pdjson/UNLICENSE`](upstream/pdjson/UNLICENSE), preserved
  byte-for-byte and hash-pinned in
  [`artifacts/upstream-manifest.json`](artifacts/upstream-manifest.json)
  (`sha256:7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c`).

The Unlicense places the work in the public domain and imposes no conditions on
copying, modification, redistribution, or relicensing. There is therefore no
copyleft obligation, no attribution requirement, and no license-compatibility
conflict with this repository's own license. Attribution is given here because
it is the honest thing to do, not because the license compels it.

### What is reused, and what is not

| Item | Reused verbatim? | Where |
| --- | --- | --- |
| `pdjson.c` (parser implementation) | **No.** Never compiled into, linked into, called by, or translated into the Zig library. | Kept only as read-only evidence under `upstream/pdjson/`, and compiled separately into the reference oracle and benchmark opponent. |
| `pdjson.h` (public header) | **Yes**, byte-identical. | Copied to `include/pdjson.h`; the two files share `sha256:724f8ad9…dac6`. This is deliberate: a drop-in replacement must present exactly the same contract. It is an interface declaration, not implementation. |
| `tests/tests.c`, `tests/stream.c`, `tests/pretty.c` | **Yes**, byte-identical and unmodified. | Compiled from `upstream/pdjson/tests/` and linked against the Zig library. No copy is made and no line is edited; see `docs/test-preservation.md`. |
| `README.md`, `Makefile` | Not reused. | Present as pinned evidence only. |

The Zig implementation in `src/` was written against the *observable behaviour*
of the original — its event sequence, token bytes, diagnostics, and struct
layout — rather than by transliterating its statements. `DECISIONS.md` records
where the internal structure intentionally diverges.

## This project

`pdjson-zig` is released under the **Unlicense** as well, matching upstream so
that no new restriction is introduced downstream of a public-domain work. See
[`UNLICENSE`](UNLICENSE).

## Third-party test corpora

| Corpus | License | Use |
| --- | --- | --- |
| [JSONTestSuite](https://github.com/nst/JSONTestSuite) (`nst/JSONTestSuite`) | MIT | Fetched on demand by `scripts/fetch-conformance.sh` into `tests/conformance/JSONTestSuite/`, which is **not** committed. Used to distinguish "the Zig port is wrong" from "the C original is wrong" from "the standard leaves it open". Its `LICENSE` is preserved in place when fetched. |

No third-party source code is vendored into the built artifacts. The Zig library
has no dependencies beyond libc (for `malloc`/`free`, and `fgetc`/`ungetc` on the
`FILE *` source only).
