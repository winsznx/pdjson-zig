#!/usr/bin/env python3
"""Specify the parser's state machine, then measure which transitions the
evidence actually exercises.

"0 divergences across 5,805 comparisons" is a count of *inputs*. It says nothing
about which parts of the state machine those inputs drive. A corpus can be large
and still never reach, say, a boolean immediately after a key inside an object.

So the transition relation is written out here, from the JSON grammar and the
pdjson API rather than from the implementation, and the corpus is measured
against it. Three things fall out, and all three are findings:

  * a **specified transition never observed** is a coverage gap
  * an **observed transition not in the specification** is either a parser bug
    or a wrong specification, and has to be resolved either way
  * a transition observed for one implementation and not the other would mean
    the transcripts are not byte-identical after all

The state is derived from what a caller can observe -- `json_get_context`'s type
and count -- not from any internal variable. Inside an object the count
disambiguates a key from a value: `json_next` increments it per event, so an odd
count means a key was just returned and an even one means a value was.

  python3 scripts/state-machine.py             # measure the corpus
  python3 scripts/state-machine.py --self-test # check the state derivation
"""
from __future__ import annotations

import collections
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
C_BIN = ROOT / "build" / "transcript_c"
ZIG_BIN = ROOT / "zig-out" / "bin" / "transcript_zig"
OUT = ROOT / "artifacts" / "state-machine" / "coverage.json"

VALUES = ["OBJECT", "ARRAY", "STRING", "NUMBER", "TRUE", "FALSE", "NULL"]

# ---------------------------------------------------------------------------
# The specification.
#
# Written from RFC 8259's grammar and pdjson.h's contract. Not derived from
# either implementation -- that would make it agree by construction.
# ---------------------------------------------------------------------------
SPEC: dict[str, list[str]] = {
    # Nothing parsed yet: any value, an empty document, or a bad first byte.
    "TOP.START": VALUES + ["DONE", "ERROR"],
    # A complete top-level value has been returned. Next comes end of text, or
    # an error if there is trailing garbage.
    "TOP.AFTER_VALUE": ["DONE", "ERROR"],
    # DONE is idempotent: calling json_next again without a reset stays there.
    "TOP.DONE": ["DONE"],
    # After json_reset, streaming mode begins the next document.
    "TOP.DONE_RESET": VALUES + ["DONE", "ERROR"],
    # The error flag latches; nothing clears it but a reset.
    "ERROR_LATCHED": ["ERROR"],
    # '{' has been returned and no member yet: a key, or an empty object.
    "OBJECT.EMPTY": ["STRING", "OBJECT_END", "ERROR"],
    # A key has been returned; its value must follow.
    "OBJECT.AFTER_KEY": VALUES + ["ERROR"],
    # A member is complete: another key, or the object closes.
    "OBJECT.AFTER_VALUE": ["STRING", "OBJECT_END", "ERROR"],
    # '[' has been returned and no element yet.
    "ARRAY.EMPTY": VALUES + ["ARRAY_END", "ERROR"],
    # An element is complete: another element, or the array closes.
    "ARRAY.AFTER_VALUE": VALUES + ["ARRAY_END", "ERROR"],
}

def state_of(prev: dict | None, after_reset: bool) -> str:
    """The state a caller is in, derived only from observable values."""
    if prev is None:
        return "TOP.START"
    if prev["event"] == "ERROR":
        return "ERROR_LATCHED"
    ctx, n = prev["ctx"], prev["ctxn"]
    if ctx == "OBJECT":
        if n == 0:
            return "OBJECT.EMPTY"
        return "OBJECT.AFTER_KEY" if n % 2 == 1 else "OBJECT.AFTER_VALUE"
    if ctx == "ARRAY":
        return "ARRAY.EMPTY" if n == 0 else "ARRAY.AFTER_VALUE"
    # Top level.
    if prev["event"] == "DONE":
        return "TOP.DONE_RESET" if after_reset else "TOP.DONE"
    return "TOP.AFTER_VALUE"


def walk(text: str, observed: collections.Counter, examples: dict) -> tuple[int, int]:
    """Accumulate (state, event) transitions from a batch of transcripts."""
    prev = None
    after_reset = False
    current_input = "?"
    transcripts = records = 0
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        r = json.loads(line)
        if "input" in r:
            current_input = r["input"]
            continue
        if "schema" in r:
            prev, after_reset = None, False
            transcripts += 1
            continue
        if "end" in r:
            continue
        records += 1
        op = r.get("op")
        if op == "reset":
            after_reset = True
            continue
        # peek does not advance the parser, so it cannot form a transition;
        # skip consumes a whole value, so the event it lands on is not the
        # single-step successor the specification is about.
        if op != "next":
            prev, after_reset = r, False
            continue
        key = (state_of(prev, after_reset), r["event"])
        observed[key] += 1
        examples.setdefault(key, current_input)
        prev, after_reset = r, False
    return transcripts, records


