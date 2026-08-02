#!/usr/bin/env python3
"""Measure what this library costs a consumer in bytes.

Archive size on its own is a bad number. A `.a` carries symbol tables, relocation
records and one section per function, and a linker discards most of it -- quoting
`ls -l libpdjson.a` against `ls -l pdjson.o` compares two things that are not the
same kind of object.

What a consumer actually pays is the difference in *their* binary. So this links
one identical C program twice -- once against the pinned original's object, once
against the Zig archive -- and reports the delta. Both links use the same
compiler, the same flags and the same source, so the only variable is which
implementation is behind the pinned header.

Three sizes are reported for each:

  archive/object   what the build produces, for reference
  linked           an executable of the same consumer, and its stripped size
  sections         per-section counts via `size`, so the machine-code figure is
                   the `__text`/`.text` *section* and not a segment the loader
                   has rounded up to a page

  python3 scripts/size-report.py
"""
from __future__ import annotations

import json
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "upstream" / "pdjson"
LIB = ROOT / "zig-out" / "lib" / "libpdjson.a"
OUT = ROOT / "artifacts" / "size-report.json"

# The smallest program that exercises the library through the pinned header.
# Deliberately tiny: anything it adds is charged equally to both sides.
CONSUMER = r"""
#include <stdio.h>
#include "pdjson.h"

int main(void)
{
    struct json_stream json[1];
    json_open_string(json, "{\"a\":[1,2,3],\"b\":\"x\"}");
    int events = 0;
    for (enum json_type t = json_next(json); t != JSON_DONE && t != JSON_ERROR;
         t = json_next(json))
        events++;
    json_close(json);
    printf("%d\n", events);
    return 0;
}
"""


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, **kw)


def sections(path: pathlib.Path) -> dict:
    """Per-section byte counts, so the comparison is not page-rounding noise.

    `size` on Mach-O reports the __TEXT *segment*, which arm64 rounds to 16 KiB.
    Comparing two segments that way turned an actual 3.3x code-size difference
    into a reported 4.0x, which is the kind of number that is wrong in a
    direction nobody checks. The `__text` section is the machine code.
    """
    out = {}
    p = sh(["size", "-m", str(path)])
    if p.returncode == 0 and "__TEXT" in p.stdout.decode():
        for line in p.stdout.decode().splitlines():
            m = re.match(r"\s+Section (__\w+): (\d+)", line)
            if m:
                out[m.group(1).lstrip("_")] = int(m.group(2))
        return out
    # ELF: `size -A` lists .text, .rodata, .data, .bss with sizes.
    p = sh(["size", "-A", str(path)])
    if p.returncode == 0:
        for line in p.stdout.decode().splitlines():
            m = re.match(r"(\.\w[\w.]*)\s+(\d+)", line)
            if m and m.group(1) in (".text", ".rodata", ".data", ".bss",
                                    ".eh_frame", ".data.rel.ro"):
                out[m.group(1).lstrip(".")] = int(m.group(2))
    return out


def panic_handler_cost(tmp: pathlib.Path) -> dict | None:
    """What the custom panic handler saves, measured rather than remembered.

    src/root.zig replaces std's default panic handler with a write+abort. The
    README quoted "4.6 MB before" from a development-time observation that
    nothing reproduced -- and it does not reproduce: the figure today is
    2.4 MB. Building both and subtracting is the only honest way to state it.
    """
    work = tmp / "default-panic"
    for item in ("build.zig", "src", "tools", "include"):
        src, dst = ROOT / item, work / item
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    target = work / "src" / "root.zig"
    text = target.read_text()
    marker = "pub const panic = std.debug.FullPanic("
    if marker not in text:
        return None
    i = text.index(marker)
    j = text.index("}.handler);", i) + len("}.handler);")
    target.write_text(text[:i] + "// std's default panic handler\n" + text[j:])

    build = sh(["zig", "build", "--prefix", str(work / "out")], cwd=work)
    if build.returncode != 0:
        return None
    default_lib = work / "out" / "lib" / "libpdjson.a"
    if not default_lib.exists():
        return None
    a, b = default_lib.stat().st_size, LIB.stat().st_size
    return {
        "with_std_default_handler_bytes": a,
        "with_custom_handler_bytes": b,
        "saved_bytes": a - b,
        "ratio": round(a / b, 2) if b else None,
        "why": ("std's default handler pulls in the unwinder, the DWARF reader "
                "and symbol tables. A library whose whole job is to parse bytes "
                "has no Zig caller to catch a panic, so aborting is the right "
                "behaviour and the machinery is pure cost."),
    }


