"""Scene generation via Gemini 3 Flash.

Supports two modes:
  - use_reference_images=True (default): 5-step pipeline
      1. Generate manifest (text-only, Gemini 3 Flash) — NEG is optional here,
         generated separately by neg_generator.py
      2a. Generate background image (Gemini image model) → downscale → base64 PNG
      2b. Generate entity images × N (Gemini image model, on red #FF0000 chroma-key) → Pillow chroma key → raw pixels
      3. Generate masks (Gemini 3 Flash, receives entity images) → sub-entity IDs per pixel (RLE)
      4. Assembly → {bg: image_background, entity_01: raw_sprite, ...}
  - use_reference_images=False (legacy): single-call all-in-one
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from PIL import Image

from google import genai
from google.genai import types

from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.story_state import StoryState
from src.models.student_profile import StudentProfile
from src.generation.prompts.scene_prompt import (
    BACKGROUND_IMAGE_PROMPT_TEMPLATE,
    CONTINUATION_SCENE_USER_PROMPT,
    INITIAL_SCENE_USER_PROMPT,
    MANIFEST_SYSTEM_PROMPT,
    SCENE_SYSTEM_PROMPT,
)
from src.generation.prompts.sprite_prompt import (
    ENTITY_IMAGE_PROMPT,
    MASK_SYSTEM_PROMPT,
    MASK_USER_PROMPT,
)
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
IMAGE_MODEL_ID = "gemini-3-pro-image-preview"
MASK_MODEL_ID = "gemini-2.5-flash"

# Timeouts (seconds) for individual LLM calls to prevent indefinite hangs
MANIFEST_TIMEOUT = 60    # text-only, generous for complex scenes
IMAGE_TIMEOUT = 120      # image generation via Gemini Pro is slow
MASK_TIMEOUT = 60        # text + image input, needs headroom

# Retry counts per call type
MANIFEST_MAX_RETRIES = 2  # manifest is non-optional, retry on failure
IMAGE_MAX_RETRIES = 2     # retry entity/background images on timeout or error
MASK_MAX_RETRIES = 2      # retry mask generation on timeout or bad output

# Resolution model (must match engine.js)
SOURCE_W = 1120   # manifest coordinates, image generation
SOURCE_H = 720
K = 4             # pixel-art aggregation factor
ART_W = SOURCE_W // K   # 280 — art grid (pixel buffer, animations)
ART_H = SOURCE_H // K   # 180


# ---------------------------------------------------------------------------
# Story themes — common, everyday environments for children's stories
# ---------------------------------------------------------------------------

STORY_THEMES = [
    "a school classroom during an art lesson",
    "a sunny beach with tide pools",
    "a birthday party in a backyard",
    "a farm with animals in the morning",
    "a playground in a park",
    "a kitchen where someone is baking",
    "a camping trip in the woods",
    "a pet shop with different animals",
    "a rainy day at home",
    "a family picnic by a lake",
    "a trip to the supermarket",
    "a garden with flowers and insects",
    "a library with tall bookshelves",
    "a snowy day in the neighborhood",
    "a visit to the dentist",
    "a swimming pool on a hot day",
    "a train ride through the countryside",
    "a treehouse in a big oak tree",
    "a Saturday morning at the farmers market",
    "a family road trip stop at a gas station",
    "a football match at a local field",
    "a bedtime story in a cozy bedroom",
    "a school bus ride on Monday morning",
    "a bakery that just opened for the day",
    "a fishing trip at a small river",
    "a winter morning building a snowman",
    "a veterinary clinic with a sick puppy",
    "a laundromat on a busy afternoon",
    "a zoo visit on a spring day",
    "a bike ride through the neighborhood",
]


# ---------------------------------------------------------------------------
# Prompt builders (shared between legacy and pipeline)
# ---------------------------------------------------------------------------

def _build_initial_prompt(
    skill_objectives: List[str],
    theme: str,
) -> str:
    return INITIAL_SCENE_USER_PROMPT.format(
        skill_objectives=", ".join(skill_objectives),
        theme=theme,
    )


def _build_continuation_prompt(
    story_state: StoryState,
    student_profile: Optional[StudentProfile],
    skill_objectives: List[str],
) -> str:
    # Story context: narrative summaries of each scene
    story_lines = []
    for s in story_state.scenes:
        story_lines.append(
            f"- {s.get('scene_id', '?')}: {s.get('narrative_text', '')}"
        )
    story_context = "\n".join(story_lines) if story_lines else "(first scene)"

    # Previous manifest
    previous_manifest = "{}"
    if story_state.scenes:
        last = story_state.scenes[-1]
        previous_manifest = json.dumps(last.get("manifest", {}), indent=2)

    # Active entities summary
    entity_lines = []
    for eid, ent in story_state.active_entities.items():
        entity_lines.append(
            f"- {eid} (type={ent.type}, appeared={ent.first_appeared}, "
            f"pos={ent.last_position})"
        )
    active_entities = "\n".join(entity_lines) if entity_lines else "(none)"

    # Student profile
    profile_ctx = ""
    if student_profile:
        profile_ctx = student_profile.to_prompt_context()

    scene_number = len(story_state.scenes) + 1

    return CONTINUATION_SCENE_USER_PROMPT.format(
        story_context=story_context,
        previous_manifest=previous_manifest,
        active_entities=active_entities,
        student_profile_context=profile_ctx,
        skill_objectives=", ".join(skill_objectives),
        scene_number=scene_number,
    )


def _validate_scene_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize the LLM response into canonical form.

    NEG is optional — when the manifest is generated without NEG (new
    pipeline), the NEG is produced separately by neg_generator.py.
    If NEG is present, it is validated and included; if absent, an
    empty NEG is used as placeholder.
    """
    # Validate manifest
    manifest_data = data.get("manifest")
    if not manifest_data:
        raise ValueError("Response missing 'manifest' field")
    manifest = SceneManifest.model_validate(manifest_data)

    # Validate NEG (optional — may be absent in new pipeline)
    neg_data = data.get("neg")
    if neg_data:
        neg = NEG.model_validate(neg_data)
    else:
        neg = NEG()

    # Normalize sprite_code
    sprite_code = data.get("sprite_code", {})
    if not isinstance(sprite_code, dict):
        sprite_code = {}

    # Normalize carried_over_entities
    carried_over = data.get("carried_over_entities", [])
    if not isinstance(carried_over, list):
        carried_over = []

    # Normalize background_changed (default True = safe, always regenerate)
    background_changed = data.get("background_changed", True)
    if not isinstance(background_changed, bool):
        background_changed = True

    return {
        "narrative_text": data.get("narrative_text", ""),
        "branch_summary": data.get("branch_summary", ""),
        "scene_description": data.get("scene_description", ""),
        "background_description": data.get("background_description", ""),
        "manifest": manifest.model_dump(),
        "neg": neg.model_dump(),
        "sprite_code": sprite_code,
        "carried_over_entities": carried_over,
        "background_changed": background_changed,
    }


# ---------------------------------------------------------------------------
# Step 1: Manifest generation (text-only, NEG optional)
# ---------------------------------------------------------------------------

