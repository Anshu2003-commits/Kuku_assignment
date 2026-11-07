"""Deterministic screenplay parser.

Turns a Kuku microdrama screenplay .txt into typed beats carrying exact
episode-relative timestamps. Nothing here is probabilistic: the LLM downstream
only ever selects *beat ids*, and this module is the sole source of truth for
what time those ids correspond to.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- line grammar -----------------------------------------------------------

RE_EP_NUM = re.compile(r"^EPISODE NUMBER:\s*(?:Episode\s*)?(\d+)\s*$", re.I)
RE_EP_LOG = re.compile(r"^EPISODE LOGLINE:\s*(.+)$", re.I)
RE_EP_CONT = re.compile(r"^CONTINUITY NOTES:\s*(.+)$", re.I)
RE_SCRIPT_START = re.compile(r"^---\s*SCRIPT START\s*---\s*$", re.I)
RE_SCRIPT_END = re.compile(r"^---\s*SCRIPT END\s*---\s*$", re.I)
RE_SCENE = re.compile(r"^SCENE\s+(\d+)\s*$", re.I)
RE_SCENE_HEAD = re.compile(r"^Scene heading:\s*(.+)$", re.I)
RE_SCENE_TIME = re.compile(r"^Scene (start|end):\s*(\d+:\d+(?:\.\d+)?)\s*$", re.I)
RE_BEAT_HEAD = re.compile(r"^\*\*(.+?):\*\*\s*$")
RE_BEAT_TIME = re.compile(
    r"^Start:\s*\((\d+:\d+(?:\.\d+)?)\)\s*\|\s*End:\s*\((\d+:\d+(?:\.\d+)?)\)\s*$",
    re.I,
)

NON_DIALOGUE_TYPES = {"CAMERA", "ACTION", "SOUND FX", "SFX", "MUSIC", "VFX", "TEXT"}
# Delivery modifiers that are not emotions.
MODIFIERS = {"O.S.", "OS", "V.O.", "VO", "CONT'D", "CONTD", "OFF"}


def parse_timestamp(ts: str) -> float:
    """'02:18.00' -> 138.0 seconds. Also tolerates 'H:MM:SS.ss'."""
    parts = ts.split(":")
    if len(parts) == 2:
        mins, secs = parts
        return int(mins) * 60 + float(secs)
    if len(parts) == 3:
        hrs, mins, secs = parts
        return int(hrs) * 3600 + int(mins) * 60 + float(secs)
    raise ValueError(f"unparseable timestamp: {ts!r}")


def format_timestamp(seconds: float) -> str:
    """138.0 -> '02:18.00'."""
    mins = int(seconds // 60)
    rem = seconds - mins * 60
    return f"{mins:02d}:{rem:05.2f}"


def split_speaker_label(label: str) -> tuple[str, str, list[str], list[str]]:
    """Split a beat header label into (kind, speaker, emotions, modifiers).

    'CAMERA'                   -> ('CAMERA', '', [], [])
    'LADY JULIANA (Desperate)' -> ('DIALOGUE', 'LADY JULIANA', ['Desperate'], [])
    'MATRIARCH (O.S.) (Firm)'  -> ('DIALOGUE', 'MATRIARCH', ['Firm'], ['O.S.'])
    """
    groups = re.findall(r"\(([^()]*)\)", label)
    base = re.sub(r"\(([^()]*)\)", "", label).strip(" -")
    base = re.sub(r"\s+", " ", base).strip()

    normalised_modifiers = {m.replace(" ", "").upper() for m in MODIFIERS}
    modifiers: list[str] = []
    emotions: list[str] = []
    for group in groups:
        group = group.strip()
        if not group:
            continue
        if group.replace(" ", "").upper() in normalised_modifiers:
            modifiers.append(group)
        else:
            emotions.extend(e.strip() for e in group.split(",") if e.strip())

    upper = base.upper()
    if upper in NON_DIALOGUE_TYPES:
        return upper, "", emotions, modifiers
    return "DIALOGUE", base, emotions, modifiers


@dataclass
class Beat:
    id: str
    episode: int
    scene: int
    index: int  # 0-based position within the episode
    kind: str  # CAMERA | ACTION | SOUND FX | DIALOGUE | ...
    speaker: str
    emotions: list[str]
    modifiers: list[str]
    start_s: float
    end_s: float
    text: str

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s

    def render(self) -> str:
        """One-line form shown to the LLM. Carries the id so it can cite it."""
        span = f"({format_timestamp(self.start_s)}-{format_timestamp(self.end_s)})"
        if self.kind == "DIALOGUE":
            emo = f" ({', '.join(self.emotions)})" if self.emotions else ""
            return f"[{self.id}] {span} {self.speaker}{emo}: {self.text}"
        return f"[{self.id}] {span} {self.kind}: {self.text}"


@dataclass
class Scene:
    number: int
    heading: str
    start_s: float
    end_s: float


@dataclass
class Episode:
    number: int
    title: str
    source_file: str
    logline: str
    continuity: str
    scenes: list[Scene] = field(default_factory=list)
    beats: list[Beat] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.scenes:
            return max(s.end_s for s in self.scenes)
        return max((b.end_s for b in self.beats), default=0.0)

    def beat_by_id(self, beat_id: str) -> Beat | None:
        for beat in self.beats:
            if beat.id == beat_id:
                return beat
        return None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["duration"] = self.duration
        return data


def _episode_number_from_name(path: Path) -> int | None:
    m = re.search(r"episode[-_ ]?(\d+)", path.stem, re.I)
    return int(m.group(1)) if m else None


def parse_episode(path: str | Path) -> Episode:
    """Parse one screenplay file. Unrecognised in-script lines become warnings."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    ep_num = _episode_number_from_name(path)
    ep = Episode(
        number=ep_num if ep_num is not None else -1,
        title=path.stem,
        source_file=path.name,
        logline="",
        continuity="",
    )

    in_script = False
    cur_scene = 0
    scene_heading = ""
    scene_start: float | None = None

    pending: dict | None = None  # beat being assembled
    body: list[str] = []

    def flush() -> None:
        nonlocal pending, body
        if pending is None:
            return
        text = " ".join(line.strip() for line in body if line.strip()).strip()
        idx = len(ep.beats)
        ep.beats.append(
            Beat(
                id=f"E{ep.number:02d}.B{idx:03d}",
                episode=ep.number,
                scene=pending["scene"],
                index=idx,
                kind=pending["kind"],
                speaker=pending["speaker"],
                emotions=pending["emotions"],
                modifiers=pending["modifiers"],
                start_s=pending["start_s"],
                end_s=pending["end_s"],
                text=text,
            )
        )
        pending, body = None, []

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        i += 1

        if not stripped:
            continue

        if not in_script:
            if m := RE_EP_NUM.match(stripped):
                if ep.number == -1:
                    ep.number = int(m.group(1))
            elif m := RE_EP_LOG.match(stripped):
                ep.logline = m.group(1).strip()
            elif m := RE_EP_CONT.match(stripped):
                ep.continuity = m.group(1).strip()
            elif RE_SCRIPT_START.match(stripped):
                in_script = True
            continue

        if RE_SCRIPT_END.match(stripped):
            flush()
            in_script = False
            continue

        if m := RE_SCENE.match(stripped):
            flush()
            cur_scene = int(m.group(1))
            scene_heading, scene_start = "", None
            continue

        if m := RE_SCENE_HEAD.match(stripped):
            scene_heading = m.group(1).strip()
            continue

        if m := RE_SCENE_TIME.match(stripped):
            which, ts = m.group(1).lower(), parse_timestamp(m.group(2))
            if which == "start":
                scene_start = ts
            else:
                ep.scenes.append(Scene(cur_scene, scene_heading, scene_start or 0.0, ts))
            continue

        if m := RE_BEAT_HEAD.match(stripped):
            flush()
            kind, speaker, emotions, modifiers = split_speaker_label(m.group(1))
            # The Start/End line must follow, allowing blank lines between.
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            tm = RE_BEAT_TIME.match(lines[j].strip()) if j < len(lines) else None
            if tm is None:
                ep.warnings.append(
                    f"{path.name}: beat header {stripped!r} has no Start/End line"
                )
                continue
            pending = {
                "scene": cur_scene,
                "kind": kind,
                "speaker": speaker,
                "emotions": emotions,
                "modifiers": modifiers,
                "start_s": parse_timestamp(tm.group(1)),
                "end_s": parse_timestamp(tm.group(2)),
            }
            i = j + 1
            continue

        if RE_BEAT_TIME.match(stripped):
            ep.warnings.append(f"{path.name}: orphan timestamp line: {stripped!r}")
            continue

        if pending is not None:
            body.append(stripped)
            continue

        if stripped.lower().startswith("actions and dialogue"):
            continue

        ep.warnings.append(f"{path.name}: unrecognised line: {stripped[:80]!r}")

    flush()
    _validate(ep)
    return ep


