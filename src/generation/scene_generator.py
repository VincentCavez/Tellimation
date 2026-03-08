"""Scene asset generation via Nano Banana 2.

Pipeline:
  1. Generate background HD (Nano Banana 2, 16:9)
  2. Generate entity HD images × N (Nano Banana 2, magenta #FF00FF chroma-key)
  3. Remove magenta background programmatically (Pillow)
  4. Downscale everything to pixel art (NEAREST neighbor)
  5. Compose sprites on background using manifest positions

The manifest + NEG are generated separately by scene_neg_generator.py.
This module only handles image generation and sprite assembly.
"""

from __future__ import annotations

import asyncio
import base64
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
)
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

# Nano Banana 2 for image generation
IMAGE_MODEL_ID = "gemini-3.1-flash-image-preview"

# Timeouts (seconds)
IMAGE_TIMEOUT = 120
IMAGE_MAX_RETRIES = 2

# Resolution model (must match engine.js)
SOURCE_W = 1120   # manifest coordinates
SOURCE_H = 720
K = 4             # pixel-art aggregation factor
ART_W = SOURCE_W // K   # 280
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
# Prompt builders
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
# Entity description builder (sanitizes cross-entity references)
# ---------------------------------------------------------------------------

_CROSS_ENTITY_RE = re.compile(
    r"""\b
    (against|on|upon|beside|under|beneath|below|above|from|near|
     of|into|onto|atop|off|behind|inside|within|along|around|over|
     next\s+to|in\s+front\s+of|on\s+top\s+of|attached\s+to|
     resting\s+on|resting\s+against|leaning\s+against|leaning\s+on|
     stuck\s+to|pinned\s+to|nailed\s+to|hanging\s+from|
     growing\s+from|sprouting\s+from|emerging\s+from)
    \s+(?:the|a|an)\s+
    [\w\s,'-]{1,40}?
    (?=\s*[.,;!?]|\s*$|\s+(?:and|with|while|but|looking|head|tail|ears|body))
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _sanitize_for_isolation(text: str) -> str:
    """Remove references to other entities from a description field."""
    if not text:
        return text
    text = _CROSS_ENTITY_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"^\s*[,;]\s*", "", text)
    return text.strip().rstrip(",").strip()


def _build_entity_description(entity: Dict[str, Any]) -> str:
    """Build a rich text description of an entity for image generation.

    Sanitizes ``pose`` and ``distinctive_features`` to remove cross-entity
    references that would cause the image model to draw other scene elements.
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


# ---------------------------------------------------------------------------
# Magenta chroma-key removal
# ---------------------------------------------------------------------------

def _is_magenta_background(r: int, g: int, b: int) -> bool:
    """Detect magenta chroma-key background pixels (#FF00FF).

    A pixel is magenta background if:
    - R > 200
    - G < 60
    - B > 200
    """
    return r > 200 and g < 60 and b > 200


def _remove_magenta(image_bytes: bytes) -> Image.Image:
    """Remove magenta (#FF00FF) background from an entity image.

    Converts RGB image to RGBA, setting magenta pixels to fully transparent.
    Uses the rule: R>200 AND G<60 AND B>200 → alpha=0.

    Returns:
        PIL Image in RGBA mode with magenta removed.
    """
    import numpy as np

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img, dtype=np.uint8)  # (H, W, 3)

    # Magenta mask: R>200 AND G<60 AND B>200
    magenta = (arr[:, :, 0] > 200) & (arr[:, :, 1] < 60) & (arr[:, :, 2] > 200)

    # Build RGBA
    alpha = np.where(magenta, 0, 255).astype(np.uint8)
    rgba_arr = np.zeros((*arr.shape[:2], 4), dtype=np.uint8)
    rgba_arr[:, :, :3] = arr
    rgba_arr[:, :, 3] = alpha

    return Image.fromarray(rgba_arr, "RGBA")


# ---------------------------------------------------------------------------
# Downscale (NEAREST neighbor for pixel art)
# ---------------------------------------------------------------------------

