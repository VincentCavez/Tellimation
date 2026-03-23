"""Tellimation module — deterministic animation selection from the grammar.

Provides:
  - select_discrepancy(): pick which discrepancy to animate
  - _select_animation_for_discrepancy(): map discrepancy → animation ID
  - load_animation_params(): load default params from grammar JSON
  - generate_invocation_array(): build a full InvocationArray from discrepancies
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.models.assessment import Discrepancy
from src.models.invocation import AnimationInvocation, InvocationArray
from src.models.scene import SceneManifest
from src.models.student_profile import StudentProfile
from animations.grammar import get_animation, get_animations_by_category, get_animations_by_mode
from config.misl import (
    MISL_TO_ANIMATIONS,
    ANIMATION_ID_TO_TEMPLATE,
    ANIMATION_PARAMS,
    COUNT_ANIMATIONS,
)

logger = logging.getLogger(__name__)


# Default durations per template (matching JS registration)
_DEFAULT_DURATIONS: Dict[str, int] = {
    "spotlight": 3000, "nametag": 2000, "color_pop": 3000, "emanation": 2500,
    "motion_lines": 2000, "flip": 2000, "reveal": 2500, "stamp": 3000,
    "flashback": 3000, "timelapse": 4000, "magnetism": 2500, "repel": 2000,
    "causal_push": 2000, "sequential_glow": 3000, "disintegration": 2000,
    "ghost_outline": 2500, "speech_bubble": 1500, "thought_bubble": 1500,
    "alert": 1200, "interjection": 2000, "decomposition": 2500,
}


def _build_fallback(
    target_id: str,
    misl_element: str,
) -> Dict[str, Any]:
    """Build a Mode A fallback using the first eligible template."""
    eligible = MISL_TO_ANIMATIONS.get(misl_element, [])
    if eligible:
        aid = eligible[0]
        template = ANIMATION_ID_TO_TEMPLATE.get(aid, "spotlight")
    else:
        aid = "I1_spotlight"
        template = "spotlight"

    duration_ms = _DEFAULT_DURATIONS.get(template, 1500)

    logger.info("[tellimation] Using fallback Mode A: template=%s for %s (misl=%s)",
                template, target_id, misl_element)

    return {
        "mode": "use_default",
        "animation_id": aid,
        "template": template,
        "params": {},
        "duration_ms": duration_ms,
        "steps": [],
        "code": "",
        "text_overlays": [],
    }




# ---------------------------------------------------------------------------
# Category → MISL element mapping for discrepancy routing
# ---------------------------------------------------------------------------

_CATEGORY_TO_MISL: Dict[str, str] = {
    "Identity": "character",
    "Property": "elaborated_noun_phrases",
    "Action": "action",
    "Space": "setting",
    "Time": "tense",
    "Relation": "coordinating_conjunctions",
    "Count": "character",
    "Discourse": "internal_response",
}

# Priority order for sorting within each pass
_CATEGORY_PRIORITY: Dict[str, int] = {
    "Identity": 0,
    "Count": 1,
    "Property": 2,
    "Action": 3,
    "Space": 4,
    "Time": 5,
    "Relation": 6,
    "Discourse": 7,
}


def _select_animation_for_discrepancy(
    discrepancy: Discrepancy,
    student_profile: StudentProfile,
) -> Optional[str]:
    """Select the best animation ID for a discrepancy.

    Deterministic mapping based on category and context:
    - Identity → spotlight (I1): highlight entity to prompt naming
    - Property → color_pop (P1): emphasize visual attributes
    - Action → motion_lines (A1): draw attention to what entity is doing
    - Space (with entity targets) → stamp (S2): show where entity is
    - Space (setting, no specific entity) → reveal (S1): reveal the whole scene
    - Time → flashback (T1): temporal context
    - Relation → magnetism (R1): show connection between entities
    - Count → sequential_glow (C1): highlight entities for counting
    - Discourse → speech_bubble (D1): prompt dialogue/internal response
    """
    category = discrepancy.type
    is_correction = discrepancy.pass_type == "correction"
    has_targets = bool(discrepancy.target_entities)
    desc_lower = discrepancy.description.lower()

    # Use the animation_id provided by the LLM
    _ID_TO_TEMPLATE = {
        "I1": "I1_spotlight", "I2": "I2_nametag",
        "P1": "P1_color_pop",
        "P2A": "P2a_emanation_shame", "P2B": "P2b_emanation_cold",
        "P2C": "P2c_emanation_joy", "P2D": "P2d_emanation_love",
        "P2E": "P2e_emanation_anger", "P2F": "P2f_emanation_fear",
        "A1": "A1_motion_line", "A2": "A2_flip",
        "S1": "S1_reveal", "S2": "S2_stamp",
        "T1": "T1_flashback", "T2": "T2_timelapse",
        "R1": "R1_magnetism", "R2": "R2_repel", "R3": "R3_causal_push",
        "C1": "C1_sequential_glow", "C2": "C2_disintegration", "C3": "C3_ghost_outline",
        "D1": "D1_speech_bubble", "D2": "D2_thought_bubble", "D3": "D3_alert", "D4": "D4_interjection",
    }

    if discrepancy.animation_id:
        # Extract short ID: "P2c_emanation_joy" → "P2C", "I1" → "I1"
        aid = discrepancy.animation_id.split("_")[0].upper()
        if aid in _ID_TO_TEMPLATE:
            # Validate target count vs animation's target_type
            anim_def = get_animation(aid)
            if anim_def and discrepancy.target_entities:
                n = len(discrepancy.target_entities)
                is_scene = discrepancy.target_entities == ["scene"]
                target_ok = (
                    (is_scene and "scene" in anim_def.target_type) or
                    (n == 1 and not is_scene and "entity" in anim_def.target_type) or
                    (n == 2 and "duo" in anim_def.target_type) or
                    (n >= 3 and "group" in anim_def.target_type)
                )
                if not target_ok:
                    logger.warning(
                        "[tellimation] Target count mismatch for %s: got %d targets, "
                        "valid types are %s. Falling back to category default.",
                        aid, n, anim_def.target_type,
                    )
                    # Try to find another animation in the same category that fits
                    for alt_id, alt_tmpl in _ID_TO_TEMPLATE.items():
                        alt_def = get_animation(alt_id)
                        if alt_def and alt_def.category == category:
                            alt_ok = (
                                (is_scene and "scene" in alt_def.target_type) or
                                (n == 1 and not is_scene and "entity" in alt_def.target_type) or
                                (n == 2 and "duo" in alt_def.target_type) or
                                (n >= 3 and "group" in alt_def.target_type)
                            )
                            if alt_ok:
                                logger.info("[tellimation] Using %s instead of %s", alt_id, aid)
                                return alt_tmpl
                    # No alternative found, use original anyway
            return _ID_TO_TEMPLATE[aid]

    # No animation_id → no animation
    logger.warning("[tellimation] No animation_id for discrepancy: %s", discrepancy.description)
    return None


def select_discrepancy(
    corrections: List[Discrepancy],
    suggestions: List[Discrepancy],
    study_log_entries: List[Dict[str, Any]],
) -> Optional[Discrepancy]:
    """Deterministic selection of which discrepancy to animate.

    Priority: corrections over suggestions.
    Within the list (ordered by severity/relevance from LLM):
    - Pick the first item whose animation_id was NOT resolved last time.
    - If all were resolved last time, pick the first item anyway.

    Args:
        corrections: correction discrepancies, ordered by severity (most severe first).
        suggestions: suggestion discrepancies, ordered by relevance (most relevant first).
        study_log_entries: flat list of all log entries for this participant
            (from study_log.json or training_log.json).

    Returns:
        The chosen Discrepancy, or None if both lists are empty.
    """
    candidates = corrections if corrections else suggestions
    if not candidates:
        return None

    # Build map: animation_id → last resolved status from logs
    last_resolved: Dict[str, bool] = {}
    for entry in study_log_entries:
        if entry.get("event") == "resolution" and entry.get("animation_id"):
            last_resolved[entry["animation_id"]] = entry.get("resolved", False)

    # Try to find first candidate whose last resolution is NOT true
    for disc in candidates:
        aid = disc.animation_id
        if aid is None:
            continue
        if aid not in last_resolved or not last_resolved[aid]:
            # Never seen or last time was not resolved → pick this one
            return disc

    # All candidates were resolved last time → pick the first one
    return candidates[0]


def load_animation_params(
    animation_id: str,
    study_log_entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Load animation parameters from grammar JSON.

    If the animation was played before and last resolution was False,
    accentuate/vary parameters to make the animation more noticeable.
    Otherwise, use defaults.

    Args:
        animation_id: e.g. "I1_spotlight" or "I1"
        study_log_entries: flat list of all log entries for this participant.

    Returns:
        Dict of parameter overrides to pass to the animation.
    """
    import random

    # Extract the short ID (e.g. "I1" from "I1_spotlight", "P2a" from "P2a_emanation_shame")
    short_id = animation_id.split("_")[0].upper()

    # Load grammar JSON
    grammar_dir = Path(__file__).parent.parent.parent / "animations" / "grammar"
    grammar_path = grammar_dir / f"{short_id}.json"
    if not grammar_path.exists():
        return {}

    grammar = json.load(open(grammar_path))
    parameters = grammar.get("parameters", [])
    if not parameters:
        return {}

    # Check last resolution for this animation
    last_resolved = None
    for entry in study_log_entries:
        if entry.get("event") == "resolution" and entry.get("animation_id") == short_id:
            last_resolved = entry.get("resolved", False)

    # Build param dict
    params: Dict[str, Any] = {}

    if last_resolved is None or last_resolved is True:
        # First time or last time resolved → use defaults
        for p in parameters:
            params[p["name"]] = p["default"]
    else:
        # Last time NOT resolved → accentuate parameters
        for p in parameters:
            default = p["default"]
            prange = p.get("range", [])
            ptype = p.get("type", "")

            if ptype == "float" and isinstance(default, (int, float)) and len(prange) == 2:
                lo, hi = prange
                # Push 40% toward the max, add small random variation
                accentuated = default + (hi - default) * 0.4
                variation = (hi - lo) * 0.1 * (random.random() - 0.5)
                params[p["name"]] = max(lo, min(hi, round(accentuated + variation, 3)))

            elif ptype == "int" and isinstance(default, int) and len(prange) == 2:
                lo, hi = prange
                accentuated = default + (hi - default) * 0.4
                variation = (hi - lo) * 0.1 * (random.random() - 0.5)
                params[p["name"]] = max(lo, min(hi, round(accentuated + variation)))

            elif ptype == "rgb_vary" and isinstance(default, list) and len(default) == 3:
                # Vary each RGB channel by ±30
                params[p["name"]] = [
                    max(0, min(255, ch + random.randint(-30, 30)))
                    for ch in default
                ]

            else:
                # Non-numeric (rgb, string list, etc.) → use default
                params[p["name"]] = default

        logger.info("[tellimation] Accentuated params for %s: %s", animation_id, params)

    return params


