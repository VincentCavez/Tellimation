"""On-the-fly animation code generation via Gemini 3 Flash."""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from google import genai
from google.genai import types

from src.models.animation_cache import AnimationCache, CachedAnimation
from src.generation.prompts.animation_prompt import (
    ANIMATION_SYSTEM_PROMPT,
    ANIMATION_USER_PROMPT,
)

MODEL_ID = "gemini-3-flash-preview"


def _format_scene_context(scene_context: Dict[str, Any]) -> str:
    """Format scene context dict into a readable string for the prompt."""
    if not scene_context:
        return "(no scene context)"

    lines = []
    entities = scene_context.get("entities", [])
    for ent in entities:
        eid = ent.get("id", "?")
        etype = ent.get("type", "?")
        props = ent.get("properties", {})
        pos = ent.get("position", {})
        lines.append(
            f"- {eid} ({etype}): properties={props}, "
            f"position=({pos.get('x', '?')}, {pos.get('y', '?')})"
        )

    relations = scene_context.get("relations", [])
    if relations:
        lines.append("Relations:")
        for rel in relations:
            lines.append(
                f"  {rel.get('entity_a', '?')} {rel.get('preposition', '?')} "
                f"{rel.get('entity_b', '?')}"
            )

    actions = scene_context.get("actions", [])
    if actions:
        lines.append("Actions:")
        for act in actions:
            manner = act.get("manner", "")
            manner_str = f" ({manner})" if manner else ""
            lines.append(f"  {act.get('entity_id', '?')} -> {act.get('verb', '?')}{manner_str}")

    return "\n".join(lines) if lines else "(empty scene)"


def _extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from LLM response, handling markdown fences."""
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return json.loads(cleaned)


def _validate_animation_response(data: Dict[str, Any]) -> CachedAnimation:
    """Validate and extract a CachedAnimation from the LLM response."""
    code = data.get("code", "")
    if not code or not isinstance(code, str):
        raise ValueError("Response missing or invalid 'code' field")

    # Ensure the code contains the function signature
    if "function animate" not in code and "animate" not in code:
        raise ValueError("Animation code must contain an 'animate' function")

    duration_ms = data.get("duration_ms", 1200)
    if not isinstance(duration_ms, (int, float)):
        duration_ms = 1200
    duration_ms = int(duration_ms)

    animation_type = data.get("animation_type", "")

    return CachedAnimation(
        code=code,
        duration_ms=duration_ms,
        generated_for=animation_type,
    )


async def generate_animation(
    api_key: str,
    error_type: str,
    entity_id: str,
    sub_entity: str,
    entity_bounds: Dict[str, int],
    scene_context: Dict[str, Any],
    animation_cache: AnimationCache,
) -> CachedAnimation:
    """Generate or retrieve an animation for an entity/error pair.

    1. Check the cache — if an animation exists for this sub_entity + error_type,
       return it immediately (no API call).
    2. Otherwise, call Gemini 3 Flash to generate animation code.
    3. Store the result in the cache and return it.

    Args:
        api_key: Gemini API key.
        error_type: Error type string (e.g. "PROPERTY_COLOR", "SPATIAL").
        entity_id: Root entity ID (e.g. "rabbit_01").
        sub_entity: Specific sub-entity to animate (e.g. "rabbit_01.body").
        entity_bounds: Bounding box dict with keys x, y, width, height.
        scene_context: Current scene manifest dict (entities, relations, actions).
        animation_cache: The shared AnimationCache instance.

    Returns:
        CachedAnimation with code, duration_ms, and generated_for.
    """
    # 1. Cache lookup
    cached = animation_cache.lookup(sub_entity, error_type)
    if cached is not None:
        return cached

    # 2. Build prompt
    context_str = _format_scene_context(scene_context)
    user_prompt = ANIMATION_USER_PROMPT.format(
        error_type=error_type,
        entity_id=entity_id,
        sub_entity=sub_entity,
        bbox_x=entity_bounds.get("x", 0),
        bbox_y=entity_bounds.get("y", 0),
        bbox_w=entity_bounds.get("width", 0),
        bbox_h=entity_bounds.get("height", 0),
        scene_context=context_str,
    )

    # 3. Call Gemini
    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=ANIMATION_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            temperature=0.7,
            response_mime_type="application/json",
        ),
    )

    # 4. Parse and validate
    raw_text = response.text
    data = _extract_json(raw_text)
    animation = _validate_animation_response(data)

    # Override generated_for with the actual sub_entity
    animation.generated_for = sub_entity

    # 5. Store in cache
    animation_cache.store(sub_entity, error_type, animation)

    return animation
