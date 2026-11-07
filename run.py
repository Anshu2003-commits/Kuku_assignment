#!/usr/bin/env python
"""CLI: find promos in a folder of microdrama screenplays.

    python run.py --input "<folder of episode .txt files>" --out outputs/
    python run.py --input ... --no-llm          # heuristics only, no API key
    python run.py --input ... --provider groq --model llama-3.3-70b-versatile

Works on any folder of episode .txt files, not just the pack it was built on.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from promo_finder.candidates import TOP_PER_EPISODE, Candidate, generate_all
from promo_finder.judge import Judgement, judge_all
from promo_finder.llm import RateLimiter, build_llm, default_rate_limiter
from promo_finder.parse import filter_episodes, parse_pack
from promo_finder.rank import build_stitch_promos, dedupe_slate, rank_promos
from promo_finder.render import (
    write_csv,
    write_judgements,
    write_json,
    write_markdown,
    write_report,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Find promo clips in screenplays.")
    ap.add_argument("--input", required=True, help="folder of episode .txt files")
    ap.add_argument("--out", default="outputs", help="output folder")
    ap.add_argument("--show", default=None, help="show name (defaults to folder name)")
    ap.add_argument("--provider", default=None, help="gemini|openai|groq|anthropic|mock")
    ap.add_argument("--model", default=None, help="model id for the provider")
    ap.add_argument("--no-llm", action="store_true", help="heuristics only (MockLLM)")
    ap.add_argument("--per-episode", type=int, default=TOP_PER_EPISODE)
    ap.add_argument("--limit", type=int, default=20, help="promos in the final slate")
    ap.add_argument("--stitches", type=int, default=3, help="multi-segment promos")
    ap.add_argument("--workers", type=int, default=4, help="parallel LLM calls")
    ap.add_argument(
        "--rpm",
        type=int,
        default=None,
        help="requests/minute cap (default: 14 for gemini free tier)",
    )
    ap.add_argument("--episodes", default=None, help="filter, e.g. '40-50' or '1,2,3'")
    ap.add_argument(
        "--reuse-judgements",
        default=None,
        metavar="PATH",
        help="re-rank a previous run's judgements.json instead of calling the "
        "LLM again (free, and makes ranking changes reproducible)",
    )
    args = ap.parse_args(argv)

    t0 = time.time()
    in_dir = Path(args.input)
    if not in_dir.is_dir():
        print(f"error: {in_dir} is not a directory", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    show = args.show or in_dir.name

    print(f"[1/5] parsing {in_dir} ...")
    episodes = filter_episodes(parse_pack(in_dir), args.episodes)
    if not episodes:
        print("error: no episodes parsed", file=sys.stderr)
        return 2
    warnings = [w for e in episodes for w in e.warnings]
    n_beats = sum(len(e.beats) for e in episodes)
    print(f"      {len(episodes)} episodes, {n_beats} beats, {len(warnings)} warnings")
    for w in warnings[:20]:
        print(f"      ! {w}")

    print(f"[2/5] generating candidates (top {args.per_episode}/episode) ...")
    candidates = generate_all(episodes, top_n=args.per_episode)
    print(f"      {len(candidates)} candidates")

    print("[3/5] judging ...")
    if args.reuse_judgements:
        judgements, errors, notes = load_judgements(args.reuse_judgements)
        model_name = judgements[0].judged_by if judgements else "reused"
        print(f"      reused {len(judgements)} judgements from "
              f"{args.reuse_judgements} (no API calls)")
    else:
        judgements, errors, notes, model_name = run_judging(args, candidates, episodes, show)
    kept = sum(1 for j in judgements if j.keep)
    flagged = sum(1 for j in judgements if j.flags)
    print(
        f"      {len(judgements)} judged, {kept} kept, {flagged} boundary-flagged, "
        f"{len(errors)} failed"
    )

    print("[4/5] ranking ...")
    # Over-generate: dedupe below removes overlapping footage, so ranking a
    # wider pool keeps the final slate at --limit.
    promos = rank_promos(judgements, limit=args.limit * 3)
    if args.stitches:
        stitched = build_stitch_promos(
            judgements, episodes, max_stitches=args.stitches
        )
        promos.extend(stitched)
        print(f"      {len(stitched)} multi-segment promos proposed")
    before = len(promos)
    promos = dedupe_slate(promos)[: args.limit]
    if before != len(promos):
        print(f"      {before - len(promos)} dropped as overlapping or surplus")
    print(f"      {len(promos)} promos in the slate")
    llm_name = model_name
    return finish(args, episodes, candidates, judgements, errors, notes,
                  promos, llm_name, show, in_dir, out_dir, warnings, n_beats, t0)


def run_judging(args, candidates, episodes, show):
    """Judge every candidate with the configured provider."""
    provider = "mock" if args.no_llm else args.provider
    llm, notes = build_llm(provider=provider, model=args.model)
    for n in notes:
        print(f"      ! {n}")
    print(f"      model: {llm.name}")
    limiter = (
        None
        if llm.name == "mock"
        else (
            RateLimiter(args.rpm)
            if args.rpm
            else default_rate_limiter(llm.name.split(":")[0])
        )
    )
    if limiter:
        eta = len(candidates) / limiter.rpm
        print(f"      rate limit: {limiter.rpm} req/min (~{eta:.0f} min for this run)")
    judgements, errors = judge_all(
        candidates, episodes, llm, show, workers=args.workers, limiter=limiter
    )
    return judgements, errors, notes, llm.name


def load_judgements(path: str) -> tuple[list[Judgement], list[str], list[str]]:
    """Rebuild Judgement objects from a previous run's judgements.json."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[Judgement] = []
    for d in raw:
        cand = Candidate(**d["candidate"])
        fields = {k: v for k, v in d.items() if k not in ("candidate", "duration")}
        out.append(Judgement(candidate=cand, **fields))
    return out, [], [f"reused judgements from {path}"]


def finish(args, episodes, candidates, judgements, errors, notes, promos,
           llm_name, show, in_dir, out_dir, warnings, n_beats, t0):
    elapsed = round(time.time() - t0, 2)
    kept = sum(1 for j in judgements if j.keep)
    meta = {
        "show": show,
        "input_dir": str(in_dir),
        "n_episodes": len(episodes),
        "n_beats": n_beats,
        "n_candidates": len(candidates),
        "n_judged": len(judgements),
        "n_kept": kept,
        "model": llm_name,
        "elapsed_s": elapsed,
    }
    report = {
        **meta,
        "parse_warnings": warnings,
        "llm_notes": notes,
        "judge_errors": errors,
        "boundary_flags": [
            {"episode": j.candidate.episode, "start": j.start_beat, "flags": j.flags}
            for j in judgements
            if j.flags
        ],
        "rejects": [
            {
                "episode": j.candidate.episode,
                "start": j.start_beat,
                "reason": j.reject_reason,
            }
            for j in judgements
            if not j.keep
        ],
    }

    print(f"[5/5] writing to {out_dir} ...")
    write_json(out_dir / "promos.json", promos, meta)
    write_csv(out_dir / "promos.csv", promos)
    write_markdown(out_dir / "promos.md", promos, meta)
    write_judgements(out_dir / "judgements.json", judgements)
    write_report(out_dir / "run_report.json", report)
    print(f"done in {elapsed}s -> {out_dir}/promos.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
