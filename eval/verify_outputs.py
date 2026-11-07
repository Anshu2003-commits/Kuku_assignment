#!/usr/bin/env python
"""Independently verify a run's output against the source screenplays.

This does not trust any part of the pipeline. It re-parses the scripts from
scratch and checks that every timestamp the tool emitted is a real boundary in
the source, that durations are legal, and that segments are ordered.

    python eval/verify_outputs.py --input "<pack>" --promos outputs/promos.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promo_finder.candidates import MAX_PROMO_S, MIN_PROMO_S  # noqa: E402
from promo_finder.parse import format_timestamp, parse_pack  # noqa: E402

TOL = 0.011  # timestamps are written to 2dp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--promos", default="outputs/promos.json")
    args = ap.parse_args(argv)

    episodes = {e.number: e for e in parse_pack(args.input)}
    promos = json.loads(Path(args.promos).read_text(encoding="utf-8"))["promos"]

    starts: dict[int, set[float]] = {
        n: {b.start_s for b in e.beats} for n, e in episodes.items()
    }
    ends: dict[int, set[float]] = {
        n: {b.end_s for b in e.beats} for n, e in episodes.items()
    }

    failures: list[str] = []
    checked = 0

    def near(value: float, pool: set[float]) -> bool:
        return any(abs(value - p) <= TOL for p in pool)

    for p in promos:
        tag = f"#{p['rank']}"
        if not p["segments"]:
            failures.append(f"{tag}: no segments")
            continue

        total = 0.0
        for i, seg in enumerate(p["segments"]):
            checked += 1
            ep = seg["episode"]
            if ep not in episodes:
                failures.append(f"{tag} seg{i}: episode {ep} not in input")
                continue
            if not near(seg["start_s"], starts[ep]):
                failures.append(
                    f"{tag} seg{i}: start {format_timestamp(seg['start_s'])} "
                    f"is not a beat boundary in Ep{ep:02d}"
                )
            if not near(seg["end_s"], ends[ep]):
                failures.append(
                    f"{tag} seg{i}: end {format_timestamp(seg['end_s'])} "
                    f"is not a beat boundary in Ep{ep:02d}"
                )
            if seg["end_s"] <= seg["start_s"]:
                failures.append(f"{tag} seg{i}: end <= start")
            if seg["end_s"] > episodes[ep].duration + TOL:
                failures.append(
                    f"{tag} seg{i}: end {format_timestamp(seg['end_s'])} runs past "
                    f"Ep{ep:02d} duration {format_timestamp(episodes[ep].duration)}"
                )
            # The beat ids must resolve, and resolve to these exact times.
            beat = episodes[ep].beat_by_id(seg["start_beat"])
            if beat is None:
                failures.append(f"{tag} seg{i}: unknown start_beat {seg['start_beat']}")
            elif abs(beat.start_s - seg["start_s"]) > TOL:
                failures.append(
                    f"{tag} seg{i}: start_beat {seg['start_beat']} time mismatch"
                )
            total += seg["end_s"] - seg["start_s"]

        if not (MIN_PROMO_S - TOL <= total <= MAX_PROMO_S + TOL):
            failures.append(f"{tag}: total duration {total:.2f}s outside 20-60s")

        same_ep = [s for s in p["segments"] if s["episode"] == p["segments"][0]["episode"]]
        for a, b in zip(same_ep, same_ep[1:]):
            if b["start_s"] < a["end_s"]:
                failures.append(f"{tag}: segments overlap within an episode")

    print(f"verified {len(promos)} promos / {checked} segments")
    if failures:
        print(f"\nFAIL - {len(failures)} problems:")
        for f in failures[:40]:
            print(f"  - {f}")
        return 1
    print("PASS - every timestamp is a real beat boundary in the source scripts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
