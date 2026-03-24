# Claude Code Task: Discrepancy Assessment and Animation Selection Pipeline

## Context

Tellimations detects gaps between what a child says and what a scene contains, then selects animations to scaffold the child's narration. Most of the pipeline already exists. This task restructures the selection logic and adds deterministic prioritization.

## Step 0: Explore the codebase

Find:
1. The CHIRP STT transcription module (already working)
2. The resolution check Gemini call (already working)
3. The error check Gemini call (already working, returns errors with animations)
4. The enrichment check Gemini call (already working, needs adjustment)
5. The animation handler (already working, needs priority ordering)
6. The logging system
7. The scene manifest format and `misl_targets` field per scene
8. The animation grammar JSON definitions

## Pipeline Overview

```
transcription (CHIRP STT)
       │
       ▼
resolution check (Gemini)
       │
       ▼
   update logs
       │
       ├──────────────────────────────┐
       ▼                              ▼
error check (Gemini)          MISL candidate selection
returns ALL errors            (deterministic)
with animation_id                    │
                                     ▼
                              enrichment check (Gemini)
                              returns ONE suggestion
                              with rationale, targets,
                              animation_id
       │                              │
       └──────────────┬───────────────┘
                      ▼
              animation handler
              (deterministic)
              errors > suggestions
              fixed priority within errors
```

## Step 1: Transcription

CHIRP STT transcribes the child's utterance. Already implemented. No changes.

## Step 2: Resolution check (Gemini)

Already implemented. Takes the latest transcription and the last animation triggered. Gemini returns "resolved" or "unresolved". No changes to the call itself.

## Step 3: Update logs

Log the resolution status. Update the per-scene mention counter: which MISL elements the child has addressed so far. This must happen BEFORE the parallel step, because the deterministic selection reads from the logs.

## Step 4: Parallel execution

Launch two paths in parallel.

### Path A: Error check (Gemini)

Already mostly implemented. Gemini receives:
- The child's transcription
- The scene manifest (entities, properties, spatial relations, actions)

Gemini returns ALL factual errors found, each with:
- Category (Identity, Count, Space, Action, Property, Relation, Time, Discourse)
- Target entities
- animation_id
- Description

No changes needed to the Gemini call itself. The output format should already include animation_id per error.

### Path B: Deterministic selection → Enrichment check (Gemini)

Two sub-steps executed sequentially.

#### Step 4B-1: MISL candidate selection (deterministic, no LLM)

Select the MISL element(s) to pass to Gemini. Pure code.

**Macro pass (fixed order):**

```
CH > S > IE > A > CO > IR > P
```

For each element in order:
1. Is it present in this scene's `misl_targets.macro`? If no, skip.
2. Has the child mentioned it fewer than 3 times in this scene (from logs)? If no, skip. If no candidates pass this filter, ignore it (remove the < 3 filter).
3. Is the last logged occurrence for this element "unresolved"? If no, skip. If no logged occurrence, it passes. If no candidates pass this filter, ignore it (remove the unresolved filter).
4. First element that passes all active filters: selected. Stop.

If a macro element is selected, pass it directly to the enrichment check (Step 4B-2). Gemini formulates a suggestion for this specific element.

**If no macro matches, micro pass:**

Collect all micro elements present in this scene's `misl_targets.micro`:
```
Candidates from: ENP, SC, CC, M, L, ADV, G, T
```

Apply same filters:
1. < 3 mentions (if none pass, ignore filter)
2. Last occurrence unresolved (if none pass, ignore filter)

**Shuffle the remaining candidates randomly** (to avoid LLM positional bias in Step 4B-2).

Pass the entire shuffled list to the enrichment check. Gemini will choose the most pertinent one.

**If no candidates at all:** skip Path B entirely. No enrichment call.

Log every step of this selection process (see Step 6).

#### Step 4B-2: Enrichment check (Gemini)

**If a macro element was selected:** Gemini receives:
- The single selected MISL element
- The scene's `misl_targets`
- The scene manifest
- The child's latest transcription
- Instruction: produce one suggestion for this specific MISL element.

**If micro candidates were passed:** Gemini receives:
- The shuffled list of candidate MISL elements
- The scene's `misl_targets`
- The scene manifest
- The child's latest transcription
- Instruction: choose the ONE most pertinent MISL element from the list given the child's last utterance, and produce a suggestion for it.

Gemini returns ONE suggestion:
```json
{
  "misl_element": "ENP",
  "rationale": "The child said 'the cat' but could describe it as 'the orange cat with a red collar'",
  "targets": ["entity_cat"],
  "animation_id": "P1"
}
```

