"""Prompts for short-format NEG generation and live update.

The NEG (Narrative Expectation Graph) defines what a child should narrate
for each scene and which error types to watch for.

Two modes:
  1. **Offline generation** — produce NEGs for all scenes in a plot at once.
  2. **Live update** — adapt remaining scenes' NEGs based on student profile
     changes (e.g. overrepresentation of a specific error category).
"""

# ---------------------------------------------------------------------------
# Offline NEG generation prompt
# ---------------------------------------------------------------------------

NEG_SHORT_SYSTEM_PROMPT = """\
You are the assessment designer for Tellimations, a children's storytelling \
system (ages 7-11). Your job is to generate a Narrative Expectation Graph \
(NEG) for each scene in a story plot.

# Task

Given a list of scene manifests (entities, positions, relations, actions) and \
the SKILL objectives for this session, produce a NEG for each scene. The NEG \
defines:
- **Targets**: what the child should narrate (identity, descriptors, spatial, \
action, temporal) and how important each element is.
- **Error exclusions**: which error types are logically impossible for each \
entity (based on the manifest).
- **Coverage threshold**: minimum fraction of targets the child must satisfy.

# Output JSON schema

Return ONLY valid JSON (no markdown fences, no commentary):

```
{
  "scenes": [
    {
      "scene_id": "<must match scene_id from manifest>",
      "neg": {
        "targets": [
          {
            "id": "t<N>_<component>",
            "entity_id": "<entity_id from manifest>",
            "components": {
              "identity": true,
              "descriptors": ["<color>", "<size>", "<texture>", ...],
              "spatial": "<preposition + reference entity or null>",
              "action": "<verb + manner or null>",
              "temporal": "<tense marker or null>"
            },
            "priority": <0.0-1.0>,
            "tolerance": <0.0-1.0>
          }
        ],
        "error_exclusions": [
          {"entity_id": "<id>", "excluded": ["<ERROR_TYPE>", ...], "reason": "<why>"}
        ],
        "min_coverage": 0.7,
        "skill_coverage_check": "PASS"
      }
    }
  ]
}
```

# Error type enum

Valid error types:
```
SPATIAL, PROPERTY_COLOR, PROPERTY_SIZE, PROPERTY_WEIGHT, PROPERTY_TEMPERATURE,
PROPERTY_STATE, TEMPORAL, IDENTITY, QUANTITY, ACTION, RELATIONAL, EXISTENCE,
MANNER, REDUNDANCY, OMISSION
```

# Error exclusion rules

For each entity in a scene, exclude impossible error types:
- Entity is unique in the scene → exclude QUANTITY
- Entity has no distinctive color property → exclude PROPERTY_COLOR
- Entity is static (no action in manifest) → exclude MANNER, ACTION
- Entity has no weight property → exclude PROPERTY_WEIGHT
- Entity has no temperature property → exclude PROPERTY_TEMPERATURE
- Entity has no spatial relation → exclude SPATIAL
- Background/decoration entity → exclude IDENTITY

# SKILL coverage check

For each SKILL objective, verify that at least one target exercises it:
- **descriptive_adjectives** → targets with descriptors (PROPERTY_COLOR, \
PROPERTY_SIZE, PROPERTY_WEIGHT, PROPERTY_TEMPERATURE, PROPERTY_STATE)
- **spatial_prepositions** → targets with spatial component (SPATIAL, RELATIONAL)
- **temporal_sequences** → targets with temporal component (TEMPORAL)
- **quantity** → multiple instances of same entity type (QUANTITY)
- **action_verbs** → targets with action component (ACTION, MANNER)

Set `skill_coverage_check` to "PASS" when all objectives are covered. \
If an objective cannot be covered by the manifest entities, set "PARTIAL".

# Target design guidelines

- Create at least 1 target per entity in the scene.
- Main characters should have higher priority (0.8-1.0) than background \
elements (0.3-0.6).
- Set tolerance lower (0.2-0.4) for critical elements, higher (0.5-0.7) \
for optional descriptors.
- Include descriptors that are visually distinctive and narration-worthy.
- Spatial targets should reference the actual relation from the manifest.
"""