async def _generate_manifest(
    client: Any,
    user_prompt: str,
) -> Dict[str, Any]:
    """Step 1: Generate manifest (no sprite code).

    NEG is optional — in the new pipeline, NEG is generated separately
    by neg_generator.py. If the LLM includes a NEG (legacy prompt or
    fallback), it is validated but not required.

    Retries up to MANIFEST_MAX_RETRIES times with timeout. This is the only
    non-optional step — if it fails after all retries, the exception propagates
    and the pipeline falls back to legacy mode.

    Returns:
        Raw parsed dict with narrative_text, branch_summary, scene_description,
        manifest, (optionally neg), carried_over_entities.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, MANIFEST_MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=MODEL_ID,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=MANIFEST_SYSTEM_PROMPT,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        temperature=0.9,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=MANIFEST_TIMEOUT,
            )
            data = _extract_json(_get_response_text(response))

            # Validate manifest (required)
            manifest_data = data.get("manifest")
            if not manifest_data:
                raise ValueError("Manifest response missing 'manifest' field")
            SceneManifest.model_validate(manifest_data)

            # Validate NEG if present (optional)
            neg_data = data.get("neg")
            if neg_data:
                NEG.model_validate(neg_data)

            return data

        except asyncio.TimeoutError:
            logger.warning("[manifest] Attempt %d/%d timed out after %ds",
                           attempt, MANIFEST_MAX_RETRIES, MANIFEST_TIMEOUT)
            last_exc = asyncio.TimeoutError(
                f"Manifest generation timed out after {MANIFEST_TIMEOUT}s")
        except Exception as exc:
            logger.warning("[manifest] Attempt %d/%d failed (%s): %s",
                           attempt, MANIFEST_MAX_RETRIES,
                           type(exc).__name__, exc or "no details")
            last_exc = exc

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Step 2: Scene reference illustration generation
# ---------------------------------------------------------------------------

def _build_scene_image_prompt(manifest_data: Dict[str, Any]) -> str:
    """Build the prompt for generating a background-only illustration.

    Prefers ``background_description`` (entity-free) over ``scene_description``
    to avoid drawing entities in the background that will be composited separately.

    Computes entity ground level from manifest positions so the background
    image model knows where to place the ground/floor surface.
    """
    bg_desc = manifest_data.get("background_description", "")
    if not bg_desc:
        # Fallback: use scene_description if background_description is missing
        bg_desc = manifest_data.get("scene_description", "")

    # Compute ground level from entity foot positions in manifest
    entities = manifest_data.get("manifest", {}).get("entities", [])
    foot_positions: list[int] = []
    for ent in entities:
        pos = ent.get("position", {})
        y_center = pos.get("y", 0)
        h_hint = ent.get("height_hint", 200)
        foot_positions.append(y_center + h_hint // 2)

    if foot_positions:
        avg_foot = sum(foot_positions) // len(foot_positions)
        pct = round(avg_foot / SOURCE_H * 100)
        ground_level_hint = (
            f"Characters will stand with their feet at approximately "
            f"{pct}% from the top of the image. "
            f"The ground/floor surface MUST be clearly visible at this level."
        )
    else:
        ground_level_hint = (
            "The ground or floor should be at approximately 70% from the top."
        )

    return BACKGROUND_IMAGE_PROMPT_TEMPLATE.format(
        scene_description=bg_desc,
        ground_level_hint=ground_level_hint,
    )


# ---------------------------------------------------------------------------
# Step 2b: Generate individual entity images (Gemini 2.5 Flash Image)
# ---------------------------------------------------------------------------

# Pattern matching cross-entity references in pose/feature descriptions.
# Matches phrases like "against the tree trunk", "on the rock", "from the oak",
# "of the tall oak", etc. that would cause the image model to draw other entities.
_CROSS_ENTITY_RE = re.compile(
    r"""\b
    (against|on|upon|beside|under|beneath|below|above|from|near|
     of|into|onto|atop|off|behind|inside|within|along|around|over|
     next\s+to|in\s+front\s+of|on\s+top\s+of|attached\s+to|
     resting\s+on|resting\s+against|leaning\s+against|leaning\s+on|
     stuck\s+to|pinned\s+to|nailed\s+to|hanging\s+from|
     growing\s+from|sprouting\s+from|emerging\s+from)
    \s+(?:the|a|an)\s+
    [\w\s,'-]{1,40}?           # noun phrase (up to ~40 chars)
    (?=\s*[.,;!?]|\s*$|\s+(?:and|with|while|but|looking|head|tail|ears|body))
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _sanitize_for_isolation(text: str) -> str:
    """Remove references to other entities from a description field.

    Strips phrases like 'against the tree', 'on the rock', 'from the oak'
    that would cause the image model to draw other scene elements.

    This is a safety net for when the LLM still includes cross-entity
    references despite prompt instructions to avoid them.
    """
    if not text:
        return text
    text = _CROSS_ENTITY_RE.sub("", text)
    # Clean up leftover artifacts: double spaces, dangling commas/periods
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"^\s*[,;]\s*", "", text)
    return text.strip().rstrip(",").strip()


def _build_entity_description(entity: Dict[str, Any]) -> str:
    """Build a rich text description of an entity for image generation.

    Sanitizes ``pose`` and ``distinctive_features`` to remove cross-entity
    references that would cause the image model to draw other scene elements
    (e.g. a tree trunk behind the fox, bark behind the map).
    """
    props = entity.get("properties", {})
    etype = entity.get("type", "entity")
    size = props.get("size", "")
    color = props.get("color", "")
    texture = props.get("texture", "")
    pattern = props.get("pattern", "")
    distinctive = _sanitize_for_isolation(props.get("distinctive_features", ""))

    desc = f"A {size} {color} {texture} {etype}".strip()
    if pattern:
        desc += f" with {pattern} pattern"
    if entity.get("emotion"):
        desc += f", looking {entity['emotion']}"
    if entity.get("pose"):
        sanitized_pose = _sanitize_for_isolation(entity["pose"])
        if sanitized_pose:
            desc += f", {sanitized_pose}"
    if distinctive:
        desc += f". {distinctive}"
    return desc


async def _generate_entity_image(
    client: Any,
    entity: Dict[str, Any],
    scene_desc: str,
) -> Optional[bytes]:
    """Generate a single entity image on red chroma-key background.

    Uses Gemini 3 Pro Image to produce an illustration
    on solid #FF0000 red background. Retries up to IMAGE_MAX_RETRIES
    times on failure or timeout.

    Returns:
        PNG image bytes, or None if generation fails after all retries.
    """
    entity_desc = _build_entity_description(entity)
    prompt = ENTITY_IMAGE_PROMPT.format(entity_description=entity_desc)
    eid = entity["id"]

    for attempt in range(1, IMAGE_MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=IMAGE_MODEL_ID,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(
                            aspect_ratio="1:1",
                        ),
                    ),
                ),
                timeout=IMAGE_TIMEOUT,
            )

            # Extract image data from response
            img_data: Optional[bytes] = None
            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        img_data = part.inline_data.data
                        break

            if img_data is None:
                logger.warning("[entity-image] %s: attempt %d/%d no image data",
                               eid, attempt, IMAGE_MAX_RETRIES)
                continue

            logger.info("[entity-image] %s: got image (%d bytes)", eid, len(img_data))
            return img_data

        except Exception as exc:
            logger.warning("[entity-image] %s: attempt %d/%d failed (%s): %s",
                           eid, attempt, IMAGE_MAX_RETRIES,
                           type(exc).__name__, exc or "no details")

    logger.warning("[entity-image] %s: all %d attempts exhausted", eid, IMAGE_MAX_RETRIES)
    return None


async def _generate_entity_images_parallel(
    client: Any,
    manifest_data: Dict[str, Any],
    carried_over: List[str],
    scene_desc: str,
) -> Dict[str, bytes]:
    """Generate images for all new entities in parallel.

    Returns:
        Dict mapping entity_id -> PNG image bytes for successful generations.
    """
    entities_to_generate = []
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        if ent.get("id") in carried_over or ent.get("carried_over"):
            continue
        entities_to_generate.append(ent)

    if not entities_to_generate:
        return {}

    logger.info("[entity-images] Generating %d entity images in parallel...",
                len(entities_to_generate))

    tasks = [
        _generate_entity_image(client, ent, scene_desc)
        for ent in entities_to_generate
    ]

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=IMAGE_TIMEOUT * IMAGE_MAX_RETRIES + 30,
        )
    except asyncio.TimeoutError:
        logger.warning("[entity-images] Global timeout — returning empty")
        return {}

    entity_images: Dict[str, bytes] = {}
    for ent, result in zip(entities_to_generate, results):
        eid = ent["id"]
        if isinstance(result, Exception):
            logger.warning("[entity-images] %s: exception: %s", eid, result)
        elif result is not None:
            entity_images[eid] = result
        else:
            logger.warning("[entity-images] %s: no image generated", eid)

    logger.info("[entity-images] Successfully generated %d/%d images",
                len(entity_images), len(entities_to_generate))
    return entity_images


# ---------------------------------------------------------------------------
# Pixel extraction: chroma-key removal (entity) and quantization (background)
# ---------------------------------------------------------------------------

def _is_chroma_background(r: int, g: int, b: int) -> bool:
    """Detect red chroma-key background pixels (#FF0000).

    Uses channel thresholds instead of exact color matching.
    Red is chosen over green because bright red never appears in
    natural pixel art sprites (leaves, frogs, grass are green).

    A pixel is red background if:
    - Red channel is dominant (r > 120)
    - Green and blue channels are low (< 120 each)
    - Red exceeds both green and blue by at least 30

    NOTE: This function is kept for backward compatibility and tests.
    New code uses _detect_background_color() + _is_background_pixel().
    """
    return r > 120 and g < 120 and b < 120 and r > g + 30 and r > b + 30


