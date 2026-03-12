"""Tellimation module — generates animations from the animation grammar.

Given a target entity and a misl_element from the discrepancy assessment,
the LLM selects one of 4 modes:
  A) use_default   — apply template with default params
  B) adjust_params  — tune template parameters
  C) sequence       — chain 2-3 animations
  D) custom_code    — generate new JS code (last resort)

Model: Gemini 3 Flash (gemini-3-flash-preview)

Fallback: if LLM generation fails, returns Mode A with the first eligible template.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from src.models.scene import SceneManifest
from src.models.student_profile import StudentProfile
from src.generation.prompts.tellimation_prompt import (
    TELLIMATION_SYSTEM_PROMPT,
    TELLIMATION_USER_PROMPT_TEMPLATE,
)
from config.misl import (
    MISL_TO_ANIMATIONS,
    ANIMATION_ID_TO_TEMPLATE,
    ANIMATION_PARAMS,
    build_params_prompt,
)
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
TELLIMATION_TIMEOUT = 30
MAX_RETRIES = 2

# Default durations per template (matching JS registration)
_DEFAULT_DURATIONS: Dict[str, int] = {
    "spotlight": 3000, "nametag": 2000, "color_pop": 3000, "emanation": 2500,
    "motion_lines": 2000, "anticipation": 2000, "reveal": 2500, "stamp": 3000,
    "flashback": 3000, "timelapse": 4000, "magnetism": 2500, "repel": 2000,
    "causal_push": 2000, "sequential_glow": 3000, "disintegration": 2000,
    "ghost_outline": 2500, "speech_bubble": 1500, "thought_bubble": 1500,
    "alert": 1200, "interjection": 2000, "decomposition": 2500,
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


def _format_recent_decisions(student_profile: StudentProfile) -> str:
    """Format recent animation decisions for the LLM context."""
    decisions = getattr(student_profile, "animation_decisions", [])
    if not decisions:
        return "(No previous decisions.)"

    # Show last 10, most recent first
    recent = list(reversed(decisions[-10:]))
    lines = []
    for d in recent:
        outcome = d.outcome if hasattr(d, "outcome") else d.get("outcome", "pending")
        mode = d.mode if hasattr(d, "mode") else d.get("mode", "?")
        template = d.template if hasattr(d, "template") else d.get("template", "?")
        misl = d.misl_element if hasattr(d, "misl_element") else d.get("misl_element", "?")
        target = d.target_id if hasattr(d, "target_id") else d.get("target_id", "?")
        lines.append(f"- [{outcome}] mode={mode}, template={template}, "
                      f"misl={misl}, target={target}")
    return "\n".join(lines)


def _validate_params(template: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and clamp params against ANIMATION_PARAMS schema."""
    schema = ANIMATION_PARAMS.get(template, {})
    validated: Dict[str, Any] = {}
    for key, value in params.items():
        if key == "entityPrefix":
            continue  # injected server-side
        spec = schema.get(key)
        if spec is None:
            continue  # unknown param, skip
        ptype = spec["type"]
        if ptype == "float":
            try:
                v = float(value)
                v = max(spec.get("min", v), min(spec.get("max", v), v))
                validated[key] = v
            except (TypeError, ValueError):
                pass
        elif ptype == "int":
            try:
                v = int(value)
                v = max(spec.get("min", v), min(spec.get("max", v), v))
                validated[key] = v
            except (TypeError, ValueError):
                pass
        elif ptype == "enum":
            if value in spec.get("values", []):
                validated[key] = value
        elif ptype == "rgb":
            if isinstance(value, list) and len(value) == 3:
                validated[key] = [max(0, min(255, int(c))) for c in value]
        elif ptype == "bool":
            validated[key] = bool(value)
        elif ptype in ("string", "string_array"):
            validated[key] = value
    return validated


def _build_fallback(
    target_id: str,
    misl_element: str,
) -> Dict[str, Any]:
    """Build a Mode A fallback using the first eligible template."""
    eligible = MISL_TO_ANIMATIONS.get(misl_element, [])
    if eligible:
        aid = eligible[0]
        template = ANIMATION_ID_TO_TEMPLATE.get(aid, "spotlight")
    else:
        aid = "I1_spotlight"
        template = "spotlight"

    duration_ms = _DEFAULT_DURATIONS.get(template, 1500)

    logger.info("[tellimation] Using fallback Mode A: template=%s for %s (misl=%s)",
                template, target_id, misl_element)

    return {
        "mode": "use_default",
        "animation_id": aid,
        "template": template,
        "params": {},
        "duration_ms": duration_ms,
        "steps": [],
        "code": "",
        "text_overlays": [],
    }


