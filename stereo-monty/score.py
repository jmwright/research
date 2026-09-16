#!/usr/bin/env python
"""Score a gate run: one line per object, three rotations each, then the totals.

`result` holds the object the episode terminated on, and is empty on a time out.
`primary_performance` is not usable here - it compares against the scene directory
name, which cannot equal a library object's name - so the verdict is derived from
`result` against the object that was actually presented.
"""
import csv
import os
import sys

OBJECTS = ["mug", "block", "glass", "bar", "i"]


def verdict(row: dict, obj: str) -> str:
    result = row["result"]
    if not result:
        return "time_out"
    if result == f"rig_{obj}":
        return "correct"
    if result == "no_match":
        return "no_match"
    return f"confused->{result}"


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    totals: dict[str, int] = {}
    missing = False
    for obj in OBJECTS:
        path = os.path.join(out, f"rig_{obj}", "eval_stats.csv")
        if not os.path.exists(path):
            print(f"{obj:6s} MISSING - {path}")
            missing = True
            continue
        cells = []
        for row in csv.DictReader(open(path)):
            v = verdict(row, obj)
            totals[v.split("->")[0]] = totals.get(v.split("->")[0], 0) + 1
            cells.append(f"{v}({row['num_steps']})")
        print(f"{obj:6s} " + " | ".join(cells))
    print()
    print("  ".join(f"{k}: {v}" for k, v in sorted(totals.items())))
    correct = totals.get("correct", 0)
    print(f"\n{correct} of 15" + (" - expected 15 of 15" if correct != 15 else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
