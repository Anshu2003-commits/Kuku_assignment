"""Heuristic candidate generation - pure code, no LLM.

This stage exists for *recall and cost control*, not final judgement. It turns
~3,700 beats into ~4 windows per episode so the LLM spend per show is bounded
and predictable. Getting a mediocre promo into the candidate set is fine; the
judge throws it out. Missing a great one is not, so the lexicons lean generous.

Every knob lives in SIGNALS so it is tunable without touching logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parse import Beat, Episode

MIN_PROMO_S = 20.0
MAX_PROMO_S = 60.0
HOOK_WINDOW_S = 3.0  # a hook must land inside the first 3s of the clip

SIGNALS: dict[str, dict] = {
    # Physical/visual shocks in ACTION, SOUND FX and CAMERA prose.
    "hook_action": {
        "weight": 3.0,
        "patterns": [
            r"\bslap(s|ped|ping)?\b", r"\bstrik(e|es|ing)\b", r"\bstruck\b",
            r"\bpunch(es|ed)?\b", r"\bshove(s|d)?\b", r"\bgrab(s|bed|bing)?\b",
            r"\bcrack(s|ed)?\b", r"\bwhip\b", r"\bcrash(es|ed|ing)?\b",
            r"\bsmash(es|ed|ing)?\b", r"\bshatter(s|ed|ing)?\b",
            r"\bcollapse(s|d)?\b", r"\bfalls? to (her|his) knees\b",
            r"\bscream(s|ed|ing)?\b", r"\bgasp(s|ed|ing)?\b", r"\bshriek\b",
            r"\bblood\b", r"\bblade\b", r"\bsword\b", r"\bdagger\b", r"\bgun\b",
            r"\bdraws? (a|his|her) (blade|sword|dagger|pistol)\b",
            r"\bslams?\b", r"\bkicks? (open|down)\b", r"\bdrags?\b",
            r"\bthrows? (her|him|it)\b", r"\bchok(e|es|ing)\b",
            r"\bstabs?\b", r"\bexplo(de|des|sion)\b", r"\bgunshot\b",
            # NOT a bare \bfires?\b - in a period drama "hearth fire" is
            # everywhere, and it scored a cosy bandaging scene as violence.
            r"\bfires? (a|his|her|the) (gun|pistol|musket|shot)\b",
        ],
    },
    # Dialogue delivered at high arousal - the parenthetical emotion tags.
    "hook_emotion": {
        "weight": 2.5,
        "patterns": [
            r"outrag", r"shock", r"gloat", r"furious", r"fury", r"venom",
            r"horrifi", r"mock", r"accus", r"scream", r"bark", r"snarl",
            r"vicious", r"cruel", r"savage", r"seething", r"enraged",
            r"contempt", r"triumph", r"malice", r"menac", r"threat",
            r"desperate", r"betrayed", r"devastat", r"piercing", r"commanding",
        ],
    },
    # Lines that work as a cold open to a stranger - confrontation and reveal.
    "hook_line": {
        "weight": 2.5,
        "patterns": [
            r"\byou knew\b", r"\bhow long\b", r"\bwho are you\b",
            r"\bwhat are you\b", r"\bshe'?s alive\b", r"\bhe'?s alive\b",
            r"\bi'?m your\b", r"\byou'?re my\b", r"\bmarry me\b",
            r"\byou swore\b", r"\byou promised\b", r"\byou lied\b",
            r"\bi changed my mind\b", r"\bnot anymore\b", r"\bget out\b",
            r"\bhow dare you\b", r"\bi'?ll kill\b", r"\bkneel\b",
            r"\byou let me\b", r"\bit was you\b", r"\bi am the\b",
            r"\bdo you know who\b", r"\bmy (wife|husband|son|daughter)\b",
            r"\bduke\b", r"\bbastard\b", r"\bwhore\b", r"\bdivorce\b",
            r"\bpregnan", r"\bdead\b", r"\bdie\b", r"\bkill(ed|ing)?\b",
        ],
    },
    # Cues the script itself gives that a beat is an ending.
    "cliff_sfx": {
        "weight": 4.0,
        "patterns": [
            r"cliffhanger", r"abrupt(ly)? cut", r"sudden silence",
            r"cuts? to black", r"smash cut", r"hard cut",
            r"ominous", r"sting(er)?\b", r"swell(s|ing)?\b",
            r"drone", r"tension", r"suspense", r"freeze",
        ],
    },
    # Visual endings in CAMERA prose.
    "cliff_camera": {
        "weight": 3.0,
        "patterns": [
            r"cut to black", r"smash cut", r"hard cut", r"freeze frame",
            r"holds? on", r"push-?in", r"slow(ly)? (zoom|push)",
            r"lingers?", r"turns? to (face|look)", r"steps? into (the )?(light|frame|view)",
            r"reveal(s|ing|ed)?", r"widen(s|ing)?", r"eyes widen",
        ],
    },
    # A question as the last spoken line leaves the viewer hanging.
    "cliff_question": {"weight": 2.0, "patterns": [r"\?\s*\"?\s*$"]},
}

# Structural bonuses.
BONUS_SCENE_OPEN_S = 15.0   # beats near a scene opening read as natural starts
BONUS_SCENE_OPEN = 1.0
BONUS_EPISODE_END_S = 12.0  # beats near the episode end are built to hang
BONUS_EPISODE_END = 2.5
BONUS_AFTER_SFX = 1.0       # dialogue right after a sound sting

DIALOGUE_DENSITY_WEIGHT = 2.0  # a promo with no dialogue rarely stands alone
NMS_IOU = 0.6
TOP_PER_EPISODE = 4

_COMPILED = {
    name: [re.compile(p, re.I) for p in spec["patterns"]]
    for name, spec in SIGNALS.items()
}


def _hits(patterns: list[re.Pattern], text: str) -> int:
    return sum(1 for p in patterns if p.search(text))


def score_beat(
    beat: Beat, episode: Episode, prev: Beat | None
) -> tuple[float, float, float]:
    """Return (hook_content, hook_structural, cliff_score) for one beat.

    Content and structure are kept apart on purpose. Position in the episode
    is weak evidence: a clip that opens on a camera move onto a doorknob
    scores on the scene-open bonus alone and is not a hook at all. Window
    generation therefore requires CONTENT evidence, and uses the structural
    bonus only to break ties between beats that already qualify.
    """
    text = beat.text
    emo_text = " ".join(beat.emotions)
    hook = 0.0
    structural = 0.0
    cliff = 0.0

    if beat.kind in {"ACTION", "SOUND FX", "CAMERA"}:
        hook += SIGNALS["hook_action"]["weight"] * _hits(_COMPILED["hook_action"], text)
    if beat.kind == "DIALOGUE":
        hook += SIGNALS["hook_action"]["weight"] * 0.5 * _hits(
            _COMPILED["hook_action"], text
        )
        hook += SIGNALS["hook_emotion"]["weight"] * _hits(
            _COMPILED["hook_emotion"], emo_text
        )
        hook += SIGNALS["hook_line"]["weight"] * _hits(_COMPILED["hook_line"], text)
        # Short, punchy lines cut through on a muted autoplay feed - but a
        # one-word line ("Nothing.") tells a cold viewer nothing at all.
        words = len(re.findall(r"\w+", text))
        if 2 <= words <= 6:
            hook += 1.0

    # Natural entry point: near the top of a scene.
    scene = next((s for s in episode.scenes if s.number == beat.scene), None)
    if scene and beat.start_s - scene.start_s <= BONUS_SCENE_OPEN_S:
        structural += BONUS_SCENE_OPEN
    if prev is not None and prev.kind == "SOUND FX" and beat.kind == "DIALOGUE":
        structural += BONUS_AFTER_SFX

    if beat.kind == "SOUND FX":
        cliff += SIGNALS["cliff_sfx"]["weight"] * _hits(_COMPILED["cliff_sfx"], text)
    if beat.kind == "CAMERA":
        cliff += SIGNALS["cliff_camera"]["weight"] * _hits(
            _COMPILED["cliff_camera"], text
        )
    if beat.kind == "DIALOGUE":
        cliff += SIGNALS["cliff_question"]["weight"] * _hits(
            _COMPILED["cliff_question"], text
        )
        cliff += SIGNALS["hook_line"]["weight"] * 0.4 * _hits(
            _COMPILED["hook_line"], text
        )

    if episode.duration - beat.end_s <= BONUS_EPISODE_END_S:
        cliff += BONUS_EPISODE_END

    return hook, structural, cliff


@dataclass
class Candidate:
    episode: int
    start_beat: str
    end_beat: str
    start_s: float
    end_s: float
    beat_ids: list[str]
    hook_score: float
    cliff_score: float
    heuristic_score: float
    signals: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s


def _iou(a: Candidate, b: Candidate) -> float:
    lo = max(a.start_s, b.start_s)
    hi = min(a.end_s, b.end_s)
    inter = max(0.0, hi - lo)
    union = (a.duration + b.duration) - inter
    return inter / union if union > 0 else 0.0


def generate_candidates(
    episode: Episode, top_n: int = TOP_PER_EPISODE
) -> list[Candidate]:
    """Slide 20-60s windows over an episode and keep the most promising few."""
    beats = episode.beats
    if not beats:
        return []

    scores: dict[str, tuple[float, float, float]] = {}
    for i, beat in enumerate(beats):
        scores[beat.id] = score_beat(beat, episode, beats[i - 1] if i else None)

    windows: list[Candidate] = []
    for i, start in enumerate(beats):
        # The hook has to land in the first 3s, so look only inside that window.
        hook_cap = start.start_s + HOOK_WINDOW_S
        head = [b for b in beats[i:] if b.start_s < hook_cap]
        # Require CONTENT evidence in the opening 3 seconds. Position alone
        # (the scene-open bonus) is not a hook: it lets a clip that opens on a
        # camera move onto a closed door into the shortlist.
        hook_content = max((scores[b.id][0] for b in head), default=0.0)
        if hook_content <= 0:
            continue
        hook_score = hook_content + max(
            (scores[b.id][1] for b in head), default=0.0
        )

        # CAMERA and DIALOGUE are parallel tracks, so adjacent beats can step
        # back by up to ~1s in this pack. Track the running extent rather than
        # trusting positional order, or the break below can fire early.
        first_beat = start  # beat owning the window's in-point
        last_beat = start  # beat owning the window's out-point
        for j in range(i + 1, len(beats)):
            end = beats[j]
            if end.start_s < first_beat.start_s:
                first_beat = end
            if end.end_s > last_beat.end_s:
                last_beat = end
            win_start, win_end = first_beat.start_s, last_beat.end_s
            dur = win_end - win_start
            if dur < MIN_PROMO_S:
                continue
            if dur > MAX_PROMO_S:
                break

            inner = beats[i : j + 1]
            cliff_score = max(scores[b.id][2] for b in inner[-3:])
            dialogue = sum(1 for b in inner if b.kind == "DIALOGUE")
            density = dialogue / max(1, len(inner))
            body = sum(scores[b.id][0] for b in inner) / max(1, len(inner))

            total = (
                hook_score
                + cliff_score
                + body
                + DIALOGUE_DENSITY_WEIGHT * density
            )
            windows.append(
                Candidate(
                    episode=episode.number,
                    start_beat=first_beat.id,
                    end_beat=last_beat.id,
                    start_s=win_start,
                    end_s=win_end,
                    beat_ids=[b.id for b in inner],
                    hook_score=round(hook_score, 3),
                    cliff_score=round(cliff_score, 3),
                    heuristic_score=round(total, 3),
                    signals={
                        "hook_content": round(hook_content, 3),
                        "dialogue_density": round(density, 3),
                        "mean_body_hook": round(body, 3),
                        "n_beats": len(inner),
                    },
                )
            )

    windows.sort(key=lambda c: c.heuristic_score, reverse=True)

    # Non-max suppression so the shortlist is not one moment eight ways.
    kept: list[Candidate] = []
    for cand in windows:
        if all(_iou(cand, k) < NMS_IOU for k in kept):
            kept.append(cand)
        if len(kept) >= top_n:
            break
    return kept


def generate_all(episodes: list[Episode], top_n: int = TOP_PER_EPISODE) -> list[Candidate]:
    out: list[Candidate] = []
    for ep in episodes:
        out.extend(generate_candidates(ep, top_n=top_n))
    return out
