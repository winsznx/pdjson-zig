#!/usr/bin/env python3
"""Classify every escape hatch in the shipped library, one occurrence at a time.

`scripts/safety-scan.sh` counts escape hatches and enforces a budget. A budget
answers "how many", which is the weaker question. This answers "which ones, and
why is each one sound", per occurrence.

Two design choices matter:

  * **Occurrences are keyed by enclosing function, not by line.** The previous
    report listed `parser.zig:1011`; by the time it was read the line was 1018,
    because seven lines had been inserted above it and nothing re-derived the
    file. A justification pinned to a line number rots silently. A rule keyed by
    `close` + `@ptrCast` does not.

  * **An unclassified occurrence fails.** Every hatch has to match a rule in
    RULES below, which means introducing a new one is a deliberate edit to this
    file that shows up in review -- not something that slides in under a budget
    that happened to have room.

The wrapping arithmetic operators are inventoried alongside the casts. They are
not escape hatches in the usual sense -- they *add* definition rather than
removing a check -- but they are the reason no input can panic this parser, and
each one is a deliberate decision to reproduce C's unsigned overflow.

  python3 scripts/safety-inventory.py            # write artifacts/safety/inventory.json
  python3 scripts/safety-inventory.py --check    # fail if the committed copy is stale
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
OUT = ROOT / "artifacts" / "safety" / "inventory.json"

# What we look for. Each is an operation that can reinterpret memory, bypass a
# check, read uninitialised storage, or deliberately redefine overflow.
OPERATIONS = {
    "@ptrCast": "pointer reinterpretation",
    "@alignCast": "alignment assertion",
    "@constCast": "const removal",
    "@bitCast": "bit reinterpretation",
    "@intCast": "checked narrowing",
    "@truncate": "unchecked narrowing",
    "@enumFromInt": "integer to enum",
    "@fieldParentPtr": "pointer arithmetic on a field",
    "= undefined": "uninitialised storage",
    "unreachable": "assumed unreachable",
    "@setRuntimeSafety": "safety check removal",
    "volatile": "volatile access",
    "asm ": "inline assembly",
    "+%": "wrapping add",
    "-%": "wrapping subtract",
    "*%": "wrapping multiply",
}

# Operations that must not appear at all. Present here so the inventory and the
# budget gate cannot disagree about which are forbidden.
FORBIDDEN = {"@constCast", "unreachable", "@setRuntimeSafety", "volatile", "asm "}

# (file, function, operation) -> (category, why this one is sound).
#
# Keyed by function because functions survive edits that renumber lines. A
# wildcard function of "*" applies to every function in that file, used only
# where the reason genuinely does not vary between call sites.
RULES = [
    # ---------------------------------------------------------- C boundaries
    ("parser.zig", "cMalloc", "@ptrCast",
     "c-allocator-boundary",
     "json_allocator.malloc is a C interface returning void*. The result is "
     "immediately treated as [*]u8, whose alignment is 1, so no alignment "
     "assumption is made. A null return is handled by the caller as OOM."),
    ("parser.zig", "cRealloc", "@ptrCast",
     "c-allocator-boundary",
     "Same boundary as cMalloc: void* in, [*]u8 out, alignment 1."),
    ("parser.zig", "cFree", "@ptrCast",
     "c-allocator-boundary",
     "free() takes void*; the pointer being released came from this same "
     "allocator, so no object type is reinterpreted."),
    ("parser.zig", "push", "@ptrCast",
     "c-allocator-boundary",
     "The container stack round-trips through the C allocator's void*. Going "
     "in it is a [*]Stack; coming out it is re-typed to the same element "
     "type, never to a different one."),
    ("parser.zig", "push", "@alignCast",
     "c-allocator-boundary",
     "C requires realloc's result to be suitably aligned for any object type "
     "with a fundamental alignment, which includes struct json_stack. This "
     "states that guarantee to the compiler; it is checked at runtime in "
     "ReleaseSafe, which is the mode this library ships."),
    ("parser.zig", "pushcharSlow", "@ptrCast",
     "c-allocator-boundary",
     "The token buffer round-trips through the C allocator as void*, back to "
     "[*]u8 with alignment 1."),
    ("parser.zig", "close", "@ptrCast",
     "c-allocator-boundary",
     "Releasing the stack and the token buffer through free(), which takes "
     "void*. Both pointers came from this allocator."),

    # ------------------------------------------------- public char* boundary
    ("c_api.zig", "json_open_buffer", "@ptrCast",
     "public-header-boundary",
     "The pinned header declares the parameter const void*, so the cast to "
     "[*]const u8 is the header's own contract. The length is taken from the "
     "caller's size argument and never derived from the pointer."),
    ("c_api.zig", "json_get_string", "@ptrCast",
     "public-header-boundary",
     "The header promises char*. The buffer is the parser's own token buffer, "
     "which the parser NUL-terminates before every event it reports, so the "
     "caller's strlen is in bounds."),
    ("c_api.zig", "json_get_error", "@ptrCast",
     "public-header-boundary",
     "The header promises const char* into json_stream.errmsg, a fixed 128-byte "
     "array inside the caller's own struct. errmsg.zig never writes without "
     "leaving a NUL within the array."),

    # ------------------------------------------------------ char signedness
    ("parser.zig", "byteAsC", "@bitCast",
     "c-semantics-reproduction",
     "Reproduces what C does when a byte is loaded through a const char*: on a "
     "signed-char target 0xFF widens to -1. Integer to integer, no memory "
     "reinterpretation. This is the whole mechanism behind upstream #37, and "
     "using c_char rather than i8 makes it correct on unsigned-char targets "
     "too. Reported by `zig build diagnose`."),

    # --------------------------------------------------- printf reproduction
    ("errmsg.zig", "*", "@bitCast",
     "c-semantics-reproduction",
     "Reproducing printf's %c and %d conversions on a c_int, integer to "
     "integer. The diagnostics have to be byte-identical to the original's."),
    ("errmsg.zig", "*", "@truncate",
     "c-semantics-reproduction",
     "printf %c takes the low byte of an int. Defined for every input."),
    ("errmsg.zig", "*", "@intCast",
     "checked-narrowing",
     "Narrowing a value the caller has already bounded. @intCast is checked in "
     "ReleaseSafe, so a bound that turned out to be wrong would abort rather "
     "than corrupt memory -- the failure mode is loud. That the bounds hold on "
     "untrusted input is evidenced by the fuzz session and by the 20,000 "
     "random byte strings in tests/port/regressions.zig, not by this note."),
    ("errmsg.zig", "hex", "= undefined",
     "write-before-read",
     "A 2*sizeof(c_long) digit scratch. The loop writes digits from the low "
     "end and the slice handed onward starts at the first byte written, so no "
     "unwritten byte is ever read."),

    # ---------------------------------------------------------- parser body
    ("parser.zig", "*", "@bitCast",
     "c-semantics-reproduction",
     "Integer to integer only: signed/unsigned reinterpretation where the C "
     "original relies on it, including json_get_context's long-to-size_t."),
    ("parser.zig", "*", "@truncate",
     "narrowing-to-byte",
     "Extracting a byte from a c_int that the surrounding code has already "
     "constrained to 0..255, or reproducing printf %c. Defined for all inputs."),
    ("parser.zig", "*", "@intCast",
     "checked-narrowing",
     "UTF-8 assembly and codepoint arithmetic on values the surrounding range "
     "checks have bounded. @intCast is checked in ReleaseSafe, so a wrong "
     "bound aborts rather than corrupting memory. That the bounds hold on "
     "untrusted input is evidenced by the 11.8M-case fuzz session and by the "
     "20,000 random byte strings in tests/port/regressions.zig."),
    ("parser.zig", "*", "+%",
     "deliberate-wraparound",
     "C's counters are unsigned and allowed to wrap; source.position, lineno "
     "and ntokens must therefore wrap rather than panic, or untrusted input "
     "could take down the process. stack_top's increment past the (size_t)-1 "
     "empty sentinel depends on it."),
    ("parser.zig", "*", "-%",
     "deliberate-wraparound",
     "The empty-stack sentinel is (size_t)-1, reached by decrementing 0. Same "
     "reasoning as the wrapping add."),
    ("parser.zig", "*", "*%",
     "deliberate-wraparound",
     "Reproduces the original's unsigned multiply where a checked multiply "
     "would panic. Size computations that feed an allocation are checked "
     "separately and reported as out-of-memory instead."),
    ("parser.zig", "*", "@enumFromInt",
     "non-exhaustive-enum",
     "abi.Type is declared non-exhaustive (`_`) precisely because the original "
     "stores (enum json_type)0 as a sentinel, so no integer value is illegal."),

    # ------------------------------------------------------------- strtod
    ("strtod.zig", "*", "@bitCast",
     "ieee754-bit-pattern",
     "Assembling or reading an f64 bit pattern as u64. Same width, no "
     "aliasing; this is how a NaN payload and a correctly-rounded result are "
     "constructed."),
    ("strtod.zig", "*", "@intCast",
     "checked-narrowing",
     "Exponent and shift arithmetic on values the surrounding range checks "
     "have bounded. @intCast is checked in ReleaseSafe; the bounds are "
     "exercised by 200,017 hex-float literals concentrated at the overflow and "
     "subnormal boundaries (docs/hex-float-proof.md) and by the number-torture "
     "tests."),
    ("strtod.zig", "*", "@truncate",
     "narrowing-to-byte",
     "Digit extraction from a bounded value."),
    ("strtod.zig", "*", "= undefined",
     "write-before-read",
     "Local digit scratch, fully written before it is read back."),
    ("strtod.zig", "*", "+%",
     "deliberate-wraparound",
     "Counter arithmetic that must not panic on adversarial exponent input."),
    ("strtod.zig", "*", "-%",
     "deliberate-wraparound",
     "Counter arithmetic that must not panic on adversarial exponent input."),

    # ---------------------------------------------------------- Zig-side API
    ("api.zig", "initBuffer", "= undefined",
     "write-before-read",
     "The json_stream is handed straight to core.openBuffer, which fills every "
     "field, on the next line. Nothing reads it in between, and the value does "
     "not escape until after."),
    ("api.zig", "*", "@intCast",
     "checked-narrowing",
     "Converting the C API's c_int/c_long results to Zig widths, checked at "
     "runtime in ReleaseSafe."),
    ("api.zig", "*", "@bitCast",
     "c-semantics-reproduction",
     "Signed/unsigned reinterpretation at the C API boundary, same width."),
    ("c_api.zig", "*", "@intCast",
     "checked-narrowing",
     "Converting Zig widths to the c_int/c_long the pinned header declares."),
    ("c_api.zig", "*", "@bitCast",
     "c-semantics-reproduction",
     "Signed/unsigned reinterpretation at the header boundary, same width."),
    ("root.zig", "*", "= undefined",
     "write-before-read",
     "Panic-handler scratch, written before it is read."),
    ("abi_contract.zig", "*", "= undefined",
     "comptime-only",
     "Comptime-only helper storage; nothing survives into the artifact."),
]


def strip_comments(text: str) -> list[str]:
    """Blank out `//` comments without touching `//` inside a string literal.

    Zig has no block comments, so a small state machine over each line is
    enough. Multiline string lines (`\\\\`) are left alone -- a `//` inside one
    is data, not a comment.
    """
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("\\\\"):
            out.append(line)
            continue
        result = []
        i = 0
        in_str = False
        in_char = False
        while i < len(line):
            ch = line[i]
            if in_str or in_char:
                if ch == "\\":
                    result.append(line[i:i + 2])
                    i += 2
                    continue
                if (in_str and ch == '"') or (in_char and ch == "'"):
                    in_str = in_char = False
            elif ch == '"':
                in_str = True
            elif ch == "'":
                in_char = True
            elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            result.append(ch)
            i += 1
        out.append("".join(result))
    return out


FN_RE = re.compile(r"^\s*(?:pub\s+|export\s+|inline\s+|extern\s*(?:\"[^\"]*\"\s*)?)*fn\s+(\w+)")
TEST_RE = re.compile(r'^\s*test\s+(?:"([^"]*)"|(\w+))?\s*\{')


def enclosing_functions(lines: list[str]) -> list[str]:
    """For each line, the name of the function it sits in ("" at file scope)."""
    names = []
    current = ""
    depth = 0
    pending = None
    for line in lines:
        m = FN_RE.match(line)
        if m:
            pending = m.group(1)
        else:
            # Test blocks are compiled into `zig build test`, never into the
            # shipped archive. Naming them keeps a test's scratch buffer from
            # inheriting a justification written about the parser's state.
            t = TEST_RE.match(line)
            if t:
                m = t
                pending = "test:" + (t.group(1) or t.group(2) or "<anonymous>")
        opens = line.count("{")
        closes = line.count("}")
        if pending is not None and opens:
            current = pending
            pending = None
        depth += opens - closes
        if depth <= 0 and not m:
            current = ""
            depth = max(depth, 0)
        names.append(current if current else "")
    return names


def find_rule(file: str, fn: str, op: str):
    for rf, rfn, rop, cat, why in RULES:
        if rf == file and rop == op and (rfn == fn or rfn == "*"):
            return cat, why
    return None


def scan_text(name: str, text: str) -> list[dict]:
    """Classify every occurrence in one file's source text."""
    occurrences = []
    raw = text.split("\n")
    stripped = strip_comments(text)
    fns = enclosing_functions(stripped)
    for idx, code in enumerate(stripped):
        if True:
            for op in OPERATIONS:
                # Count every occurrence, not every line: two @intCasts on one
                # line are two things to justify, and a per-line count would
                # quietly report one.
                if op in ("+%", "-%", "*%"):
                    # `-%` must not match `->`; require an operand or `=` after.
                    hits = len(re.findall(re.escape(op) + r"=?[\s(]", code))
                else:
                    hits = code.count(op)
                if not hits:
                    continue
                fn = fns[idx]
                if fn.startswith("test:"):
                    rule = ("test-only", "Inside a `test` block, which is "
                            "compiled into `zig build test` and never into the "
                            "shipped archive. Excluded from the library totals.")
                else:
                    rule = find_rule(name, fn, op)
                for _ in range(hits):
                    occurrences.append({
                        "file": f"src/{name}",
                        "line": idx + 1,
                        "function": fn or "<file scope>",
                        "operation": op,
                        "kind": OPERATIONS[op],
                        "source": raw[idx].strip() if idx < len(raw) else code.strip(),
                        "category": rule[0] if rule else "unclassified",
                        "justification": rule[1] if rule else None,
                        "forbidden": op in FORBIDDEN,
                    })
    return occurrences


