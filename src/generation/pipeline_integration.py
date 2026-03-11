"""Reanchor entity positions after background image generation.

Integrates anchor_detection (Gemini vision) and anchor_resolver (position
correction) into a single async call for the scene generation pipeline.

Insert between background HD generation and pixelization:

    scene = await generate_scene_and_neg(...)
    bg_hd = await generate_background(...)
    scene = await reanchor_scene(api_key, bg_hd, scene)   # <-- HERE
    bg_pixel = pixelize(bg_hd)
    final = composite(bg_pixel, scene)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from PIL import Image

from src.generation.anchor_detection import detect_anchors
from src.generation.anchor_resolver import resolve_positions

logger = logging.getLogger(__name__)


async def reanchor_scene(
    api_key: str,
    hd_background: Image.Image,
    scene: Dict[str, Any],
) -> Dict[str, Any]:
    """Detect structural elements in the background and adjust entity positions.

    1. Calls Gemini 3 Flash to detect bounding boxes of structural elements
       listed in the scene manifest.
    2. For each entity with a spatial_ref pointing to a structural element,
       recomputes (x, y) based on the detected bbox and the spatial relation.
    3. Returns the updated scene dict (deep copy — original is not mutated).

    If detection fails or no structural elements exist, returns the scene
    unchanged (no-op fallback).

    Args:
        api_key: Gemini API key.
        hd_background: HD background PIL Image (pre-pixelization).
        scene: Scene manifest dict (the full JSON from scene_neg_generator).

    Returns:
        Updated scene dict with corrected entity positions.
    """
    # Quick exit: no structural elements to anchor against
    bg = scene.get("manifest", {}).get("background", {})
    if not isinstance(bg, dict):
        logger.info("[reanchor] No background in manifest — skipping")
        return scene

    structural = bg.get("structural_elements", [])
    if not structural:
        logger.info("[reanchor] No structural elements — skipping")
        return scene

    # Count entities that actually have a spatial_ref (worth re-anchoring)
    entities = scene.get("manifest", {}).get("entities", [])
    refs = [
        e for e in entities
        if e.get("position", {}).get("spatial_ref")
    ]
    if not refs:
        logger.info("[reanchor] No entities with spatial_ref — skipping")
        return scene

    logger.info(
        "[reanchor] Detecting %d structural elements for %d spatial_refs...",
        len(structural), len(refs),
    )

    # Step 1: detect
    t0 = time.time()
    try:
        anchors = await detect_anchors(api_key, hd_background, scene)
    except Exception as exc:
        logger.warning(
            "[reanchor] Detection failed (%s): %s — keeping original positions",
            type(exc).__name__, exc,
        )
        return scene

    detect_ms = (time.time() - t0) * 1000
    logger.info("[reanchor] Detection: %d anchors in %.0fms", len(anchors), detect_ms)

    if not anchors:
        logger.warning("[reanchor] No anchors detected — keeping original positions")
        return scene

    # Step 2: resolve
    t1 = time.time()
    updated = resolve_positions(scene, anchors)
    resolve_ms = (time.time() - t1) * 1000
    logger.info("[reanchor] Resolution done in %.0fms", resolve_ms)

    return updated
