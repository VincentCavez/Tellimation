"""Animation execution and voice/TTS handling.

Extracted from app.py to isolate the animation pipeline (generation,
temp sprites, efficacy tracking) and voice (TTS) concerns.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Protocol

from google import genai
from google.genai import types

from src.interaction.tellimation import generate_tellimation
from src.models.scene import SceneManifest
from src.models.student_profile import StudentProfile
from src.narration.voice_guidance import text_to_speech

logger = logging.getLogger(__name__)

_ORAL_GUIDANCE_MODEL = "gemini-3-flash-preview"
_ORAL_GUIDANCE_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Minimal WS protocol (avoids importing _WebSocketAdapter from app.py)
# ---------------------------------------------------------------------------

class WSProtocol(Protocol):
    async def send_json(self, data: Dict[str, Any]) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...


# ---------------------------------------------------------------------------
# Oral guidance (LLM fallback when animation fails)
# ---------------------------------------------------------------------------

async def generate_oral_guidance(
    api_key: str,
    target_id: str,
    misl_element: str,
    manifest: SceneManifest,
    student_profile: StudentProfile,
) -> str:
    """Generate a spoken English guidance question via LLM.

    Used as the last-resort fallback when animation generation fails entirely.
    """
    root_id = target_id.split(".")[0] if "." in target_id else target_id
    entity = manifest.get_entity(root_id)
    if entity:
        props = ", ".join(f"{k}={v}" for k, v in entity.properties.items())
        entity_desc = f"{entity.type} ({props})" if props else entity.type
    else:
        entity_desc = root_id

    prompt = (
        f"Generate a single short spoken English question (max 15 words) "
        f"to guide a child (age {student_profile.age}) to describe a "
        f"{entity_desc} focusing on: {misl_element}. "
        f"Just the question, nothing else."
    )

    client = genai.Client(api_key=api_key)
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=_ORAL_GUIDANCE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=256),
                temperature=0.7,
            ),
        ),
        timeout=_ORAL_GUIDANCE_TIMEOUT,
    )
    text = response.text.strip().strip('"')
    logger.info("[animation] Oral fallback generated: %s", text)
    return text


# ---------------------------------------------------------------------------
# Animation execution
# ---------------------------------------------------------------------------

async def execute_animation(
    session: Any,  # SessionState — avoids circular import
    ws: WSProtocol,
    target_id: str,
    misl_element: str = "character",
    problematic_segment: Optional[str] = None,
) -> None:
    """Generate and send a tellimation animation for a target entity."""
    try:
        code, duration_ms, temp_sprites, animation_id, text_overlays = (
            await generate_tellimation(
                api_key=session.api_key,
                sprite_code=(
                    session.current_scene.get("sprite_code", {})
                    if session.current_scene else {}
                ),
                manifest=session.current_manifest,
                student_profile=session.student_profile,
                target_id=target_id,
                misl_element=misl_element,
                neg=session.current_neg,
                problematic_segment=problematic_segment,
            )
        )

        # Send temp sprites BEFORE animation
        if temp_sprites:
            for sprite_id, sprite_data in temp_sprites.items():
                await ws.send_json({
                    "type": "add_temp_sprite",
                    "id": sprite_id,
                    "sprite": sprite_data,
                })

        # Send animation with full context
        msg: Dict[str, Any] = {
            "type": "animation",
            "animation_id": animation_id,
            "target_id": target_id,
            "misl_element": misl_element,
            "code": code,
            "duration_ms": duration_ms,
        }
        if text_overlays:
            msg["text_overlays"] = text_overlays
        await ws.send_json(msg)

        # Schedule temp sprite removal after animation ends
        if temp_sprites:
            sprite_ids = list(temp_sprites.keys())

            async def _remove_temp_sprites_after_delay() -> None:
                await asyncio.sleep(duration_ms / 1000.0)
                for sid in sprite_ids:
                    await ws.send_json({
                        "type": "remove_temp_sprite",
                        "id": sid,
                    })

            asyncio.ensure_future(_remove_temp_sprites_after_delay())

        # Log last animation for efficacy tracking
        session.last_animation = {
            "target_id": target_id,
            "animation_type": animation_id,
            "misl_element": misl_element,
            "timestamp": time.time(),
        }

        # Append pending efficacy entry (discrepancy module updates led_to_correction)
        session.student_profile.animation_efficacy.append({
            "target_id": target_id,
            "animation_type": animation_id,
            "misl_element": misl_element,
            "led_to_correction": False,
            "escalation_level": 0,
            "timestamp": time.time(),
            "scene_id": (
                session.current_scene.get("scene_id", "")
                if session.current_scene else ""
            ),
        })

        session.animations_played_this_scene.append(target_id)
        session.conversation_history.append({
            "role": "system",
            "text": f"Animation '{animation_id}' on {target_id} ({misl_element})",
            "action": "animate",
        })

    except Exception:
        logger.warning("[animation] Animation failed for %s, falling back to oral guidance",
                       target_id)
        try:
            guidance = await generate_oral_guidance(
                session.api_key, target_id, misl_element,
                session.current_manifest, session.student_profile,
            )
            session.conversation_history.append({
                "role": "system",
                "text": guidance,
                "action": "oral_guidance",
            })
            await ws.send_json({
                "type": "assessment_result",
                "action": "oral_guidance",
                "guidance_text": guidance,
            })
            asyncio.ensure_future(send_voice(session, ws, guidance))
        except Exception:
            logger.exception("[animation] Oral guidance fallback also failed for %s",
                             target_id)


# ---------------------------------------------------------------------------
# Voice (TTS)
# ---------------------------------------------------------------------------

async def send_voice(
    session: Any,  # SessionState — avoids circular import
    ws: WSProtocol,
    text: str,
) -> None:
    """Generate TTS audio and send to client (serialized via voice lock)."""
    async with session._voice_lock:
        try:
            audio_bytes = await text_to_speech(
                api_key=session.api_key,
                text=text,
            )
            await ws.send_json({"type": "voice_start", "text": text})
            await ws.send_bytes(audio_bytes)
            await ws.send_json({"type": "voice_end"})
        except Exception:
            logger.warning("TTS failed for: %s", text[:50])