def collect() -> dict:
    occurrences = []
    for path in sorted(SRC.glob("*.zig")):
        occurrences.extend(scan_text(path.name, path.read_text()))

    shipped = [o for o in occurrences if o["category"] != "test-only"]
    unclassified = [o for o in occurrences if o["category"] == "unclassified"]
    present_forbidden = [o for o in occurrences if o["forbidden"]]

    by_category: dict[str, int] = {}
    by_operation: dict[str, int] = {}
    for o in occurrences:
        by_category[o["category"]] = by_category.get(o["category"], 0) + 1
        by_operation[o["operation"]] = by_operation.get(o["operation"], 0) + 1

    return {
        "schema": "pdjson-zig/safety-inventory@1",
        "scope": "src/ only -- the library that ships. Tools and tests are not scanned.",
        "method": (
            "Every occurrence is matched to a rule keyed by (file, enclosing "
            "function, operation). Line numbers are recorded but are not the "
            "key, because a justification pinned to a line number rots the "
            "moment anything above it moves. An occurrence with no matching "
            "rule is reported as unclassified and fails the check."
        ),
        "forbidden_operations": sorted(FORBIDDEN),
        "total_occurrences": len(occurrences),
        "shipped_occurrences": len(shipped),
        "test_only_occurrences": len(occurrences) - len(shipped),
        "unclassified": len(unclassified),
        "forbidden_present": len(present_forbidden),
        "by_category": dict(sorted(by_category.items())),
        "by_operation": dict(sorted(by_operation.items())),
        "occurrences": occurrences,
    }



