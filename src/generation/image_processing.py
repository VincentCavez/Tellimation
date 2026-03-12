"""Image processing utilities for scene asset generation.

Extracted from scene_generator.py — contains:
  - Resolution constants
  - Prompt building helpers
  - Magenta chroma-key removal
  - Downscale functions (NEAREST neighbor for pixel art)
  - Background and entity image generation (Nano Banana 2)
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from PIL import Image

from google import genai
from google.genai import types

from src.generation.prompts.scene_prompt import BACKGROUND_IMAGE_PROMPT_TEMPLATE
from src.generation.prompts.sprite_prompt import ENTITY_IMAGE_PROMPT

logger = logging.getLogger(__name__)

# Nano Banana 2 for image generation
IMAGE_MODEL_ID = "gemini-3.1-flash-image-preview"

# Timeouts (seconds)
IMAGE_TIMEOUT = 120
IMAGE_MAX_RETRIES = 3

# Resolution model (must match engine.js)
SOURCE_W = 1120   # HD image resolution (not used for manifest coords — those are normalized)
SOURCE_H = 720
K = 4             # pixel-art aggregation factor (4×4 HD pixels → 1 art pixel)
ART_W = SOURCE_W // K   # 280
ART_H = SOURCE_H // K   # 180


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

    # Use background.ground_line if available, else compute from entity feet
    bg_data = manifest_data.get("manifest", {}).get("background")
    if bg_data and isinstance(bg_data, dict) and "ground_line" in bg_data:
        pct = round(bg_data["ground_line"] * 100)
        ground_level_hint = (
            f"The ground/floor surface MUST be at approximately "
            f"{pct}% from the top of the image. "
            f"Characters will be composited on top of this background at that level."
        )
    else:
        # Fallback: compute ground level from entity foot positions (normalized)
        entities = manifest_data.get("manifest", {}).get("entities", [])
        foot_positions: list[float] = []
        for ent in entities:
            pos = ent.get("position", {})
            y_center = pos.get("y", 0.0)
            h_hint = ent.get("height_hint", 0.28)
            foot_positions.append(y_center + h_hint / 2)

        if foot_positions:
            avg_foot = sum(foot_positions) / len(foot_positions)
            pct = round(avg_foot * 100)
            ground_level_hint = (
                f"Characters will stand with their feet at approximately "
                f"{pct}% from the top of the image. "
                f"The ground/floor surface MUST be clearly visible at this level."
            )
        else:
            ground_level_hint = (
                "The ground or floor should be at approximately 70% from the top."
            )

    # Build structural element placement hints from manifest positions
    # NOTE: Only use natural language placement (left/right/center) — NEVER
    # include numeric coordinates.  The image model renders them as literal
    # text artefacts on the illustration.
    structural_hints = ""
    bg_data_se = manifest_data.get("manifest", {}).get("background", {})
    structural_elements = bg_data_se.get("structural_elements", []) if isinstance(bg_data_se, dict) else []
    if structural_elements:
        lines = ["\n## Structural element placement (CRITICAL)"]
        lines.append(
            "The following elements MUST appear in the background at the "
            "described positions. Do NOT draw any text, labels, numbers, or "
            "coordinates on the image."
        )
        for se in structural_elements:
            if isinstance(se, dict):
                name = se.get("name", "unknown")
                sx = se.get("x", 0.5)
                sy = se.get("y", 0.5)
                h_pct = "on the left side" if sx < 0.35 else "on the right side" if sx > 0.65 else "in the center"
                v_pct = "near the top" if sy < 0.35 else "near the bottom" if sy > 0.65 else "in the middle"
                lines.append(f"- {name}: {h_pct}, {v_pct} of the image")
            elif isinstance(se, str):
                lines.append(f"- {se}")
        structural_hints = "\n".join(lines)

    return BACKGROUND_IMAGE_PROMPT_TEMPLATE.format(
        scene_description=bg_desc,
        ground_level_hint=ground_level_hint,
    ) + structural_hints


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

    # Orientation — tell the image model which direction the entity faces
    orientation = entity.get("orientation", "")
    if orientation == "facing_left":
        desc += ". FACING LEFT — the character looks toward the LEFT side of the image."
    elif orientation == "facing_right":
        desc += ". FACING RIGHT — the character looks toward the RIGHT side of the image."
    elif orientation == "facing_viewer":
        desc += ". FACING THE VIEWER — the character looks directly at the camera."

    # Sensory properties (temperature, sound, smell) — enriches image prompt
    sensory = entity.get("sensory")
    if sensory and isinstance(sensory, dict):
        sensory_parts = [f"{k}: {v}" for k, v in sensory.items() if v]
        if sensory_parts:
            desc += f". Sensory qualities: {', '.join(sensory_parts)}"

    return desc


# ---------------------------------------------------------------------------
# Magenta chroma-key removal — multi-layer safeguards
# ---------------------------------------------------------------------------

# Pure magenta reference in float for distance calculations
_MAGENTA_F = (255.0, 0.0, 255.0)

# Thresholds (tuned for Nano Banana 2 output)
_COLOR_DIST_HARD = 80.0      # Euclidean RGB distance — definite magenta
_FLOOD_DIST = 110.0          # Flood fill propagation — catches off-magenta BG
_COLOR_DIST_SOFT = 140.0     # Border fringe cleanup — anti-aliased edges only
_GREEN_CHANNEL_MAX = 80      # Magenta has G≈0; genuine pink entities have G>80
_EDGE_ERODE_PX = 1           # Pixels to erode from foreground border
_DESPILL_STRENGTH = 0.7      # How aggressively to remove magenta tint (0–1)
_MIN_FOREGROUND_PCT = 2.0    # Warn if less than this % of pixels are foreground
_MAX_FOREGROUND_PCT = 98.0   # Warn if too few pixels removed (bad generation?)


def _color_dist_magenta(r: float, g: float, b: float) -> float:
    """Euclidean distance from pure magenta (255, 0, 255) in RGB space."""
    return ((r - 255.0) ** 2 + g ** 2 + (b - 255.0) ** 2) ** 0.5


def _remove_magenta(image_bytes: bytes) -> Image.Image:
    """Remove magenta (#FF00FF) chroma-key background with multiple safeguards.

    Layers:
      1. **Corner sampling**: Detect actual background color from image corners
         (in case model used a slightly off-magenta).
      2. **Color distance**: Mark pixels within _COLOR_DIST_HARD of the detected
         background color as definite background.
      3. **Green channel gate**: Any pixel with G < _GREEN_CHANNEL_MAX and
         within _COLOR_DIST_SOFT is also background (catches anti-aliased edges).
      4. **Flood fill from edges**: Starting from all 4 image edges, flood-fill
         through near-magenta pixels. This catches connected background regions
         that might have slight color variation, without accidentally removing
         interior magenta-ish pixels (e.g., pink elements).
      5. **Border erosion**: Erode _EDGE_ERODE_PX pixels from the foreground
         boundary to remove halo/fringe pixels at entity edges.
      6. **Magenta despill**: For remaining edge pixels that have magenta
         contamination, reduce the magenta tint while preserving luminance.
      7. **Diagnostics**: Log % removed; warn if foreground is suspiciously
         small or large.

    Returns:
        PIL Image in RGBA mode with background removed.
    """
    import numpy as np
    from scipy import ndimage

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img, dtype=np.float32)  # (H, W, 3) float for precision
    H, W = arr.shape[:2]
    total = H * W

    # ── Layer 1: Corner sampling ──────────────────────────────────────────
    # Sample 8×8 blocks from each corner to find the actual background color.
    corner_size = min(8, H // 4, W // 4)
    corners = [
        arr[:corner_size, :corner_size],                  # top-left
        arr[:corner_size, W - corner_size:],              # top-right
        arr[H - corner_size:, :corner_size],              # bottom-left
        arr[H - corner_size:, W - corner_size:],          # bottom-right
    ]
    corner_pixels = np.concatenate([c.reshape(-1, 3) for c in corners], axis=0)
    bg_color = np.median(corner_pixels, axis=0)  # Robust to outliers
    logger.info("[magenta] Corner-sampled background color: (%.0f, %.0f, %.0f)",
                bg_color[0], bg_color[1], bg_color[2])

    # If corner color is far from magenta, warn but still proceed with both
    corner_dist = _color_dist_magenta(bg_color[0], bg_color[1], bg_color[2])
    if corner_dist > _COLOR_DIST_SOFT:
        logger.warning("[magenta] Corner color is %.0f from magenta — "
                       "image may not have magenta background!", corner_dist)

    # ── Layer 2: Color distance from both pure magenta and detected bg ────
    # Distance from pure magenta (255, 0, 255)
    diff_magenta = arr - np.array(_MAGENTA_F, dtype=np.float32)
    dist_magenta = np.sqrt(np.sum(diff_magenta ** 2, axis=2))

    # Distance from corner-sampled background
    diff_corner = arr - bg_color.reshape(1, 1, 3)
    dist_corner = np.sqrt(np.sum(diff_corner ** 2, axis=2))

    # Minimum distance to either reference color
    dist_min = np.minimum(dist_magenta, dist_corner)

    # A pixel is "hard background" if close to either reference
    hard_bg = dist_min < _COLOR_DIST_HARD

    # ── Layer 3: Green channel gate (combined with hard threshold) ────────
    # Magenta has G≈0. Reinforce hard_bg with low-green pixels that are
    # within moderate distance. This catches slight deviations from pure magenta.
    green = arr[:, :, 1]
    hard_bg = hard_bg | ((green < _GREEN_CHANNEL_MAX) & (dist_min < _FLOOD_DIST))

    # ── Layer 4: Flood fill from edges ────────────────────────────────────
    # Only remove connected background reachable from image borders.
    # Uses _FLOOD_DIST (stricter than _COLOR_DIST_SOFT) to avoid eating
    # into interior pinkish entities that are NOT background.
    flood_candidate = dist_min < _FLOOD_DIST

    # Seed from all 4 edges
    edge_seed = np.zeros((H, W), dtype=bool)
    edge_seed[0, :] = True
    edge_seed[H - 1, :] = True
    edge_seed[:, 0] = True
    edge_seed[:, W - 1] = True

    # Flood fill: iteratively expand from edges through flood candidates
    flood_mask = edge_seed & flood_candidate
    struct = ndimage.generate_binary_structure(2, 2)  # 8-connected
    while True:
        expanded = ndimage.binary_dilation(flood_mask, structure=struct)
        expanded &= flood_candidate
        if np.array_equal(expanded, flood_mask):
            break
        flood_mask = expanded

    # Combine: pixel is background if (hard_bg) OR (flood-filled from edge)
    bg_mask = hard_bg | flood_mask

    # ── Layer 4b: Soft border fringe cleanup ──────────────────────────────
    # For pixels immediately adjacent to confirmed background that are within
    # _COLOR_DIST_SOFT, mark as background too (anti-aliased fringe pixels).
    bg_border = ndimage.binary_dilation(bg_mask, structure=struct) & ~bg_mask
    fringe = bg_border & (dist_min < _COLOR_DIST_SOFT) & (green < _GREEN_CHANNEL_MAX)
    fringe_count = int(np.sum(fringe))
    if fringe_count > 0:
        bg_mask = bg_mask | fringe
        logger.info("[magenta] Soft fringe cleanup removed %d border pixels",
                    fringe_count)

    # ── Layer 5: Border erosion ───────────────────────────────────────────
    # Erode foreground by _EDGE_ERODE_PX to remove halo/fringe pixels.
    fg_mask = ~bg_mask
    if _EDGE_ERODE_PX > 0:
        erode_struct = ndimage.generate_binary_structure(2, 1)  # 4-connected
        fg_eroded = ndimage.binary_erosion(
            fg_mask, structure=erode_struct, iterations=_EDGE_ERODE_PX
        )
        # Pixels that were foreground but got eroded = border halo
        halo = fg_mask & ~fg_eroded
        bg_mask = bg_mask | halo
        fg_mask = ~bg_mask
        halo_count = int(np.sum(halo))
        logger.info("[magenta] Border erosion removed %d halo pixels", halo_count)

    # ── Layer 6: Magenta despill on surviving edge pixels ─────────────────
    # Find foreground pixels adjacent to background (the edge ring)
    fg_dilated = ndimage.binary_dilation(fg_mask, structure=struct)
    edge_ring = fg_mask & ndimage.binary_dilation(bg_mask, structure=struct)

    out_arr = arr.copy()
    if np.any(edge_ring):
        # For edge pixels: reduce magenta contamination
        # Magenta = high R, low G, high B. Despill by pulling R and B
        # toward a neutral value based on luminance.
        edge_r = out_arr[edge_ring, 0]
        edge_g = out_arr[edge_ring, 1]
        edge_b = out_arr[edge_ring, 2]

        # Compute "magenta-ness": how much R and B exceed G
        magenta_excess_r = np.maximum(0, edge_r - edge_g)
        magenta_excess_b = np.maximum(0, edge_b - edge_g)
        magenta_strength = np.minimum(magenta_excess_r, magenta_excess_b) / 255.0

        # Only despill where there's actual magenta contamination
        despill_factor = magenta_strength * _DESPILL_STRENGTH
        out_arr[edge_ring, 0] -= (magenta_excess_r * despill_factor)
        out_arr[edge_ring, 2] -= (magenta_excess_b * despill_factor)

        despilled = int(np.sum(magenta_strength > 0.05))
        logger.info("[magenta] Despilled %d edge pixels", despilled)

    # ── Build RGBA output ─────────────────────────────────────────────────
    out_arr = np.clip(out_arr, 0, 255).astype(np.uint8)
    alpha = np.where(bg_mask, 0, 255).astype(np.uint8)
    rgba_arr = np.zeros((H, W, 4), dtype=np.uint8)
    rgba_arr[:, :, :3] = out_arr
    rgba_arr[:, :, 3] = alpha

    # ── Layer 7: Diagnostics ──────────────────────────────────────────────
    fg_count = int(np.sum(~bg_mask))
    bg_count = total - fg_count
    fg_pct = fg_count / total * 100
    bg_pct = bg_count / total * 100
    logger.info("[magenta] Result: %d fg (%.1f%%) / %d bg (%.1f%%) of %d total",
                fg_count, fg_pct, bg_count, bg_pct, total)

    if fg_pct < _MIN_FOREGROUND_PCT:
        logger.warning("[magenta] VERY LOW foreground (%.1f%%) — entity may be "
                       "almost entirely transparent! Check generation quality.",
                       fg_pct)
    if fg_pct > _MAX_FOREGROUND_PCT:
        logger.warning("[magenta] VERY HIGH foreground (%.1f%%) — magenta removal "
                       "may have failed. Background might still be visible.",
                       fg_pct)

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
            t0 = time.time()
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
            elapsed = time.time() - t0

            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        logger.info("[bg] Attempt %d/%d: got %d bytes in %.1fs",
                                    attempt, IMAGE_MAX_RETRIES,
                                    len(part.inline_data.data), elapsed)
                        return part.inline_data.data

            logger.warning("[bg] Attempt %d/%d: no image data (%.1fs)",
                           attempt, IMAGE_MAX_RETRIES, elapsed)

        except Exception as exc:
            logger.warning("[bg] Attempt %d/%d failed in %.1fs (%s): %s",
                           attempt, IMAGE_MAX_RETRIES,
                           time.time() - t0, type(exc).__name__, exc or "no details")

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
            t0 = time.time()
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
            elapsed = time.time() - t0

            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        logger.info("[entity] %s: attempt %d/%d got %d bytes in %.1fs",
                                    eid, attempt, IMAGE_MAX_RETRIES,
                                    len(part.inline_data.data), elapsed)
                        return part.inline_data.data

            logger.warning("[entity] %s: attempt %d/%d no image data (%.1fs)",
                           eid, attempt, IMAGE_MAX_RETRIES, elapsed)

        except Exception as exc:
            logger.warning("[entity] %s: attempt %d/%d failed in %.1fs (%s): %s",
                           eid, attempt, IMAGE_MAX_RETRIES,
                           time.time() - t0, type(exc).__name__, exc or "no details")

    logger.warning("[entity] %s: all %d attempts exhausted", eid, IMAGE_MAX_RETRIES)
    return None