def run(binary: pathlib.Path, mode: str, listfile: pathlib.Path) -> str:
    p = subprocess.run([str(binary), "--batch", mode, str(listfile)],
                       capture_output=True, timeout=1800)
    if p.returncode != 0:
        raise SystemExit(f"{binary.name} failed in mode {mode}: "
                         f"{p.stderr.decode()[:400]}")
    return p.stdout.decode(errors="replace")


SELF_TEST = [
    (None, False, "TOP.START"),
    ({"event": "ERROR", "ctx": "DONE", "ctxn": 0}, False, "ERROR_LATCHED"),
    ({"event": "ERROR", "ctx": "ARRAY", "ctxn": 3}, False, "ERROR_LATCHED"),
    ({"event": "OBJECT", "ctx": "OBJECT", "ctxn": 0}, False, "OBJECT.EMPTY"),
    ({"event": "STRING", "ctx": "OBJECT", "ctxn": 1}, False, "OBJECT.AFTER_KEY"),
    ({"event": "NUMBER", "ctx": "OBJECT", "ctxn": 2}, False, "OBJECT.AFTER_VALUE"),
    ({"event": "STRING", "ctx": "OBJECT", "ctxn": 3}, False, "OBJECT.AFTER_KEY"),
    ({"event": "ARRAY_END", "ctx": "OBJECT", "ctxn": 4}, False, "OBJECT.AFTER_VALUE"),
    ({"event": "ARRAY", "ctx": "ARRAY", "ctxn": 0}, False, "ARRAY.EMPTY"),
    ({"event": "NUMBER", "ctx": "ARRAY", "ctxn": 1}, False, "ARRAY.AFTER_VALUE"),
    ({"event": "NUMBER", "ctx": "DONE", "ctxn": 0}, False, "TOP.AFTER_VALUE"),
    ({"event": "OBJECT_END", "ctx": "DONE", "ctxn": 0}, False, "TOP.AFTER_VALUE"),
    ({"event": "DONE", "ctx": "DONE", "ctxn": 0}, False, "TOP.DONE"),
    ({"event": "DONE", "ctx": "DONE", "ctxn": 0}, True, "TOP.DONE_RESET"),
]