def _dechroma_pixel(r: int, g: int, b: int) -> List[int]:
    """Shift a visible pixel's color so it won't be detected as chroma-key.

    Some entity sprites legitimately contain reddish pixels (fox fur,
    flowers, etc.) that would match ``_is_chroma_background()``.  This
    function adds minimal green to pull the pixel out of the chroma zone
    while preserving the visual appearance as much as possible.

    Only modifies pixels that would match ``_is_chroma_background()``.

    NOTE: This function is kept for backward compatibility and tests.
    New code uses _detect_background_color() + _is_background_pixel().
    """
    if not _is_chroma_background(r, g, b):
        return [r, g, b]
    # _is_chroma_background requires: g < 120 AND r > g + 30
    # Setting g = r - 29 breaks the "r > g + 30" condition.
    # Also ensure g >= 120 to break the "g < 120" condition.
    new_g = max(g, min(120, r - 29))
    return [r, new_g, b]


def _detect_background_color(img: Image.Image) -> tuple:
    """Detect the actual background color by sampling corner pixels.

    Gemini does NOT always generate pure #FF0000 backgrounds.  Real outputs
    vary: (254,0,0), (254,52,47), (254,18,59), etc.  This function samples
    16 pixels from the four corners of the original (pre-downscale) image
    and returns the median of each channel as the detected background color.

    Args:
        img: PIL Image (RGB mode), ideally at original resolution (1024×1024).

    Returns:
        Tuple (r, g, b) of the detected background color.
    """
    w, h = img.size
    samples = []
    for x, y in [
        (0, 0), (1, 0), (0, 1), (1, 1),
        (w - 1, 0), (w - 2, 0), (w - 1, 1), (w - 2, 1),
        (0, h - 1), (1, h - 1), (0, h - 2), (1, h - 2),
        (w - 1, h - 1), (w - 2, h - 1), (w - 1, h - 2), (w - 2, h - 2),
    ]:
        samples.append(img.getpixel((x, y)))

    r = sorted(s[0] for s in samples)[len(samples) // 2]
    g = sorted(s[1] for s in samples)[len(samples) // 2]
    b = sorted(s[2] for s in samples)[len(samples) // 2]
    return (r, g, b)


def _is_background_pixel(
    r: int, g: int, b: int,
    bg_r: int, bg_g: int, bg_b: int,
    threshold: float = 60.0,
) -> bool:
    """Check if a pixel is close enough to the detected background color.

    Uses Euclidean distance in RGB space.  A threshold of 60 works well:
    - Pure red bg (254,0,0) vs brownish sprite (140,80,70): dist ~130 → kept
    - Impure red bg (254,52,47) vs same brown: dist ~120 → kept
    - Impure red bg (254,52,47) vs nearby red (250,48,50): dist ~6 → removed

    Args:
        r, g, b: Pixel color.
        bg_r, bg_g, bg_b: Detected background color.
        threshold: Maximum Euclidean distance to consider as background.

    Returns:
        True if the pixel should be treated as background (transparent).
    """
    dist = ((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2) ** 0.5
    return dist < threshold


def _find_content_bbox(
    img: Image.Image,
    bg_r: int,
    bg_g: int,
    bg_b: int,
    threshold: float = 60.0,
    margin: int = 2,
) -> tuple:
    """Find the bounding box of non-background content in an image.

    Uses numpy for fast vectorized distance computation on the full
    high-res image (e.g. 1024×1024).

    Returns:
        (left, upper, right, lower) tuple for Image.crop().
    """
    import numpy as np

    w, h = img.size
    arr = np.array(img, dtype=np.float32)  # shape (H, W, 3)
    bg = np.array([bg_r, bg_g, bg_b], dtype=np.float32)

    # Euclidean distance from each pixel to the background color
    dist = np.sqrt(np.sum((arr - bg) ** 2, axis=2))  # shape (H, W)

    # Content mask: pixels far enough from background
    content = dist >= threshold

    if not np.any(content):
        # No content found — return full image
        return (0, 0, w, h)

    # Find bounding box of True values
    rows = np.any(content, axis=1)
    cols = np.any(content, axis=0)
    min_y, max_y = np.where(rows)[0][[0, -1]]
    min_x, max_x = np.where(cols)[0][[0, -1]]

    # Add margin, clamped to image bounds
    left = max(0, int(min_x) - margin)
    upper = max(0, int(min_y) - margin)
    right = min(w, int(max_x) + 1 + margin)
    lower = min(h, int(max_y) + 1 + margin)
    return (left, upper, right, lower)


def _extract_entity_sprite(
    image_bytes: bytes,
    target_w: int,
    target_h: int,
) -> Dict[str, Any]:
    """Extract raw pixels from an entity image using chroma-key background removal.

    Pipeline (chroma-key + decontamination on HD, then downscale to art grid):
    1. Open HD image (e.g. 1024×1024)
    2. Detect background color from corner pixels
    3. Chroma-key: Euclidean distance in RGB → alpha channel (soft threshold)
    4. Alpha decontamination on HD — correct semi-transparent edge colors
    5. Crop to content bounding box
    6. Downscale RGBA to art-grid target size (target_w, target_h)
    7. Convert to pixel list: transparent → None, visible → [r,g,b]

    target_w and target_h should already be in art-grid coordinates
    (i.e. width_hint // K, height_hint // K).

    Returns:
        Dict with keys: pixels (flat list of [r,g,b] or None), w, h
    """
    import numpy as np

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    logger.info("[extract-sprite] Original: %dx%d -> art-grid target %dx%d",
                img.width, img.height, target_w, target_h)

    # Detect background color from corner pixels
    bg_color = _detect_background_color(img)
    bg_r, bg_g, bg_b = bg_color
    logger.info("[extract-sprite] Detected background color: rgb(%d, %d, %d)",
                bg_r, bg_g, bg_b)

    # Chroma-key: Euclidean distance from background color → alpha
    INNER_THRESH = 40.0   # distance < INNER → fully transparent (pure background)
    OUTER_THRESH = 80.0   # distance > OUTER → fully opaque (pure entity)
                          # between → proportional alpha (anti-aliased edge)

    arr = np.array(img, dtype=np.float32)           # (H, W, 3)
    bg = np.array(bg_color, dtype=np.float32)        # (3,)
    dist = np.sqrt(np.sum((arr - bg) ** 2, axis=2))  # (H, W)

    alpha_f = np.clip((dist - INNER_THRESH) / (OUTER_THRESH - INNER_THRESH), 0, 1)
    alpha_ch = (alpha_f * 255).astype(np.float32)

    # Build RGBA array for decontamination
    rgba_arr = np.zeros((*arr.shape[:2], 4), dtype=np.float32)
    rgba_arr[:, :, :3] = arr
    rgba_arr[:, :, 3] = alpha_ch

    # Alpha decontamination on HD — correct edge colors BEFORE downscale
    # Semi-transparent pixels have background color blended in; remove it:
    #   observed = alpha * foreground + (1 - alpha) * background
    #   foreground = (observed - background * (1 - alpha)) / alpha
    semi_mask = (alpha_ch > 20) & (alpha_ch < 255)
    if np.any(semi_mask):
        af = alpha_ch[semi_mask] / 255.0
        for c, bg_c in enumerate([bg_r, bg_g, bg_b]):
            rgba_arr[:, :, c][semi_mask] = np.clip(
                (rgba_arr[:, :, c][semi_mask] - bg_c * (1 - af)) / af, 0, 255
            )
    # Flatten: fully transparent stays, semi-transparent becomes opaque
    rgba_arr[:, :, 3] = np.where(alpha_ch < 20, 0, 255)
    rgba = Image.fromarray(rgba_arr.astype(np.uint8), "RGBA")

    # Crop to content via alpha channel
    alpha = rgba.split()[3]
    bbox = alpha.getbbox()
    if bbox:
        content_w = bbox[2] - bbox[0]
        content_h = bbox[3] - bbox[1]
        logger.info("[extract-sprite] Content bbox: (%d,%d)-(%d,%d) = %dx%d",
                    bbox[0], bbox[1], bbox[2], bbox[3], content_w, content_h)
        rgba = rgba.crop(bbox)
    else:
        content_w, content_h = rgba.size
        logger.warning("[extract-sprite] No content found via alpha, using full image")

    # Downscale to art-grid target (preserving aspect ratio)
    if content_w > 0 and content_h > 0:
        scale = min(target_w / content_w, target_h / content_h)
        final_w = max(1, round(content_w * scale))
        final_h = max(1, round(content_h * scale))
    else:
        final_w, final_h = target_w, target_h

    logger.info("[extract-sprite] Scaling %dx%d -> %dx%d (art-grid target %dx%d)",
                content_w, content_h, final_w, final_h, target_w, target_h)
    rgba = rgba.resize((final_w, final_h), Image.LANCZOS)

    # Convert to pixel list (decontamination already done on HD)
    total = final_w * final_h
    pixels: List[Optional[List[int]]] = []
    transparent_count = 0
    for y in range(final_h):
        for x in range(final_w):
            r, g, b, a = rgba.getpixel((x, y))
            if a < 128:
                pixels.append(None)
                transparent_count += 1
            else:
                pixels.append([r, g, b])

    visible_count = total - transparent_count
    pct_removed = (transparent_count / total * 100) if total > 0 else 0
    logger.info("[extract-sprite] Extracted %d visible pixels out of %d total "
                "(%.1f%% background removed via chroma-key)",
                visible_count, total, pct_removed)

    return {"pixels": pixels, "w": final_w, "h": final_h}


def _extract_background_sprite(image_bytes: bytes) -> Dict[str, Any]:
    """Convert a background image to a base64 PNG at art-grid resolution.

    Downscales the HD image from Gemini directly to ART_W × ART_H with
    LANCZOS.  The client renders it 1:1 into the pixel buffer; the Renderer
    then upscales each art pixel K×K for display.

    Returns:
        Dict with format="image_background", width, height, image_base64.
    """
    import base64

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    logger.info("[extract-bg] Original: %dx%d -> art grid %dx%d",
                img.width, img.height, ART_W, ART_H)

    img = img.resize((ART_W, ART_H), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="PNG")
    png_bytes = out.getvalue()

    b64 = base64.b64encode(png_bytes).decode("ascii")
    logger.info("[extract-bg] image_background: %dx%d, %d bytes PNG, %d chars base64",
                ART_W, ART_H, len(png_bytes), len(b64))

    return {
        "format": "image_background",
        "width": ART_W,
        "height": ART_H,
        "image_base64": b64,
    }


# ---------------------------------------------------------------------------
# Step 3: Mask generation (Gemini 3 Flash, one per entity)
# ---------------------------------------------------------------------------

def _expand_rle_mask(
    rle_data: List[List[Any]],
    total_pixels: int,
    entity_id: str,
    sprite_pixels: List[Optional[List[int]]],
) -> List[Optional[str]]:
    """Expand an RLE-encoded mask to a flat per-pixel array.

    Args:
        rle_data: List of [sub_entity_id_or_null, pixel_count] runs.
        total_pixels: Expected total pixel count (w * h).
        entity_id: Root entity ID for filling visible pixels without mask.
        sprite_pixels: Flat list of [r,g,b] or None per pixel (for transparency sync).

    Returns:
        Flat list of (entity_id string or None) with length total_pixels.
    """
    mask: List[Optional[str]] = []

    for item in rle_data:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        sub_id = item[0]
        count = item[1]

        # Normalize sub_id
        if sub_id is None or sub_id == "null":
            sub_id = None
        elif not isinstance(sub_id, str):
            sub_id = None

        # Normalize count
        if not isinstance(count, (int, float)) or count < 1:
            continue
        count = int(count)

        mask.extend([sub_id] * count)

    # Pad or truncate to exact size
    if len(mask) < total_pixels:
        mask.extend([None] * (total_pixels - len(mask)))
    mask = mask[:total_pixels]

    # Sync mask transparency with pixel transparency
    for i in range(total_pixels):
        if i < len(sprite_pixels) and sprite_pixels[i] is None:
            mask[i] = None
        elif i < len(sprite_pixels) and sprite_pixels[i] is not None and mask[i] is None:
            # Visible pixel without mask → assign root entity ID
            mask[i] = entity_id

    return mask


def _is_rle_format(mask_raw: List[Any]) -> bool:
    """Detect whether a mask response is RLE format or legacy flat format.

    RLE format: [[str_or_null, int], ...]
    Legacy flat: [str_or_null, str_or_null, ...]
    """
    if not mask_raw:
        return False
    first = mask_raw[0]
    return isinstance(first, (list, tuple))


async def _generate_mask_for_entity(
    client: Any,
    entity_id: str,
    entity_type: str,
    image_bytes: bytes,
    sprite_w: int,
    sprite_h: int,
    sprite_pixels: List[Optional[List[int]]],
) -> Optional[List[Optional[str]]]:
    """Generate sub-entity ID mask for one entity's sprite.

    Sends the entity image to Gemini along with the sprite dimensions,
    and gets back a mask in RLE format (run-length encoded).

    Retries up to ``MASK_MAX_RETRIES`` times on failure, timeout, or
    degenerate output (single unique ID).

    Returns:
        List of (entity_id string or None) with length sprite_w * sprite_h,
        or None if generation fails after all retries.
    """
    total_pixels = sprite_w * sprite_h
    user_text = MASK_USER_PROMPT.format(
        entity_id=entity_id,
        entity_type=entity_type,
        width=sprite_w,
        height=sprite_h,
        total_pixels=total_pixels,
    )
    system_text = MASK_SYSTEM_PROMPT.format(
        eid=entity_id,
        width=sprite_w,
        height=sprite_h,
        total_pixels=total_pixels,
    )

    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        user_text,
    ]

    last_error: Optional[str] = None

    for attempt in range(1, MASK_MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=MASK_MODEL_ID,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_text,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        temperature=0.3,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=MASK_TIMEOUT,
            )

            # Validate response before extraction
            if not response.candidates:
                last_error = "no candidates (prompt_feedback=%s)" % (
                    getattr(response, 'prompt_feedback', 'N/A'),)
                logger.warning("[mask] %s: attempt %d/%d — %s",
                               entity_id, attempt, MASK_MAX_RETRIES, last_error)
                if attempt < MASK_MAX_RETRIES:
                    await asyncio.sleep(2)
                continue

            data = _extract_json(_get_response_text(response))
            mask_raw = data.get("mask", [])

            if not isinstance(mask_raw, list):
                last_error = "mask is not a list"
                logger.warning("[mask] %s: attempt %d/%d — %s",
                               entity_id, attempt, MASK_MAX_RETRIES, last_error)
                if attempt < MASK_MAX_RETRIES:
                    await asyncio.sleep(2)
                continue

            # Auto-detect format: RLE vs legacy flat
            if _is_rle_format(mask_raw):
                logger.info("[mask] %s: attempt %d/%d — RLE format (%d runs)",
                            entity_id, attempt, MASK_MAX_RETRIES, len(mask_raw))
                mask = _expand_rle_mask(mask_raw, total_pixels, entity_id, sprite_pixels)
            else:
                # Legacy flat format (backward compatibility)
                logger.info("[mask] %s: attempt %d/%d — flat format (%d elements)",
                            entity_id, attempt, MASK_MAX_RETRIES, len(mask_raw))
                mask = []
                for i in range(total_pixels):
                    if i < len(mask_raw):
                        val = mask_raw[i]
                        if val is None or val == "null":
                            mask.append(None)
                        elif isinstance(val, str):
                            mask.append(val)
                        else:
                            mask.append(None)
                    else:
                        mask.append(None)

                # Sync mask transparency with pixel transparency
                for i in range(total_pixels):
                    if i < len(sprite_pixels) and sprite_pixels[i] is None:
                        mask[i] = None
                    elif i < len(sprite_pixels) and sprite_pixels[i] is not None and mask[i] is None:
                        mask[i] = entity_id

            visible_mask = sum(1 for m in mask if m is not None)
            unique_ids = set(m for m in mask if m is not None)

            # Quality gate: if only 1 unique ID (= fallback quality), retry
            if len(unique_ids) <= 1 and attempt < MASK_MAX_RETRIES:
                logger.warning(
                    "[mask] %s: attempt %d/%d — only %d unique ID(s), retrying",
                    entity_id, attempt, MASK_MAX_RETRIES, len(unique_ids))
                last_error = "degenerate mask (%d unique ID)" % len(unique_ids)
                await asyncio.sleep(2)
                continue

            logger.info("[mask] %s: attempt %d/%d — %d masked pixels, "
                        "%d unique sub-entity IDs",
                        entity_id, attempt, MASK_MAX_RETRIES,
                        visible_mask, len(unique_ids))

            # Log each unique sub-entity ID with pixel count
            from collections import Counter
            id_counts = Counter(m for m in mask if m is not None)
            for sub_id, count in sorted(id_counts.items()):
                logger.info("[mask]   %-40s %5d px", sub_id, count)

            return mask

        except asyncio.TimeoutError:
            last_error = "TIMEOUT after %ds" % MASK_TIMEOUT
            logger.warning("[mask] %s: attempt %d/%d — %s",
                           entity_id, attempt, MASK_MAX_RETRIES, last_error)
        except json.JSONDecodeError as exc:
            last_error = "JSON parse failed: %s" % exc
            logger.warning("[mask] %s: attempt %d/%d — %s",
                           entity_id, attempt, MASK_MAX_RETRIES, last_error)
        except Exception as exc:
            last_error = "%s: %r" % (type(exc).__name__, exc)
            logger.warning("[mask] %s: attempt %d/%d — %s",
                           entity_id, attempt, MASK_MAX_RETRIES, last_error)

        # Backoff before retry
        if attempt < MASK_MAX_RETRIES:
            await asyncio.sleep(2)

    logger.warning("[mask] %s: all %d attempts exhausted (last: %s)",
                   entity_id, MASK_MAX_RETRIES, last_error)
    return None


def _build_fallback_mask(
    entity_id: str,
    pixels: List[Optional[List[int]]],
) -> List[Optional[str]]:
    """Build a simple fallback mask where all visible pixels get the root entity ID."""
    return [entity_id if p is not None else None for p in pixels]


async def _generate_mask_for_background(
    client: Any,
    image_bytes: bytes,
    width: int,
    height: int,
) -> Optional[List[str]]:
    """Generate sub-entity ID mask for the background image.

    Unlike entity masks, background masks have NO transparent pixels —
    every pixel gets a sub-entity ID starting with ``bg.``.

    Args:
        client: Gemini API client.
        image_bytes: PNG image bytes of the background at art-grid resolution.
        width: Background width in pixels (ART_W = 280).
        height: Background height in pixels (ART_H = 180).

    Returns:
        Flat list of ``bg.*`` strings with length ``width * height``,
        or *None* if generation fails.
    """
    from src.generation.prompts.sprite_prompt import (
        BG_MASK_SYSTEM_PROMPT,
        BG_MASK_USER_PROMPT,
    )

    total_pixels = width * height
    system_text = BG_MASK_SYSTEM_PROMPT.format(
        width=width, height=height, total_pixels=total_pixels,
    )
    user_text = BG_MASK_USER_PROMPT.format(
        width=width, height=height, total_pixels=total_pixels,
    )

    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        user_text,
    ]

    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=MASK_MODEL_ID,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_text,
                    thinking_config=types.ThinkingConfig(thinking_budget=1024),
                    temperature=0.3,
                    response_mime_type="application/json",
                ),
            ),
            timeout=MASK_TIMEOUT,
        )

        data = _extract_json(_get_response_text(response))
        mask_raw = data.get("mask", [])

        if not isinstance(mask_raw, list):
            logger.warning("[bg-mask] mask is not a list")
            return None

        # Expand RLE runs → flat list (no nulls for backgrounds)
        mask: List[str] = []
        for item in mask_raw:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            sub_id = item[0]
            count = item[1]
            if not isinstance(sub_id, str) or not sub_id.startswith("bg."):
                sub_id = "bg"
            if not isinstance(count, (int, float)) or count < 1:
                continue
            mask.extend([sub_id] * int(count))

        # Pad or truncate to exact size
        if len(mask) < total_pixels:
            logger.warning("[bg-mask] RLE expanded to %d, expected %d — padding",
                           len(mask), total_pixels)
            mask.extend(["bg"] * (total_pixels - len(mask)))
        if len(mask) > total_pixels:
            logger.warning("[bg-mask] RLE expanded to %d, expected %d — truncating",
                           len(mask), total_pixels)
        mask = mask[:total_pixels]

        unique_ids = set(mask)
        logger.info("[bg-mask] %d pixels, %d unique sub-entity IDs: %s",
                    len(mask), len(unique_ids), sorted(unique_ids))

        from collections import Counter
        id_counts = Counter(mask)
        for sub_id, count in sorted(id_counts.items()):
            logger.info("[bg-mask]   %-40s %5d px", sub_id, count)

        return mask

    except asyncio.TimeoutError:
        logger.warning("[bg-mask] TIMEOUT after %ds", MASK_TIMEOUT)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("[bg-mask] JSON parse failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("[bg-mask] generation failed: %s: %r",
                       type(exc).__name__, exc)
        return None


