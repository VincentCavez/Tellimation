"""Scene generation via Gemini 3 Flash.

Supports two modes:
  - use_reference_images=True (default): 5-step pipeline
      1. Generate manifest + NEG (text-only, Gemini 3 Flash)
      2a. Generate background image (Gemini 2.5 Flash Image) → downscale → base64 PNG
      2b. Generate entity images × N (Gemini 2.5 Flash Image, on red #FF0000 chroma-key) → Pillow chroma key → raw pixels
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

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
IMAGE_MODEL_ID = "gemini-2.5-flash-image"


# ---------------------------------------------------------------------------
# Response text extraction (avoids thought_signature warnings)
# ---------------------------------------------------------------------------

def _get_response_text(response: Any) -> str:
    """Extract text from a Gemini response, skipping thinking/signature parts.

    When thinking_config is enabled, response.text triggers a warning about
    non-text parts (thought, thought_signature).  This helper accesses the
    parts directly and concatenates only the text ones.
    """
    if response.candidates and response.candidates[0].content:
        text_parts = []
        for part in response.candidates[0].content.parts:
            if hasattr(part, "text") and part.text is not None:
                text_parts.append(part.text)
        if text_parts:
            return "".join(text_parts)
    # Fallback: let the SDK handle it (may warn, but at least doesn't crash)
    return response.text


# ---------------------------------------------------------------------------
# Prompt builders (shared between legacy and pipeline)
# ---------------------------------------------------------------------------

def _build_initial_prompt(
    skill_objectives: List[str],
    seed_index: int,
) -> str:
    return INITIAL_SCENE_USER_PROMPT.format(
        skill_objectives=", ".join(skill_objectives),
        seed_index=seed_index,
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


# ---------------------------------------------------------------------------
# JSON extraction and validation
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from LLM response, handling markdown fences and trailing text.

    The model sometimes outputs valid JSON followed by extra commentary or
    duplicate JSON blocks (especially with high thinking budgets).  This
    helper tries several strategies:

    1. Strip markdown fences and parse.
    2. If that fails with "Extra data", use json.JSONDecoder to parse only
       the first complete JSON object and ignore the rest.
    3. Regex-extract the first ``{...}`` block (greedy, brace-balanced).
    """
    # Strip markdown code fences if present
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Strategy 1: plain parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_err:
        # Strategy 2: parse only the first JSON value (ignores trailing data)
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(cleaned)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 3: find the first brace-balanced {...} substring
        start = cleaned.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(cleaned)):
                ch = cleaned[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start : i + 1])
                        except json.JSONDecodeError:
                            break

        # Nothing worked — raise the original error
        raise first_err


