"""Scene Manifest + NEG co-generation module.

Replaces the old separate manifest generation (scene_generator.py Step 1)
and NEG generation (neg_generator.py) with a single LLM call that produces
both structures together.

Model: Gemini 3 Flash (gemini-3-flash-preview)

The co-generation ensures that the scene is designed WITH its learning
objectives in mind.  Entity properties serve as "descriptive affordances"
— visual features that invite and support specific verbal descriptions
from the child.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from config.misl import (
    AGE_EXPECTATIONS,
    ALL_KEYS,
    MACROSTRUCTURE,
    MICRO_AGE_THRESHOLD_LEVEL1,
    MICRO_AGE_THRESHOLD_LEVEL2,
    MICRO_KEYS,
    MICROSTRUCTURE,
    MISL_TO_ANIMATIONS,
    COUNT_ANIMATIONS,
)
from src.generation.prompts.scene_neg_prompt import (
    CONTINUATION_SCENE_USER_PROMPT,
    INITIAL_SCENE_USER_PROMPT,
    SCENE_NEG_SYSTEM_PROMPT,
)
from src.generation.utils import extract_json, get_response_text
from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.story_state import StoryState
from src.models.student_profile import StudentProfile

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"

# Timeouts and retries
GENERATION_TIMEOUT = 90  # seconds (higher thinking budget needs more time)
MAX_RETRIES = 2


# Cached MISL rubric text (built once)
_misl_rubric_cache: Optional[str] = None


def _build_misl_rubric() -> str:
    """Build a human-readable text of the full MISL rubric for prompt injection."""
    global _misl_rubric_cache
    if _misl_rubric_cache is not None:
        return _misl_rubric_cache

    lines: List[str] = []

    lines.append("# MISL Rubric — Macrostructure (7 elements, scores 0-3)")
    lines.append("")
    for key, info in MACROSTRUCTURE.items():
        lines.append(f"## {info['label']}")
        for score, desc in info["scores"].items():
            lines.append(f"  {score}: {desc}")
        anims = MISL_TO_ANIMATIONS.get(key, [])
        if anims:
            lines.append(f"  Eligible animations: {', '.join(anims)}")
        lines.append("")

    lines.append("# MISL Rubric — Microstructure (8 elements, scores 0-3)")
    lines.append("")
    for key, info in MICROSTRUCTURE.items():
        lines.append(f"## {info['label']}")
        for score, desc in info["scores"].items():
            lines.append(f"  {score}: {desc}")
        anims = MISL_TO_ANIMATIONS.get(key, [])
        if anims:
            lines.append(f"  Eligible animations: {', '.join(anims)}")
        lines.append("")

    lines.append("# Count animations (apply to any element for count errors)")
    lines.append(f"  {', '.join(COUNT_ANIMATIONS)}")

    _misl_rubric_cache = "\n".join(lines)
    return _misl_rubric_cache


def _build_developmental_expectations(age: int) -> str:
    """Format the expected MISL levels for the child's age."""
    lines: List[str] = []
    lines.append(f"Child age: {age}")
    lines.append("")

    # Macrostructure expectations
    clamped = max(4, min(15, age))
    age_row = AGE_EXPECTATIONS.get(clamped, {})
    lines.append("Macrostructure expected levels:")
    for key, level in age_row.items():
        lines.append(f"  {key}: {level}")
    lines.append("")

    # Microstructure expectations
    if age >= MICRO_AGE_THRESHOLD_LEVEL2:
        micro_level = 2
    elif age >= MICRO_AGE_THRESHOLD_LEVEL1:
        micro_level = 1
    else:
        micro_level = 0
    lines.append(f"Microstructure expected level (all elements): {micro_level}")
    lines.append("")
    lines.append(
        "CRITICAL: target_level must NEVER exceed expected_level + 1 "
        "(zone of proximal development). For elements already at or above "
        "expected level, target_level = current_level (maintenance, not growth)."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _build_initial_prompt(
    student_profile: Optional[StudentProfile],
    theme: str,
) -> str:
    """Build user prompt for an initial scene (no story state)."""
    age = student_profile.age if student_profile else 8

    profile_ctx = ""
    if student_profile and student_profile.total_utterances > 0:
        profile_ctx = student_profile.to_prompt_context()
    else:
        profile_ctx = f"(New student, age {age} — no error history yet.)"

    return INITIAL_SCENE_USER_PROMPT.format(
        misl_rubric=_build_misl_rubric(),
        developmental_expectations=_build_developmental_expectations(age),
        student_profile=profile_ctx,
        theme=theme,
    )


def _build_continuation_prompt(
    story_state: StoryState,
    student_profile: Optional[StudentProfile],
    previous_manifest: Optional[Dict[str, Any]],
    previous_neg: Optional[Dict[str, Any]],
) -> str:
    """Build user prompt for a continuation scene."""
    # Story context: narrative summaries of each scene
    story_lines = []
    for s in story_state.scenes:
        story_lines.append(
            f"- {s.get('scene_id', '?')}: "
            f"entities={[e['id'] for e in s.get('manifest', {}).get('entities', [])]}"
        )
    story_context = "\n".join(story_lines) if story_lines else "(first scene)"

    # Previous manifest
    prev_manifest_str = json.dumps(previous_manifest, indent=2) if previous_manifest else "{}"

    # Previous NEG
    prev_neg_str = json.dumps(previous_neg, indent=2) if previous_neg else "{}"

    # Active entities summary
    entity_lines = []
    for eid, ent in story_state.active_entities.items():
        entity_lines.append(
            f"- {eid} (type={ent.type}, appeared={ent.first_appeared}, "
            f"pos={ent.last_position})"
        )
    active_entities = "\n".join(entity_lines) if entity_lines else "(none)"

    # Student profile + age
    age = student_profile.age if student_profile else 8
    profile_ctx = ""
    if student_profile:
        profile_ctx = student_profile.to_prompt_context()

    scene_number = len(story_state.scenes) + 1

    return CONTINUATION_SCENE_USER_PROMPT.format(
        misl_rubric=_build_misl_rubric(),
        developmental_expectations=_build_developmental_expectations(age),
        story_context=story_context,
        previous_manifest=prev_manifest_str,
        previous_neg=prev_neg_str,
        active_entities=active_entities,
        student_profile=profile_ctx,
        scene_number=scene_number,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_response(data: Dict[str, Any]) -> Tuple[SceneManifest, NEG]:
    """Validate the LLM response and return parsed models.

    Raises ValueError if required fields are missing or invalid.
    """
    manifest_data = data.get("manifest")
    if not manifest_data:
        raise ValueError("Response missing 'manifest' field")
    manifest = SceneManifest.model_validate(manifest_data)

    neg_data = data.get("neg")
    if not neg_data:
        raise ValueError("Response missing 'neg' field")
    neg = NEG.model_validate(neg_data)

    return manifest, neg


# Expected y-ranges (normalized) per zone — generous tolerance
_ZONE_Y_RANGES: Dict[str, Tuple[float, float]] = {
    "background": (0.15, 0.60),
    "midground": (0.40, 0.80),
    "foreground": (0.55, 1.0),
}

# Flying/airborne entity types exempt from ground_contact checks
_AIRBORNE_TYPES = frozenset({
    "bird", "cloud", "butterfly", "kite", "airplane", "bee", "bat",
    "dragonfly", "balloon", "eagle", "hawk", "owl_flying",
})


def _validate_semantic(
    manifest: "SceneManifest",
    neg: "NEG",
    data: Dict[str, Any],
) -> List[str]:
    """Semantic validation of the manifest + NEG.

    Returns a list of warning strings.  These are non-fatal — the scene is
    still usable, but the warnings highlight quality issues that the LLM
    prompt should ideally prevent.
    """
    warnings: List[str] = []

    # ------------------------------------------------------------------
    # 1. Zone/position consistency (positions are already normalized 0-1)
    # ------------------------------------------------------------------
    for ent in manifest.entities:
        zone = ent.position.zone
        if zone and zone in _ZONE_Y_RANGES:
            y_norm = ent.position.y
            lo, hi = _ZONE_Y_RANGES[zone]
            if y_norm < lo - 0.05 or y_norm > hi + 0.05:
                warnings.append(
                    f"Entity {ent.id}: y={y_norm:.2f} "
                    f"inconsistent with zone '{zone}' "
                    f"(expected {lo:.2f}-{hi:.2f})"
                )

    # ------------------------------------------------------------------
    # 2. (Removed — scale consistency is now enforced by _apply_zone_scale)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 3. Minimum entity count (3-5 recommended)
    # ------------------------------------------------------------------
    n_entities = len(manifest.entities)
    if n_entities < 3:
        warnings.append(
            f"Only {n_entities} entities (3-5 recommended)"
        )

    # ------------------------------------------------------------------
    # 4. Minimum spatial relations (2+ recommended)
    # ------------------------------------------------------------------
    if len(manifest.relations) < 2:
        warnings.append(
            f"Only {len(manifest.relations)} spatial relation(s) "
            f"(2+ recommended)"
        )

    # ------------------------------------------------------------------
    # 5. Color diversity (at least 2 distinct color families)
    # ------------------------------------------------------------------
    color_words: List[str] = []
    for ent in manifest.entities:
        raw = ent.properties.get("color", "").strip().lower()
        if raw:
            # Take the first significant word (skip modifiers like "warm", "bright")
            for w in raw.split():
                if w not in ("warm", "bright", "dark", "light", "pale",
                             "deep", "soft", "rich", "dusty", "vivid"):
                    color_words.append(w)
                    break
            else:
                color_words.append(raw.split()[0])
    unique_colors = set(color_words)
    if len(unique_colors) < 2 and n_entities >= 2:
        warnings.append(
            f"Low color diversity ({unique_colors}) — "
            f"need 2+ distinct color families"
        )

    # ------------------------------------------------------------------
    # 6. Ground contact for foreground entities
    # ------------------------------------------------------------------
    for ent in manifest.entities:
        if (ent.position.zone == "foreground"
                and not ent.position.ground_contact
                and ent.type.lower() not in _AIRBORNE_TYPES):
            warnings.append(
                f"Entity {ent.id} ({ent.type}) in foreground "
                f"without ground_contact"
            )

    # ------------------------------------------------------------------
    # 7. Bounding box within canvas (normalized 0.0-1.0)
    # ------------------------------------------------------------------
    for ent in manifest.entities:
        wh = ent.width_hint or 0.09
        hh = ent.height_hint or 0.14
        x, y = ent.position.x, ent.position.y
        if x - wh / 2 < -0.01 or x + wh / 2 > 1.01:
            warnings.append(
                f"Entity {ent.id}: x-bounds overflow canvas "
                f"(x={x:.2f}, w={wh:.2f})"
            )
        if y - hh / 2 < -0.01 or y + hh / 2 > 1.01:
            warnings.append(
                f"Entity {ent.id}: y-bounds overflow canvas "
                f"(y={y:.2f}, h={hh:.2f})"
            )

    # ------------------------------------------------------------------
    # 8. Missing orientation on character entities
    # ------------------------------------------------------------------
    character_types = {"person", "boy", "girl", "man", "woman", "child",
                       "rabbit", "cat", "dog", "fox", "bear", "owl",
                       "frog", "mouse", "squirrel", "deer", "bird",
                       "penguin", "monkey", "elephant", "lion", "tiger"}
    for ent in manifest.entities:
        if ent.type.lower() in character_types and not ent.orientation:
            warnings.append(
                f"Character entity {ent.id} ({ent.type}) missing orientation"
            )

    # ------------------------------------------------------------------
    # 9. NEG target count (at least 3)
    # ------------------------------------------------------------------
    if len(neg.targets) < 3:
        warnings.append(
            f"Only {len(neg.targets)} NEG target(s) (3+ recommended)"
        )

    # ------------------------------------------------------------------
    # 10. Entity / structural element overlap
    # ------------------------------------------------------------------
    structural = (
        data.get("manifest", {}).get("background", {}).get("structural_elements", [])
    )
    if structural and isinstance(structural, list):
        structural_lower = {
            s.lower().strip() for s in structural if isinstance(s, str)
        }
        for ent in manifest.entities:
            ent_type = ent.type.lower().strip()
            for se in structural_lower:
                if ent_type in se or se in ent_type:
                    warnings.append(
                        f"Entity {ent.id} (type='{ent.type}') may overlap "
                        f"with structural element '{se}' — risk of double "
                        f"rendering"
                    )

    return warnings


# ---------------------------------------------------------------------------
# Post-processing: auto scale + orientation resolution
# ---------------------------------------------------------------------------

_DEFAULT_ZONE_SCALES = {"sky": 0.5, "background": 0.7, "midground": 0.9, "foreground": 1.0}


def _apply_zone_scale(manifest: SceneManifest) -> None:
    """Apply zone-based scale to entity width/height hints.

    The LLM provides BASE sizes (foreground scale). This function multiplies
    them by the zone's scale_hint so background entities are automatically
    smaller. Also sets scale_factor on each entity for downstream use.
    """
    # Build zone → scale_hint map from manifest background zones
    zone_scales: Dict[str, float] = {}
    if manifest.background and manifest.background.zones:
        for z in manifest.background.zones:
            zone_scales[z.id] = z.scale_hint

    for ent in manifest.entities:
        zone = ent.position.zone
        if zone:
            scale = zone_scales.get(zone, _DEFAULT_ZONE_SCALES.get(zone, 1.0))
        else:
            scale = 1.0
        ent.scale_factor = scale
        if ent.width_hint is not None:
            ent.width_hint *= scale
        if ent.height_hint is not None:
            ent.height_hint *= scale


def _sync_manifest_to_data(manifest: SceneManifest, data: Dict[str, Any]) -> None:
    """Sync post-processed manifest values back to the raw data dict.

    After _apply_zone_scale and _resolve_orientations modify the manifest model,
    the raw data dict still has original LLM values. This syncs width_hint,
    height_hint, scale_factor, and orientation back so downstream consumers
    (scene_generator.py) read the correct values.
    """
    raw_entities = data.get("manifest", {}).get("entities", [])
    model_by_id = {ent.id: ent for ent in manifest.entities}
    for raw_ent in raw_entities:
        eid = raw_ent.get("id", "")
        model_ent = model_by_id.get(eid)
        if model_ent is None:
            continue
        if model_ent.width_hint is not None:
            raw_ent["width_hint"] = model_ent.width_hint
        if model_ent.height_hint is not None:
            raw_ent["height_hint"] = model_ent.height_hint
        if model_ent.scale_factor is not None:
            raw_ent["scale_factor"] = model_ent.scale_factor
        if model_ent.orientation is not None:
            raw_ent["orientation"] = model_ent.orientation


def _resolve_orientations(manifest: SceneManifest) -> None:
    """Resolve relational orientations (facing:<entity_id>) to absolute directions.

    Compares x positions to determine facing_left vs facing_right.
    """
    positions = {ent.id: ent.position.x for ent in manifest.entities}
    for ent in manifest.entities:
        if not ent.orientation or not ent.orientation.startswith("facing:"):
            continue
        target_id = ent.orientation[len("facing:"):]
        target_x = positions.get(target_id)
        if target_x is None:
            logger.warning(
                "[orientation] %s: target '%s' not found, defaulting to facing_right",
                ent.id, target_id,
            )
            ent.orientation = "facing_right"
            continue
        ent.orientation = "facing_left" if target_x < ent.position.x else "facing_right"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def generate_scene_and_neg(
    api_key: str,
    story_state: Optional[StoryState] = None,
    student_profile: Optional[StudentProfile] = None,
    theme: str = "",
    previous_manifest: Optional[Dict[str, Any]] = None,
    previous_neg: Optional[Dict[str, Any]] = None,
) -> Tuple[SceneManifest, NEG, Dict[str, Any]]:
    """Co-generate a scene manifest and NEG in a single LLM call.

    Args:
        api_key: Gemini API key.
        story_state: Cumulative story state, or None for initial scene.
        student_profile: Child's error profile.
        theme: Story theme for initial scenes. Ignored for continuations.
        previous_manifest: Manifest dict of the previous scene (for continuity).
        previous_neg: NEG dict of the previous scene (for continuity).

    Returns:
        Tuple of (SceneManifest, NEG, raw_response_dict).
        The raw dict contains additional fields like scene_description,
        background_description, carried_over_entities, background_changed.
    """
    # Build user prompt
    is_initial = story_state is None or len(story_state.scenes) == 0
    if is_initial:
        user_prompt = _build_initial_prompt(student_profile, theme)
    else:
        user_prompt = _build_continuation_prompt(
            story_state, student_profile, previous_manifest, previous_neg
        )

    client = genai.Client(api_key=api_key)

    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                "[scene-neg] Attempt %d/%d — %s scene (model=%s)",
                attempt, MAX_RETRIES,
                "initial" if is_initial else "continuation",
                MODEL_ID,
            )

            t0 = time.time()
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=MODEL_ID,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SCENE_NEG_SYSTEM_PROMPT,
                        thinking_config=types.ThinkingConfig(thinking_budget=8192),
                        temperature=1.0,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=GENERATION_TIMEOUT,
            )
            api_elapsed = time.time() - t0
            logger.info("[scene-neg] API call took %.1fs", api_elapsed)

            t1 = time.time()
            raw_text = get_response_text(response)
            data = extract_json(raw_text)
            manifest, neg = _validate_response(data)

            # Post-processing: auto-scale and orientation resolution
            _apply_zone_scale(manifest)
            _resolve_orientations(manifest)

            # Sync post-processed values back to raw data dict
            # (scene_generator reads from data, not from manifest model)
            _sync_manifest_to_data(manifest, data)

            parse_elapsed = time.time() - t1
            logger.info("[scene-neg] Parsing + validation took %.1fs", parse_elapsed)

            # Semantic validation (non-fatal warnings)
            sem_warnings = _validate_semantic(manifest, neg, data)
            for w in sem_warnings:
                logger.warning("[scene-neg] Semantic: %s", w)
            if sem_warnings:
                logger.info(
                    "[scene-neg] %d semantic warning(s) for scene '%s'",
                    len(sem_warnings), manifest.scene_id,
                )

            logger.info(
                "[scene-neg] Generated scene '%s': %d entities, %d relations, "
                "%d actions, %d NEG targets (coverage_check=%s) — total %.1fs",
                manifest.scene_id,
                len(manifest.entities),
                len(manifest.relations),
                len(manifest.actions),
                len(neg.targets),
                neg.skill_coverage_check,
                time.time() - t0,
            )

            return manifest, neg, data

        except asyncio.TimeoutError:
            logger.warning(
                "[scene-neg] Attempt %d/%d timed out after %ds",
                attempt, MAX_RETRIES, GENERATION_TIMEOUT,
            )
            last_exc = asyncio.TimeoutError(
                f"Scene+NEG generation timed out after {GENERATION_TIMEOUT}s"
            )
        except Exception as exc:
            logger.warning(
                "[scene-neg] Attempt %d/%d failed (%s): %s",
                attempt, MAX_RETRIES, type(exc).__name__, exc,
            )
            last_exc = exc

    raise last_exc  # type: ignore[misc]
