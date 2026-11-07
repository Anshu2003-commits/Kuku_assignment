#!/usr/bin/env python
"""Build a blank labelling sheet, stratified across the ranking.

Samples from the top of the slate, the middle, the tail, and the rejects, so
precision is measured on more than the clips the tool is proudest of.

    python eval/make_sample.py --out eval/labels.csv --n 30
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--promos", default="outputs/promos.json")
    ap.add_argument("--judgements", default="outputs/judgements.json")
    ap.add_argument("--out", default="eval/labels.csv")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    random.seed(args.seed)

    promos = json.loads(Path(args.promos).read_text(encoding="utf-8"))["promos"]
    judgements = json.loads(Path(args.judgements).read_text(encoding="utf-8"))

    singles = [p for p in promos if p["kind"] == "single"]
    top = singles[:10]
    mid = singles[10:]
    rejects = [j for j in judgements if not j["keep"]]

    n_top = min(10, len(top))
    n_mid = min(10, len(mid))
    n_rej = min(max(0, args.n - n_top - n_mid), len(rejects))
    picked_mid = random.sample(mid, n_mid) if mid else []
    picked_rej = random.sample(rejects, n_rej) if rejects else []

    rows = []
    for p in top + picked_mid:
        seg = p["segments"][0]
        rows.append(
            {
                "id": f"{seg['episode']}:{seg['start_beat']}",
                "slice": "top10" if p in top else "slate_tail",
                "rank": p["rank"],
                "timecodes": " | ".join(p["timecodes"]),
                "duration_s": p["duration"],
                "hook": p["hook"],
                "cliffhanger": p["cliffhanger"],
                "verdict": "",
                "failure_mode": "",
                "note": "",
            }
        )
    for j in picked_rej:
        rows.append(
            {
                "id": f"{j['candidate']['episode']}:{j['start_beat']}",
                "slice": "reject",
                "rank": "",
                "timecodes": f"Ep {j['candidate']['episode']:02d}",
                "duration_s": j["duration"],
                "hook": j["hook"],
                "cliffhanger": j["cliffhanger"],
                "verdict": "",
                "failure_mode": "",
                "note": "",
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")
    print("fill in `verdict` (good|borderline|bad) and optionally `failure_mode`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
