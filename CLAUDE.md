# Tellimations: System Specification (v2)

## Project Overview

Tellimations is a research system that generates storytelling animations responsive to children's narration in real-time. When a child narrates a scene and makes errors or omissions (e.g., saying "the cat" instead of "fluffy orange cat on the fence"), the system animates visual elements to scaffold more complete and coherent narration.

The system is grounded in the SKILL narrative literacy framework and intuitive animation theories. Target age: 7-11 years.

See `docs/abstract.txt` for the research abstract, `docs/Tellimations.pdf` for the animation grammar catalog, and `docs/tellimations_architecture.html` for the visual architecture diagram.

## Core Design Decisions

### Pixel Art Engine (no image generation)

Scenes are rendered as pixel art using a custom canvas engine. The LLM generates **JavaScript drawing code** using a small API of geometric primitives (`circ`, `ellip`, `rect`, `tri`, `line`, `arc`). Each pixel in the buffer carries a **hierarchical entity ID** (e.g., `snowman.hat.brim`, `snowman.face.nose`), enabling targeted animations at any level of granularity.

This eliminates image generation models entirely. Benefits:
- Cost: text tokens instead of image generation (~$0.01/scene vs ~$1/scene)
- Speed: instant rendering, no image generation latency
- Consistency: entity definitions carry across scenes as code
- Manipulation: direct pixel-level access by entity ID for animations
- Verification: programmatic (parse code, check entity IDs and positions) instead of VLM

### Scene-by-Scene Generation (not batch)

The story is generated one scene at a time, not as a complete story upfront. Each scene generation takes into account:
- **Story state**: characters, setting, plot events so far
- **Student profile**: the child's error patterns from narration so far, which influences the narrative richness of future scenes (a key research contribution)
- **SKILL objectives**: learning goals for the session

After the child successfully narrates a scene, **3 candidate next scenes** are generated. The child chooses one by clicking, which determines the story direction. This gives the child agency while ensuring each branch is pedagogically informed by their error profile.

### On-the-fly Animation Generation (not pre-computed)

Animations are not pre-computed in a dispatch table. Instead, when an error is detected and no cached animation exists for that `[entity.subpart x error_type]` pair, the system generates animation code on-the-fly via LLM. Generated animation code is **cached** for reuse: if the same entity makes the same error again, the cached animation plays instantly.

### Single Model: Gemini 3 Flash

All LLM calls use Gemini 3 Flash with different `thinking_level` settings:

| Call | thinking_level | Latency context |
|------|---------------|-----------------|
| Scene generation (manifest + NEG + sprite code) | `medium` | Between scenes, blocking while 3 branches generated |
| 3 branch generation | `medium` | Same as above |
| Animation code generation | `medium` | On first occurrence of error type per entity; cached after |
| Transcription + discrepancy detection | `low` | Real-time, in narration loop, latency-critical |
| Post-session analytics | `medium` | Offline |

Gemini 3 Flash is chosen for all tasks because:
- Native audio understanding for transcription
- `thinking_level` control for latency tuning
- Sufficient code generation quality for the constrained primitive API
- Explicitly faster and cheaper than Pro, which is "not designed around prioritizing audio understanding" (Google docs)

Model ID: `gemini-3-flash-preview`

## Architecture

### Session Flow

```
LOGIN PAGE
  |
  +-- User enters Gemini API key + participant number
  +-- Click "Ok" -> main page
  |
STORY SELECTION ("Let's tell a story together!")
  |
  +-- Generate 3 random first scenes (parallel)
  +-- Display as clickable thumbnails (small pixel art previews)
  +-- Optional: "I want to see one more" button -> generates 1 more thumbnail
  +-- Child clicks a thumbnail
  |
STORY PAGE (Scene 1 displayed full-size)
  |
  +-- NARRATION LOOP (Scene 1)
  |     +-- Child speaks (push-to-talk)
  |     +-- Transcription + discrepancy detection
  |     +-- If error: generate/lookup animation -> play
  |     +-- Child self-corrects, speaks again
  |     +-- Update student_profile with error data
  |     +-- Loop until scene_progress >= threshold
  |
  +-- Scene 1 complete
  +-- Generate 3 candidate next scenes
  |     (informed by updated student_profile)
  +-- Display as clickable thumbnails below the current scene
  +-- Child clicks preferred scene
  +-- Render chosen Scene 2 full-size
  |
  +-- NARRATION LOOP (Scene 2)
  |     +-- ... same pattern ...
  |
  +-- ... repeat for N scenes ...
  |
  +-- POST-SESSION
        +-- Generate analytics report
```

### Pages

