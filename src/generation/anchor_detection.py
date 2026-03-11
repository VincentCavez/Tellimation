"""Detect structural element positions in generated background images.

Uses Gemini 3 Flash with structured output to locate background elements
(counters, windows, fences, etc.) in the generated background image.
Returns bounding boxes and anchor points in normalized 0-1 coordinates.

This module bridges the gap between the scene manifest (which specifies
WHERE structural elements should be) and the background image generator
(which may place them differently). By detecting actual positions post-
generation, we can adjust entity placement to match the real background.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any, Dict, List, Optional

from PIL import Image

from google import genai
from google.genai import types

from src.generation.utils import get_response_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_ID = "gemini-3-flash-preview"
DETECTION_TIMEOUT = 60  # seconds

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_DETECTION_SYSTEM_PROMPT = """\
You are a precise visual element detector for children's illustration backgrounds.

You receive a background illustration and a list of structural elements to locate.
For each element, return its bounding box as [ymin, xmin, ymax, xmax] on a \
0-1000 integer grid (0 = top/left edge, 1000 = bottom/right edge).

Rules:
- Only return elements you can actually see in the image.
- If an element is not visible, omit it from the results.
- Bounding boxes should tightly enclose the element.
- For elements that span the full width (e.g., "tiled floor"), use the full \
  horizontal extent (xmin near 0, xmax near 1000).
- For elements partially cut off at edges, extend the box to the image edge.
"""

_DETECTION_USER_PROMPT = """\
Locate these structural elements in the background image:

{element_list}

Return a JSON array. Each entry has "name" (the element name exactly as listed) \
and "box_2d" as [ymin, xmin, ymax, xmax] on a 0-1000 grid.
Only include elements you can clearly identify in the image.
"""


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------

_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.ARRAY,
    items=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "name": types.Schema(type=types.Type.STRING),
            "box_2d": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.INTEGER),
            ),
        },
        required=["name", "box_2d"],
    ),
)


# ---------------------------------------------------------------------------
# Core detection function
# ---------------------------------------------------------------------------

async def detect_anchors(
    api_key: str,
    background_image: Image.Image,
    manifest_data: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Detect structural element positions in a background image.

    Args:
        api_key: Gemini API key.
        background_image: PIL Image of the generated background (any size).
        manifest_data: Scene manifest dict containing
            manifest.background.structural_elements.

    Returns:
        Dict mapping element name -> {
            "bbox": (xmin, ymin, xmax, ymax),  # normalized 0-1
            "center": (cx, cy),                 # normalized 0-1
            "top_center": (cx, ymin),            # normalized 0-1
            "bottom_center": (cx, ymax),         # normalized 0-1
        }
        Only includes elements that were successfully detected.
    """
    # Extract structural element names from manifest
    element_names = _extract_element_names(manifest_data)
    if not element_names:
        logger.info("[anchor] No structural elements to detect")
        return {}

    # Encode image as PNG bytes for the API
    image_bytes = _image_to_png_bytes(background_image)

    # Call Gemini
    detections = await _call_gemini(api_key, image_bytes, element_names)
    if detections is None:
        logger.warning("[anchor] Detection call failed, returning empty")
        return {}

    # Parse results into normalized coordinates
    return _parse_detections(detections, element_names)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_element_names(manifest_data: Dict[str, Any]) -> List[str]:
    """Extract structural element names from the manifest."""
    bg = manifest_data.get("manifest", {}).get("background", {})
    if not isinstance(bg, dict):
        return []

    elements = bg.get("structural_elements", [])
    names = []
    for el in elements:
        if isinstance(el, dict):
            name = el.get("name", "").strip()
            if name:
                names.append(name)
        elif isinstance(el, str) and el.strip():
            names.append(el.strip())
    return names


def _image_to_png_bytes(img: Image.Image) -> bytes:
    """Convert a PIL Image to PNG bytes."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


async def _call_gemini(
    api_key: str,
    image_bytes: bytes,
    element_names: List[str],
) -> Optional[List[Dict[str, Any]]]:
    """Call Gemini 3 Flash to detect element bounding boxes.

    Returns parsed JSON array, or None on failure.
    """
    client = genai.Client(api_key=api_key)

    element_list = "\n".join(f"- {name}" for name in element_names)
    user_text = _DETECTION_USER_PROMPT.format(element_list=element_list)

    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
    text_part = types.Part.from_text(text=user_text)

    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=MODEL_ID,
                contents=[image_part, text_part],
                config=types.GenerateContentConfig(
                    system_instruction=_DETECTION_SYSTEM_PROMPT,
                    temperature=0.2,
                    response_mime_type="application/json",
                    response_schema=_RESPONSE_SCHEMA,
                ),
            ),
            timeout=DETECTION_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("[anchor] Gemini call failed (%s): %s",
                       type(exc).__name__, exc)
        return None

    raw_text = get_response_text(response)
    try:
        result = json.loads(raw_text)
        if isinstance(result, list):
            logger.info("[anchor] Detected %d elements", len(result))
            return result
        logger.warning("[anchor] Unexpected response type: %s", type(result))
        return None
    except json.JSONDecodeError as exc:
        logger.warning("[anchor] Failed to parse response JSON: %s", exc)
        return None


def _parse_detections(
    detections: List[Dict[str, Any]],
    requested_names: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Parse raw Gemini detections into normalized anchor points.

    Args:
        detections: List of {"name": str, "box_2d": [ymin, xmin, ymax, xmax]}
            on a 0-1000 grid.
        requested_names: Original list of requested element names.

    Returns:
        Dict mapping name -> anchor data in normalized 0-1 coords.
    """
    result: Dict[str, Dict[str, Any]] = {}
    detected_names: set[str] = set()

    for det in detections:
        name = det.get("name", "")
        box = det.get("box_2d", [])

        if not name or not isinstance(box, list) or len(box) != 4:
            logger.warning("[anchor] Skipping malformed detection: %s", det)
            continue

        try:
            ymin, xmin, ymax, xmax = (int(v) for v in box)
        except (ValueError, TypeError):
            logger.warning("[anchor] Non-integer box values for '%s': %s",
                           name, box)
            continue

        # Validate bounds
        if not (0 <= ymin <= 1000 and 0 <= xmin <= 1000
                and 0 <= ymax <= 1000 and 0 <= xmax <= 1000):
            logger.warning("[anchor] Out-of-range box for '%s': %s", name, box)
            continue

        if ymin >= ymax or xmin >= xmax:
            logger.warning("[anchor] Degenerate box for '%s': %s", name, box)
            continue

        # Convert from 0-1000 grid to 0-1 normalized
        nx_min = xmin / 1000.0
        ny_min = ymin / 1000.0
        nx_max = xmax / 1000.0
        ny_max = ymax / 1000.0
        cx = (nx_min + nx_max) / 2.0
        cy = (ny_min + ny_max) / 2.0

        result[name] = {
            "bbox": (nx_min, ny_min, nx_max, ny_max),
            "center": (cx, cy),
            "top_center": (cx, ny_min),
            "bottom_center": (cx, ny_max),
        }
        detected_names.add(name)
        logger.info("[anchor] '%s': bbox=(%.3f, %.3f, %.3f, %.3f) center=(%.3f, %.3f)",
                    name, nx_min, ny_min, nx_max, ny_max, cx, cy)

    # Warn about undetected elements
    for name in requested_names:
        if name not in detected_names:
            logger.warning("[anchor] Element NOT detected in image: '%s'", name)

    return result
