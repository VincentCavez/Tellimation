"""Tellimation module — generates animations from the animation grammar.

Given a target entity and a misl_element from the discrepancy assessment,
the LLM selects one of 4 modes:
  A) use_default   — apply template with default params
  B) adjust_params  — tune template parameters
  C) sequence       — chain 2-3 animations
  D) custom_code    — generate new JS code (last resort)

Also provides generate_invocation_array() which takes a full discrepancy list
and produces a structured InvocationArray (corrections first, then suggestions).

Model: Gemini 3 Flash (gemini-3-flash-preview)

Fallback: if LLM generation fails, returns Mode A with the first eligible template.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from src.models.assessment import Discrepancy
from src.models.invocation import AnimationInvocation, InvocationArray
from src.models.scene import SceneManifest
from src.models.student_profile import StudentProfile
from src.generation.prompts.tellimation_prompt import (
    TELLIMATION_SYSTEM_PROMPT,
    TELLIMATION_USER_PROMPT_TEMPLATE,
)
from animations.grammar import get_animation, get_animations_by_category, get_animations_by_mode
from config.misl import (
    MISL_TO_ANIMATIONS,
    ANIMATION_ID_TO_TEMPLATE,
    ANIMATION_PARAMS,
    COUNT_ANIMATIONS,
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
    "motion_lines": 2000, "flip": 2000, "reveal": 2500, "stamp": 3000,
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
                        temperature=1.0,
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


# ---------------------------------------------------------------------------
# Category → MISL element mapping for discrepancy routing
# ---------------------------------------------------------------------------

_CATEGORY_TO_MISL: Dict[str, str] = {
    "Identity": "character",
    "Property": "elaborated_noun_phrases",
    "Action": "action",
    "Space": "setting",
    "Time": "tense",
    "Relation": "coordinating_conjunctions",
    "Count": "character",
    "Discourse": "internal_response",
}

# Priority order for sorting within each pass
_CATEGORY_PRIORITY: Dict[str, int] = {
    "Identity": 0,
    "Count": 1,
    "Property": 2,
    "Action": 3,
    "Space": 4,
    "Time": 5,
    "Relation": 6,
    "Discourse": 7,
}


def _select_animation_for_discrepancy(
    discrepancy: Discrepancy,
    student_profile: StudentProfile,
) -> Optional[str]:
    """Select the best animation ID for a discrepancy.

    Deterministic mapping based on category and context:
    - Identity → spotlight (I1): highlight entity to prompt naming
    - Property → color_pop (P1): emphasize visual attributes
    - Action → motion_lines (A1): draw attention to what entity is doing
    - Space (with entity targets) → stamp (S2): show where entity is
    - Space (setting, no specific entity) → reveal (S1): reveal the whole scene
    - Time → flashback (T1): temporal context
    - Relation → magnetism (R1): show connection between entities
    - Count → sequential_glow (C1): highlight entities for counting
    - Discourse → speech_bubble (D1): prompt dialogue/internal response
    """
    category = discrepancy.type
    is_correction = discrepancy.pass_type == "correction"
    has_targets = bool(discrepancy.target_entities)
    desc_lower = discrepancy.description.lower()

    # Use the animation_id provided by the LLM
    _ID_TO_TEMPLATE = {
        "I1": "I1_spotlight", "I2": "I2_nametag",
        "P1": "P1_color_pop", "P2": "P2_emanation",
        "A1": "A1_motion_line", "A2": "A2_flip",
        "S1": "S1_reveal", "S2": "S2_stamp",
        "T1": "T1_flashback", "T2": "T2_timelapse",
        "R1": "R1_magnetism", "R2": "R2_repel", "R3": "R3_causal_push",
        "C1": "C1_sequential_glow", "C2": "C2_disintegration", "C3": "C3_ghost_outline",
        "D1": "D1_speech_bubble", "D2": "D2_thought_bubble", "D3": "D3_alert", "D4": "D4_interjection",
    }

    if discrepancy.animation_id:
        aid = discrepancy.animation_id.upper()
        # Handle P2 variants: P2_SHAME → P2_emanation (with particleType extracted)
        if aid.startswith("P2_") and aid != "P2":
            return "P2_emanation"
        if aid in _ID_TO_TEMPLATE:
            return _ID_TO_TEMPLATE[aid]

    # No animation_id → no animation
    logger.warning("[tellimation] No animation_id for discrepancy: %s", discrepancy.description)
    return None


def select_discrepancy(
    corrections: List[Discrepancy],
    suggestions: List[Discrepancy],
    study_log_entries: List[Dict[str, Any]],
) -> Optional[Discrepancy]:
    """Deterministic selection of which discrepancy to animate.

    Priority: corrections over suggestions.
    Within the list (ordered by severity/relevance from LLM):
    - Pick the first item whose animation_id was NOT resolved last time.
    - If all were resolved last time, pick the first item anyway.

    Args:
        corrections: correction discrepancies, ordered by severity (most severe first).
        suggestions: suggestion discrepancies, ordered by relevance (most relevant first).
        study_log_entries: flat list of all log entries for this participant
            (from study_log.json or training_log.json).

    Returns:
        The chosen Discrepancy, or None if both lists are empty.
    """
    candidates = corrections if corrections else suggestions
    if not candidates:
        return None

    # Build map: animation_id → last resolved status from logs
    last_resolved: Dict[str, bool] = {}
    for entry in study_log_entries:
        if entry.get("event") == "resolution" and entry.get("animation_id"):
            last_resolved[entry["animation_id"]] = entry.get("resolved", False)

    # Try to find first candidate whose last resolution is NOT true
    for disc in candidates:
        aid = disc.animation_id
        if aid is None:
            continue
        if aid not in last_resolved or not last_resolved[aid]:
            # Never seen or last time was not resolved → pick this one
            return disc

    # All candidates were resolved last time → pick the first one
    return candidates[0]


def load_animation_params(
    animation_id: str,
    study_log_entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Load animation parameters from grammar JSON.

    If the animation was played before and last resolution was False,
    accentuate/vary parameters to make the animation more noticeable.
    Otherwise, use defaults.

    Args:
        animation_id: e.g. "I1_spotlight" or "I1"
        study_log_entries: flat list of all log entries for this participant.

    Returns:
        Dict of parameter overrides to pass to the animation.
    """
    import random

    # Extract the short ID (e.g. "I1" from "I1_spotlight")
    short_id = animation_id.split("_")[0].upper()

    # Handle P2 variants: "P2_shame" → short_id="P2", variant="shame"
    p2_variant = None
    if short_id == "P2" and "_" in animation_id:
        parts = animation_id.split("_", 1)
        if len(parts) == 2 and parts[1].lower() in ("shame", "cold", "great", "love", "anger", "fear"):
            p2_variant = parts[1].lower()

    # Load grammar JSON
    grammar_dir = Path(__file__).parent.parent.parent / "animations" / "grammar"
    grammar_path = grammar_dir / f"{short_id}.json"
    if not grammar_path.exists():
        return {}

    grammar = json.load(open(grammar_path))
    parameters = grammar.get("parameters", [])
    if not parameters:
        return {}

    # Check last resolution for this animation
    last_resolved = None
    for entry in study_log_entries:
        if entry.get("event") == "resolution" and entry.get("animation_id") == short_id:
            last_resolved = entry.get("resolved", False)

    # Build param dict
    params: Dict[str, Any] = {}

    if last_resolved is None or last_resolved is True:
        # First time or last time resolved → use defaults
        for p in parameters:
            params[p["name"]] = p["default"]
        # Override particleType for P2 variants
        if p2_variant:
            params["particleType"] = p2_variant
    else:
        # Last time NOT resolved → accentuate parameters
        for p in parameters:
            default = p["default"]
            prange = p.get("range", [])
            ptype = p.get("type", "")

            if ptype == "float" and isinstance(default, (int, float)) and len(prange) == 2:
                lo, hi = prange
                # Push 40% toward the max, add small random variation
                accentuated = default + (hi - default) * 0.4
                variation = (hi - lo) * 0.1 * (random.random() - 0.5)
                params[p["name"]] = max(lo, min(hi, round(accentuated + variation, 3)))

            elif ptype == "int" and isinstance(default, int) and len(prange) == 2:
                lo, hi = prange
                accentuated = default + (hi - default) * 0.4
                variation = (hi - lo) * 0.1 * (random.random() - 0.5)
                params[p["name"]] = max(lo, min(hi, round(accentuated + variation)))

            elif ptype == "rgb_vary" and isinstance(default, list) and len(default) == 3:
                # Vary each RGB channel by ±30
                params[p["name"]] = [
                    max(0, min(255, ch + random.randint(-30, 30)))
                    for ch in default
                ]

            else:
                # Non-numeric (rgb, string list, etc.) → use default
                params[p["name"]] = default

        logger.info("[tellimation] Accentuated params for %s: %s", animation_id, params)

    # Override particleType for P2 variants
    if p2_variant:
        params["particleType"] = p2_variant

    return params


