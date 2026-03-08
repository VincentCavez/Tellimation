"""Discrepancy assessment module — the conversational brain of Tellimations.

Handles ALL oral interaction with the child: when to speak, what to say,
and how to react. Each call to assess_and_respond() makes a single LLM
decision about the next action.

Model: Gemini 3 Flash (gemini-3-flash-preview)

Escalation protocol (encoded in the prompt, not hardcoded):
  Level 0: Open-ended invitation ("What do you see?")
  Level 1: Animate the highest-priority unsatisfied target
  Level 2: Guided question ("What does the fox look like?")
  Level 3: Explicit model ("Look, it's an orange fox!")
  Level 4: Move on to next target
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from src.models.assessment import AssessmentDecision
from src.models.neg import NEG
from src.models.student_profile import StudentProfile
from src.generation.prompts.assessment_prompt import (
    ASSESSMENT_SYSTEM_PROMPT,
    ASSESSMENT_USER_PROMPT_TEMPLATE,
)
from src.generation.scene_neg_generator import _build_misl_rubric
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
ASSESSMENT_TIMEOUT = 30   # latency-critical, must respond fast
MAX_RETRIES = 2


def _format_conversation_history(history: List[Dict[str, Any]]) -> str:
    """Format conversation history for the prompt.

    Each entry is a dict with:
      - role: "child" or "system"
      - text: transcription or guidance text
      - action: (optional) what action was taken after this entry
    """
    if not history:
        return "(No conversation yet — this is the start of the scene.)"

    lines = []
    for i, entry in enumerate(history, 1):
        role = entry.get("role", "unknown")
        text = entry.get("text", "")
        action = entry.get("action", "")

        if role == "child":
            lines.append(f"[{i}] CHILD: \"{text}\"")
        elif role == "system":
            lines.append(f"[{i}] SYSTEM ({action}): \"{text}\"")
        else:
            lines.append(f"[{i}] {role}: \"{text}\"")

    return "\n".join(lines)


def _format_animations_played(animations: List[str]) -> str:
    """Format list of animated target IDs for the prompt."""
    if not animations:
        return "(No animations played yet in this scene.)"
    return "\n".join(f"- {tid}" for tid in animations)


async def assess_and_respond(
    api_key: str,
    student_profile: StudentProfile,
    neg: NEG,
    conversation_history: List[Dict[str, Any]],
    animations_played: List[str],
) -> AssessmentDecision:
    """Assess the current interaction state and decide the next action.

    This is the per-utterance decision function. It examines the full
    conversation history, the NEG targets, and the student profile to
    decide whether to animate, speak, advance, or wait.

    After the decision, it also updates the student_profile with
    animation efficacy data (if the LLM reports corrections).

    Args:
        api_key: Gemini API key.
        student_profile: Child's cumulative error profile.
        neg: Narrative Expectation Graph for the current scene.
        conversation_history: List of conversation entries in this scene.
            Each entry: {"role": "child"|"system", "text": str, "action": str}
        animations_played: List of target_ids already animated in this scene.

    Returns:
        AssessmentDecision with the chosen action.
    """
    client = genai.Client(api_key=api_key)

    # Build user prompt
    neg_json = json.dumps(neg.model_dump(), indent=2)
    history_text = _format_conversation_history(conversation_history)
    animations_text = _format_animations_played(animations_played)
    profile_text = student_profile.to_prompt_context()

    user_prompt = ASSESSMENT_USER_PROMPT_TEMPLATE.format(
        misl_rubric=_build_misl_rubric(),
        neg_json=neg_json,
        conversation_history=history_text,
        animations_played=animations_text,
        student_profile=profile_text,
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

            # Extract the decision
            decision = AssessmentDecision(
                action=data.get("action", "wait"),
                target_id=data.get("target_id"),
                misl_element=data.get("misl_element"),
                guidance_text=data.get("guidance_text"),
                reasoning=data.get("reasoning", ""),
            )

            # Record MISL scores from LLM evaluation
            misl_scores = data.get("misl_scores", {})
            _update_misl_scores(student_profile, misl_scores)

            # Log animation efficacy from LLM response
            efficacy_list = data.get("animation_efficacy", [])
            _update_animation_efficacy(student_profile, efficacy_list)

            logger.info(
                "[assessment] action=%s target=%s misl=%s reasoning=%s",
                decision.action,
                decision.target_id,
                decision.misl_element,
                decision.reasoning[:80] if decision.reasoning else "",
            )

            return decision

        except asyncio.TimeoutError:
            logger.warning("[assessment] Attempt %d/%d timed out after %ds",
                           attempt, MAX_RETRIES, ASSESSMENT_TIMEOUT)
            last_exc = asyncio.TimeoutError(
                f"Assessment timed out after {ASSESSMENT_TIMEOUT}s")
        except Exception as exc:
            logger.warning("[assessment] Attempt %d/%d failed (%s): %s",
                           attempt, MAX_RETRIES,
                           type(exc).__name__, exc or "no details")
            last_exc = exc

    # All retries exhausted — return a safe fallback
    logger.error("[assessment] All %d attempts failed, returning wait",
                 MAX_RETRIES)
    return AssessmentDecision(
        action="wait",
        reasoning=f"LLM call failed after {MAX_RETRIES} attempts: {last_exc}",
    )


def _update_misl_scores(
    profile: StudentProfile,
    scores: Dict[str, Any],
) -> None:
    """Append MISL scores from LLM evaluation to the student profile."""
    for element, score in scores.items():
        if not isinstance(score, int) or score < 0 or score > 3:
            continue
        if element not in profile.misl_scores:
            profile.misl_scores[element] = []
        profile.misl_scores[element].append(score)
    if scores:
        logger.info("[assessment] MISL scores recorded: %s", scores)


def _update_animation_efficacy(
    profile: StudentProfile,
    efficacy_list: List[Dict[str, Any]],
) -> None:
    """Update student profile with animation efficacy data from LLM response.

    Finds pending entries in profile.animation_efficacy (led_to_correction=False)
    and updates them based on the LLM's assessment of whether the child corrected.
    """
    for entry in efficacy_list:
        target_id = entry.get("target_id", "")
        led_to_correction = entry.get("led_to_correction", False)

        if not target_id:
            continue

        if led_to_correction:
            # Find the most recent pending efficacy entry for this target
            for eff in reversed(profile.animation_efficacy):
                if (
                    eff.get("target_id") == target_id
                    and not eff.get("led_to_correction", False)
                ):
                    eff["led_to_correction"] = True
                    break
            profile.corrections_after_animation += 1
            logger.info("[assessment] Animation on %s led to correction", target_id)
        else:
            logger.info("[assessment] Animation on %s did NOT lead to correction",
                        target_id)
