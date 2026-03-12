"""Deterministic decision logic after Gemini assessment.

Pure Python, no LLM calls. Processes the AssessmentResponse and decides
what action to take: correct factual errors, provide MISL guidance, or
advance to the next scene.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from src.models.assessment import (
    AssessmentResponse,
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

    Args:
        utterance_text: The child's transcribed utterance.
        response: The Gemini assessment response.
        scene_log: The current scene's log (mutated in place).
        misl_difficulty_profile: Persistent difficulty data (mutated on suggestion).
        audio_path: Path to the audio file (for logging).

    Returns:
        Tuple of (action, action_data):
        - "correct", {"factual_errors": [...]} — factual errors need correction
        - "accept_and_guide", {"misl_opportunities": [...]} — accepted, with MISL guidance
        - "accept_and_advance", None — accepted, scene is done
    """
    if response.factual_errors:
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
        }

    # Utterance accepted — add to story
    scene_log.story.append(SceneStoryEntry(
        utterance_text=utterance_text,
        audio_path=audio_path,
    ))

    has_opportunities = bool(response.misl_opportunities)
    under_limit = scene_log.misl_opportunities_given < MAX_MISL_OPPORTUNITIES_PER_SCENE

    if has_opportunities and under_limit:
        scene_log.misl_opportunities_given += 1
        scene_log.assessments.append(SceneAssessmentEntry(
            timestamp=time.time(),
            utterance_text=utterance_text,
            audio_path=audio_path,
            gemini_response=response,
            accepted=True,
            misl_guidance_triggered=True,
        ))
        # Update difficulty profile
        for opp in response.misl_opportunities:
            misl_difficulty_profile.record_suggestion(opp.dimension)
        return "accept_and_guide", {
            "misl_opportunities": [o.model_dump() for o in response.misl_opportunities],
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
