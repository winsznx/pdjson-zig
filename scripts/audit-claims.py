#!/usr/bin/env python3
"""Audit the claim ledger itself.

`scripts/validate-claims.py` checks that each claim's *machine-readable check*
passes against its artifact. That is the load-bearing check, and it has a gap:
the claim's English text is not checked at all. A claim can say "5,805
comparisons" while its check asserts `divergences == 0` against an artifact that
now reads 6,104, and everything passes.

That is not hypothetical. It is what happened when the corpus grew, and the only
thing that caught it was reading the file.

So this audits the ledger as a document:

  * every number in a claim's text must appear in the artifact it cites
  * every artifact must exist and parse
  * every check path must resolve
  * IDs must be unique and contiguous
  * required fields must be present, and every claim must carry a limitation
  * a claim not permitted in public copy must not appear in the README
  * every command must name a script or make target that exists

  python3 scripts/audit-claims.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLAIMS = ROOT / "CLAIMS.json"
README = ROOT / "README.md"

REQUIRED = ["id", "text", "status", "proof_level", "artifact", "check",
            "command", "verified_date", "limitation", "allowed_in"]
KNOWN_STATUS = {"verified", "reported-not-independently-verified",
                "partially-verified", "unverified"}
KNOWN_CHANNELS = {"readme", "devfolio", "video", "social"}

# Numbers in claim text that are not measurements and so cannot be looked up in
# an artifact. Each needs a reason, so the list cannot quietly grow.
TEXT_NUMBER_EXEMPT = {
    "36": "upstream issue number",
    "37": "upstream issue number",
    "38": "upstream issue number",
    "8259": "RFC number",
    "754": "IEEE 754",
    "912": "a line number in the pinned pdjson.c",
    "0xff": "a byte value",
    "64": "binary64",
    "20287": "a Zig issue number",
    "10737": "a Zig issue number",
    "2083": "a Zig issue number",
    "11477": "a Zig issue number",
    "0": "zero is checked by the machine-readable check, not by text matching",
    "1": "too common to be a useful signal",
    "2": "too common to be a useful signal",
    "3": "too common to be a useful signal",
    "4": "too common to be a useful signal",
    "5": "too common to be a useful signal",
    "6": "too common to be a useful signal",
    "7": "too common to be a useful signal",
    "8": "too common to be a useful signal",
    "9": "too common to be a useful signal",
    "99": "a percentile label",
    "95": "a percentile label",
    "50": "a percentile label",
    "1074": "an IEEE-754 exponent",
    "1022": "an IEEE-754 exponent",
    "53": "the binary64 significand width",
    "20260802": "a seed",
    "78": "the pinned upstream commit prefix",
    "32": "'32- and 64-bit' is prose, not a measurement",
    "0.16": "a Zig version",
    "0.17": "a Zig version",
    "30": "'30-minute' is prose; the artifact records 1800 seconds",
    "11.8": "'11.8 million' is prose; the artifact records the exact case count",
}


def numbers_in(text: str) -> list[str]:
    """Integers a reader would take as measurements."""
    out = []
    # NOT \b at the end: "26.07x" has no word boundary between "7" and "x", so
    # the regex backtracked to "26" and reported a figure nobody wrote. On macOS
    # every ratio truncated to an exempt single digit ("2.42x" -> "2") and the
    # check passed by luck; the Linux job's two-digit ratios exposed it.
    for raw in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\d,])", text):
        n = raw.replace(",", "")
        if n.endswith(".0"):
            n = n[:-2]
        out.append(n)
    return out


def artifact_numbers(obj, acc: set) -> set:
    """Every number anywhere in an artifact, as a string, plus text figures."""
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        s = repr(obj)
        if s.endswith(".0"):
            s = s[:-2]
        acc.add(s)
        # A claim writing "3.29" against a stored 3.291 is the same measurement.
        if isinstance(obj, float):
            acc.add(f"{obj:.2f}")
            acc.add(f"{obj:.1f}")
        return acc
    if isinstance(obj, str):
        for n in numbers_in(obj):
            acc.add(n)
        return acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            for n in numbers_in(str(k)):
                acc.add(n)
            artifact_numbers(v, acc)
        return acc
    if isinstance(obj, list):
        for v in obj:
            artifact_numbers(v, acc)
    return acc


def resolve(obj, path: str):
    """Walk a dotted path, through lists as well as dicts.

    An earlier version only walked dicts and reported `issues.2.url` as
    unresolvable, which validate-claims.py resolves without trouble. An audit
    that reports false problems trains its reader to ignore it.
    """
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            if not part.isdigit() or int(part) >= len(cur):
                return None, False
            cur = cur[int(part)]
            continue
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def main() -> int:
    claims = json.loads(CLAIMS.read_text())["claims"]
    readme = README.read_text() if README.exists() else ""
    problems: list[str] = []
    notes: list[str] = []

    seen_ids = set()
    for i, c in enumerate(claims, start=1):
        cid = c.get("id", f"<claim {i} with no id>")

        for field in REQUIRED:
            if field not in c or c[field] in (None, "", []):
                if field == "allowed_in" and c.get("allowed_in") == []:
                    continue  # an empty channel list is a deliberate embargo
                problems.append(f"{cid}: missing or empty field {field!r}")

        if cid in seen_ids:
            problems.append(f"{cid}: duplicate id")
        seen_ids.add(cid)
        if cid != f"C-{i:02d}":
            problems.append(f"{cid}: ids are not contiguous (expected C-{i:02d})")

        if c.get("status") not in KNOWN_STATUS:
            problems.append(f"{cid}: unknown status {c.get('status')!r}")
        for ch in c.get("allowed_in", []):
            if ch not in KNOWN_CHANNELS:
                problems.append(f"{cid}: unknown channel {ch!r}")

        # A claim embargoed from public copy must not be in the README.
        in_readme = f"| {cid} |" in readme
        if in_readme and "readme" not in c.get("allowed_in", []):
            problems.append(f"{cid}: appears in README.md but is not allowed there")
        if not in_readme and "readme" in c.get("allowed_in", []):
            notes.append(f"{cid}: allowed in the README but not currently in it")

        # The command must name something that exists.
        cmd = c.get("command", "")
        for token in re.findall(r"(?:scripts|fuzz|bench|tools|tests)/[\w./-]+", cmd):
            if not (ROOT / token).exists():
                problems.append(f"{cid}: command references missing path {token}")

        art_path = ROOT / c.get("artifact", "")
        if not art_path.exists():
            problems.append(f"{cid}: artifact {c.get('artifact')} does not exist")
            continue
        try:
            artifact = json.loads(art_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            problems.append(f"{cid}: artifact does not parse: {e}")
            continue

        check = c.get("check") or {}
        if check.get("path"):
            _, ok = resolve(artifact, check["path"])
            if not ok:
                problems.append(f"{cid}: check path {check['path']!r} does not "
                                f"resolve in {c['artifact']}")

        # The part validate-claims.py cannot see: numbers in the prose.
        available = artifact_numbers(artifact, set())
        for n in numbers_in(c["text"]):
            if n in TEXT_NUMBER_EXEMPT or n in available:
                continue
            problems.append(
                f"{cid}: text says {n} but that number appears nowhere in "
                f"{c['artifact']} -- either the claim is stale or it is citing "
                f"the wrong artifact")

    report = {
        "schema": "pdjson-zig/claim-audit@1",
        "method": ("Audits the ledger as a document, which validate-claims.py "
                   "does not: every number in a claim's English text must appear "
                   "in the artifact it cites, every artifact must exist and "
                   "parse, every check path must resolve, ids must be unique and "
                   "contiguous, every claim must carry a limitation, and a claim "
                   "embargoed from public copy must not be in the README."),
        "claims": len(claims),
        "problems": len(problems),
        "problem_detail": problems,
        "notes": notes,
        "text_number_exemptions": TEXT_NUMBER_EXEMPT,
        "limitation": ("Number matching is textual: a claim saying '6,104 "
                       "comparisons' passes if 6104 appears anywhere in the "
                       "artifact, not necessarily as the comparison count. It "
                       "catches staleness, which is the failure that actually "
                       "happens; it does not catch a number cited under the "
                       "wrong label."),
    }
    out = ROOT / "artifacts" / "claim-audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"  {len(claims)} claims audited")
    for n in notes:
        print(f"    note: {n}")
    if problems:
        print(f"  {len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"    {p}")
    else:
        print("  no problems: every number in every claim's text is present in "
              "the artifact it cites")
    print(f"  wrote {out.relative_to(ROOT)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
