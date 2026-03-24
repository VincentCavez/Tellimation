"""Deterministic MISL candidate selector.

Pure Python, no LLM calls. Selects which MISL element(s) to pass to the
enrichment Gemini call based on scene targets, mention counts, and
resolution history.

Algorithm:
  1. Macro pass: fixed priority order CH > S > IE > A > CO > IR > P
     - Filter < 3 mentions (HARD gate — if none pass, fall through to micros)
     - Filter unresolved (relaxable — if none pass, ignore this filter)
     - Select first in priority order
  2. Micro pass (if no macro survived < 3 filter):
     - Filter < 3 mentions (relaxable — if none pass, ignore)
     - Filter unresolved (relaxable — if none pass, ignore)
     - Shuffle remaining candidates randomly
     - Pass list to Gemini
  3. If nothing: no_action
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from config.misl import MACRO_PRIORITY_ORDER, MICRO_CODES, ALL_MISL_CODES

logger = logging.getLogger(__name__)


def _element_in_scene(code: str, targets: Dict[str, Any]) -> bool:
    """Check if a MISL element is present in the scene's misl_targets."""
    macro = targets.get("macro", {})
    micro = targets.get("micro", {})
    val = macro.get(code) or micro.get(code)
    # Present if value is a non-empty list (not null, not [])
    if isinstance(val, list):
        return len(val) > 0
    return bool(val)


def _last_resolution_status(
    code: str,
    study_log_entries: List[Dict[str, Any]],
) -> Optional[bool]:
    """Get the last resolution status for a MISL element from study logs.

    Returns True if last occurrence was resolved, False if unresolved,
    None if never logged.
    """
    from config.misl import MISL_CODE_TO_KEY

    misl_key = MISL_CODE_TO_KEY.get(code, code)

    last_status: Optional[bool] = None
    for entry in study_log_entries:
        if entry.get("event") == "resolution":
            # Check if this resolution relates to this MISL element
            anim_id = entry.get("animation_id", "")
            pass_type = entry.get("pass_type", "")
            # Also check misl_element field if present
            entry_misl = entry.get("misl_element", "")
            if entry_misl == code or entry_misl == misl_key:
                last_status = entry.get("resolved", False)
    return last_status


def _apply_filters(
    candidates: List[str],
    mention_counts: Dict[str, int],
    study_log_entries: List[Dict[str, Any]],
) -> List[str]:
    """Apply progressive filters: < 3 mentions, then unresolved.

    If a filter eliminates all candidates, it is skipped (ignored).
    """
    # Filter 1: < 3 mentions
    under_3 = [c for c in candidates if mention_counts.get(c, 0) < 3]
    if under_3:
        candidates = under_3

    # Filter 2: last occurrence unresolved (or never seen)
    unresolved = []
    for c in candidates:
        status = _last_resolution_status(c, study_log_entries)
        if status is None or status is False:
            unresolved.append(c)
    if unresolved:
        candidates = unresolved

    return candidates


def select_misl_candidates(
    misl_targets: Dict[str, Any],
    mention_counts: Dict[str, int],
    study_log_entries: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[List[str]], Dict[str, Any]]:
    """Deterministic MISL candidate selection.

    Args:
        misl_targets: Scene's misl_targets dict with "macro" and "micro" keys.
        mention_counts: Per-scene mention counts for all 15 MISL elements.
        study_log_entries: Flat list of all log entries for this participant.

    Returns:
        Tuple of:
        - macro_selected: Selected macro element code, or None.
        - micro_candidates_shuffled: Shuffled list of micro candidates, or None.
        - trace: Dict with all intermediate filter results for logging.
    """
    trace: Dict[str, Any] = {
        "macro_in_scene": None,
        "macro_under_3": None,
        "macro_unresolved": None,
        "macro_selected": None,
        "micro_in_scene": None,
        "micro_under_3": None,
        "micro_unresolved": None,
        "micro_candidates_shuffled": None,
        "micro_gemini_selected": None,
    }

    if not misl_targets:
        logger.info("[misl_selector] No misl_targets — skipping selection")
        return None, None, trace

    # ── Macro pass ──
    macro_in_scene = [
        code for code in MACRO_PRIORITY_ORDER
        if _element_in_scene(code, misl_targets)
    ]
    trace["macro_in_scene"] = macro_in_scene

    if macro_in_scene:
        # Filter 1: < 3 mentions — HARD GATE for macros
        # If no macro passes this filter, fall through to micro pass.
        macro_under_3 = [c for c in macro_in_scene if mention_counts.get(c, 0) < 3]
        trace["macro_under_3"] = macro_under_3

        if macro_under_3:
            pool = macro_under_3

            # Filter 2: last occurrence unresolved or never seen (relaxable)
            macro_unresolved = []
            for c in pool:
                status = _last_resolution_status(c, study_log_entries)
                if status is None or status is False:
                    macro_unresolved.append(c)
            trace["macro_unresolved"] = macro_unresolved
            pool = macro_unresolved if macro_unresolved else pool

            # Select first (highest priority) from remaining pool
            priority_map = {code: i for i, code in enumerate(MACRO_PRIORITY_ORDER)}
            pool_sorted = sorted(pool, key=lambda c: priority_map.get(c, 99))
            selected = pool_sorted[0]
            trace["macro_selected"] = selected

            logger.info("[misl_selector] Macro selected: %s (from pool %s)", selected, pool_sorted)
            return selected, None, trace

        # All macros have >= 3 mentions → fall through to micro pass
        logger.info("[misl_selector] All macros >= 3 mentions, falling through to micros")

    # ── Micro pass (no macro survived < 3 filter, or no macros in scene) ──
    micro_in_scene = [
        code for code in MICRO_CODES
        if _element_in_scene(code, misl_targets)
    ]
    trace["micro_in_scene"] = micro_in_scene

    if not micro_in_scene:
        logger.info("[misl_selector] No macro or micro candidates in scene")
        return None, None, trace

    # Filter 1: < 3 mentions
    micro_under_3 = [c for c in micro_in_scene if mention_counts.get(c, 0) < 3]
    trace["micro_under_3"] = micro_under_3
    pool = micro_under_3 if micro_under_3 else micro_in_scene

    # Filter 2: last occurrence unresolved or never seen
    micro_unresolved = []
    for c in pool:
        status = _last_resolution_status(c, study_log_entries)
        if status is None or status is False:
            micro_unresolved.append(c)
    trace["micro_unresolved"] = micro_unresolved
    pool = micro_unresolved if micro_unresolved else pool

    # Shuffle to avoid LLM positional bias
    shuffled = list(pool)
    random.shuffle(shuffled)
    trace["micro_candidates_shuffled"] = shuffled

    logger.info("[misl_selector] Micro candidates (shuffled): %s", shuffled)
    return None, shuffled, trace
