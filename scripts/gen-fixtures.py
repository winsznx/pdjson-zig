#!/usr/bin/env python3
"""Generate the fixed conformance corpus in tests/conformance/fixtures/.

The files are committed, so this script only needs to run when adding cases.
Binary content (embedded NUL, invalid UTF-8, lone 0xFF) is why this is a
generator rather than hand-written files: those bytes do not survive casual
editing.

Each fixture is a raw input document. The differential harness runs every
fixture through every transcript mode, so a single file exercises the plain
event loop, peek, skip, reset, the separator API, non-streaming strictness,
and the allocation-failure schedules.
"""
import os
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "tests" / "conformance" / "fixtures"

CASES: dict[str, bytes] = {}


def add(name: str, data) -> None:
    if isinstance(data, str):
        data = data.encode("utf-8")
    assert name not in CASES, f"duplicate fixture {name}"
    CASES[name] = data


# -- literals ---------------------------------------------------------------
add("lit-null", "null")
add("lit-true", "true")
add("lit-false", "false")
add("lit-null-trailing", "nullx")
add("lit-truncated-tru", "tru")
add("lit-truncated-nul", "nul")
add("lit-case-True", "True")
add("lit-bare-word", "undefined")

# -- integers ---------------------------------------------------------------
add("num-zero", "0")
add("num-neg-zero", "-0")
add("num-one", "1")
add("num-int", "1024")
add("num-neg", "-1")
add("num-leading-zero", "01")
add("num-neg-leading-zero", "-01")
add("num-plus", "+1")
add("num-lone-minus", "-")
add("num-lone-dot", ".")
add("num-i64-max", "9223372036854775807")
add("num-i64-overflow", "9223372036854775808")
add("num-u64-overflow", "18446744073709551616")
add("num-huge-digits", "1" + "0" * 400)

# -- decimals and exponents -------------------------------------------------
add("num-decimal", "1.5")
add("num-decimal-trailing-dot", "1.")
add("num-decimal-leading-dot", ".5")
add("num-exp-lower", "1e3")
add("num-exp-upper", "1E3")
add("num-exp-plus", "1e+3")
add("num-exp-minus", "1e-3")
add("num-exp-empty", "1e")
add("num-exp-sign-empty", "1e+")
add("num-exp-huge", "1e999")
add("num-exp-tiny", "1e-999")
add("num-double-min-subnormal", "4.9406564584124654e-324")
add("num-double-max", "1.7976931348623157e308")
add("num-double-just-over-max", "1.7976931348623159e308")
add("num-many-frac-digits", "0." + "3" * 400)
add("num-dot-dot", "1..2")
add("num-double-exp", "1e3e3")
add("num-neg-only-exp", "-e3")

# -- strings ----------------------------------------------------------------
add("str-empty", '""')
add("str-simple", '"foo"')
add("str-unterminated", '"foo')
add("str-all-escapes", r'"\" \\ \/ \b \f \n \r \t"')
add("str-invalid-escape", r'"\x"')
add("str-escape-at-eof", '"\\')
add("str-raw-newline", '"a\nb"')
add("str-raw-tab", '"a\tb"')
add("str-del-byte", '"\x7f"')
add("str-embedded-nul-escape", '"a\\u0000b"')

# Every unescaped control byte, individually. The parser rejects 0x00-0x1F in
# a string, and the boundary at 0x1F/0x20 is exactly the kind of off-by-one a
# port can get wrong without any structural test noticing.
for _cc in range(0x00, 0x21):
    add(f"ctrl-raw-{_cc:02x}", b'"a' + bytes([_cc]) + b'b"')
# ...and the escaped forms of the same range, which are legal.
for _cc in range(0x00, 0x21):
    add(f"ctrl-escaped-{_cc:02x}", ('"a\\u%04xb"' % _cc))

# -- unicode ----------------------------------------------------------------
add("uni-basic", r'"hello"')
add("uni-bmp", r'"é中￿"')
add("uni-surrogate-pair", r'":𐀀"')
add("uni-surrogate-pair-max", r'"􏿿"')
add("uni-high-alone", r'"\uD800"')
add("uni-high-then-plain", r'"\uD800e"')
add("uni-low-alone", r'"\uDC00"')
add("uni-misordered", r'":\uDc00\uD800"')
add("uni-high-then-raw", '"\\uD800x"')
add("uni-short-escape", r'"\u12"')
add("uni-bad-hex", r'"\u12g4"')
add("uni-truncated", '"\\u')
add("utf8-2byte", '"é"')
add("utf8-3byte", '"中"')
add("utf8-4byte", '"\U0001f600"')
# Escape-form surrogate pairs at both ends of the range. The literal UTF-8
# forms above do not exercise the surrogate decoder at all.
add("uni-escaped-pair-min", r'"\uD800\uDC00"')
add("uni-escaped-pair-max", r'"\uDBFF\uDFFF"')
add("uni-escaped-pair-hi-end", r'"\uDBFF\uDC00"')
add("uni-escaped-pair-lo-end", r'"\uD800\uDFFF"')
add("uni-escaped-just-below-hi", r'"\uD7FF"')
add("uni-escaped-just-above-lo", r'"\uE000"')

