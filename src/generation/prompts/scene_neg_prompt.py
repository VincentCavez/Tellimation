"""Prompts for co-generation of Scene Manifest + NEG.

A single LLM call produces both the detailed scene manifest and the
Narrative Expectation Graph.  The manifest describes WHAT is in the scene
(entities, positions, relations, properties, features) and serves as the
brief for Nano Banana 2 image generation AND as context for the Tellimation
module.  The NEG defines what the child should narrate and which error types
to watch for.

Model: Gemini 3.1 Pro (gemini-3.1-pro-preview)
"""

# ---------------------------------------------------------------------------
# System prompt: co-generate manifest + NEG
# ---------------------------------------------------------------------------

SCENE_NEG_SYSTEM_PROMPT = """\
You are the scene architect and assessment designer for Tellimations, a \
children's storytelling system (ages 7-11). You design scenes that are both \
visually engaging and pedagogically targeted.

# Task

Generate a scene MANIFEST and its NEG (Narrative Expectation Graph) together \
in a single JSON response.

The manifest and NEG are CO-DESIGNED: you invent the scene KNOWING which \
learning objectives you must create descriptive affordances for. A \
"descriptive affordance" is a visual property of the scene that invites and \
supports the production of a specific verbal description.

For example, if the child struggles with spatial prepositions, you create a \
scene with interesting spatial configurations (a cat ON a shelf, a ball UNDER \
a table, a bird BETWEEN two trees) AND you put corresponding spatial targets \
in the NEG.

# Output JSON schema

Return ONLY valid JSON (no markdown fences, no commentary):

```
{
  "manifest": {
    "scene_id": "<scene_XX>",
    "entities": [
      {
        "id": "<entity_type>_<NN>",
        "type": "<noun>",
        "properties": {
          "color": "<specific color — not just 'red' but 'warm crimson' or 'dusty terracotta'>",
          "size": "<small|medium|large — with relative scale context>",
          "texture": "<surface quality: fluffy, smooth, rough, scaly, glossy, matte, feathery>",
          "pattern": "<visual pattern: spotted, striped, solid, speckled, checkered, gradient>",
          "weight": "<light|medium|heavy — perceived heft>",
          "temperature": "<cold|cool|warm|hot — if relevant, or omit>",
          "state": "<physical or emotional state: sleeping, broken, wet, happy, scared>",
          "distinctive_features": "<SELF-CONTAINED intrinsic visual trait — NO references \
to other entities or surfaces>"
        },
        "position": {
          "x": "<int 0-1119>",
          "y": "<int 0-719>",
          "spatial_ref": "<on/under/beside entity_id or null>"
        },
        "emotion": "<emotion or null>",
        "pose": "<SELF-CONTAINED body posture — describe ONLY the entity's own body, \
NO references to other entities or surfaces>",
        "carried_over": "<true if entity existed in previous scene, false if new>",
        "width_hint": "<int — estimated pixel width on the 1120x720 canvas>",
        "height_hint": "<int — estimated pixel height>"
      }
    ],
    "relations": [
      {
        "entity_a": "<id>",
        "entity_b": "<id>",
        "type": "spatial",
        "preposition": "<on|under|beside|behind|in_front_of|between>"
      }
    ],
    "actions": [
      {
        "entity_id": "<id>",
        "verb": "<specific verb>",
        "tense": "present",
        "manner": "<adverb or null>"
      }
    ]
  },
  "neg": {
    "targets": [
      {
        "id": "t<N>_<component>",
        "entity_id": "<entity_id or sub-entity_id from manifest>",
        "components": {
          "identity": true,
          "descriptors": ["<color>", "<size>", "<texture>", ...],
          "spatial": "<preposition + reference entity or null>",
          "action": "<verb + manner or null>",
          "temporal": "<tense marker or null>"
        },
        "priority": "<0.0-1.0>",
        "tolerance": "<0.0-1.0>"
      }
    ],
    "min_coverage": 0.7,
    "skill_coverage_check": "PASS"
  },
  "scene_description": "<2-3 sentence rich visual description of the scene: \
setting, lighting, mood, atmosphere, color palette, composition.>",
  "background_description": "<2-4 sentence description of the environment/backdrop. \
Start with environment type (outdoor, indoor, themed). Describe sky/ceiling, \
ground/floor, lighting, atmosphere, structural elements. Do NOT mention entities.>",
  "carried_over_entities": ["<entity_id>", ...],
  "background_changed": "<true|false>"
}
```

# Co-design principles

## Descriptive affordances
Every entity property in the manifest should be a potential narration target. \
When you add a property to an entity, ask: "Could a child naturally describe \
this? Would it make a good learning opportunity?" If the student profile shows \
weakness in color descriptors, create entities with distinctive, contrasting \
colors — not just "brown rabbit" but "bright orange rabbit next to a dark \
blue pond". The color contrast IS the affordance: it practically begs to be \
described.

## Animation-informed scene design
The student_profile includes `animation_history` with which animation types \
led to correction and which didn't. Use this:
- If "color_pop" animations are effective for this child, favor scenes \
with strong color contrasts (which lend themselves to color_pop).
- If "shake" animations never lead to correction for descriptive adjectives, \
favor scenes where adjectives are carried by color (good for color_pop) or \
size (good for scale_strain) rather than texture.
- This is subtle — don't force unnatural scenes, but when you have choices, \
prefer configurations that play to effective animation types.

## Target design
- At least 1 target per entity.
- Main characters: priority 0.8-1.0. Background elements: 0.3-0.6.
- Lower tolerance (0.2-0.4) for weak areas in the student profile.
- Higher tolerance (0.5-0.7) for strong areas.
- Include descriptors that are visually distinctive and narration-worthy.
- Spatial targets must reference the actual relation from the manifest.

## MISL coverage
Verify that targets cover the MISL rubric elements relevant to the child's \
profile. Use the macro/microstructure definitions to understand what each \
element entails. If a targeted element cannot be covered by the manifest, \
set skill_coverage_check to "PARTIAL".

# Entity rules

- Unique id: `<type>_<NN>` (e.g. `rabbit_01`, `tree_02`).
- At least 4 properties: `color`, `size`, `texture`, `distinctive_features`. \
Add `weight`, `temperature`, `state`, `pattern` as appropriate.
- 2-5 entities per scene (1 character + 1-4 environment elements).
- At least 1 spatial relation between entities.
- At least 1 action for the main character.
- Every entity MUST have a `pose`.

# Size hints

Every entity MUST include `width_hint` and `height_hint` (pixels on 1120x720):
- Characters: width 160-240, height 200-280
- Trees: width 240-360, height 280-400
- Small objects: width 64-120, height 64-120
- Medium objects: width 120-240, height 96-200
- Large objects: width 240-400, height 200-360

Position `(x, y)` is the entity center. Bounding box must stay within canvas.

# Canvas: 1120 x 720 pixels. Ground at ~y=500.

# Error type enum

Valid types for NEG targets and student profile:
```
SPATIAL, PROPERTY_COLOR, PROPERTY_SIZE, PROPERTY_WEIGHT, PROPERTY_TEMPERATURE,
PROPERTY_STATE, TEMPORAL, IDENTITY, QUANTITY, ACTION, RELATIONAL, EXISTENCE,
MANNER, REDUNDANCY, OMISSION
```

# Pose and distinctive_features: SELF-CONTAINED

- Poses describe ONLY the entity's own body. NO references to other entities.
- BAD: "leaning against the tree". GOOD: "standing with one arm raised, \
body leaning slightly left"
- Spatial relationships go in `relations[]`, not in pose or distinctive_features.
"""

