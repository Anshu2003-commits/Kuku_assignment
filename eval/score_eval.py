#!/usr/bin/env python
"""Quality check: score the tool's picks against hand labels.

    python eval/make_sample.py --out eval/labels.csv --n 30   # blank sheet
    (label `verdict` by hand: good | borderline | bad)
    python eval/score_eval.py

Every row is labelled for CLIP QUALITY against the brief's three criteria
(hook inside the first 3s, ends unresolved, standalone). That means:

  * for slate rows, `good` = the tool was right to promote it
  * for reject rows, `bad`  = the tool was right to drop it

so the two slices are read in opposite directions, and the report says so.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

ORDER = ["top10", "slate_tail", "reject"]


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.DictReader(fh) if r.get("verdict", "").strip()]


def pct(n: int, d: int) -> str:
    return f"{n / d:.0%}" if d else "n/a"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="eval/labels.csv")
    ap.add_argument("--out", default="eval/eval_report.md")
    args = ap.parse_args(argv)

    rows = load(Path(args.labels))
    if not rows:
        print("no labelled rows - fill in the `verdict` column first")
        return 1

    by_slice: dict[str, list[dict]] = {}
    for r in rows:
        by_slice.setdefault(r.get("slice", "other"), []).append(r)

    def count(subset: list[dict], verdict: str) -> int:
        return sum(1 for r in subset if r["verdict"].strip().lower() == verdict)

    out = [
        "# Quality check\n",
        f"{len(rows)} clips hand-labelled against the brief's three criteria: "
        "a hook inside the first 3 seconds, an unresolved ending, and making "
        "sense to someone who has never seen the show.\n",
        "| Slice | n | good | borderline | bad | reading |",
        "|---|---|---|---|---|---|",
    ]

    slate_rows: list[dict] = []
    for name in ORDER:
        subset = by_slice.get(name, [])
        if not subset:
            continue
        g, b, x = count(subset, "good"), count(subset, "borderline"), count(subset, "bad")
        if name == "reject":
            reading = f"**{pct(x, len(subset))} correctly dropped**"
        else:
            reading = f"**{pct(g, len(subset))} clean, {pct(g + b, len(subset))} usable**"
            slate_rows.extend(subset)
        out.append(f"| {name} | {len(subset)} | {g} | {b} | {x} | {reading} |")

    if slate_rows:
        g = count(slate_rows, "good")
        b = count(slate_rows, "borderline")
        out.append(
            f"| **whole slate** | {len(slate_rows)} | {g} | {b} | "
            f"{count(slate_rows, 'bad')} | "
            f"**{pct(g, len(slate_rows))} clean, "
            f"{pct(g + b, len(slate_rows))} usable** |"
        )

    failures = Counter(
        f.strip()
        for r in rows
        for f in (r.get("failure_mode") or "").split(";")
        if f.strip()
    )
    if failures:
        out.append("\n## Failure modes, by frequency\n")
        for mode, n in failures.most_common():
            out.append(f"- **{mode}** — {n}")

    out.append("\n## Every labelled clip\n")
    for name in ORDER:
        subset = by_slice.get(name, [])
        if not subset:
            continue
        out.append(f"\n### {name}\n")
        for r in sorted(subset, key=lambda r: int(r["rank"]) if r["rank"] else 999):
            rank = f"#{r['rank']}" if r["rank"] else "—"
            out.append(
                f"- {rank} `{r['id']}` **{r['verdict']}**"
                + (f" ({r['failure_mode']})" if r["failure_mode"] else "")
                + (f" — {r['note']}" if r["note"] else "")
            )

    report = "\n".join(out) + "\n"
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