def self_test() -> int:
    bad = 0
    for prev, reset, want in SELF_TEST:
        got = state_of(prev, reset)
        if got == want:
            print(f"  ok    {want}")
        else:
            bad += 1
            print(f"  FAIL  expected {want}, got {got} for prev={prev} reset={reset}")
    # The specification must not name a state the derivation cannot produce, or
    # its transitions would be permanently uncoverable for a silly reason.
    producible = {want for _, _, want in SELF_TEST}
    for state in SPEC:
        if state not in producible:
            bad += 1
            print(f"  FAIL  {state} is specified but no self-test case produces it")
    print(f"\nstate-derivation self-test: {len(SELF_TEST)} cases, {bad} failure(s)")
    return 1 if bad else 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    for b in (C_BIN, ZIG_BIN):
        if not b.exists():
            print(f"missing {b}; run 'make build' first", file=sys.stderr)
            return 2

    corpora = {
        "fixtures": sorted((ROOT / "tests" / "conformance" / "fixtures").glob("*.json")),
    }
    # The fixture corpus alone has to reach full coverage. JSONTestSuite is
    # fetched on demand and the minimized fuzz findings accumulate over time, so
    # leaning on either would mean `make verify` passing here and failing in a
    # clean checkout -- which is exactly what it did.
    fixtures_only = dict(corpora)
    jts = ROOT / "tests" / "conformance" / "JSONTestSuite" / "test_parsing"
    if jts.is_dir():
        corpora["jsontestsuite"] = sorted(jts.glob("*.json"))
    mini = ROOT / "fuzz" / "minimized"
    if mini.is_dir() and any(mini.iterdir()):
        corpora["fuzz-minimized"] = sorted(p for p in mini.glob("*") if p.is_file())

    modes = ["next", "nostream", "stream:next", "user:next", "after-end"]

    work = ROOT / "fuzz" / "work"
    work.mkdir(parents=True, exist_ok=True)
    listfile = work / "state-machine-list.txt"

    per_impl = {"c": collections.Counter(), "zig": collections.Counter()}
    fixtures_observed: collections.Counter = collections.Counter()
    examples: dict = {}
    transcripts = records = 0

    for corpus, files in corpora.items():
        if not files:
            continue
        listfile.write_text("\n".join(str(f) for f in files) + "\n")
        for mode in modes:
            for impl, binary in (("c", C_BIN), ("zig", ZIG_BIN)):
                out = run(binary, mode, listfile)
                t, n = walk(out, per_impl[impl], examples)
                if impl == "zig":
                    transcripts += t
                    records += n
                    if corpus in fixtures_only:
                        walk(out, fixtures_observed, {})

    specified = {(s, e) for s, evs in SPEC.items() for e in evs}
    # Coverage that depends on a corpus a clean checkout does not have is not
    # coverage. This was 48/54 on fixtures alone while reporting 54/54 here,
    # and `make verify` failed in a fresh clone because of it.
    uncovered_fixtures_only = sorted(specified - set(fixtures_observed))
    observed_zig = set(per_impl["zig"])
    observed_c = set(per_impl["c"])

    covered = specified & observed_zig
    uncovered = sorted(specified - observed_zig)
    unspecified = sorted(observed_zig - specified)
    only_c = sorted(observed_c - observed_zig)
    only_zig = sorted(observed_zig - observed_c)

    by_state = {}
    for state, events in SPEC.items():
        hit = [e for e in events if (state, e) in observed_zig]
        by_state[state] = {
            "specified": len(events),
            "covered": len(hit),
            "uncovered": sorted(set(events) - set(hit)),
        }

    summary = {
        "schema": "pdjson-zig/state-machine-coverage@1",
        "method": (
            "The transition relation is written from RFC 8259's grammar and "
            "pdjson.h's contract, not derived from either implementation. The "
            "state a caller is in is computed only from json_get_context's type "
            "and count, both of which are observable through the public API; "
            "inside an object an odd count means a key was just returned and an "
            "even one means a value was."
        ),
        "scope": (
            "Single-step transitions under json_next only. peek does not "
            "advance the parser and skip consumes a whole value, so neither "
            "forms a single-step successor; both are excluded and their "
            "coverage is reported by scripts/api-coverage.py instead."
        ),
        "corpora": {k: len(v) for k, v in corpora.items()},
        "modes": modes,
        "transcripts": transcripts,
        "records": records,
        "states": len(SPEC),
        "transitions_specified": len(specified),
        "transitions_covered": len(covered),
        "transitions_uncovered": len(uncovered),
        "transitions_unspecified_but_observed": len(unspecified),
        "coverage_percent": round(100 * len(covered) / len(specified), 1),
        "implementations_differ": len(only_c) + len(only_zig),
        "transitions_uncovered_by_fixtures_alone": len(uncovered_fixtures_only),
        "fixtures_only_note": (
            "The committed fixture corpus must reach every specified transition "
            "on its own. JSONTestSuite is fetched on demand and the minimized "
            "fuzz findings accumulate over time, so leaning on either would mean "
            "this passing here and failing in a clean checkout."),
        "uncovered_by_fixtures_alone": [
            {"state": s, "event": e} for s, e in uncovered_fixtures_only],
        "uncovered": [{"state": s, "event": e} for s, e in uncovered],
        "unspecified_but_observed": [
            {"state": s, "event": e, "count": per_impl["zig"][(s, e)],
             "first_seen_in": examples.get((s, e))}
            for s, e in unspecified
        ],
        "observed_only_in_c": [{"state": s, "event": e} for s, e in only_c],
        "observed_only_in_zig": [{"state": s, "event": e} for s, e in only_zig],
        "by_state": by_state,
        "transitions": sorted(
            [{"state": s, "event": e, "count": per_impl["zig"][(s, e)],
              "in_spec": (s, e) in specified,
              "first_seen_in": examples.get((s, e))}
             for s, e in observed_zig],
            key=lambda d: (d["state"], d["event"]),
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"  {len(SPEC)} states, {len(specified)} specified transitions")
    print(f"  {transcripts} transcripts, {records} records, "
          f"{len(modes)} modes, {sum(len(v) for v in corpora.values())} inputs")
    print(f"  covered {len(covered)}/{len(specified)} "
          f"({summary['coverage_percent']}%)")
    print(f"  covered by the committed fixtures alone: "
          f"{len(specified) - len(uncovered_fixtures_only)}/{len(specified)}")
    for st, ev in uncovered_fixtures_only:
        print(f"    NOT REACHED WITHOUT A FETCHED CORPUS: {st} -> {ev}")
    if unspecified:
        print(f"  OBSERVED BUT NOT SPECIFIED: {len(unspecified)}")
        for s, e in unspecified:
            print(f"    {s} -> {e}  ({per_impl['zig'][(s, e)]}x, first in "
                  f"{examples.get((s, e))})")
    if only_c or only_zig:
        print(f"  IMPLEMENTATIONS DIFFER on {len(only_c) + len(only_zig)} transition(s)")
        for s, e in only_c + only_zig:
            print(f"    {s} -> {e}")
    if uncovered:
        print(f"  uncovered ({len(uncovered)}):")
        for s, e in uncovered:
            print(f"    {s} -> {e}")
    print(f"  wrote {OUT.relative_to(ROOT)}")

    # An observed transition the specification does not contain, or the two
    # implementations covering different sets, are both hard failures. An
    # uncovered transition is reported, and the gate on it lives in CLAIMS.json
    # so the number is visible rather than buried in an exit code.
    return 1 if (unspecified or only_c or only_zig
                 or uncovered_fixtures_only) else 0


if __name__ == "__main__":
    sys.exit(main())
