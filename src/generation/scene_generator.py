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
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from google import genai

from src.models.story_state import StoryState
from src.generation.image_processing import (
    ART_W,
    ART_H,
    _downscale_background,
    _downscale_entity,
    _generate_background,
    _generate_entity,
    _remove_magenta,
)

logger = logging.getLogger(__name__)


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
# Position computation
# ---------------------------------------------------------------------------

def _compute_entity_positions(
    manifest_data: Dict[str, Any],
    entity_sprites: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, int]]:
    """Compute top-left positions and sizes in art-grid coordinates.

    Manifest positions are NORMALIZED (0.0-1.0).
    Converts to art-grid coordinates (0-ART_W-1, 0-ART_H-1).

    Returns:
        Dict mapping entity_id -> {"x": int, "y": int, "w": int, "h": int}
        in art-grid coordinates.
    """
    positions = {}
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        eid = ent["id"]
        pos = ent.get("position", {})

        # Sprite dimensions (already in art-grid coords from downscale)
        if entity_sprites and eid in entity_sprites:
            w = entity_sprites[eid]["w"]
            h = entity_sprites[eid]["h"]
        else:
            w = max(1, int(ent.get("width_hint", 0.05) * ART_W))
            h = max(1, int(ent.get("height_hint", 0.08) * ART_H))

        # Convert normalized center to art-grid center
        art_cx = int(pos.get("x", 0.0) * ART_W)
        art_cy = int(pos.get("y", 0.0) * ART_H)

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
    canonical_ground_art_y = int(0.7 * ART_H)  # ~126
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
# Entity / background deconfliction (code-level enforcement)
# ---------------------------------------------------------------------------


# Material/modifier words to SKIP when matching entity types against background.
# These describe properties, not structural objects.
_MODIFIER_WORDS = frozenset({
    # Materials
    "wooden", "metal", "stone", "brick", "glass", "plastic",
    "iron", "steel", "concrete", "marble", "ceramic",
    # Sizes
    "small", "medium", "large", "big", "tiny", "tall", "short",
    # Colors
    "red", "blue", "green", "yellow", "white", "black", "brown",
    "grey", "gray", "orange", "pink", "purple", "golden",
    # Age/state
    "old", "new", "broken", "rusty", "dusty", "shiny",
})


def _fuzzy_word_match(entity_word: str, text_word: str) -> bool:
    """Check if two words are likely the same concept (singular/plural/variant).

    Uses common-prefix length ratio. The shared prefix must be:
    - At least 75% of the shorter word
    - At least 60% of the longer word
    - At least 4 characters

    This catches: bookshelf↔bookshelves, lamp↔lamps, fence↔fences, pool↔pools
    but rejects: book↔bookshelves, pot↔pottery, tide↔tidepool
    """
    if entity_word == text_word:
        return True
    common = 0
    for a, b in zip(entity_word, text_word):
        if a == b:
            common += 1
        else:
            break
    if common < 4:
        return False
    min_len = min(len(entity_word), len(text_word))
    max_len = max(len(entity_word), len(text_word))
    return common >= min_len * 0.75 and common >= max_len * 0.6


def _word_matches_text(word: str, text: str) -> bool:
    """Check if a word fuzzy-matches any word in a text string."""
    for tw in text.split():
        # Strip punctuation from text words
        tw_clean = re.sub(r'[^a-z]', '', tw)
        if len(tw_clean) < 3:
            continue
        if _fuzzy_word_match(word, tw_clean):
            return True
    return False


