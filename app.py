"""Streamlit UI for the promo finder.

    streamlit run app.py

Drop in one or more episode .txt files (or point it at the pack folder) and get
a ranked promo shortlist with exact timecodes an editor can cut against.

Results live in st.session_state, not in the button's branch. Streamlit reruns
the whole script on every widget interaction - including a download_button
click - so rendering inside `if st.button(...)` would throw away a 16-minute
run the moment someone downloaded the CSV.
"""

from __future__ import annotations

import csv
import io
import tempfile
import threading
import time
from pathlib import Path

import streamlit as st

from promo_finder.candidates import generate_all
from promo_finder.judge import judge_all
from promo_finder.llm import DEFAULT_MODELS, RateLimiter, build_llm
from promo_finder.parse import filter_episodes, parse_pack
from promo_finder.rank import build_stitch_promos, dedupe_slate, rank_promos

st.set_page_config(page_title="Kuku Promo Finder", page_icon="🎬", layout="wide")

st.title("🎬 Promo Finder")
st.caption(
    "Reads microdrama screenplays and finds 20-60s clips that open on a hook "
    "and cut on a cliffhanger. Timestamps come from the script, never from the model."
)

with st.sidebar:
    st.header("Model")
    provider = st.selectbox(
        "Provider", ["gemini", "groq", "openai", "anthropic", "mock"], index=0
    )
    model = st.text_input("Model id", value=DEFAULT_MODELS.get(provider, ""))
    api_key = st.text_input("API key", type="password", help="Left blank: uses env var")
    st.caption("No key -> runs heuristics only, with placeholder scores.")

    st.header("Tuning")
    per_episode = st.slider("Candidates per episode", 1, 8, 4)
    limit = st.slider("Promos in the slate", 5, 40, 20)
    stitches = st.slider("Multi-segment promos", 0, 5, 3)
    workers = st.slider("Parallel calls", 1, 8, 4)
    rpm = st.slider(
        "Requests/min cap", 5, 300, 14, help="Gemini free tier is 15 RPM."
    )

st.subheader("Input")
tab_upload, tab_folder = st.tabs(["Upload episode files", "Use a folder path"])
episodes = None

with tab_upload:
    uploads = st.file_uploader(
        "Episode .txt files", type=["txt"], accept_multiple_files=True
    )
    if uploads:
        tmp = Path(tempfile.mkdtemp())
        for up in uploads:
            (tmp / up.name).write_bytes(up.getvalue())
        episodes = parse_pack(tmp)
        st.success(f"Parsed {len(episodes)} episode(s).")

with tab_folder:
    folder = st.text_input("Folder containing episode .txt files")
    ep_spec = st.text_input(
        "Episodes", placeholder="e.g. 40-50 or 1,2,3 — blank for all",
        help="Use a small slice for a fast demo; the full pack takes ~16 min.",
    )
    if folder and Path(folder).is_dir():
        episodes = filter_episodes(parse_pack(folder), ep_spec)
        if episodes:
            st.success(f"Parsed {len(episodes)} episode(s) from {folder}")
        else:
            st.error(f"No episodes matched {ep_spec!r}.")
            episodes = None
    elif folder:
        st.error("Not a directory.")

