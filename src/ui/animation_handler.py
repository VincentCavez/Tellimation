"""Animation execution and voice/TTS handling.

Extracted from app.py to isolate the animation pipeline (generation,
efficacy tracking) and voice (TTS) concerns.

The tellimation module now returns a 4-mode decision dict. This handler
translates the decision into WebSocket messages for the client.

Also provides execute_invocation_array() which iterates through a structured
InvocationArray and plays each animation with appropriate delays.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Protocol

from google import genai
from google.genai import types

from config.misl import ANIMATION_ID_TO_TEMPLATE
from src.interaction.tellimation import generate_invocation_array, _build_fallback
from src.models.assessment import Discrepancy
from src.models.invocation import AnimationInvocation, InvocationArray
from src.models.scene import SceneManifest
from src.models.student_profile import AnimationDecision, StudentProfile
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
    """Generate a brief, kind oral explanation of an error with correction.

    Used as escalation when animation alone didn't lead to self-correction,
    or as fallback when animation generation fails entirely.
    """
    root_id = target_id.split(".")[0] if "." in target_id else target_id
    entity = manifest.get_entity(root_id)
    if entity:
        props = ", ".join(f"{k}={v}" for k, v in entity.properties.items())
        entity_desc = f"{entity.type} ({props})" if props else entity.type
    else:
        entity_desc = root_id

    prompt = (
        f"A child (age {student_profile.age}) made an error describing a "
        f"{entity_desc}. The issue is about: {misl_element}. "
        f"Generate a single brief, kind spoken explanation (max 20 words) "
        f"that gently tells the child what was wrong and gives the correction. "
        f"Be warm and encouraging. "
        f"Example: 'Look, the rabbit is actually brown, not white! Can you try again?' "
        f"Just the spoken text, nothing else."
    )

    client = genai.Client(api_key=api_key)
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=_ORAL_GUIDANCE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=256),
                temperature=1.0,
            ),
        ),
        timeout=_ORAL_GUIDANCE_TIMEOUT,
    )
    text = response.text.strip().strip('"')
    logger.info("[animation] Oral fallback generated: %s", text)
    return text


# ---------------------------------------------------------------------------
# Send animation to client based on mode
# ---------------------------------------------------------------------------

async def _send_animation_message(
    ws: WSProtocol,
    decision: Dict[str, Any],
    target_id: str,
    misl_element: str,
) -> None:
    """Send animation message(s) to client based on the decision mode."""
    mode = decision["mode"]
    logger.info(
        "\033[95m[ANIMATION]\033[0m mode=%s  id=%s  target=%s  misl=%s  duration=%dms",
        mode,
        decision.get("animation_id", "?"),
        target_id,
        misl_element,
        decision.get("duration_ms", 0),
    )
    if mode == "sequence":
        for j, step in enumerate(decision.get("steps", [])):
            logger.info(
                "\033[95m[ANIMATION]   step %d:\033[0m id=%s  template=%s  duration=%dms",
                j, step.get("animation_id", "?"),
                step.get("template", "?"),
                step.get("duration_ms", 0),
            )
    elif mode == "custom_code":
        code_preview = (decision.get("code", "")[:120] + "...") if len(decision.get("code", "")) > 120 else decision.get("code", "")
        logger.info("\033[95m[ANIMATION]   code:\033[0m %s", code_preview)
    elif mode in ("use_default", "adjust_params"):
        logger.info(
            "\033[95m[ANIMATION]   template:\033[0m %s  params=%s",
            decision.get("template", "?"),
            decision.get("params", {}),
        )

    if mode in ("use_default", "adjust_params"):
        msg: Dict[str, Any] = {
            "type": "animation",
            "animation_id": decision["animation_id"],
            "target_id": target_id,
            "misl_element": misl_element,
            "template": decision["template"],
            "params": decision.get("params", {}),
            "duration_ms": decision["duration_ms"],
        }
        if decision.get("text_overlays"):
            msg["text_overlays"] = decision["text_overlays"]
        await ws.send_json(msg)

    elif mode == "sequence":
        # Send all steps in a single message — client handles sequencing + looping
        steps = decision.get("steps", [])
        msg = {
            "type": "animation",
            "animation_id": decision["animation_id"],
            "target_id": target_id,
            "misl_element": misl_element,
            "steps": steps,
            "duration_ms": decision["duration_ms"],
        }
        await ws.send_json(msg)

    elif mode == "custom_code":
        msg = {
            "type": "animation",
            "animation_id": decision["animation_id"],
            "target_id": target_id,
            "misl_element": misl_element,
            "code": decision["code"],
            "duration_ms": decision["duration_ms"],
        }
        if decision.get("template_name"):
            msg["template_name"] = decision["template_name"]
        if decision.get("text_overlays"):
            msg["text_overlays"] = decision["text_overlays"]
        await ws.send_json(msg)


# ---------------------------------------------------------------------------
# Animation execution
# ---------------------------------------------------------------------------

async def execute_animation(
    session: Any,  # SessionState — avoids circular import
    ws: WSProtocol,
    target_id: str,
    misl_element: str = "character",
    problematic_segment: Optional[str] = None,
    animation_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Generate and send a tellimation animation for a target entity.

    Returns the decision dict on success (for deferred voice tracking),
    or None on failure.
    """
    # Study control condition: skip animations
    if getattr(session, "study_animations_enabled", None) is False:
        logger.info("[study-control] Skipping animation for %s (control condition)", target_id)
        return None

    try:
        if animation_id:
            # Use the specific animation requested
            # Try full ID first (e.g. "A2_flip"), then short prefix (e.g. "A2")
            template = ANIMATION_ID_TO_TEMPLATE.get(animation_id)
            if not template:
                short_id = animation_id.split("_")[0]  # "A2_flip" → "A2"
                for key, tmpl in ANIMATION_ID_TO_TEMPLATE.items():
                    if key.startswith(short_id + "_"):
                        template = tmpl
                        break
            if not template:
                template = "spotlight"
            from src.interaction.tellimation import _DEFAULT_DURATIONS
            duration_ms = _DEFAULT_DURATIONS.get(template, 1500)
            # Load params from grammar JSON (includes tint, tintSaturation, etc.)
            from src.interaction.tellimation import load_animation_params
            study_log = getattr(session, 'study_log_entries', [])
            params = load_animation_params(animation_id, study_log)

            decision = {
                "mode": "use_default",
                "animation_id": animation_id,
                "template": template,
                "params": params,
                "duration_ms": duration_ms,
                "steps": [],
                "code": "",
                "text_overlays": [],
            }
        else:
            decision = _build_fallback(target_id, misl_element)

        # Inject entityPrefix into params for all template-based modes
        # "scene" target means no specific entity — use empty prefix
        entity_prefix = "" if target_id == "scene" else target_id
        if decision["mode"] in ("use_default", "adjust_params"):
            decision["params"]["entityPrefix"] = entity_prefix

        if decision["mode"] == "sequence":
            for step in decision.get("steps", []):
                step["params"]["entityPrefix"] = entity_prefix

        # Send animation to client
        await _send_animation_message(ws, decision, target_id, misl_element)

        # Log last animation for efficacy tracking
        session.last_animation = {
            "target_id": target_id,
            "animation_type": decision["animation_id"],
            "misl_element": misl_element,
            "timestamp": time.time(),
        }

        # Append pending efficacy entry (legacy)
        session.student_profile.animation_efficacy.append({
            "target_id": target_id,
            "animation_type": decision["animation_id"],
            "misl_element": misl_element,
            "led_to_correction": False,
            "escalation_level": 0,
            "timestamp": time.time(),
            "scene_id": (
                session.current_scene.get("scene_id", "")
                if session.current_scene else ""
            ),
        })

        # Log AnimationDecision for 4-mode tracking
        session.student_profile.animation_decisions.append(AnimationDecision(
            timestamp=time.time(),
            scene_id=(
                session.current_scene.get("scene_id", "")
                if session.current_scene else ""
            ),
            target_id=target_id,
            misl_element=misl_element,
            mode=decision["mode"],
            animation_id=decision["animation_id"],
            template=decision.get("template", ""),
            params=decision.get("params", {}),
            steps=decision.get("steps", []),
            code=decision.get("code", ""),
            duration_ms=decision["duration_ms"],
            outcome="pending",
        ))

        session.animations_played_this_scene.append(target_id)
        session.conversation_history.append({
            "role": "system",
            "text": f"Animation '{decision['animation_id']}' (mode={decision['mode']}) "
                    f"on {target_id} ({misl_element})",
            "action": "animate",
        })

        return decision

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
        return None


