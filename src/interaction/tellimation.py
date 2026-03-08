"""Tellimation module — selects animation templates and optional particles.

Responds to discrepancy assessment decisions by choosing pre-written
animation templates (A01-A16) and parameterizing them for the scene.

Model: Gemini 3 Flash (gemini-3-flash-preview)

Fallback: if LLM generation fails, maps error type to a default template.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from src.models.animation_cache import AnimationCache, CachedAnimation
from src.models.scene import SceneManifest
from src.models.student_profile import StudentProfile
from src.generation.prompts.tellimation_prompt import (
    TELLIMATION_SYSTEM_PROMPT,
    TELLIMATION_USER_PROMPT_TEMPLATE,
)
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
TELLIMATION_TIMEOUT = 30   # latency-critical
MAX_RETRIES = 2

# Mapping from error type to default template name (A01-A16)
_FALLBACK_MAP = {
    "SPATIAL": "settle",
    "PROPERTY_COLOR": "color_pop",
    "PROPERTY_SIZE": "scale_strain",
    "PROPERTY_WEIGHT": "emanation",
    "PROPERTY_TEMPERATURE": "emanation",
    "PROPERTY_STATE": "emanation",
    "TEMPORAL": "afterimage",
    "ACTION": "motion_lines",
    "MANNER": "motion_lines",
    "IDENTITY": "wobble",
    "QUANTITY": "sequential_glow",
    "RELATIONAL": "magnetism",
    "EXISTENCE": "ghost_outline",
    "OMISSION": "ghost_outline",
    "REDUNDANCY": "bonk",
}

_FALLBACK_DURATION_MS = 1200


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

    seen_skill_types: set = set()
    for entry in student_profile.animation_efficacy:
        st = entry.get("skill_type", "")
        if st:
            seen_skill_types.add(st)

    for skill_type in sorted(seen_skill_types):
        scores = student_profile.get_effective_animations(skill_type)
        if scores:
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            parts = [f"{atype}={score:.0%}" for atype, score in ranked]
            lines.append(f"For {skill_type}: efficacy scores: {', '.join(parts)}")

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


def _build_fallback(target_id: str, error_type: str) -> CachedAnimation:
    """Build a fallback CachedAnimation using a default template."""
    template_name = _FALLBACK_MAP.get(error_type, "wobble")
    logger.info("[tellimation] Using fallback template '%s' for %s (error=%s)",
                template_name, target_id, error_type)

    params: Dict[str, Any] = {"entityPrefix": target_id}

    # Template-specific defaults for fallback
    if template_name == "emanation":
        ptype_map = {
            "PROPERTY_WEIGHT": "dust",
            "PROPERTY_TEMPERATURE": "steam",
            "PROPERTY_STATE": "sparkle",
        }
        params["particleType"] = ptype_map.get(error_type, "steam")

    return CachedAnimation(
        template=template_name,
        params=params,
        duration_ms=_FALLBACK_DURATION_MS,
        generated_for=target_id,
    )


async def generate_tellimation(
    api_key: str,
    sprite_code: Dict[str, Any],
    manifest: SceneManifest,
    student_profile: StudentProfile,
    target_id: str,
    animation_cache: Optional[AnimationCache] = None,
    error_type: str = "",
) -> CachedAnimation:
    """Generate a tellimation animation for a target entity.

    Returns a CachedAnimation containing either a template spec or raw code.
    The caller converts it to a WebSocket message via .to_ws_dict().
    """
    # Cache lookup
    if animation_cache and error_type:
        cached = animation_cache.lookup(target_id, error_type)
        if cached is not None:
            logger.info("[tellimation] Cache hit: %s × %s", target_id, error_type)
            return cached

    # Build prompt
    entity_details = _format_entity_details(target_id, manifest)
    sprite_info = _format_sprite_info(target_id, sprite_code)
    scene_context = _format_scene_context(manifest)
    profile_text = student_profile.to_prompt_context()
    effectiveness = _format_animation_effectiveness(target_id, student_profile)

    user_prompt = TELLIMATION_USER_PROMPT_TEMPLATE.format(
        target_id=target_id,
        error_type=error_type or "OMISSION",
        entity_details=entity_details,
        sprite_info=sprite_info,
        scene_context=scene_context,
        student_profile=profile_text,
        animation_effectiveness=effectiveness,
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

            # Template-based response (preferred)
            template_name = data.get("template")
            if template_name and isinstance(template_name, str):
                params = data.get("params", {})
                if not isinstance(params, dict):
                    params = {}
                # Ensure entityPrefix is set
                if "entityPrefix" not in params:
                    params["entityPrefix"] = target_id

                particles = data.get("particles", [])
                if not isinstance(particles, list):
                    particles = []

                text_overlays = data.get("text_overlays", [])
                if not isinstance(text_overlays, list):
                    text_overlays = []

                duration_ms = data.get("duration_ms", 1200)
                if not isinstance(duration_ms, (int, float)):
                    duration_ms = 1200
                duration_ms = int(duration_ms)

                animation_id = data.get("animation_id", template_name)
                logger.info("[tellimation] Template '%s' (%s) for %s (%d ms)",
                            template_name, animation_id, target_id, duration_ms)

                result = CachedAnimation(
                    template=template_name,
                    params=params,
                    particles=particles,
                    text_overlays=text_overlays,
                    duration_ms=duration_ms,
                    generated_for=target_id,
                )

                # Cache
                if animation_cache and error_type:
                    animation_cache.store(target_id, error_type, result)

                student_profile.record_animation(
                    entity_id=target_id,
                    error_type=error_type or "OMISSION",
                    animation_type=template_name,
                )

                return result

            # Custom code fallback response
            code = data.get("code", "")
            if code and isinstance(code, str):
                if "animate" not in code:
                    raise ValueError("Custom code must contain an 'animate' function")

                duration_ms = data.get("duration_ms", 1200)
                if not isinstance(duration_ms, (int, float)):
                    duration_ms = 1200
                duration_ms = int(duration_ms)

                animation_type = data.get("animation_type", "custom")
                logger.info("[tellimation] Custom code '%s' for %s (%d ms)",
                            animation_type, target_id, duration_ms)

                result = CachedAnimation(
                    code=code,
                    duration_ms=duration_ms,
                    generated_for=target_id,
                )

                if animation_cache and error_type:
                    animation_cache.store(target_id, error_type, result)

                student_profile.record_animation(
                    entity_id=target_id,
                    error_type=error_type or "OMISSION",
                    animation_type=animation_type,
                )

                return result

            raise ValueError("Response has neither 'template' nor 'code' field")

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

    result = _build_fallback(target_id, error_type or "OMISSION")

    student_profile.record_animation(
        entity_id=target_id,
        error_type=error_type or "OMISSION",
        animation_type=f"fallback_{result.template}",
    )

    return result
