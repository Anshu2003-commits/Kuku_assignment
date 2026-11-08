"""Build the 2-page solution deck: deck.html -> promo-finder-solution-anshu.pdf.

Draws promo #1 as a beat strip (ids, kinds and times only - no script text,
since the input pack is confidential) and prints the page with Chromium.

    python deck/build_deck.py --scripts "<folder of episode .txt files>"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from promo_finder.parse import parse_episode  # noqa: E402

LANES = [("DIALOGUE", "dialogue", "#24508F"), ("ACTION", "action", "#B45309"),
         ("SOUND FX", "sound", "#7A4FA0"), ("CAMERA", "camera", "#9AA0A8")]


def strip_svg(beats: list[tuple[str, float, float, str]], dur: float) -> str:
    w, left, lane_h, top = 1000, 64, 15, 16
    plot = w - left - 8
    h = top + lane_h * len(LANES) + 22
    x = lambda t: left + plot * t / dur  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" font-family="IBM Plex Mono, monospace">']
    # hook zone and cut marker
    out.append(f'<rect x="{x(0):.1f}" y="{top-4}" width="{x(3)-x(0):.1f}" height="{lane_h*len(LANES)+8}" fill="#FBEEDD"/>')
    out.append(f'<text x="{x(0)+2:.1f}" y="{top-6}" font-size="10" fill="#B45309" font-weight="600">HOOK ≤ 3 s</text>')
    out.append(f'<line x1="{x(dur):.1f}" x2="{x(dur):.1f}" y1="{top-6}" y2="{top+lane_h*len(LANES)+4}" stroke="#B42318" stroke-width="2"/>')
    out.append(f'<text x="{x(dur)-4:.1f}" y="{top-6}" font-size="10" fill="#B42318" font-weight="600" text-anchor="end">CUT · unresolved</text>')
    for i, (kind, label, col) in enumerate(LANES):
        y = top + i * lane_h
        out.append(f'<text x="0" y="{y+10.5}" font-size="10" fill="#7A7F88">{label}</text>')
        out.append(f'<line x1="{left}" x2="{w-8}" y1="{y+lane_h/2}" y2="{y+lane_h/2}" stroke="#E6E1D7"/>')
        for _bid, s, e, k in beats:
            if k == kind:
                out.append(f'<rect x="{x(s):.1f}" y="{y+2.5}" width="{max(2, x(e)-x(s)-1.2):.1f}" height="{lane_h-5}" rx="2" fill="{col}"/>')
    ay = top + lane_h * len(LANES) + 14
    for t in range(0, int(dur) + 1, 10):
        out.append(f'<text x="{x(t):.1f}" y="{ay}" font-size="9.5" fill="#7A7F88" text-anchor="middle">{t}s</text>')
    out.append(f'<text x="{(x(0)+x(dur))/2:.1f}" y="{top-6}" font-size="10" fill="#16181D" text-anchor="middle">{len(beats)} beats · {beats[0][0]} → {beats[-1][0]}</text>')
    out.append("</svg>")
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts", required=True)
    ap.add_argument("--out", default=str(ROOT / "promo-finder-solution-anshu.pdf"))
    args = ap.parse_args()

    top = json.loads((ROOT / "outputs/promos.json").read_text(encoding="utf-8"))["promos"][0]
    seg = top["segments"][0]
    ep_file = next(Path(args.scripts).glob(f"episode-{seg['episode']:03d}-*.txt"))
    ep = parse_episode(ep_file)
    s0, s1 = seg["start_s"], seg["end_s"]
    beats = [(b.id, b.start_s - s0, b.end_s - s0, b.kind) for b in ep.beats
             if b.start_s >= s0 - 1e-6 and b.end_s <= s1 + 1e-6]
    assert beats[0][0] == seg["start_beat"] and beats[-1][0] == seg["end_beat"], "strip does not match promo #1"

    html = (ROOT / "deck/deck.html").read_text(encoding="utf-8").replace("{{STRIP}}", strip_svg(beats, s1 - s0))
    built = ROOT / "deck/deck.built.html"
    built.write_text(html, encoding="utf-8")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(built.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.pdf(path=args.out, width="297mm", height="210mm", print_background=True,
                 margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
        b.close()
    print("wrote", args.out)


if __name__ == "__main__":
    main()
