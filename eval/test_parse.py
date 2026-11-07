#!/usr/bin/env python
"""Invariant tests for the parser and candidate generator.

No pytest dependency - run it directly:

    python eval/test_parse.py --input "<pack>"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promo_finder.candidates import (  # noqa: E402
    MAX_PROMO_S,
    MIN_PROMO_S,
    generate_candidates,
)
from promo_finder.parse import (  # noqa: E402
    format_timestamp,
    parse_pack,
    parse_timestamp,
    split_speaker_label,
)

PASS, FAIL = "  ok  ", " FAIL "
failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    print(f"[{PASS if cond else FAIL}] {name}" + (f" - {detail}" if detail else ""))
    if not cond:
        failures += 1


def test_units() -> None:
    check("timestamp parse", parse_timestamp("02:18.00") == 138.0)
    check("timestamp parse sub-minute", parse_timestamp("00:04.80") == 4.8)
    check("timestamp roundtrip", format_timestamp(138.0) == "02:18.00")
    check("timestamp roundtrip 2", format_timestamp(4.8) == "00:04.80")

    kind, spk, emo, mod = split_speaker_label("CAMERA")
    check("CAMERA label", (kind, spk, emo) == ("CAMERA", "", []))

    kind, spk, emo, mod = split_speaker_label("LADY JULIANA (Desperate, Unbelieving)")
    check(
        "dialogue label",
        (kind, spk, emo) == ("DIALOGUE", "LADY JULIANA", ["Desperate", "Unbelieving"]),
        f"{kind}/{spk}/{emo}",
    )

    kind, spk, emo, mod = split_speaker_label("MATRIARCH (O.S.) (Firm)")
    check(
        "O.S. modifier split",
        (kind, spk, emo, mod) == ("DIALOGUE", "MATRIARCH", ["Firm"], ["O.S."]),
        f"{kind}/{spk}/{emo}/{mod}",
    )

    kind, _, _, _ = split_speaker_label("SOUND FX")
    check("SOUND FX label", kind == "SOUND FX")


def test_pack(input_dir: str) -> None:
    episodes = parse_pack(input_dir)
    check("episodes parsed", len(episodes) > 0, f"{len(episodes)}")

    warnings = [w for e in episodes for w in e.warnings]
    check("zero parse warnings", not warnings, f"{len(warnings)} warnings")
    for w in warnings[:5]:
        print(f"        ! {w}")

    beats = [b for e in episodes for b in e.beats]
    check("beats parsed", len(beats) > 0, f"{len(beats)}")
    check("all beats have text", all(b.text for b in beats))
    check("all beats end >= start", all(b.end_s >= b.start_s for b in beats))
    check(
        "beat ids unique per episode",
        all(len({b.id for b in e.beats}) == len(e.beats) for e in episodes),
    )
    # CAMERA and DIALOGUE are parallel tracks, so adjacent beats legitimately
    # step back a little. Strict ordering is NOT a property of this format;
    # what must hold is that the backward steps stay small.
    backsteps = [
        a.start_s - b.start_s
        for e in episodes
        for a, b in zip(e.beats, e.beats[1:])
        if b.start_s < a.start_s
    ]
    check(
        "backward steps are small (parallel tracks)",
        not backsteps or max(backsteps) <= 2.0,
        f"{len(backsteps)} pairs, max {max(backsteps, default=0):.2f}s",
    )
    check(
        "scenes chain within each episode",
        all(
            all(abs(a.end_s - b.start_s) < 0.011 for a, b in zip(e.scenes, e.scenes[1:]))
            for e in episodes
        ),
    )
    check(
        "no beat runs past its episode",
        all(b.end_s <= e.duration + 0.011 for e in episodes for b in e.beats),
    )

    cands = [c for e in episodes for c in generate_candidates(e)]
    check("candidates generated", len(cands) > 0, f"{len(cands)}")
    bad = [c for c in cands if not (MIN_PROMO_S <= c.duration <= MAX_PROMO_S)]
    check("every candidate is 20-60s", not bad, f"{len(bad)} out of range")

    by_id = {b.id: b for e in episodes for b in e.beats}
    check(
        "candidate boundaries resolve to real beats",
        all(c.start_beat in by_id and c.end_beat in by_id for c in cands),
    )
    check(
        "candidate times match their beats",
        all(
            abs(by_id[c.start_beat].start_s - c.start_s) < 1e-6
            and abs(by_id[c.end_beat].end_s - c.end_s) < 1e-6
            for c in cands
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    args = ap.parse_args()
    print("unit tests")
    test_units()
    print("\npack tests")
    test_pack(args.input)
    print(f"\n{'FAILED: ' + str(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