def _parse_decision(data: Dict[str, Any], target_id: str) -> Dict[str, Any]:
    """Parse and validate the LLM's 4-mode decision."""
    mode = data.get("mode", "use_default")
    animation_id = data.get("animation_id", "custom")
    template = data.get("template", "")
    raw_params = data.get("params", {})
    if not isinstance(raw_params, dict):
        raw_params = {}
    duration_ms = data.get("duration_ms", 1500)
    if not isinstance(duration_ms, (int, float)):
        duration_ms = 1500
    duration_ms = int(duration_ms)
    text_overlays = data.get("text_overlays", [])
    if not isinstance(text_overlays, list):
        text_overlays = []

    result: Dict[str, Any] = {
        "mode": mode,
        "animation_id": animation_id,
        "template": template,
        "template_name": "",
        "params": {},
        "duration_ms": duration_ms,
        "steps": [],
        "code": "",
        "text_overlays": text_overlays,
    }

    if mode == "use_default":
        if not template:
            raise ValueError("use_default requires 'template'")
        if template not in ANIMATION_PARAMS and template not in _DEFAULT_DURATIONS:
            raise ValueError(f"Unknown template '{template}'")
        if not duration_ms:
            result["duration_ms"] = _DEFAULT_DURATIONS.get(template, 1500)

    elif mode == "adjust_params":
        if not template:
            raise ValueError("adjust_params requires 'template'")
        result["params"] = _validate_params(template, raw_params)
        if not duration_ms:
            result["duration_ms"] = _DEFAULT_DURATIONS.get(template, 1500)

    elif mode == "sequence":
        steps = data.get("steps", [])
        if not isinstance(steps, list) or len(steps) < 2:
            raise ValueError("sequence requires 'steps' with at least 2 entries")
        if len(steps) > 3:
            steps = steps[:3]
        validated_steps = []
        for step in steps:
            st = step.get("template", "")
            sp = step.get("params", {})
            sd = step.get("duration_ms", 1500)
            if not st:
                raise ValueError("Each sequence step requires 'template'")
            validated_sp = _validate_params(st, sp) if isinstance(sp, dict) else {}
            validated_steps.append({
                "template": st,
                "params": validated_sp,
                "duration_ms": int(sd) if isinstance(sd, (int, float)) else 1500,
            })
        result["steps"] = validated_steps
        # Total duration is sum of steps
        result["duration_ms"] = sum(s["duration_ms"] for s in validated_steps)

    elif mode == "custom_code":
        code = data.get("code", "")
        if not isinstance(code, str) or "animate" not in code:
            raise ValueError("custom_code requires 'code' with animate function")
        result["code"] = code
        # Extract or derive template_name for registration/reuse
        tname = data.get("template_name", "")
        if not tname and animation_id.startswith("custom_"):
            tname = animation_id[len("custom_"):]
        result["template_name"] = tname or "custom_anim"

    else:
        raise ValueError(f"Unknown mode '{mode}'")

    return result


async def generate_tellimation(
    api_key: str,
    sprite_code: Dict[str, Any],
    manifest: SceneManifest,
    student_profile: StudentProfile,
    target_id: str,
    misl_element: str,
    problematic_segment: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a tellimation animation decision for a target entity.

    Returns a dict with keys:
        mode, animation_id, template, params, duration_ms,
        steps (mode C), code (mode D), text_overlays.
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
    recent_decisions = _format_recent_decisions(student_profile)

    # Inject params reference into system prompt
    params_ref = build_params_prompt()
    system_prompt = TELLIMATION_SYSTEM_PROMPT.format(params_reference=params_ref)

    user_prompt = TELLIMATION_USER_PROMPT_TEMPLATE.format(
        target_id=target_id,
        misl_element=misl_element,
        eligible_animations=eligible_text,
        entity_details=entity_details,
        sprite_info=sprite_info,
        scene_context=scene_context,
        student_profile=profile_text,
        animation_effectiveness=effectiveness,
        recent_decisions=recent_decisions,
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
                        system_instruction=system_prompt,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                        temperature=0.7,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=TELLIMATION_TIMEOUT,
            )

            data = _extract_json(_get_response_text(response))
            result = _parse_decision(data, target_id)

            logger.info(
                "[tellimation] Generated mode=%s template=%s id=%s for %s (%d ms)",
                result["mode"], result.get("template", "-"),
                result["animation_id"], target_id, result["duration_ms"],
            )

            student_profile.record_animation(
                entity_id=target_id,
                error_type=misl_element,
                animation_type=result["animation_id"],
            )

            return result

        except asyncio.TimeoutError:
            logger.warning("[tellimation] Attempt %d/%d timed out after %ds",
                           attempt, MAX_RETRIES, TELLIMATION_TIMEOUT)
            last_exc = asyncio.TimeoutError()
        except Exception as exc:
            logger.warning("[tellimation] Attempt %d/%d failed (%s): %s",
                           attempt, MAX_RETRIES,
                           type(exc).__name__, exc or "no details")
            last_exc = exc

    # All retries failed — use template-based fallback
    logger.warning("[tellimation] All %d attempts failed (%s), using fallback",
                   MAX_RETRIES, last_exc)

    result = _build_fallback(target_id, misl_element)

    student_profile.record_animation(
        entity_id=target_id,
        error_type=misl_element,
        animation_type=result["animation_id"],
    )

    return result