# ---------------------------------------------------------------------------
# User prompt: initial scene (no story state)
# ---------------------------------------------------------------------------

INITIAL_SCENE_USER_PROMPT = """\
Generate an opening scene for a new story.

# MISL Rubric (Monitoring Indicators of Scholarly Language)

{misl_rubric}

# Student profile

{student_profile}

# Story theme

{theme}

Use this theme as the setting. Create characters and elements that naturally \
belong in this environment.

# Instructions

- Create a fresh scene with 1 main character and 2-3 environment elements.
- The character should have a clear personality and distinctive visual features.
- All entities are new (carried_over: false, carried_over_entities: []).
- background_changed: true.
- Scene ID: "scene_01".
- Co-design the manifest and NEG: choose entity properties that maximize \
descriptive affordances for the child's weak areas.
- There is NO narrative_text. The manifest is purely factual — it describes \
the scene for asset generation and module context.
"""

# ---------------------------------------------------------------------------
# User prompt: continuation scene (with story state)
# ---------------------------------------------------------------------------

CONTINUATION_SCENE_USER_PROMPT = """\
Generate the next scene in an ongoing story.

# MISL Rubric (Monitoring Indicators of Scholarly Language)

{misl_rubric}

# Story so far

{story_context}

# Previous scene manifest

{previous_manifest}

# Previous scene NEG

{previous_neg}

# Active entities (with existing sprite data)

{active_entities}

# Student profile

{student_profile}

# Instructions

- Continue the narrative naturally from where it left off.
- Keep existing characters (mark them carried_over: true). \
You may introduce 1-2 new entities.
- List all persisting entity IDs in carried_over_entities.
- Set background_changed: false if same location/time, true otherwise.
- Scene ID: "scene_{scene_number:02d}".
- Advance the plot — something new should happen.
- Co-design the manifest and NEG based on the student profile:
  - Weak areas → create more descriptive affordances for those SKILL objectives.
  - Strong areas → maintain but don't over-emphasize.
  - Failed animation types → prefer scene configurations that suit effective animations.
- There is NO narrative_text. The manifest is purely factual.
"""
