"""Transcription + discrepancy detection via Gemini 3 Flash (multimodal)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from google import genai
from google.genai import types

from src.models.neg import NEG
from src.models.student_profile import Discrepancy, StudentProfile

from src.generation.prompts.transcription_prompt import (
    TRANSCRIPTION_SYSTEM_PROMPT,
    TRANSCRIPTION_USER_PROMPT,
)
from src.generation.utils import extract_json as _extract_json

MODEL_ID = "gemini-3-flash-preview"


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ProfileUpdates(BaseModel):
    errors_this_scene: Dict[str, int] = Field(default_factory=dict)
    patterns: str = ""


class TranscriptionResult(BaseModel):
    transcription: str = ""
    discrepancies: List[Discrepancy] = Field(default_factory=list)
    scene_progress: float = 0.0
    satisfied_targets: List[str] = Field(default_factory=list)
    updated_history: List[str] = Field(default_factory=list)
    profile_updates: ProfileUpdates = Field(default_factory=ProfileUpdates)
    voice_guidance: str = ""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _parse_discrepancies(raw: List[Dict[str, Any]]) -> List[Discrepancy]:
    """Parse discrepancy dicts into Discrepancy models."""
    result: List[Discrepancy] = []
    for item in raw:
        result.append(Discrepancy(
            type=item.get("type", "OMISSION"),
            entity_id=item.get("entity_id", ""),
            sub_entity=item.get("sub_entity", ""),
            details=item.get("details", ""),
            severity=float(item.get("severity", 0.5)),
        ))
    return result


def _parse_profile_updates(raw: Any) -> ProfileUpdates:
    """Parse profile_updates from the LLM response."""
    if not isinstance(raw, dict):
        return ProfileUpdates()
    return ProfileUpdates(
        errors_this_scene=raw.get("errors_this_scene", {}),
        patterns=raw.get("patterns", ""),
    )


def _validate_transcription_response(data: Dict[str, Any]) -> TranscriptionResult:
    """Validate and build a TranscriptionResult from the LLM response."""
    raw_discrepancies = data.get("discrepancies", [])
    if not isinstance(raw_discrepancies, list):
        raw_discrepancies = []

    satisfied = data.get("satisfied_targets", [])
    if not isinstance(satisfied, list):
        satisfied = []

    updated_history = data.get("updated_history", [])
    if not isinstance(updated_history, list):
        updated_history = []

    progress = data.get("scene_progress", 0.0)
    if not isinstance(progress, (int, float)):
        progress = 0.0
    progress = max(0.0, min(1.0, float(progress)))

    voice_guidance = data.get("voice_guidance", "")
    if not isinstance(voice_guidance, str):
        voice_guidance = ""

    return TranscriptionResult(
        transcription=data.get("transcription", ""),
        discrepancies=_parse_discrepancies(raw_discrepancies),
        scene_progress=progress,
        satisfied_targets=satisfied,
        updated_history=updated_history,
        profile_updates=_parse_profile_updates(data.get("profile_updates")),
        voice_guidance=voice_guidance,
    )


def _build_user_prompt(
    neg: NEG,
    narration_history: List[str],
    student_profile: Optional[StudentProfile],
    narrative_text: str = "",
) -> str:
    """Build the text portion of the user prompt."""
    neg_json = json.dumps(neg.model_dump(), indent=2)

    narrative_str = narrative_text if narrative_text else "(no narrative context yet)"

    if narration_history:
        history_str = "\n".join(
            f"{i+1}. \"{utt}\"" for i, utt in enumerate(narration_history)
        )
    else:
        history_str = "(no previous utterances — this is the first)"

    profile_str = "(no profile yet — first interaction)"
    if student_profile and student_profile.total_utterances > 0:
        profile_str = student_profile.to_prompt_context()

    return TRANSCRIPTION_USER_PROMPT.format(
        narrative_text=narrative_str,
        neg_json=neg_json,
        narration_history=history_str,
        student_profile=profile_str,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def transcribe_and_detect(
    api_key: str,
    audio_bytes: bytes,
    neg: NEG,
    narration_history: Optional[List[str]] = None,
    student_profile: Optional[StudentProfile] = None,
    narrative_text: str = "",
) -> TranscriptionResult:
    """Transcribe child audio and detect discrepancies against the NEG.

    Uses Gemini 3 Flash in multimodal mode (audio + text) with
    thinking_level: low for minimal latency.

    Args:
        api_key: Gemini API key.
        audio_bytes: Raw audio bytes (WAV/WebM/OGG).
        neg: The current scene's Narrative Expectation Graph.
        narration_history: Previous utterance transcriptions (ordered).
        student_profile: Child's error profile for context priming.
        narrative_text: The scene's story narrative for voice guidance context.

    Returns:
        TranscriptionResult with transcription, discrepancies (filtered
        by the NEG), scene_progress, voice_guidance, and more.
    """
    if narration_history is None:
        narration_history = []

    # Build prompts
    user_prompt = _build_user_prompt(
        neg, narration_history, student_profile, narrative_text
    )

    # Multimodal content: audio part + text part
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/webm")
    text_part = types.Part.from_text(text=user_prompt)

    # Call Gemini (thinking_level: low for real-time latency)
    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=[audio_part, text_part],
        config=types.GenerateContentConfig(
            system_instruction=TRANSCRIPTION_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(thinking_budget=256),
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )

    # Parse and validate
    raw_text = response.text
    data = _extract_json(raw_text)
    result = _validate_transcription_response(data)

    return result