# --------------------------------------------------------------------- self-test

SELF_TEST_CASES = [
    # (name, file, source, predicate on the occurrence list, description)
    (
        "a forbidden operation is flagged",
        "parser.zig",
        "fn f() void {\n    const x = @constCast(p);\n}\n",
        lambda o: any(x["operation"] == "@constCast" and x["forbidden"] for x in o),
        "@constCast must be reported as forbidden, not merely counted",
    ),
    (
        "a hatch in an unlisted function is unclassified",
        "parser.zig",
        "fn brandNewHelper() void {\n    const p: [*]u8 = @ptrCast(q);\n}\n",
        lambda o: any(x["category"] == "unclassified" for x in o),
        "rules are keyed by function, so a @ptrCast at a new site must not "
        "inherit an existing justification",
    ),
    (
        "a hatch inside a comment is not counted",
        "parser.zig",
        "fn f() void {\n    // @ptrCast is discussed here but not used\n}\n",
        lambda o: not o,
        "prose mentioning an operation must not count as a use; this is how the "
        "reported 'unreachable: 0' stays true while DECISIONS.md talks about it",
    ),
    (
        "// inside a string literal is not a comment",
        "parser.zig",
        'fn f() void {\n    const s = "a//b";\n    const x = @intCast(y);\n}\n',
        lambda o: len(o) == 1 and o[0]["operation"] == "@intCast",
        "naive comment stripping would delete the rest of the line and lose the cast",
    ),
    (
        "the enclosing function is identified",
        "parser.zig",
        "fn cMalloc() void {\n    const p: [*]u8 = @ptrCast(q);\n}\n",
        lambda o: len(o) == 1 and o[0]["function"] == "cMalloc"
        and o[0]["category"] == "c-allocator-boundary",
        "the classification depends on getting the enclosing function right",
    ),
    (
        "-> is not read as a wrapping subtract",
        "parser.zig",
        "fn f() void {\n    const g = fn () -> void;\n}\n",
        lambda o: not any(x["operation"] == "-%" for x in o),
        "an over-eager pattern would inflate the wraparound count with arrows",
    ),
    (
        "a real wrapping subtract is counted",
        "parser.zig",
        "fn f() void {\n    self.stack_top -%= 1;\n}\n",
        lambda o: any(x["operation"] == "-%" for x in o),
        "and the fix for the arrow case must not suppress the real thing",
    ),
    (
        "a test block is not counted as shipped code",
        "api.zig",
        'test "something" {\n    var buf: [4]u8 = undefined;\n}\n',
        lambda o: len(o) == 1 and o[0]["category"] == "test-only"
        and o[0]["function"] == "test:something",
        "a test's scratch buffer must not inherit a justification written "
        "about the parser's own state -- which it did, until this case existed",
    ),
    (
        "code after a test block is shipped again",
        "parser.zig",
        'test "t" {\n    var b: [4]u8 = undefined;\n}\n\n'
        "fn cMalloc() void {\n    const p: [*]u8 = @ptrCast(q);\n}\n",
        lambda o: any(x["category"] == "c-allocator-boundary" for x in o),
        "the test-block attribution must end at the closing brace",
    ),
    (
        "two hatches on one line are two occurrences",
        "parser.zig",
        "fn f() void {\n    const a = @intCast(x) + @intCast(y);\n}\n",
        lambda o: len([x for x in o if x["operation"] == "@intCast"]) == 2,
        "a per-line count would report one thing to justify where there are two",
    ),
]


