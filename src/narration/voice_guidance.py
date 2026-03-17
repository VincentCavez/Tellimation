"""Voice guidance via Gemini 2.5 Flash TTS (Achernar voice).

Provides:
  - text_to_speech: Convert text to PCM audio (24 kHz, 16-bit mono).
  - generate_scene_intro: Introductory sentence when a scene loads.
  - generate_correction_text: Child-friendly verbal correction after 3 failed animations.
  - generate_branch_narration: Narrate the 3 branch options after scene completion.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------

TTS_MODEL_ID = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Achernar"

TEXT_MODEL_ID = "gemini-3-flash-preview"

# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------


async def text_to_speech(api_key: str, text: str) -> bytes:
    """Convert text to raw PCM audio via Gemini 2.5 Flash TTS.

    Args:
        api_key: Gemini API key.
        text: The text to speak.  May include stage directions as a prefix
              (e.g. ``"Say cheerfully: Hello!"``).

    Returns:
        Raw PCM audio bytes (24 kHz, 16-bit signed, mono).

    Raises:
        RuntimeError: If the TTS call fails or returns no audio.
    """
    client = genai.Client(api_key=api_key)

    response = await client.aio.models.generate_content(
        model=TTS_MODEL_ID,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=TTS_VOICE,
                    )
                )
            ),
        ),
    )

    # Extract audio data from the response
    if response.candidates and response.candidates[0].content:
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                logger.info(
                    "[tts] Generated %d bytes of PCM audio",
                    len(part.inline_data.data),
                )
                return part.inline_data.data

    raise RuntimeError("TTS call returned no audio data")


# ---------------------------------------------------------------------------
# Scene introduction
# ---------------------------------------------------------------------------

SCENE_INTRO_SYSTEM_PROMPT = """\
You generate a short introductory sentence for a children's storytelling \
app (ages 7-11). The child is about to narrate a pixel-art scene.

Generate ONE sentence (max 20 words) that:
1. Introduces the main character(s) and setting
2. Sets up what's happening in the scene
3. Is warm and inviting, like a storyteller beginning a tale

Examples:
- "Let's tell the story of a brave rabbit exploring a magical forest!"
- "Look! A little fox is searching for her friend near the river!"
"""


async def generate_scene_intro(
    api_key: str,
    narrative_text: str,
    manifest: Dict[str, Any],
) -> str:
    """Generate an introductory sentence for a scene.

    Returns:
        A short intro sentence suitable for TTS, or empty string on failure.
    """
    entities = manifest.get("entities", [])
    entity_list = ", ".join(
        f"{e.get('type', '?')} ({e['id']})" for e in entities
    )

    user_prompt = (
        f"Scene narrative: {narrative_text}\n"
        f"Entities: {entity_list}\n\n"
        f"Generate ONE warm introductory sentence (max 20 words)."
    )

    client = genai.Client(api_key=api_key)
    try:
        response = await client.aio.models.generate_content(
            model=TEXT_MODEL_ID,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SCENE_INTRO_SYSTEM_PROMPT,
                thinking_config=types.ThinkingConfig(thinking_budget=256),
                temperature=1.0,
            ),
        )
        text = response.text.strip().strip('"').strip("'")
        logger.info("[voice-intro] Generated: %s", text)
        return text
    except Exception:
        logger.exception("[voice-intro] Failed to generate scene intro")
        return ""


# ---------------------------------------------------------------------------
# Correction text generation
# ---------------------------------------------------------------------------

CORRECTION_SYSTEM_PROMPT = """\
You generate short, encouraging voice prompts for a children's storytelling \
app (ages 7-11). The child made an error while narrating a scene and hasn't \
corrected it after seeing an animation hint.

Generate ONE sentence (max 20 words) that:
1. Gently points out the correct description
2. Is encouraging, not critical
3. Uses simple, age-appropriate language
4. References the story context naturally

Examples:
- "Look closely — the cat is actually orange! Can you describe the orange cat?"
- "The rabbit is hopping, not walking. Try saying what the rabbit is doing!"
- "There are actually three birds on the branch. Can you count them?"
"""


async def generate_correction_text(
    api_key: str,
    entity_id: str,
    error_type: str,
    discrepancy_details: str,
    scene_manifest: Dict[str, Any],
    narrative_text: str,
) -> str:
    """Generate a child-friendly verbal correction for an uncorrected error.

    Uses Gemini 3 Flash with low thinking budget for speed.

    Returns:
        A short correction sentence suitable for TTS, or empty string on
        failure.
    """
    # Build a concise user prompt
    entities_summary = []
    for ent in scene_manifest.get("entities", []):
        props = ent.get("properties", {})
        entities_summary.append(
            f"- {ent['id']} ({ent.get('type', '?')}): "
            f"color={props.get('color', '?')}, size={props.get('size', '?')}"
        )
    entities_str = "\n".join(entities_summary) if entities_summary else "(none)"

    user_prompt = (
        f"Story context: {narrative_text}\n\n"
        f"Entities in scene:\n{entities_str}\n\n"
        f"Error: entity={entity_id}, type={error_type}\n"
        f"Details: {discrepancy_details}\n\n"
        f"Generate ONE encouraging correction sentence (max 20 words)."
    )

    client = genai.Client(api_key=api_key)
    try:
        response = await client.aio.models.generate_content(
            model=TEXT_MODEL_ID,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=CORRECTION_SYSTEM_PROMPT,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=256,
                ),
                temperature=1.0,
            ),
        )
        text = response.text.strip().strip('"').strip("'")
        logger.info("[voice-correction] Generated: %s", text)
        return text
    except Exception:
        logger.exception("[voice-correction] Failed to generate correction text")
        return ""


# ---------------------------------------------------------------------------
# Branch narration
# ---------------------------------------------------------------------------

BRANCH_NARRATION_SYSTEM_PROMPT = """\
You narrate story branch options for a children's storytelling app (ages 7-11).
Given 2-3 possible next scenes, create a brief, exciting narration (2-3 sentences \
total) introducing ALL the choices. Be enthusiastic and child-friendly.

Example: "Amazing storytelling! Now, what happens next? You could follow the \
rabbit into a dark cave, or watch the fox chase a butterfly across the meadow, \
or help the owl find its lost acorn!"
"""


async def generate_branch_narration(
    api_key: str,
    branches: List[Dict[str, Any]],
) -> str:
    """Generate a narration introducing the branch options.

    Returns:
        A narration paragraph suitable for TTS, or empty string on failure.
    """
    summaries = []
    for i, b in enumerate(branches, 1):
        summary = b.get("branch_summary") or b.get("narrative_text", "")
        summaries.append(f"Option {i}: {summary}")
    summaries_str = "\n".join(summaries)

    user_prompt = (
        f"The child just finished narrating a scene. Here are the branch options:\n\n"
        f"{summaries_str}\n\n"
        f"Generate an exciting 2-3 sentence narration introducing these choices."
    )

    client = genai.Client(api_key=api_key)
    try:
        response = await client.aio.models.generate_content(
            model=TEXT_MODEL_ID,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=BRANCH_NARRATION_SYSTEM_PROMPT,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=256,
                ),
                temperature=1.0,
            ),
        )
        text = response.text.strip()
        logger.info("[voice-branches] Generated: %s", text)
        return text
    except Exception:
        logger.exception("[voice-branches] Failed to generate branch narration")
        return ""
