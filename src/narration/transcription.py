"""Audio transcription via Gemini 3 Flash (multimodal)."""

from __future__ import annotations

from typing import List, Optional

from google import genai
from google.genai import types

from src.generation.prompts.transcription_prompt import (
    TRANSCRIPTION_SYSTEM_PROMPT,
    TRANSCRIPTION_USER_PROMPT,
)
from src.generation.utils import extract_json as _extract_json

MODEL_ID = "gemini-3-flash-preview"


def _build_user_prompt(
    narration_history: List[str],
    narrative_text: str = "",
) -> str:
    """Build the text portion of the user prompt."""
    narrative_str = narrative_text if narrative_text else "(no narrative context yet)"

    if narration_history:
        history_str = "\n".join(
            f'{i+1}. "{utt}"' for i, utt in enumerate(narration_history)
        )
    else:
        history_str = "(no previous utterances — this is the first)"

    return TRANSCRIPTION_USER_PROMPT.format(
        narrative_text=narrative_str,
        narration_history=history_str,
    )


async def transcribe_audio(
    api_key: str,
    audio_bytes: bytes,
    narration_history: Optional[List[str]] = None,
    narrative_text: str = "",
) -> str:
    """Transcribe child audio to text.

    Uses Gemini 3 Flash in multimodal mode (audio + text) with
    low thinking budget for minimal latency.

    Args:
        api_key: Gemini API key.
        audio_bytes: Raw audio bytes (WAV/WebM/OGG).
        narration_history: Previous utterance transcriptions (ordered).
        narrative_text: The scene's story narrative for context.

    Returns:
        The transcription string.
    """
    if narration_history is None:
        narration_history = []

    user_prompt = _build_user_prompt(narration_history, narrative_text)

    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/webm")
    text_part = types.Part.from_text(text=user_prompt)

    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=[audio_part, text_part],
        config=types.GenerateContentConfig(
            system_instruction=TRANSCRIPTION_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(thinking_budget=256),
            temperature=1.0,
            response_mime_type="application/json",
        ),
    )

    raw_text = response.text
    data = _extract_json(raw_text)
    return data.get("transcription", "")
