"""Discrepancy assessment module.

Two-pass Gemini assessment per utterance:
  Pass 1 (Correction): detect factual errors in the child's utterance.
  Pass 2 (Enrichment): identify MISL scaffolding opportunities.

The orchestrator function assess_utterance() runs both passes sequentially
and merges results into a single AssessmentResponse with a unified
discrepancies list (corrections first, then suggestions).

Model: Gemini 3 Flash (gemini-3-flash-preview)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from google import genai
from google.genai import types

from config.misl import MACROSTRUCTURE, MICROSTRUCTURE
from src.models.assessment import (
    AssessmentResponse,
    Discrepancy,
    FactualError,
    MISLOpportunity,
)
from src.models.scene import SceneManifest
from src.models.student_profile import MISLDifficultyProfile
from src.narration.transcription import transcribe_audio
from src.generation.prompts.assessment_prompt import (
    CORRECTION_SYSTEM_PROMPT,
    CORRECTION_USER_PROMPT_TEMPLATE,
    ENRICHMENT_SYSTEM_PROMPT,
    ENRICHMENT_USER_PROMPT_TEMPLATE,
)
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
ASSESSMENT_TIMEOUT = 30
MAX_RETRIES = 2


def _build_misl_taxonomy() -> str:
    """Build a human-readable MISL taxonomy text for the prompt."""
    lines = ["## Macrostructure (7 elements, scores 0-3)\n"]
    for key, info in MACROSTRUCTURE.items():
        lines.append(f"**{info['label']}** (`{key}`)")
        for score, desc in info["scores"].items():
            lines.append(f"  {score}: {desc}")
        lines.append("")

    lines.append("## Microstructure (8 elements, scores 0-3)\n")
    for key, info in MICROSTRUCTURE.items():
        lines.append(f"**{info['label']}** (`{key}`)")
        for score, desc in info["scores"].items():
            lines.append(f"  {score}: {desc}")
        lines.append("")

    return "\n".join(lines)


def _build_names_text(
    manifest: SceneManifest,
    character_names: Optional[Dict[str, str]],
) -> str:
    if character_names:
        names_lines = []
        for eid, name in character_names.items():
            ent = manifest.get_entity(eid)
            etype = ent.type if ent else eid
            names_lines.append(f'- {eid} is named "{name}" (type: {etype})')
        return "\n".join(names_lines)
    return "(No character names given yet.)"


def _build_story_text(story_so_far: List[str]) -> str:
    if story_so_far:
        return "\n".join(
            f'{i+1}. "{utt}"' for i, utt in enumerate(story_so_far)
        )
    return "(No accepted utterances yet — this is the first.)"


async def _gemini_call(
    client: genai.Client,
    system_prompt: str,
    user_prompt: str,
    thinking_budget: int = 512,
) -> Dict:
    """Make a single Gemini call with retries. Returns parsed JSON dict."""
    last_exc: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=MODEL_ID,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        thinking_config=types.ThinkingConfig(
                            thinking_budget=thinking_budget
                        ),
                        temperature=1.0,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=ASSESSMENT_TIMEOUT,
            )
            return _extract_json(_get_response_text(response))

        except asyncio.TimeoutError:
            logger.warning(
                "[assessment] Attempt %d/%d timed out after %ds",
                attempt, MAX_RETRIES, ASSESSMENT_TIMEOUT,
            )
            last_exc = asyncio.TimeoutError(
                f"Assessment timed out after {ASSESSMENT_TIMEOUT}s"
            )
        except Exception as exc:
            logger.warning(
                "[assessment] Attempt %d/%d failed (%s): %s",
                attempt, MAX_RETRIES, type(exc).__name__, exc or "no details",
            )
            last_exc = exc

    raise last_exc or RuntimeError("All assessment retries exhausted")


# ---------------------------------------------------------------------------
# Pass 1: Correction
# ---------------------------------------------------------------------------

def _load_correction_intents() -> str:
    """Load correction_intent from all animation grammar JSONs."""
    grammar_dir = Path(__file__).parent.parent.parent / "animations" / "grammar"
    lines = []
    for f in sorted(grammar_dir.glob("*.json")):
        try:
            d = json.load(open(f))
            intent = d.get("correction_intent")
            if intent:
                targets = d.get("target_type", ["entity"])
                lines.append(f"- {d['id']} ({d['name']}) [targets: {', '.join(targets)}]: {intent}")
        except Exception:
            pass
    return "\n".join(lines) if lines else "(none)"


_CORRECTION_INTENTS_TEXT: Optional[str] = None


def _get_correction_intents() -> str:
    global _CORRECTION_INTENTS_TEXT
    if _CORRECTION_INTENTS_TEXT is None:
        _CORRECTION_INTENTS_TEXT = _load_correction_intents()
    return _CORRECTION_INTENTS_TEXT


async def assess_corrections(
    api_key: str,
    manifest: SceneManifest,
    utterance_text: str,
    story_so_far: List[str],
    character_names: Optional[Dict[str, str]] = None,
    scene_data: Optional[Dict[str, Any]] = None,
) -> List[Discrepancy]:
    """Pass 1: Detect all mistakes (grammatical and narrative).

    Returns a list of Discrepancy objects with pass_type="correction",
    ordered by decreasing severity, with animation_id from grammar.
    """
    client = genai.Client(api_key=api_key)

    # Use raw scene_data (full story def) when available for richer context
    manifest_json = json.dumps(scene_data, indent=2) if scene_data else json.dumps(manifest.model_dump(), indent=2)
    story_text = _build_story_text(story_so_far)
    names_text = _build_names_text(manifest, character_names)

    # Inject correction intents into system prompt
    system_prompt = CORRECTION_SYSTEM_PROMPT.format(
        correction_intents=_get_correction_intents(),
    )

    user_prompt = CORRECTION_USER_PROMPT_TEMPLATE.format(
        manifest_json=manifest_json,
        utterance_text=utterance_text,
        story_so_far=story_text,
        character_names=names_text,
    )

    data = await _gemini_call(client, system_prompt, user_prompt)

    discrepancies: List[Discrepancy] = []
    raw_items = data.get("discrepancies", [])
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                discrepancies.append(Discrepancy(
                    pass_type="correction",
                    type=item.get("type", "Identity"),
                    target_entities=item.get("target_entities", []),
                    misl_elements=item.get("misl_elements", []),
                    description=item.get("description", ""),
                    animation_id=item.get("animation_id"),
                ))

    # Extract name assignments (child giving names to entities)
    name_assignments: List[Dict[str, str]] = []
    raw_names = data.get("name_assignments", [])
    if isinstance(raw_names, list):
        for na in raw_names:
            if isinstance(na, dict) and na.get("entity_id") and na.get("name"):
                name_assignments.append({"entity_id": na["entity_id"], "name": na["name"]})

    if name_assignments:
        logger.info("[assessment:correction] Name assignments detected: %s", name_assignments)

    logger.info("[assessment:correction] Found %d correction discrepancies",
                len(discrepancies))
    return discrepancies, name_assignments


# ---------------------------------------------------------------------------
# Pass 2: Enrichment
# ---------------------------------------------------------------------------

def _load_suggestion_intents() -> str:
    """Load suggestion_intent from all animation grammar JSONs."""
    grammar_dir = Path(__file__).parent.parent.parent / "animations" / "grammar"
    lines = []
    for f in sorted(grammar_dir.glob("*.json")):
        try:
            d = json.load(open(f))
            intent = d.get("suggestion_intent")
            if intent:
                targets = d.get("target_type", ["entity"])
                lines.append(f"- {d['id']} ({d['name']}) [targets: {', '.join(targets)}]: {intent}")
        except Exception:
            pass
    return "\n".join(lines) if lines else "(none)"


_SUGGESTION_INTENTS_TEXT: Optional[str] = None


def _get_suggestion_intents() -> str:
    global _SUGGESTION_INTENTS_TEXT
    if _SUGGESTION_INTENTS_TEXT is None:
        _SUGGESTION_INTENTS_TEXT = _load_suggestion_intents()
    return _SUGGESTION_INTENTS_TEXT


async def assess_enrichment(
    api_key: str,
    manifest: SceneManifest,
    utterance_text: str,
    story_so_far: List[str],
    misl_already_suggested: List[str],
    misl_difficulty_profile: MISLDifficultyProfile,
    character_names: Optional[Dict[str, str]] = None,
    correction_results: Optional[List[Discrepancy]] = None,
    scene_data: Optional[Dict[str, Any]] = None,
) -> List[Discrepancy]:
    """Pass 2: Identify enrichment opportunities.

    Returns a list of Discrepancy objects with pass_type="suggestion",
    each with an animation_id from the grammar.
    """
    client = genai.Client(api_key=api_key)

    manifest_json = json.dumps(scene_data, indent=2) if scene_data else json.dumps(manifest.model_dump(), indent=2)
    misl_taxonomy = _build_misl_taxonomy()
    story_text = _build_story_text(story_so_far)
    names_text = _build_names_text(manifest, character_names)

    suggested_text = (
        ", ".join(misl_already_suggested) if misl_already_suggested
        else "(None yet.)"
    )
    difficulty_text = misl_difficulty_profile.to_prompt_context()

    # Inject suggestion intents into system prompt
    system_prompt = ENRICHMENT_SYSTEM_PROMPT.format(
        suggestion_intents=_get_suggestion_intents(),
    )

    user_prompt = ENRICHMENT_USER_PROMPT_TEMPLATE.format(
        manifest_json=manifest_json,
        misl_taxonomy=misl_taxonomy,
        utterance_text=utterance_text,
        story_so_far=story_text,
        misl_already_suggested=suggested_text,
        misl_difficulty_profile=difficulty_text,
        character_names=names_text,
    )

    data = await _gemini_call(client, system_prompt, user_prompt)

    discrepancies: List[Discrepancy] = []
    raw_items = data.get("discrepancies", [])
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                discrepancies.append(Discrepancy(
                    pass_type="suggestion",
                    type=item.get("type", "Discourse"),
                    target_entities=item.get("target_entities", []),
                    misl_elements=item.get("misl_elements", []),
                    description=item.get("description", ""),
                    animation_id=item.get("animation_id"),
                ))

    logger.info("[assessment:enrichment] Found %d enrichment discrepancies",
                len(discrepancies))
    return discrepancies


# ---------------------------------------------------------------------------
# Orchestrator: two-pass assess_utterance
# ---------------------------------------------------------------------------

async def assess_resolution(
    api_key: str,
    utterance_text: str,
    previous_discrepancy: Optional[Discrepancy] = None,
    scene_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Check if the child's new utterance resolves a previous correction/suggestion.

    Returns {"resolved": True/False, "animation_id": "I1"} or None if no previous.
    """
    if not previous_discrepancy or not utterance_text:
        return None

    client = genai.Client(api_key=api_key)

    scene_context = ""
    if scene_data:
        scene_context = f"\n\n# Scene context\n```json\n{json.dumps(scene_data, indent=2)}\n```"

    system_prompt = (
        "You check whether a child's new utterance addresses a previous "
        "correction or suggestion about a scene they are narrating. "
        "Return ONLY valid JSON:\n"
        '{"resolved": true or false}\n'
        "resolved=true means the child incorporated the feedback. "
        "Be generous: if they made a reasonable attempt, count it as resolved."
    )

    user_prompt = (
        f"Previous feedback (animation {previous_discrepancy.animation_id or 'unknown'}, "
        f"type {previous_discrepancy.pass_type}):\n"
        f'"{previous_discrepancy.description}"\n'
        f"Targets: {previous_discrepancy.target_entities}"
        f"{scene_context}\n\n"
        f'Child\'s new utterance:\n"{utterance_text}"\n\n'
        f"Did the child address the feedback?"
    )

    try:
        data = await _gemini_call(client, system_prompt, user_prompt)
        resolved = data.get("resolved", False)
        logger.info(
            "\033[93m[RESOLUTION]\033[0m %s (animation %s) → %s",
            previous_discrepancy.pass_type,
            previous_discrepancy.animation_id,
            "resolved" if resolved else "not resolved",
        )
        return {
            "resolved": resolved,
            "animation_id": previous_discrepancy.animation_id,
            "pass_type": previous_discrepancy.pass_type,
        }
    except Exception as exc:
        logger.error("[assessment] Resolution check failed: %s", exc)
        return None


