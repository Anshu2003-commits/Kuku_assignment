"""Ranking and selection - code, not AI.

The judge scores each clip in isolation. Picking a *slate* is a different job:
it has to avoid near-duplicates, avoid five promos from the same scene, and
weigh spoiler risk. That is deterministic policy, so it lives here where it can
be read and argued with, rather than inside a prompt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .judge import Judgement
from .parse import Episode, format_timestamp

WEIGHTS = {
    "hook": 0.35,
    "cliffhanger": 0.30,
    "standalone": 0.25,
    "spoiler": -0.10,
}
# Each additional promo from the same episode is worth less on the slate.
SAME_EPISODE_DECAY = 0.55
OVERLAP_IOU = 0.5


@dataclass
class Segment:
    episode: int
    start_s: float
    end_s: float
    start_beat: str
    end_beat: str

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s

    def label(self) -> str:
        return (
            f"Ep {self.episode:02d} "
            f"{format_timestamp(self.start_s)}-{format_timestamp(self.end_s)}"
        )


@dataclass
class Promo:
    rank: int
    kind: str  # "single" | "stitch"
    segments: list[Segment]
    hook: str
    cliffhanger: str
    why_it_works: str
    hook_strength: int
    cliffhanger_strength: int
    standalone: int
    spoiler_risk: int
    base_score: float
    slate_score: float
    judged_by: str
    flags: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    @property
    def episodes(self) -> list[int]:
        return sorted({s.episode for s in self.segments})

    def to_dict(self) -> dict:
        data = asdict(self)
        data["duration"] = round(self.duration, 2)
        data["episodes"] = self.episodes
        data["timecodes"] = [s.label() for s in self.segments]
        return data


def base_score(j: Judgement) -> float:
    return round(
        WEIGHTS["hook"] * j.hook_strength
        + WEIGHTS["cliffhanger"] * j.cliffhanger_strength
        + WEIGHTS["standalone"] * j.standalone
        + WEIGHTS["spoiler"] * j.spoiler_risk,
        4,
    )


def _iou(a: Judgement, b: Judgement) -> float:
    if a.candidate.episode != b.candidate.episode:
        return 0.0
    lo, hi = max(a.start_s, b.start_s), min(a.end_s, b.end_s)
    inter = max(0.0, hi - lo)
    union = a.duration + b.duration - inter
    return inter / union if union > 0 else 0.0


def _to_promo(j: Judgement, rank: int, slate: float) -> Promo:
    return Promo(
        rank=rank,
        kind="single",
        segments=[
            Segment(
                episode=j.candidate.episode,
                start_s=j.start_s,
                end_s=j.end_s,
                start_beat=j.start_beat,
                end_beat=j.end_beat,
            )
        ],
        hook=j.hook,
        cliffhanger=j.cliffhanger,
        why_it_works=j.why_it_works,
        hook_strength=j.hook_strength,
        cliffhanger_strength=j.cliffhanger_strength,
        standalone=j.standalone,
        spoiler_risk=j.spoiler_risk,
        base_score=base_score(j),
        slate_score=round(slate, 4),
        judged_by=j.judged_by,
        flags=list(j.flags),
    )


def rank_promos(judgements: list[Judgement], limit: int = 20) -> list[Promo]:
    """Greedy slate selection: best marginal value first."""
    pool = [j for j in judgements if j.keep]
    pool.sort(key=base_score, reverse=True)

    chosen: list[Judgement] = []
    per_episode: dict[int, int] = {}
    promos: list[Promo] = []

    for j in pool:
        if len(promos) >= limit:
            break
        if any(_iou(j, c) > OVERLAP_IOU for c in chosen):
            continue
        n_seen = per_episode.get(j.candidate.episode, 0)
        slate = base_score(j) * (SAME_EPISODE_DECAY**n_seen)
        chosen.append(j)
        per_episode[j.candidate.episode] = n_seen + 1
        promos.append(_to_promo(j, 0, slate))

    # Re-sort by slate score so the diversity penalty actually moves the order.
    promos.sort(key=lambda p: p.slate_score, reverse=True)
    for i, p in enumerate(promos, 1):
        p.rank = i
    return promos


def build_stitches(
    judgements: list[Judgement],
    max_stitches: int = 3,
) -> list[tuple[Judgement, Judgement]]:
    """Pair the strongest hooks with the strongest cliffhangers.

    The brief allows a promo to join a few moments. These are *proposals*:
    `build_stitch_promos` snaps them to beat boundaries and drops any whose
    combined length lands outside 20-60s.
    """
    pool = [j for j in judgements if j.keep]
    if len(pool) < 2:
        return []

    hooks = sorted(pool, key=lambda j: (j.hook_strength, j.standalone), reverse=True)
    cliffs = sorted(
        pool, key=lambda j: (j.cliffhanger_strength, -j.spoiler_risk), reverse=True
    )

    pairs: list[tuple[Judgement, Judgement]] = []
    used: set[tuple[int, str]] = set()
    for h in hooks:
        if len(pairs) >= max_stitches:
            break
        hkey = (h.candidate.episode, h.start_beat)
        if hkey in used:
            continue
        for c in cliffs:
            ckey = (c.candidate.episode, c.start_beat)
            if ckey in used or ckey == hkey:
                continue
            # Use the front of the hook clip and the tail of the cliff clip.
            if h.candidate.episode == c.candidate.episode and _iou(h, c) > 0.1:
                continue
            if c.candidate.episode < h.candidate.episode:
                continue
            pairs.append((h, c))
            used.add(hkey)
            used.add(ckey)
            break
    return pairs


def build_stitch_promos(
    judgements: list[Judgement],
    episodes: list[Episode],
    max_stitches: int = 3,
) -> list[Promo]:
    """Pair, snap to beat boundaries, then keep only legal-length results."""
    ep_by_num = {e.number: e for e in episodes}
    out: list[Promo] = []
    # Over-generate, since snapping can push a pair out of the legal range.
    for h, c in build_stitches(judgements, max_stitches=max_stitches * 3):
        if len(out) >= max_stitches:
            break
        promo = stitch_to_promo(h, c, rank=0, episodes=ep_by_num)
        if 20.0 <= promo.duration <= 60.0:
            out.append(promo)
    return out


SEGMENT_CAP_S = 25.0


def _head_segment(j: Judgement, ep: Episode, cap: float) -> Segment:
    """Front of a clip, trimmed to `cap` seconds but snapped to real beats."""
    beats = [
        b for b in ep.beats if j.start_s - 1e-6 <= b.start_s and b.end_s <= j.end_s + 1e-6
    ]
    kept = [b for b in beats if b.end_s - j.start_s <= cap] or beats[:1]
    last = max(kept, key=lambda b: b.end_s)
    return Segment(ep.number, j.start_s, last.end_s, j.start_beat, last.id)


def _tail_segment(j: Judgement, ep: Episode, cap: float) -> Segment:
    """Tail of a clip, trimmed to `cap` seconds but snapped to real beats."""
    beats = [
        b for b in ep.beats if j.start_s - 1e-6 <= b.start_s and b.end_s <= j.end_s + 1e-6
    ]
    kept = [b for b in beats if j.end_s - b.start_s <= cap] or beats[-1:]
    first = min(kept, key=lambda b: b.start_s)
    return Segment(ep.number, first.start_s, j.end_s, first.id, j.end_beat)


def stitch_to_promo(
    h: Judgement, c: Judgement, rank: int, episodes: dict[int, Episode]
) -> Promo:
    """Assemble a two-segment promo from a hook clip and a cliffhanger clip.

    Both cuts snap to beat boundaries - the same rule single-segment promos
    follow. `eval/verify_outputs.py` enforces this.
    """
    segments = [
        _head_segment(h, episodes[h.candidate.episode], SEGMENT_CAP_S),
        _tail_segment(c, episodes[c.candidate.episode], SEGMENT_CAP_S),
    ]
    score = round(
        WEIGHTS["hook"] * h.hook_strength
        + WEIGHTS["cliffhanger"] * c.cliffhanger_strength
        + WEIGHTS["standalone"] * min(h.standalone, c.standalone)
        + WEIGHTS["spoiler"] * max(h.spoiler_risk, c.spoiler_risk),
        4,
    )
    return Promo(
        rank=rank,
        kind="stitch",
        segments=segments,
        hook=h.hook,
        cliffhanger=c.cliffhanger,
        why_it_works=(
            f"Opens on the strongest hook in the show and cuts to the hardest "
            f"unresolved beat: {c.cliffhanger}"
        ),
        hook_strength=h.hook_strength,
        cliffhanger_strength=c.cliffhanger_strength,
        standalone=min(h.standalone, c.standalone),
        spoiler_risk=max(h.spoiler_risk, c.spoiler_risk),
        base_score=score,
        slate_score=score,
        judged_by=h.judged_by,
        flags=["stitch_unverified_continuity"],
    )


SLATE_CONTAINMENT = 0.6


def _containment(a: Segment, b: Segment) -> float:
    """Overlap as a fraction of the SHORTER segment.

    IoU is the wrong measure for a posting slate: a 24s clip fully inside a
    54s clip scores only 0.45 IoU, but to a viewer the two posts open on the
    identical footage. Containment catches that; IoU does not.
    """
    if a.episode != b.episode:
        return 0.0
    lo, hi = max(a.start_s, b.start_s), min(a.end_s, b.end_s)
    inter = max(0.0, hi - lo)
    shorter = min(a.duration, b.duration)
    return inter / shorter if shorter > 0 else 0.0


def dedupe_slate(promos: list[Promo], threshold: float = SLATE_CONTAINMENT) -> list[Promo]:
    """Drop promos that re-use footage already in a higher-ranked promo.

    `rank_promos` dedupes singles against each other, but a stitch is built
    from clips that may already be on the slate - without this the #1 promo's
    first segment can be literally identical to #4. Runs over the merged slate
    so singles and stitches are compared on the same footing.
    """
    kept: list[Promo] = []
    for promo in sorted(promos, key=lambda p: p.slate_score, reverse=True):
        clash = any(
            _containment(seg, other_seg) > threshold
            for seg in promo.segments
            for other in kept
            for other_seg in other.segments
        )
        if not clash:
            kept.append(promo)
    for i, promo in enumerate(kept, 1):
        promo.rank = i
    return kept


def post_order_rationale(promos: list[Promo]) -> list[str]:
    """One line per top promo explaining its place in the posting order.

    Leads with the hook, because that is what an editor actually scans for.
    Only caveats worth acting on are appended - listing the strengths every
    promo shares ("hook lands instantly") on all ten rows tells nobody
    anything.
    """
    lines: list[str] = []
    for p in promos:
        caveats = []
        if p.spoiler_risk >= 4:
            caveats.append("spoiler-heavy, post later in the campaign")
        if min(p.episodes) <= 5:
            caveats.append("early-episode, safest for cold audiences")
        if p.kind == "stitch":
            caveats.append("multi-segment - confirm it cuts cleanly")
        if p.standalone <= 3:
            caveats.append("may not land without context")
        if p.cliffhanger_strength <= 3:
            caveats.append("soft ending")
        tail = f" [{'; '.join(caveats)}]" if caveats else ""
        hook = p.hook or "(no hook recorded)"
        lines.append(
            f"**#{p.rank}** {', '.join(s.label() for s in p.segments)} "
            f"({p.duration:.0f}s, score {p.slate_score:.2f}) - {hook}{tail}"
        )
    return lines
