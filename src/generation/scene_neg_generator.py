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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

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

# SKILL framework files
_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
_SKILL_MACRO_PATH = _CONFIG_DIR / "skill_macro.txt"
_SKILL_MICRO_PATH = _CONFIG_DIR / "skill_micro.txt"

# Cached SKILL texts (loaded once)
_skill_macro_cache: Optional[str] = None
_skill_micro_cache: Optional[str] = None


def _load_skill_macro() -> str:
    """Load SKILL macro-objectives from config/skill_macro.txt."""
    global _skill_macro_cache
    if _skill_macro_cache is None:
        _skill_macro_cache = _SKILL_MACRO_PATH.read_text(encoding="utf-8")
    return _skill_macro_cache


def _load_skill_micro() -> str:
    """Load SKILL micro-objectives from config/skill_micro.txt."""
    global _skill_micro_cache
    if _skill_micro_cache is None:
        _skill_micro_cache = _SKILL_MICRO_PATH.read_text(encoding="utf-8")
    return _skill_micro_cache


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _build_initial_prompt(
    student_profile: Optional[StudentProfile],
    theme: str,
) -> str:
    """Build user prompt for an initial scene (no story state)."""
    profile_ctx = ""
    if student_profile and student_profile.total_utterances > 0:
        profile_ctx = student_profile.to_prompt_context()
    else:
        profile_ctx = "(New student — no error history yet.)"

    return INITIAL_SCENE_USER_PROMPT.format(
        skill_macro=_load_skill_macro(),
        skill_micro=_load_skill_micro(),
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

    # Student profile
    profile_ctx = ""
    if student_profile:
        profile_ctx = student_profile.to_prompt_context()

    scene_number = len(story_state.scenes) + 1

    return CONTINUATION_SCENE_USER_PROMPT.format(
        skill_macro=_load_skill_macro(),
        skill_micro=_load_skill_micro(),
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
