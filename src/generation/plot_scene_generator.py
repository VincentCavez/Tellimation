"""Standalone plot + scene manifest generation via Gemini 3.1 Pro.

Generates a complete story plot (ordered list of scenes with manifests)
in a single LLM call. Decoupled from NEG and sprite code generation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from google import genai
from google.genai import types

from src.generation.prompts.plot_prompt import (
    PLOT_SYSTEM_PROMPT,
    PLOT_USER_PROMPT_TEMPLATE,
)
from src.generation.utils import extract_json, get_response_text
from src.models.plot import (
    PlotCharacter,
    PlotGenerationResult,
    PlotSetting,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3.1-pro-preview"

# Retry defaults
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 30.0  # seconds


class PlotGenerationError(Exception):
    """Raised when plot generation fails after all retries."""


def _build_user_prompt(character: PlotCharacter, setting: PlotSetting) -> str:
    """Build the user prompt from character and setting inputs."""
    traits_str = (
        ", ".join(f"{k}: {v}" for k, v in character.traits.items())
        if character.traits
        else "(none specified)"
    )

    return PLOT_USER_PROMPT_TEMPLATE.format(
        character_name=character.name,
        character_type=character.type,
        character_traits=traits_str,
        setting_lieu=setting.lieu,
        setting_ambiance=setting.ambiance,
        setting_epoch=setting.epoch,
    )


def _validate_plot_response(data: Dict[str, Any]) -> PlotGenerationResult:
    """Validate the LLM response against the Pydantic models.

    Raises ValueError if validation fails.
    """
    result = PlotGenerationResult.model_validate(data)

    if not result.plot:
        raise ValueError("Plot is empty -- no scenes generated")

    if len(result.plot) < 2:
        raise ValueError(
            f"Plot has only {len(result.plot)} scene(s); expected at least 2"
        )

    for scene in result.plot:
        if not scene.manifest.elements:
            raise ValueError(
                f"Scene {scene.scene_id} has no elements in its manifest"
            )

    return result


async def generate_plot(
    api_key: str,
    character: PlotCharacter,
    setting: PlotSetting,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    temperature: float = 0.9,
    thinking_budget: int = 2048,
) -> PlotGenerationResult:
    """Generate a complete story plot via Gemini 3.1 Pro.

    Makes a single LLM call to produce an ordered list of scenes,
    each with a scene manifest containing elements, relations, and ground.

    Args:
        api_key: Gemini API key.
        character: Main character definition (name, type, traits).
        setting: Setting definition (lieu, ambiance, epoch).
        max_retries: Maximum number of retry attempts on failure.
        initial_delay: Initial backoff delay in seconds.
        max_delay: Maximum backoff delay in seconds.
        temperature: LLM temperature.
        thinking_budget: Token budget for thinking/reasoning.

    Returns:
        PlotGenerationResult containing the full plot with scene manifests.

    Raises:
        PlotGenerationError: If generation fails after all retries.
    """
    user_prompt = _build_user_prompt(character, setting)
    client = genai.Client(api_key=api_key)

    last_error: Optional[Exception] = None
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "[plot-gen] Attempt %d/%d: calling %s...",
                attempt,
                max_retries,
                MODEL_ID,
            )

            response = await client.aio.models.generate_content(
                model=MODEL_ID,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=PLOT_SYSTEM_PROMPT,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=thinking_budget
                    ),
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )

            raw_text = get_response_text(response)
            logger.info(
                "[plot-gen] Got response (%d chars), parsing JSON...",
                len(raw_text),
            )

            data = extract_json(raw_text)
            result = _validate_plot_response(data)

            logger.info(
                "[plot-gen] Success: %d scenes generated.",
                len(result.plot),
            )
            return result

        except Exception as exc:
            last_error = exc
            logger.warning(
                "[plot-gen] Attempt %d/%d failed: %s: %s",
                attempt,
                max_retries,
                type(exc).__name__,
                exc,
            )

            if attempt < max_retries:
                logger.info("[plot-gen] Retrying in %.1f seconds...", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    raise PlotGenerationError(
        f"Plot generation failed after {max_retries} attempts. "
        f"Last error: {last_error}"
    ) from last_error