def _filter_overlapping_entities(
    manifest_data: Dict[str, Any],
) -> List[str]:
    """Return entity IDs to SKIP because they duplicate background elements.

    Data-driven checks only — no hardcoded keyword list. Uses the LLM's own
    structural_elements and background_description to detect overlap via
    fuzzy word matching (handles singular/plural).

    Entities with an 'emotion' field are assumed to be characters and are
    NEVER filtered.
    """
    skip_ids: List[str] = []

    bg_data = manifest_data.get("manifest", {}).get("background", {})
    structural = bg_data.get("structural_elements", []) if isinstance(bg_data, dict) else []
    # Support both old format (list of strings) and new format (list of dicts with "name")
    structural_names = []
    for s in structural:
        if isinstance(s, dict):
            structural_names.append(s.get("name", "").lower())
        elif isinstance(s, str):
            structural_names.append(s.lower())
    structural_text = " ".join(structural_names)

    bg_desc = manifest_data.get("background_description", "").lower()

    for ent in manifest_data.get("manifest", {}).get("entities", []):
        eid = ent.get("id", "")
        etype = ent.get("type", "").lower().strip()

        # Characters (have emotion) are NEVER filtered
        if ent.get("emotion"):
            continue

        # Check each word in the entity type against structural_elements
        # Skip modifier words — they describe materials/properties, not objects
        type_words = [
            tw for tw in etype.replace("_", " ").split()
            if tw not in _MODIFIER_WORDS
        ]
        matched = False
        for tw in type_words:
            if len(tw) < 3:
                continue
            if _word_matches_text(tw, structural_text):
                skip_ids.append(eid)
                logger.warning(
                    "[deconflict] Removing %s (type='%s'): word '%s' "
                    "matches structural_elements",
                    eid, etype, tw,
                )
                matched = True
                break

        if not matched:
            # Check entity type against background_description
            for tw in type_words:
                if len(tw) < 4:
                    continue
                if _word_matches_text(tw, bg_desc):
                    skip_ids.append(eid)
                    logger.warning(
                        "[deconflict] Removing %s (type='%s'): word '%s' "
                        "found in background_description",
                        eid, etype, tw,
                    )
                    break

    return skip_ids


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

    # --- Filter entities that duplicate background structural elements ---
    skip_ids = _filter_overlapping_entities(manifest_data)
    if skip_ids:
        logger.info("[assets] Filtered %d overlapping entities: %s",
                     len(skip_ids), skip_ids)

    # --- Collect entities to generate (skip carried_over + overlapping) ---
    entities_to_generate = []
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        if ent.get("id") in carried_over or ent.get("carried_over"):
            continue
        if ent.get("id") in skip_ids:
            continue  # Would duplicate background
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

    t_img = time.time()
    if all_tasks:
        results = await asyncio.gather(*all_tasks, return_exceptions=True)
    else:
        results = []
    logger.info("[assets] All image generation took %.1fs (%d tasks)",
                time.time() - t_img, len(all_tasks))

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

    logger.info("[assets] Generated %d/%d entity images (first pass)",
                len(entity_images), len(entities_to_generate))

    # --- Retry pass: re-attempt failed entities sequentially ---
    failed_entities = [
        ent for ent in entities_to_generate
        if ent["id"] not in entity_images
    ]
    if failed_entities:
        logger.warning(
            "[assets] %d entities failed first pass, retrying: %s",
            len(failed_entities),
            [e["id"] for e in failed_entities],
        )
        for ent in failed_entities:
            eid = ent["id"]
            result = await _generate_entity(client, ent)
            if isinstance(result, bytes):
                entity_images[eid] = result
                logger.info("[assets] %s: retry succeeded (%d bytes)", eid, len(result))
            else:
                logger.error(
                    "[assets] %s: FAILED after retry — entity will be MISSING from scene",
                    eid,
                )
        logger.info("[assets] After retry: %d/%d entity images",
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
        # Target dimensions in art-grid coordinates (normalized → pixels)
        art_w = max(20, int(ent.get("width_hint", 0.05) * ART_W))
        art_h = max(20, int(ent.get("height_hint", 0.08) * ART_H))

        t_proc = time.time()
        rgba = _remove_magenta(entity_images[eid])
        entity_sprites[eid] = _downscale_entity(rgba, art_w, art_h)
        logger.info("[assets] %s: magenta + downscale -> %dx%d in %.1fs",
                    eid, art_w, art_h, time.time() - t_proc)

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