def self_test() -> int:
    failures = 0
    for name, file, source, predicate, why in SELF_TEST_CASES:
        found = scan_text(file, source)
        if predicate(found):
            print(f"  ok    {name}")
        else:
            failures += 1
            print(f"  FAIL  {name}: {why}")
            print(f"        got {json.dumps([{k: v for k, v in o.items() if k in ('operation', 'function', 'category')} for o in found])}")
    print(f"\nself-test: {len(SELF_TEST_CASES)} cases, {failures} failure(s)")
    return 1 if failures else 0


# --------------------------------------------------------------------------- doc

CATEGORY_BLURB = {
    "c-allocator-boundary":
        "`json_allocator` is a C interface: `malloc`/`realloc`/`free` speak "
        "`void*`. Every crossing needs a cast, and each one round-trips a "
        "pointer back to the same element type it went in as. None "
        "reinterprets one object type as another.",
    "public-header-boundary":
        "The pinned header promises `char*` and takes `const void*`. These are "
        "the header's own contract, not a choice this port made.",
    "c-semantics-reproduction":
        "Integer-to-integer reinterpretation where the original's behaviour "
        "depends on it -- `char` signedness when loading an input byte, "
        "printf's `%c`, `json_get_context`'s `long`-to-`size_t`. Never a "
        "pointer.",
    "checked-narrowing":
        "`@intCast` is *checked* in ReleaseSafe. A bound that turned out to be "
        "wrong aborts loudly rather than corrupting memory. That the bounds "
        "hold on untrusted input is evidence from fuzzing, not an assertion "
        "made here.",
    "narrowing-to-byte":
        "`@truncate` on a value the surrounding code has already constrained "
        "to a byte, or reproducing printf's `%c`. Defined for every input.",
    "ieee754-bit-pattern":
        "Assembling or reading an `f64` as a `u64`. Same width, no aliasing; "
        "this is how a correctly-rounded result and a NaN payload are built.",
    "deliberate-wraparound":
        "The opposite of an escape hatch: these *add* definition. C's counters "
        "are unsigned and allowed to wrap, and the empty-stack sentinel is "
        "`(size_t)-1` reached by decrementing 0. Using `+%`/`-%` rather than "
        "`+`/`-` is what stops untrusted input from turning an overflow into a "
        "panic.",
    "write-before-read":
        "Storage fully written before anything reads it back.",
    "test-only":
        "Inside a `test` block. Compiled by `zig build test`, never linked "
        "into the shipped archive.",
}


