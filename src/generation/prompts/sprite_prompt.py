"""Prompts for the image-based sprite generation pipeline.

Exports:
  ENTITY_IMAGE_PROMPT — Nano Banana 2 — generate one entity on magenta chroma key
  BACKGROUND_IMAGE_PROMPT — Nano Banana 2 — generate full scene background
"""

# ---------------------------------------------------------------------------
# Entity image generation (Nano Banana 2, one per entity)
# ---------------------------------------------------------------------------

ENTITY_IMAGE_PROMPT = """\
Create an illustration of the following character/object on a \
SOLID MAGENTA (#FF00FF) background. The magenta must be perfectly uniform \
— no gradients, no shading, no variation. Pure #FF00FF everywhere except the subject.

## Subject
{entity_description}

## Style Guidelines
- **Clean children's illustration style**: smooth shapes, clear outlines, rich colors. \
  Warm, friendly, suitable for ages 7-11.
- **Rich color palette**: smooth shading with dark shadows on edges, mid-tones in \
  the middle, bright highlights. No flat/blocky fills.
- **Detailed**: clearly distinct body parts (head, body, limbs, tail, ears, eyes). \
  Eyes should have at least 2 colors (pupil + shine).
- **HUMANIZATION RULE — CRITICAL**: \
  * If the subject is a CHARACTER or LIVING CREATURE (animal, person, creature), \
    it MAY have eyes, mouth, expressions, and humanoid traits. \
  * If the subject is an INANIMATE OBJECT (snowball, rock, tree, furniture, vehicle, \
    food, tool, etc.), it MUST NOT have eyes, mouth, faces, or human expressions. \
    Draw it realistically — a snowball is a plain white sphere, a rock is a plain stone, \
    a tree has no face. NO ANTHROPOMORPHISM for non-living things.
- **Side view** (like a 2D storybook): flat side profile, no 3D perspective.
- **ORIENTATION IS CRITICAL**: If the description says "FACING LEFT", the character \
  MUST look toward the LEFT side of the image (its nose/face points LEFT). \
  If it says "FACING RIGHT", the character MUST look toward the RIGHT side \
  (its nose/face points RIGHT). Getting this wrong breaks the scene composition.
- **The subject should fill most of the image** — center it, leave only a small \
  margin of magenta around it.
- **NO other elements**: no ground, no shadow, no text, no decorations. \
  ONLY the subject on solid magenta.

## ISOLATION — This sprite is generated COMPLETELY ALONE
- Generate ONLY the described entity. Nothing else exists in this image.
- Generate EXACTLY ONE instance of this character/object. Never draw duplicates, \
  mirrors, reflections, shadows, or multiple copies. The image must contain a SINGLE entity.
- Do NOT draw any environmental context: no ground, no trees, no rocks, no bark, \
  no branches, no roots, no walls, no surfaces, no other objects.
- If the description mentions a pose "against" or "on" something, IGNORE the surface — \
  only draw the entity's body in that posture.
- If the subject is "beside" or "on top of" something, do NOT include that something.
- The entity floats on the magenta background. There is NOTHING for it to lean on, sit on, \
  or attach to. Draw only the entity's own body/form.

## CRITICAL
- The background MUST be perfectly solid #FF00FF (magenta).
- Any pixel that is NOT part of the sprite must be exactly #FF00FF.
- The sprite should NOT contain any magenta (#FF00FF) pixels.
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
- **NO ANTHROPOMORPHISM**: Trees, rocks, terrain, weather, and all background \
  elements must be drawn realistically without faces, eyes, or human expressions. \
  They are environments, not characters.
"""