async def assess_utterance(
    api_key: str,
    manifest: SceneManifest,
    utterance_text: str,
    story_so_far: List[str],
    misl_already_suggested: List[str],
    misl_difficulty_profile: MISLDifficultyProfile,
    character_names: Optional[Dict[str, str]] = None,
    audio_bytes: Optional[bytes] = None,
    narration_history: Optional[List[str]] = None,
    narrative_text: str = "",
    scene_data: Optional[Dict[str, Any]] = None,
    previous_discrepancy: Optional[Discrepancy] = None,
) -> AssessmentResponse:
    """Two-pass assessment of a child's utterance.

    If audio_bytes is provided, transcription is performed first using
    Gemini 3 Flash, then the text is assessed.

    Pass 1: Detect factual errors (corrections).
    Pass 2: Identify MISL scaffolding opportunities (enrichment).

    Results are merged into a single AssessmentResponse with:
    - transcription: the transcribed text (from audio or utterance_text)
    - discrepancies: unified list, corrections first then suggestions
    - factual_errors: backward-compatible list from Pass 1
    - misl_opportunities: backward-compatible list from Pass 2
    - utterance_is_acceptable: False if corrections exist, True otherwise

    Args:
        api_key: Gemini API key.
        manifest: The current scene's manifest.
        utterance_text: The transcribed child utterance (used if no audio_bytes).
        story_so_far: List of accepted utterance texts in this scene.
        misl_already_suggested: MISL dimensions already prompted this scene.
        misl_difficulty_profile: Persistent MISL difficulty data.
        character_names: Map of entity_id → child-given name.
        audio_bytes: Raw audio bytes; if provided, transcription is done here.
        narration_history: Previous utterance transcriptions (for transcription context).
        narrative_text: Scene narrative text (for transcription context).

    Returns:
        AssessmentResponse with two-pass results including transcription.
    """
    # --- Step 0: Transcription (if audio provided) ---
    if audio_bytes is not None:
        utterance_text = await transcribe_audio(
            api_key=api_key,
            audio_bytes=audio_bytes,
            narration_history=narration_history,
            narrative_text=narrative_text,
        )
        logger.info("\033[92m[TRANSCRIPTION]\033[0m %s", utterance_text)

    if not utterance_text:
        return AssessmentResponse(transcription="")

    # --- Run correction, enrichment, and resolution in parallel ---
    async def _run_corrections():
        try:
            return await assess_corrections(
                api_key=api_key,
                manifest=manifest,
                utterance_text=utterance_text,
                story_so_far=story_so_far,
                character_names=character_names,
                scene_data=scene_data,
            )
        except Exception as exc:
            logger.error("[assessment] Correction pass failed: %s", exc)
            return [], []

    async def _run_enrichment():
        try:
            return await assess_enrichment(
                api_key=api_key,
                manifest=manifest,
                utterance_text=utterance_text,
                story_so_far=story_so_far,
                misl_already_suggested=misl_already_suggested,
                misl_difficulty_profile=misl_difficulty_profile,
                character_names=character_names,
                correction_results=None,  # not available in parallel
                scene_data=scene_data,
            )
        except Exception as exc:
            logger.error("[assessment] Enrichment pass failed: %s", exc)
            return []

    async def _run_resolution():
        return await assess_resolution(
            api_key=api_key,
            utterance_text=utterance_text,
            previous_discrepancy=previous_discrepancy,
            scene_data=scene_data,
        )

    (corrections_result, suggestions, resolution_result) = await asyncio.gather(
        _run_corrections(), _run_enrichment(), _run_resolution()
    )
    corrections, name_assignments = corrections_result

    # Register detected name assignments
    if name_assignments and character_names is not None:
        for na in name_assignments:
            character_names[na["entity_id"]] = na["name"]
            logger.info("[assessment] Registered name: %s → %s", na["entity_id"], na["name"])


    # --- Merge into unified discrepancies list (corrections first) ---
    discrepancies = corrections + suggestions

    # --- Build backward-compatible fields ---
    factual_errors: List[FactualError] = []
    for d in corrections:
        factual_errors.append(FactualError(
            utterance_fragment="",
            manifest_ref=", ".join(d.target_entities),
            explanation=d.description,
        ))

    misl_opportunities: List[MISLOpportunity] = []
    for d in suggestions:
        misl_opportunities.append(MISLOpportunity(
            dimension=d.misl_elements[0] if d.misl_elements else d.type,
            manifest_elements=d.target_entities,
            suggestion=d.description,
        ))

    acceptable = len(corrections) == 0

    result = AssessmentResponse(
        transcription=utterance_text,
        factual_errors=factual_errors,
        misl_opportunities=misl_opportunities,
        discrepancies=discrepancies,
        utterance_is_acceptable=acceptable,
        name_assignments=name_assignments,
        resolution=resolution_result,
    )

    logger.info(
        "[assessment] corrections=%d suggestions=%d acceptable=%s",
        len(corrections), len(suggestions), acceptable,
    )

    return result