def _validate_scene_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize the LLM response into canonical form."""
    # Validate manifest
    manifest_data = data.get("manifest")
    if not manifest_data:
        raise ValueError("Response missing 'manifest' field")
    manifest = SceneManifest.model_validate(manifest_data)

    # Validate NEG
    neg_data = data.get("neg")
    if not neg_data:
        raise ValueError("Response missing 'neg' field")
    neg = NEG.model_validate(neg_data)

    # Normalize sprite_code
    sprite_code = data.get("sprite_code", {})
    if not isinstance(sprite_code, dict):
        sprite_code = {}

    # Normalize carried_over_entities
    carried_over = data.get("carried_over_entities", [])
    if not isinstance(carried_over, list):
        carried_over = []

    return {
        "narrative_text": data.get("narrative_text", ""),
        "branch_summary": data.get("branch_summary", ""),
        "scene_description": data.get("scene_description", ""),
        "manifest": manifest.model_dump(),
        "neg": neg.model_dump(),
        "sprite_code": sprite_code,
        "carried_over_entities": carried_over,
    }


# ---------------------------------------------------------------------------
# Step 1: Manifest + NEG generation (text-only)
# ---------------------------------------------------------------------------

async def _generate_manifest(
    client: Any,
    user_prompt: str,
) -> Dict[str, Any]:
    """Step 1: Generate manifest + NEG (no sprite code).

    Returns:
        Raw parsed dict with narrative_text, branch_summary, scene_description,
        manifest, neg, carried_over_entities.
    """
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=MANIFEST_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            temperature=0.9,
            response_mime_type="application/json",
        ),
    )
    data = _extract_json(_get_response_text(response))

    # Validate manifest and NEG
    manifest_data = data.get("manifest")
    if not manifest_data:
        raise ValueError("Manifest response missing 'manifest' field")
    SceneManifest.model_validate(manifest_data)

    neg_data = data.get("neg")
    if not neg_data:
        raise ValueError("Manifest response missing 'neg' field")
    NEG.model_validate(neg_data)

    return data


# ---------------------------------------------------------------------------
# Step 2: Scene reference illustration generation
# ---------------------------------------------------------------------------

def _build_scene_image_prompt(manifest_data: Dict[str, Any]) -> str:
    """Build the prompt for generating a background-only illustration."""
    scene_desc = manifest_data.get("scene_description", "")
    return BACKGROUND_IMAGE_PROMPT_TEMPLATE.format(
        scene_description=scene_desc,
    )


# ---------------------------------------------------------------------------
# Step 2b: Generate individual entity images (Gemini 2.5 Flash Image)
# ---------------------------------------------------------------------------

def _build_entity_description(entity: Dict[str, Any]) -> str:
    """Build a rich text description of an entity for image generation."""
    props = entity.get("properties", {})
    etype = entity.get("type", "entity")
    size = props.get("size", "")
    color = props.get("color", "")
    texture = props.get("texture", "")
    pattern = props.get("pattern", "")
    distinctive = props.get("distinctive_features", "")

    desc = f"A {size} {color} {texture} {etype}".strip()
    if pattern:
        desc += f" with {pattern} pattern"
    if entity.get("emotion"):
        desc += f", looking {entity['emotion']}"
    if entity.get("pose"):
        desc += f", {entity['pose']}"
    if distinctive:
        desc += f". {distinctive}"
    return desc


async def _generate_entity_image(
    client: Any,
    entity: Dict[str, Any],
    scene_desc: str,
) -> Optional[bytes]:
    """Generate a single entity image on red chroma-key background.

    Uses Gemini 2.5 Flash Image to produce a pixel art sprite
    on solid #FF0000 red background.

    Returns:
        PNG image bytes, or None if generation fails.
    """
    entity_desc = _build_entity_description(entity)
    prompt = ENTITY_IMAGE_PROMPT.format(entity_description=entity_desc)

    try:
        response = await client.aio.models.generate_content(
            model=IMAGE_MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio="1:1",
                ),
            ),
        )

        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    logger.info("[entity-image] %s: got image (%d bytes)",
                                entity["id"], len(part.inline_data.data))
                    return part.inline_data.data

        logger.warning("[entity-image] %s: no image data returned", entity["id"])
        return None

    except Exception as exc:
        logger.warning("[entity-image] %s: generation failed: %s", entity["id"], exc)
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
    results = await asyncio.gather(*tasks, return_exceptions=True)

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


def _extract_entity_sprite(
    image_bytes: bytes,
    target_w: int,
    target_h: int,
) -> Dict[str, Any]:
    """Extract raw pixels from an entity image with chroma-key removal.

    1. Open the image at original resolution (e.g. 1024×1024)
    2. Detect background color by sampling corners (before downscale)
    3. Downscale to (target_w, target_h) with NEAREST (pixel art)
    4. Remove pixels close to the detected background color (Euclidean distance)

    Returns:
        Dict with keys: pixels (flat list of [r,g,b] or None), w, h
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    logger.info("[extract-sprite] Original: %dx%d -> target %dx%d",
                img.width, img.height, target_w, target_h)

    # Detect background color on the original (high-res) image
    bg_color = _detect_background_color(img)
    logger.info("[extract-sprite] Detected background color: rgb(%d, %d, %d)",
                bg_color[0], bg_color[1], bg_color[2])

    # Downscale to target size
    img = img.resize((target_w, target_h), Image.NEAREST)

    total = target_w * target_h
    bg_r, bg_g, bg_b = bg_color

    # Remove pixels close to the detected background color
    pixels: List[Optional[List[int]]] = []
    chroma_count = 0
    for y in range(target_h):
        for x in range(target_w):
            r, g, b = img.getpixel((x, y))
            if _is_background_pixel(r, g, b, bg_r, bg_g, bg_b):
                pixels.append(None)
                chroma_count += 1
            else:
                pixels.append([r, g, b])

    visible_count = total - chroma_count
    pct_removed = (chroma_count / total * 100) if total > 0 else 0
    logger.info("[extract-sprite] Extracted %d visible pixels out of %d total "
                "(%.1f%% background removed)",
                visible_count, total, pct_removed)

    return {"pixels": pixels, "w": target_w, "h": target_h}


