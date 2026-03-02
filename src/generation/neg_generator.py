"""NEG generation and live update via Gemini 3.1 Pro.

Two public functions:

- ``generate_neg_for_plot()`` — generate initial NEGs for all scenes offline.
- ``update_neg_live()`` — update remaining scenes' NEGs based on student profile.

Both call ``gemini-3.1-pro-preview`` and validate output using the ``NEG``
pydantic model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from src.models.neg import NEG
from src.models.student_profile import StudentProfile
from src.generation.prompts.neg_short_prompt import (
    NEG_SHORT_SYSTEM_PROMPT,
    NEG_SHORT_USER_PROMPT_TEMPLATE,
    NEG_UPDATE_SYSTEM_PROMPT,
    NEG_UPDATE_USER_PROMPT_TEMPLATE,
)
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "config")


def _load_skill_micro() -> str:
    """Load skill_micro.txt from config/."""
    micro_path = os.path.join(_CONFIG_DIR, "skill_micro.txt")
    if os.path.exists(micro_path):
        with open(micro_path) as f:
            return f.read()
    return ""

NEG_MODEL_ID = "gemini-3.1-pro-preview"

# Timeouts and retries
NEG_TIMEOUT = 60
NEG_MAX_RETRIES = 2


def _validate_neg_response(
    data: Dict[str, Any],
    expected_scene_ids: Optional[List[str]] = None,
) -> Dict[str, NEG]:
    """Validate and parse the LLM NEG response.

    Args:
        data: Parsed JSON from the LLM.
        expected_scene_ids: If provided, verify that all scene_ids are present.

    Returns:
        Dict mapping scene_id -> validated NEG object.
    """
    scenes_list = data.get("scenes", [])
    if not isinstance(scenes_list, list):
        raise ValueError("NEG response missing 'scenes' list")

    result: Dict[str, NEG] = {}
    for entry in scenes_list:
        scene_id = entry.get("scene_id", "")
        neg_data = entry.get("neg", {})
        if not scene_id:
            logger.warning("[neg] Skipping entry without scene_id")
            continue
        neg = NEG.model_validate(neg_data)
        result[scene_id] = neg

    if expected_scene_ids:
        missing = set(expected_scene_ids) - set(result.keys())
        if missing:
            logger.warning("[neg] Missing NEGs for scenes: %s", missing)

    return result


async def generate_neg_for_plot(
    api_key: str,
    plot_scenes: List[Dict[str, Any]],
    skill_objectives: Optional[List[str]] = None,
    student_profile: Optional[StudentProfile] = None,
    masks_summary: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, NEG]:
    """Generate initial NEGs for all scenes in a plot.

    Args:
        api_key: Gemini API key.
        plot_scenes: List of scene dicts, each with at minimum a "manifest"
            key containing the scene manifest (entities, relations, actions).
        skill_objectives: SKILL objectives for the session.
        student_profile: The child's error profile (adapts priorities/tolerances).
        masks_summary: Dict mapping entity_id -> list of sub-entity IDs from
            visual masks (e.g. {"turtle_01": ["turtle_01.body", "turtle_01.shell"]}).

    Returns:
        Dict mapping scene_id -> NEG for each scene.
    """
    if skill_objectives is None:
        skill_objectives = [
            "descriptive_adjectives",
            "spatial_prepositions",
            "action_verbs",
        ]

    # Build compact plot representation for the prompt
    plot_for_prompt = []
    expected_ids = []
    for scene in plot_scenes:
        manifest = scene.get("manifest", {})
        scene_id = manifest.get("scene_id", "")
        expected_ids.append(scene_id)
        plot_for_prompt.append({
            "scene_id": scene_id,
            "entities": manifest.get("entities", []),
            "relations": manifest.get("relations", []),
            "actions": manifest.get("actions", []),
        })

    # Load skill micro-objectives
    micro = _load_skill_micro()

    user_prompt = NEG_SHORT_USER_PROMPT_TEMPLATE.format(
        plot_json=json.dumps(plot_for_prompt, indent=2),
        skill_objectives=", ".join(skill_objectives),
        student_profile=(
            student_profile.to_prompt_context()
            if student_profile else "(new student — no error history yet)"
        ),
        masks_summary=json.dumps(masks_summary or {}, indent=2),
        skill_micro=micro or "(not available yet)",
    )

    client = genai.Client(api_key=api_key)
    last_exc: Optional[Exception] = None

    for attempt in range(1, NEG_MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=NEG_MODEL_ID,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=NEG_SHORT_SYSTEM_PROMPT,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        temperature=0.7,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=NEG_TIMEOUT,
            )
            data = _extract_json(_get_response_text(response))
            result = _validate_neg_response(data, expected_ids)
            logger.info("[neg-gen] Generated NEGs for %d/%d scenes",
                        len(result), len(expected_ids))
            return result

        except asyncio.TimeoutError:
            logger.warning("[neg-gen] Attempt %d/%d timed out after %ds",
                           attempt, NEG_MAX_RETRIES, NEG_TIMEOUT)
            last_exc = asyncio.TimeoutError(
                f"NEG generation timed out after {NEG_TIMEOUT}s")
        except Exception as exc:
            logger.warning("[neg-gen] Attempt %d/%d failed (%s): %s",
                           attempt, NEG_MAX_RETRIES,
                           type(exc).__name__, exc or "no details")
            last_exc = exc

    raise last_exc  # type: ignore[misc]


async def update_neg_live(
    api_key: str,
    remaining_negs: Dict[str, NEG],
    student_profile: StudentProfile,
    completed_scene_ids: Optional[List[str]] = None,
) -> Dict[str, NEG]:
    """Update NEGs for remaining scenes based on student profile (live).

    Called after each narration loop completes, when the student profile
    reveals error patterns that should influence upcoming scenes.

    Args:
        api_key: Gemini API key.
        remaining_negs: Dict mapping scene_id -> current NEG for unplayed scenes.
        student_profile: The child's current error profile.
        completed_scene_ids: List of scene_ids already completed.

    Returns:
        Dict mapping scene_id -> updated NEG for each remaining scene.
    """
    if completed_scene_ids is None:
        completed_scene_ids = []

    # Serialize NEGs for the prompt
    negs_for_prompt = []
    expected_ids = list(remaining_negs.keys())
    for scene_id, neg in remaining_negs.items():
        negs_for_prompt.append({
            "scene_id": scene_id,
            "neg": neg.model_dump(),
        })

    user_prompt = NEG_UPDATE_USER_PROMPT_TEMPLATE.format(
        remaining_negs_json=json.dumps(negs_for_prompt, indent=2),
        student_profile=student_profile.to_prompt_context(),
        completed_scenes=json.dumps(completed_scene_ids),
    )

    client = genai.Client(api_key=api_key)
    last_exc: Optional[Exception] = None

    for attempt in range(1, NEG_MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=NEG_MODEL_ID,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=NEG_UPDATE_SYSTEM_PROMPT,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        temperature=0.5,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=NEG_TIMEOUT,
            )
            data = _extract_json(_get_response_text(response))
            result = _validate_neg_response(data, expected_ids)
            logger.info("[neg-update] Updated NEGs for %d/%d scenes",
                        len(result), len(expected_ids))
            return result

        except asyncio.TimeoutError:
            logger.warning("[neg-update] Attempt %d/%d timed out after %ds",
                           attempt, NEG_MAX_RETRIES, NEG_TIMEOUT)
            last_exc = asyncio.TimeoutError(
                f"NEG update timed out after {NEG_TIMEOUT}s")
        except Exception as exc:
            logger.warning("[neg-update] Attempt %d/%d failed (%s): %s",
                           attempt, NEG_MAX_RETRIES,
                           type(exc).__name__, exc or "no details")
            last_exc = exc

    # On failure, return the original NEGs unchanged
    logger.warning("[neg-update] All attempts failed, returning original NEGs")
    return remaining_negs
