"""Tellimation module — generates animations and optional extra sprites.

Responds to discrepancy assessment decisions by generating JavaScript
animation code that visually scaffolds the child's narration.

Model: Gemini 3 Flash (gemini-3-flash-preview)

Fallback: if LLM generation fails, uses client-side FallbackAnimations
(colorPop, shake, pulse, bounce) based on error type heuristics.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

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

# Mapping from error type to fallback animation name (client-side FallbackAnimations)
_FALLBACK_MAP = {
    "PROPERTY_COLOR": "colorPop",
    "PROPERTY_SIZE": "colorPop",
    "PROPERTY_WEIGHT": "colorPop",
    "PROPERTY_TEMPERATURE": "colorPop",
    "PROPERTY_STATE": "pulse",
    "IDENTITY": "shake",
    "QUANTITY": "pulse",
    "SPATIAL": "bounce",
    "ACTION": "shake",
    "MANNER": "shake",
    "TEMPORAL": "pulse",
    "RELATIONAL": "bounce",
    "EXISTENCE": "pulse",
    "OMISSION": "pulse",
    "REDUNDANCY": "shake",
}

# Default fallback duration
_FALLBACK_DURATION_MS = 1200


def _format_entity_details(
    target_id: str,
    manifest: SceneManifest,
) -> str:
    """Extract and format entity details from the manifest for the prompt."""
    # target_id might be "fox_01" or "fox_01.body" — extract root entity
    root_id = target_id.split(".")[0] if "." in target_id else target_id
    # Also try with _NN suffix (e.g. "fox_01")
    entity = manifest.get_entity(root_id)
    if entity is None:
        # Try matching by prefix
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

    # Relations involving this entity
    for rel in manifest.relations:
        if rel.entity_a == entity.id or rel.entity_b == entity.id:
            lines.append(f"Relation: {rel.entity_a} {rel.preposition} {rel.entity_b}")

    # Actions of this entity
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

    # Mask sub-entity IDs
    mask = entry.get("mask", [])
    if mask:
        unique_ids = set(m for m in mask if m is not None)
        lines.append(f"Sub-entity IDs in mask ({len(unique_ids)}):")
        for sid in sorted(unique_ids):
            count = sum(1 for m in mask if m == sid)
            lines.append(f"  {sid}: {count} px")
    else:
        lines.append("(no mask — only root entity ID available)")

    # Pixel stats
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
    """Format animation effectiveness info for the prompt.

    Tells the LLM which animation types worked and which didn't for
    this child, so it can adapt its approach.
    """
    lines = []

    # Check all error types that could apply
    for error_type in student_profile.error_counts:
        effective = student_profile.get_effective_animations(error_type)
        ineffective = student_profile.get_ineffective_animations(error_type)

        if effective:
            lines.append(
                f"For {error_type}: EFFECTIVE animations: {', '.join(effective)}"
            )
        if ineffective:
            lines.append(
                f"For {error_type}: INEFFECTIVE animations (avoid): {', '.join(ineffective)}"
            )

    if not lines:
        return "(No animation history yet — use your best judgment.)"

    return "\n".join(lines)


def _get_fallback_animation(target_id: str, error_type: str) -> Tuple[str, int]:
    """Return a fallback animation code string and duration.

    Uses the client-side FallbackAnimations naming convention. The actual
    JS code is generated client-side — we return a sentinel that tells
    the client to use its built-in fallback.

    Returns:
        Tuple of (fallback_name, duration_ms) — the client uses the name
        to call FallbackAnimations[name](entityPrefix).
    """
    fallback_name = _FALLBACK_MAP.get(error_type, "pulse")
    logger.info("[tellimation] Using fallback animation '%s' for %s (error=%s)",
                fallback_name, target_id, error_type)
    return fallback_name, _FALLBACK_DURATION_MS


async def generate_tellimation(
    api_key: str,
    sprite_code: Dict[str, Any],
    manifest: SceneManifest,
    student_profile: StudentProfile,
    target_id: str,
    animation_cache: Optional[AnimationCache] = None,
    error_type: str = "",
) -> Tuple[str, int]:
    """Generate a tellimation animation for a target entity.

    Tries LLM generation first. On failure, falls back to client-side
    FallbackAnimations (colorPop, shake, pulse, bounce).

    Args:
        api_key: Gemini API key.
        sprite_code: Current scene sprites (positions, pixels, masks).
        manifest: Scene manifest for entity details and relations.
        student_profile: Child's profile for animation effectiveness.
        target_id: Sub-entity or entity to animate (from assessment).
        animation_cache: Optional cache for reusing animations.
        error_type: Optional error type for cache lookup and fallback selection.

    Returns:
        Tuple of (JS animation code string, duration_ms).
        The code is a full `function animate(buf, PW, PH, t) { ... }`.
        On fallback, returns the fallback name instead (client resolves it).
    """
    # Cache lookup
    if animation_cache and error_type:
        cached = animation_cache.lookup(target_id, error_type)
        if cached is not None:
            logger.info("[tellimation] Cache hit: %s × %s", target_id, error_type)
            return cached.code, cached.duration_ms

    # Build prompt
    entity_details = _format_entity_details(target_id, manifest)
    sprite_info = _format_sprite_info(target_id, sprite_code)
    scene_context = _format_scene_context(manifest)
    profile_text = student_profile.to_prompt_context()
    effectiveness = _format_animation_effectiveness(target_id, student_profile)

    user_prompt = TELLIMATION_USER_PROMPT_TEMPLATE.format(
        target_id=target_id,
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

            code = data.get("code", "")
            if not code or not isinstance(code, str):
                raise ValueError("Response missing 'code' field")

            if "function animate" not in code and "animate" not in code:
                raise ValueError("Code must contain an 'animate' function")

            duration_ms = data.get("duration_ms", 1200)
            if not isinstance(duration_ms, (int, float)):
                duration_ms = 1200
            duration_ms = int(duration_ms)

            animation_type = data.get("animation_type", "generated")
            extra_sprite_code = data.get("extra_sprite_code")

            logger.info("[tellimation] Generated '%s' for %s (%d ms)",
                        animation_type, target_id, duration_ms)

            # Log extra sprite code if present
            if extra_sprite_code:
                logger.info("[tellimation] Extra sprite code: %d chars",
                            len(extra_sprite_code))

            # Cache the result
            if animation_cache and error_type:
                animation_cache.store(
                    target_id,
                    error_type,
                    CachedAnimation(
                        code=code,
                        duration_ms=duration_ms,
                        generated_for=target_id,
                    ),
                )

            # Record in student profile
            student_profile.record_animation(
                entity_id=target_id,
                error_type=error_type or "OMISSION",
                animation_type=animation_type,
            )

            return code, duration_ms

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

    fallback_name, fallback_duration = _get_fallback_animation(
        target_id, error_type or "OMISSION"
    )

    # Record fallback in profile
    student_profile.record_animation(
        entity_id=target_id,
        error_type=error_type or "OMISSION",
        animation_type=f"fallback_{fallback_name}",
    )

    # Return the fallback name — the client-side AnimationRunner resolves it
    # to FallbackAnimations[name](entityPrefix)
    return f"__fallback__{fallback_name}", fallback_duration