def _sprite_to_png(sprite: Dict[str, Any]) -> bytes:
    """Re-encode extracted sprite pixels as a small PNG for mask generation.

    Instead of sending the original 1024×1024 image to Gemini for mask
    generation, we send the already-downscaled sprite (e.g. 35×30).
    This reduces token cost from ~1032 (4 tiles) to 258 (1 tile) and
    makes Gemini's job much easier since it sees exactly the pixels it
    needs to label.

    Transparent (None) pixels are rendered as red (#FF0000) to match
    the chroma-key convention described in the mask prompt.
    """
    w, h = sprite["w"], sprite["h"]
    pixels = sprite["pixels"]
    img = Image.new("RGB", (w, h))
    for y in range(h):
        for x in range(w):
            idx = y * w + x
            px = pixels[idx] if idx < len(pixels) else None
            if px is None:
                img.putpixel((x, y), (255, 0, 0))  # red = transparent
            else:
                img.putpixel((x, y), (px[0], px[1], px[2]))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def _generate_masks_parallel(
    client: Any,
    entity_images: Dict[str, bytes],
    entity_sprites: Dict[str, Dict[str, Any]],
    manifest_data: Dict[str, Any],
) -> Dict[str, List[Optional[str]]]:
    """Generate masks for all entities in parallel.

    Sends the downscaled sprite images (not the original 1024×1024) to
    reduce token cost and improve mask accuracy.

    Returns:
        Dict mapping entity_id -> mask list.
    """
    # Build entity type lookup
    entity_types = {}
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        entity_types[ent["id"]] = ent.get("type", "entity")

    tasks = []
    entity_ids = []

    for eid in entity_images:
        if eid not in entity_sprites:
            continue
        sprite = entity_sprites[eid]
        # Send the small downscaled sprite instead of the 1024×1024 original
        small_png = _sprite_to_png(sprite)
        logger.info("[masks] %s: sending %dx%d sprite (%d bytes) instead of "
                    "1024x1024 original (%d bytes)",
                    eid, sprite["w"], sprite["h"], len(small_png),
                    len(entity_images[eid]))
        entity_ids.append(eid)
        tasks.append(
            _generate_mask_for_entity(
                client=client,
                entity_id=eid,
                entity_type=entity_types.get(eid, "entity"),
                image_bytes=small_png,
                sprite_w=sprite["w"],
                sprite_h=sprite["h"],
                sprite_pixels=sprite["pixels"],
            )
        )

    if not tasks:
        return {}

    logger.info("[masks] Generating masks for %d entities in parallel...", len(tasks))

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=MASK_TIMEOUT * MASK_MAX_RETRIES + 30,  # accounts for retries
        )
    except asyncio.TimeoutError:
        logger.warning("[masks] Global mask generation timed out, using fallback masks")
        return {
            eid: _build_fallback_mask(eid, entity_sprites[eid]["pixels"])
            for eid in entity_ids
        }

    masks: Dict[str, List[Optional[str]]] = {}
    for eid, result in zip(entity_ids, results):
        if isinstance(result, Exception):
            logger.warning("[masks] %s: exception: %s, using fallback", eid, result)
            masks[eid] = _build_fallback_mask(eid, entity_sprites[eid]["pixels"])
        elif result is not None:
            masks[eid] = result
        else:
            logger.warning("[masks] %s: no mask generated, using fallback", eid)
            masks[eid] = _build_fallback_mask(eid, entity_sprites[eid]["pixels"])

    return masks


