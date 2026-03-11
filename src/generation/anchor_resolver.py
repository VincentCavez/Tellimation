"""Resolve entity positions against detected structural element bounding boxes.

After background generation, the actual positions of structural elements
(counters, shelves, fences, etc.) may differ from the manifest's intended
positions.  This module adjusts entity (x, y) coordinates so they align
with where those elements actually ended up in the background image.

Usage:
    from src.generation.anchor_detection import detect_anchors
    from src.generation.anchor_resolver import resolve_positions

    anchors = await detect_anchors(api_key, bg_image, manifest_data)
    updated  = resolve_positions(manifest_data, anchors)
"""

from __future__ import annotations

import copy
import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Preposition patterns — ordered longest-first so greedy match works
# ---------------------------------------------------------------------------

_PREPOSITION_PATTERNS: List[Tuple[str, str]] = [
    ("hanging from", "hanging_from"),
    ("in front of",  "in_front_of"),
    ("on top of",    "on"),
    ("next to",      "beside"),
    ("attached to",  "on"),
    ("resting on",   "on"),
    ("leaning against", "beside"),
    ("on",           "on"),
    ("under",        "under"),
    ("beneath",      "under"),
    ("below",        "under"),
    ("above",        "above"),
    ("beside",       "beside"),
    ("behind",       "behind"),
    ("near",         "beside"),
    ("between",      "between"),
    ("inside",       "inside"),
    ("within",       "inside"),
    ("facing",       "facing"),
]

