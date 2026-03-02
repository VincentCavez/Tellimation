"""Prompts for the image-based sprite generation pipeline.

This module provides prompt templates for:
  - ENTITY_IMAGE_PROMPT: Gemini 3 Pro Image — generate one entity on red chroma key
  - BACKGROUND_IMAGE_PROMPT: Gemini 3 Pro Image — generate full scene background
  - MASK_SYSTEM_PROMPT / MASK_USER_PROMPT: Gemini 3 Flash — assign sub-entity IDs to entity pixels
  - BG_MASK_SYSTEM_PROMPT / BG_MASK_USER_PROMPT: Gemini 3 Flash — assign sub-entity IDs to background pixels
"""

# ---------------------------------------------------------------------------
# Entity image generation (Gemini 3 Pro Image, one per entity)
# ---------------------------------------------------------------------------

ENTITY_IMAGE_PROMPT = """\
Create an illustration of the following character/object on a \
SOLID BRIGHT RED (#FF0000) background. The red must be perfectly uniform \
— no gradients, no shading, no variation. Pure #FF0000 everywhere except the subject.

## Subject
{entity_description}

## Style Guidelines
- **Clean children's illustration style**: smooth shapes, clear outlines, rich colors. \
  Warm, friendly, suitable for ages 7-11.
- **Rich color palette**: smooth shading with dark shadows on edges, mid-tones in \
  the middle, bright highlights. No flat/blocky fills.
- **Detailed**: clearly distinct body parts (head, body, limbs, tail, ears, eyes). \
  Eyes should have at least 2 colors (pupil + shine).
- **Side view** (like a 2D storybook): flat side profile, no 3D perspective.
- **The subject should fill most of the image** — center it, leave only a small \
  margin of red around it.
- **NO other elements**: no ground, no shadow, no text, no decorations. \
  ONLY the subject on solid red.

## ISOLATION — This sprite is generated COMPLETELY ALONE
- Generate ONLY the described entity. Nothing else exists in this image.
- Generate EXACTLY ONE instance of this character/object. Never draw duplicates, \
  mirrors, reflections, shadows, or multiple copies. The image must contain a SINGLE entity.
- Do NOT draw any environmental context: no ground, no trees, no rocks, no bark, \
  no branches, no roots, no walls, no surfaces, no other objects.
- If the description mentions a pose "against" or "on" something, IGNORE the surface — \
  only draw the entity's body in that posture.
- If the subject is "beside" or "on top of" something, do NOT include that something.
- The entity floats on the red background. There is NOTHING for it to lean on, sit on, \
  or attach to. Draw only the entity's own body/form.

## CRITICAL
- The background MUST be perfectly solid #FF0000 (bright red).
- Any pixel that is NOT part of the sprite must be exactly #FF0000.
- The sprite should NOT contain any bright red (#FF0000) pixels.
"""

# ---------------------------------------------------------------------------
# Background image generation (Gemini 3 Pro Image, scene background)
# ---------------------------------------------------------------------------

BACKGROUND_IMAGE_PROMPT = """\
Create a background scene illustration. This is ONLY the background — \
no characters, no objects, no entities. Just the environment.

## Scene
{scene_description}

## Style Guidelines
- **Clean children's illustration style**: smooth gradients, clear shapes, \
  warm and friendly. Suitable for ages 7-11.
- **Flat side-view** (like a 2D storybook): no perspective, no 3/4 angle.
- **Horizon at roughly the middle** of the image. The sky and ground should \
  BOTH contain visual detail — the sky should have clouds, color gradients, or \
  atmospheric elements, NOT be a flat solid color. The ground should have texture \
  and depth. Fill the ENTIRE image with illustrated content.
- **Rich atmospheric gradients**: sky should have color variation (lighter at \
  horizon, darker above). Ground should have texture (grass, sand, stone, etc.).
- **Atmospheric details**: clouds, stars, sun glow, distant mountains, etc.
- **NO characters or objects**: this is purely the background environment.
- **Color richness**: use many shades for the sky and ground to create depth.
"""

# ---------------------------------------------------------------------------
# Mask generation (Gemini 3 Flash, text-only with image)
# ---------------------------------------------------------------------------

