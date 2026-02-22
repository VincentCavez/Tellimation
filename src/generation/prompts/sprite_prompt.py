"""Prompts for the image-based sprite generation pipeline.

This module provides prompt templates for:
  - ENTITY_IMAGE_PROMPT: Gemini 2.5 Flash Image — generate one entity on red chroma key
  - BACKGROUND_IMAGE_PROMPT: Gemini 2.5 Flash Image — generate full 560x360 background
  - MASK_SYSTEM_PROMPT / MASK_USER_PROMPT: Gemini 3 Flash — assign sub-entity IDs to pixels
"""

# ---------------------------------------------------------------------------
# Entity image generation (Gemini 2.5 Flash Image, one per entity)
# ---------------------------------------------------------------------------

ENTITY_IMAGE_PROMPT = """\
Create a single pixel art sprite of the following character/object on a \
SOLID BRIGHT RED (#FF0000) background. The red must be perfectly uniform \
— no gradients, no shading, no variation. Pure #FF0000 everywhere except the sprite.

## Subject
{entity_description}

## Style Guidelines
- **Classic pixel art style**: chunky, blocky sprite with visible individual pixels. \
  Think SNES / GBA / Stardew Valley / Celeste sprites.
- **Rich color palette**: use 8-15 distinct colors with smooth shading — dark \
  shadows on edges, mid-tones in the middle, bright highlights.
- **Detailed**: clearly distinct body parts (head, body, limbs, tail, ears, eyes). \
  Eyes should have at least 2 colors (pupil + shine).
- **Side view** (like a 2D platformer): flat side profile, no 3D perspective.
- **The sprite should fill most of the image** — center it, leave only a small \
  margin of red around it.
- **NO other elements**: no ground, no shadow, no text, no decorations. \
  ONLY the sprite on solid red.

## ISOLATION — This sprite is generated COMPLETELY ALONE
- Generate ONLY the described entity. Nothing else exists in this image.
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
# Background image generation (Gemini 2.5 Flash Image, 560x360 scene)
# ---------------------------------------------------------------------------

BACKGROUND_IMAGE_PROMPT = """\
Create a pixel art background scene illustration. This is ONLY the background — \
no characters, no objects, no entities. Just the environment.

## Scene
{scene_description}

## Style Guidelines
- **Classic pixel art style**: chunky, blocky pixels, SNES / GBA era aesthetic.
- **Flat side-view** (like a 2D platformer): no perspective, no 3/4 angle.
- **Ground line at ~60% from top**. Sky above, ground below.
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