def _extract_background_sprite(image_bytes: bytes) -> Dict[str, Any]:
    """Convert a background image to a base64 PNG for direct canvas rendering.

    The background is non-interactive (no entity IDs, no sub-entities), so
    we skip palette quantization entirely and just downscale + encode as
    base64 PNG.  The client renders it directly to the pixel buffer.

    1. Open image and downscale to 280×180 with nearest-neighbor
    2. Re-encode as PNG and base64

    Returns:
        Dict with format="image_background", width, height, image_base64.
    """
    import base64

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    logger.info("[extract-bg] Original: %dx%d", img.width, img.height)

    # Downscale to canvas size
    img = img.resize((280, 180), Image.NEAREST)

    # Re-encode as PNG
    out = io.BytesIO()
    img.save(out, format="PNG")
    png_bytes = out.getvalue()

    b64 = base64.b64encode(png_bytes).decode("ascii")
    logger.info("[extract-bg] image_background: 280x180, %d bytes PNG, %d chars base64",
                len(png_bytes), len(b64))

    return {
        "format": "image_background",
        "x": 0,
        "y": 0,
        "width": 280,
        "height": 180,
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

    Sends the entity image to Gemini 3 Flash along with the sprite dimensions,
    and gets back a mask in RLE format (run-length encoded).

    Returns:
        List of (entity_id string or None) with length sprite_w * sprite_h,
        or None if generation fails.
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

    try:
        response = await client.aio.models.generate_content(
            model=MODEL_ID,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_text,
                thinking_config=types.ThinkingConfig(thinking_budget=1024),
                temperature=0.3,
                response_mime_type="application/json",
            ),
        )

        data = _extract_json(_get_response_text(response))
        mask_raw = data.get("mask", [])

        if not isinstance(mask_raw, list):
            logger.warning("[mask] %s: mask is not a list", entity_id)
            return None

        # Auto-detect format: RLE vs legacy flat
        if _is_rle_format(mask_raw):
            logger.info("[mask] %s: detected RLE format (%d runs)",
                        entity_id, len(mask_raw))
            mask = _expand_rle_mask(mask_raw, total_pixels, entity_id, sprite_pixels)
        else:
            # Legacy flat format (backward compatibility)
            logger.info("[mask] %s: detected legacy flat format (%d elements)",
                        entity_id, len(mask_raw))
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
        logger.info("[mask] %s: %d masked pixels, %d unique sub-entity IDs",
                    entity_id, visible_mask, len(unique_ids))

        return mask

    except Exception as exc:
        logger.warning("[mask] %s: generation failed: %s", entity_id, exc)
        return None


