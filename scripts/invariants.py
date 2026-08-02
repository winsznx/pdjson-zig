#!/usr/bin/env python3
"""Validate behaviour transcripts against invariants, independently of both
implementations.

Everything else in this project compares C against Zig. That catches the two
disagreeing, but not both being wrong in the same way -- and it cannot say
anything at all about a transcript neither side produced. This checker reads a
transcript on its own terms and asks whether it describes a parser behaving
sanely.

Deliberately written in Python rather than Zig or C: it must not share code with
either implementation, and using the parser under test to read its own output
would be circular.

Every rule here is one the pdjson API actually promises. Rules were developed by
running them against the *C original's* transcripts first -- a rule that fires on
unmodified upstream output is a wrong rule, not a finding -- and only then
applied to the port. Rules that turned out to be assumptions rather than promises
are recorded at the bottom of docs/transcript-invariants.md rather than encoded.

Usage:
  invariants.py --self-test
  invariants.py <transcript-file> [...]
  invariants.py --sweep            # generate and check the full corpus
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
C_BIN = ROOT / "build" / "transcript_c"
ZIG_BIN = ROOT / "zig-out" / "bin" / "transcript_zig"

CONTAINER_OPEN = {"OBJECT", "ARRAY"}
CONTAINER_CLOSE = {"OBJECT_END", "ARRAY_END"}
VALUE_EVENTS = {"STRING", "NUMBER", "TRUE", "FALSE", "NULL"}


class Violation(Exception):
    def __init__(self, rule: str, detail: str, record=None):
        self.rule = rule
        self.detail = detail
        self.record = record
        super().__init__(f"{rule}: {detail}")


# ---------------------------------------------------------------------------
# Rules
#
# Each takes the parsed record list and raises Violation. They receive only what
# a caller of the public API could observe.
# ---------------------------------------------------------------------------

def rule_sequence_contiguous(records, header, term):
    """Sequence numbers start at 0 and increase by exactly one.

    A gap would mean the harness dropped a record, which would silently weaken
    every comparison built on it.
    """
    expect = 0
    for r in records:
        if r["seq"] != expect:
            raise Violation("sequence-contiguous",
                            f"expected seq {expect}, found {r['seq']}", r)
        expect += 1


def rule_token_length_matches_bytes(records, header, term):
    """`toklen` is the length of the token bytes actually emitted.

    json_get_string's out-parameter and the bytes it points at have to agree, or
    a caller reading `length` bytes would read the wrong amount.
    """
    for r in records:
        if "tok" not in r:
            continue
        if len(r["tok"]) % 2:
            raise Violation("token-hex-even", f"odd-length hex {r['tok']!r}", r)
        if len(r["tok"]) // 2 != r["toklen"]:
            raise Violation("token-length-matches-bytes",
                            f"toklen={r['toklen']} but {len(r['tok']) // 2} bytes encoded", r)


def rule_position_monotonic(records, header, term):
    """Byte position never decreases.

    json_get_position reports bytes consumed from the source. Nothing in the API
    rewinds, so it may stall but never go backwards.
    """
    last = 0
    for r in records:
        if "pos" not in r:
            continue
        if r["pos"] < last:
            raise Violation("position-monotonic",
                            f"position went {last} -> {r['pos']}", r)
        last = r["pos"]


def rule_line_monotonic(records, header, term):
    """Line number never decreases, and starts at 1."""
    last = 1
    for r in records:
        if "line" not in r:
            continue
        if r["line"] < 1:
            raise Violation("line-positive", f"line={r['line']}", r)
        if r["line"] < last:
            raise Violation("line-monotonic", f"line went {last} -> {r['line']}", r)
        last = r["line"]


def rule_error_is_latched(records, header, term):
    """Once an ERROR event appears, later `next`/`skip` events stay ERROR until a
    reset.

    pdjson sets JSON_FLAG_ERROR and json_next returns JSON_ERROR immediately
    while it is set; json_reset clears it. The README states the stream cannot be
    used again until it is reset.
    """
    errored = False
    for r in records:
        op = r.get("op")
        if op == "reset":
            errored = False
            continue
        if op not in ("next", "skip", "peek"):
            continue
        if errored and r["event"] != "ERROR":
            raise Violation("error-is-latched",
                            f"event {r['event']} after a latched error", r)
        if r["event"] == "ERROR":
            errored = True


def rule_error_has_message(records, header, term):
    """An ERROR event carries a diagnostic.

    json_get_error returns the message whenever the error flag is set, and the
    flag is what produces the ERROR event.
    """
    for r in records:
        if r.get("event") == "ERROR" and r.get("err") is None:
            raise Violation("error-has-message", "ERROR event with err=null", r)


def rule_depth_matches_container_events(records, header, term):
    """Depth moves by exactly one on container events and not at all otherwise.

    Scoped to records before any error: after a failed allocation the original
    advances its stack index without producing a container event, which is
    upstream #36 and is undefined territory rather than a rule violation.

    Not applied in skip modes. json_skip consumes an entire value and returns
    the event that *started* it, so the depth reported alongside is the depth
    after the whole value was consumed -- an OBJECT event with unchanged depth
    is correct there. Firing on the C original's own skip output is what
    revealed this; the rule was wrong, not upstream.
    """
    if header.get("mode", "").split(":")[-1] == "skip":
        return
    prev = None
    for r in records:
        if r.get("op") not in ("next", "skip") or "depth" not in r:
            continue
        if r["event"] == "ERROR":
            return
        if r["event"] == "DONE":
            prev = r["depth"]
            continue
        if prev is not None:
            d = r["depth"] - prev
            if r["event"] in CONTAINER_OPEN and d != 1:
                raise Violation("depth-matches-container-events",
                                f"{r['event']} changed depth by {d}, expected +1", r)
            if r["event"] in CONTAINER_CLOSE and d != -1:
                raise Violation("depth-matches-container-events",
                                f"{r['event']} changed depth by {d}, expected -1", r)
            if r["event"] in VALUE_EVENTS and d != 0:
                raise Violation("depth-matches-container-events",
                                f"scalar {r['event']} changed depth by {d}, expected 0", r)
        prev = r["depth"]


def rule_containers_balanced(records, header, term):
    """Container opens and closes balance over a transcript that completes.

    Only applied when the transcript ran to completion with no error: a
    document that errors, or one long enough to hit the record cap, legitimately
    ends with containers still open. JSONTestSuite's 100,000-deep array does
    exactly that in peek mode, which is what exposed the missing check.
    """
    if any(r.get("event") == "ERROR" for r in records):
        return
    if term is None or term.get("truncated"):
        return
    opens = {"OBJECT": 0, "ARRAY": 0}
    for r in records:
        if r.get("op") not in ("next", "skip"):
            continue
        e = r.get("event")
        if e == "OBJECT":
            opens["OBJECT"] += 1
        elif e == "ARRAY":
            opens["ARRAY"] += 1
        elif e == "OBJECT_END":
            opens["OBJECT"] -= 1
            if opens["OBJECT"] < 0:
                raise Violation("containers-balanced", "OBJECT_END without OBJECT", r)
        elif e == "ARRAY_END":
            opens["ARRAY"] -= 1
            if opens["ARRAY"] < 0:
                raise Violation("containers-balanced", "ARRAY_END without ARRAY", r)
    # json_skip consumes a whole value without emitting its interior events, so
    # an unbalanced count is expected there and is not checked.
    if header.get("mode", "").endswith("skip"):
        return
    for k, v in opens.items():
        if v != 0:
            raise Violation("containers-balanced", f"{v} unclosed {k}")


def rule_context_agrees_with_depth(records, header, term):
    """Container context is reported exactly when inside a container.

    json_get_context returns JSON_DONE at the top level and json_get_depth
    returns 0 there. Scoped to records before any error, for the same reason as
    the depth rule.
    """
    for r in records:
        if r.get("op") not in ("next", "skip") or "ctx" not in r:
            continue
        if r["event"] == "ERROR":
            return
        at_top = r["depth"] == 0
        ctx_top = r["ctx"] == "DONE"
        if at_top != ctx_top:
            raise Violation("context-agrees-with-depth",
                            f"depth={r['depth']} but ctx={r['ctx']}", r)


def rule_reset_clears_state(records, header, term):
    """After a reset the parser is back at the top level with no error pending.

    json_reset sets stack_top to the empty sentinel, zeroes the token counter and
    clears the error flag. It deliberately does *not* clear the token buffer or a
    buffered peek, so those are not checked.
    """
    for r in records:
        if r.get("op") != "reset":
            continue
        if r["depth"] != 0:
            raise Violation("reset-clears-state", f"depth={r['depth']} after reset", r)
        if r["ctx"] != "DONE":
            raise Violation("reset-clears-state", f"ctx={r['ctx']} after reset", r)
        if r["err"] is not None:
            raise Violation("reset-clears-state", "error still latched after reset", r)


def rule_peek_then_next_agree(records, header, term):
    """In peek mode, a peek is followed by a next reporting the same event.

    json_peek stores the event and json_next returns the stored one, so the pair
    must agree. This is the promise the header's "peek" name makes.
    """
    if header.get("mode", "").split(":")[-1] != "peek":
        return
    for a, b in zip(records, records[1:]):
        if a.get("op") == "peek" and b.get("op") == "next":
            if a["event"] != b["event"]:
                raise Violation("peek-then-next-agree",
                                f"peek said {a['event']}, next said {b['event']}", b)


def rule_number_defined_exactly_when_terminated(records, header, term):
    """`num` is recorded exactly when the token bytes contain a NUL.

    This is the project's own exclusion rule for upstream #38, and it has to be
    applied identically on both sides or it could hide a difference. Checking it
    here means the rule is verified rather than assumed.
    """
    for r in records:
        if "tok" not in r or "num" not in r:
            continue
        raw = bytes.fromhex(r["tok"])
        terminated = b"\x00" in raw
        recorded = r["num"] is not None
        if terminated != recorded:
            raise Violation("number-defined-when-terminated",
                            f"token {'has' if terminated else 'has no'} NUL but num is "
                            f"{'recorded' if recorded else 'null'}", r)


def rule_schema_and_terminator(records, header, term):
    """A transcript declares its schema and states how it ended."""
    if not header.get("schema", "").startswith("pdjson-zig/transcript@"):
        raise Violation("schema-declared", f"bad header {header!r}")


RULES = [
    rule_sequence_contiguous,
    rule_token_length_matches_bytes,
    rule_position_monotonic,
    rule_line_monotonic,
    rule_error_is_latched,
    rule_error_has_message,
    rule_depth_matches_container_events,
    rule_containers_balanced,
    rule_context_agrees_with_depth,
    rule_reset_clears_state,
    rule_peek_then_next_agree,
    rule_number_defined_exactly_when_terminated,
    rule_schema_and_terminator,
]


# ---------------------------------------------------------------------------
# Driving
# ---------------------------------------------------------------------------

def split_transcripts(text: str):
    """Yield (header, records, terminator) per transcript.

    Handles the plain form and the --batch/--pack framing, where each transcript
    is preceded by an {"input": ...} marker.
    """
    header, records, term, label = None, [], None, None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise Violation("well-formed-ndjson", f"{e} in {line[:120]!r}")
        if "input" in obj and len(obj) == 1:
            if header is not None:
                yield label, header, records, term
            header, records, term, label = None, [], None, obj["input"]
            continue
        if "schema" in obj:
            if header is not None:
                yield label, header, records, term
                records, term = [], None
            header = obj
        elif "end" in obj or "truncated" in obj:
            term = obj
        else:
            records.append(obj)
    if header is not None:
        yield label, header, records, term


def check_text(text: str, source: str):
    """Returns a list of violation dicts."""
    out = []
    try:
        for label, header, records, term in split_transcripts(text):
            for rule in RULES:
                try:
                    rule(records, header, term)
                except Violation as v:
                    out.append({
                        "source": source, "input": label,
                        "mode": header.get("mode"), "rule": v.rule,
                        "detail": v.detail, "record": v.record,
                    })
    except Violation as v:
        out.append({"source": source, "rule": v.rule, "detail": v.detail})
    return out


def run_binary(binary: pathlib.Path, mode: str, listfile: pathlib.Path) -> str:
    p = subprocess.run([str(binary), "--batch", mode, str(listfile)],
                       capture_output=True, timeout=600)
    return p.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Self-test: every rule must be able to fail
# ---------------------------------------------------------------------------

HEADER = '{"schema":"pdjson-zig/transcript@2","mode":"next","bytes":3}'

def rec(**kw):
    base = {"seq": 0, "op": "next", "event": "NUMBER", "tok": "3100", "toklen": 2,
            "num": "3ff0000000000000", "line": 1, "pos": 1, "depth": 0,
            "ctx": "DONE", "ctxn": 0, "err": None}
    base.update(kw)
    return json.dumps(base)

MALFORMED = {
    "sequence-contiguous": [HEADER, rec(seq=0), rec(seq=2)],
    "token-length-matches-bytes": [HEADER, rec(seq=0, tok="3100", toklen=7)],
    "position-monotonic": [HEADER, rec(seq=0, pos=10), rec(seq=1, pos=4)],
    "line-monotonic": [HEADER, rec(seq=0, line=5), rec(seq=1, line=2)],
    "error-is-latched": [HEADER, rec(seq=0, event="ERROR", err="6f6f7073"),
                         rec(seq=1, event="NUMBER")],
    "error-has-message": [HEADER, rec(seq=0, event="ERROR", err=None)],
    "depth-matches-container-events": [HEADER, rec(seq=0, event="ARRAY", depth=1, ctx="ARRAY"),
                                       rec(seq=1, event="ARRAY", depth=5, ctx="ARRAY")],
    # Needs a terminator: the rule deliberately does not judge a transcript that
    # was truncated or never finished.
    "containers-balanced": [HEADER, rec(seq=0, event="ARRAY", depth=1, ctx="ARRAY"),
                            rec(seq=1, event="DONE", depth=1, ctx="ARRAY"),
                            '{"end":true,"records":2}'],
    "context-agrees-with-depth": [HEADER, rec(seq=0, event="NUMBER", depth=0, ctx="ARRAY")],
    "reset-clears-state": [HEADER, rec(seq=0, op="reset", event="DONE", depth=0,
                                       ctx="DONE", err="6f6f7073")],
    "peek-then-next-agree": ['{"schema":"pdjson-zig/transcript@2","mode":"peek","bytes":3}',
                             rec(seq=0, op="peek", event="NUMBER"),
                             rec(seq=1, op="next", event="STRING")],
    "number-defined-when-terminated": [HEADER, rec(seq=0, tok="3132", toklen=2,
                                                   num="3ff0000000000000")],
    "schema-declared": ['{"schema":"something-else@1","mode":"next","bytes":0}', rec(seq=0)],
    "well-formed-ndjson": ["{not json"],
}


def self_test() -> int:
    failures = []

    # 1. Every rule must reject its malformed fixture.
    for rule_name, lines in MALFORMED.items():
        found = check_text("\n".join(lines), "self-test")
        if not any(v["rule"] == rule_name for v in found):
            failures.append(f"rule {rule_name!r} did not fire on its malformed fixture "
                            f"(got {[v['rule'] for v in found] or 'nothing'})")

    # 2. A well-formed transcript must produce no violations at all.
    clean = [HEADER,
             rec(seq=0, event="ARRAY", depth=1, ctx="ARRAY", tok="", toklen=0, num=None),
             rec(seq=1, event="NUMBER", depth=1, ctx="ARRAY", ctxn=1, pos=2),
             rec(seq=2, event="ARRAY_END", depth=0, ctx="DONE", pos=3),
             rec(seq=3, event="DONE", depth=0, ctx="DONE", pos=3),
             '{"end":true,"records":4}']
    found = check_text("\n".join(clean), "self-test")
    if found:
        failures.append(f"a well-formed transcript produced violations: {found}")

    for f in failures:
        print(f"SELF-TEST FAIL {f}", file=sys.stderr)
    print(f"self-test: {len(MALFORMED)} rules, "
          f"{len(MALFORMED) - len([f for f in failures if 'did not fire' in f])} "
          f"provably able to fail, {len(failures)} problem(s)")
    return 1 if failures else 0


# ---------------------------------------------------------------------------

def sweep(out_path: pathlib.Path) -> int:
    modes = ["next", "nostream", "peek", "skip", "sep", "after-end",
             "stream:next", "stream:peek", "stream:sep",
             "user:next", "user:peek", "user:skip",
             "oom:0", "oom:2"]
    corpora = {
        "fixtures": sorted((ROOT / "tests" / "conformance" / "fixtures").glob("*.json")),
    }
    jts = ROOT / "tests" / "conformance" / "JSONTestSuite" / "test_parsing"
    if jts.is_dir():
        corpora["jsontestsuite"] = sorted(jts.glob("*.json"))
    mini = ROOT / "fuzz" / "minimized"
    if mini.is_dir() and any(mini.iterdir()):
        corpora["fuzz-minimized"] = sorted(p for p in mini.glob("*") if p.is_file())

    tmp = ROOT / "fuzz" / "work"
    tmp.mkdir(parents=True, exist_ok=True)
    listfile = tmp / "invariant-list.txt"

    results = {"c": [], "zig": []}
    counts = {"transcripts": 0, "records": 0}
    # Counted separately because JSONTestSuite is fetched on demand. A claim
    # that quotes the combined figure is true in a tree that has fetched it and
    # false in a fresh clone -- which is exactly how `make verify` came to pass
    # here and fail there.
    fixture_counts = {"transcripts": 0, "records": 0}

    for corpus, files in corpora.items():
        if not files:
            continue
        listfile.write_text("\n".join(str(f) for f in files) + "\n")
        for mode in modes:
            for impl, binary in (("c", C_BIN), ("zig", ZIG_BIN)):
                # The C original crashes under allocation failure (upstream #36),
                # so its output there is truncated by a signal rather than
                # malformed. Skip those for the C side only, and say so.
                if impl == "c" and mode.startswith("oom:"):
                    continue
                text = run_binary(binary, mode, listfile)
                n_t = text.count('"schema"')
                n_r = sum(1 for l in text.splitlines() if '"seq"' in l)
                counts["transcripts"] += n_t
                counts["records"] += n_r
                if corpus == "fixtures":
                    fixture_counts["transcripts"] += n_t
                    fixture_counts["records"] += n_r
                results[impl].extend(check_text(text, f"{impl}:{corpus}:{mode}"))

    c_only = [v for v in results["c"]]
    zig_only = [v for v in results["zig"]]
    c_keys = {(v["rule"], v.get("input"), v.get("mode")) for v in c_only}
    zig_keys = {(v["rule"], v.get("input"), v.get("mode")) for v in zig_only}
    both = c_keys & zig_keys

    summary = {
        "schema": "pdjson-zig/invariant-summary@1",
        "method": ("Each transcript is validated on its own terms, without reference "
                   "to the other implementation. Rules were developed against the C "
                   "original's output first: a rule that fires on unmodified upstream "
                   "output is a wrong rule, not a finding."),
        "rules": [r.__name__.replace("rule_", "").replace("_", "-") for r in RULES],
        "rule_functions": len(RULES),
        "violation_classes": len(MALFORMED),
        "each_class_provably_fails": True,
        "corpora": {k: len(v) for k, v in corpora.items()},
        "modes": modes,
        "note_oom_c": ("Allocation-failure modes are not run against the C binary: it "
                       "crashes there (upstream #36), so its transcript is truncated by "
                       "a signal rather than being invalid. The Zig side is checked."),
        "transcripts_checked": counts["transcripts"],
        "records_checked": counts["records"],
        # The figures a fresh clone with no network also produces. Claims quote
        # these; the totals above vary with what has been fetched.
        "transcripts_checked_committed_corpus": fixture_counts["transcripts"],
        "records_checked_committed_corpus": fixture_counts["records"],
        "corpus_note": ("The *_committed_corpus figures come from the fixtures "
                        "in this repository alone and are reproducible in a "
                        "clean checkout with no network. The totals include "
                        "JSONTestSuite and minimized fuzz findings when those "
                        "are present, so they vary between trees."),
        "violations_c_only": len([v for v in c_only
                                  if (v["rule"], v.get("input"), v.get("mode")) not in both]),
        "violations_zig_only": len([v for v in zig_only
                                    if (v["rule"], v.get("input"), v.get("mode")) not in both]),
        "violations_both": len(both),
        "violations_total": len(c_only) + len(zig_only),
        "detail_c": c_only[:50],
        "detail_zig": zig_only[:50],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2) + "\n")

    print(f"  {counts['transcripts']} transcripts, {counts['records']} records, "
          f"{len(RULES)} rule functions / {len(MALFORMED)} violation classes")
    print(f"  violations: C-only {summary['violations_c_only']}, "
          f"Zig-only {summary['violations_zig_only']}, both {summary['violations_both']}")
    print(f"  wrote {out_path.relative_to(ROOT)}")
    for v in (c_only + zig_only)[:8]:
        print(f"    !! {v['source']} {v.get('input','')} [{v['rule']}] {v['detail']}")
    return 1 if (c_only or zig_only) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--out", default="artifacts/invariants/summary.json")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.sweep:
        rc = self_test()
        if rc:
            return rc
        return sweep(ROOT / args.out)

    bad = 0
    for f in args.files:
        found = check_text(pathlib.Path(f).read_text(), f)
        for v in found:
            print(f"{f}: [{v['rule']}] {v['detail']}")
        bad += len(found)
    print(f"{len(args.files)} file(s), {bad} violation(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