def _validate(ep: Episode) -> None:
    if not ep.beats:
        ep.warnings.append(f"{ep.source_file}: no beats parsed")
        return
    for beat in ep.beats:
        if beat.end_s < beat.start_s:
            ep.warnings.append(f"{beat.id}: end before start")
        if not beat.text:
            ep.warnings.append(f"{beat.id}: empty body text")
    # Scenes should chain: scene N+1 starts where scene N ended.
    for a, b in zip(ep.scenes, ep.scenes[1:]):
        if abs(a.end_s - b.start_s) > 0.01:
            ep.warnings.append(
                f"{ep.source_file}: scene {a.number}->{b.number} gap "
                f"{a.end_s:.2f}->{b.start_s:.2f}"
            )


def filter_episodes(episodes: list[Episode], spec: str | None) -> list[Episode]:
    """Select a subset of episodes from a spec like '40-50' or '1,2,3'.

    An empty or missing spec returns the list unchanged. Shared by the CLI
    (`--episodes`) and the Streamlit app so the two cannot drift apart.
    """
    if not spec or not spec.strip():
        return episodes
    wanted: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part.lstrip("-"):
            lo, _, hi = part.partition("-")
            wanted.update(range(int(lo), int(hi) + 1))
        else:
            wanted.add(int(part))
    return [e for e in episodes if e.number in wanted]


def parse_pack(input_dir: str | Path, pattern: str = "*.txt") -> list[Episode]:
    """Parse a folder of episode .txt files, sorted by episode number."""
    input_dir = Path(input_dir)
    files = sorted(input_dir.glob(pattern))
    if not files:
        files = sorted(input_dir.rglob(pattern))
    episodes = [parse_episode(f) for f in files]
    episodes.sort(key=lambda e: (e.number if e.number > 0 else 10_000, e.source_file))
    return episodes
