# Promo Finder — Kuku AI Generalist Assignment, Problem 1

Reads a folder of microdrama screenplays and returns a ranked shortlist of
20–60 second promo clips, each with exact start/end timecodes, the hook, the
cliffhanger, and one line on why it works.

**Read the solution doc:** <https://anshu2003-commits.github.io/Kuku_assignment/>
· [PDF version](SOLUTION.pdf)

*Anshu Kumar Sharma · Enrolment 22322008 · Problem 1*

**The core design rule: the model never emits a timestamp.** The parser turns
every scene, line and action into a *beat* with an id and exact times. The LLM
only ever selects beat ids and writes prose; code maps ids back to times. A
hallucinated time is therefore impossible by construction — the worst the model
can do is pick a bad beat, which validation catches and the run report records.

## Run it

```bash
pip install -r requirements.txt

# Heuristics only — no API key needed, always works
python run.py --input "<folder of episode .txt files>" --out outputs/ --no-llm

# Full run with the LLM judge
export GEMINI_API_KEY=...          # Windows: $env:GEMINI_API_KEY="..."
python run.py --input "<folder>" --out outputs/

# Swap the model without touching code
python run.py --input "<folder>" --provider groq  --model llama-3.3-70b-versatile
python run.py --input "<folder>" --provider openai --model gpt-4o-mini
python run.py --input "<folder>" --provider anthropic --model claude-sonnet-5

# A slice, e.g. to re-run on unseen episodes
python run.py --input "<folder>" --episodes 40-50 --out outputs/holdout/

# Re-rank a previous run without spending a single API call
python run.py --input "<folder>" --reuse-judgements outputs/judgements.json
```

**Model note.** The default is `gemini-3.1-flash-lite` with thinking off. A
reasoning-grade Gemini took ~245s per judgement versus ~2.5s and returned
identical scores on the same clip, which is ~5 hours versus ~16 minutes for a
50-episode show. The free tier allows **15 requests/minute and 500 per day**;
the client paces itself to 14 rpm and honours the server's retry hints. The
daily cap is the real ceiling — 500 requests is about **2.5 full 50-episode
runs per day**, so iterate with `--episodes 1-5`, `--no-llm`, or
`--reuse-judgements` rather than re-running the whole pack.

UI:

```bash
streamlit run app.py
```

## Outputs

| File | What it is |
|---|---|
| `promos.md` | The readable brief — "post these first", then the full ranked list |
| `promos.csv` | The editor-facing sheet: rank, timecodes, hook, cliffhanger, why |
| `promos.json` | Full structured records including beat ids and every score |
| `judgements.json` | **Every** judgement including rejects — the unfiltered record |
| `run_report.json` | Parse warnings, LLM notes, failures, boundary flags, rejects |

## How it works

```text
 .txt screenplays
      │
      ├─[1] parse.py        deterministic. 3,727 beats, exact timestamps, 0 warnings
      │                     fails loudly: unrecognised lines become warnings
      │
      ├─[2] candidates.py   heuristic hook/cliffhanger lexicons + 20–60s sliding
      │                     windows + non-max suppression → ~4 per episode.
      │                     Purpose: recall and cost control, not judgement.
      │
      ├─[3] judge.py        ← the only LLM step. One call per candidate.
      │     + llm.py        Returns beat ids + hook/cliffhanger/why + 4 scores.
      │                     Invalid ids or illegal durations fall back to the
      │                     heuristic window and get flagged in the report.
      │
      ├─[4] rank.py         slate selection: weighted score, same-episode decay,
      │                     spoiler weighting, multi-segment "stitch" proposals,
      │                     then a containment dedupe so no two promos on the
      │                     slate open on the same footage.
      │
      └─[5] render.py       md / csv / json
```

### What went to AI vs. what stayed as code

| Kept as code | Given to the AI |
|---|---|
| Parsing, timestamps, beat ids | Is this actually a hook? |
| Duration limits, boundary snapping | Does it cut on something unresolved? |
| Candidate generation and pruning | Would a stranger follow it? |
| Ranking policy, diversity, dedupe | One-line hook / cliffhanger / why |
| Validating everything the model returns | |

Ranking policy is code on purpose: it is the part a human should be able to
read and argue with, rather than something buried in a prompt.

### Where the human stays in the loop

The tool produces a ranked shortlist, not a publishing decision. An editor
reviews ~20 clips instead of watching 50 episodes. Multi-segment promos are
explicitly flagged `stitch_unverified_continuity` — the tool reads text, so it
cannot confirm two shots cut together cleanly.

## Cost and scale

One show (50 episodes, ~75k words) = **200 LLM calls** at ~960 input / ~200
output tokens, about 230k tokens total. Two free-tier limits bind: **15
requests/minute**, which sets the ~16 minute wall time, and **500 requests per
day**, which caps you at ~2.5 shows/day. A paid tier removes both and the same
run is under a minute for cents. Parsing and candidate generation take 0.8s.

Per-show cost scales with `--per-episode`, not with episode length, so the
budget for a new show is known before it runs. `--reuse-judgements` re-ranks a
past run for free, so tuning the ranking policy costs no API calls at all.

## Measured quality

30 clips hand-labelled by reading their actual beats against the brief's three
criteria — never the model's description of its own pick. Full detail in
`eval/eval_report.md`, before/after in `eval/before_after.md`.

| Slice | n | good | borderline | bad | |
|---|---|---|---|---|---|
| Top 10 | 10 | 6 | 3 | 1 | **60% clean, 90% usable** |
| Whole slate | 18 | 8 | 8 | 2 | **44% clean, 89% usable** |
| Rejects | 12 | 0 | 4 | 8 | **67% correctly dropped** |

Mechanically: 3,727 beats parsed with 0 warnings, 200/200 candidates judged
with 0 API failures, 8 invalid model boundaries caught by validation, and every
emitted timestamp verified back against the source scripts.

## Known limits

- **Soft endings are the top failure (6/30).** The model scores cliffhanger 4/5
  and the clip still cuts on a resolution. Absolute 1–5 scoring is the cause:
  `standalone` has sd 0.66 and no clip in 200 scored below 2.
- **Slightly too harsh at the gate** — 4 of 12 sampled rejects were postable.
- **Spoiler risk is effectively dead**: 198/200 scored 1 or 2.
- The tool reads screenplay prose, **not frames**. A striking image with no
  dialogue or action verb is systematically underrated — a ceiling on any
  text-only approach, not a tunable bug.
- The `SOUND FX` cliffhanger cue only exists in 22 of 50 episodes, so
  cliffhanger recall is uneven across the season.
- Stitched promos are text-level proposals; continuity is unverified and they
  ship flagged `stitch_unverified_continuity`.

## Layout

```text
promo_finder/parse.py       screenplay -> typed beats with exact timestamps
promo_finder/candidates.py  heuristic scoring + window generation
promo_finder/llm.py         provider-agnostic LLM (gemini|openai|groq|anthropic|mock)
promo_finder/judge.py       prompt, strict-JSON parse, validation, repair retry
promo_finder/rank.py        slate selection, diversity, multi-segment stitching
promo_finder/render.py      md / csv / json writers
run.py                      CLI
app.py                      Streamlit UI
eval/test_parse.py          parser + candidate invariants (no pytest needed)
eval/test_app.py            Streamlit UI behaviour, via streamlit's AppTest
eval/verify_outputs.py      re-parses the scripts and checks every emitted time
eval/make_sample.py         build a stratified blank labelling sheet
eval/score_eval.py          precision + failure-mode breakdown from hand labels
eval/analyse_run.py         score distributions, bias checks, reject reasons
```
