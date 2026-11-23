# Blind re-coding of resolution (CHI 2027 revision, Section 6.3)

Instructions for Claude Code. The goal is to turn the children's session logs (JSON) into blind coding sheets for two coders, then analyse their coding against the pipeline's own resolution judgments.

Two scripts do the work and must not be rewritten:

- `make_coding_items.py`: reads one flat CSV of gaps and writes the coder sheets.
- `analyze_coding.py`: reads the two filled sheets and the key, and prints the numbers that go into Section 6.3.

Put both scripts in `scripts/resolution_coding/` and write everything you create (the adapter, outputs) in that same folder. Never modify the session JSON files.

## What you have to write: the adapter

The only missing piece is `build_gaps_csv.py`, which reads the session JSON files and writes `gaps.csv`, one row per gap that the pipeline identified. **Start by opening two or three session files and describing their structure before writing any code.** Sessions are known to contain `study_scene` and `training_scene` objects, each with a `scene_key` and a list of `entries`, but check the actual field names: do not guess them.

### Columns of `gaps.csv`

| column | content |
|---|---|
| `child` | participant id (P1 to P12) |
| `story` | story id or name |
| `condition` | `animation` or `control` |
| `scene_key` | scene id within the story |
| `scene_image` | path of the scene illustration (find it in the story config, e.g. `config/study_stories.json`) |
| `utterance` | the child's utterance in which the pipeline found the gap |
| `gap` | the pipeline's description of the gap (rationale) |
| `next_utterance` | the child's next utterance **in the same scene**, empty if there is none |
| `llm_resolved` | the pipeline's resolution judgment for this gap (1 or 0) |
| `animation_id` | animation selected for the gap (also logged in the control condition, where display is suppressed) |
| `interrupted` | 1 if the animation never appeared, 0 otherwise |
| `is_practice` | 1 for gaps from `training_scene` (practice stories), 0 for `study_scene` |

### Rules

1. **One row per identified gap**, in both conditions. In the control condition the pipeline runs identically and suppresses display, so gaps and resolution judgments are logged there too.
2. **`next_utterance` must come from the same scene.** The last gap of each scene is usually followed by a click on "Next Scene", not by an utterance: leave `next_utterance` empty for it. `make_coding_items.py` then drops it, because the child had no chance to respond.
3. **Check how the pipeline's resolution judgment is paired with its gap.** The resolution check runs on the *next* utterance and refers to the previous gap (`session._study_previous_discrepancy` in `src/ui/app.py` and `assess_resolution()` in `src/interaction/discrepancy_assessment.py`). That previous gap is stored at the session level, so the first utterance of a new scene may have been judged against the last gap of the previous scene. Find out from the logs whether this happened, and **never pair a gap with a resolution judgment made in another scene**. Report how many such cross-scene judgments exist.
4. **Interruptions have two causes.** An animation is interrupted either because the child pressed push-to-talk again before it appeared, or because the child clicked "Next Scene". Find in the logs how each case is recorded and keep the two apart when you report counts. Only the first kind can occur among the gaps kept for coding.
5. **Leaks come from the children's utterances, not from the `gap` text.** The control condition runs the same pipeline (rationale and animation selection included, only display suppressed), so a rationale that names an animation exists in both conditions and does not reveal the condition. What can reveal it is a child reacting to an animation ("why is the dog shaking?", "it's glowing!"), which only happens in the animation condition. Keep these items as they are: do not remove or redact them. Count them from `leak_report.csv`, rows where `field` is `utterance` or `next_utterance`, by condition. The coders' condition guesses then show whether blinding held.
6. **Check that rationales describe the gap, not the animation.** The coders judge whether the child addressed what was missing, so the `gap` text should say what is missing from the narration (for example "the child did not say where the boy is running"), not what will move on screen. Report how many rationales mostly describe the animation, with a few examples, and ask Vincent before rewriting any of them.

### Sanity check against the paper (do this before generating the sheets)

Section 6.3 currently reports, over 12 children (P7 stopped after the two animated stories, so 24 animated and 22 control stories):

- animation: 20.6 cycles per story, 425 gaps followed by another utterance in the same scene, 197 resolved by the pipeline (46%);
- control: 14.7 cycles per story, 265 such gaps, 34 resolved (13%);
- animations: 11.9 displayed and 8.7 interrupted per story.

Recompute these from `gaps.csv` and print them next to the paper's values. If they differ (they may, because 20.6 minus 17.7 is only about 2.9 dropped gaps per story, not one per scene), **stop and report the difference and its cause to Vincent instead of adjusting anything.** Also print the share of interrupted animations among the kept animation gaps only, split by cause.

## Running

```bash
cd scripts/resolution_coding
python build_gaps_csv.py <path to session JSON folder> --out gaps.csv
python make_coding_items.py gaps.csv --out coding --seed 2027
```

`make_coding_items.py` writes into `coding/`:

- `coder_A.csv` and `coder_B.csv`: the same items in two different random orders, with empty columns `resolved` (1/0), `condition_guess` (animation/control) and `comment`;
- `practice_items.csv`: practice-story gaps for calibrating the coders on the coding guide (not analysed);
- `key.csv`: item id to child, story, condition and pipeline judgment. **Never give this file to the coders**, and do not put it in a folder they can open;
- `leak_report.csv`: items whose text may reveal the condition.

Item ids (G001, G002, ...) are assigned after a shuffle and carry no information.

Once both coders have filled in their sheets:

```bash
python analyze_coding.py coding/coder_A.csv coding/coder_B.csv coding/key.csv
```

It prints, in this order: agreement between the coders (percentage and Cohen's kappa), the accuracy of each coder's condition guesses and their kappa with the actual condition (the blinding check), resolved gaps per condition by the coders and by the pipeline, the paired Wilcoxon test on the 11 children who completed both conditions, agreement between the coders and the pipeline, and resolution for displayed versus interrupted animations. A gap counts as resolved only when both coders marked it as resolved, which is the rule stated in the paper.

## Where the numbers go

They replace the `XX` placeholders in the red (`\review{}`) text of Section 6.3 in `06_evaluation.tex`. Do not edit the paper without Vincent's go-ahead.