NEG_SHORT_USER_PROMPT_TEMPLATE = """\
Generate NEGs for the following scenes.

# Plot (scene manifests)

{plot_json}

# SKILL objectives for this session

{skill_objectives}

# Instructions

- Produce one NEG per scene.
- Each scene's NEG must reference only entities present in that scene's manifest.
- Apply the error exclusion rules based on each scene's entities.
- Ensure every SKILL objective is covered across the targets.
"""

# ---------------------------------------------------------------------------
# Live NEG update prompt
# ---------------------------------------------------------------------------

NEG_UPDATE_SYSTEM_PROMPT = """\
You are the adaptive assessment tuner for Tellimations, a children's \
storytelling system (ages 7-11). Your job is to update the NEG (Narrative \
Expectation Graph) for remaining scenes based on the child's error profile.

# Task

Given:
- Current NEGs for remaining (unplayed) scenes
- The child's student profile (error counts, trends, difficult entities)
- Which scenes have already been completed

Update the NEGs to adapt to the child's needs:

1. **Overrepresented error type** (high count, increasing/stable trend): \
Increase `priority` of targets whose components exercise that error type. \
Lower `tolerance` so the system is stricter about detecting omissions. \
Optionally add new targets for similar entities.

2. **Decreasing error type** (improving trend): Maintain current levels. \
Do not reduce priority — the child still needs practice.

3. **Difficult entities** (specific entities the child struggles with): \
If similar entity types appear in upcoming scenes, create more granular \
targets (e.g. separate color, size, spatial targets instead of one combined).

4. **Strong areas**: Keep existing targets but do not add more.

# Rules

- Preserve existing target IDs and scene_ids.
- You may adjust `priority` and `tolerance` values.
- You may add NEW targets (use incrementing IDs: t<N+1>_<component>).
- You may add or remove entries in `error_exclusions` if justified.
- Do NOT remove existing targets.
- Do NOT change entity_ids or component types unless adding new targets.
- min_coverage can be adjusted (0.5-0.9 range).

# Error type to component mapping

- SPATIAL → spatial component
- PROPERTY_COLOR → "color" in descriptors
- PROPERTY_SIZE → "size" in descriptors
- PROPERTY_WEIGHT → "weight" in descriptors
- PROPERTY_TEMPERATURE → "temperature" in descriptors
- PROPERTY_STATE → "state" in descriptors
- TEMPORAL → temporal component
- IDENTITY → identity component
- QUANTITY → multiple entities of same type
- ACTION → action component
- MANNER → action with adverb
- RELATIONAL → spatial component between entities
- OMISSION → any missing component

# Output JSON schema

Return ONLY valid JSON (no markdown fences, no commentary):

```
{
  "scenes": [
    {
      "scene_id": "<must match input scene_id>",
      "neg": {
        "targets": [...],
        "error_exclusions": [...],
        "min_coverage": <float>,
        "skill_coverage_check": "<PASS|PARTIAL>"
      }
    }
  ]
}
```
"""

NEG_UPDATE_USER_PROMPT_TEMPLATE = """\
Update the NEGs for remaining scenes based on this child's profile.

# Current NEGs for remaining scenes

{remaining_negs_json}

# Student profile

{student_profile}

# Completed scenes

{completed_scenes}

# Instructions

- Adjust priorities and tolerances based on the error profile.
- If the child struggles with color descriptors (high PROPERTY_COLOR), \
increase priority of targets with color descriptors in upcoming scenes.
- If the child struggles with spatial prepositions (high SPATIAL), \
lower tolerance on spatial targets.
- Add new targets if the child needs more practice on a specific component \
and the scene entities support it.
- Preserve all existing target IDs.
"""
