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

# Type alias for flexibility
from typing import Any
from src.generation.utils import (
    extract_json as _extract_json,
    get_response_text as _get_response_text,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-flash-preview"
ASSESSMENT_TIMEOUT = 30
MAX_RETRIES = 2

# Cache: animation short ID → category (loaded from grammar JSONs)
_ANIM_ID_TO_CATEGORY: Optional[Dict[str, str]] = None


def _category_from_animation_id(animation_id: str) -> Optional[str]:
    """Derive the category from an animation ID using the grammar JSONs.

    E.g. "D1" → "Discourse", "P2c" → "Property", "I1" → "Identity".
    Returns None if the ID is unknown.
    """
    global _ANIM_ID_TO_CATEGORY
    if _ANIM_ID_TO_CATEGORY is None:
        _ANIM_ID_TO_CATEGORY = {}
        grammar_dir = Path(__file__).parent.parent.parent / "animations" / "grammar"
        for f in grammar_dir.glob("*.json"):
            try:
                d = json.load(open(f))
                _ANIM_ID_TO_CATEGORY[d["id"].upper()] = d["category"]
            except Exception:
                pass
    if not animation_id:
        return None
    short = animation_id.split("_")[0].upper()
    return _ANIM_ID_TO_CATEGORY.get(short)


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
    character_names: Optional[Dict[str, str]],
) -> str:
    if character_names:
        names_lines = []
        for eid, name in character_names.items():
            names_lines.append(f'- {eid} is named "{name}"')
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
    """Load correction_intent from all animation grammar JSONs, including misl_elements."""
    grammar_dir = Path(__file__).parent.parent.parent / "animations" / "grammar"
    lines = []
    for f in sorted(grammar_dir.glob("*.json")):
        try:
            d = json.load(open(f))
            intent = d.get("correction_intent")
            if intent:
                targets = d.get("target_type", ["entity"])
                misl = d.get("misl_elements", [])
                misl_str = f" [misl: {', '.join(misl)}]" if misl else ""
                lines.append(f"- {d['id']} ({d['name']}) [targets: {', '.join(targets)}]{misl_str}: {intent}")
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
    utterance_text: str,
    story_so_far: List[str],
    scene_description: str,
    character_names: Optional[Dict[str, str]] = None,
) -> "tuple[list[Discrepancy], list[dict[str, str]]]":
    """Pass 1: Detect all mistakes (grammatical and narrative).

    Args:
        api_key: Gemini API key.
        utterance_text: The child's transcribed utterance.
        story_so_far: All accepted phrases from the ENTIRE story (not just scene).
        scene_description: Detailed scene description (obligatory).
        character_names: entity_id → child-given name.

    Returns:
        Tuple of (discrepancies, name_assignments).
        Each Discrepancy has pass_type="correction", with animation_id and
        misl_elements identifying the precise MISL element concerned.
    """
    client = genai.Client(api_key=api_key)

    story_text = _build_story_text(story_so_far)
    names_text = _build_names_text(character_names)

    logger.info("[assessment:correction] scene_description length=%d, first 100 chars: %s",
                len(scene_description), scene_description[:100] if scene_description else "(EMPTY)")

    # Inject correction intents (with [misl: ...]) into system prompt
    system_prompt = CORRECTION_SYSTEM_PROMPT.format(
        correction_intents=_get_correction_intents(),
    )

    user_prompt = CORRECTION_USER_PROMPT_TEMPLATE.format(
        manifest_json=scene_description,
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
                # misl_element: Gemini returns the precise MISL code
                misl_el = item.get("misl_element", "")
                misl_elements = [misl_el] if misl_el else item.get("misl_elements", [])
                # Derive category from animation_id (don't trust Gemini's "type")
                aid = item.get("animation_id", "")
                category = _category_from_animation_id(aid) or item.get("type", "Identity")
                discrepancies.append(Discrepancy(
                    pass_type="correction",
                    type=category,
                    target_entities=item.get("target_entities", []),
                    misl_elements=misl_elements,
                    description=item.get("description", ""),
                    animation_id=aid,
                    correction_word=item.get("correction_word"),
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

def _load_suggestion_intents_all() -> List[Dict[str, Any]]:
    """Load all suggestion_intent records from grammar JSONs as structured data."""
    grammar_dir = Path(__file__).parent.parent.parent / "animations" / "grammar"
    records: List[Dict[str, Any]] = []
    for f in sorted(grammar_dir.glob("*.json")):
        try:
            d = json.load(open(f))
            intent = d.get("suggestion_intent")
            if intent:
                records.append({
                    "id": d["id"],
                    "name": d["name"],
                    "target_type": d.get("target_type", ["entity"]),
                    "misl_elements": d.get("misl_elements", []),
                    "suggestion_intent": intent,
                })
        except Exception:
            pass
    return records


_SUGGESTION_INTENTS_ALL: Optional[List[Dict[str, Any]]] = None


def _get_suggestion_intents_all() -> List[Dict[str, Any]]:
    global _SUGGESTION_INTENTS_ALL
    if _SUGGESTION_INTENTS_ALL is None:
        _SUGGESTION_INTENTS_ALL = _load_suggestion_intents_all()
    return _SUGGESTION_INTENTS_ALL


def _get_suggestion_intents() -> str:
    """Format ALL suggestion intents as a string for the system prompt."""
    records = _get_suggestion_intents_all()
    lines = []
    for r in records:
        targets = ", ".join(r["target_type"])
        misl = r["misl_elements"]
        misl_str = f" [misl: {', '.join(misl)}]" if misl else ""
        lines.append(f"- {r['id']} ({r['name']}) [targets: {targets}]{misl_str}: {r['suggestion_intent']}")
    return "\n".join(lines) if lines else "(none)"


def _get_filtered_suggestion_intents(misl_codes: List[str]) -> str:
    """Filter suggestion intents to only those whose misl_elements overlap with misl_codes."""
    records = _get_suggestion_intents_all()
    lines = []
    for r in records:
        if any(code in r["misl_elements"] for code in misl_codes):
            targets = ", ".join(r["target_type"])
            misl = r["misl_elements"]
            misl_str = f" [misl: {', '.join(misl)}]" if misl else ""
            lines.append(f"- {r['id']} ({r['name']}) [targets: {targets}]{misl_str}: {r['suggestion_intent']}")
    return "\n".join(lines) if lines else "(none)"


async def assess_enrichment(
    api_key: str,
    utterance_text: str,
    story_so_far: List[str],
    character_names: Optional[Dict[str, str]],
    misl_targets: Dict[str, Any],
    entities_in_scene: List[str],
    macro_selected: Optional[str] = None,
    micro_candidates: Optional[List[str]] = None,
) -> List[Discrepancy]:
    """Pass 2: Identify enrichment opportunities.

    Two modes:
    - **Macro mode** (macro_selected is set): Gemini produces ONE suggestion
      for the pre-selected MISL element. Receives only suggestion_intents
      whose misl_elements contain the selected code.
    - **Micro mode** (micro_candidates is set): Gemini picks ONE from the
      shuffled list. Receives suggestion_intents whose misl_elements overlap
      with any of the candidate codes.

    Args:
        api_key: Gemini API key.
        utterance_text: The child's transcribed utterance.
        story_so_far: All accepted phrases from the ENTIRE story.
        character_names: entity_id → child-given name.
        misl_targets: Scene's misl_targets dict (obligatory).
        entities_in_scene: List of entity IDs present in the scene.
        macro_selected: Single MISL code for macro mode, or None.
        micro_candidates: Shuffled list of MISL codes for micro mode, or None.

    Returns:
        List of Discrepancy objects (capped to 1).
    """
    from config.misl import MISL_CODE_TO_KEY
    from src.generation.prompts.assessment_prompt import (
        ENRICHMENT_MACRO_USER_PROMPT_TEMPLATE,
        ENRICHMENT_MICRO_USER_PROMPT_TEMPLATE,
    )

    client = genai.Client(api_key=api_key)

    story_text = _build_story_text(story_so_far)
    names_text = _build_names_text(character_names)
    entities_text = ", ".join(entities_in_scene) if entities_in_scene else "(none)"

    if macro_selected is not None:
        # ── Macro mode: single pre-selected element ──
        element_name = MISL_CODE_TO_KEY.get(macro_selected, macro_selected)
        # Filter suggestion_intents to only those relevant to this MISL code
        filtered_intents = _get_filtered_suggestion_intents([macro_selected])

        targets_for_el = ""
        macro_targets = misl_targets.get("macro", {})
        val = macro_targets.get(macro_selected)
        if isinstance(val, list):
            targets_for_el = ", ".join(str(v) for v in val)
        elif val:
            targets_for_el = str(val)

        system_prompt = ENRICHMENT_SYSTEM_PROMPT.format(
            suggestion_intents=filtered_intents,
        )
        user_prompt = ENRICHMENT_MACRO_USER_PROMPT_TEMPLATE.format(
            manifest_json=f"Entities in scene: {entities_text}",
            misl_element_code=macro_selected,
            misl_element_name=element_name,
            misl_targets_for_element=targets_for_el or "(see scene)",
            utterance_text=utterance_text,
            story_so_far=story_text,
            character_names=names_text,
        )
        logger.info("[assessment:enrichment] Macro mode: element=%s, intents=%d",
                     macro_selected, filtered_intents.count("\n") + 1)

    elif micro_candidates is not None and len(micro_candidates) > 0:
        # ── Micro mode: shuffled candidate list ──
        # Filter suggestion_intents to those overlapping with any candidate
        filtered_intents = _get_filtered_suggestion_intents(micro_candidates)

        candidates_text_lines = []
        for code in micro_candidates:
            name = MISL_CODE_TO_KEY.get(code, code)
            targets_for_el = ""
            micro_targets = misl_targets.get("micro", {})
            val = micro_targets.get(code)
            if isinstance(val, list):
                targets_for_el = ", ".join(str(v) for v in val)
            candidates_text_lines.append(
                f"- **{code}** ({name}): {targets_for_el or '(see scene)'}"
            )

        system_prompt = ENRICHMENT_SYSTEM_PROMPT.format(
            suggestion_intents=filtered_intents,
        )
        user_prompt = ENRICHMENT_MICRO_USER_PROMPT_TEMPLATE.format(
            manifest_json=f"Entities in scene: {entities_text}",
            micro_candidates_text="\n".join(candidates_text_lines),
            utterance_text=utterance_text,
            story_so_far=story_text,
            character_names=names_text,
        )
        logger.info("[assessment:enrichment] Micro mode: candidates=%s, intents=%d",
                     micro_candidates, filtered_intents.count("\n") + 1)

    else:
        logger.warning("[assessment:enrichment] Called with no macro_selected or micro_candidates — skipping")
        return []

    data = await _gemini_call(client, system_prompt, user_prompt)

    discrepancies: List[Discrepancy] = []
    raw_items = data.get("discrepancies", [])
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                misl_el = item.get("misl_element", "")
                misl_elements = [misl_el] if misl_el else item.get("misl_elements", [])
                aid = item.get("animation_id", "")
                category = _category_from_animation_id(aid) or item.get("type", "Discourse")
                discrepancies.append(Discrepancy(
                    pass_type="suggestion",
                    type=category,
                    target_entities=item.get("target_entities", []),
                    misl_elements=misl_elements,
                    description=item.get("description", ""),
                    animation_id=aid,
                ))

    # Cap at 1 suggestion
    if len(discrepancies) > 1:
        discrepancies = discrepancies[:1]

    logger.info("[assessment:enrichment] Found %d enrichment discrepancies",
                len(discrepancies))
    return discrepancies


# ---------------------------------------------------------------------------
# MISL element detection (lightweight call)
# ---------------------------------------------------------------------------

async def detect_misl_elements(
    api_key: str,
    utterance_text: str,
    scene_data: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Detect which MISL narrative elements are present in the child's utterance.

    Lightweight Gemini call that returns a list of MISL abbreviation codes
    (e.g. ["CH", "A", "ENP"]) found in the utterance.

    Args:
        api_key: Gemini API key.
        utterance_text: The child's transcribed utterance.
        scene_data: Optional scene manifest for context.

    Returns:
        List of MISL codes present in the utterance.
    """
    from config.misl import ALL_MISL_CODES, MISL_CODE_TO_KEY

    if not utterance_text:
        return []

    client = genai.Client(api_key=api_key)

    scene_context = ""
    if scene_data:
        scene_context = f"\n\n# Scene context\n```json\n{json.dumps(scene_data, indent=2)}\n```"

    codes_ref = "\n".join(
        f"- {code}: {MISL_CODE_TO_KEY[code]}"
        for code in ALL_MISL_CODES
    )

    system_prompt = (
        "You analyze a child's narrative utterance and identify which MISL "
        "(Monitoring Indicators of Scholarly Language) narrative elements are "
        "present. Return ONLY valid JSON.\n\n"
        "# MISL codes\n"
        f"{codes_ref}\n\n"
        "# Rules\n"
        "- CH: child mentions or refers to a character\n"
        "- S: child describes the setting (place or time)\n"
        "- IE: child describes an initiating event that starts an episode\n"
        "- A: child describes an action by a character\n"
        "- CO: child describes a consequence or outcome\n"
        "- IR: child expresses feelings, emotions, or internal states\n"
        "- P: child expresses a plan or intention\n"
        "- ENP: child uses elaborated noun phrases (adjectives, modifiers)\n"
        "- SC: child uses subordinating conjunctions (because, when, after...)\n"
        "- CC: child uses coordinating conjunctions (and, but, so...)\n"
        "- M: child uses mental verbs (thought, decided, wanted...)\n"
        "- L: child uses linguistic verbs (said, told, yelled...)\n"
        "- ADV: child uses adverbs (suddenly, slowly, very...)\n"
        "- G: grammaticality — score 1 if there are errors, omit if correct\n"
        "- T: tense — score 1 if there are tense changes, omit if consistent\n\n"
        'Return: {"misl_elements": ["CH", "A", ...]}'
    )

    user_prompt = (
        f'Child\'s utterance:\n"{utterance_text}"{scene_context}\n\n'
        "Which MISL elements are present in this utterance?"
    )

    try:
        data = await _gemini_call(client, system_prompt, user_prompt, thinking_budget=256)
        raw = data.get("misl_elements", [])
        # Validate: only keep known codes
        valid = [code for code in raw if code in ALL_MISL_CODES]
        logger.info("[assessment:misl_detect] Detected MISL elements: %s", valid)
        return valid
    except Exception as exc:
        logger.error("[assessment:misl_detect] Failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Orchestrator: two-pass assess_utterance
# ---------------------------------------------------------------------------

async def assess_resolution(
    api_key: str,
    utterance_text: str,
    previous_rationale: Optional[str] = None,
    scene_description: str = "",
) -> Optional[bool]:
    """Check if the child's new utterance resolves a previous correction/suggestion.

    Args:
        api_key: Gemini API key.
        utterance_text: The child's new utterance.
        previous_rationale: The .description of the previous Discrepancy.
        scene_description: The scene_description field from the scene JSON.

    Returns:
        True if resolved, False if not, None if no previous_rationale.
    """
    if not previous_rationale or not utterance_text:
        return None

    client = genai.Client(api_key=api_key)

    scene_context = ""
    if scene_description:
        scene_context = f"\n\n# Scene description\n{scene_description}"

    system_prompt = (
        "You check whether a child's new utterance addresses a previous "
        "correction or suggestion about a scene they are narrating. "
        "Return ONLY valid JSON:\n"
        '{"resolved": true or false}\n'
        "resolved=true means the child incorporated the feedback. "
        "Be generous: if they made a reasonable attempt, count it as resolved."
    )

    user_prompt = (
        f"Previous feedback:\n"
        f'"{previous_rationale}"'
        f"{scene_context}\n\n"
        f'Child\'s new utterance:\n"{utterance_text}"\n\n'
        f"Did the child address the feedback?"
    )

    try:
        data = await _gemini_call(client, system_prompt, user_prompt)
        resolved = data.get("resolved", False)
        logger.info(
            "\033[93m[RESOLUTION]\033[0m → %s",
            "resolved" if resolved else "not resolved",
        )
        return resolved
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
