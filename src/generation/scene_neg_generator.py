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
    QUANTITY_ANIMATIONS,
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
GENERATION_TIMEOUT = 60  # seconds
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

    lines.append("# Quantity animations (apply to any element for count errors)")
    lines.append(f"  {', '.join(QUANTITY_ANIMATIONS)}")

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
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
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
            parse_elapsed = time.time() - t1
            logger.info("[scene-neg] Parsing + validation took %.1fs", parse_elapsed)

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
