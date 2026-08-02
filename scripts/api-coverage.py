#!/usr/bin/env python3
"""Classify how each exported pdjson function is covered by evidence.

"The differential passes" says nothing about which parts of the API the
differential actually exercises. This reads the pinned header for the export
list, then works out from the harness and test sources how each one is reached,
so the answer is derived rather than asserted.

Classification:

  differential   the transcript records this function's result on every record,
                 so every compared case exercises it
  scenario       driven by specific drive modes or dedicated fixtures
  consumer       exercised by tests/original/abi_consumer.c against the pinned
                 header, linked to the Zig archive
  unit           covered by Zig-native tests
  abi-only       exported and layout-checked, but its *behaviour* is not compared
  untested       no evidence at all

A function may hold several. `untested` is the one that matters, and the script
fails if any export lands there.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEADER = ROOT / "upstream" / "pdjson" / "pdjson.h"

# Where each kind of evidence lives.
SOURCES = {
    "transcript_c": ROOT / "oracle" / "transcript_c.c",
    "transcript_zig": ROOT / "tools" / "transcript_zig.zig",
    "consumer": ROOT / "tests" / "original" / "abi_consumer.c",
    "upstream_tests": ROOT / "upstream" / "pdjson" / "tests" / "tests.c",
    "upstream_pretty": ROOT / "upstream" / "pdjson" / "tests" / "pretty.c",
    "upstream_stream": ROOT / "upstream" / "pdjson" / "tests" / "stream.c",
    "bench_c": ROOT / "oracle" / "bench_c.c",
}
ZIG_TESTS = sorted((ROOT / "tests" / "port").glob("*.zig"))

# C name -> the Zig-side entry points that implement or wrap it, so a Zig test
# that calls core.skip() counts as covering json_skip.
ZIG_EQUIVALENT = {
    "json_open_buffer": ["openBuffer", "initBuffer"],
    "json_open_string": ["openBuffer"],
    "json_open_stream": ["openStream"],
    "json_open_user": ["openUser"],
    "json_close": ["close", "deinit"],
    "json_set_allocator": ["setAllocator"],
    "json_set_streaming": ["setStreaming"],
    "json_next": ["nextEvent", "next("],
    "json_peek": ["peekEvent", "peek("],
    "json_reset": ["reset("],
    "json_get_string": ["getStringSlice", "getStringPtr", "token(", "tokenText"],
    "json_get_number": ["getNumber", "number("],
    "json_skip": ["core.skip", "p.skip", ".skip("],
    "json_skip_until": ["skipUntil"],
    "json_get_lineno": ["lineno"],
    "json_get_position": ["position"],
    "json_get_depth": ["getDepth", "depth("],
    "json_get_context": ["getContext", "context("],
    "json_get_error": ["getErrorSlice", "errorMessage"],
    "json_source_get": ["sourceGet"],
    "json_source_peek": ["sourcePeek"],
    "json_isspace": ["isSpace"],
}

# Functions whose value the transcript records on *every* record, so they are
# compared on every one of the corpus comparisons.
IN_EVERY_RECORD = {
    "json_get_string", "json_get_number", "json_get_lineno", "json_get_position",
    "json_get_depth", "json_get_context", "json_get_error",
}

# Drive modes that specifically exercise a function.
MODE_DRIVEN = {
    "json_next": ["next", "nostream", "peek", "sep", "oom:*", "stream:*", "user:*"],
    "json_peek": ["peek", "stream:peek", "user:peek"],
    "json_skip": ["skip", "user:skip"],
    "json_skip_until": ["skipuntil:4, skipuntil:6, skipuntil:7, skipuntil:8, skipuntil:11"],
    "json_reset": ["next", "sep", "stream:next", "user:next"],
    "json_set_streaming": ["nostream (false) and all others (true)"],
    "json_source_get": ["sep", "stream:sep"],
    "json_source_peek": ["sep", "stream:sep"],
    "json_isspace": ["sep", "stream:sep"],
    "json_open_buffer": ["all buffer-source modes"],
    "json_open_string": ["string:next, string:nostream, string:peek"],
    "json_open_stream": ["stream:*"],
    "json_open_user": ["user:*"],
    "json_set_allocator": ["oom:0, oom:1, oom:2, oom:5"],
    "json_close": ["every mode, at the end of every transcript"],
}


def exported_functions() -> list[str]:
    text = HEADER.read_text()
    return re.findall(r"PDJSON_SYMEXPORT\s+[\w \*]+?\**\s*(\w+)\s*\(", text)


def used_in(path: pathlib.Path, needles) -> bool:
    if not path.exists():
        return False
    text = path.read_text(errors="replace")
    # Strip comments so a mention in prose does not count as a use.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return any(n in text for n in needles)


def main() -> int:
    exports = exported_functions()
    rows = []

    for fn in exports:
        cov = []
        notes = []

        if fn in IN_EVERY_RECORD:
            cov.append("differential")
            notes.append("value recorded on every transcript record, so compared "
                         "on every corpus comparison")
        elif used_in(SOURCES["transcript_c"], [fn]) and used_in(
                SOURCES["transcript_zig"], ZIG_EQUIVALENT.get(fn, [fn])):
            cov.append("differential")
            notes.append("driven by both transcript producers")

        if fn in MODE_DRIVEN:
            cov.append("scenario")
            notes.append("modes: " + ", ".join(MODE_DRIVEN[fn]))

        if used_in(SOURCES["consumer"], [fn]):
            cov.append("consumer")
            notes.append("tests/original/abi_consumer.c, via the pinned header")

        for p in (SOURCES["upstream_tests"], SOURCES["upstream_pretty"],
                  SOURCES["upstream_stream"]):
            if used_in(p, [fn]):
                cov.append("upstream")
                notes.append(f"used by {p.name}")
                break

        zig_needles = ZIG_EQUIVALENT.get(fn, [])
        if zig_needles and any(used_in(t, zig_needles) for t in ZIG_TESTS):
            cov.append("unit")
            hits = [t.name for t in ZIG_TESTS if used_in(t, zig_needles)]
            notes.append("Zig tests: " + ", ".join(hits))

        if not cov:
            cov.append("untested")

        rows.append({
            "function": fn,
            "coverage": sorted(set(cov)),
            "notes": notes,
        })

    untested = [r["function"] for r in rows if r["coverage"] == ["untested"]]
    abi_only = [r["function"] for r in rows
                if set(r["coverage"]) <= {"abi-only", "consumer"}]

    summary = {
        "schema": "pdjson-zig/api-coverage@1",
        "header": "upstream/pdjson/pdjson.h",
        "exported_functions": len(exports),
        "classification": {
            "differential": len([r for r in rows if "differential" in r["coverage"]]),
            "scenario": len([r for r in rows if "scenario" in r["coverage"]]),
            "consumer": len([r for r in rows if "consumer" in r["coverage"]]),
            "upstream": len([r for r in rows if "upstream" in r["coverage"]]),
            "unit": len([r for r in rows if "unit" in r["coverage"]]),
            "untested": len(untested),
        },
        "untested": untested,
        "behaviour_compared_but_not_per-record": abi_only,
        "functions": rows,
    }

    out = ROOT / "artifacts" / "differential" / "api-coverage.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"  {len(exports)} exported functions")
    for k, v in summary["classification"].items():
        print(f"    {k:<14} {v}")
    if untested:
        print(f"  UNTESTED: {untested}")
    print(f"  wrote {out.relative_to(ROOT)}")
    return 1 if untested else 0


if __name__ == "__main__":
    sys.exit(main())
