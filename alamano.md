# Claude Code Task: Generate Story A Test Images

## Goal

Generate all images for Story A (The Runaway Wagon) in two versions per scene:
1. Background only (no characters, no key objects)
2. Full scene (background + all characters and objects, in HD)

Then downscale both to pixel art. Vincent will manually extract sprites from the full scene pixel art versions.

## Step 0: Explore the codebase

Find:
1. The existing image generation module (what API: Gemini, Replicate, etc.)
2. The existing prompting strategy for scene generation (how scenes are described to the model)
3. The pixel art downscale/quantization function if it exists
4. The scene manifest format in `data/study_scenes/A/`
5. Any style reference images or prompt templates already in use
6. Canvas dimensions, target pixel art resolution, palette constraints

Read `/mnt/user-data/outputs/tellimations_story_candidates.md` for Story A = Story 2 (The Runaway Wagon).

## Step 1: Create the scene generation input

Create `scripts/study_gen/story_a_scenes.json`:

```json
{
  "story_id": "A",
  "title": "The Runaway Wagon",
  "characters": {
    "boy": "a boy with curly hair",
    "grandmother": "an elderly grandmother wearing a green apron",
    "dog": "a small brown dog"
  },
  "scenes": [
    {
      "scene_number": 1,
      "title": "The Baking",
      "background_prompt": "Warm cozy kitchen interior, wooden table, window with afternoon light, shelves with jars, tiled floor. No people, no animals.",
      "full_scene_prompt": "Warm cozy kitchen interior. A boy with curly hair and an elderly grandmother in a green apron stand by a wooden table placing fresh cookies on a red wagon. A small brown dog watches from under the table. Afternoon light through the window.",
      "entities_in_scene": ["boy", "grandmother", "dog"],
      "key_objects": ["red wagon", "fresh cookies", "wooden table"]
    },
    {
      "scene_number": 2,
      "title": "The Escape",
      "background_prompt": "Front porch of a house with steps leading down to a steep residential street. Warm afternoon light. No people, no animals.",
      "full_scene_prompt": "Front porch of a house. An elderly grandmother in a green apron stands on the porch looking surprised. A boy with curly hair watches from the porch. A red wagon rolls down the steep street with cookies on it. A small brown dog is near the wagon.",
      "entities_in_scene": ["boy", "grandmother", "dog"],
      "key_objects": ["red wagon", "cookies", "steep street"]
    },
    {
      "scene_number": 3,
      "title": "The Chase",
      "background_prompt": "A residential street with sidewalk, a flower stand on the side, a man carrying boxes in the distance. No children, no animals.",
      "full_scene_prompt": "A boy with curly hair runs down a street chasing a red wagon. A small brown dog runs beside him barking. The wagon swerves past a man carrying boxes near a flower stand. In the background, an elderly grandmother in a green apron calls from a porch.",
      "entities_in_scene": ["boy", "grandmother", "dog"],
      "key_objects": ["red wagon", "flower stand", "boxes"]
    },
    {
      "scene_number": 4,
      "title": "The Crash",
      "background_prompt": "A market square with a stone fountain in the center, cobblestone ground, shop fronts around the edges. No people, no animals.",
      "full_scene_prompt": "A market square. A red wagon has crashed into a stone fountain, cookies scattered on the wet cobblestone ground. A boy with curly hair stands nearby out of breath. A small brown dog sits beside him. A shopkeeper woman comes out of a shop door looking at the mess.",
      "entities_in_scene": ["boy", "dog"],
      "key_objects": ["red wagon", "fountain", "scattered cookies", "shopkeeper"]
    },
    {
      "scene_number": 5,
      "title": "The Fix",
      "background_prompt": "A gentle hill with a residential street going upward, houses on both sides, warm late afternoon light. No people, no animals.",
      "full_scene_prompt": "A boy with curly hair and a small brown dog pull a dented red wagon up a gentle hill. The boy carries a bag of pastries. At the top of the hill, an elderly grandmother in a green apron smiles from the porch of a house.",
      "entities_in_scene": ["boy", "grandmother", "dog"],
      "key_objects": ["dented red wagon", "bag of pastries"]
    }
  ]
}
```

**Important**: Adapt these prompts to match the prompting style and format already used in the codebase. If the existing system uses specific style prefixes, aspect ratio keywords, negative prompts, or other conventions, apply them here.

## Step 2: Create the generation script

Create `scripts/study_gen/generate_story_images.py` (or .js, match project language).

The script does the following for each scene:

### 2a. Generate background-only image (HD)

Call the existing image generation API with `background_prompt`. Add style modifiers consistent with the existing pipeline (e.g., "children's storybook illustration style" or whatever the system already uses).

Save to: `data/study_gen/A/hd/scene_{N}_bg.png`

### 2b. Generate full scene image (HD)

Call the existing image generation API with `full_scene_prompt`.

**Character consistency**: if the existing system has a strategy for character consistency (e.g., passing a reference image, using a consistent character description block, using a style reference), use it. If not, at minimum:
- Use identical character descriptions across all 5 scenes
- If the API supports image-to-image or style reference, generate scene 1 first, then pass it as style reference for scenes 2-5

Save to: `data/study_gen/A/hd/scene_{N}_full.png`

### 2c. Downscale to pixel art

Apply the existing pixel art conversion if the codebase has one. If not, implement:
- Downscale to target resolution (check what the system uses, probably 64x64 or 128x128)
- Quantize palette to N colors (check existing config, probably 16-32 colors)
- Use nearest-neighbor interpolation (not bilinear, that creates blurry pixel art)

Save to:
- `data/study_gen/A/pixel/scene_{N}_bg.png`
- `data/study_gen/A/pixel/scene_{N}_full.png`

### 2d. Generate character reference sheets

For each character (boy, grandmother, dog), generate a standalone reference image in HD, then downscale. This is for Vincent to use as visual reference during manual extraction, and potentially for character consistency prompting.

Save to: `data/study_gen/A/refs/boy.png`, `data/study_gen/A/refs/grandmother.png`, `data/study_gen/A/refs/dog.png`

## Step 3: Extract scene manifest info

For each scene, read the existing scene JSON from `data/study_scenes/A/scene_{N}.json` (if it exists) and print its manifest format so Vincent can see what fields the system expects for entities, positions, layers, etc.

If no scene JSONs exist yet, create placeholder JSONs that match the expected format with the entity list and spatial relations from `story_a_scenes.json`.

## Step 4: Output structure

```
data/study_gen/A/
  hd/
    scene_1_bg.png
    scene_1_full.png
    scene_2_bg.png
    scene_2_full.png
    ...
  pixel/
    scene_1_bg.png
    scene_1_full.png
    scene_2_bg.png
    scene_2_full.png
    ...
  refs/
    boy.png
    grandmother.png
    dog.png
```

## Step 5: Run for Story A only

Run the script for Story A as a test. Add flags:
- `--story A` (default, only process Story A)
- `--scenes 1` (optional: generate only scene 1 for quick testing)
- `--skip-pixel` (optional: skip downscale step for faster iteration on HD prompts)
- `--hd-only` (optional: only generate HD, no pixel art)

## Constraints

- Reuse existing generation functions. Do not write new API clients from scratch.
- Match existing prompt style. Read the prompts already in the codebase before writing new ones.
- If the API has rate limits, add a delay between calls and print progress.
- If generation fails for a scene, log the error and continue to the next scene. Do not crash.
- Save all prompts used to a log file `data/study_gen/A/prompts_log.json` so Vincent can review and adjust them.
