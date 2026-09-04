# Study 1 (Prolific, March 2026) — materials

Two blocks, 80 participants. Block 1: participants pick what they would say
about a scene. Block 2: participants rate the pipeline's output on a Likert
scale (200 outputs x 4 ratings = 800 ratings).

**`study1_all_stimuli.json`** — the 200 stimuli: 100 corrections and 100
suggestions over 100 scenes. Per stimulus: `narrator_text`, `target_animation`,
`target_entities`, `pipeline_intent`.

**`pipeline_results.json`** — the pipeline's output on those 200 stimuli
(run 2026-03-30, 200/200 success). Per stimulus: chosen animation, target
entities, `validation`, `elapsed_ms`, `raw_discrepancies`. Not a blind
benchmark: stimuli were curated one by one until the pipeline produced the
designed animation, hence the 100/100 animation match.

**`study1_utterances.pdf`** — the same 200 utterances, printable.

**`counterbalancing_lists.json`** — block 1 design: 8 lists of 10 participants,
25 stimuli each (25 animations x 4 scenes), 10 views per stimulus.

**`block2_assignments.json`** — block 2 design: per-participant assignment,
10 stimuli each, no scene overlap with block 1.

Participant responses are not included here.
