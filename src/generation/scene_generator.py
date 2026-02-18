"""Scene generation via Gemini 3 Flash.

Supports two modes:
  - use_reference_images=True (default): 3-step pipeline
      1. Generate manifest + NEG (text-only)
      2. Generate scene reference illustration (gemini-2.5-flash-image)
      3. Generate sprite code (multimodal: manifest + illustration)
  - use_reference_images=False (legacy): single-call all-in-one
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.story_state import StoryState
from src.models.student_profile import StudentProfile
from src.generation.prompts.scene_prompt import (
    CONTINUATION_SCENE_USER_PROMPT,
    INITIAL_SCENE_USER_PROMPT,
    MANIFEST_SYSTEM_PROMPT,
    SCENE_IMAGE_PROMPT_TEMPLATE,
    SCENE_SYSTEM_PROMPT,
)
from src.generation.prompts.sprite_prompt import (
    SPRITE_SYSTEM_PROMPT,
    SPRITE_USER_PROMPT,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
IMAGE_MODEL_ID = "gemini-2.5-flash-image"


# ---------------------------------------------------------------------------
# Response text extraction (avoids thought_signature warnings)
# ---------------------------------------------------------------------------

def _get_response_text(response: Any) -> str:
    """Extract text from a Gemini response, skipping thinking/signature parts.

    When thinking_config is enabled, response.text triggers a warning about
    non-text parts (thought, thought_signature).  This helper accesses the
    parts directly and concatenates only the text ones.
    """
    if response.candidates and response.candidates[0].content:
        text_parts = []
        for part in response.candidates[0].content.parts:
            if hasattr(part, "text") and part.text is not None:
                text_parts.append(part.text)
        if text_parts:
            return "".join(text_parts)
    # Fallback: let the SDK handle it (may warn, but at least doesn't crash)
    return response.text


# ---------------------------------------------------------------------------
# Prompt builders (shared between legacy and pipeline)
# ---------------------------------------------------------------------------

def _build_initial_prompt(
    skill_objectives: List[str],
    seed_index: int,
) -> str:
    return INITIAL_SCENE_USER_PROMPT.format(
        skill_objectives=", ".join(skill_objectives),
        seed_index=seed_index,
    )


def _build_continuation_prompt(
    story_state: StoryState,
    student_profile: Optional[StudentProfile],
    skill_objectives: List[str],
) -> str:
    # Story context: narrative summaries of each scene
    story_lines = []
    for s in story_state.scenes:
        story_lines.append(
            f"- {s.get('scene_id', '?')}: {s.get('narrative_text', '')}"
        )
    story_context = "\n".join(story_lines) if story_lines else "(first scene)"

    # Previous manifest
    previous_manifest = "{}"
    if story_state.scenes:
        last = story_state.scenes[-1]
        previous_manifest = json.dumps(last.get("manifest", {}), indent=2)

    # Active entities summary
    entity_lines = []
    for eid, ent in story_state.active_entities.items():
        entity_lines.append(
            f"- {eid} (type={ent.type}, appeared={ent.first_appeared}, "
            f"pos={ent.last_position})"
        )
    active_entities = "\n".join(entity_lines) if entity_lines else "(none)"

    # Student profile
    profile_ctx = ""
    if student_profile:
        profile_ctx = student_profile.to_prompt_context()

    scene_number = len(story_state.scenes) + 1

    return CONTINUATION_SCENE_USER_PROMPT.format(
        story_context=story_context,
        previous_manifest=previous_manifest,
        active_entities=active_entities,
        student_profile_context=profile_ctx,
        skill_objectives=", ".join(skill_objectives),
        scene_number=scene_number,
    )


# ---------------------------------------------------------------------------
# JSON extraction and validation
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from LLM response, handling markdown fences and trailing text.

    The model sometimes outputs valid JSON followed by extra commentary or
    duplicate JSON blocks (especially with high thinking budgets).  This
    helper tries several strategies:

    1. Strip markdown fences and parse.
    2. If that fails with "Extra data", use json.JSONDecoder to parse only
       the first complete JSON object and ignore the rest.
    3. Regex-extract the first ``{...}`` block (greedy, brace-balanced).
    """
    # Strip markdown code fences if present
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Strategy 1: plain parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_err:
        # Strategy 2: parse only the first JSON value (ignores trailing data)
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(cleaned)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 3: find the first brace-balanced {...} substring
        start = cleaned.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(cleaned)):
                ch = cleaned[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start : i + 1])
                        except json.JSONDecodeError:
                            break

        # Nothing worked — raise the original error
        raise first_err


