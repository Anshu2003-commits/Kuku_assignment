"""Output writers: machine JSON, an editor-facing CSV, and a readable brief."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .judge import Judgement
from .rank import Promo, post_order_rationale


def write_json(path: Path, promos: list[Promo], meta: dict) -> None:
    payload = {"meta": meta, "promos": [p.to_dict() for p in promos]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_csv(path: Path, promos: list[Promo]) -> None:
    """The artefact an editor actually opens: one row per promo."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "rank", "kind", "episodes", "timecodes", "duration_s",
                "hook", "cliffhanger", "why_it_works",
                "hook_strength", "cliffhanger_strength", "standalone",
                "spoiler_risk", "score", "flags",
            ]
        )
        for p in promos:
            w.writerow(
                [
                    p.rank,
                    p.kind,
                    " + ".join(f"Ep{e:02d}" for e in p.episodes),
                    " | ".join(s.label() for s in p.segments),
                    f"{p.duration:.2f}",
                    p.hook,
                    p.cliffhanger,
                    p.why_it_works,
                    p.hook_strength,
                    p.cliffhanger_strength,
                    p.standalone,
                    p.spoiler_risk,
                    f"{p.slate_score:.3f}",
                    ";".join(p.flags),
                ]
            )


def write_markdown(path: Path, promos: list[Promo], meta: dict, top: int = 10) -> None:
    out: list[str] = []
    out.append(f"# Promo shortlist - {meta.get('show', 'unknown show')}\n")
    out.append(
        f"{meta.get('n_episodes', 0)} episodes, {meta.get('n_beats', 0)} beats, "
        f"{meta.get('n_candidates', 0)} candidates judged by "
        f"`{meta.get('model', 'unknown')}`.\n"
    )

    out.append("\n## Post these first\n")
    for line in post_order_rationale(promos[:top]):
        out.append(f"- {line}")

    out.append("\n## Full ranked list\n")
    for p in promos:
        segs = ", ".join(s.label() for s in p.segments)
        tag = " [multi-segment]" if p.kind == "stitch" else ""
        out.append(f"\n### #{p.rank} - {segs} ({p.duration:.0f}s){tag}\n")
        out.append(f"- **Hook:** {p.hook}")
        out.append(f"- **Cliffhanger:** {p.cliffhanger}")
        out.append(f"- **Why it works:** {p.why_it_works}")
        out.append(
            f"- **Scores:** hook {p.hook_strength}/5 - cliff "
            f"{p.cliffhanger_strength}/5 - standalone {p.standalone}/5 - "
            f"spoiler risk {p.spoiler_risk}/5 - **slate {p.slate_score:.2f}**"
        )
        if p.flags:
            out.append(f"- **Flags:** {', '.join(p.flags)}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def write_judgements(path: Path, judgements: list[Judgement]) -> None:
    """Every judgement including rejects - the honest, unfiltered record."""
    path.write_text(
        json.dumps([j.to_dict() for j in judgements], indent=2), encoding="utf-8"
    )


def write_report(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
