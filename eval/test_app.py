#!/usr/bin/env python
"""UI behaviour tests for the Streamlit app, via Streamlit's own AppTest.

These exist because the failure mode they guard against is invisible in normal
use until it costs you a 16-minute run: Streamlit reruns the whole script on
every widget interaction, including a download_button click, so results must
live in st.session_state rather than inside the button's branch.

Runs entirely on the mock provider - no API key, no network.

    python eval/test_app.py --input "<pack>"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    print(f"[{'  ok  ' if cond else ' FAIL '}] {name}" + (f" - {detail}" if detail else ""))
    if not cond:
        failures += 1


def widget(group, label):
    """Look widgets up by label - index order mixes sidebar and main body."""
    for w in group:
        if w.label == label:
            return w
    raise AssertionError(f"no widget labelled {label!r}")


def boot(pack: str, episodes: str = "1-3") -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    widget(at.selectbox, "Provider").set_value("mock")
    widget(at.text_input, "Folder containing episode .txt files").set_value(pack)
    widget(at.text_input, "Episodes").set_value(episodes)
    at.run()
    return at


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    args = ap.parse_args()
    pack = str(Path(args.input).resolve())  # app resolves paths against its own cwd

    at = boot(pack)
    check("app runs without exception", not at.exception, str(at.exception))
    check("episode filter applied", any("3 episode" in s.value for s in at.success),
          "; ".join(s.value for s in at.success))

    widget(at.button, "Find promos").click().run()
    check("run produced results", "results" in at.session_state)
    n = len(at.session_state["results"]["promos"]) if "results" in at.session_state else 0
    check("promos rendered", n > 0, f"{n} promos")
    check("no exception during run", not at.exception, str(at.exception))

    # The real regression: a rerun that is NOT the button click must keep results.
    widget(at.slider, "Promos in the slate").set_value(9).run()
    check(
        "results survive a slider change",
        "results" in at.session_state
        and len(at.session_state["results"]["promos"]) == n,
        "results were lost on rerun" if "results" not in at.session_state else "",
    )
    check("promo expanders still on screen", len(at.expander) > 0,
          f"{len(at.expander)} expanders")

    # A download_button click triggers a rerun too - this is what used to wipe it.
    if at.button:
        at.run()
        check(
            "results survive a bare rerun (download click)",
            "results" in at.session_state,
        )

    widget(at.button, "Clear results").click().run()
    check("Clear results empties them", "results" not in at.session_state)

    print(f"\n{'FAILED: ' + str(failures) if failures else 'all UI checks passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