def _build_fallback_mask(
    entity_id: str,
    pixels: List[Optional[List[int]]],
) -> List[Optional[str]]:
    """Build a simple fallback mask where all visible pixels get the root entity ID."""
    return [entity_id if p is not None else None for p in pixels]


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
    results = await asyncio.gather(*tasks, return_exceptions=True)

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
    """Downscale an image to exact canvas dimensions using nearest-neighbor.

    Nearest-neighbor preserves the blocky pixel-art aesthetic.
    Returns PNG bytes.
    """
    img = Image.open(io.BytesIO(image_bytes))
    logger.info("[downscale] Original image: %dx%d → target %dx%d",
                img.width, img.height, target_w, target_h)
    img = img.resize((target_w, target_h), Image.NEAREST)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def _generate_background_image(
    client: Any,
    manifest_data: Dict[str, Any],
) -> Optional[bytes]:
    """Step 2a: Generate a background-only illustration.

    Generates at 16:9 aspect ratio then downscales to 280×180
    (canvas size) using nearest-neighbor for pixel-art aesthetic.

    Returns:
        PNG image bytes at 280×180, or None if generation fails.
    """
    prompt = _build_scene_image_prompt(manifest_data)

    try:
        response = await client.aio.models.generate_content(
            model=IMAGE_MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio="16:9",
                ),
            ),
        )

        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    raw_bytes = part.inline_data.data
                    return _downscale_to_canvas(raw_bytes)

        logger.warning("[bg-image] No image data returned")
        return None

    except Exception as exc:
        logger.warning("[bg-image] Generation failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Step 4: Assembly — combine background + entity sprites + masks
# ---------------------------------------------------------------------------

def _assemble_sprite_code(
    bg_sprite: Optional[Dict[str, Any]],
    entity_sprites: Dict[str, Dict[str, Any]],
    entity_masks: Dict[str, List[Optional[str]]],
    manifest_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble the final sprite_code dict from all pipeline outputs.

    Returns:
        Dict mapping entity_id -> sprite data:
        - "bg" -> image_background dict (or legacy palette_grid)
        - entity_id -> raw_sprite dict
    """
    sprite_code: Dict[str, Any] = {}

    # Background
    if bg_sprite:
        sprite_code["bg"] = bg_sprite
        bg_fmt = bg_sprite.get("format", "unknown")
        logger.info("[assemble] bg: %s 280x180", bg_fmt)

    # Entity positions from manifest
    entity_positions = {}
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        eid = ent["id"]
        pos = ent.get("position", {})
        w = ent.get("width_hint", 50)
        h = ent.get("height_hint", 60)
        # Position is center; compute top-left
        entity_positions[eid] = {
            "x": pos.get("x", 0) - w // 2,
            "y": pos.get("y", 0) - h // 2,
            "w": w,
            "h": h,
        }

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
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SCENE_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            temperature=0.9,
            response_mime_type="application/json",
        ),
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
    seed_index: int = 0,
    commit_to_state: bool = True,
    extra_prompt: str = "",
    use_reference_images: bool = True,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Generate a single scene via Gemini 3 Flash.

    Args:
        api_key: Gemini API key.
        story_state: Cumulative story state, or None for initial scene.
        student_profile: Child's error profile, or None for initial scene.
        skill_objectives: SKILL objectives for the session.
        seed_index: Seed for variety (1, 2, 3 for initial thumbnails).
        commit_to_state: If True, call story_state.add_scene() with the result.
            Set to False for candidate branches that shouldn't mutate state.
        extra_prompt: Additional text appended to the user prompt (e.g. branch directive).
        use_reference_images: If True, use 5-step pipeline with reference image.
            If False, use legacy single-call pipeline.
        progress_callback: Optional async callback called after each pipeline step.
            Receives a step name string: "manifest", "images", "masks", "assembly".

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
        user_prompt = _build_initial_prompt(skill_objectives, seed_index)
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
            client, user_prompt, story_state, progress_callback
        )
    else:
        result = await _generate_scene_legacy(client, user_prompt)

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


async def _pipeline_with_reference_image(
    client: Any,
    user_prompt: str,
    story_state: Optional[StoryState],
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Run the 5-step pipeline:

    Step 1: Manifest + NEG (Gemini 3 Flash, text-only)
    Step 2a: Background image (Gemini 2.5 Flash Image) -> downscale -> base64 PNG
    Step 2b: Entity images x N (Gemini 2.5 Flash Image, red #FF0000 chroma-key) -> Pillow chroma key -> raw pixels
    Step 3: Mask generation (Gemini 3 Flash, receives entity images) -> sub-entity IDs
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
        # ── Step 1: Manifest + NEG ──────────────────────────────────────────
        logger.info("[pipeline] Step 1: Generating manifest + NEG...")
        manifest_data = await _generate_manifest(client, user_prompt)
        scene_desc = manifest_data.get("scene_description", "")
        logger.info("[pipeline] Step 1 done. scene_description=%s", scene_desc[:80])
        await _notify("manifest")

        carried_over = manifest_data.get("carried_over_entities", [])
        if not isinstance(carried_over, list):
            carried_over = []

        # ── Step 2a + 2b: Background + Entity images (PARALLEL) ────────────
        logger.info("[pipeline] Step 2: Generating background + entity images in parallel...")

        bg_task = _generate_background_image(client, manifest_data)
        entity_task = _generate_entity_images_parallel(
            client, manifest_data, carried_over, scene_desc
        )

        bg_image_bytes, entity_images = await asyncio.gather(bg_task, entity_task)

        if bg_image_bytes:
            logger.info("[pipeline] Step 2a done. Background: %d bytes", len(bg_image_bytes))
        else:
            logger.warning("[pipeline] Step 2a: No background image generated!")

        logger.info("[pipeline] Step 2b done. Entity images: %s",
                    {eid: len(b) for eid, b in entity_images.items()})
        await _notify("images")

        # ── Pillow extraction (no LLM) ─────────────────────────────────────

        # Background -> image_background (base64 PNG, non-interactive)
        bg_sprite: Optional[Dict[str, Any]] = None
        if bg_image_bytes:
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
            logger.info("[pipeline] Extracting sprite for %s (%dx%d)...",
                        eid, size["w"], size["h"])
            entity_sprites[eid] = _extract_entity_sprite(
                img_bytes, size["w"], size["h"]
            )

        # ── Step 3: Mask generation ────────────────────────────────────────
        logger.info("[pipeline] Step 3: Generating masks for %d entities...",
                    len(entity_sprites))

        entity_masks = await _generate_masks_parallel(
            client, entity_images, entity_sprites, manifest_data
        )
        logger.info("[pipeline] Step 3 done. Masks: %s",
                    {eid: sum(1 for m in mask if m is not None)
                     for eid, mask in entity_masks.items()})
        await _notify("masks")

        # ── Step 4: Assembly ───────────────────────────────────────────────
        logger.info("[pipeline] Step 4: Assembling sprite_code...")
        sprite_code = _assemble_sprite_code(
            bg_sprite, entity_sprites, entity_masks, manifest_data
        )
        logger.info("[pipeline] Step 4 done. %d entries: %s",
                    len(sprite_code), list(sprite_code.keys()))
        await _notify("assembly")

        # ── Validate and return ────────────────────────────────────────────
        manifest = SceneManifest.model_validate(manifest_data["manifest"])
        neg = NEG.model_validate(manifest_data["neg"])

        result = {
            "narrative_text": manifest_data.get("narrative_text", ""),
            "branch_summary": manifest_data.get("branch_summary", ""),
            "scene_description": manifest_data.get("scene_description", ""),
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
        logger.warning(
            "Reference image pipeline failed, falling back to legacy: %s", exc
        )
        return await _generate_scene_legacy(client, user_prompt)