# -- invalid UTF-8 ----------------------------------------------------------
add("bad-utf8-lone-continuation", b'"\x80"')
add("bad-utf8-overlong-c0", b'"\xc0\x80"')
add("bad-utf8-overlong-c1", b'"\xc1\xbf"')
add("bad-utf8-truncated-2byte", b'"\xc3"')
add("bad-utf8-truncated-3byte", b'"\xe4\xb8"')
add("bad-utf8-surrogate-encoded", b'"\xed\xa0\x80"')
add("bad-utf8-f5", b'"\xf5\x80\x80\x80"')
add("bad-utf8-fe", b'"\xfe"')
add("bad-utf8-ff", b'"\xff"')
add("bad-utf8-e0-overlong", b'"\xe0\x80\x80"')
add("bad-utf8-f0-overlong", b'"\xf0\x80\x80\x80"')
add("bad-utf8-f4-out-of-range", b'"\xf4\x90\x80\x80"')

# -- raw 0xFF / EOF conflation (upstream defect, see docs/upstream-bug-0xff.md)
add("ff-bare", b"\xff")
add("ff-in-string", b'"\xff"')
add("ff-after-number", b"1\xff")
add("ff-in-array", b"[\xff]")
add("ff-run", b"\xff\xff\xff")
add("fe-bare", b"\xfe")

# -- embedded NUL in the input ---------------------------------------------
add("nul-bare", b"\x00")
add("nul-in-string", b'"a\x00b"')
add("nul-after-number", b"1\x00")
add("nul-in-array", b"[\x00]")
add("nul-leading", b"\x001")

# -- containers -------------------------------------------------------------
add("arr-empty", "[]")
add("arr-flat", "[1, 2, 3]")
add("arr-mixed", '[1, "two", true, null, {}, []]')
add("arr-trailing-comma", "[1,]")
add("arr-leading-comma", "[,1]")
add("arr-double-comma", "[1,,2]")
add("arr-unclosed", "[1, 2, 3")
add("arr-extra-close", "[1]]")
add("arr-close-mismatch", "[1}")
add("obj-empty", "{}")
add("obj-simple", '{"abc": -1}')
add("obj-nested", '{"a": {"b": {"c": [1, 2]}}}')
add("obj-trailing-comma", '{"a":1,}')
add("obj-nonstring-key", "{1:2}")
add("obj-missing-colon", '{"a" 1}')
add("obj-missing-value", '{"a":}')
add("obj-duplicate-key", '{"a":1,"a":2}')
add("obj-unclosed", '{"a":1')
add("obj-close-mismatch", '{"a":1]')
add("obj-value-then-junk", '{"a":1 "b":2}')

# -- nesting ----------------------------------------------------------------
add("deep-array-32", "[" * 32 + "]" * 32)
add("deep-array-512", "[" * 512 + "]" * 512)
add("deep-object-256", '{"a":' * 256 + "1" + "}" * 256)
add("deep-unclosed-1000", "[" * 1000)

# -- whitespace, positions, lines -------------------------------------------
add("ws-leading", "  \t\r\n  1")
add("ws-trailing", "1  \t\r\n  ")
add("ws-multiline", "[\n1,\n2,\n3\n]")
add("ws-vertical-tab", "\x0b1")
add("ws-formfeed", "\x0c1")
add("ws-crlf-lines", "1\r\n2\r\n3")
add("empty-input", "")
add("only-whitespace", " \n\t\r ")

# -- streaming --------------------------------------------------------------
add("stream-numbers", "1 10 100 2002")
add("stream-newline-separated", "1\n10 \n100 \n 2002")
add("stream-mixed", '{"foo": [1, 2, 3]}\n[]\n"name"')
add("stream-no-separator", "12")
add("stream-adjacent-objects", "{}{}")
add("stream-error-midway", "1 2 [ 3")
add("stream-trailing-junk", "1 @")

# -- misc malformed ---------------------------------------------------------
add("junk-only", "@")
add("junk-brace-close", "}")
add("junk-bracket-close", "]")
add("junk-comma", ",")
add("junk-colon", ":")
add("comment-slash", "// nope\n1")
add("comment-block", "/* nope */ 1")
add("single-quoted", "'a'")
add("nan-literal", "NaN")
add("inf-literal", "Infinity")

# -- large / structural -----------------------------------------------------
add("large-flat-array", "[" + ",".join(str(i) for i in range(2000)) + "]")
add("large-string", '"' + "a" * 5000 + '"')
add("string-just-over-1k", '"' + "b" * 1100 + '"')  # crosses the 1024 buffer growth
add("string-exactly-1023", '"' + "c" * 1023 + '"')
add("many-small-docs", " ".join("{}" for _ in range(500)))

# -- regressions from fuzzing ----------------------------------------------
# Found by the published differential fuzz session at ~30M cases. A number
# token leaves "97634922337286237e3\0" in the token buffer; the next value is
# an unterminated string starting "0x", so json_get_number() sees "0x" followed
# by the previous token's tail -- a 19-hex-digit float. std.fmt.parseFloat
# truncated rather than rounded there, one ULP off libc strtod.
# See DECISIONS.md D-18 and src/strtod.zig parseHexFloat.
add("regress-fuzz-hexfloat-stale-buffer", b'97634922337286237e3"0x')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for name, data in sorted(CASES.items()):
        (OUT / f"{name}.json").write_bytes(data)
    print(f"wrote {len(CASES)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