async def generate_invocation_array(
    api_key: str,
    sprite_code: Dict[str, Any],
    manifest: SceneManifest,
    student_profile: StudentProfile,
    discrepancies: List[Discrepancy],
) -> InvocationArray:
    """Generate a structured invocation array from a list of discrepancies.

    For each discrepancy:
    1. Select the best animation via the grammar loader
    2. Call generate_tellimation() for LLM-based parameterization
    3. Build an AnimationInvocation with the result

    Corrections come first in the sequence, then suggestions.
    Within each pass, ordered by category priority.

    Args:
        api_key: Gemini API key.
        sprite_code: Current scene sprite data.
        manifest: Scene manifest.
        student_profile: Child's profile with efficacy history.
        discrepancies: Unified discrepancy list from assessment.

    Returns:
        InvocationArray with ordered sequence of animations.
    """
    if not discrepancies:
        return InvocationArray(sequence=[])

    # Sort: corrections first, then suggestions; within each, by category priority
    sorted_disc = sorted(
        discrepancies,
        key=lambda d: (
            0 if d.pass_type == "correction" else 1,
            _CATEGORY_PRIORITY.get(d.type, 99),
        ),
    )

    sequence: List[AnimationInvocation] = []

    for disc in sorted_disc:
        # Determine target entity
        target_id = disc.target_entities[0] if disc.target_entities else ""
        if not target_id:
            logger.warning("[invocation] Skipping discrepancy with no target: %s",
                           disc.description)
            continue

        # Determine MISL element for the LLM call
        misl_element = _CATEGORY_TO_MISL.get(disc.type, "character")
        if disc.misl_elements:
            # Try to map MISL code to full MISL key
            code_to_key = _build_misl_code_to_key()
            for code in disc.misl_elements:
                if code in code_to_key:
                    misl_element = code_to_key[code]
                    break

        # Use animation_id from the discrepancy (set by LLM)
        aid = _select_animation_for_discrepancy(disc, student_profile)
        if aid:
            sequence.append(AnimationInvocation(
                animation_id=aid,
                targets=disc.target_entities,
                parameter_overrides={},
            ))

    # Hard cap: max 2 animations per invocation array
    sequence = sequence[:2]

    result = InvocationArray(sequence=sequence)
    logger.info("[invocation] Generated invocation array with %d animations",
                len(sequence))
    return result


def _build_misl_code_to_key() -> Dict[str, str]:
    """Build a mapping from MISL abbreviation codes to full MISL keys."""
    from config.misl import MACROSTRUCTURE, MICROSTRUCTURE

    code_map: Dict[str, str] = {}
    for key, info in MACROSTRUCTURE.items():
        label = info["label"]
        # Extract code from label like "Character (CH)" → "CH"
        if "(" in label and ")" in label:
            code = label.split("(")[1].split(")")[0]
            code_map[code] = key
    for key, info in MICROSTRUCTURE.items():
        label = info["label"]
        if "(" in label and ")" in label:
            code = label.split("(")[1].split(")")[0]
            code_map[code] = key
    return code_map