def _downscale_to_canvas(image_bytes: bytes, target_w: int = 280, target_h: int = 180) -> bytes:
    """Downscale an image to exact canvas dimensions using LANCZOS.

    LANCZOS produces smooth results from HD source images.
    Returns PNG bytes.
    """
    img = Image.open(io.BytesIO(image_bytes))
    logger.info("[downscale] Original image: %dx%d → target %dx%d",
                img.width, img.height, target_w, target_h)
    img = img.resize((target_w, target_h), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def _generate_background_image(
    client: Any,
    manifest_data: Dict[str, Any],
) -> Optional[bytes]:
    """Step 2a: Generate a background-only illustration.

    Generates at 16:9 aspect ratio then downscales to 280×180
    (canvas size) using LANCZOS for smooth results.
    Retries up to IMAGE_MAX_RETRIES times on failure or timeout.

    Returns:
        PNG image bytes at 280×180, or None if generation fails after all retries.
    """
    prompt = _build_scene_image_prompt(manifest_data)

    for attempt in range(1, IMAGE_MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=IMAGE_MODEL_ID,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(
                            aspect_ratio="16:9",
                        ),
                    ),
                ),
                timeout=IMAGE_TIMEOUT,
            )

            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        raw_bytes = part.inline_data.data
                        return _downscale_to_canvas(raw_bytes)

            logger.warning("[bg-image] Attempt %d/%d: no image data returned",
                           attempt, IMAGE_MAX_RETRIES)

        except Exception as exc:
            logger.warning("[bg-image] Attempt %d/%d failed (%s): %s",
                           attempt, IMAGE_MAX_RETRIES,
                           type(exc).__name__, exc or "no details")

    logger.warning("[bg-image] All %d attempts exhausted", IMAGE_MAX_RETRIES)
    return None