def _validate_scene_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize the LLM response into canonical form."""
    # Validate manifest
    manifest_data = data.get("manifest")
    if not manifest_data:
        raise ValueError("Response missing 'manifest' field")
    manifest = SceneManifest.model_validate(manifest_data)

    # Validate NEG
    neg_data = data.get("neg")
    if not neg_data:
        raise ValueError("Response missing 'neg' field")
    neg = NEG.model_validate(neg_data)

    # Normalize sprite_code
    sprite_code = data.get("sprite_code", {})
    if not isinstance(sprite_code, dict):
        sprite_code = {}

    # Normalize carried_over_entities
    carried_over = data.get("carried_over_entities", [])
    if not isinstance(carried_over, list):
        carried_over = []

    return {
        "narrative_text": data.get("narrative_text", ""),
        "branch_summary": data.get("branch_summary", ""),
        "scene_description": data.get("scene_description", ""),
        "manifest": manifest.model_dump(),
        "neg": neg.model_dump(),
        "sprite_code": sprite_code,
        "carried_over_entities": carried_over,
    }


# ---------------------------------------------------------------------------
# Step 1: Manifest + NEG generation (text-only)
# ---------------------------------------------------------------------------

async def _generate_manifest(
    client: Any,
    user_prompt: str,
) -> Dict[str, Any]:
    """Step 1: Generate manifest + NEG (no sprite code).

    Returns:
        Raw parsed dict with narrative_text, branch_summary, scene_description,
        manifest, neg, carried_over_entities.
    """
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=MANIFEST_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            temperature=0.9,
            response_mime_type="application/json",
        ),
    )
    data = _extract_json(_get_response_text(response))

    # Validate manifest and NEG
    manifest_data = data.get("manifest")
    if not manifest_data:
        raise ValueError("Manifest response missing 'manifest' field")
    SceneManifest.model_validate(manifest_data)

    neg_data = data.get("neg")
    if not neg_data:
        raise ValueError("Manifest response missing 'neg' field")
    NEG.model_validate(neg_data)

    return data


# ---------------------------------------------------------------------------
# Step 2: Scene reference illustration generation
# ---------------------------------------------------------------------------

def _build_scene_image_prompt(manifest_data: Dict[str, Any]) -> str:
    """Build the prompt for generating a reference illustration of the scene."""
    scene_desc = manifest_data.get("scene_description", "")

    entity_parts = []
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        if ent.get("carried_over"):
            continue  # Only describe new entities in detail
        props = ent.get("properties", {})
        parts = []
        # Build description from properties
        size = props.get("size", "")
        color = props.get("color", "")
        texture = props.get("texture", "")
        etype = ent.get("type", "entity")
        desc = f"A {size} {color} {texture} {etype}".strip()
        if ent.get("emotion"):
            desc += f", looking {ent['emotion']}"
        if ent.get("pose"):
            desc += f", {ent['pose']}"
        distinctive = props.get("distinctive_features", "")
        if distinctive:
            desc += f". {distinctive}"
        spatial = ent.get("position", {}).get("spatial_ref")
        if spatial:
            desc += f" ({spatial})"
        entity_parts.append(f"- {desc}")

    # Also add carried-over entities with brief descriptions
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        if not ent.get("carried_over"):
            continue
        props = ent.get("properties", {})
        color = props.get("color", "")
        etype = ent.get("type", "entity")
        desc = f"A {color} {etype}".strip()
        if ent.get("pose"):
            desc += f", {ent['pose']}"
        spatial = ent.get("position", {}).get("spatial_ref")
        if spatial:
            desc += f" ({spatial})"
        entity_parts.append(f"- {desc}")

    entity_descriptions = "\n".join(entity_parts) if entity_parts else "(no entities)"

    return SCENE_IMAGE_PROMPT_TEMPLATE.format(
        scene_description=scene_desc,
        entity_descriptions=entity_descriptions,
    )


async def _generate_scene_image(
    client: Any,
    manifest_data: Dict[str, Any],
) -> Optional[bytes]:
    """Step 2: Generate a reference illustration of the full scene.

    Returns:
        PNG image bytes, or None if generation fails.
    """
    prompt = _build_scene_image_prompt(manifest_data)

    try:
        response = await client.aio.models.generate_content(
            model=IMAGE_MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        # Extract image from response
        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    return part.inline_data.data

        logger.warning("Image generation returned no image data")
        return None

    except Exception as exc:
        logger.warning("Scene image generation failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Step 3: Sprite code generation (multimodal)
# ---------------------------------------------------------------------------

def _build_sprite_user_prompt(
    manifest_data: Dict[str, Any],
    carried_over_entities: List[str],
    story_state: Optional[StoryState],
) -> str:
    """Build the user prompt for sprite code generation."""
    scene_desc = manifest_data.get("scene_description", "")

    # Format entity details for new entities
    entity_detail_parts = []
    for ent in manifest_data.get("manifest", {}).get("entities", []):
        if ent.get("id") in carried_over_entities:
            continue
        props = ent.get("properties", {})
        pos = ent.get("position", {})
        detail = (
            f"- **{ent.get('id')}** (type: {ent.get('type')})\n"
            f"  Position: x={pos.get('x', 0)}, y={pos.get('y', 0)}"
        )
        if pos.get("spatial_ref"):
            detail += f", {pos['spatial_ref']}"
        detail += "\n"
        prop_items = [f"{k}: {v}" for k, v in props.items()]
        if prop_items:
            detail += f"  Properties: {', '.join(prop_items)}\n"
        if ent.get("emotion"):
            detail += f"  Emotion: {ent['emotion']}\n"
        if ent.get("pose"):
            detail += f"  Pose: {ent['pose']}\n"
        entity_detail_parts.append(detail)

    entity_details = "\n".join(entity_detail_parts) if entity_detail_parts else "(none)"

    carried_str = ", ".join(carried_over_entities) if carried_over_entities else "(none)"

    return SPRITE_USER_PROMPT.format(
        scene_description=scene_desc,
        entity_details=entity_details,
        carried_over_entities=carried_str,
    )


async def _generate_sprite_code(
    client: Any,
    manifest_data: Dict[str, Any],
    scene_image_bytes: Optional[bytes],
    carried_over_entities: List[str],
    story_state: Optional[StoryState],
) -> Dict[str, str]:
    """Step 3: Generate sprite code using manifest + reference image.

    Returns:
        Dict mapping entity_id -> JavaScript sprite code string.
    """
    user_text = _build_sprite_user_prompt(
        manifest_data, carried_over_entities, story_state
    )

    # Build multimodal contents — image FIRST so the model grounds on it
    # before reading the text instructions (better visual fidelity).
    contents: List[Any] = []
    if scene_image_bytes:
        contents.append(
            types.Part.from_bytes(data=scene_image_bytes, mime_type="image/png")
        )
    contents.append(user_text)

    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SPRITE_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(thinking_budget=4096),
            temperature=0.7,
            response_mime_type="application/json",
        ),
    )

    data = _extract_json(_get_response_text(response))
    sprite_code = data.get("sprite_code", {})
    if not isinstance(sprite_code, dict):
        sprite_code = {}

    return sprite_code


# ---------------------------------------------------------------------------
# Legacy single-call pipeline
# ---------------------------------------------------------------------------

async def _generate_scene_legacy(
    client: Any,
    user_prompt: str,
) -> Dict[str, Any]:
    """Legacy all-in-one generation: manifest + NEG + sprite code in one call."""
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SCENE_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            temperature=0.9,
            response_mime_type="application/json",
        ),
    )
    data = _extract_json(_get_response_text(response))
    return _validate_scene_response(data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_scene(
    api_key: str,
    story_state: Optional[StoryState] = None,
    student_profile: Optional[StudentProfile] = None,
    skill_objectives: Optional[List[str]] = None,
    seed_index: int = 0,
    commit_to_state: bool = True,
    extra_prompt: str = "",
    use_reference_images: bool = True,
) -> Dict[str, Any]:
    """Generate a single scene via Gemini 3 Flash.

    Args:
        api_key: Gemini API key.
        story_state: Cumulative story state, or None for initial scene.
        student_profile: Child's error profile, or None for initial scene.
        skill_objectives: SKILL objectives for the session.
        seed_index: Seed for variety (1, 2, 3 for initial thumbnails).
        commit_to_state: If True, call story_state.add_scene() with the result.
            Set to False for candidate branches that shouldn't mutate state.
        extra_prompt: Additional text appended to the user prompt (e.g. branch directive).
        use_reference_images: If True, use 3-step pipeline with reference image.
            If False, use legacy single-call pipeline.

    Returns:
        Dict with narrative_text, branch_summary, manifest, neg,
        sprite_code, carried_over_entities.
    """
    if skill_objectives is None:
        skill_objectives = [
            "descriptive_adjectives",
            "spatial_prepositions",
            "action_verbs",
        ]

    # Build user prompt
    if story_state is None or len(story_state.scenes) == 0:
        user_prompt = _build_initial_prompt(skill_objectives, seed_index)
    else:
        user_prompt = _build_continuation_prompt(
            story_state, student_profile, skill_objectives
        )

    if extra_prompt:
        user_prompt += "\n" + extra_prompt

    # Create client
    client = genai.Client(api_key=api_key)

    if use_reference_images:
        result = await _pipeline_with_reference_image(
            client, user_prompt, story_state
        )
    else:
        result = await _generate_scene_legacy(client, user_prompt)

    # Update story_state if provided and commit requested
    if story_state is not None and commit_to_state:
        story_state.add_scene(
            scene_id=result["manifest"]["scene_id"],
            narrative_text=result["narrative_text"],
            manifest=result["manifest"],
            neg=result["neg"],
            sprite_code=result["sprite_code"] or None,
        )

    return result


async def _pipeline_with_reference_image(
    client: Any,
    user_prompt: str,
    story_state: Optional[StoryState],
) -> Dict[str, Any]:
    """Run the 3-step pipeline: manifest → image → sprites.

    Falls back to legacy single-call if any step fails.
    """
    try:
        # Step 1: Manifest + NEG
        manifest_data = await _generate_manifest(client, user_prompt)

        carried_over = manifest_data.get("carried_over_entities", [])
        if not isinstance(carried_over, list):
            carried_over = []

        # Step 2: Reference illustration
        scene_image_bytes = await _generate_scene_image(client, manifest_data)

        # Step 3: Sprite code (works even if image is None — falls back to text-only)
        sprite_code = await _generate_sprite_code(
            client, manifest_data, scene_image_bytes, carried_over, story_state
        )

        # Validate and assemble final result
        manifest = SceneManifest.model_validate(manifest_data["manifest"])
        neg = NEG.model_validate(manifest_data["neg"])

        return {
            "narrative_text": manifest_data.get("narrative_text", ""),
            "branch_summary": manifest_data.get("branch_summary", ""),
            "scene_description": manifest_data.get("scene_description", ""),
            "manifest": manifest.model_dump(),
            "neg": neg.model_dump(),
            "sprite_code": sprite_code,
            "carried_over_entities": carried_over,
            "_reference_image_bytes": scene_image_bytes,
        }

    except Exception as exc:
        logger.warning(
            "Reference image pipeline failed, falling back to legacy: %s", exc
        )
        return await _generate_scene_legacy(client, user_prompt)
