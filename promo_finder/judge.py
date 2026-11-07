"""The AI step: judge one candidate window as a promo.

Hard rule enforced here - the model never emits a timestamp. It selects beat
ids, and `parse.py` is the only thing that knows what time an id maps to. Any
id the model invents fails validation and we fall back to the heuristic bounds.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

from .candidates import MAX_PROMO_S, MIN_PROMO_S, Candidate
from .llm import LLM, LLMError, RateLimiter, complete_with_retry, extract_json
from .parse import Episode

SYSTEM = """\
You are a social-media promo editor for vertical microdramas (ReelShort / \
DramaBox style). You judge whether a proposed clip would stop a stranger \
mid-scroll on TikTok, Instagram Reels or YouTube Shorts.

A good promo:
- HOOK: something in the FIRST 3 SECONDS that makes a scrolling viewer stop - \
a slap, a reveal, a shocking line. Beautiful scenery or slow build is not a hook.
- CLIFFHANGER: it cuts at an unresolved moment, so the viewer must know what \
happens next. Do not end on a resolution or a calm beat.
- STANDALONE: it makes sense to someone who has never seen the show. If the \
clip depends on knowing who people are, it is weak no matter how dramatic.

You are strict. Most candidates are mediocre; say so. A show has at most a \
handful of genuinely strong promos.

Return ONLY a JSON object, no prose, no code fences."""

USER_TEMPLATE = """\
SHOW: {show}
EPISODE {episode} LOGLINE: {logline}

LEAD-IN (context only - NOT part of the clip, do not select these ids):
{context}

PROPOSED CLIP ({duration:.1f}s, {n_beats} beats):
{clip}

Judge this clip. You may TIGHTEN the clip by choosing a later start_beat or an
earlier end_beat from the PROPOSED CLIP ids above, but the result must stay
between {min_s:.0f} and {max_s:.0f} seconds. Never invent a beat id.

