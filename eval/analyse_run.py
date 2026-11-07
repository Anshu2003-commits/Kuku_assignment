#!/usr/bin/env python
"""Summarise a run: score distributions, rejects, bias checks.

Separate from score_eval.py (which needs hand labels). This one needs nothing
but the run's own output, and is the fastest way to see whether the judge is
actually discriminating or just rubber-stamping.

    python eval/analyse_run.py --out-dir outputs
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

AXES = ("hook_strength", "cliffhanger_strength", "standalone", "spoiler_risk")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="outputs")
    args = ap.parse_args(argv)
    d = Path(args.out_dir)

    judgements = json.loads((d / "judgements.json").read_text(encoding="utf-8"))
    payload = json.loads((d / "promos.json").read_text(encoding="utf-8"))
    promos, meta = payload["promos"], payload["meta"]
    report = json.loads((d / "run_report.json").read_text(encoding="utf-8"))

    print(f"=== {meta['show']} ===")
    print(f"model          : {meta['model']}")
    print(f"episodes/beats : {meta['n_episodes']} / {meta['n_beats']}")
    print(f"candidates     : {meta['n_candidates']}")
    print(f"judged         : {len(judgements)}  ({len(report['judge_errors'])} failed)")
    kept = [j for j in judgements if j["keep"]]
    print(f"kept           : {len(kept)} ({len(kept) / max(1, len(judgements)):.0%})")
    print(f"boundary flags : {len(report['boundary_flags'])}")
    print(f"wall time      : {meta['elapsed_s']}s")

    print("\n--- score distribution (all judged) ---")
    for axis in AXES:
        vals = [j[axis] for j in judgements]
        dist = Counter(vals)
        bars = " ".join(f"{k}:{dist.get(k, 0):>3}" for k in range(1, 6))
        spread = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        print(f"{axis:22s} {bars}   mean {statistics.mean(vals):.2f} sd {spread:.2f}")

    print("\n--- how often does the model move the boundaries? ---")
    moved = sum(
        1
        for j in judgements
        if j["start_beat"] != j["candidate"]["start_beat"]
        or j["end_beat"] != j["candidate"]["end_beat"]
    )
    print(f"tightened by the model : {moved}/{len(judgements)} ({moved / max(1, len(judgements)):.0%})")

    print("\n--- episode-position bias ---")
    eps = [j["candidate"]["episode"] for j in judgements]
    kept_eps = [j["candidate"]["episode"] for j in kept]
    slate_eps = [e for p in promos for e in p["episodes"]]
    for name, pool in (("candidates", eps), ("kept", kept_eps), ("slate", slate_eps)):
        if pool:
            print(f"{name:11s} mean episode {statistics.mean(pool):5.1f}  "
                  f"range {min(pool)}-{max(pool)}")

    print("\n--- where in the episode do clips start? ---")
    # Fraction of the episode elapsed at the clip's in-point.
    print("(needs the pack to compute precisely; using raw start seconds)")
    starts = [j["start_s"] for j in kept]
    if starts:
        print(f"start_s mean {statistics.mean(starts):.1f}s  "
              f"median {statistics.median(starts):.1f}s  max {max(starts):.1f}s")

    print("\n--- duration ---")
    durs = [p["duration"] for p in promos]
    if durs:
        print(f"slate durations mean {statistics.mean(durs):.1f}s  "
              f"min {min(durs):.1f}  max {max(durs):.1f}")

    print(f"\n--- rejects ({len(report['rejects'])}) ---")
    for r in report["rejects"][:12]:
        print(f"  Ep{r['episode']:02d} {r['start']}: {r['reason']}")
    if len(report["rejects"]) > 12:
        print(f"  ... and {len(report['rejects']) - 12} more")

    print("\n--- slate composition ---")
    print(f"singles {sum(1 for p in promos if p['kind'] == 'single')}  "
          f"stitches {sum(1 for p in promos if p['kind'] == 'stitch')}")
    dupes = Counter(tuple(p["episodes"]) for p in promos)
    multi = {k: v for k, v in dupes.items() if v > 1}
    print(f"episodes appearing more than once in the slate: {multi or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