# ---------------------------------------------------------------------------
# Invocation array execution
# ---------------------------------------------------------------------------

_INTER_ANIMATION_DELAY_MS = 500


async def execute_invocation_array(
    session: Any,  # SessionState
    ws: WSProtocol,
    discrepancies: List[Discrepancy],
) -> Optional[Dict[str, Any]]:
    """Generate and execute a full invocation array from discrepancies.

    Calls generate_invocation_array() to get the structured sequence,
    then iterates through it, playing each animation with delays.

    Returns the first animation's decision dict (for pending animation
    tracking / voice escalation), or None on failure.
    """
    # Study control condition: skip animations but log that we would have animated
    if getattr(session, "study_animations_enabled", None) is False:
        logger.info("[study-control] Skipping animation for %d discrepancies (control condition)",
                     len(discrepancies))
        return None

    try:
        invocation = await generate_invocation_array(
            api_key=session.api_key,
            sprite_code=(
                session.current_scene.get("sprite_code", {})
                if session.current_scene else {}
            ),
            manifest=session.current_manifest,
            student_profile=session.student_profile,
            discrepancies=discrepancies,
        )
    except Exception:
        logger.warning("[animation] Invocation array generation failed")
        return None

    if not invocation.sequence:
        logger.info("[animation] Empty invocation array, nothing to play")
        return None

    first_decision = None

    # Collect all (target, misl_element) pairs across all sequence items
    all_animations = []
    for item in invocation.sequence:
        targets = item.targets if item.targets else []
        if not targets:
            continue

        misl_element = "character"
        for disc in discrepancies:
            if disc.target_entities and disc.target_entities[0] == targets[0]:
                if disc.misl_elements:
                    misl_element = disc.misl_elements[0]
                break

        # Join multiple targets with | for multi-entity animations
        combined_target = "|".join(targets)
        all_animations.append((combined_target, misl_element, item.animation_id))

    # Fire ALL animations simultaneously — no sequencing
    for target_id, misl_element, anim_id in all_animations:
        decision = await execute_animation(
            session=session,
            ws=ws,
            target_id=target_id,
            misl_element=misl_element,
            animation_id=anim_id,
        )
        if first_decision is None:
            first_decision = decision

    # ── Console log: full invocation array ──
    logger.info(
        "\033[95m[INVOCATION ARRAY]\033[0m %d animation(s): %s",
        len(invocation.sequence),
        [(inv.animation_id, inv.targets) for inv in invocation.sequence],
    )

    # Send the invocation array structure to the client for reference
    await ws.send_json({
        "type": "invocation_array",
        "sequence": [inv.model_dump() for inv in invocation.sequence],
    })

    return first_decision


# ---------------------------------------------------------------------------
# Voice (TTS)
# ---------------------------------------------------------------------------

async def send_voice(
    session: Any,  # SessionState — avoids circular import
    ws: WSProtocol,
    text: str,
) -> None:
    """Generate TTS audio and send to client (serialized via voice lock)."""
    # Study mode: skip ALL voice output (system never speaks)
    if getattr(session, "study_animations_enabled", None) is not None:
        logger.info("[study] Skipping voice: %s", text[:80])
        return

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