MASK_SYSTEM_PROMPT = """\
You are a sprite mask generator for a pixel art animation engine. Your job is to \
assign hierarchical entity IDs to pixels in a sprite image using **run-length encoding (RLE)**.

You receive:
1. An image of a single pixel art sprite (the entity)
2. The entity ID and type
3. The sprite dimensions (width x height)

## Output Format: RLE (Run-Length Encoding)

Return a JSON object with a "mask" array of **runs**. Each run is a 2-element array:
`[sub_entity_id_or_null, pixel_count]`

Runs are in **row-major order** (left to right, top to bottom). The sum of all \
pixel_count values MUST equal exactly {width} × {height} = {total_pixels}.

Example for a 4×4 sprite (16 pixels total):
```json
{{"mask": [[null, 5], ["{eid}.head", 3], [null, 1], ["{eid}.body", 4], [null, 3]]}}
```
This means: 5 transparent pixels, then 3 head pixels, 1 transparent, 4 body pixels, 3 transparent.

## Hierarchical Entity ID Rules

Every character entity MUST have at least these sub-entity categories:
- `{eid}` (root — for pixels that don't clearly belong to a specific part)
- `{eid}.body` (torso/main body)
- `{eid}.body.belly` (lighter belly/chest area, if visible)
- `{eid}.head` (head)
- `{eid}.head.ears.left` / `{eid}.head.ears.right`
- `{eid}.head.eyes.left` / `{eid}.head.eyes.right`
- `{eid}.head.nose` / `{eid}.head.mouth`
- `{eid}.legs.front_left` / `{eid}.legs.front_right`
- `{eid}.legs.back_left` / `{eid}.legs.back_right`
- `{eid}.tail` (if applicable)

Non-character entities (trees, rocks, houses) should have at least 4 sub-entities:
- `{eid}.trunk`, `{eid}.canopy`, `{eid}.canopy.highlight`, `{eid}.roots` (tree)
- `{eid}.base`, `{eid}.surface`, `{eid}.moss`, `{eid}.shadow` (rock)
- `{eid}.cap`, `{eid}.cap.spots`, `{eid}.stem` (mushroom)

## How to Assign

Look at the sprite image and identify which body part each visible pixel belongs to:
- Top portion → head, ears, eyes
- Upper-middle → neck, upper body
- Middle → body, belly
- Lower → legs, feet
- Sides → arms, wings, tail (depending on pose)
- Fine details → eyes, nose, mouth, markings

Scan the sprite in row-major order (row 0 left-to-right, then row 1, etc.). \
Group consecutive pixels with the same sub-entity ID into one run. \
Red background pixels are null.

## CRITICAL RULES
- The sum of all counts MUST equal exactly {total_pixels}.
- Every visible (non-red) pixel MUST have a sub-entity ID (not null).
- Every transparent (red background) pixel MUST be null.
- Consecutive pixels with the SAME ID must be merged into a single run.
"""

MASK_USER_PROMPT = """\
Assign sub-entity IDs for this sprite using RLE (run-length encoding).

Entity ID: **{entity_id}**
Entity type: **{entity_type}**
Sprite dimensions: {width} x {height} ({total_pixels} pixels total)

The attached image shows the sprite on a red chroma-key background. \
Red pixels are transparent (null). All other pixels need a sub-entity ID.

Return the mask as RLE: {{"mask": [[id_or_null, count], [id_or_null, count], ...]}}
The sum of all counts MUST be exactly {total_pixels}.
"""

# ---------------------------------------------------------------------------
# Background mask generation (Gemini 3 Flash, landscape segmentation)
# ---------------------------------------------------------------------------

BG_MASK_SYSTEM_PROMPT = """\
You are a background mask generator for a pixel art animation engine. Your job is to \
assign hierarchical entity IDs to EVERY pixel in a scene background image using \
**run-length encoding (RLE)**.

You receive:
1. An image of a full scene background (landscape / environment)
2. The image dimensions (width × height)

## Output Format: RLE (Run-Length Encoding)

Return a JSON object with a "mask" array of **runs**. Each run is a 2-element array:
`[sub_entity_id, pixel_count]`

Runs are in **row-major order** (left to right, top to bottom). The sum of all \
pixel_count values MUST equal exactly {width} × {height} = {total_pixels}.

Example for a 4×4 background (16 pixels total):
```json
{{"mask": [["bg.sky", 8], ["bg.mountain", 4], ["bg.ground.grass", 4]]}}
```

## Hierarchical Entity ID Rules

ALL IDs must start with `bg.` — the root prefix.

**Sky region:**
- `bg.sky` — plain sky area (gradients, solid color)
- `bg.sky.clouds` — clouds
- `bg.sky.sun` or `bg.sky.moon` — celestial bodies
- `bg.sky.stars` — stars (if visible)

**Ground / terrain:**
- `bg.ground` — generic ground
- `bg.ground.grass` — grassy areas
- `bg.ground.path` — paths, roads
- `bg.ground.sand` — sandy areas
- `bg.ground.snow` — snowy ground

**Natural features:**
- `bg.mountain` — mountains, hills
- `bg.water` — water bodies (lakes, rivers, ocean)
- `bg.water.surface` — water surface reflections
- `bg.trees` — distant trees / forest
- `bg.trees.canopy` — tree canopy area
- `bg.trees.trunks` — tree trunk area
- `bg.rocks` — rock formations

**Structures:**
- `bg.building` — buildings, houses
- `bg.fence` — fences, walls
- `bg.bridge` — bridges

## How to Assign

Look at the background image and identify distinct landscape regions:
- Top portion → typically sky (clouds, sun, moon, stars)
- Middle → mountains, hills, distant trees, buildings
- Bottom → ground (grass, path, sand, water, rocks)
- Transitions belong to the region they visually resemble most

Scan in row-major order (row 0 left-to-right, then row 1, etc.). \
Group consecutive pixels with the same sub-entity ID into one run.

## CRITICAL RULES
- The sum of all counts MUST equal exactly {total_pixels}.
- EVERY pixel MUST have a sub-entity ID — no null values. Backgrounds have no transparency.
- All IDs MUST start with the `bg.` prefix.
- Consecutive pixels with the SAME ID must be merged into a single run.
- Use at least 3 distinct sub-entity IDs (backgrounds always have sky + ground + something).
- Prefer broad semantic regions over pixel-level precision.
"""

BG_MASK_USER_PROMPT = """\
Assign sub-entity IDs for this background image using RLE (run-length encoding).

Image dimensions: {width} × {height} ({total_pixels} pixels total)

The attached image shows the full scene background (landscape / environment). \
There are NO transparent pixels — every pixel belongs to a landscape region.

Return the mask as RLE: {{"mask": [["bg.xxx", count], ["bg.yyy", count], ...]}}
The sum of all counts MUST be exactly {total_pixels}.
"""