# ---------------------------------------------------------------------------
# Step 4: Assembly — combine background + entity sprites + masks
# ---------------------------------------------------------------------------

def _compute_entity_positions(
    manifest_data: Dict[str, Any],
    entity_sprites: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, int]]:
    """Compute top-left positions and sizes in art-grid coordinates.

    Manifest positions are in source coordinates (0–1119, 0–719).
    This function converts them to art-grid coordinates (0–ART_W-1, 0–ART_H-1).

    When ``entity_sprites`` is provided, uses actual sprite dimensions
    (already in art-grid pixels) for centering.

    Returns:
        Dict mapping entity_id -> {"x": int, "y": int, "w": int, "h": int}
        in art-grid coordinates.
    """
    positions = {}
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        eid = ent["id"]
        pos = ent.get("position", {})

        # Sprite dimensions (already in art-grid coords)
        if entity_sprites and eid in entity_sprites:
            w = entity_sprites[eid]["w"]
            h = entity_sprites[eid]["h"]
        else:
            w = max(1, ent.get("width_hint", 50) // K)
            h = max(1, ent.get("height_hint", 60) // K)

        # Convert source center to art-grid center, then to top-left
        art_cx = pos.get("x", 0) // K
        art_cy = pos.get("y", 0) // K
        x = art_cx - w // 2
        y = art_cy - h // 2

        # Clamp to art grid
        if w > ART_W or h > ART_H:
            logger.warning(
                "[positions] Entity %s sprite (%dx%d) exceeds art grid (%dx%d); "
                "it will be partially clipped",
                eid, w, h, ART_W, ART_H,
            )
        x = max(0, min(x, ART_W - w))
        y = max(0, min(y, ART_H - h))

        positions[eid] = {
            "x": x,
            "y": y,
            "w": w,
            "h": h,
        }

    # Diagnostic: warn if entity feet are significantly above the canonical
    # ground line (~70% from top = y≈126 in art-grid).  This helps detect
    # cases where the LLM placed entities too high and they may appear to float.
    canonical_ground_art_y = SOURCE_H * 70 // (100 * K)  # ≈126
    float_threshold = ART_H // 6  # ~30 px
    for eid, pos in positions.items():
        foot_y = pos["y"] + pos["h"]
        if foot_y < canonical_ground_art_y - float_threshold:
            logger.warning(
                "[positions] %s: feet at art-y=%d, expected ~%d — "
                "entity may appear floating (%d px above ground)",
                eid, foot_y, canonical_ground_art_y,
                canonical_ground_art_y - foot_y,
            )

    return positions


def _assemble_sprite_code(
    bg_sprite: Optional[Dict[str, Any]],
    entity_sprites: Dict[str, Dict[str, Any]],
    entity_masks: Dict[str, List[Optional[str]]],
    entity_positions: Dict[str, Dict[str, int]],
) -> Dict[str, Any]:
    """Assemble the final sprite_code dict from all pipeline outputs.

    All positions and dimensions are in art-grid coordinates.

    Returns:
        Dict mapping entity_id -> sprite data:
        - "bg" -> image_background dict
        - entity_id -> raw_sprite dict
    """
    sprite_code: Dict[str, Any] = {}

    # Background
    if bg_sprite:
        sprite_code["bg"] = bg_sprite
        bg_fmt = bg_sprite.get("format", "unknown")
        logger.info("[assemble] bg: %s %dx%d", bg_fmt,
                    bg_sprite.get("width", "?"), bg_sprite.get("height", "?"))

    # Entities as raw_sprite
    for eid, sprite in entity_sprites.items():
        pos = entity_positions.get(eid, {"x": 0, "y": 0})
        mask = entity_masks.get(eid)

        raw_sprite: Dict[str, Any] = {
            "format": "raw_sprite",
            "x": pos["x"],
            "y": pos["y"],
            "w": sprite["w"],
            "h": sprite["h"],
            "pixels": sprite["pixels"],
            "mask": mask,
        }
        sprite_code[eid] = raw_sprite
        visible = sum(1 for p in sprite["pixels"] if p is not None)
        mask_count = sum(1 for m in (mask or []) if m is not None) if mask else 0
        logger.info("[assemble] %s: raw_sprite %dx%d at (%d,%d), %d visible px, %d mask entries",
                    eid, sprite["w"], sprite["h"], pos["x"], pos["y"],
                    visible, mask_count)

    return sprite_code


# ---------------------------------------------------------------------------
# Legacy single-call pipeline
# ---------------------------------------------------------------------------

async def _generate_scene_legacy(
    client: Any,
    user_prompt: str,
) -> Dict[str, Any]:
    """Legacy all-in-one generation: manifest + NEG + sprite code in one call."""
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=MODEL_ID,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SCENE_SYSTEM_PROMPT,
                thinking_config=types.ThinkingConfig(thinking_budget=1024),
                temperature=0.9,
                response_mime_type="application/json",
            ),
        ),
        timeout=MANIFEST_TIMEOUT,
    )
    data = _extract_json(_get_response_text(response))
    return _validate_scene_response(data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_scene(
    api_key: str,
    story_state: Optional[StoryState] = None,
    student_profile: Optional[StudentProfile] = None,
    skill_objectives: Optional[List[str]] = None,
    theme: str = "",
    commit_to_state: bool = True,
    extra_prompt: str = "",
    use_reference_images: bool = True,
    skip_masks: bool = False,
    progress_callback: Optional[Callable] = None,
    neg_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a single scene via Gemini 3 Flash.

    Args:
        api_key: Gemini API key.
        story_state: Cumulative story state, or None for initial scene.
        student_profile: Child's error profile, or None for initial scene.
        skill_objectives: SKILL objectives for the session.
        theme: Story theme for initial scenes (e.g. "a sunny beach with
            tide pools"). Ignored for continuation scenes where context
            comes from story_state.  If empty, a random theme is picked
            from STORY_THEMES.
        commit_to_state: If True, call story_state.add_scene() with the result.
            Set to False for candidate branches that shouldn't mutate state.
        extra_prompt: Additional text appended to the user prompt (e.g. branch directive).
        use_reference_images: If True, use 5-step pipeline with reference image.
            If False, use legacy single-call pipeline.
        skip_masks: If True, skip mask generation (Step 3) and use fallback
            root-entity-ID masks. Useful for thumbnails where masks aren't
            needed yet. Masks can be generated later when the scene is selected.
        progress_callback: Optional async callback called after each pipeline step.
            Receives a step name string: "starting", "manifest", "images", "masks", "assembly".
        neg_override: Optional NEG dict to inject into the result. When the
            NEG is generated separately (by neg_generator.py), pass it here
            to attach it to the scene. If None and the manifest contains a
            NEG, the manifest's NEG is used.

    Returns:
        Dict with narrative_text, branch_summary, manifest, neg,
        sprite_code, carried_over_entities.
    """
    if skill_objectives is None:
        skill_objectives = [
            "descriptive_adjectives",
            "spatial_prepositions",
            "action_verbs",
        ]

    # Build user prompt
    if story_state is None or len(story_state.scenes) == 0:
        import random
        chosen_theme = theme or random.choice(STORY_THEMES)
        user_prompt = _build_initial_prompt(skill_objectives, chosen_theme)
    else:
        user_prompt = _build_continuation_prompt(
            story_state, student_profile, skill_objectives
        )

    if extra_prompt:
        user_prompt += "\n" + extra_prompt

    # Create client
    client = genai.Client(api_key=api_key)

    if use_reference_images:
        result = await _pipeline_with_reference_image(
            client, user_prompt, story_state, skip_masks, progress_callback
        )
    else:
        result = await _generate_scene_legacy(client, user_prompt)

    # Inject externally-provided NEG if given
    if neg_override is not None:
        neg = NEG.model_validate(neg_override)
        result["neg"] = neg.model_dump()

    # Update story_state if provided and commit requested
    if story_state is not None and commit_to_state:
        story_state.add_scene(
            scene_id=result["manifest"]["scene_id"],
            narrative_text=result["narrative_text"],
            manifest=result["manifest"],
            neg=result["neg"],
            sprite_code=result["sprite_code"] or None,
        )

    return result


async def generate_masks_for_scene(
    api_key: str,
    scene: Dict[str, Any],
) -> Dict[str, Any]:
    """Generate masks for a scene that was created with skip_masks=True.

    Retroactively generates sub-entity ID masks for all entities AND the
    background in the scene's sprite_code.  Updates the scene dict in-place.

    Entity masks use chroma-key based sprite images.  The background mask
    sends the art-grid PNG directly to the LLM (no chroma-key — every
    pixel gets a ``bg.*`` ID).

    Both entity masks and the background mask are generated **in parallel**.

    Args:
        api_key: Gemini API key.
        scene: Scene dict with sprite_code containing raw_sprite entries
            that have fallback (root-ID only) masks.

    Returns:
        The updated scene dict (also modified in-place).
    """
    sprite_code = scene.get("sprite_code", {})
    if not sprite_code:
        return scene

    client = genai.Client(api_key=api_key)
    manifest_data = {"manifest": scene.get("manifest", {})}

    # ------------------------------------------------------------------
    # Collect entities that need real masks
    # ------------------------------------------------------------------
    entity_images: Dict[str, bytes] = {}
    entity_sprites: Dict[str, Dict[str, Any]] = {}

    for eid, entry in sprite_code.items():
        if eid == "bg":
            continue
        if not isinstance(entry, dict) or entry.get("format") != "raw_sprite":
            continue
        entity_sprites[eid] = {
            "w": entry["w"],
            "h": entry["h"],
            "pixels": entry["pixels"],
        }
        entity_images[eid] = _sprite_to_png(entity_sprites[eid])

    # ------------------------------------------------------------------
    # Detect background that needs a mask
    # ------------------------------------------------------------------
    bg_entry = sprite_code.get("bg")
    need_bg_mask = (
        isinstance(bg_entry, dict)
        and bg_entry.get("format") == "image_background"
        and bg_entry.get("image_base64")
        and not bg_entry.get("mask")
    )

    if not entity_sprites and not need_bg_mask:
        return scene

    # ------------------------------------------------------------------
    # Launch entity masks + bg mask in parallel
    # ------------------------------------------------------------------
    tasks: List[Any] = []

    # Task 0: entity masks (or None placeholder)
    if entity_sprites:
        logger.info("[generate-masks] Generating masks for %d entities...",
                    len(entity_sprites))
        tasks.append(
            _generate_masks_parallel(
                client, entity_images, entity_sprites, manifest_data
            )
        )
    else:
        tasks.append(asyncio.sleep(0))  # placeholder

    # Task 1: background mask
    if need_bg_mask:
        import base64 as _b64
        bg_png = _b64.b64decode(bg_entry["image_base64"])
        bg_w = bg_entry.get("width", ART_W)
        bg_h = bg_entry.get("height", ART_H)
        logger.info("[generate-masks] Generating mask for background (%dx%d)...",
                    bg_w, bg_h)
        tasks.append(
            _generate_mask_for_background(client, bg_png, bg_w, bg_h)
        )
    else:
        tasks.append(asyncio.sleep(0))  # placeholder

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Apply entity masks
    # ------------------------------------------------------------------
    entity_masks = results[0]
    if isinstance(entity_masks, dict):
        for eid, mask in entity_masks.items():
            if eid in sprite_code:
                sprite_code[eid]["mask"] = mask
                mask_count = sum(1 for m in mask if m is not None)
                logger.info("[generate-masks] %s: updated with %d mask entries",
                            eid, mask_count)
    elif isinstance(entity_masks, Exception):
        logger.warning("[generate-masks] Entity mask generation failed: %s",
                       entity_masks)

    # ------------------------------------------------------------------
    # Apply background mask
    # ------------------------------------------------------------------
    bg_mask_result = results[1]
    if isinstance(bg_mask_result, list) and bg_mask_result:
        sprite_code["bg"]["mask"] = bg_mask_result
        unique_bg = set(bg_mask_result)
        logger.info("[generate-masks] bg: updated with %d pixels, %d unique IDs",
                    len(bg_mask_result), len(unique_bg))
    elif isinstance(bg_mask_result, Exception):
        logger.warning("[generate-masks] Background mask generation failed: %s",
                       bg_mask_result)

    return scene


async def generate_features_for_scene(
    api_key: str,
    scene: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract visual features for a scene that was created with the image pipeline.

    Calls Gemini 3.1 Pro on each entity's sprite image to extract structured
    properties (colors, texture, material, hardness, etc.).
    Updates scene dict in-place with a 'features' key.

    Args:
        api_key: Gemini API key.
        scene: Scene dict with sprite_code containing raw_sprite entries.

    Returns:
        The updated scene dict (also modified in-place).
    """
    from src.generation.feature_scanner import _scan_element

    sprite_code = scene.get("sprite_code", {})
    if not sprite_code:
        return scene

    client = genai.Client(api_key=api_key)

    # Build entity type lookup from manifest
    entity_types: Dict[str, str] = {}
    for ent in scene.get("manifest", {}).get("entities", []):
        entity_types[ent["id"]] = ent.get("type", "entity")

    # Collect entities to scan
    tasks = []
    entity_ids = []

    for eid, entry in sprite_code.items():
        if eid == "bg":
            continue
        if not isinstance(entry, dict) or entry.get("format") != "raw_sprite":
            continue
        # Convert sprite pixels to PNG for the feature scanner
        sprite = {"w": entry["w"], "h": entry["h"], "pixels": entry["pixels"]}
        image_bytes = _sprite_to_png(sprite)
        entity_ids.append(eid)
        tasks.append(
            _scan_element(
                client=client,
                element_id=eid,
                element_type=entity_types.get(eid, "entity"),
                image_bytes=image_bytes,
            )
        )

    if not tasks:
        return scene

    logger.info("[generate-features] Scanning features for %d entities...",
                len(tasks))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    features: Dict[str, Any] = {}
    for eid, result in zip(entity_ids, results):
        if isinstance(result, Exception):
            logger.warning("[generate-features] %s: failed: %s", eid, result)
        else:
            features[eid] = result.model_dump()
            logger.info("[generate-features] %s: %d global props, %d parts",
                        eid, len(result.global_properties), len(result.parts))

    scene["features"] = features
    return scene


async def _pipeline_with_reference_image(
    client: Any,
    user_prompt: str,
    story_state: Optional[StoryState],
    skip_masks: bool = False,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Run the 5-step pipeline:

    Step 1: Manifest (Gemini 3 Flash, text-only) — NEG is optional here
    Step 2a: Background image (Gemini image model) -> downscale -> base64 PNG
    Step 2b: Entity images x N (Gemini image model, red #FF0000 chroma-key) -> Pillow chroma key -> raw pixels
    Step 3: Mask generation (Gemini 3 Flash, receives entity images) -> sub-entity IDs
           (skipped when skip_masks=True — uses fallback root-entity-ID masks)
    Step 4: Assembly -> {bg: image_background, entity: raw_sprite, ...}

    Steps 2a and 2b run in parallel. Step 3 waits for 2b.
    Falls back to legacy single-call if Step 1 fails.
    """
    async def _notify(step_name: str) -> None:
        if progress_callback:
            try:
                await progress_callback(step_name)
            except Exception:
                pass  # Don't let callback errors break the pipeline

    try:
        await _notify("starting")

        # ── Step 1: Manifest (NEG optional) ──────────────────────────────────
        logger.info("[pipeline] Step 1: Generating manifest...")
        manifest_data = await _generate_manifest(client, user_prompt)
        scene_desc = manifest_data.get("scene_description", "")
        logger.info("[pipeline] Step 1 done. scene_description=%s", scene_desc[:80])
        await _notify("manifest")

        carried_over = manifest_data.get("carried_over_entities", [])
        if not isinstance(carried_over, list):
            carried_over = []

        # ── Step 2a + 2b: Background + Entity images (PARALLEL) ────────────
        background_changed = manifest_data.get("background_changed", True)
        reused_bg_sprite: Optional[Dict[str, Any]] = None

        # Check if we can reuse the background from story_state
        if not background_changed and story_state is not None:
            old_bg = story_state.get_entity_sprite("bg")
            if (old_bg and isinstance(old_bg, dict)
                    and old_bg.get("format") == "image_background"):
                reused_bg_sprite = old_bg
                logger.info("[pipeline] Step 2a: REUSING background "
                            "(background_changed=false)")

        bg_image_bytes: Optional[bytes] = None
        if reused_bg_sprite is not None:
            # Skip background generation, only generate entity images
            logger.info("[pipeline] Step 2: Generating entity images only "
                        "(background reused)...")
            entity_images = await _generate_entity_images_parallel(
                client, manifest_data, carried_over, scene_desc
            )
        else:
            # Generate both background and entity images in parallel
            logger.info("[pipeline] Step 2: Generating background + "
                        "entity images in parallel...")
            bg_task = _generate_background_image(client, manifest_data)
            entity_task = _generate_entity_images_parallel(
                client, manifest_data, carried_over, scene_desc
            )
            bg_image_bytes, entity_images = await asyncio.gather(
                bg_task, entity_task
            )

        if bg_image_bytes:
            logger.info("[pipeline] Step 2a done. Background: %d bytes",
                        len(bg_image_bytes))
        elif reused_bg_sprite is None:
            logger.warning("[pipeline] Step 2a: No background image generated!")

        logger.info("[pipeline] Step 2b done. Entity images: %s",
                    {eid: len(b) for eid, b in entity_images.items()})
        await _notify("images")

        # ── Pillow extraction (no LLM) ─────────────────────────────────────

        # Background -> image_background (base64 PNG, non-interactive)
        bg_sprite: Optional[Dict[str, Any]] = None
        if reused_bg_sprite is not None:
            bg_sprite = reused_bg_sprite
            logger.info("[pipeline] Using reused background sprite")
        elif bg_image_bytes:
            logger.info("[pipeline] Extracting background image...")
            bg_sprite = _extract_background_sprite(bg_image_bytes)

        # Entities -> raw pixels via chroma-key removal
        entity_sprites: Dict[str, Dict[str, Any]] = {}
        entity_sizes = {}
        for ent in manifest_data.get("manifest", {}).get("entities", []):
            entity_sizes[ent["id"]] = {
                "w": ent.get("width_hint", 50),
                "h": ent.get("height_hint", 60),
            }

        for eid, img_bytes in entity_images.items():
            size = entity_sizes.get(eid, {"w": 50, "h": 60})
            # Target dimensions in art-grid coordinates
            art_w = max(1, size["w"] // K)
            art_h = max(1, size["h"] // K)
            logger.info("[pipeline] Extracting sprite for %s (source %dx%d -> art %dx%d)...",
                        eid, size["w"], size["h"], art_w, art_h)
            entity_sprites[eid] = _extract_entity_sprite(
                img_bytes, art_w, art_h
            )

        # ── Step 3: Mask generation (entities + background in parallel) ────
        if skip_masks:
            logger.info("[pipeline] Step 3: SKIPPED (skip_masks=True), using fallback masks")
            entity_masks: Dict[str, List[Optional[str]]] = {
                eid: _build_fallback_mask(eid, sprite["pixels"])
                for eid, sprite in entity_sprites.items()
            }
        else:
            # Launch entity masks + background mask in parallel
            mask_tasks: List[Any] = []

            # Task 0: entity masks
            logger.info("[pipeline] Step 3: Generating masks for %d entities...",
                        len(entity_sprites))
            mask_tasks.append(
                _generate_masks_parallel(
                    client, entity_images, entity_sprites, manifest_data
                )
            )

            # Task 1: background mask (if bg_sprite has base64 data)
            launch_bg_mask = (
                bg_sprite is not None
                and bg_sprite.get("format") == "image_background"
                and bg_sprite.get("image_base64")
                and not bg_sprite.get("mask")
            )
            if launch_bg_mask:
                import base64 as _b64
                bg_png = _b64.b64decode(bg_sprite["image_base64"])
                bg_w = bg_sprite.get("width", ART_W)
                bg_h = bg_sprite.get("height", ART_H)
                logger.info("[pipeline] Step 3: Also generating bg mask (%dx%d)...",
                            bg_w, bg_h)
                mask_tasks.append(
                    _generate_mask_for_background(client, bg_png, bg_w, bg_h)
                )

            mask_results = await asyncio.gather(*mask_tasks, return_exceptions=True)

            # Entity masks result
            entity_masks = mask_results[0] if isinstance(mask_results[0], dict) else {
                eid: _build_fallback_mask(eid, sprite["pixels"])
                for eid, sprite in entity_sprites.items()
            }
            if isinstance(mask_results[0], Exception):
                logger.warning("[pipeline] Entity mask generation failed: %s",
                               mask_results[0])
            else:
                logger.info("[pipeline] Step 3 done. Masks: %s",
                            {eid: sum(1 for m in mask if m is not None)
                             for eid, mask in entity_masks.items()})

            # Background mask result
            if launch_bg_mask and len(mask_results) > 1:
                bg_mask_result = mask_results[1]
                if isinstance(bg_mask_result, list) and bg_mask_result:
                    bg_sprite["mask"] = bg_mask_result
                    logger.info("[pipeline] Step 3: bg mask done — %d unique IDs",
                                len(set(bg_mask_result)))
                elif isinstance(bg_mask_result, Exception):
                    logger.warning("[pipeline] Background mask failed: %s",
                                   bg_mask_result)

        await _notify("masks")

        # ── Step 4: Assembly ───────────────────────────────────────────────
        logger.info("[pipeline] Step 4: Assembling sprite_code...")
        entity_positions = _compute_entity_positions(manifest_data, entity_sprites)
        sprite_code = _assemble_sprite_code(
            bg_sprite, entity_sprites, entity_masks, entity_positions
        )

        # ── Step 4b: Backfill carried-over entities from StoryState ──────
        if story_state and carried_over:
            for eid in carried_over:
                if eid in sprite_code:
                    continue  # already assembled (shouldn't happen, but safe)
                old_sprite = story_state.get_entity_sprite(eid)
                if old_sprite and isinstance(old_sprite, dict):
                    # Copy sprite data with updated position from new manifest
                    reused = dict(old_sprite)
                    pos = entity_positions.get(eid)
                    if pos:
                        reused["x"] = pos["x"]
                        reused["y"] = pos["y"]
                    sprite_code[eid] = reused
                    logger.info("[pipeline] Reused carried-over sprite for %s at (%s,%s)",
                                eid, reused.get("x"), reused.get("y"))
                else:
                    logger.warning("[pipeline] Carried-over entity %s has no stored sprite", eid)

        logger.info("[pipeline] Step 4 done. %d entries: %s",
                    len(sprite_code), list(sprite_code.keys()))
        await _notify("assembly")

        # ── Validate and return ────────────────────────────────────────────
        manifest = SceneManifest.model_validate(manifest_data["manifest"])
        neg_data = manifest_data.get("neg")
        neg = NEG.model_validate(neg_data) if neg_data else NEG()

        result = {
            "narrative_text": manifest_data.get("narrative_text", ""),
            "branch_summary": manifest_data.get("branch_summary", ""),
            "scene_description": manifest_data.get("scene_description", ""),
            "background_description": manifest_data.get("background_description", ""),
            "manifest": manifest.model_dump(),
            "neg": neg.model_dump(),
            "sprite_code": sprite_code,
            "carried_over_entities": carried_over,
        }

        # Attach binary images for persistence (popped before JSON serialization)
        if bg_image_bytes:
            result["_reference_image_bytes"] = bg_image_bytes
        if entity_images:
            result["_entity_image_bytes"] = entity_images  # Dict[eid, bytes]

        return result

    except Exception as exc:
        logger.error(
            "Reference image pipeline failed: %s", exc
        )
        raise