async def generate_invocation_array(
    api_key: str,
    sprite_code: Dict[str, Any],
    manifest: SceneManifest,
    student_profile: StudentProfile,
    discrepancies: List[Discrepancy],
) -> InvocationArray:
    """Generate a structured invocation array from a list of discrepancies.

    For each discrepancy:
    1. Select the best animation via the grammar loader
    2. Build animation parameters from grammar defaults
    3. Build an AnimationInvocation with the result

    Corrections come first in the sequence, then suggestions.
    Within each pass, ordered by category priority.

    Args:
        api_key: Gemini API key.
        sprite_code: Current scene sprite data.
        manifest: Scene manifest.
        student_profile: Child's profile with efficacy history.
        discrepancies: Unified discrepancy list from assessment.

    Returns:
        InvocationArray with ordered sequence of animations.
    """
    if not discrepancies:
        return InvocationArray(sequence=[])

    # Sort: corrections first, then suggestions; within each, by category priority
    sorted_disc = sorted(
        discrepancies,
        key=lambda d: (
            0 if d.pass_type == "correction" else 1,
            _CATEGORY_PRIORITY.get(d.type, 99),
        ),
    )

    sequence: List[AnimationInvocation] = []

    for disc in sorted_disc:
        # Determine target entity
        target_id = disc.target_entities[0] if disc.target_entities else ""
        if not target_id:
            logger.warning("[invocation] Skipping discrepancy with no target: %s",
                           disc.description)
            continue

        # Determine MISL element for the LLM call
        misl_element = _CATEGORY_TO_MISL.get(disc.type, "character")
        if disc.misl_elements:
            # Try to map MISL code to full MISL key
            code_to_key = _build_misl_code_to_key()
            for code in disc.misl_elements:
                if code in code_to_key:
                    misl_element = code_to_key[code]
                    break

        # Use animation_id from the discrepancy (set by LLM)
        aid = _select_animation_for_discrepancy(disc, student_profile)
        if aid:
            sequence.append(AnimationInvocation(
                animation_id=aid,
                targets=disc.target_entities,
                parameter_overrides={},
            ))

    # Hard cap: max 2 animations per invocation array
    sequence = sequence[:2]

    result = InvocationArray(sequence=sequence)
    logger.info("[invocation] Generated invocation array with %d animations",
                len(sequence))
    return result


def _build_misl_code_to_key() -> Dict[str, str]:
    """Build a mapping from MISL abbreviation codes to full MISL keys."""
    from config.misl import MACROSTRUCTURE, MICROSTRUCTURE

    code_map: Dict[str, str] = {}
    for key, info in MACROSTRUCTURE.items():
        label = info["label"]
        # Extract code from label like "Character (CH)" → "CH"
        if "(" in label and ")" in label:
            code = label.split("(")[1].split(")")[0]
            code_map[code] = key
    for key, info in MICROSTRUCTURE.items():
        label = info["label"]
        if "(" in label and ")" in label:
            code = label.split("(")[1].split(")")[0]
            code_map[code] = key
    return code_map