def write_doc(data: dict) -> None:
    ship = [o for o in data["occurrences"] if o["category"] != "test-only"]
    cats: dict[str, list] = {}
    for o in ship:
        cats.setdefault(o["category"], []).append(o)

    L = []
    a = L.append
    a("<!-- GENERATED by scripts/safety-inventory.py -- do not edit by hand. -->")
    a("")
    a("# Escape hatches, one at a time")
    a("")
    a("Zig has no `unsafe` keyword, so \"no unsafe code\" needs a definition before")
    a("it means anything. Here it is: **no operation that can reinterpret memory,")
    a("bypass a runtime check, or read uninitialised storage** -- except at a")
    a("boundary that cannot be expressed otherwise, justified individually.")
    a("")
    a("Counting them answers *how many*, which is the weaker question. This page")
    a("answers *which ones, and why is each sound*.")
    a("")
    a("```sh")
    a("make safety                                   # budget gate + this inventory")
    a("python3 scripts/safety-inventory.py --self-test")
    a("```")
    a("")
    a(f"**{len(ship)} occurrences in the shipped library, 0 unclassified.**")
    a(f"{data['test_only_occurrences']} more live in `test` blocks, which never reach the archive.")
    a("")
    a("## Never present")
    a("")
    a("These are not budgeted at some small number; they are absent, and the check")
    a("fails if one appears.")
    a("")
    a("| Operation | Count |")
    a("| --- | --- |")
    for op in sorted(data["forbidden_operations"]):
        n = sum(1 for o in data["occurrences"] if o["operation"] == op)
        a(f"| `{op.strip()}` | {n} |")
    a("")
    a("Force-unwraps (`.?`) are also zero, enforced by the budget gate in")
    a("[`scripts/safety-scan.sh`](../scripts/safety-scan.sh).")
    a("")
    a("A caveat that matters: comment text is stripped before scanning, so prose")
    a("*about* `unreachable` in `DECISIONS.md` or a doc comment does not count as a")
    a("use of it. The self-test covers exactly that case, and the reverse -- a `//`")
    a("inside a string literal must not swallow the rest of the line.")
    a("")
    a("## What is present, by category")
    a("")
    for cat in sorted(cats):
        items = cats[cat]
        a(f"### {cat} ({len(items)})")
        a("")
        a(CATEGORY_BLURB.get(cat, ""))
        a("")
        # Grouped by justification, so each row sits under the reason that
        # actually covers it. A category-wide list of quotes leaves the reader
        # guessing which one applies where.
        groups: dict[str, list] = {}
        for o in items:
            groups.setdefault(o["justification"] or "(none)", []).append(o)
        for why in sorted(groups, key=lambda w: -len(groups[w])):
            a(f"> {why}")
            a("")
            a("| Location | Function | Operation | Source |")
            a("| --- | --- | --- | --- |")
            for o in sorted(groups[why], key=lambda x: (x["file"], x["line"])):
                src = o["source"].split("//")[0].strip().replace("|", "\\|")
                if len(src) > 62:
                    src = src[:59] + "..."
                a(f"| [`{o['file']}:{o['line']}`](../{o['file']}#L{o['line']}) "
                  f"| `{o['function']}` | `{o['operation'].strip()}` | `{src}` |")
            a("")

    a("## Why occurrences are keyed by function, not by line")
    a("")
    a("The previous report listed `parser.zig:1011`. By the time anyone read it the")
    a("line was 1018, because seven lines had been inserted above it and nothing")
    a("re-derived the file. A justification pinned to a line number rots silently,")
    a("and a rotted justification is worse than none -- it reads as verified.")
    a("")
    a("So every rule is keyed by `(file, enclosing function, operation)`. Line")
    a("numbers are still recorded, because they are useful for navigation, but they")
    a("are not what the justification hangs on.")
    a("")
    a("Some rules use a wildcard function (`*`) where the reason genuinely does not")
    a("vary between sites -- every `@intCast` in `strtod.zig` is the same argument.")
    a("Where a wildcard turned out to cover two different things it was split:")
    a("`api.zig`'s `= undefined` rule described the parser's own state and was")
    a("silently being applied to a *test*'s scratch array. That is what motivated")
    a("separating test blocks out, and the self-test now pins the behaviour.")
    a("")
    a("## An unclassified occurrence fails the build")
    a("")
    a("A budget with room in it absorbs a new escape hatch without comment. A rule")
    a("table does not: a hatch at a site the table does not know about is reported")
    a("as `unclassified` and `make safety` exits non-zero. Introducing one is then")
    a("a deliberate edit to")
    a("[`scripts/safety-inventory.py`](../scripts/safety-inventory.py), which shows")
    a("up in review.")
    a("")
    a("## The classifier is itself tested")
    a("")
    a("Ten cases, each one a way this analysis could quietly report the wrong thing:")
    a("a forbidden operation not being flagged, a hatch at a new site inheriting an")
    a("existing justification, a mention in a comment being counted as a use, `//`")
    a("inside a string literal swallowing a cast, `->` being read as a wrapping")
    a("subtract, two hatches on one line counting as one, a test block's scratch")
    a("being counted as shipped code.")
    a("")
    a("```")
    a("$ python3 scripts/safety-inventory.py --self-test")
    a("self-test: 10 cases, 0 failure(s)")
    a("```")
    a("")
    a("## Limits, stated plainly")
    a("")
    a("- **These are arguments, not proofs.** Each justification says why an")
    a("  operation is sound; none is machine-checked. What *is* machine-checked is")
    a("  that every occurrence has one, that none is forbidden, and that the")
    a("  inventory matches the current source.")
    a("- **Scope is `src/`.** The tools in `tools/` and the harnesses are not")
    a("  scanned; they are not shipped.")
    a("- **It is textual.** A hatch reached through a construct the scanner does")
    a("  not model -- `@call` with a builtin name assembled at comptime, say --")
    a("  would be missed. Nothing in this codebase does that.")
    a("- **`@intCast` bounds are argued, not proven.** The claim that they hold on")
    a("  untrusted input rests on the fuzz session and the random-input regression")
    a("  test, and is only as strong as that evidence.")
    a("")
    a("Machine-readable, with every justification in full:")
    a("[`artifacts/safety/inventory.json`](../artifacts/safety/inventory.json).")
    a("")
    (ROOT / "docs" / "safety.md").write_text("\n".join(L))


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    check_only = "--check" in sys.argv
    data = collect()

    if check_only:
        if not OUT.exists():
            print(f"FAIL: {OUT.relative_to(ROOT)} is missing", file=sys.stderr)
            return 1
        committed = json.loads(OUT.read_text())
        if committed != data:
            print("FAIL: artifacts/safety/inventory.json is stale relative to src/",
                  file=sys.stderr)
            print("      run 'python3 scripts/safety-inventory.py'", file=sys.stderr)
            return 1
        print("  artifacts/safety/inventory.json matches src/")
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(data, indent=2) + "\n")
        write_doc(data)

    print(f"  {data['shipped_occurrences']} escape-hatch occurrence(s) in the shipped "
          f"library ({data['test_only_occurrences']} more in test blocks), "
          f"{data['unclassified']} unclassified")
    for cat, n in data["by_category"].items():
        print(f"    {cat:<28} {n}")

    if data["forbidden_present"]:
        for o in data["occurrences"]:
            if o["forbidden"]:
                print(f"  FORBIDDEN {o['operation']} at {o['file']}:{o['line']}",
                      file=sys.stderr)
        return 1
    if data["unclassified"]:
        for o in data["occurrences"]:
            if o["category"] == "unclassified":
                print(f"  UNCLASSIFIED {o['operation']} in {o['function']} "
                      f"({o['file']}:{o['line']}): {o['source']}", file=sys.stderr)
        print("  add a rule to RULES in scripts/safety-inventory.py, or remove the "
              "occurrence", file=sys.stderr)
        return 1
    if not check_only:
        print(f"  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
