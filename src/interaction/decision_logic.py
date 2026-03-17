"""Deterministic decision logic after Gemini assessment.

Pure Python, no LLM calls. Processes the AssessmentResponse and decides
what action to take: correct factual errors, provide MISL guidance, or
advance to the next scene.

Now works with the unified discrepancies list from the two-pass assessment.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from src.models.assessment import (
    AssessmentResponse,
    Discrepancy,
    SceneAssessmentEntry,
    SceneLog,
    SceneStoryEntry,
)
from src.models.student_profile import MISLDifficultyProfile

MAX_MISL_OPPORTUNITIES_PER_SCENE = 3


def process_assessment(
    utterance_text: str,
    response: AssessmentResponse,
    scene_log: SceneLog,
    misl_difficulty_profile: MISLDifficultyProfile,
    audio_path: str = "",
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Process an assessment response and decide what to do.

    Uses the unified discrepancies list when available, falling back
    to the legacy factual_errors/misl_opportunities fields.

    Args:
        utterance_text: The child's transcribed utterance.
        response: The Gemini assessment response.
        scene_log: The current scene's log (mutated in place).
        misl_difficulty_profile: Persistent difficulty data (mutated on suggestion).
        audio_path: Path to the audio file (for logging).

    Returns:
        Tuple of (action, action_data):
        - "correct", {"factual_errors": [...], "discrepancies": [...]}
        - "accept_and_guide", {"misl_opportunities": [...], "discrepancies": [...]}
        - "accept_and_advance", None
    """
    # Extract corrections and suggestions from unified discrepancies
    corrections = [d for d in response.discrepancies if d.pass_type == "correction"]
    suggestions = [d for d in response.discrepancies if d.pass_type == "suggestion"]

    # Fall back to legacy fields if discrepancies list is empty
    has_corrections = bool(corrections) or bool(response.factual_errors)
    has_suggestions = bool(suggestions) or bool(response.misl_opportunities)

    if has_corrections:
        # Log as rejected
        scene_log.assessments.append(SceneAssessmentEntry(
            timestamp=time.time(),
            utterance_text=utterance_text,
            audio_path=audio_path,
            gemini_response=response,
            accepted=False,
            correction_triggered=True,
        ))
        return "correct", {
            "factual_errors": [e.model_dump() for e in response.factual_errors],
            "discrepancies": [d.model_dump() for d in response.discrepancies],
        }

    # Utterance accepted — add to story
    scene_log.story.append(SceneStoryEntry(
        utterance_text=utterance_text,
        audio_path=audio_path,
    ))

    under_limit = scene_log.misl_opportunities_given < MAX_MISL_OPPORTUNITIES_PER_SCENE

    if has_suggestions and under_limit:
        scene_log.misl_opportunities_given += 1
        scene_log.assessments.append(SceneAssessmentEntry(
            timestamp=time.time(),
            utterance_text=utterance_text,
            audio_path=audio_path,
            gemini_response=response,
            accepted=True,
            misl_guidance_triggered=True,
        ))
        # Update difficulty profile from legacy fields
        for opp in response.misl_opportunities:
            misl_difficulty_profile.record_suggestion(opp.dimension)
        # Also update from discrepancy MISL elements
        for d in suggestions:
            for misl_el in d.misl_elements:
                misl_difficulty_profile.record_suggestion(misl_el)
        return "accept_and_guide", {
            "misl_opportunities": [o.model_dump() for o in response.misl_opportunities],
            "discrepancies": [d.model_dump() for d in response.discrepancies],
        }

    # No errors, no opportunities (or max reached) — scene done
    scene_log.assessments.append(SceneAssessmentEntry(
        timestamp=time.time(),
        utterance_text=utterance_text,
        audio_path=audio_path,
        gemini_response=response,
        accepted=True,
    ))
    return "accept_and_advance", None


def get_accepted_utterances(scene_log: SceneLog) -> List[str]:
    """Return list of accepted utterance texts for the current scene."""
    return [entry.utterance_text for entry in scene_log.story]


def get_misl_dimensions_suggested(scene_log: SceneLog) -> List[str]:
    """Return list of MISL dimensions already suggested in this scene."""
    suggested: List[str] = []
    for assessment in scene_log.assessments:
        if assessment.misl_guidance_triggered:
            for opp in assessment.gemini_response.misl_opportunities:
                if opp.dimension not in suggested:
                    suggested.append(opp.dimension)
    return suggested