def _downscale(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Downscale image using NEAREST neighbor resampling.

    For pixel art aesthetic — no smoothing, no anti-aliasing.
    Preserves aspect ratio within target bounds.

    Args:
        img: PIL Image (RGB or RGBA).
        target_w: Maximum width.
        target_h: Maximum height.

    Returns:
        Downscaled PIL Image.
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return img

    scale = min(target_w / w, target_h / h)
    final_w = max(1, round(w * scale))
    final_h = max(1, round(h * scale))

    return img.resize((final_w, final_h), Image.NEAREST)


def _downscale_background(image_bytes: bytes) -> Dict[str, Any]:
    """Downscale a background image to art-grid resolution as base64 PNG.

    Uses NEAREST neighbor for pixel art aesthetic.

    Returns:
        Dict with format="image_background", width, height, image_base64.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    logger.info("[downscale-bg] Original: %dx%d -> art grid %dx%d",
                img.width, img.height, ART_W, ART_H)

    img = img.resize((ART_W, ART_H), Image.NEAREST)

    out = io.BytesIO()
    img.save(out, format="PNG")
    png_bytes = out.getvalue()

    b64 = base64.b64encode(png_bytes).decode("ascii")
    logger.info("[downscale-bg] image_background: %dx%d, %d bytes PNG",
                ART_W, ART_H, len(png_bytes))

    return {
        "format": "image_background",
        "width": ART_W,
        "height": ART_H,
        "image_base64": b64,
    }


def _downscale_entity(
    rgba_img: Image.Image,
    target_w: int,
    target_h: int,
) -> Dict[str, Any]:
    """Crop to content bbox, downscale NEAREST, convert to pixel list.

    Args:
        rgba_img: RGBA image with magenta already removed.
        target_w: Target width in art-grid pixels.
        target_h: Target height in art-grid pixels.

    Returns:
        Dict with keys: pixels (flat list of [r,g,b] or None), w, h.
    """
    # Crop to content via alpha channel
    bbox = rgba_img.split()[3].getbbox()
    if bbox:
        content_w = bbox[2] - bbox[0]
        content_h = bbox[3] - bbox[1]
        logger.info("[downscale-entity] Content bbox: (%d,%d)-(%d,%d) = %dx%d",
                    bbox[0], bbox[1], bbox[2], bbox[3], content_w, content_h)
        rgba_img = rgba_img.crop(bbox)
    else:
        content_w, content_h = rgba_img.size
        logger.warning("[downscale-entity] No content found, using full image")

    # Downscale NEAREST
    if content_w > 0 and content_h > 0:
        scale = min(target_w / content_w, target_h / content_h)
        final_w = max(1, round(content_w * scale))
        final_h = max(1, round(content_h * scale))
    else:
        final_w, final_h = target_w, target_h

    logger.info("[downscale-entity] %dx%d -> %dx%d (target %dx%d)",
                content_w, content_h, final_w, final_h, target_w, target_h)
    rgba_img = rgba_img.resize((final_w, final_h), Image.NEAREST)

    # Convert to pixel list
    total = final_w * final_h
    pixels: List[Optional[List[int]]] = []
    transparent_count = 0
    for y in range(final_h):
        for x in range(final_w):
            r, g, b, a = rgba_img.getpixel((x, y))
            if a < 128:
                pixels.append(None)
                transparent_count += 1
            else:
                pixels.append([r, g, b])

    visible_count = total - transparent_count
    pct_removed = (transparent_count / total * 100) if total > 0 else 0
    logger.info("[downscale-entity] %d visible / %d total (%.1f%% background removed)",
                visible_count, total, pct_removed)

    return {"pixels": pixels, "w": final_w, "h": final_h}


# ---------------------------------------------------------------------------
# Image generation (Nano Banana 2)
# ---------------------------------------------------------------------------

async def _generate_background(
    client: Any,
    manifest_data: Dict[str, Any],
) -> Optional[bytes]:
    """Generate a background-only HD illustration with Nano Banana 2.

    Generates at 16:9 aspect ratio. Returns raw HD image bytes.
    Retries up to IMAGE_MAX_RETRIES times.

    Returns:
        PNG image bytes (HD), or None if all attempts fail.
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
                        logger.info("[bg] Attempt %d/%d: got %d bytes",
                                    attempt, IMAGE_MAX_RETRIES,
                                    len(part.inline_data.data))
                        return part.inline_data.data

            logger.warning("[bg] Attempt %d/%d: no image data",
                           attempt, IMAGE_MAX_RETRIES)

        except Exception as exc:
            logger.warning("[bg] Attempt %d/%d failed (%s): %s",
                           attempt, IMAGE_MAX_RETRIES,
                           type(exc).__name__, exc or "no details")

    logger.warning("[bg] All %d attempts exhausted", IMAGE_MAX_RETRIES)
    return None


async def _generate_entity(
    client: Any,
    entity: Dict[str, Any],
) -> Optional[bytes]:
    """Generate a single entity image on magenta (#FF00FF) background.

    Uses Nano Banana 2. Retries up to IMAGE_MAX_RETRIES times.

    Returns:
        PNG image bytes, or None if all attempts fail.
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

            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        logger.info("[entity] %s: attempt %d/%d got %d bytes",
                                    eid, attempt, IMAGE_MAX_RETRIES,
                                    len(part.inline_data.data))
                        return part.inline_data.data

            logger.warning("[entity] %s: attempt %d/%d no image data",
                           eid, attempt, IMAGE_MAX_RETRIES)

        except Exception as exc:
            logger.warning("[entity] %s: attempt %d/%d failed (%s): %s",
                           eid, attempt, IMAGE_MAX_RETRIES,
                           type(exc).__name__, exc or "no details")

    logger.warning("[entity] %s: all %d attempts exhausted", eid, IMAGE_MAX_RETRIES)
    return None


# ---------------------------------------------------------------------------
# Position computation
# ---------------------------------------------------------------------------

def _compute_entity_positions(
    manifest_data: Dict[str, Any],
    entity_sprites: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, int]]:
    """Compute top-left positions and sizes in art-grid coordinates.

    Manifest positions are in source coordinates (0-1119, 0-719).
    Converts to art-grid coordinates (0-ART_W-1, 0-ART_H-1).

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

        positions[eid] = {"x": x, "y": y, "w": w, "h": h}

    # Diagnostic: warn if entity feet are above canonical ground line
    canonical_ground_art_y = SOURCE_H * 70 // (100 * K)  # ~126
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


# ---------------------------------------------------------------------------
# Fallback mask (root entity ID for all visible pixels)
# ---------------------------------------------------------------------------

def _build_fallback_mask(
    entity_id: str,
    pixels: List[Optional[List[int]]],
) -> List[Optional[str]]:
    """Build a simple fallback mask where all visible pixels get the root entity ID."""
    return [entity_id if p is not None else None for p in pixels]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _assemble_sprite_code(
    bg_sprite: Optional[Dict[str, Any]],
    entity_sprites: Dict[str, Dict[str, Any]],
    entity_masks: Dict[str, List[Optional[str]]],
    entity_positions: Dict[str, Dict[str, int]],
) -> Dict[str, Any]:
    """Assemble the final sprite_code dict from all pipeline outputs.

    Returns:
        Dict mapping entity_id -> sprite data:
        - "bg" -> image_background dict
        - entity_id -> raw_sprite dict
    """
    sprite_code: Dict[str, Any] = {}

    # Background
    if bg_sprite:
        sprite_code["bg"] = bg_sprite
        logger.info("[assemble] bg: %s %dx%d",
                    bg_sprite.get("format", "unknown"),
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
        logger.info("[assemble] %s: raw_sprite %dx%d at (%d,%d), %d visible px",
                    eid, sprite["w"], sprite["h"], pos["x"], pos["y"], visible)

    return sprite_code


# ---------------------------------------------------------------------------
# Compose scene (main function for image pipeline)
# ---------------------------------------------------------------------------

def _compose_scene(
    bg_sprite: Optional[Dict[str, Any]],
    entity_sprites: Dict[str, Dict[str, Any]],
    entity_positions: Dict[str, Dict[str, int]],
) -> Dict[str, Any]:
    """Compose sprites onto background and assemble final sprite_code.

    Uses fallback masks (root entity ID) for all entities.
    Mask generation is handled separately if needed.

    Returns:
        sprite_code dict ready for the client.
    """
    entity_masks = {
        eid: _build_fallback_mask(eid, sprite["pixels"])
        for eid, sprite in entity_sprites.items()
    }

    return _assemble_sprite_code(
        bg_sprite, entity_sprites, entity_masks, entity_positions
    )


# ---------------------------------------------------------------------------
# Public API: generate_scene_assets
# ---------------------------------------------------------------------------

async def generate_scene_assets(
    api_key: str,
    manifest_data: Dict[str, Any],
    story_state: Optional[StoryState] = None,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Generate all visual assets for a scene from its manifest.

    Pipeline:
      1. Generate background HD (Nano Banana 2, 16:9) — or reuse from story_state
      2. Generate entity HD images × N (Nano Banana 2, magenta #FF00FF)
         — steps 1 and 2 run in parallel
      3. Remove magenta background (Pillow)
      4. Downscale to pixel art (NEAREST neighbor)
      5. Compose sprites on background

    Args:
        api_key: Gemini API key.
        manifest_data: Scene manifest dict (from scene_neg_generator).
            Must contain "manifest" with entities, and optionally
            "background_description" / "scene_description".
        story_state: Optional story state for reusing carried-over sprites.
        progress_callback: Optional async callback for progress updates.

    Returns:
        Dict with sprite_code (ready for client), plus metadata:
        - sprite_code: {bg: image_background, entity_id: raw_sprite, ...}
        - carried_over_entities: list of reused entity IDs
    """
    client = genai.Client(api_key=api_key)

    async def _notify(step: str) -> None:
        if progress_callback:
            try:
                await progress_callback(step)
            except Exception:
                pass

    await _notify("starting")

    carried_over = manifest_data.get("carried_over_entities", [])
    if not isinstance(carried_over, list):
        carried_over = []

    background_changed = manifest_data.get("background_changed", True)

    # --- Check if we can reuse background from story_state ---
    reused_bg_sprite: Optional[Dict[str, Any]] = None
    if not background_changed and story_state is not None:
        old_bg = story_state.get_entity_sprite("bg")
        if (old_bg and isinstance(old_bg, dict)
                and old_bg.get("format") == "image_background"):
            reused_bg_sprite = old_bg
            logger.info("[assets] Reusing background (background_changed=false)")

    # --- Collect entities to generate (skip carried_over) ---
    entities_to_generate = []
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        if ent.get("id") in carried_over or ent.get("carried_over"):
            continue
        entities_to_generate.append(ent)

    # --- Step 1+2: Background + Entity images (PARALLEL) ---
    logger.info("[assets] Generating %s + %d entity images...",
                "background" if reused_bg_sprite is None else "NO background (reused)",
                len(entities_to_generate))

    bg_task = None
    entity_tasks = []

    if reused_bg_sprite is None:
        bg_task = _generate_background(client, manifest_data)

    for ent in entities_to_generate:
        entity_tasks.append(_generate_entity(client, ent))

    # Run all image generation in parallel
    all_tasks = []
    if bg_task:
        all_tasks.append(bg_task)
    all_tasks.extend(entity_tasks)

    if all_tasks:
        results = await asyncio.gather(*all_tasks, return_exceptions=True)
    else:
        results = []

    # Split results
    bg_image_bytes: Optional[bytes] = None
    entity_results_start = 0
    if bg_task:
        bg_result = results[0]
        if isinstance(bg_result, bytes):
            bg_image_bytes = bg_result
            logger.info("[assets] Background: %d bytes", len(bg_image_bytes))
        elif isinstance(bg_result, Exception):
            logger.warning("[assets] Background generation failed: %s", bg_result)
        else:
            logger.warning("[assets] Background: no image generated")
        entity_results_start = 1

    entity_images: Dict[str, bytes] = {}
    for i, ent in enumerate(entities_to_generate):
        idx = entity_results_start + i
        result = results[idx] if idx < len(results) else None
        eid = ent["id"]
        if isinstance(result, bytes):
            entity_images[eid] = result
        elif isinstance(result, Exception):
            logger.warning("[assets] %s: image generation failed: %s", eid, result)
        else:
            logger.warning("[assets] %s: no image generated", eid)

    logger.info("[assets] Generated %d/%d entity images",
                len(entity_images), len(entities_to_generate))
    await _notify("images")

    # --- Step 3+4: Magenta removal + Downscale ---
    bg_sprite: Optional[Dict[str, Any]] = None
    if reused_bg_sprite is not None:
        bg_sprite = reused_bg_sprite
    elif bg_image_bytes:
        bg_sprite = _downscale_background(bg_image_bytes)

    entity_sprites: Dict[str, Dict[str, Any]] = {}
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        eid = ent["id"]
        if eid not in entity_images:
            continue
        # Target dimensions in art-grid coordinates
        art_w = max(1, ent.get("width_hint", 50) // K)
        art_h = max(1, ent.get("height_hint", 60) // K)

        logger.info("[assets] Processing %s: magenta removal + downscale -> %dx%d",
                    eid, art_w, art_h)
        rgba = _remove_magenta(entity_images[eid])
        entity_sprites[eid] = _downscale_entity(rgba, art_w, art_h)

    await _notify("processing")

    # --- Step 5: Compose ---
    entity_positions = _compute_entity_positions(manifest_data, entity_sprites)
    sprite_code = _compose_scene(bg_sprite, entity_sprites, entity_positions)

    # Backfill carried-over entities from story_state
    if story_state and carried_over:
        for eid in carried_over:
            if eid in sprite_code:
                continue
            old_sprite = story_state.get_entity_sprite(eid)
            if old_sprite and isinstance(old_sprite, dict):
                reused = dict(old_sprite)
                pos = entity_positions.get(eid)
                if pos:
                    reused["x"] = pos["x"]
                    reused["y"] = pos["y"]
                sprite_code[eid] = reused
                logger.info("[assets] Reused carried-over sprite for %s at (%s,%s)",
                            eid, reused.get("x"), reused.get("y"))
            else:
                logger.warning("[assets] Carried-over entity %s has no stored sprite", eid)

    logger.info("[assets] Done. %d sprite entries: %s",
                len(sprite_code), list(sprite_code.keys()))
    await _notify("assembly")

    return {
        "sprite_code": sprite_code,
        "carried_over_entities": carried_over,
    }


# ---------------------------------------------------------------------------
# DEPRECATED — Legacy code below, kept for reference
# ---------------------------------------------------------------------------

# These are preserved for backward compatibility but should not be used
# in new code. The new pipeline uses generate_scene_assets() above.

MODEL_ID = "gemini-3-flash-preview"  # DEPRECATED: legacy manifest model
MANIFEST_TIMEOUT = 60
MANIFEST_MAX_RETRIES = 2
MASK_MODEL_ID = "gemini-2.5-flash"
MASK_TIMEOUT = 60
MASK_MAX_RETRIES = 2


def _build_initial_prompt(  # DEPRECATED
    skill_objectives: List[str],
    theme: str,
) -> str:
    return INITIAL_SCENE_USER_PROMPT.format(
        skill_objectives=", ".join(skill_objectives),
        theme=theme,
    )


def _build_continuation_prompt(  # DEPRECATED
    story_state: StoryState,
    student_profile: Optional[StudentProfile],
    skill_objectives: List[str],
) -> str:
    story_lines = []
    for s in story_state.scenes:
        story_lines.append(
            f"- {s.get('scene_id', '?')}: {s.get('narrative_text', '')}"
        )
    story_context = "\n".join(story_lines) if story_lines else "(first scene)"
    previous_manifest = "{}"
    if story_state.scenes:
        last = story_state.scenes[-1]
        previous_manifest = json.dumps(last.get("manifest", {}), indent=2)
    entity_lines = []
    for eid, ent in story_state.active_entities.items():
        entity_lines.append(
            f"- {eid} (type={ent.type}, appeared={ent.first_appeared}, "
            f"pos={ent.last_position})"
        )
    active_entities = "\n".join(entity_lines) if entity_lines else "(none)"
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


def _validate_scene_response(data: Dict[str, Any]) -> Dict[str, Any]:  # DEPRECATED
    """Validate and normalize the LLM response into canonical form."""
    manifest_data = data.get("manifest")
    if not manifest_data:
        raise ValueError("Response missing 'manifest' field")
    manifest = SceneManifest.model_validate(manifest_data)
    neg_data = data.get("neg")
    neg = NEG.model_validate(neg_data) if neg_data else NEG()
    sprite_code = data.get("sprite_code", {})
    if not isinstance(sprite_code, dict):
        sprite_code = {}
    carried_over = data.get("carried_over_entities", [])
    if not isinstance(carried_over, list):
        carried_over = []
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


async def _generate_manifest(  # DEPRECATED
    client: Any,
    user_prompt: str,
) -> Dict[str, Any]:
    """Step 1: Generate manifest (no sprite code). DEPRECATED."""
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
            manifest_data = data.get("manifest")
            if not manifest_data:
                raise ValueError("Manifest response missing 'manifest' field")
            SceneManifest.model_validate(manifest_data)
            neg_data = data.get("neg")
            if neg_data:
                NEG.model_validate(neg_data)
            return data
        except asyncio.TimeoutError:
            logger.warning("[manifest] Attempt %d/%d timed out",
                           attempt, MANIFEST_MAX_RETRIES)
            last_exc = asyncio.TimeoutError()
        except Exception as exc:
            logger.warning("[manifest] Attempt %d/%d failed: %s",
                           attempt, MANIFEST_MAX_RETRIES, exc)
            last_exc = exc
    raise last_exc  # type: ignore[misc]


async def _generate_scene_legacy(  # DEPRECATED
    client: Any,
    user_prompt: str,
) -> Dict[str, Any]:
    """Legacy all-in-one generation. DEPRECATED."""
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


async def _pipeline_with_reference_image(  # DEPRECATED
    client: Any,
    user_prompt: str,
    story_state: Optional[StoryState],
    skip_masks: bool = False,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Old 5-step pipeline. DEPRECATED — use generate_scene_assets() instead."""
    raise NotImplementedError(
        "DEPRECATED: Use generate_scene_assets() with the new pipeline. "
        "The old 5-step pipeline (manifest → images → masks → assembly) "
        "has been replaced by the Nano Banana 2 pipeline."
    )


async def generate_scene(  # DEPRECATED
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
    """DEPRECATED — use scene_neg_generator + generate_scene_assets() instead.

    This function combined manifest generation + image pipeline in one call.
    The new architecture separates these concerns:
      1. scene_neg_generator.generate_scene_and_neg() → manifest + NEG
      2. generate_scene_assets() → images + sprites
    """
    raise NotImplementedError(
        "DEPRECATED: Use scene_neg_generator.generate_scene_and_neg() "
        "for manifest + NEG, then generate_scene_assets() for images."
    )


async def generate_masks_for_scene(  # DEPRECATED
    api_key: str,
    scene: Dict[str, Any],
) -> Dict[str, Any]:
    """DEPRECATED — masks are no longer generated separately."""
    raise NotImplementedError("DEPRECATED: Mask generation removed from pipeline.")


async def generate_features_for_scene(  # DEPRECATED
    api_key: str,
    scene: Dict[str, Any],
) -> Dict[str, Any]:
    """DEPRECATED — features are embedded in the manifest via scene_neg_generator."""
    raise NotImplementedError("DEPRECATED: Feature scanning removed from pipeline.")