# Compiled regex: match any preposition at the start of the spatial_ref
_PREP_RE = re.compile(
    r"^(" + "|".join(re.escape(p) for p, _ in _PREPOSITION_PATTERNS) + r")\s+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_spatial_ref(spatial_ref: str) -> Optional[Tuple[str, str]]:
    """Parse a spatial_ref string into (normalized_relation, target_name).

    Examples:
        "on wooden counter"      -> ("on", "wooden counter")
        "beside rabbit_01"       -> ("beside", "rabbit_01")
        "hanging from wall hook" -> ("hanging_from", "wall hook")
        "on top of shelf"        -> ("on", "shelf")

    Returns None if the string can't be parsed.
    """
    if not spatial_ref:
        return None

    text = spatial_ref.strip()
    m = _PREP_RE.match(text)
    if not m:
        # No recognized preposition — treat the whole thing as a target
        # with implicit "on" relation (most common case)
        return ("on", text)

    raw_prep = m.group(1).lower()
    target = text[m.end():].strip()
    if not target:
        return None

    # Normalize the preposition
    for pattern, normalized in _PREPOSITION_PATTERNS:
        if raw_prep == pattern:
            return (normalized, target)

    return ("on", target)


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

def _find_matching_anchor(
    target_name: str,
    anchor_names: List[str],
    cutoff: float = 0.45,
) -> Optional[str]:
    """Find the best matching anchor name for a spatial_ref target.

    Uses difflib.get_close_matches with a low cutoff since targets may be
    abbreviated or use different phrasing.  Also tries substring matching
    as a fallback (e.g., "counter" matches "wooden counter").
    """
    if not anchor_names:
        return None

    target_lower = target_name.lower().strip()
    anchors_lower = {a.lower().strip(): a for a in anchor_names}

    # Exact match
    if target_lower in anchors_lower:
        return anchors_lower[target_lower]

    # difflib fuzzy match
    matches = difflib.get_close_matches(
        target_lower, list(anchors_lower.keys()), n=1, cutoff=cutoff
    )
    if matches:
        return anchors_lower[matches[0]]

    # Substring fallback: target is a substring of an anchor name or vice-versa
    for anchor_lower, anchor_orig in anchors_lower.items():
        # "counter" in "wooden counter" OR "wooden counter" in "counter"
        if target_lower in anchor_lower or anchor_lower in target_lower:
            return anchor_orig

    # Word overlap: check if any significant word (len>=4) matches
    target_words = {w for w in target_lower.split() if len(w) >= 4}
    for anchor_lower, anchor_orig in anchors_lower.items():
        anchor_words = {w for w in anchor_lower.split() if len(w) >= 4}
        if target_words & anchor_words:
            return anchor_orig

    return None


# ---------------------------------------------------------------------------
# Position resolution
# ---------------------------------------------------------------------------

def _is_wide_anchor(anchor: Dict[str, Any], threshold: float = 0.75) -> bool:
    """Check if an anchor spans most of the canvas width (floor, ceiling, etc.)."""
    bbox = anchor["bbox"]  # (xmin, ymin, xmax, ymax)
    width = bbox[2] - bbox[0]
    return width >= threshold


def _resolve_on(
    anchor: Dict[str, Any],
    entity: Dict[str, Any],
) -> Tuple[float, float]:
    """Place entity ON TOP of a surface (e.g., "on counter").

    Keep entity's original x (horizontal position is usually correct from
    the manifest).  Snap entity y so its bottom edge sits on the anchor's
    top edge.

    For wide anchors (floors) the y is also kept — the entity is already
    "on" the floor by definition.
    """
    pos = entity.get("position", {})
    orig_x = pos.get("x", 0.5)
    orig_y = pos.get("y", 0.5)

    # Wide anchors (floor, ground) — no repositioning needed
    if _is_wide_anchor(anchor):
        return (orig_x, orig_y)

    top_cx, top_cy = anchor["top_center"]
    h_hint = entity.get("height_hint", 0.3)
    new_y = top_cy - h_hint / 2

    # Keep original x but constrain it within the anchor's horizontal extent
    bbox = anchor["bbox"]
    clamped_x = max(bbox[0], min(bbox[2], orig_x))

    return (clamped_x, max(0.0, new_y))


def _resolve_under(
    anchor: Dict[str, Any],
    entity: Dict[str, Any],
) -> Tuple[float, float]:
    """Place entity UNDER a surface (e.g., "under table").

    Keep entity's original x.  Snap y so entity sits below anchor bottom edge.
    """
    pos = entity.get("position", {})
    orig_x = pos.get("x", 0.5)

    if _is_wide_anchor(anchor):
        return (orig_x, pos.get("y", 0.5))

    bot_cx, bot_cy = anchor["bottom_center"]
    h_hint = entity.get("height_hint", 0.3)
    new_y = bot_cy + h_hint / 2

    bbox = anchor["bbox"]
    clamped_x = max(bbox[0], min(bbox[2], orig_x))

    return (clamped_x, min(1.0, new_y))


def _resolve_beside(
    anchor: Dict[str, Any],
    entity: Dict[str, Any],
) -> Tuple[float, float]:
    """Place entity BESIDE a structural element.

    Keep entity's original y (vertical alignment is usually correct).
    Shift entity x to the right edge of the anchor bbox + half entity width.
    If entity was originally to the left of anchor center, place on the left side.
    """
    bbox = anchor["bbox"]  # (xmin, ymin, xmax, ymax)
    anchor_cx = anchor["center"][0]
    w_hint = entity.get("width_hint", 0.2)

    pos = entity.get("position", {})
    orig_x = pos.get("x", 0.5)
    orig_y = pos.get("y", 0.5)

    if orig_x < anchor_cx:
        # Place to the left of the anchor
        new_x = bbox[0] - w_hint / 2 - 0.02
    else:
        # Place to the right of the anchor
        new_x = bbox[2] + w_hint / 2 + 0.02

    return (max(0.0, min(1.0, new_x)), orig_y)


def _resolve_hanging_from(
    anchor: Dict[str, Any],
    entity: Dict[str, Any],
) -> Tuple[float, float]:
    """Place entity HANGING FROM an anchor (e.g., "hanging from hook").

    Keep entity's original x.  Snap y below anchor center.
    """
    pos = entity.get("position", {})
    orig_x = pos.get("x", 0.5)

    cx, cy = anchor["center"]
    h_hint = entity.get("height_hint", 0.3)
    new_y = cy + h_hint / 2

    bbox = anchor["bbox"]
    clamped_x = max(bbox[0], min(bbox[2], orig_x))

    return (clamped_x, min(1.0, new_y))


def _resolve_above(
    anchor: Dict[str, Any],
    entity: Dict[str, Any],
) -> Tuple[float, float]:
    """Place entity ABOVE an anchor.

    Keep entity's original x.  Snap y above the anchor top edge.
    """
    pos = entity.get("position", {})
    orig_x = pos.get("x", 0.5)

    if _is_wide_anchor(anchor):
        return (orig_x, pos.get("y", 0.5))

    top_cx, top_cy = anchor["top_center"]
    h_hint = entity.get("height_hint", 0.3)
    new_y = top_cy - h_hint / 2 - 0.03

    bbox = anchor["bbox"]
    clamped_x = max(bbox[0], min(bbox[2], orig_x))

    return (clamped_x, max(0.0, new_y))


def _resolve_inside(
    anchor: Dict[str, Any],
    entity: Dict[str, Any],
) -> Tuple[float, float]:
    """Place entity INSIDE an anchor (e.g., "inside basket").

    Entity center = anchor center.
    """
    return anchor["center"]


def _resolve_behind(
    anchor: Dict[str, Any],
    entity: Dict[str, Any],
) -> Tuple[float, float]:
    """Place entity BEHIND an anchor.

    Keep entity's original x.  Slightly higher y (further back = higher in scene).
    """
    pos = entity.get("position", {})
    orig_x = pos.get("x", 0.5)

    cx, cy = anchor["center"]
    new_y = cy - 0.05

    bbox = anchor["bbox"]
    clamped_x = max(bbox[0], min(bbox[2], orig_x))

    return (clamped_x, max(0.0, new_y))


_RELATION_RESOLVERS = {
    "on":           _resolve_on,
    "under":        _resolve_under,
    "beside":       _resolve_beside,
    "hanging_from": _resolve_hanging_from,
    "above":        _resolve_above,
    "inside":       _resolve_inside,
    "behind":       _resolve_behind,
    "in_front_of":  _resolve_beside,  # similar lateral placement
}


# ---------------------------------------------------------------------------
# Entity-to-entity resolution (for "beside rabbit_01" etc.)
# ---------------------------------------------------------------------------

def _is_entity_ref(target: str, entity_ids: List[str]) -> bool:
    """Check if a spatial_ref target refers to another entity (by ID)."""
    return target in entity_ids


def _resolve_entity_beside(
    target_entity: Dict[str, Any],
    source_entity: Dict[str, Any],
) -> Tuple[float, float]:
    """Place source entity beside a target entity.

    Keep vertical alignment, shift horizontally to be adjacent.
    """
    target_pos = target_entity.get("position", {})
    target_x = target_pos.get("x", 0.5)
    target_y = target_pos.get("y", 0.5)
    target_w = target_entity.get("width_hint", 0.2)

    source_pos = source_entity.get("position", {})
    source_x = source_pos.get("x", 0.5)
    source_w = source_entity.get("width_hint", 0.2)

    if source_x < target_x:
        new_x = target_x - (target_w + source_w) / 2 - 0.02
    else:
        new_x = target_x + (target_w + source_w) / 2 + 0.02

    return (max(0.0, min(1.0, new_x)), target_y)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_positions(
    manifest_data: Dict[str, Any],
    anchors: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Resolve entity positions against detected structural element bboxes.

    For each entity with a non-null spatial_ref:
    1. Parse the spatial_ref to extract (relation, target).
    2. If the target is a structural element found in `anchors`, compute
       a new (x, y) based on the relation and the detected bbox.
    3. If the target is another entity, resolve against that entity's position.
    4. If the target wasn't detected, keep the original (x, y).

    Args:
        manifest_data: Scene manifest dict (will NOT be mutated).
        anchors: Output of detect_anchors() — maps element name to bbox data.

    Returns:
        A deep copy of manifest_data with updated entity positions.
    """
    result = copy.deepcopy(manifest_data)
    entities = result.get("manifest", {}).get("entities", [])

    if not entities:
        return result

    # Build entity ID -> entity dict lookup
    entity_by_id: Dict[str, Dict[str, Any]] = {
        e["id"]: e for e in entities if "id" in e
    }
    entity_ids = list(entity_by_id.keys())
    anchor_names = list(anchors.keys())

    resolved_count = 0
    fallback_count = 0

    for entity in entities:
        eid = entity.get("id", "?")
        pos = entity.get("position", {})
        spatial_ref = pos.get("spatial_ref")

        if not spatial_ref:
            continue

        parsed = _parse_spatial_ref(spatial_ref)
        if not parsed:
            logger.warning("[resolver] %s: could not parse spatial_ref '%s'",
                           eid, spatial_ref)
            continue

        relation, target = parsed

        # Case 1: target is another entity
        if _is_entity_ref(target, entity_ids):
            if relation in ("beside", "next_to", "near", "facing"):
                target_ent = entity_by_id[target]
                new_x, new_y = _resolve_entity_beside(target_ent, entity)
                pos["x"] = round(new_x, 3)
                pos["y"] = round(new_y, 3)
                resolved_count += 1
                logger.info("[resolver] %s: resolved '%s' -> entity %s "
                            "(%.3f, %.3f)",
                            eid, spatial_ref, target, new_x, new_y)
            else:
                # For "on entity_id" etc., the LLM already positions correctly
                # relative to other entities in the manifest
                logger.debug("[resolver] %s: spatial_ref '%s' targets entity "
                             "%s — keeping manifest position",
                             eid, spatial_ref, target)
            continue

        # Case 2: target is a structural element — fuzzy match to anchors
        matched_anchor_name = _find_matching_anchor(target, anchor_names)
        if matched_anchor_name is None:
            fallback_count += 1
            logger.warning(
                "[resolver] %s: spatial_ref '%s' — target '%s' not found "
                "in detected anchors %s — keeping original position",
                eid, spatial_ref, target, anchor_names,
            )
            continue

        anchor = anchors[matched_anchor_name]
        resolver_fn = _RELATION_RESOLVERS.get(relation)

        if resolver_fn is None:
            logger.warning(
                "[resolver] %s: no resolver for relation '%s' — "
                "keeping original position",
                eid, relation,
            )
            fallback_count += 1
            continue

        new_x, new_y = resolver_fn(anchor, entity)
        old_x, old_y = pos.get("x", 0.5), pos.get("y", 0.5)
        pos["x"] = round(new_x, 3)
        pos["y"] = round(new_y, 3)
        resolved_count += 1

        logger.info(
            "[resolver] %s: '%s' matched '%s' — %s (%.3f,%.3f) -> (%.3f,%.3f)",
            eid, spatial_ref, matched_anchor_name, relation,
            old_x, old_y, new_x, new_y,
        )

    logger.info(
        "[resolver] Done: %d resolved, %d fallback (kept original)",
        resolved_count, fallback_count,
    )
    return result