if episodes:
    n_beats = sum(len(e.beats) for e in episodes)
    warns = [w for e in episodes for w in e.warnings]
    c1, c2, c3 = st.columns(3)
    c1.metric("Episodes", len(episodes))
    c2.metric("Beats parsed", n_beats)
    c3.metric("Parse warnings", len(warns))
    if warns:
        with st.expander(f"{len(warns)} parse warnings"):
            for w in warns[:50]:
                st.text(w)

    n_candidates = len(generate_all(episodes, top_n=per_episode))
    if provider == "mock":
        st.info(f"{n_candidates} candidates · mock provider, runs instantly.")
    else:
        st.info(
            f"{n_candidates} candidates · at {rpm} req/min this run takes about "
            f"**{n_candidates / rpm:.0f} min**."
        )

    go, clear = st.columns([1, 5])
    run_now = go.button("Find promos", type="primary")
    if clear.button("Clear results"):
        st.session_state.pop("results", None)

    if run_now:
        with st.spinner("Generating candidates..."):
            candidates = generate_all(episodes, top_n=per_episode)

        llm, notes = build_llm(provider, model, api_key or None)
        for n in notes:
            st.warning(n)

        bar = st.progress(0.0, text=f"Judging 0/{len(candidates)} with {llm.name}...")

        # Only the main thread may touch a Streamlit widget: calls from the
        # judge's pool threads have no ScriptRunContext and are dropped
        # silently, so the bar would sit at 0% for the whole run. The workers
        # just bump a counter; the main thread polls it and draws.
        tally = {"done": 0, "total": len(candidates)}
        box: dict[str, tuple] = {}

        def worker() -> None:
            try:
                box["out"] = judge_all(
                    candidates, episodes, llm, "uploaded show",
                    workers=workers,
                    limiter=None if llm.name == "mock" else RateLimiter(rpm),
                    on_progress=lambda d, t: tally.update(done=d, total=t),
                )
            except Exception as exc:  # noqa: BLE001 - surfaced below
                box["err"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while thread.is_alive():
            done, total = tally["done"], tally["total"]
            bar.progress(done / total, text=f"Judged {done}/{total} candidates...")
            time.sleep(0.25)
        thread.join()

        if "err" in box:
            bar.empty()
            st.error(f"Judging failed: {box['err']}")
            st.stop()
        judgements, errors = box["out"]
        bar.progress(1.0, text=f"Judged {len(judgements)}/{len(candidates)}")

        promos = rank_promos(judgements, limit=limit * 3)
        if stitches:
            promos.extend(
                build_stitch_promos(judgements, episodes, max_stitches=stitches)
            )
        promos = dedupe_slate(promos)[:limit]

        st.session_state["results"] = {
            "promos": promos,
            "n_judged": len(judgements),
            "n_kept": sum(1 for j in judgements if j.keep),
            "errors": errors,
            "model": llm.name,
        }

# Rendered from session state, so it survives downloads and slider changes.
results = st.session_state.get("results")
if results:
    promos = results["promos"]
    st.subheader(
        f"{len(promos)} promos · {results['n_kept']}/{results['n_judged']} "
        f"candidates kept · judged by `{results['model']}`"
    )

    if results["errors"]:
        quota = [e for e in results["errors"] if "RESOURCE_EXHAUSTED" in e]
        # The two free-tier limits need opposite remedies, so say which one hit:
        # pacing fixes a per-minute rate, but nothing fixes a spent daily budget.
        per_day = any("PerDay" in e for e in quota)
        if quota and per_day:
            st.error(
                f"**{len(quota)} calls refused — the daily quota is spent.** "
                "Gemini's free tier allows 500 requests/day (~2.5 full "
                "50-episode runs), and it resets at midnight Pacific. "
                "Lowering 'Requests/min cap' will **not** help: that paces the "
                "per-minute limit, not the daily budget. Switch Provider to "
                "`mock` to keep working, or come back after the reset."
            )
        elif quota:
            st.error(
                f"**{len(quota)} calls hit the per-minute rate limit.** Gemini's "
                "free tier allows 15 requests/minute. Lower 'Requests/min cap' "
                "to 14 or below, or reduce 'Parallel calls'."
            )
        elif len(results["errors"]) > results["n_judged"]:
            st.error(f"{len(results['errors'])} of the calls failed — see below.")
        with st.expander(f"{len(results['errors'])} judge errors"):
            for e in results["errors"][:50]:
                st.text(e)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "episodes", "timecodes", "duration_s", "hook",
                "cliffhanger", "why_it_works", "score"])
    for p in promos:
        w.writerow([
            p.rank,
            " + ".join(f"Ep{e:02d}" for e in p.episodes),
            " | ".join(s.label() for s in p.segments),
            f"{p.duration:.2f}", p.hook, p.cliffhanger, p.why_it_works,
            f"{p.slate_score:.3f}",
        ])
    st.download_button("Download promos.csv", buf.getvalue(), "promos.csv", "text/csv")

    for p in promos:
        tag = " · multi-segment" if p.kind == "stitch" else ""
        header = ", ".join(s.label() for s in p.segments)
        with st.expander(
            f"#{p.rank}  {header}  ({p.duration:.0f}s){tag}  "
            f"— score {p.slate_score:.2f}",
            expanded=p.rank <= 3,
        ):
            st.markdown(f"**Hook** — {p.hook}")
            st.markdown(f"**Cliffhanger** — {p.cliffhanger}")
            st.markdown(f"**Why it works** — {p.why_it_works}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Hook", f"{p.hook_strength}/5")
            m2.metric("Cliffhanger", f"{p.cliffhanger_strength}/5")
            m3.metric("Standalone", f"{p.standalone}/5")
            m4.metric("Spoiler risk", f"{p.spoiler_risk}/5")
            st.code(
                "\n".join(
                    f"{s.label()}   ({s.duration:.1f}s)  "
                    f"[{s.start_beat} -> {s.end_beat}]"
                    for s in p.segments
                ),
                language="text",
            )
            if p.flags:
                st.warning("Flags: " + ", ".join(p.flags))
elif not episodes:
    st.info("Upload episode .txt files or give a folder path to start.")
