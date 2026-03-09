"""Tellimation module — generates animations from the animation grammar.

Given a target entity and a misl_element from the discrepancy assessment,
generates JS animation code, a duration, and optional temporary sprites.

The module chooses, adapts and combines animations from the MISL→animation
mapping (config/misl.py).

Model: Gemini 3 Flash (gemini-3-flash-preview)

Fallback: if LLM generation fails, returns a simple deterministic animation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.student_profile import StudentProfile
from src.generation.prompts.tellimation_prompt import (
    TELLIMATION_SYSTEM_PROMPT,
    TELLIMATION_USER_PROMPT_TEMPLATE,
)
from config.misl import MISL_TO_ANIMATIONS
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
TELLIMATION_TIMEOUT = 30   # latency-critical
MAX_RETRIES = 2

# Fallback animations when LLM generation fails.
# Keyed by coarse family; MISL elements map to these via _MISL_TO_FALLBACK.
_FALLBACK_CODE: Dict[str, str] = {
    "colorPop": """\
function animate(buf, PW, PH, t) {
  var env = _easeEnvelope(t, 0.15, 0.15);
  for (var i = 0; i < buf.length; i++) {
    var isTarget = buf[i].e.startsWith('TARGET');
    if (isTarget) {
      var glow = 1 + 0.3 * env * (0.7 + 0.3 * Math.sin(t * Math.PI * 6));
      buf[i].r = Math.min(255, Math.round(buf[i]._r * glow));
      buf[i].g = Math.min(255, Math.round(buf[i]._g * glow));
      buf[i].b = Math.min(255, Math.round(buf[i]._b * glow));
    } else if (buf[i].e && buf[i].e !== 'bg') {
      var L = Math.round(buf[i]._r * 0.3 + buf[i]._g * 0.59 + buf[i]._b * 0.11);
      buf[i].r = Math.round(buf[i]._r * (1 - env * 0.7) + L * env * 0.7);
      buf[i].g = Math.round(buf[i]._g * (1 - env * 0.7) + L * env * 0.7);
      buf[i].b = Math.round(buf[i]._b * (1 - env * 0.7) + L * env * 0.7);
    }
  }
}""",
    "desaturate": """\
function animate(buf, PW, PH, t) {
  var env = _easeEnvelope(t, 0.2, 0.2);
  for (var i = 0; i < buf.length; i++) {
    if (buf[i].e.startsWith('TARGET')) {
      var L = Math.round(buf[i]._r * 0.3 + buf[i]._g * 0.59 + buf[i]._b * 0.11);
      buf[i].r = Math.round(buf[i]._r * (1 - env * 0.8) + L * env * 0.8);
      buf[i].g = Math.round(buf[i]._g * (1 - env * 0.8) + L * env * 0.8);
      buf[i].b = Math.round(buf[i]._b * (1 - env * 0.8) + L * env * 0.8);
    }
  }
}""",
    "pulse": """\
function animate(buf, PW, PH, t) {
  var env = _easeEnvelope(t, 0.15, 0.15);
  var pulse = 0.5 + 0.5 * Math.sin(t * Math.PI * 4);
  for (var i = 0; i < buf.length; i++) {
    if (buf[i].e.startsWith('TARGET')) {
      var glow = 1 + 0.25 * env * pulse;
      buf[i].r = Math.min(255, Math.round(buf[i]._r * glow));
      buf[i].g = Math.min(255, Math.round(buf[i]._g * glow));
      buf[i].b = Math.min(255, Math.round(buf[i]._b * glow));
    }
  }
}""",
    "bounce": """\
