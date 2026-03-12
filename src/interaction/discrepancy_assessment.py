"""Discrepancy assessment module.

Single Gemini call per utterance: compares the child's speech against the
scene manifest + MISL taxonomy to detect factual errors and identify MISL
scaffolding opportunities.

Model: Gemini 3 Flash (gemini-3-flash-preview)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, List, Optional

from google import genai
from google.genai import types

from config.misl import MACROSTRUCTURE, MICROSTRUCTURE
from src.models.assessment import AssessmentResponse, FactualError, MISLOpportunity
from src.models.scene import SceneManifest
from src.models.student_profile import MISLDifficultyProfile
from src.generation.prompts.assessment_prompt import (
    ASSESSMENT_SYSTEM_PROMPT,
    ASSESSMENT_USER_PROMPT_TEMPLATE,
)
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
ASSESSMENT_TIMEOUT = 30
MAX_RETRIES = 2


def _build_misl_taxonomy() -> str:
    """Build a human-readable MISL taxonomy text for the prompt."""
    lines = ["## Macrostructure (7 elements, scores 0-3)\n"]
    for key, info in MACROSTRUCTURE.items():
        lines.append(f"**{info['label']}** (`{key}`)")
        for score, desc in info["scores"].items():
            lines.append(f"  {score}: {desc}")
        lines.append("")

    lines.append("## Microstructure (8 elements, scores 0-3)\n")
    for key, info in MICROSTRUCTURE.items():
        lines.append(f"**{info['label']}** (`{key}`)")
        for score, desc in info["scores"].items():
            lines.append(f"  {score}: {desc}")
        lines.append("")

    return "\n".join(lines)


async def assess_utterance(
    api_key: str,
    manifest: SceneManifest,
    utterance_text: str,
    story_so_far: List[str],
    misl_already_suggested: List[str],
    misl_difficulty_profile: MISLDifficultyProfile,
    character_names: Optional[Dict[str, str]] = None,
) -> AssessmentResponse:
    """Assess a child's utterance against the scene manifest and MISL taxonomy.

    Single Gemini call that checks for factual errors and identifies MISL
    opportunities for scaffolding.

    Args:
        api_key: Gemini API key.
        manifest: The current scene's manifest.
        utterance_text: The transcribed child utterance.
        story_so_far: List of accepted utterance texts in this scene.
        misl_already_suggested: MISL dimensions already prompted this scene.
        misl_difficulty_profile: Persistent MISL difficulty data.

    Returns:
        AssessmentResponse with factual errors and/or MISL opportunities.
    """
    client = genai.Client(api_key=api_key)

    # Build user prompt
    manifest_json = json.dumps(manifest.model_dump(), indent=2)
    misl_taxonomy = _build_misl_taxonomy()

    if story_so_far:
        story_text = "\n".join(
            f'{i+1}. "{utt}"' for i, utt in enumerate(story_so_far)
        )
    else:
        story_text = "(No accepted utterances yet — this is the first.)"

    if misl_already_suggested:
        suggested_text = ", ".join(misl_already_suggested)
    else:
        suggested_text = "(None yet.)"

    difficulty_text = misl_difficulty_profile.to_prompt_context()

    if character_names:
        names_lines = []
        for eid, name in character_names.items():
            ent = manifest.get_entity(eid)
            etype = ent.type if ent else eid
            names_lines.append(f"- {eid} is named \"{name}\" (type: {etype})")
        names_text = "\n".join(names_lines)
    else:
        names_text = "(No character names given yet.)"

    user_prompt = ASSESSMENT_USER_PROMPT_TEMPLATE.format(
        manifest_json=manifest_json,
        misl_taxonomy=misl_taxonomy,
        utterance_text=utterance_text,
        story_so_far=story_text,
        misl_already_suggested=suggested_text,
        misl_difficulty_profile=difficulty_text,
        character_names=names_text,
    )

    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=MODEL_ID,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=ASSESSMENT_SYSTEM_PROMPT,
                        thinking_config=types.ThinkingConfig(thinking_budget=512),
                        temperature=0.7,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=ASSESSMENT_TIMEOUT,
            )

            data = _extract_json(_get_response_text(response))

            # Parse factual errors
            raw_errors = data.get("factual_errors", [])
            factual_errors = []
            if isinstance(raw_errors, list):
                for item in raw_errors:
                    if isinstance(item, dict):
                        factual_errors.append(FactualError(
                            utterance_fragment=item.get("utterance_fragment", ""),
                            manifest_ref=item.get("manifest_ref", ""),
                            explanation=item.get("explanation", ""),
                        ))

            # Parse MISL opportunities
            raw_opps = data.get("misl_opportunities", [])
            misl_opportunities = []
            if isinstance(raw_opps, list):
                for item in raw_opps:
                    if isinstance(item, dict):
                        misl_opportunities.append(MISLOpportunity(
                            dimension=item.get("dimension", ""),
                            manifest_elements=item.get("manifest_elements", []),
                            suggestion=item.get("suggestion", ""),
                        ))

            acceptable = data.get("utterance_is_acceptable", True)
            if not isinstance(acceptable, bool):
                acceptable = True

            result = AssessmentResponse(
                factual_errors=factual_errors,
                misl_opportunities=misl_opportunities,
                utterance_is_acceptable=acceptable,
            )

            logger.info(
                "[assessment] errors=%d opportunities=%d acceptable=%s",
                len(factual_errors),
                len(misl_opportunities),
                acceptable,
            )

            return result

        except asyncio.TimeoutError:
            logger.warning(
                "[assessment] Attempt %d/%d timed out after %ds",
                attempt, MAX_RETRIES, ASSESSMENT_TIMEOUT,
            )
            last_exc = asyncio.TimeoutError(
                f"Assessment timed out after {ASSESSMENT_TIMEOUT}s"
            )
        except Exception as exc:
            logger.warning(
                "[assessment] Attempt %d/%d failed (%s): %s",
                attempt, MAX_RETRIES, type(exc).__name__, exc or "no details",
            )
            last_exc = exc

    # All retries exhausted — return a safe fallback (accept the utterance)
    logger.error(
        "[assessment] All %d attempts failed, accepting utterance by default",
        MAX_RETRIES,
    )
    return AssessmentResponse(utterance_is_acceptable=True)
