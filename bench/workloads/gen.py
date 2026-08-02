#!/usr/bin/env python3
"""Generate the benchmark workloads.

Committed as generated files rather than produced at benchmark time, so the
inputs are byte-identical across machines and runs. Each name states what the
workload is meant to stress.
"""
import json
import pathlib
import random

OUT = pathlib.Path(__file__).resolve().parent
rng = random.Random(20260802)

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
         "hotel", "india", "juliet", "kilo", "lima", "mike", "november"]


def w(name: str, data) -> None:
    if isinstance(data, (dict, list)):
        data = json.dumps(data, separators=(",", ":"))
    if isinstance(data, str):
        data = data.encode("utf-8")
    (OUT / f"{name}.json").write_bytes(data)
    print(f"  {name}.json  {len(data):>10,} bytes")


def record(i: int) -> dict:
    return {
        "id": i,
        "name": f"{rng.choice(WORDS)}-{i}",
        "active": rng.random() < 0.5,
        "score": round(rng.uniform(-1000, 1000), 6),
        "tags": [rng.choice(WORDS) for _ in range(rng.randint(0, 5))],
        "meta": {"created": f"2026-0{rng.randint(1,9)}-1{rng.randint(0,9)}",
                 "revision": rng.randint(0, 100000)},
    }


def main() -> None:
    print("writing benchmark workloads:")

    # A large, realistic mixed document: the headline throughput case.
    w("large-mixed", {"records": [record(i) for i in range(6000)]})

    # Numbers only, in the shapes that stress the number lexer and strtod.
    nums = []
    for i in range(60000):
        k = i % 6
        if k == 0:   nums.append(i)
        elif k == 1: nums.append(-i)
        elif k == 2: nums.append(round(i / 7, 9))
        elif k == 3: nums.append(float(f"1e{i % 300 - 150}"))
        elif k == 4: nums.append(i * 10**12)
        else:        nums.append(-round(i / 3.0, 12))
    w("numbers", nums)

    # Strings only, ASCII: the token-buffer copy path.
    w("strings-ascii", ["".join(rng.choice(WORDS) for _ in range(8))
                        for _ in range(20000)])

    # Strings with escapes and multi-byte UTF-8: the decode and re-encode path.
    fancy = []
    for _ in range(12000):
        fancy.append("".join(rng.choice([
            "plain", "é", "中文", "\U0001F600", "tab\there",
            "quote\"inside", "back\\slash", "\u0000nul",
        ]) for _ in range(4)))
    w("strings-unicode", fancy)

    # Deep nesting: the container-stack growth path.
    depth = 20000
    w("deep-nesting", "[" * depth + "1" + "]" * depth)

    # Many small documents in streaming mode: per-value setup and reset cost.
    w("many-small-docs", "\n".join(json.dumps({"i": i, "v": [i, i + 1]},
                                              separators=(",", ":"))
                                   for i in range(40000)))

    # Malformed early: how fast the parser gives up on garbage at the front.
    w("malformed-early", "@" + "x" * 200000)

    # Malformed late: a valid prefix followed by a structural error, so the
    # cost is dominated by successful parsing before the failure.
    big = json.dumps([record(i) for i in range(4000)], separators=(",", ":"))
    w("malformed-late", big[:-1] + ",")

    # Whitespace-heavy: the skip loop rather than token work.
    w("whitespace-heavy", "[\n" + ",\n".join("    1" + " " * 40
                                             for _ in range(20000)) + "\n]")

    # Flat array of small integers: the cheapest possible per-event work.
    w("flat-ints", list(range(120000)))


if __name__ == "__main__":
    main()
