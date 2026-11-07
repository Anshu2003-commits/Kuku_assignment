# Quality check

30 clips hand-labelled against the brief's three criteria: a hook inside the first 3 seconds, an unresolved ending, and making sense to someone who has never seen the show.

| Slice | n | good | borderline | bad | reading |
|---|---|---|---|---|---|
| top10 | 10 | 6 | 3 | 1 | **60% clean, 90% usable** |
| slate_tail | 8 | 2 | 5 | 1 | **25% clean, 88% usable** |
| reject | 12 | 0 | 4 | 8 | **67% correctly dropped** |
| **whole slate** | 18 | 8 | 8 | 2 | **44% clean, 89% usable** |

## Failure modes, by frequency

- **soft-ending** — 6
- **wrong-reject** — 4
- **soft-hook** — 3
- **overstated-hook** — 1
- **hook-late** — 1
- **opens-mid-sentence** — 1
- **needs-context** — 1

## Every labelled clip


### top10

- #1 `3:E03.B064` **good** — 'Stand aside, stranger!' into the stranger dropping to one knee with a ring and a stunned congregation gasp.
- #3 `10:E10.B004` **bad** (overstated-hook;soft-ending) — Model says 'blood-soaked man stumbles in'; script says 'a cut from the tenant fields'. Ends on a flirt.
- #4 `15:E15.B030` **good** — Shoved to the ground at 2.6s; cuts as Alexander storms in with lethal intent.
- #5 `45:E45.B056` **good** — Cathedral doors blown open, a Bishop interrupts the wedding; cuts on Catherine's despair turning vindictive.
- #7 `24:E24.B020` **borderline** (soft-ending;hook-late) — Whip strike at 3.5s. Ends on 'Clean it.' / 'All of it, my lady?' - a chore, not a cliffhanger.
- #8 `32:E32.B088` **borderline** (opens-mid-sentence) — Branding poker and 'Silence!' are strong, but it opens on the back half of someone else's line.
- #9 `16:E16.B016` **good** — Opens on a thug slammed into a wall; cuts on 'Who sent you?' - a clean unanswered question.
- #10 `34:E34.B063` **good** — Witch-trial testimony; cuts as the judge says refusal is proof of witchcraft.
- #11 `36:E36.B099` **good** — '...and killing my wife afterward.' into an ominous sting over dark woods.
- #12 `1:E01.B054` **borderline** (soft-hook) — 'Father... tell them this is a mistake' is sad, not shocking. Great ending and fully standalone.

### slate_tail

- #13 `2:E02.B005` **borderline** (soft-ending) — Strong cold open, but ends on 'Accept it, Juliana' - a dismissal, not a hanging question.
- #14 `7:E07.B031` **borderline** (soft-ending) — Strong threat to open, but 'I didn't need to.' is cryptic rather than cliffhanging.
- #15 `13:E13.B027` **good** — Chained man roaring her name; cuts on the macro shot of the ring she has just unearthed.
- #16 `18:E18.B059` **good** — Shatters through a window in the first second; ends on 'We have nowhere to go.'
- #17 `23:E23.B016` **borderline** (soft-hook) — Opens on laboured breathing. But 'Arsenic.' is one of the best endings in the show.
- #18 `25:E25.B008` **bad** (soft-hook;soft-ending) — 'You're hurt.' to 'The Dowager?' - no shock at either end.
- #19 `28:E28.B031` **borderline** (needs-context) — 'And you... you knew!' hooks, but who knew what is unclear cold.
- #20 `30:E30.B048` **borderline** (soft-ending) — Good conspiracy opener, but ends on a static tableau of her crying in a cell.

### reject

- — `39:E39.B048` **borderline** (wrong-reject) — 'Secure the chapel' / 'I am bringing my wife home' has real momentum. Harsh drop.
- — `6:E06.B036` **bad** — Quiet cottage conversation, no conflict.
- — `27:E27.B005` **borderline** (wrong-reject) — Ends on 'He told me he was only a sergeant' - a real identity reveal.
- — `16:E16.B051` **bad** — Romantic resolution despite the script's own 'cliffhanger' sound cue.
- — `6:E06.B055` **bad** — She tidies the cottage. Nothing at stake.
- — `7:E07.B043` **bad** — Mild exchange, no shock at either end.
- — `23:E23.B031` **borderline** (wrong-reject) — Weak visual hook, but 'Arsenic.' is one of the best endings in the show.
- — `22:E22.B041` **bad** — Housekeeper warning; ends on a logistics question.
- — `6:E06.B012` **bad** — Quiet rain-soaked kindness, no hook.
- — `17:E17.B044` **bad** — Tender scene closing on warm strings - the opposite of a cliffhanger.
- — `41:E41.B000` **bad** — Cuts just before the explosion - the model caught this correctly.
- — `38:E38.B012` **borderline** (wrong-reject) — Slow, but ends on 'What if I never see you again?' which does hang.