function animate(buf, PW, PH, t) {
  var offset = Math.round(Math.abs(Math.sin(t * Math.PI * 3)) * -8 * (1 - t));
  if (offset === 0) return;
  var pixels = _collectEntityPixels(buf, PW, 'TARGET');
  _blankEntityPixels(buf, pixels);
  _redrawEntityPixels(buf, PW, PH, pixels, 0, offset);
}""",
}

_FALLBACK_DURATION_MS = 1200

# Map MISL elements to fallback animation for _FALLBACK_CODE lookup.
_MISL_TO_FALLBACK: Dict[str, str] = {
    "character": "pulse",            # I1 spotlight → pulse
    "setting": "bounce",             # S2 settle → bounce
    "initiating_event": "pulse",     # A2 anticipation → pulse
    "internal_response": "colorPop", # P2 emanation → colorPop
    "plan": "pulse",                 # D2 thought bubble → pulse
    "action": "bounce",              # A1 motion lines → bounce
    "consequence": "pulse",          # R3 causal push → pulse
    "coordinating_conjunctions": "pulse",
    "subordinating_conjunctions": "pulse",
    "mental_verbs": "colorPop",      # D2 thought bubble → colorPop
    "linguistic_verbs": "pulse",     # D1 speech bubble → pulse
    "adverbs": "colorPop",           # P1 color pop
    "elaborated_noun_phrases": "colorPop",  # P1 color pop
    "grammaticality": "pulse",       # D4 interjection → pulse
    "tense": "desaturate",           # T1 flashback → desaturate
}


def _format_entity_details(
    target_id: str,
    manifest: SceneManifest,
) -> str:
    """Extract and format entity details from the manifest for the prompt."""
    root_id = target_id.split(".")[0] if "." in target_id else target_id
    entity = manifest.get_entity(root_id)
    if entity is None:
        for ent in manifest.entities:
            if target_id.startswith(ent.id):
                entity = ent
                break

    if entity is None:
        return f"(entity for target '{target_id}' not found in manifest)"

    lines = [f"Entity: {entity.id} (type: {entity.type})"]

    if entity.properties:
        lines.append("Properties:")
        for k, v in entity.properties.items():
            lines.append(f"  {k}: {v}")

    if entity.emotion:
        lines.append(f"Emotion: {entity.emotion}")

    if entity.pose:
        lines.append(f"Pose: {entity.pose}")

    if entity.position.spatial_ref:
        lines.append(f"Spatial ref: {entity.position.spatial_ref}")

    for rel in manifest.relations:
        if rel.entity_a == entity.id or rel.entity_b == entity.id:
            lines.append(f"Relation: {rel.entity_a} {rel.preposition} {rel.entity_b}")

    for act in manifest.actions:
        if act.entity_id == entity.id:
            manner = f" ({act.manner})" if act.manner else ""
            lines.append(f"Action: {act.verb}{manner}")

    return "\n".join(lines)


def _format_sprite_info(
    target_id: str,
    sprite_code: Dict[str, Any],
) -> str:
    """Format sprite info for the target entity from sprite_code."""
    root_id = target_id.split(".")[0] if "." in target_id else target_id

    entry = sprite_code.get(root_id)
    if not entry or not isinstance(entry, dict):
        return f"(no sprite data for '{root_id}')"

    lines = []
    fmt = entry.get("format", "unknown")
    lines.append(f"Format: {fmt}")
    lines.append(f"Position: x={entry.get('x', '?')}, y={entry.get('y', '?')}")
    lines.append(f"Size: {entry.get('w', '?')}x{entry.get('h', '?')}")

    mask = entry.get("mask", [])
    if mask:
        unique_ids = set(m for m in mask if m is not None)
        lines.append(f"Sub-entity IDs in mask ({len(unique_ids)}):")
        for sid in sorted(unique_ids):
            count = sum(1 for m in mask if m == sid)
            lines.append(f"  {sid}: {count} px")
    else:
        lines.append("(no mask — only root entity ID available)")

    pixels = entry.get("pixels", [])
    if pixels:
        visible = sum(1 for p in pixels if p is not None)
        lines.append(f"Visible pixels: {visible}/{len(pixels)}")

    return "\n".join(lines)


def _format_scene_context(manifest: SceneManifest) -> str:
    """Format the full scene context for the prompt."""
    lines = []
    for ent in manifest.entities:
        props_str = ", ".join(f"{k}={v}" for k, v in ent.properties.items())
        lines.append(f"- {ent.id} ({ent.type}): {props_str}")

    if manifest.relations:
        lines.append("Relations:")
        for rel in manifest.relations:
            lines.append(f"  {rel.entity_a} {rel.preposition} {rel.entity_b}")

    if manifest.actions:
        lines.append("Actions:")
        for act in manifest.actions:
            manner = f" ({act.manner})" if act.manner else ""
            lines.append(f"  {act.entity_id}: {act.verb}{manner}")

    return "\n".join(lines) if lines else "(empty scene)"


def _format_animation_effectiveness(
    target_id: str,
    student_profile: StudentProfile,
) -> str:
    """Format animation effectiveness info for the prompt."""
    lines: List[str] = []

    seen_misl_elements: set = set()
    for entry in student_profile.animation_efficacy:
        me = entry.get("misl_element", "")
        if me:
            seen_misl_elements.add(me)

    for misl_element in sorted(seen_misl_elements):
        scores = student_profile.get_effective_animations(misl_element)
        if scores:
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            parts = [f"{atype}={score:.0%}" for atype, score in ranked]
            lines.append(f"For {misl_element}: efficacy scores: {', '.join(parts)}")

    for error_type in student_profile.error_counts:
        ineffective = student_profile.get_ineffective_animations(error_type)
        if ineffective:
            lines.append(
                f"For {error_type}: INEFFECTIVE animations (avoid): "
                f"{', '.join(ineffective)}"
            )

    if not lines:
        return "(No animation history yet — use your best judgment.)"

    return "\n".join(lines)


def _extract_fallback_text(
    target_id: str,
    misl_element: str,
    manifest: SceneManifest,
    neg: Optional[NEG],
) -> List[Dict[str, Any]]:
    """Extract text overlay from NEG target description for fallback animation.

    Returns a list with one text_overlay dict, or empty list if no match found.
    """
    if neg is None:
        return []

    # Find matching NEG target
    root_id = target_id.split(".")[0] if "." in target_id else target_id
    matching_target = None
    for t in neg.targets:
        if t.entity_id == root_id and t.misl_element == misl_element:
            matching_target = t
            break
    # Fallback: match just entity_id
    if matching_target is None:
        for t in neg.targets:
            if t.entity_id == root_id:
                matching_target = t
                break

    if matching_target is None or not matching_target.description:
        return []

    # Take first 2 words, uppercase, strip non-font chars
    words = matching_target.description.strip().split()[:2]
    text = " ".join(words).upper()
    # Bitmap font only supports A-Z 0-9 . , ! ? - '  SPACE
    text = "".join(c for c in text if c.isalnum() or c in " .,!?-'")
    if not text:
        return []

    # Position text above the entity
    entity = manifest.get_entity(root_id)
    if entity and entity.position:
        tx = max(5, min(entity.position.x - len(text) * 3, 500))
        ty = max(5, entity.position.y - 20)
    else:
        tx, ty = 200, 50

    return [{
        "text": text,
        "x": tx,
        "y": ty,
        "color": [255, 255, 100],
        "id": "fallback_text",
        "scale": 2,
    }]


def _build_fallback(
    target_id: str,
    misl_element: str,
    manifest: SceneManifest,
    neg: Optional[NEG] = None,
) -> Tuple[str, int, None, str, List[Dict[str, Any]]]:
    """Build a fallback animation with text overlay when LLM generation fails."""
    family = _MISL_TO_FALLBACK.get(misl_element, "pulse")
    code = _FALLBACK_CODE.get(family, _FALLBACK_CODE["pulse"])
    code = code.replace("TARGET", target_id)
    animation_id = f"fallback_{family}"
    text_overlays = _extract_fallback_text(target_id, misl_element, manifest, neg)
    logger.info("[tellimation] Using fallback for %s (misl=%s, family=%s, text=%s)",
                target_id, misl_element, family,
                text_overlays[0]["text"] if text_overlays else "none")
    return (code, _FALLBACK_DURATION_MS, None, animation_id, text_overlays)


async def generate_tellimation(
    api_key: str,
    sprite_code: Dict[str, Any],
    manifest: SceneManifest,
    student_profile: StudentProfile,
    target_id: str,
    misl_element: str,
    neg: Optional[NEG] = None,
    problematic_segment: Optional[str] = None,
) -> Tuple[str, int, Optional[Dict], str, List[Dict[str, Any]]]:
    """Generate a tellimation animation for a target entity.

    Returns (JS_code, duration_ms, temp_sprites_or_None, animation_id, text_overlays).
    temp_sprites is a dict of sprite entries (same format as sprite_code)
    for temporary visual elements like speech bubbles or nametags.
    text_overlays is a list of text overlay dicts for the client to render.
    """
    # Build eligible animations list from MISL mapping
    eligible = MISL_TO_ANIMATIONS.get(misl_element, [])
    eligible_text = ", ".join(eligible) if eligible else "(any)"

    # Build problematic segment section for D4 interjection
    if problematic_segment:
        segment_section = (
            f"Problematic segment (from child's speech): \"{problematic_segment}\"\n"
        )
    else:
        segment_section = ""

    # Build prompt
    entity_details = _format_entity_details(target_id, manifest)
    sprite_info = _format_sprite_info(target_id, sprite_code)
    scene_context = _format_scene_context(manifest)
    profile_text = student_profile.to_prompt_context()
    effectiveness = _format_animation_effectiveness(target_id, student_profile)

    user_prompt = TELLIMATION_USER_PROMPT_TEMPLATE.format(
        target_id=target_id,
        misl_element=misl_element,
        eligible_animations=eligible_text,
        entity_details=entity_details,
        sprite_info=sprite_info,
        scene_context=scene_context,
        student_profile=profile_text,
        animation_effectiveness=effectiveness,
        problematic_segment_section=segment_section,
    )

    client = genai.Client(api_key=api_key)
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=MODEL_ID,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=TELLIMATION_SYSTEM_PROMPT,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        temperature=0.7,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=TELLIMATION_TIMEOUT,
            )

            data = _extract_json(_get_response_text(response))

            code = data.get("code", "")
            if not isinstance(code, str) or "animate" not in code:
                raise ValueError("Response missing valid 'code' with animate function")

            duration_ms = data.get("duration_ms", 1200)
            if not isinstance(duration_ms, (int, float)):
                duration_ms = 1200
            duration_ms = int(duration_ms)

            temp_sprites = data.get("temp_sprites")
            if temp_sprites is not None and not isinstance(temp_sprites, dict):
                temp_sprites = None

            animation_id = data.get("animation_id", "custom")
            text_overlays = data.get("text_overlays", [])
            if not isinstance(text_overlays, list):
                text_overlays = []

            logger.info("[tellimation] Generated '%s' for %s (%d ms, temp_sprites=%s, overlays=%d)",
                        animation_id, target_id, duration_ms,
                        bool(temp_sprites), len(text_overlays))

            student_profile.record_animation(
                entity_id=target_id,
                error_type=misl_element,
                animation_type=animation_id,
            )

            return (code, duration_ms, temp_sprites, animation_id, text_overlays)

        except asyncio.TimeoutError:
            logger.warning("[tellimation] Attempt %d/%d timed out after %ds",
                           attempt, MAX_RETRIES, TELLIMATION_TIMEOUT)
            last_exc = asyncio.TimeoutError()
        except Exception as exc:
            logger.warning("[tellimation] Attempt %d/%d failed (%s): %s",
                           attempt, MAX_RETRIES,
                           type(exc).__name__, exc or "no details")
            last_exc = exc

    # All retries failed — use fallback
    logger.warning("[tellimation] All %d attempts failed (%s), using fallback",
                   MAX_RETRIES, last_exc)

    code, duration_ms, temp_sprites, animation_id, text_overlays = _build_fallback(
        target_id, misl_element, manifest, neg,
    )

    student_profile.record_animation(
        entity_id=target_id,
        error_type=misl_element,
        animation_type=animation_id,
    )

    return (code, duration_ms, temp_sprites, animation_id, text_overlays)