**Login page:** Simple form with two fields: Gemini API key (password input) and participant number (text input). The API key is stored client-side and sent with every WebSocket message. The participant number is used for logging and analytics. Click "Ok" to proceed.

**Story selection page:** Title "Let's tell a story together!" with 3 thumbnail previews of randomly generated first scenes. Each thumbnail is a small canvas (e.g., 140x90) rendering the scene's pixel art at 1.5x scale, downsampled from the full 560x360 by averaging 4x4 pixel blocks. Below each thumbnail, a short story hook (1 sentence from `branch_summary`). A button "I want to see one more" appends an additional thumbnail (new API call). Clicking a thumbnail transitions to the story page.

**Story page:** Full-size pixel art canvas (560x360 at 1.5x scale = 840x540). Push-to-talk button (space bar). After scene completion, 3 next-scene thumbnails appear below the canvas for the child to choose. Loading animation during generation.

### Module Breakdown

#### A. Scene Generation

**Single LLM call per scene.** Produces three outputs in one structured response:

1. **Scene Manifest**: entities with properties, positions, relations, actions
2. **NEG (Narrative Expectation Graph)**: integrated in the same call, not a separate module. The prompt instructs the LLM to verify SKILL coverage and regenerate internally if insufficient. If the self-check fails, the LLM enriches the scene within the same response.
3. **Sprite code**: JavaScript drawing code for **new or modified entities only**. Entities carried over unchanged from the previous scene reuse their existing code.

**For the initial 3 scenes** (story selection page), the LLM receives no story_state or student_profile. It generates a random, self-contained opening scene with varied characters, settings, and themes. Each call receives a different `seed_index` (1, 2, 3) to ensure variety.

**For subsequent scenes** (after narration), the full context is provided:
- `story_state`: cumulative narrative (characters, setting, plot events, entities with their sprite code)
- `student_profile`: error history (which error types are frequent, which entities cause trouble, improvement trends)
- `skill_objectives`: SKILL learning goals for the session
- Previous scene's manifest (for continuity)

**Output (structured JSON):**
```json
{
  "narrative_text": "The brave rabbit hopped onto the mossy rock...",
  "manifest": {
    "scene_id": "scene_03",
    "entities": [
      {
        "id": "rabbit_01",
        "type": "rabbit",
        "properties": {"color": "brown", "size": "small", "texture": "fluffy"},
        "position": {"x": 90, "y": 104, "spatial_ref": "on rock_01"},
        "emotion": "curious",
        "carried_over": true
      },
      {
        "id": "owl_01",
        "type": "owl",
        "properties": {"color": "grey", "size": "medium", "pattern": "speckled"},
        "position": {"x": 180, "y": 60, "spatial_ref": "on branch of tree_02"},
        "emotion": "wise",
        "carried_over": false
      }
    ],
    "relations": [],
    "actions": []
  },
  "neg": {
    "targets": [],
    "error_exclusions": [],
    "min_coverage": 0.7,
    "skill_coverage_check": "PASS"
  },
  "sprite_code": {
    "owl_01": "const eid = 'owl_01';\nellip(180, 65, 8, 10, 120, 120, 115, eid+'.body');\n..."
  },
  "carried_over_entities": ["rabbit_01", "rock_01", "tree_01", "tree_02"]
}
```

Entities with `carried_over: true` reuse their sprite code from the previous scene (stored in `story_state`). Only new entities get `sprite_code` entries.

#### B. Branch Generation (3 next scenes)

After the child completes a scene, **3 candidate next scenes** are generated. Each branch is a complete scene generation (manifest + NEG + sprite code), called in parallel (3 concurrent API calls).

**Input:** same as scene generation, plus the just-updated `student_profile`.

**Output per branch:** same structure as scene generation, plus:
```json
{
  "branch_summary": "The rabbit discovers a hidden cave behind the waterfall",
  "preview_entities": ["cave_01", "waterfall_01"]
}
```

The `branch_summary` is displayed to the child as a choice label. A small **thumbnail preview** is rendered from each branch's sprite code to give the child a visual hint.

**Latency:** This is blocking. The child waits while the 3 branches generate. With Flash `medium` and 3 parallel calls, expected latency is 3-6 seconds. A loading animation plays during this time.

#### C. Narration Loop (real-time)

Push-to-talk interaction:

- Child presses [space] to speak, releases to send
- If idle > 10s between presses: hesitation event, trigger omission animation for highest-priority unmet target

**Single LLM call per utterance** (Flash, `thinking_level: low`):

