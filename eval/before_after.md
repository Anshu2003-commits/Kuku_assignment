# Before / after: the heuristic lexicon fix

Hand-labelling the first full run surfaced three defects in the candidate
generator. All three let a clip that does not open on a hook reach the judge:

1. **Position counted as content.** A window scored a hook purely from the
   "near the start of a scene" bonus, so a clip opening on a camera move onto
   a closed door qualified (`E22.B062`, ranked 8th).
2. **`crack(ing)` matched "crackling hearth fire".** A cosy bandaging scene by
   a fireplace scored 3.0 for violence (`E14.B027`, ranked 19th).
3. **`fires?` matched "hearth fire"** - same class of bug, same scene.

Fixes: split content from structural scoring and require content evidence in
the opening 3 seconds; narrow `crack` to drop the `-ing` form; replace bare
`fires?` with `fires? (a|his|her|the) (gun|pistol|musket|shot)`.

Both runs used `gemini-3.1-flash-lite`, 200 candidates, same prompt.

| Slice | before | after |
|---|---|---|
| top 10 | 30% clean, 70% usable | **60% clean, 90% usable** |
| whole slate | 29% clean, 65% usable | **44% clean, 89% usable** |
| rejects correctly dropped | 77% | 67% |
| candidates kept by the judge | 146/200 (73%) | 152/200 (76%) |

**The trade-off is real and worth stating:** reject precision fell from 77% to
67%. Tightening the heuristic means the clips reaching the judge are better on
average, so the ones it still rejects are closer calls - four of twelve
sampled rejects were clips I would have posted. The tool is now slightly too
harsh at the gate rather than too loose.

Labels for both runs were produced the same way: read the clip's actual beats
from the screenplay and judge it against the brief's three criteria. Nothing
was scored from the model's own description of its pick.