## Step 5: Animation handler (deterministic)

Receives results from both parallel paths. Makes the final decision.

### Priority logic

**Errors always beat suggestions.**

If Path A returned errors:
- Sort by fixed category priority:
  ```
  Identity > Count > Space > Action > Property > Relation > Time > Discourse
  ```
- Select the first (highest priority) error
- Build invocation from its animation_id and targets

If Path A returned no errors:
- Use the suggestion from Path B (if any)
- Build invocation from its animation_id and targets

If neither path produced anything:
- Log "no_action". No animation.

### Control condition

In control condition, the entire pipeline runs identically. The animation handler logs what would have been triggered but does not play the animation. Logged as `"control_suppressed"`.

### Output: invocation

```json
{
  "sequence": [
    {
      "animation_id": "P1",
      "targets": ["entity_cat"],
      "parameter_overrides": {}
    }
  ]
}
```

## Step 6: Logging

Every cycle logs the full trace, including each step of the deterministic selection:

```json
{
  "timestamp": "...",
  "scene_number": 3,
  "transcription": "the cat is on the ground",
  "resolution_check": {
    "previous_animation_id": "P1",
    "previous_misl_element": "ENP",
    "status": "unresolved"
  },
  "mention_counts": {
    "CH": 4, "S": 1, "IE": 0, "A": 2, "CO": 0, "IR": 0, "P": 0,
    "ENP": 3, "SC": 0, "CC": 2, "M": 0, "L": 0, "ADV": 1, "G": 0, "T": 1
  },
  "deterministic_selection": {
    "macro_in_scene": ["CH", "S", "A", "IR"],
    "macro_under_3": ["S", "A", "IR"],
    "macro_unresolved": ["S", "IR"],
    "macro_selected": "S",
    "micro_in_scene": null,
    "micro_under_3": null,
    "micro_unresolved": null,
    "micro_candidates_shuffled": null,
    "micro_gemini_selected": null
  },
  "errors_found": [],
  "suggestion": {
    "misl_element": "S",
    "rationale": "...",
    "targets": ["entity_fence"],
    "animation_id": "S1"
  },
  "selected": {
    "source": "suggestion",
    "animation_id": "S1",
    "targets": ["entity_fence"]
  },
  "action": "triggered",
  "condition": "animation"
}
```

When a macro is selected, all micro fields are `null`. When no macro matches, `macro_selected` is `null` and the micro fields are populated. Example of a micro selection cycle:

```json
{
  "deterministic_selection": {
    "macro_in_scene": ["CH", "S", "A"],
    "macro_under_3": [],
    "macro_unresolved": [],
    "macro_selected": null,
    "micro_in_scene": ["ENP", "SC", "CC", "ADV"],
    "micro_under_3": ["SC", "CC", "ADV"],
    "micro_unresolved": ["SC", "ADV"],
    "micro_candidates_shuffled": ["ADV", "SC"],
    "micro_gemini_selected": "SC"
  }
}
```

The `mention_counts` object must always contain all 15 MISL elements: CH, S, IE, A, CO, IR, P, ENP, SC, CC, M, L, ADV, G, T. Elements not present in the scene still appear with their current count (usually 0).

## What needs to change (summary)

| Component | Status | Change needed |
|-----------|--------|---------------|
| CHIRP STT | Working | None |
| Resolution check (Gemini) | Working | None |
| Error check (Gemini) | Working | None (verify output includes animation_id per error) |
| MISL candidate selection | **New** | Implement deterministic selector: macro fixed order, filters, micro shuffle |
| Enrichment check (Gemini) | Working | Adjust: for macros, accept single pre-selected element. For micros, receive shuffled list for Gemini to choose from. |
| Animation handler | Working | Add fixed error priority: Identity > Count > Space > Action > Property > Relation > Time > Discourse. Remove any cooldown logic. |
| Logging | Working | Add `deterministic_selection` trace, `mention_counts` with all 15 elements, ensure both conditions log full pipeline |
| Mention counter | **New** | Per-scene counter, all 15 MISL elements, updated at Step 3 |

## Implementation order

1. **Mention counter**: needed by the selector, must track all 15 elements
2. **MISL candidate selection**: the new deterministic module, with full trace logging
3. **Adjust enrichment check**: macro gets single element, micro gets shuffled list for Gemini to choose from
4. **Adjust animation handler**: add error priority ordering, remove cooldown
5. **Adjust logging**: add `deterministic_selection` object and `mention_counts` with all 15 elements, ensure both conditions log full pipeline
6. **Test**: run with existing scenes, verify deterministic behavior