def main() -> int:
    if not LIB.exists():
        print(f"missing {LIB}; run 'make build' first", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        src = tmp / "consumer.c"
        src.write_text(CONSUMER)

        # The pinned original, compiled the way its own Makefile does.
        c_obj = tmp / "pdjson.o"
        p = sh(["cc", "-std=c99", "-O2", "-c", "-o", str(c_obj),
                str(UPSTREAM / "pdjson.c"), "-I", str(UPSTREAM)])
        if p.returncode != 0:
            print("failed to compile the pinned original:",
                  p.stderr.decode()[:400], file=sys.stderr)
            return 1

        results = {}
        for name, link_input in (("c", c_obj), ("zig", LIB)):
            exe = tmp / f"consumer_{name}"
            p = sh(["cc", "-std=c99", "-O2", "-o", str(exe), str(src),
                    str(link_input), "-I", str(UPSTREAM)])
            if p.returncode != 0:
                print(f"failed to link the {name} consumer:",
                      p.stderr.decode()[:400], file=sys.stderr)
                return 1
            # Confirm it runs and agrees, so a size is never reported for a
            # binary that does not work.
            r = sh([str(exe)])
            if r.returncode != 0:
                print(f"the {name} consumer built but did not run", file=sys.stderr)
                return 1
            events = r.stdout.decode().strip()

            stripped = tmp / f"consumer_{name}_stripped"
            shutil.copy(exe, stripped)
            sh(["strip", "-x", str(stripped)])

            results[name] = {
                "input": str(link_input.name),
                "input_bytes": link_input.stat().st_size,
                "linked_bytes": exe.stat().st_size,
                "linked_stripped_bytes": stripped.stat().st_size,
                "sections": sections(exe),
                "consumer_output": events,
            }

        if results["c"]["consumer_output"] != results["zig"]["consumer_output"]:
            print("the two consumers disagree; refusing to report sizes for "
                  "binaries that do not behave the same", file=sys.stderr)
            return 1

        def delta(field):
            a, b = results["c"][field], results["zig"][field]
            if a is None or b is None:
                return None
            return {"c": a, "zig": b, "delta": b - a,
                    "ratio": round(b / a, 3) if a else None}

        def section_delta(*names):
            def pick(impl):
                secs = results[impl]["sections"]
                for n in names:
                    if n in secs:
                        return secs[n]
                return None
            a, b = pick("c"), pick("zig")
            if a is None or b is None:
                return None
            return {"c": a, "zig": b, "delta": b - a,
                    "ratio": round(b / a, 3) if a else None}

        summary = {
            "schema": "pdjson-zig/size-report@1",
            "method": (
                "One identical C consumer, compiled with the same compiler and "
                "flags, linked twice: once against the pinned original's .o, "
                "once against the Zig archive. Archive-vs-object byte counts are "
                "reported for reference but are not comparable -- a .a carries "
                "symbol and relocation data the linker discards. The linked and "
                "text figures are what a consumer actually pays."
            ),
            "consumer_agreement": results["c"]["consumer_output"],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "build_input": delta("input_bytes"),
            "linked_executable": delta("linked_bytes"),
            "linked_stripped": delta("linked_stripped_bytes"),
            "machine_code": section_delta("text"),
            "read_only_data": section_delta("const", "rodata"),
            "string_data": section_delta("cstring"),
            "unwind_tables": section_delta("eh_frame"),
            "panic_handler": panic_handler_cost(tmp),
            "detail": results,
            "note": (
                "The Zig side ships ReleaseSafe, so its machine code carries the "
                "bounds and overflow checks and the panic path the C build has no "
                "equivalent of, and it emits unwind tables (eh_frame) that the C "
                "build does not. src/root.zig replaces std's default panic handler "
                "with a write+abort specifically to keep std's unwinder, DWARF "
                "reader and symbol tables out of the artifact; the panic_handler "
                "section below measures what that saves. This is a real cost, "
                "reported rather than worked around: the port is larger than the "
                "original."
            ),
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2) + "\n")

    def line(label, d):
        if not d:
            return
        print(f"  {label:<22} C {d['c']:>9,}   Zig {d['zig']:>9,}   "
              f"{d['delta']:+,} ({d['ratio']}x)")

    line("build input", summary["build_input"])
    line("linked executable", summary["linked_executable"])
    line("linked, stripped", summary["linked_stripped"])
    line("machine code", summary["machine_code"])
    line("read-only data", summary["read_only_data"])
    line("string data", summary["string_data"])
    line("unwind tables", summary["unwind_tables"])
    print(f"  wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