Return exactly this JSON shape:
{{
  "keep": true or false,
  "reject_reason": null or a short string,
  "start_beat": "<beat id from the clip>",
  "end_beat": "<beat id from the clip>",
  "hook": "one sentence - what happens in the first 3 seconds",
  "cliffhanger": "one sentence - the unresolved note it cuts on",
  "why_it_works": "one sentence - why a stranger keeps watching",
  "hook_strength": 1-5,
  "cliffhanger_strength": 1-5,
  "standalone": 1-5,
  "spoiler_risk": 1-5
}}"""

CONTEXT_BEATS = 3
INT_FIELDS = ("hook_strength", "cliffhanger_strength", "standalone", "spoiler_risk")


@dataclass
class Judgement:
    candidate: Candidate
    keep: bool
    reject_reason: str | None
    start_beat: str
    end_beat: str
    start_s: float
    end_s: float
    hook: str
    cliffhanger: str
    why_it_works: str
    hook_strength: int
    cliffhanger_strength: int
    standalone: int
    spoiler_risk: int
    judged_by: str
    flags: list[str]

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict:
        data = asdict(self)
        data["candidate"] = asdict(self.candidate)
        data["duration"] = round(self.duration, 2)
        return data


def build_prompt(cand: Candidate, episode: Episode, show: str) -> str:
    by_id = {b.id: b for b in episode.beats}
    clip_beats = [by_id[i] for i in cand.beat_ids if i in by_id]
    first_idx = clip_beats[0].index if clip_beats else 0
    ctx = episode.beats[max(0, first_idx - CONTEXT_BEATS) : first_idx]
    return USER_TEMPLATE.format(
        show=show,
        episode=episode.number,
        logline=episode.logline or "(none given)",
        context="\n".join(b.render() for b in ctx) or "(start of episode)",
        clip="\n".join(b.render() for b in clip_beats),
        duration=cand.duration,
        n_beats=len(clip_beats),
        min_s=MIN_PROMO_S,
        max_s=MAX_PROMO_S,
    )


def _coerce_int(value, default: int = 3) -> int:
    try:
        return max(1, min(5, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def validate(raw: dict, cand: Candidate, episode: Episode) -> Judgement:
    """Turn a model response into a Judgement, repairing what we safely can.

    Boundary ids are only trusted if they exist in THIS candidate and produce a
    legal duration. Otherwise we keep the heuristic window and flag it, so the
    run report shows honestly how often the model misbehaved.
    """
    flags: list[str] = []
    allowed = set(cand.beat_ids)
    by_id = {b.id: b for b in episode.beats}

    start_id = raw.get("start_beat")
    end_id = raw.get("end_beat")
    use_heuristic = False

    if start_id not in allowed or end_id not in allowed:
        flags.append("boundary_id_invalid")
        use_heuristic = True
    else:
        s_beat, e_beat = by_id[start_id], by_id[end_id]
        if s_beat.index >= e_beat.index:
            flags.append("boundary_order_invalid")
            use_heuristic = True
        else:
            dur = e_beat.end_s - s_beat.start_s
            if not (MIN_PROMO_S <= dur <= MAX_PROMO_S):
                flags.append(f"boundary_duration_{dur:.1f}s_out_of_range")
                use_heuristic = True

    if use_heuristic:
        start_id, end_id = cand.start_beat, cand.end_beat
    start_s = by_id[start_id].start_s
    end_s = by_id[end_id].end_s

    keep = bool(raw.get("keep", True))
    reason = raw.get("reject_reason")
    return Judgement(
        candidate=cand,
        keep=keep,
        reject_reason=str(reason) if reason else None,
        start_beat=start_id,
        end_beat=end_id,
        start_s=start_s,
        end_s=end_s,
        hook=str(raw.get("hook", "")).strip(),
        cliffhanger=str(raw.get("cliffhanger", "")).strip(),
        why_it_works=str(raw.get("why_it_works", "")).strip(),
        hook_strength=_coerce_int(raw.get("hook_strength")),
        cliffhanger_strength=_coerce_int(raw.get("cliffhanger_strength")),
        standalone=_coerce_int(raw.get("standalone")),
        spoiler_risk=_coerce_int(raw.get("spoiler_risk")),
        judged_by="",
        flags=flags,
    )


def judge_one(
    cand: Candidate,
    episode: Episode,
    llm: LLM,
    show: str,
    limiter: RateLimiter | None = None,
) -> tuple[Judgement | None, str | None]:
    """Judge one candidate. Returns (judgement, error). One repair retry."""
    user = build_prompt(cand, episode, show)
    try:
        text = complete_with_retry(llm, SYSTEM, user, limiter=limiter)
        raw = extract_json(text)
    except (LLMError, json.JSONDecodeError) as exc:
        try:
            text = complete_with_retry(
                llm,
                SYSTEM,
                user + "\n\nYour previous reply was not valid JSON. "
                "Reply with the JSON object only.",
                attempts=2,
                limiter=limiter,
            )
            raw = extract_json(text)
        except Exception as exc2:  # noqa: BLE001
            return None, f"{cand.episode}:{cand.start_beat}: {exc2 or exc}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{cand.episode}:{cand.start_beat}: {exc}"

    judgement = validate(raw, cand, episode)
    judgement.judged_by = llm.name
    return judgement, None


def judge_all(
    candidates: list[Candidate],
    episodes: list[Episode],
    llm: LLM,
    show: str,
    workers: int = 4,
    limiter: RateLimiter | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[Judgement], list[str]]:
    """Judge every candidate. Calls are independent, so run a small pool.

    Kept small on purpose: free tiers rate-limit aggressively and a 429 storm
    costs more wall time than it saves.

    `on_progress(done, total)` fires as each call lands, so a UI can show real
    progress on a run that takes ~16 minutes on the free tier. It is called
    from worker threads.

    Completion order is not submission order, so results are collected into
    indexed slots and flattened at the end: the output order must stay
    deterministic or `judgements.json` would reshuffle between identical runs.
    """
    ep_by_num = {e.number: e for e in episodes}

    def work(cand: Candidate):
        return judge_one(cand, ep_by_num[cand.episode], llm, show, limiter)

    total = len(candidates)
    slots: list[tuple[Judgement | None, str | None]] = [(None, None)] * total

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, c): i for i, c in enumerate(candidates)}
        for done, fut in enumerate(as_completed(futures), 1):
            idx = futures[fut]
            try:
                slots[idx] = fut.result()
            except Exception as exc:  # noqa: BLE001 - never lose the whole run
                slots[idx] = (None, f"{candidates[idx].start_beat}: {exc}")
            if on_progress is not None:
                on_progress(done, total)

    results: list[Judgement] = []
    errors: list[str] = []
    for judgement, error in slots:
        if error:
            errors.append(error)
        elif judgement is not None:
            results.append(judgement)
    return results, errors