Input:
- Audio chunk
- Current scene's NEG
- Narration history
- Student profile (so the model understands the child's patterns)

Output (structured JSON):
```json
{
  "transcription": "the cat is on the...",
  "discrepancies": [
    {
      "type": "property_color",
      "entity_id": "cat_01",
      "sub_entity": "cat_01.body",
      "details": "Child said 'cat' without color descriptor 'orange'",
      "severity": 0.7
    }
  ],
  "scene_progress": 0.45,
  "satisfied_targets": ["t1_identity", "t1_spatial"],
  "updated_history": ["the brave rabbit...", "the cat is on the..."],
  "profile_updates": {
    "errors_this_scene": {"property_color": 1},
    "patterns": "Child consistently omits color descriptors"
  }
}
```

The `profile_updates` field feeds into `student_profile` for the next scene generation.

#### D. Animation System

**On error detection:**

1. Check animation cache: is there a cached animation for `[entity_id.sub_entity x error_type]`?
2. If cached: play immediately
3. If not cached: generate animation code via LLM call (Flash, `thinking_level: medium`)
4. Execute animation on pixel buffer
5. Cache the generated code for reuse

**Animation generation input:**
- `error_type`: from the animation grammar
- `entity_id` and `sub_entity`: which part to animate
- Entity's bounding box (computed from pixel buffer)
- Entity's current pixel data (colors, shape info)
- Scene context (other entities' positions, for relational animations)
- The animation grammar description for this error type

**Animation generation output:** JavaScript code that manipulates the pixel buffer:
```javascript
// color_pop for cat_01.body
// Desaturate all non-target pixels, brighten target
function animate(buf, PW, PH, t) {
  for (let i = 0; i < buf.length; i++) {
    if (buf[i].e.startsWith('cat_01.body')) {
      const glow = 0.7 + 0.3 * Math.sin(t * Math.PI * 5);
      buf[i].r = Math.min(255, buf[i]._r * (1 + glow * 0.5));
      // ...
    } else if (buf[i].e !== 'sky' && buf[i].e !== 'ground') {
      const L = buf[i]._r * 0.3 + buf[i]._g * 0.59 + buf[i]._b * 0.11;
      buf[i].r = L * 0.3;
      // ...
    }
  }
}
```

The `_r`, `_g`, `_b` fields store original colors (snapshot before animation). The function takes a normalized time `t` (0 to 1) and mutates the buffer. The engine calls it in a `requestAnimationFrame` loop.

**Animation cache structure:**
```
cache[entity_id][error_type] = {
  code: Function,
  duration_ms: 1200,
  generated_for: "cat_01.body"
}
```

#### E. Student Profile

Cumulative data structure updated after each utterance:

```json
{
  "session_id": "sess_001",
  "error_counts": {
    "property_color": 12,
    "spatial": 3,
    "quantity": 0,
    "action": 5,
    "omission": 8
  },
  "error_trend": {
    "property_color": "decreasing",
    "omission": "stable"
  },
  "difficult_entities": ["cat_01", "tree_02"],
  "strong_areas": ["identity", "quantity"],
  "scenes_completed": 4,
  "corrections_after_animation": 18,
  "total_utterances": 42
}
```

This profile is injected into:
- **Scene generation**: to create scenes rich in the error types the child struggles with
- **Branch generation**: each branch can emphasize different weak areas
- **Transcription**: so the model is primed to detect the child's typical errors
- **Post-session analytics**: for the SLP report

#### F. Post-session Analytics

**Input:** full session log (student_profile + per-scene transcripts + discrepancies + animations fired + outcomes).

**Output (Flash, `thinking_level: medium`):** SLP report with:
- Recurring error patterns and their evolution across scenes
- Which animation types were effective (child corrected after) vs. ineffective
- SKILL progress metrics
- Impact of student_profile-driven scene adaptation
- Recommendations for next session focus areas

## Pixel Art Engine

### Primitive API

The LLM generates sprite code using these functions. All coordinates in pixels, colors as r,g,b integers 0-255:

```javascript
px(x, y, r, g, b, entityId)                           // single pixel
rect(x, y, width, height, r, g, b, entityId)           // filled rectangle
circ(cx, cy, radius, r, g, b, entityId)                 // filled circle
ellip(cx, cy, rx, ry, r, g, b, entityId)                // filled ellipse
tri(x1,y1, x2,y2, x3,y3, r, g, b, entityId)            // filled triangle
line(x1,y1, x2,y2, r, g, b, entityId)                   // 1px line (Bresenham)
thickLine(x1,y1, x2,y2, width, r, g, b, entityId)      // thick line
arc(cx, cy, radius, startAngle, endAngle, r, g, b, entityId)  // arc outline
```

### Pixel Buffer

```javascript
// Each pixel:
{ r: 0-255, g: 0-255, b: 0-255, e: "entity.sub.part" }

// Buffer: flat array, width * height
buf[y * PW + x] = { r, g, b, e }
```

Canvas size: 560 x 360 pixels, rendered at 1.5x scale (840 x 540 on screen) with `image-rendering: pixelated`.

### Hierarchical Entity IDs

Entity IDs use dot-separated hierarchical naming:
- `rabbit_01` (root: selects everything)
- `rabbit_01.body` (torso)
- `rabbit_01.head` (head)
- `rabbit_01.head.ears.left` (left ear)
- `rabbit_01.head.eyes.left` (left eye)

Prefix matching enables targeting at any level:
- `getPixelsForPrefix("rabbit_01")` returns ALL rabbit pixels
- `getPixelsForPrefix("rabbit_01.head")` returns head + ears + eyes
- `getPixelsForPrefix("rabbit_01.head.ears.left")` returns only the left ear

### Entity Persistence Across Scenes

Entities that persist between scenes carry their sprite code forward in `story_state`. When rendering a new scene:

1. Clear the pixel buffer
2. Draw background (sky + ground, generated per scene or carried over)
3. For each carried-over entity: execute stored sprite code with updated position/emotion parameters
4. For each new entity: execute freshly generated sprite code
5. Render to canvas

Position changes for carried-over entities are handled by the LLM adjusting `cx`, `cy` parameters in the manifest while reusing the same drawing code.

## Animation Grammar

Organized by error type. See `docs/Tellimations.pdf` for full visual catalog.

### Spatial Errors (prepositions, location)
- **Transparency Reveal**: occluding object becomes translucent to show actual spatial relationship
- **Settle**: object sinks into its actual position with soft bounce

### Property Errors (adjectives, attributes)
- **Color Pop**: desaturation of everything except target to emphasize its color
- **Physiological Tell**: small involuntary vital sign (blink, tear, tail wag) revealing actual state
- **Scale Strain**: object attempts claimed size, fails, returns to actual size with wobble
- **Weight Response**: environmental reaction (surface sag for heavy, drift for light)
- **Emanation**: particle sprites showing actual property (steam=hot, frost=cold, sparkle=new, dust=old)

### Temporal Errors (tense, time)
- **Afterimage/Rewind**: ghost-duplicate in previous action pose fades while character remains in current state
- **Anticipation Hold**: character frozen in "about to act" pose showing potential energy
- **Melting**: visual distortion indicating tense inconsistency

### Identity Errors (nouns, naming)
- **Decomposition**: entity briefly disassembles into constituent parts to highlight descriptors
- **Decomposition (full disintegration)**: for pronoun-antecedent errors, entity pulled in two directions
- **Vibrating Pulse/Jelloing**: gelatinous vibration for categorical instability (noun-verb confusion)

### Quantity Errors (count, pluralization)
- **Sequential Pulse**: objects glow in sequence creating visual count
- **Isolation**: surroundings dim while single object remains sharp, emphasizing singularity
- **Domino Effect**: characters wobble/fall to show plurality cannot be reduced to singular

### Action Errors (verbs)
- **Characteristic Action**: object performs brief defining behavior asserting true identity
- **Motion Line**: directional streaks showing actual direction and speed

### Relational Errors (between entities)
- **Drift**: objects attracted/repelled showing actual relationship
- **Comparison Slide**: two objects slide together for direct visual comparison

### State & Existence Errors
- **Ghost Outline**: faint dotted outline where claimed object should be, dissolves to nothing

### Adverb/Manner Errors
- **Speed Warp**: distorts perceived speed (slow becomes syrupy, fast becomes blur)

### Redundancy/Double Negative Errors
- **The Bonk**: character hits redundant word, correction stars appear
- **Burying**: redundant word is buried, hole implies missing element

### Omission Errors
- **Sprouting**: natural marker grows where word is missing

### Temporal/Scope Errors
- **Leaking**: visual leak indicating something uncontrolled (tense errors, adjective overspill, run-on sentences)

### Error Type Enum

```
SPATIAL, PROPERTY_COLOR, PROPERTY_SIZE, PROPERTY_WEIGHT, PROPERTY_TEMPERATURE,
PROPERTY_STATE, TEMPORAL, IDENTITY, QUANTITY, ACTION, RELATIONAL, EXISTENCE,
MANNER, REDUNDANCY, OMISSION
```

### Error Exclusion Rules (algorithmic, no LLM)

For each entity in a scene, certain error types are provably impossible:
- Entity is unique in scene: exclude QUANTITY
- Entity has no distinctive color property: exclude PROPERTY_COLOR
- Entity is static (no action in manifest): exclude MANNER, ACTION
- Entity has no weight property: exclude PROPERTY_WEIGHT
- Entity has no temperature property: exclude PROPERTY_TEMPERATURE
- Entity is not in a spatial relation: exclude SPATIAL
- Background/decoration entity: exclude IDENTITY

These exclusion rules are computed algorithmically from the manifest and used to filter discrepancies and skip unnecessary animation generation.

## Key Data Structures

### Story State (cumulative, passed to each scene generation)

```json
{
  "session_id": "sess_001",
  "participant_id": "P012",
  "skill_objectives": ["descriptive_adjectives", "spatial_prepositions"],
  "scenes": [
    {
      "scene_id": "scene_01",
      "narrative_text": "...",
      "manifest": {},
      "neg": {}
    }
  ],
  "active_entities": {
    "rabbit_01": {
      "type": "rabbit",
      "sprite_code": "// ...",
      "first_appeared": "scene_01",
      "last_position": {"x": 90, "y": 104}
    }
  }
}
```

### Animation Cache

```json
{
  "rabbit_01.body": {
    "property_color": {
      "code": "function animate(buf, PW, PH, t) { ... }",
      "duration_ms": 1200
    }
  }
}
```

### Student Profile

```json
{
  "error_counts": {"property_color": 12, "spatial": 3, "omission": 8},
  "error_trend": {"property_color": "decreasing", "omission": "stable"},
  "difficult_entities": ["cat_01", "tree_02"],
  "strong_areas": ["identity", "quantity"],
  "scenes_completed": 4,
  "corrections_after_animation": 18,
  "total_utterances": 42
}
```

## Project Structure

```
tellimations/
+-- CLAUDE.md
+-- docs/
|   +-- abstract.txt
|   +-- Tellimations.pdf
|   +-- tellimations_architecture.html
+-- src/
|   +-- models/
|   |   +-- scene.py              # SceneManifest, Entity, Relation, Action
|   |   +-- neg.py                # NEG, NarrativeTarget, ErrorExclusion
|   |   +-- story_state.py        # StoryState, ActiveEntity, cumulative state
|   |   +-- student_profile.py    # StudentProfile, error tracking
|   |   +-- animation_cache.py    # AnimationCache, CachedAnimation
|   +-- engine/
|   |   +-- pixel_buffer.py       # PixelBuffer class, primitive API
|   |   +-- renderer.py           # Canvas rendering, scene composition
|   |   +-- entity_registry.py    # Hierarchical entity tracking, prefix queries
|   |   +-- animation_runner.py   # Execute animation code, requestAnimationFrame loop
|   +-- generation/
|   |   +-- scene_generator.py    # Scene generation (manifest + NEG + sprites)
|   |   +-- branch_generator.py   # Generate 3 candidate next scenes
|   |   +-- animation_generator.py # On-the-fly animation code generation
|   |   +-- prompts/
|   |       +-- scene_prompt.py       # System prompt for scene generation
|   |       +-- sprite_prompt.py      # System prompt for sprite code
|   |       +-- animation_prompt.py   # System prompt for animation code
|   |       +-- transcription_prompt.py # System prompt for narration analysis
|   +-- narration/
|   |   +-- transcription.py      # Audio transcription + discrepancy detection
|   |   +-- dispatcher.py         # Error -> animation lookup/generation
|   |   +-- narration_loop.py     # Main real-time loop orchestrator
|   |   +-- error_exclusions.py   # Algorithmic exclusion rules
|   +-- analytics/
|   |   +-- session_report.py     # Post-session SLP report generation
|   +-- ui/
|       +-- app.py                # Web server (FastAPI)
|       +-- static/
|       |   +-- engine.js         # Pixel buffer + primitives + renderer (JS)
|       |   +-- animations.js     # Animation execution engine (JS)
|       |   +-- narration.js      # Push-to-talk + WebSocket client (JS)
|       |   +-- scene_picker.js   # Thumbnail choice UI (JS, used on both pages)
|       |   +-- style.css         # Shared styles
|       +-- templates/
|           +-- login.html        # API key + participant number
|           +-- selection.html    # "Let's tell a story together!" + thumbnails
|           +-- story.html        # Full-size scene + narration UI
+-- tests/
+-- config/
|   +-- skill_framework.yaml
+-- requirements.txt
```

Note: the pixel engine and animation system run **client-side in JavaScript**. The Python backend handles LLM calls and serves data via WebSocket. Sprite code and animation code are generated server-side (Python calls Gemini API) and sent to the client for execution.
