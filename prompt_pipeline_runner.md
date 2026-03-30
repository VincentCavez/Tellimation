# Task: Build a UI to batch-run the Tellimations pipeline on 200 utterances

## Context

We need to run the Tellimations pipeline on 200 prepared utterances (100 corrections + 100 suggestions) and collect the animation selection outputs. These outputs will populate the `pipeline_intent` field in `study1_all_stimuli.json` for use in the Prolific study's block 2.

The pipeline has two parallel branches:
1. **Resolution Check**: detects errors in the narrator's utterance relative to the scene
2. **MISL Detection**: detects missing narrative elements that should be scaffolded

Both branches call Gemini. The outputs are fed into the **Animation Handler**, which is a deterministic rule-based module that selects the final animation_id and target entities based on the grammar JSONs.

## Input

`study1_all_stimuli.json` contains 200 stimuli. Each stimulus has:

```json
{
  "stimulus_id": "study1_A1_A_correction",
  "scene_id": "study1_A1_A",
  "condition": "correction",
  "narrator_text": "The white fox is sitting under the tree looking at the two rabbits.",
  "options": { ... },
  
  // For suggestions only:
  "mention_counts": {"CH":3, "S":3, "IE":3, "A":0, ...},
  "target_misl": "A"
}
```

The corresponding scene JSON (in `study1_scenes/`) provides:
- The scene image (for Gemini's VLM input)
- The `entities` dict
- The `entities_in_scene` list

## What the pipeline needs per utterance

### For corrections:
1. Send to Gemini: the scene image + narrator_text + entities list
2. Gemini returns: which entity has an error, what type of error (identity, property, action, space, count, time, relation, discourse)
3. Animation Handler maps the error type to an animation_id using the grammar JSONs (e.g., property error on pot → P2a Emanation-Steam if pot is hot)
4. Output: `{animation_id, target_entities, error_type, rationale}`

### For suggestions:
1. The MISL selector (deterministic) uses mention_counts to pick the target element (the one with count 0)
2. Send to Gemini: the scene image + narrator_text + "scaffold [element]" + entities list
3. Gemini returns: which animation to use and which entities to target
4. Animation Handler validates the selection against the grammar JSONs
5. Output: `{animation_id, target_entities, misl_element, rationale}`

## Expected output per utterance

```json
{
  "stimulus_id": "study1_A1_A_correction",
  "pipeline_result": {
    "animation_id": "A1",
    "animation_name": "Motion Lines",
    "target_entities": ["fox"],
    "error_type": "action",           // corrections only
    "misl_element": null,             // suggestions only
    "rationale": "The narrator said the fox is sitting but the fox is running.",
    "pipeline_intent": "The system detected that the fox's action was described incorrectly and decided to draw attention to what the fox is actually doing.",
    "raw_gemini_response": "...",     // for debugging
    "success": true,
    "error_message": null
  }
}
```

The `pipeline_intent` field is the human-readable description shown to participants in block 2. Generate it from the animation's correction_intent or suggestion_intent in the grammar JSON, adapted to the specific scene context.

## The UI

### Main dashboard

```
┌─────────────────────────────────────────────────────────────┐
│ Pipeline Batch Runner                                        │
│                                                              │
│ [Run all 200] [Run corrections only] [Run suggestions only]  │
│                                                              │
│ Progress: ████████████░░░░░░░░ 120/200  (3 failed)          │
│                                                              │
│ Filter: [All ▼] [Corrections ▼] [Suggestions ▼]             │
│         [Success ▼] [Failed ▼] [Pending ▼]                  │
│                                                              │
│ ┌────────────────────────────────────────────────────────┐   │
│ │ ✅ study1_A1_A_correction                              │   │
│ │    Animation: A1 (Motion Lines) → fox                  │   │
│ │    Intent: "...drew attention to the fox's action..."  │   │
│ │    [Retry] [Edit intent] [Details ▼]                   │   │
│ ├────────────────────────────────────────────────────────┤   │
│ │ ✅ study1_A1_A_suggestion                              │   │
│ │    Animation: A1 (Motion Lines) → fox                  │   │
│ │    MISL: A  Intent: "...scaffold the fox's action..."  │   │
│ │    [Retry] [Edit intent] [Details ▼]                   │   │
│ ├────────────────────────────────────────────────────────┤   │
│ │ ❌ study1_C2_B_correction                              │   │
│ │    Error: Gemini returned invalid animation_id         │   │
│ │    [Retry] [Details ▼]                                 │   │
│ ├────────────────────────────────────────────────────────┤   │
│ │ ⏳ study1_C2_B_suggestion                              │   │
│ │    Pending...                                          │   │
│ └────────────────────────────────────────────────────────┘   │
│                                                              │
│ Summary: 197 success, 3 failed, 0 pending                    │
│ [Export results] [Write pipeline_intent to stimuli JSON]     │
└─────────────────────────────────────────────────────────────┘
```

### Key features

**Batch execution:**
- Run all 200 in parallel (throttled to avoid API rate limits, e.g., 5 concurrent)
- Progress bar with count and error count
- Can run corrections-only or suggestions-only

**Per-utterance row:**
- Status icon: ✅ success, ❌ failed, ⏳ pending
- Animation selected + target entities
- Generated pipeline_intent (truncated, expandable)
- [Retry] button: re-runs this single utterance through the pipeline
- [Edit intent] button: lets me manually edit the pipeline_intent text before export (in case Gemini's phrasing is awkward)
- [Details ▼] expander: shows full Gemini response, error_type/misl_element, rationale, raw response

**Validation checks (shown as warnings):**
- Animation_id doesn't match expected (based on the scene's animation): ⚠️ "Expected I1 but got I2"
- Target entity not in entities_in_scene: ⚠️ "Entity 'cat' not found in scene"
- For suggestions: MISL element doesn't match target_misl from stimuli: ⚠️ "Expected A but got S"

**Filters:**
- By condition (correction/suggestion)
- By status (success/failed/pending)
- By animation category (Identity, Count, Property, Action, Space, Time, Relation, Discourse)
- By warning (show only mismatches)

**Export:**
- [Export results]: saves the full results as `pipeline_results.json`
- [Write pipeline_intent to stimuli JSON]: reads `study1_all_stimuli.json`, adds/updates `pipeline_intent` field in each stimulus, writes back. Only updates stimuli that have a successful pipeline result.

### Detail view (expanded)

```
┌──────────────────────────────────────────────────────┐
│ study1_P2a_B_correction                              │
│                                                      │
│ Scene: study1_P2a_B (Café)                           │
│ Condition: correction                                │
│                                                      │
│ Narrator: "The woman in the green scarf drinks a     │
│ cold iced coffee at the café table on a warm day."   │
│                                                      │
│ Gemini response:                                     │
│   Error type: property                               │
│   Target: coffee                                     │
│   Rationale: "The narrator described the coffee as   │
│   cold but it is visibly hot with steam rising."     │
│                                                      │
│ Animation Handler:                                   │
│   Selected: P2a (Emanation-Steam)                    │
│   Target entities: ["coffee"]                        │
│   ✅ Matches expected animation                      │
│                                                      │
│ Pipeline intent (editable):                          │
│ ┌──────────────────────────────────────────────────┐ │
│ │ The system detected that the coffee's temperature│ │
│ │ was described incorrectly and decided to draw    │ │
│ │ attention to the steam rising from the cup.      │ │
│ └──────────────────────────────────────────────────┘ │
│                                                      │
│ [Save edit] [Retry] [Copy raw response]              │
└──────────────────────────────────────────────────────┘
```

## Pipeline intent generation

For each successful run, auto-generate the pipeline_intent string from:
1. The animation's intent template from the grammar JSON (correction_intent or suggestion_intent)
2. The specific target entities from this scene
3. The rationale from Gemini

Format: a single sentence starting with "The system decided to..." that is understandable by a naive participant without any knowledge of the system's internals. Examples:

- Correction, P2a on coffee: "The system detected that the coffee's temperature was described incorrectly and decided to draw attention to the steam rising from the cup."
- Correction, I1 on fisherman: "The system detected that a character was misidentified and decided to highlight the fisherman on the dock."
- Suggestion, A1 on fox: "The system noticed that the fox's action was not described and decided to draw attention to what the fox is doing."
- Suggestion, CH on clown: "The system noticed that a character was not mentioned and decided to highlight the clown at the carnival."

The auto-generated text should be editable in the UI before export.

## Concurrency and rate limits

- Default: 5 concurrent Gemini API calls
- Configurable in the UI (slider or input)
- Exponential backoff on rate limit errors (429)
- Auto-retry failed calls up to 3 times before marking as failed
- Each call involves: 1 image upload + 1 text prompt to Gemini

## Important notes

- The scene images must be available locally for Gemini VLM input. They are the composed scene images from the generation pipeline.
- The grammar JSONs (study1_A1.json, study1_I1.json, etc. — these are the animation grammar definitions, NOT scene files) must be loaded for the Animation Handler to validate selections.
- The Animation Handler is deterministic: given an error_type + target, or a misl_element + target, it always selects the same animation. The variable part is Gemini's detection.
- For corrections: the pipeline runs Resolution Check (Gemini detects errors) → Animation Handler maps to animation
- For suggestions: the pipeline runs MISL Selector (deterministic, uses mention_counts) → Gemini selects animation → Animation Handler validates
- Save intermediate state to disk so that if the UI is closed mid-batch, completed results are not lost
