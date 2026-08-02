#!/usr/bin/env python3
"""Check that every figure in outward-facing copy is backed by an artifact.

`scripts/audit-claims.py` audits CLAIMS.json. This audits the documents a judge
or a reader actually sees -- the staged Devfolio submission, the demo script, the
README -- because those are where an unverified number does real damage.

Two rules:

  * **Every number must be findable.** A figure in public copy has to appear
    somewhere in `artifacts/`, or be listed as prose with a reason. Not "roughly
    right"; present.
  * **Embargoed claims must not appear.** A claim whose `allowed_in` excludes a
    channel must not be described in that channel's copy. The Zig
    `std.fmt.parseFloat` defect is reproduced but unfiled, so it is barred from
    every public channel; this is what enforces that rather than remembering it.

  python3 scripts/audit-public-copy.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"

# document -> (channel, whether fenced blocks are content or commands)
#
# The distinction matters more than it looks. In the README a fenced block is a
# shell command, and its flags and paths are not claims. In the staged Devfolio
# copy the fenced blocks ARE the submission text -- they are fenced so they can
# be pasted into the form -- and stripping them made this audit blind to exactly
# the document it exists to check. It reported "0 unbacked" for a file whose
# every figure it had thrown away.
DOCUMENTS = {
    "docs/devfolio-submission.md": ("devfolio", True),
    "docs/demo-script.md": ("video", True),
    "README.md": ("readme", False),
}

# Numbers that are not measurements. Each carries its reason so the list cannot
# quietly become a way to silence the check.
PROSE_NUMBERS = {
    "8259": "RFC number",
    "7159": "RFC number",
    "4627": "RFC number",
    "754": "IEEE 754",
    "99": "C99, or a percentile label",
    "11": "C11",
    "36": "upstream issue number",
    "37": "upstream issue number",
    "38": "upstream issue number",
    "912": "a line number in the pinned pdjson.c",
    "2024": "the upstream commit's year",
    "2026": "the hackathon year",
    "1": "too common to be a signal", "2": "too common to be a signal",
    "3": "too common to be a signal", "4": "too common to be a signal",
    "5": "too common to be a signal", "6": "too common to be a signal",
    "7": "too common to be a signal", "8": "too common to be a signal",
    "9": "too common to be a signal", "0": "too common to be a signal",
    "10": "too common to be a signal", "12": "too common to be a signal",
    "20": "too common to be a signal", "30": "a duration in minutes",
    "40": "a terminal height", "45": "a tail length", "120": "a terminal width",
    "0.16": "a Zig version", "0.17": "a Zig version", "3.11": "a Python version",
    "1.3": "a C standard section number", "7.20": "a C standard section number",
    "20.1": "a C standard section number",
    "1974": "a licence year", "1970": "a licence year",
    "24": "a section or clause number",
    "80": "a column width",
    "16": "a byte or page size",
    "64": "binary64, or a bit width",
    "32": "a bit width",
    "1000": "a rounding of a larger figure, spelled out elsewhere",
    "100": "a percentage",
    "142": "a historical fixture count, explicitly labelled as superseded",
    "214": "a historical fixture count, explicitly labelled as superseded",
    "4.6": "a figure quoted as not reproducing, with the measurement that replaces it",
}

# Phrases that identify an embargoed subject in prose. Keyed by claim id.
EMBARGO_MARKERS = {
    "C-27": ["parseFloat", "parsefloat"],
}


def numbers_in(text: str, fenced_is_content: bool = False) -> set[str]:
    out = set()
    if fenced_is_content:
        # Keep the block's prose, drop only lines that are plainly shell:
        # a leading $, or a command this repository actually runs.
        def keep(block: str) -> str:
            lines = []
            for line in block.split("\n"):
                t = line.strip()
                if t.startswith(("$", "#", "```")):
                    continue
                if re.match(r"^(make|python3|sh|zig|cc|clear|git|ar |nm |diff|ASAN|\./|docker)\b", t):
                    continue
                lines.append(line)
            return "\n".join(lines)
        text = re.sub(r"```[^\n]*\n(.*?)```", lambda m: keep(m.group(1)),
                      text, flags=re.S)
    else:
        # A shell command's flags and paths are not claims.
        text = re.sub(r"```.*?```", "", text, flags=re.S)
    # Strip inline code for the same reason.
    text = re.sub(r"`[^`]*`", "", text)
    # Strip link targets, which carry issue numbers and anchors.
    text = re.sub(r"\]\([^)]*\)", "]", text)
    # Identifiers, not measurements: claim ids, decision ids, issue references,
    # and timestamps in a demo script's cue sheet.
    text = re.sub(r"\b[CD]-\d+", "", text)
    text = re.sub(r"#\d+", "", text)
    text = re.sub(r"\b\d{1,2}:\d{2}\b", "", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", text)
    # A trailing "(46)" on a line of staged copy is that field's character
    # count against the platform's limit, not a measurement.
    text = re.sub(r"\(\d{1,3}\)\s*$", "", text, flags=re.M)
    for raw in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", text):
        n = raw.replace(",", "")
        if n.endswith(".0"):
            n = n[:-2]
        out.add(n)
    return out


def _walk(obj, seen: set[str]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        s = repr(obj)
        if s.endswith(".0"):
            s = s[:-2]
        seen.add(s)
        # A figure written to two decimal places in prose ("2.42x") against a
        # value stored to three ("2.416") is the same measurement.
        if isinstance(obj, float):
            seen.add(f"{obj:.2f}")
            seen.add(f"{obj:.1f}")
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk(v, seen)
        return
    if isinstance(obj, list):
        for v in obj:
            _walk(v, seen)


def artifact_corpus() -> set[str]:
    """Every JSON *value* under artifacts/, not every digit in every file.

    An earlier version regex-scanned the raw text, which meant a figure matched
    if its digits appeared anywhere -- inside a sha256, a base64 blob, a
    transcript. With megabytes of hex in artifacts/ that check passed almost
    unconditionally, and it did: four development-time profiling figures with no
    artifact behind them sailed through it. Matching parsed values only is what
    makes a pass mean something.
    """
    seen: set[str] = set()
    for path in sorted(ARTIFACTS.rglob("*.json")):
        try:
            _walk(json.loads(path.read_text()), seen)
        except (OSError, json.JSONDecodeError):
            continue
    return seen


def main() -> int:
    claims = json.loads((ROOT / "CLAIMS.json").read_text())["claims"]
    corpus = artifact_corpus()
    problems: list[str] = []
    checked = {}

    for doc, (channel, fenced) in DOCUMENTS.items():
        path = ROOT / doc
        if not path.exists():
            problems.append(f"{doc}: missing")
            continue
        text = path.read_text()

        unbacked = []
        for n in sorted(numbers_in(text, fenced), key=lambda x: (len(x), x)):
            if n in PROSE_NUMBERS or n in corpus:
                continue
            unbacked.append(n)
        for n in unbacked:
            problems.append(
                f"{doc}: the figure {n} appears in outward-facing copy but "
                f"nowhere in artifacts/ -- either it is unverified or it needs a "
                f"reason in PROSE_NUMBERS")

        for c in claims:
            if channel in c.get("allowed_in", []):
                continue
            # A claim may be *disclosed* in a channel it may not be *claimed*
            # in: "we found this, could not get it confirmed, and are not
            # counting it" is the opposite speech act to asserting a result.
            # The distinction is recorded in CLAIMS.json rather than left to
            # judgement, and disclosure has to be granted explicitly.
            if channel in c.get("disclosure_in", []):
                continue
            for marker in EMBARGO_MARKERS.get(c["id"], []):
                if marker in text:
                    problems.append(
                        f"{doc}: mentions {marker!r}, but {c['id']} is neither "
                        f"allowed nor disclosable in the {channel!r} channel")
        checked[doc] = {"channel": channel,
                        "fenced_blocks_are_content": fenced,
                        "numbers": len(numbers_in(text, fenced)),
                        "unbacked": unbacked}

    report = {
        "schema": "pdjson-zig/public-copy-audit@1",
        "method": ("Every number in outward-facing copy must appear somewhere "
                   "under artifacts/, or carry a recorded reason for being "
                   "prose. Claims embargoed from a channel must not be "
                   "described in that channel's copy. Code blocks, inline code "
                   "and link targets are stripped first -- a flag or an issue "
                   "number is not a claim."),
        "documents": checked,
        "problems": len(problems),
        "problem_detail": problems,
        "limitation": ("Presence, not correspondence: a figure passes if it "
                       "appears anywhere in artifacts/, not necessarily under "
                       "the label the copy gives it. It catches invented and "
                       "stale numbers, which are the failures that happen."),
    }
    out = ARTIFACTS / "public-copy-audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    for doc, info in checked.items():
        print(f"  {doc}: {info['numbers']} figures, "
              f"{len(info['unbacked'])} unbacked")
    if problems:
        print(f"  {len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"    {p}")
    else:
        print("  every figure in outward-facing copy is backed by an artifact")
    print(f"  wrote {out.relative_to(ROOT)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
