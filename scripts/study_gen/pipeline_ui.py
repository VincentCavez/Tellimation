#!/usr/bin/env python3
"""Flask UI for batch-running the Tellimations pipeline on 200 study stimuli.

Runs correction (Pass 1) and suggestion (Pass 2) pipelines via Gemini,
collects animation selections & generates pipeline_intent strings.

Usage:
    python -m scripts.study_gen.pipeline_ui
    python -m scripts.study_gen.pipeline_ui --api-key YOUR_KEY
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template_string, request, send_file, jsonify

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from google import genai
from google.genai import types

from src.interaction.discrepancy_assessment import assess_corrections, assess_enrichment
from src.interaction.misl_selector import select_misl_candidates
from src.models.assessment import Discrepancy
from animations.grammar import get_animation, get_all_animations
from config.misl import MACRO_PRIORITY_ORDER, MICRO_CODES, MISL_CODE_TO_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("pipeline_ui")

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------

STIMULI_PATH = PROJECT_ROOT / "data" / "study1_all_stimuli.json"
SCENES_DIR = PROJECT_ROOT / "data" / "prolific_scenes"
IMAGES_DIR = PROJECT_ROOT / "data" / "prolific_gen"
RESULTS_PATH = PROJECT_ROOT / "data" / "pipeline_results.json"

DEFAULT_CONCURRENCY = 5
MAX_RETRIES = 3
BACKOFF_BASE = 2.0
SAVE_EVERY = 10

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

_stimuli_cache: Optional[Dict[str, Any]] = None
_scenes_cache: Dict[str, Dict[str, Any]] = {}
_grammars_cache: Optional[Dict[str, Dict[str, Any]]] = None


def load_stimuli() -> List[Dict[str, Any]]:
    global _stimuli_cache
    if _stimuli_cache is None:
        with open(STIMULI_PATH, "r", encoding="utf-8") as f:
            _stimuli_cache = json.load(f)
    return _stimuli_cache["stimuli"]


def load_scene(scene_id: str) -> Dict[str, Any]:
    if scene_id not in _scenes_cache:
        path = SCENES_DIR / f"{scene_id}.json"
        with open(path, "r", encoding="utf-8") as f:
            _scenes_cache[scene_id] = json.load(f)
    return _scenes_cache[scene_id]


def load_scene_image_path(scene_id: str) -> Optional[Path]:
    p = IMAGES_DIR / scene_id / "hd" / "scene_1_full.png"
    return p if p.exists() else None


def load_all_grammars() -> Dict[str, Dict[str, Any]]:
    """Return dict: short_id -> {name, category, correction_intent, suggestion_intent}."""
    global _grammars_cache
    if _grammars_cache is None:
        _grammars_cache = {}
        all_anims = get_all_animations()
        for aid, anim in all_anims.items():
            # Read raw JSON for intent fields (not in AnimationDef model)
            grammar_path = PROJECT_ROOT / "animations" / "grammar" / f"{aid}.json"
            raw = {}
            if grammar_path.exists():
                with open(grammar_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            _grammars_cache[aid] = {
                "name": anim.name,
                "category": anim.category,
                "correction_intent": raw.get("correction_intent", ""),
                "suggestion_intent": raw.get("suggestion_intent", ""),
                "misl_elements": anim.misl_elements,
            }
    return _grammars_cache


# ---------------------------------------------------------------------------
# Scene description generation (VLM)
# ---------------------------------------------------------------------------

DESCRIBE_MODEL_ID = "gemini-3-flash-preview"
DESCRIBE_TIMEOUT = 30

DESCRIBE_PROMPT = """\
You are looking at a children's picture-book scene. Describe EVERYTHING you see \
in precise, factual detail. Include:

- Every character/animal: appearance, clothing, posture, action, position in scene
- Every object: what it is, its color/size, where it is
- The setting: location type, time of day, weather, background elements
- Spatial relationships: who/what is next to, behind, in front of, on top of what

Be exhaustive. Use entity names from this list when possible: {entity_names}

Output a single block of text, no headings or bullet points. \
Describe left-to-right, foreground-to-background."""


describe_state: Dict[str, Any] = {
    "status": "idle",  # idle, running, done
    "total": 0,
    "done": 0,
    "errors": 0,
    "skipped": 0,
    "current": None,
}


def get_unique_scene_ids() -> List[str]:
    """Get unique scene_ids from stimuli."""
    stimuli = load_stimuli()
    seen = set()
    scene_ids = []
    for s in stimuli:
        sid = s["scene_id"]
        if sid not in seen:
            seen.add(sid)
            scene_ids.append(sid)
    return scene_ids


async def describe_one_scene(client: genai.Client, scene_id: str, force: bool) -> Optional[str]:
    """Send scene image to Gemini VLM and return description text."""
    scene_data = load_scene(scene_id)

    # Skip if already has scene_description
    if scene_data["scenes"][0].get("scene_description") and not force:
        return None

    img_path = load_scene_image_path(scene_id)
    if not img_path:
        raise FileNotFoundError(f"No image for {scene_id}")

    img_bytes = img_path.read_bytes()
    entity_names = ", ".join(scene_data.get("entities", {}).keys())

    prompt_text = DESCRIBE_PROMPT.format(entity_names=entity_names)

    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=DESCRIBE_MODEL_ID,
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                prompt_text,
            ],
            config=types.GenerateContentConfig(
                temperature=0.3,
            ),
        ),
        timeout=DESCRIBE_TIMEOUT,
    )

    text = response.text or ""
    return text.strip()


async def _run_describe_batch(api_key: str, scene_ids: List[str], concurrency: int, force: bool):
    describe_state["status"] = "running"
    describe_state["total"] = len(scene_ids)
    describe_state["done"] = 0
    describe_state["errors"] = 0
    describe_state["skipped"] = 0

    client = genai.Client(api_key=api_key)
    sem = asyncio.Semaphore(concurrency)

    async def process_one(scene_id: str):
        async with sem:
            describe_state["current"] = scene_id
            try:
                desc = await describe_one_scene(client, scene_id, force)
                if desc is None:
                    describe_state["skipped"] += 1
                    describe_state["done"] += 1
                    return

                # Invalidate cache BEFORE reading, to avoid stale data
                _scenes_cache.pop(scene_id, None)
                scene_data = load_scene(scene_id)
                scene_data["scenes"][0]["scene_description"] = desc

                _scenes_cache.pop(scene_id, None)

                scene_path = SCENES_DIR / f"{scene_id}.json"
                with open(scene_path, "w", encoding="utf-8") as f:
                    json.dump(scene_data, f, indent=2, ensure_ascii=False)

                describe_state["done"] += 1
                logger.info("[describe] Done %s (%d/%d)",
                            scene_id, describe_state["done"], describe_state["total"])

            except Exception as exc:
                logger.error("[describe] Error %s: %s", scene_id, exc)
                describe_state["errors"] += 1
                describe_state["done"] += 1

    tasks = [process_one(sid) for sid in scene_ids]
    await asyncio.gather(*tasks)
    describe_state["status"] = "done"
    describe_state["current"] = None


def run_describe_in_thread(api_key: str, scene_ids: List[str], concurrency: int, force: bool):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_describe_batch(api_key, scene_ids, concurrency, force))
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# MISL targets generation (VLM)
# ---------------------------------------------------------------------------

MISL_TARGETS_PROMPT = """\
You are analyzing a children's picture-book scene. Based on the image and the \
scene description below, generate MISL targets for narrative scaffolding.

Scene description:
{scene_description}

Entities in scene: {entity_names}

For each MISL dimension below, list concrete elements VISIBLE in this specific \
scene that a child could describe. Each element should be a short phrase (3-8 words).

MACRO dimensions (narrative structure):
- CH (Character): characters/animals visible in the scene (use descriptions, not just names)
- S (Setting): setting elements (location, time of day, weather, background details)
- IE (Initiating Event): events happening or about to happen
- IR (Internal Response): emotions or feelings characters appear to have
- P (Plan): intentions or goals characters seem to have
- A (Action): actions characters/animals are performing
- CO (Consequence): outcomes or results visible in the scene

MICRO dimensions (language features):
- ENP (Elaborated Noun Phrases): descriptive details about entities (color, size, material)
- ADV (Adverbs): manner words that describe actions in the scene
- SC (Subordinating Conjunctions): cause/time clauses applicable (because..., when..., while...)
- CC (Coordinating Conjunctions): coordination opportunities (and, but, so)
- M (Mental Verbs): mental state verbs applicable (thinks, feels, wants, knows)
- L (Linguistic Verbs): speech/communication verbs applicable (says, asks, tells)

Return ONLY valid JSON with this exact structure:
{{
  "macro": {{
    "CH": ["phrase1", "phrase2", ...],
    "S": ["phrase1", ...],
    "IE": ["phrase1", ...],
    "IR": ["phrase1", ...],
    "P": ["phrase1", ...],
    "A": ["phrase1", ...],
    "CO": ["phrase1", ...]
  }},
  "micro": {{
    "ENP": ["phrase1", ...],
    "ADV": ["word1", ...],
    "SC": ["phrase1", ...],
    "CC": ["word1", ...],
    "M": ["word1", ...],
    "L": ["word1", ...]
  }}
}}

Every dimension MUST have at least 1 entry. Provide 2-5 entries per dimension when possible."""


misl_gen_state: Dict[str, Any] = {
    "status": "idle",
    "total": 0,
    "done": 0,
    "errors": 0,
    "skipped": 0,
    "current": None,
}


async def generate_misl_targets_for_scene(
    client: genai.Client, scene_id: str, force: bool
) -> Optional[Dict[str, Any]]:
    """Send scene image + description to Gemini and return misl_targets dict."""
    scene_data = load_scene(scene_id)
    scene_obj = scene_data["scenes"][0]

    if scene_obj.get("misl_targets") and not force:
        return None

    scene_desc = scene_obj.get("scene_description") or scene_obj.get("full_scene_prompt", "")
    entity_names = ", ".join(scene_data.get("entities", {}).keys())

    prompt_text = MISL_TARGETS_PROMPT.format(
        scene_description=scene_desc,
        entity_names=entity_names,
    )

    contents = [prompt_text]

    # Add image if available
    img_path = load_scene_image_path(scene_id)
    if img_path:
        img_bytes = img_path.read_bytes()
        contents = [
            types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
            prompt_text,
        ]

    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=DESCRIBE_MODEL_ID,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.3,
                response_mime_type="application/json",
            ),
        ),
        timeout=DESCRIBE_TIMEOUT,
    )

    text = response.text or ""
    # Parse JSON
    from src.generation.utils import extract_json as _extract_json
    misl_targets = _extract_json(text)

    # Validate structure
    if not isinstance(misl_targets, dict):
        raise ValueError(f"Expected dict, got {type(misl_targets)}")
    if "macro" not in misl_targets or "micro" not in misl_targets:
        raise ValueError(f"Missing macro/micro keys: {list(misl_targets.keys())}")

    return misl_targets


async def _run_misl_gen_batch(api_key: str, scene_ids: List[str], concurrency: int, force: bool):
    misl_gen_state["status"] = "running"
    misl_gen_state["total"] = len(scene_ids)
    misl_gen_state["done"] = 0
    misl_gen_state["errors"] = 0
    misl_gen_state["skipped"] = 0

    client = genai.Client(api_key=api_key)
    sem = asyncio.Semaphore(concurrency)

    async def process_one(scene_id: str):
        async with sem:
            misl_gen_state["current"] = scene_id
            try:
                mt = await generate_misl_targets_for_scene(client, scene_id, force)
                if mt is None:
                    misl_gen_state["skipped"] += 1
                    misl_gen_state["done"] += 1
                    return

                # Invalidate cache BEFORE reading, to avoid stale data
                _scenes_cache.pop(scene_id, None)
                scene_data = load_scene(scene_id)
                scene_data["scenes"][0]["misl_targets"] = mt

                _scenes_cache.pop(scene_id, None)

                scene_path = SCENES_DIR / f"{scene_id}.json"
                with open(scene_path, "w", encoding="utf-8") as f:
                    json.dump(scene_data, f, indent=2, ensure_ascii=False)

                misl_gen_state["done"] += 1
                logger.info("[misl_gen] Done %s (%d/%d)",
                            scene_id, misl_gen_state["done"], misl_gen_state["total"])

            except Exception as exc:
                logger.error("[misl_gen] Error %s: %s", scene_id, exc)
                misl_gen_state["errors"] += 1
                misl_gen_state["done"] += 1

    tasks = [process_one(sid) for sid in scene_ids]
    await asyncio.gather(*tasks)
    misl_gen_state["status"] = "done"
    misl_gen_state["current"] = None


def run_misl_gen_in_thread(api_key: str, scene_ids: List[str], concurrency: int, force: bool):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_misl_gen_batch(api_key, scene_ids, concurrency, force))
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# MISL targets helper (for pipeline — uses scene data)
# ---------------------------------------------------------------------------

def get_misl_targets_for_stimulus(scene_data: Dict[str, Any], target_misl: str) -> Dict[str, Any]:
    """Get misl_targets from scene data, or build a minimal fallback."""
    scene_obj = scene_data["scenes"][0]
    mt = scene_obj.get("misl_targets")
    if mt and isinstance(mt, dict) and ("macro" in mt or "micro" in mt):
        return mt

    # Fallback: build minimal synthetic targets
    entities_in_scene = scene_obj.get("entities_in_scene", [])
    entities = scene_data.get("entities", {})
    entity_descriptions = [entities.get(e, e) for e in entities_in_scene]

    macro_targets: Dict[str, Any] = {}
    micro_targets: Dict[str, Any] = {}

    if target_misl in MACRO_PRIORITY_ORDER:
        macro_targets[target_misl] = entity_descriptions
    elif target_misl in MICRO_CODES:
        micro_targets[target_misl] = entity_descriptions

    return {"macro": macro_targets, "micro": micro_targets}


# ---------------------------------------------------------------------------
# Pipeline intent generation
# ---------------------------------------------------------------------------

def generate_pipeline_intent(
    condition: str,
    animation_id_short: str,
    target_entities: List[str],
    description: str,
    misl_element: Optional[str] = None,
) -> str:
    """Generate a human-readable pipeline_intent string."""
    grammars = load_all_grammars()
    grammar = grammars.get(animation_id_short.upper(), grammars.get(animation_id_short, {}))
    anim_name = grammar.get("name", animation_id_short)
    targets_str = ", ".join(target_entities) if target_entities else "the scene"

    if condition == "correction":
        intent_template = grammar.get("correction_intent", "")
        # Build a participant-friendly sentence
        if description:
            return (
                f"The system detected an issue with the description of {targets_str} "
                f"and decided to use {anim_name} to draw attention to it."
            )
        return (
            f"The system detected an error related to {targets_str} "
            f"and decided to highlight it using {anim_name}."
        )
    else:
        # suggestion
        misl_name = MISL_CODE_TO_KEY.get(misl_element, misl_element) if misl_element else "an element"
        misl_name = misl_name.replace("_", " ")
        return (
            f"The system noticed that {misl_name} was not described "
            f"and decided to draw attention to {targets_str} using {anim_name}."
        )


# ---------------------------------------------------------------------------
# Per-stimulus pipeline
# ---------------------------------------------------------------------------

def _normalize_animation_id(aid: str) -> str:
    """Extract short animation ID: 'P2c' -> 'P2c', 'I1_spotlight' -> 'I1'."""
    if not aid:
        return ""
    return aid.split("_")[0]


def _validate_correction(stimulus: Dict, animation_id_short: str, target_entities: List[str]) -> Dict:
    """Validate correction result against expected values."""
    warnings = []
    expected_anim = stimulus.get("target_animation", "")
    expected_entities = stimulus.get("target_entities", [])

    anim_match = animation_id_short.upper() == expected_anim.upper() if expected_anim else True
    if not anim_match:
        warnings.append(f"Expected {expected_anim} but got {animation_id_short}")

    entity_match = bool(set(target_entities) & set(expected_entities)) if expected_entities else True
    if not entity_match:
        warnings.append(f"Expected entities {expected_entities} but got {target_entities}")

    return {"animation_match": anim_match, "entity_match": entity_match, "warnings": warnings}


def _validate_suggestion(stimulus: Dict, misl_selected: Optional[str]) -> Dict:
    """Validate suggestion result against expected MISL."""
    warnings = []
    expected_misl = stimulus.get("target_misl", "")

    misl_match = misl_selected == expected_misl if expected_misl else True
    if not misl_match:
        warnings.append(f"Expected MISL {expected_misl} but got {misl_selected}")

    return {"misl_match": misl_match, "warnings": warnings}


async def run_correction(api_key: str, stimulus: Dict) -> Dict[str, Any]:
    """Run correction pipeline on a single stimulus."""
    scene_data = load_scene(stimulus["scene_id"])
    # Prefer VLM-generated scene_description over the generation prompt
    scene_desc = (scene_data["scenes"][0].get("scene_description")
                  or scene_data["scenes"][0]["full_scene_prompt"])
    entities_in_scene = scene_data["scenes"][0].get("entities_in_scene", [])

    t0 = time.time()
    discrepancies, _names = await assess_corrections(
        api_key=api_key,
        utterance_text=stimulus["narrator_text"],
        story_so_far=[],
        scene_description=scene_desc,
        character_names=None,
        entities_in_scene=entities_in_scene,
    )
    elapsed_ms = int((time.time() - t0) * 1000)

    if not discrepancies:
        return {
            "status": "success",
            "animation_id": None,
            "animation_id_short": None,
            "animation_name": None,
            "target_entities": [],
            "error_type": None,
            "description": "No discrepancies detected",
            "pipeline_intent": "The system did not detect any errors in the description.",
            "validation": {"animation_match": False, "entity_match": False,
                           "warnings": ["No discrepancies detected by Gemini"]},
            "elapsed_ms": elapsed_ms,
            "raw_discrepancies": [],
        }

    d = discrepancies[0]
    aid_short = _normalize_animation_id(d.animation_id or "")
    grammars = load_all_grammars()
    anim_info = grammars.get(aid_short.upper(), grammars.get(aid_short, {}))

    validation = _validate_correction(stimulus, aid_short, d.target_entities)
    intent = generate_pipeline_intent("correction", aid_short, d.target_entities, d.description)

    return {
        "status": "success",
        "animation_id": d.animation_id,
        "animation_id_short": aid_short,
        "animation_name": anim_info.get("name", aid_short),
        "target_entities": d.target_entities,
        "error_type": d.type,
        "description": d.description,
        "pipeline_intent": intent,
        "validation": validation,
        "elapsed_ms": elapsed_ms,
        "raw_discrepancies": [disc.model_dump() for disc in discrepancies],
    }


async def run_suggestion(api_key: str, stimulus: Dict) -> Dict[str, Any]:
    """Run suggestion pipeline on a single stimulus."""
    scene_data = load_scene(stimulus["scene_id"])
    entities_in_scene = scene_data["scenes"][0].get("entities_in_scene", [])

    # Get misl_targets (from VLM-generated or fallback)
    target_misl = stimulus.get("target_misl", "")
    misl_targets = get_misl_targets_for_stimulus(scene_data, target_misl)
    mention_counts = stimulus.get("mention_counts", {})

    macro_selected, micro_candidates, trace = select_misl_candidates(
        misl_targets=misl_targets,
        mention_counts=mention_counts,
        study_log_entries=[],
    )

    t0 = time.time()
    discrepancies = await assess_enrichment(
        api_key=api_key,
        utterance_text=stimulus["narrator_text"],
        story_so_far=[],
        character_names=None,
        misl_targets=misl_targets,
        entities_in_scene=entities_in_scene,
        macro_selected=macro_selected,
        micro_candidates=micro_candidates,
    )
    elapsed_ms = int((time.time() - t0) * 1000)

    misl_selected = macro_selected or (micro_candidates[0] if micro_candidates else None)

    if not discrepancies:
        return {
            "status": "success",
            "animation_id": None,
            "animation_id_short": None,
            "animation_name": None,
            "target_entities": [],
            "misl_element": misl_selected,
            "description": "No suggestions generated",
            "pipeline_intent": "The system did not generate a suggestion.",
            "validation": {"misl_match": misl_selected == target_misl,
                           "warnings": ["No discrepancies generated by Gemini"]},
            "elapsed_ms": elapsed_ms,
            "raw_discrepancies": [],
            "misl_trace": trace,
        }

    d = discrepancies[0]
    aid_short = _normalize_animation_id(d.animation_id or "")
    grammars = load_all_grammars()
    anim_info = grammars.get(aid_short.upper(), grammars.get(aid_short, {}))

    validation = _validate_suggestion(stimulus, misl_selected)
    intent = generate_pipeline_intent("suggestion", aid_short, d.target_entities, d.description, misl_selected)

    return {
        "status": "success",
        "animation_id": d.animation_id,
        "animation_id_short": aid_short,
        "animation_name": anim_info.get("name", aid_short),
        "target_entities": d.target_entities,
        "misl_element": misl_selected,
        "description": d.description,
        "pipeline_intent": intent,
        "validation": validation,
        "elapsed_ms": elapsed_ms,
        "raw_discrepancies": [disc.model_dump() for disc in discrepancies],
        "misl_trace": trace,
    }


# ---------------------------------------------------------------------------
# Results persistence
# ---------------------------------------------------------------------------

results: Dict[str, Dict[str, Any]] = {}
results_lock = threading.Lock()


def save_results():
    with results_lock:
        data = {
            "metadata": {
                "last_saved": datetime.now().isoformat(),
                "total": len(results),
                "success": sum(1 for r in results.values() if r.get("status") == "success"),
                "errors": sum(1 for r in results.values() if r.get("status") == "error"),
            },
            "results": results,
        }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("[persistence] Saved %d results to %s", len(results), RESULTS_PATH)


def load_results():
    global results
    if RESULTS_PATH.exists():
        with open(RESULTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        with results_lock:
            results = data.get("results", {})
        logger.info("[persistence] Loaded %d previous results", len(results))


def export_to_stimuli():
    """Write pipeline_intent back to study1_all_stimuli.json."""
    with open(STIMULI_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated = 0
    for stim in data["stimuli"]:
        sid = stim["stimulus_id"]
        with results_lock:
            r = results.get(sid)
        if r and r.get("status") == "success":
            # Use edited intent if available
            intent = r.get("edited_intent") or r.get("pipeline_intent", "")
            stim["pipeline_intent"] = intent
            stim["pipeline_animation_id"] = r.get("animation_id_short", "")
            stim["pipeline_target_entities"] = r.get("target_entities", [])
            updated += 1

    with open(STIMULI_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("[export] Updated %d stimuli in %s", updated, STIMULI_PATH)
    return updated


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

batch_state: Dict[str, Any] = {
    "status": "idle",  # idle, running, done, cancelled
    "total": 0,
    "done": 0,
    "errors": 0,
    "current": None,
    "cancelled": False,
}


def run_batch_in_thread(api_key: str, stimuli: List[Dict], concurrency: int, force: bool):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_batch(api_key, stimuli, concurrency, force))
    finally:
        loop.close()


async def _run_batch(api_key: str, stimuli: List[Dict], concurrency: int, force: bool):
    batch_state["status"] = "running"
    batch_state["total"] = len(stimuli)
    batch_state["done"] = 0
    batch_state["errors"] = 0
    batch_state["cancelled"] = False

    sem = asyncio.Semaphore(concurrency)
    done_count = 0

    async def process_one(stimulus: Dict):
        nonlocal done_count
        sid = stimulus["stimulus_id"]

        if batch_state["cancelled"]:
            return

        # Skip already-successful results unless force
        with results_lock:
            existing = results.get(sid)
        if existing and existing.get("status") == "success" and not force:
            batch_state["done"] += 1
            return

        async with sem:
            if batch_state["cancelled"]:
                return

            batch_state["current"] = sid
            last_error = None

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if stimulus["condition"] == "correction":
                        result = await run_correction(api_key, stimulus)
                    else:
                        result = await run_suggestion(api_key, stimulus)

                    result["condition"] = stimulus["condition"]
                    result["stimulus_id"] = sid
                    result["attempts"] = attempt

                    with results_lock:
                        results[sid] = result
                    break

                except Exception as exc:
                    last_error = str(exc)
                    logger.warning("[batch] %s attempt %d failed: %s", sid, attempt, exc)
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(BACKOFF_BASE ** attempt)
            else:
                # All retries failed
                with results_lock:
                    results[sid] = {
                        "status": "error",
                        "condition": stimulus["condition"],
                        "stimulus_id": sid,
                        "error_message": last_error,
                        "attempts": MAX_RETRIES,
                    }
                batch_state["errors"] += 1

            batch_state["done"] += 1
            done_count += 1

            # Save periodically
            if done_count % SAVE_EVERY == 0:
                save_results()

    tasks = [process_one(s) for s in stimuli]
    await asyncio.gather(*tasks)

    save_results()
    if batch_state["cancelled"]:
        batch_state["status"] = "cancelled"
    else:
        batch_state["status"] = "done"
    batch_state["current"] = None


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/stimuli")
def api_stimuli():
    stimuli = load_stimuli()
    out = []
    for s in stimuli:
        sid = s["stimulus_id"]
        with results_lock:
            r = results.get(sid)
        out.append({
            "stimulus_id": sid,
            "scene_id": s["scene_id"],
            "condition": s["condition"],
            "narrator_text": s["narrator_text"],
            "target_animation": s.get("target_animation"),
            "target_entities": s.get("target_entities"),
            "target_misl": s.get("target_misl"),
            "result": r,
        })
    return jsonify(out)


@app.route("/api/start", methods=["POST"])
def api_start():
    if batch_state["status"] == "running":
        return jsonify({"error": "Batch already running"}), 409

    body = request.get_json(force=True)
    condition_filter = body.get("filter", "all")
    concurrency = int(body.get("concurrency", DEFAULT_CONCURRENCY))
    force = bool(body.get("force", False))

    api_key = app.config.get("API_KEY", "")
    if not api_key:
        return jsonify({"error": "No API key configured"}), 500

    stimuli = load_stimuli()
    if condition_filter == "corrections":
        stimuli = [s for s in stimuli if s["condition"] == "correction"]
    elif condition_filter == "suggestions":
        stimuli = [s for s in stimuli if s["condition"] == "suggestion"]

    t = threading.Thread(
        target=run_batch_in_thread,
        args=(api_key, stimuli, concurrency, force),
        daemon=True,
    )
    t.start()
    return jsonify({"started": len(stimuli), "concurrency": concurrency})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    batch_state["cancelled"] = True
    return jsonify({"ok": True})


@app.route("/api/progress")
def api_progress():
    with results_lock:
        success = sum(1 for r in results.values() if r.get("status") == "success")
        errors = sum(1 for r in results.values() if r.get("status") == "error")
        warnings = sum(
            1 for r in results.values()
            if r.get("status") == "success" and r.get("validation", {}).get("warnings")
        )
    return jsonify({
        "status": batch_state["status"],
        "total": batch_state["total"],
        "done": batch_state["done"],
        "errors": errors,
        "success": success,
        "warnings": warnings,
        "current": batch_state["current"],
    })


@app.route("/api/result/<stimulus_id>")
def api_result(stimulus_id: str):
    with results_lock:
        r = results.get(stimulus_id)
    if not r:
        return jsonify({"error": "Not found"}), 404

    # Also include stimulus data
    stimuli = load_stimuli()
    stim = next((s for s in stimuli if s["stimulus_id"] == stimulus_id), None)

    return jsonify({"stimulus": stim, "result": r})


@app.route("/api/retry", methods=["POST"])
def api_retry():
    if batch_state["status"] == "running":
        return jsonify({"error": "Batch already running"}), 409

    body = request.get_json(force=True)
    stimulus_ids = body.get("stimulus_ids", [])

    api_key = app.config.get("API_KEY", "")
    stimuli = load_stimuli()
    to_retry = [s for s in stimuli if s["stimulus_id"] in stimulus_ids]

    if not to_retry:
        return jsonify({"error": "No matching stimuli"}), 404

    t = threading.Thread(
        target=run_batch_in_thread,
        args=(api_key, to_retry, 1, True),
        daemon=True,
    )
    t.start()
    return jsonify({"retrying": len(to_retry)})


@app.route("/api/edit_intent", methods=["POST"])
def api_edit_intent():
    body = request.get_json(force=True)
    sid = body.get("stimulus_id")
    new_intent = body.get("pipeline_intent", "")

    with results_lock:
        r = results.get(sid)
        if not r:
            return jsonify({"error": "Not found"}), 404
        r["edited_intent"] = new_intent
        r["edited"] = True

    save_results()
    return jsonify({"ok": True})


@app.route("/api/export", methods=["POST"])
def api_export():
    save_results()
    updated = export_to_stimuli()
    return jsonify({"exported_results": str(RESULTS_PATH), "updated_stimuli": updated})


@app.route("/api/describe/start", methods=["POST"])
def api_describe_start():
    if describe_state["status"] == "running":
        return jsonify({"error": "Description batch already running"}), 409

    body = request.get_json(force=True)
    concurrency = int(body.get("concurrency", 5))
    force = bool(body.get("force", False))

    api_key = app.config.get("API_KEY", "")
    if not api_key:
        return jsonify({"error": "No API key configured"}), 500

    scene_ids = get_unique_scene_ids()

    t = threading.Thread(
        target=run_describe_in_thread,
        args=(api_key, scene_ids, concurrency, force),
        daemon=True,
    )
    t.start()
    return jsonify({"started": len(scene_ids), "concurrency": concurrency})


@app.route("/api/describe/progress")
def api_describe_progress():
    return jsonify({
        "status": describe_state["status"],
        "total": describe_state["total"],
        "done": describe_state["done"],
        "errors": describe_state["errors"],
        "skipped": describe_state["skipped"],
        "current": describe_state["current"],
    })


@app.route("/api/describe/status")
def api_describe_status():
    """Check how many scenes already have scene_description and misl_targets."""
    # Clear cache to read fresh data from disk
    _scenes_cache.clear()
    scene_ids = get_unique_scene_ids()
    described = 0
    desc_missing = 0
    misl_done = 0
    misl_missing = 0
    for sid in scene_ids:
        scene_data = load_scene(sid)
        scene_obj = scene_data["scenes"][0]
        if scene_obj.get("scene_description"):
            described += 1
        else:
            desc_missing += 1
        if scene_obj.get("misl_targets"):
            misl_done += 1
        else:
            misl_missing += 1
    return jsonify({
        "total": len(scene_ids),
        "described": described, "desc_missing": desc_missing,
        "misl_done": misl_done, "misl_missing": misl_missing,
    })


@app.route("/api/misl_gen/start", methods=["POST"])
def api_misl_gen_start():
    if misl_gen_state["status"] == "running":
        return jsonify({"error": "MISL generation already running"}), 409

    body = request.get_json(force=True)
    concurrency = int(body.get("concurrency", 5))
    force = bool(body.get("force", False))

    api_key = app.config.get("API_KEY", "")
    if not api_key:
        return jsonify({"error": "No API key configured"}), 500

    scene_ids = get_unique_scene_ids()

    t = threading.Thread(
        target=run_misl_gen_in_thread,
        args=(api_key, scene_ids, concurrency, force),
        daemon=True,
    )
    t.start()
    return jsonify({"started": len(scene_ids), "concurrency": concurrency})


@app.route("/api/misl_gen/progress")
def api_misl_gen_progress():
    return jsonify({
        "status": misl_gen_state["status"],
        "total": misl_gen_state["total"],
        "done": misl_gen_state["done"],
        "errors": misl_gen_state["errors"],
        "skipped": misl_gen_state["skipped"],
        "current": misl_gen_state["current"],
    })


@app.route("/api/image/<scene_id>")
def api_image(scene_id: str):
    p = load_scene_image_path(scene_id)
    if not p:
        return "Not found", 404
    return send_file(p, mimetype="image/png")


# ---------------------------------------------------------------------------
# HTML/JS Template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Pipeline Batch Runner</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0; }
.container { max-width: 1200px; margin: 0 auto; padding: 20px; }
h1 { color: #e94560; margin-bottom: 20px; font-size: 1.5em; }

/* Control bar */
.controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
.controls button {
    padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer;
    font-size: 0.9em; font-weight: 600; transition: opacity .2s;
}
.controls button:hover { opacity: 0.85; }
.btn-primary { background: #e94560; color: #fff; }
.btn-secondary { background: #16213e; color: #e0e0e0; border: 1px solid #333; }
.btn-success { background: #0f9b58; color: #fff; }
.btn-warning { background: #f4a623; color: #1a1a2e; }
.controls select, .controls input[type=number] {
    padding: 6px 10px; background: #16213e; color: #e0e0e0; border: 1px solid #333;
    border-radius: 4px; font-size: 0.85em;
}
.controls label { font-size: 0.85em; color: #999; }

/* Progress */
.progress-section { margin-bottom: 16px; }
.progress-bar { height: 24px; background: #16213e; border-radius: 12px; overflow: hidden; display: flex; }
.progress-bar .seg-success { background: #0f9b58; transition: width .3s; }
.progress-bar .seg-warning { background: #f4a623; transition: width .3s; }
.progress-bar .seg-error { background: #e94560; transition: width .3s; }
.progress-bar .seg-pending { background: #333; transition: width .3s; }
.progress-stats { display: flex; gap: 20px; margin-top: 8px; font-size: 0.85em; color: #999; }
.progress-stats span { display: flex; align-items: center; gap: 4px; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot-success { background: #0f9b58; }
.dot-error { background: #e94560; }
.dot-warning { background: #f4a623; }
.dot-pending { background: #555; }

/* Filters */
.filters { display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }
.filters select {
    padding: 6px 10px; background: #16213e; color: #e0e0e0; border: 1px solid #333;
    border-radius: 4px; font-size: 0.85em;
}

/* Table */
.stim-table { width: 100%; border-collapse: collapse; }
.stim-table th {
    text-align: left; padding: 8px 10px; background: #16213e; color: #999;
    font-size: 0.8em; text-transform: uppercase; border-bottom: 1px solid #333;
}
.stim-table td { padding: 8px 10px; border-bottom: 1px solid #222; font-size: 0.85em; vertical-align: top; }
.stim-table tr:hover { background: #16213e; cursor: pointer; }
.badge { padding: 2px 8px; border-radius: 10px; font-size: 0.75em; font-weight: 600; }
.badge-correction { background: #e94560; color: #fff; }
.badge-suggestion { background: #0f9b58; color: #fff; }
.status-icon { font-size: 1.1em; }
.intent-cell { max-width: 350px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.warn-cell { color: #f4a623; font-size: 0.8em; }

/* Modal */
.modal-overlay {
    display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.7); z-index: 100; justify-content: center; align-items: center;
}
.modal-overlay.active { display: flex; }
.modal {
    background: #16213e; border-radius: 12px; width: 90%; max-width: 800px;
    max-height: 85vh; overflow-y: auto; padding: 24px; position: relative;
}
.modal h2 { color: #e94560; margin-bottom: 16px; font-size: 1.2em; }
.modal-close {
    position: absolute; top: 12px; right: 16px; background: none; border: none;
    color: #999; font-size: 1.5em; cursor: pointer;
}
.modal-section { margin-bottom: 16px; }
.modal-section h3 { color: #999; font-size: 0.85em; text-transform: uppercase; margin-bottom: 6px; }
.modal-section pre {
    background: #1a1a2e; padding: 12px; border-radius: 6px; font-size: 0.82em;
    overflow-x: auto; white-space: pre-wrap; color: #ccc;
}
.modal-section textarea {
    width: 100%; min-height: 80px; background: #1a1a2e; color: #e0e0e0;
    border: 1px solid #333; border-radius: 6px; padding: 10px; font-size: 0.85em;
    resize: vertical;
}
.modal-section img { max-width: 100%; border-radius: 8px; margin-top: 8px; }
.modal-actions { display: flex; gap: 10px; margin-top: 16px; }

/* Export */
.export-section { margin-top: 20px; padding-top: 16px; border-top: 1px solid #333; display: flex; gap: 10px; }
</style>
</head>
<body>
<div class="container">
    <h1>Pipeline Batch Runner</h1>

    <!-- Step 1: Describe scenes -->
    <div style="background:#16213e;padding:16px;border-radius:8px;margin-bottom:20px">
        <h2 style="font-size:1.1em;color:#e94560;margin-bottom:10px">Step 1: Generate Scene Descriptions (VLM)</h2>
        <p style="font-size:0.85em;color:#999;margin-bottom:10px">
            Sends each scene image to Gemini 3 Flash for a detailed visual description.
            This replaces the generation prompt with what Gemini actually sees.
        </p>
        <div class="controls" style="margin-bottom:8px">
            <button class="btn-warning" onclick="startDescribe()" id="describeBtn">Describe Scenes</button>
            <label><input type="checkbox" id="descForce"> Force re-describe</label>
            <span id="descStatus" style="font-size:0.85em;color:#999"></span>
        </div>
        <div class="progress-bar" id="descProgressBar" style="height:16px;margin-bottom:6px">
            <div class="seg-success" id="descSegDone" style="width:0"></div>
            <div class="seg-pending" id="descSegPending" style="width:100%"></div>
        </div>
        <div id="descStats" style="font-size:0.82em;color:#999"></div>
    </div>

    <!-- Step 1b: Generate MISL targets -->
    <div style="background:#16213e;padding:16px;border-radius:8px;margin-bottom:20px">
        <h2 style="font-size:1.1em;color:#e94560;margin-bottom:10px">Step 1b: Generate MISL Targets (VLM)</h2>
        <p style="font-size:0.85em;color:#999;margin-bottom:10px">
            Generates per-scene MISL targets (macro + micro) by analyzing each scene image
            with Gemini. Required for the suggestion pipeline.
        </p>
        <div class="controls" style="margin-bottom:8px">
            <button class="btn-warning" onclick="startMislGen()" id="mislGenBtn">Generate MISL Targets</button>
            <label><input type="checkbox" id="mislForce"> Force re-generate</label>
            <span id="mislStatus" style="font-size:0.85em;color:#999"></span>
        </div>
        <div class="progress-bar" id="mislProgressBar" style="height:16px;margin-bottom:6px">
            <div class="seg-success" id="mislSegDone" style="width:0"></div>
            <div class="seg-pending" id="mislSegPending" style="width:100%"></div>
        </div>
        <div id="mislStats" style="font-size:0.82em;color:#999"></div>
    </div>

    <!-- Step 2: Run pipeline -->
    <h2 style="font-size:1.1em;color:#e94560;margin-bottom:10px">Step 2: Run Pipeline</h2>

    <!-- Controls -->
    <div class="controls">
        <button class="btn-primary" onclick="startBatch('all')">Run All 200</button>
        <button class="btn-secondary" onclick="startBatch('corrections')">Run Corrections</button>
        <button class="btn-secondary" onclick="startBatch('suggestions')">Run Suggestions</button>
        <label>Concurrency:</label>
        <input type="number" id="concurrency" value="5" min="1" max="10" style="width:60px">
        <label><input type="checkbox" id="force"> Force re-run</label>
        <button class="btn-warning" onclick="cancelBatch()" id="cancelBtn" style="display:none">Cancel</button>
    </div>

    <!-- Progress -->
    <div class="progress-section">
        <div class="progress-bar" id="progressBar">
            <div class="seg-success" id="segSuccess" style="width:0"></div>
            <div class="seg-warning" id="segWarning" style="width:0"></div>
            <div class="seg-error" id="segError" style="width:0"></div>
            <div class="seg-pending" id="segPending" style="width:100%"></div>
        </div>
        <div class="progress-stats">
            <span><span class="dot dot-success"></span> <span id="statSuccess">0</span> success</span>
            <span><span class="dot dot-warning"></span> <span id="statWarning">0</span> warnings
                (<span id="statWarnCorr">0</span> corr, <span id="statWarnSugg">0</span> sugg)</span>
            <span><span class="dot dot-error"></span> <span id="statError">0</span> errors</span>
            <span><span class="dot dot-pending"></span> <span id="statPending">0</span> pending</span>
            <span id="statCurrent" style="color:#e94560"></span>
        </div>
    </div>

    <!-- Filters -->
    <div class="filters">
        <select id="filterCondition" onchange="renderTable()">
            <option value="all">All conditions</option>
            <option value="correction">Corrections</option>
            <option value="suggestion">Suggestions</option>
        </select>
        <select id="filterStatus" onchange="renderTable()">
            <option value="all">All statuses</option>
            <option value="success">Success</option>
            <option value="error">Failed</option>
            <option value="pending">Pending</option>
            <option value="warning">Warnings</option>
        </select>
    </div>

    <!-- Table -->
    <table class="stim-table">
        <thead>
            <tr>
                <th>Status</th>
                <th>Stimulus ID</th>
                <th>Condition</th>
                <th>Animation</th>
                <th>Target</th>
                <th>Intent</th>
                <th>Warnings</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody id="tableBody"></tbody>
    </table>

    <!-- Export -->
    <div class="export-section">
        <button class="btn-success" onclick="doExport()">Export results</button>
        <button class="btn-primary" onclick="writeToStimuli()">Write pipeline_intent to stimuli JSON</button>
    </div>
</div>

<!-- Detail Modal -->
<div class="modal-overlay" id="modalOverlay">
    <div class="modal" id="modalContent">
        <button class="modal-close" onclick="closeModal()">&times;</button>
        <div id="modalBody"></div>
    </div>
</div>

<script>
let allStimuli = [];
let pollInterval = null;
let descPollInterval = null;

// ── Describe scenes ──
async function loadDescStatus() {
    const resp = await fetch('/api/describe/status');
    const d = await resp.json();
    document.getElementById('descStats').textContent =
        `${d.described}/${d.total} scenes described, ${d.desc_missing} missing`;
    if (d.desc_missing === 0) {
        document.getElementById('descStatus').textContent = '\u2713 All scenes described';
        document.getElementById('descStatus').style.color = '#0f9b58';
    }
    // MISL targets
    document.getElementById('mislStats').textContent =
        `${d.misl_done}/${d.total} scenes with MISL targets, ${d.misl_missing} missing`;
    if (d.misl_missing === 0) {
        document.getElementById('mislStatus').textContent = '\u2713 All MISL targets generated';
        document.getElementById('mislStatus').style.color = '#0f9b58';
    }
}

async function startDescribe() {
    const force = document.getElementById('descForce').checked;
    const concurrency = parseInt(document.getElementById('concurrency').value) || 5;
    await fetch('/api/describe/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({concurrency, force})
    });
    document.getElementById('describeBtn').disabled = true;
    startDescPolling();
}

function startDescPolling() {
    if (descPollInterval) return;
    descPollInterval = setInterval(async () => {
        const resp = await fetch('/api/describe/progress');
        const p = await resp.json();
        const total = p.total || 1;
        const donePct = (p.done / total * 100) + '%';
        document.getElementById('descSegDone').style.width = donePct;
        document.getElementById('descSegPending').style.width = ((total - p.done) / total * 100) + '%';
        document.getElementById('descStats').textContent =
            `${p.done}/${p.total} done (${p.skipped} skipped, ${p.errors} errors)` +
            (p.current ? ` — ${p.current}` : '');

        if (p.status !== 'running') {
            clearInterval(descPollInterval);
            descPollInterval = null;
            document.getElementById('describeBtn').disabled = false;
            document.getElementById('descStatus').textContent =
                p.status === 'done' ? '✓ Done' : '⚠ ' + p.status;
            document.getElementById('descStatus').style.color =
                p.status === 'done' ? '#0f9b58' : '#f4a623';
            // Refresh scene cache
            loadDescStatus();
        }
    }, 1500);
}

// ── MISL targets generation ──
let mislPollInterval = null;

async function startMislGen() {
    const force = document.getElementById('mislForce').checked;
    const concurrency = parseInt(document.getElementById('concurrency').value) || 5;
    await fetch('/api/misl_gen/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({concurrency, force})
    });
    document.getElementById('mislGenBtn').disabled = true;
    startMislPolling();
}

function startMislPolling() {
    if (mislPollInterval) return;
    mislPollInterval = setInterval(async () => {
        const resp = await fetch('/api/misl_gen/progress');
        const p = await resp.json();
        const total = p.total || 1;
        document.getElementById('mislSegDone').style.width = (p.done / total * 100) + '%';
        document.getElementById('mislSegPending').style.width = ((total - p.done) / total * 100) + '%';
        document.getElementById('mislStats').textContent =
            `${p.done}/${p.total} done (${p.skipped} skipped, ${p.errors} errors)` +
            (p.current ? ` \u2014 ${p.current}` : '');

        if (p.status !== 'running') {
            clearInterval(mislPollInterval);
            mislPollInterval = null;
            document.getElementById('mislGenBtn').disabled = false;
            document.getElementById('mislStatus').textContent =
                p.status === 'done' ? '\u2713 Done' : '\u26A0 ' + p.status;
            document.getElementById('mislStatus').style.color =
                p.status === 'done' ? '#0f9b58' : '#f4a623';
            loadDescStatus();
        }
    }, 1500);
}

// Load stimuli on page load
async function loadStimuli() {
    const resp = await fetch('/api/stimuli');
    allStimuli = await resp.json();
    renderTable();
    updateStats();
}

function getStatus(item) {
    if (!item.result) return 'pending';
    return item.result.status || 'pending';
}

function hasWarnings(item) {
    return item.result?.validation?.warnings?.length > 0;
}

function renderTable() {
    const condFilter = document.getElementById('filterCondition').value;
    const statusFilter = document.getElementById('filterStatus').value;

    let filtered = allStimuli;
    if (condFilter !== 'all') filtered = filtered.filter(s => s.condition === condFilter);
    if (statusFilter === 'warning') {
        filtered = filtered.filter(s => hasWarnings(s));
    } else if (statusFilter !== 'all') {
        filtered = filtered.filter(s => getStatus(s) === statusFilter);
    }

    const tbody = document.getElementById('tableBody');
    tbody.innerHTML = filtered.map(s => {
        const status = getStatus(s);
        const r = s.result || {};
        const icon = status === 'success' ? (hasWarnings(s) ? '&#9888;' : '&#9989;') :
                     status === 'error' ? '&#10060;' : '&#9203;';
        const condBadge = `<span class="badge badge-${s.condition}">${s.condition}</span>`;
        const anim = r.animation_id_short ? `${r.animation_id_short} (${r.animation_name || ''})` : '-';
        const target = (r.target_entities || []).join(', ') || '-';
        const intent = r.edited_intent || r.pipeline_intent || '';
        const warns = (r.validation?.warnings || []).join('; ');
        return `<tr onclick="openDetail('${s.stimulus_id}')">
            <td class="status-icon">${icon}</td>
            <td>${s.stimulus_id}</td>
            <td>${condBadge}</td>
            <td>${anim}</td>
            <td>${target}</td>
            <td class="intent-cell" title="${intent.replace(/"/g,'&quot;')}">${intent.substring(0,80)}${intent.length>80?'...':''}</td>
            <td class="warn-cell">${warns}</td>
            <td><button class="btn-secondary" onclick="event.stopPropagation();retryOne('${s.stimulus_id}')" ${status==='pending'||status==='running'?'disabled':''}>Retry</button></td>
        </tr>`;
    }).join('');
}

function updateStats() {
    let success = 0, errors = 0, warnings = 0, pending = 0;
    let warnCorr = 0, warnSugg = 0;
    for (const s of allStimuli) {
        const st = getStatus(s);
        if (st === 'success') {
            success++;
            if (hasWarnings(s)) {
                warnings++;
                if (s.condition === 'correction') warnCorr++;
                else warnSugg++;
            }
        }
        else if (st === 'error') errors++;
        else pending++;
    }
    document.getElementById('statSuccess').textContent = success;
    document.getElementById('statError').textContent = errors;
    document.getElementById('statWarning').textContent = warnings;
    document.getElementById('statWarnCorr').textContent = warnCorr;
    document.getElementById('statWarnSugg').textContent = warnSugg;
    document.getElementById('statPending').textContent = pending;

    const total = allStimuli.length || 1;
    document.getElementById('segSuccess').style.width = ((success - warnings) / total * 100) + '%';
    document.getElementById('segWarning').style.width = (warnings / total * 100) + '%';
    document.getElementById('segError').style.width = (errors / total * 100) + '%';
    document.getElementById('segPending').style.width = (pending / total * 100) + '%';
}

async function startBatch(filter) {
    const concurrency = parseInt(document.getElementById('concurrency').value) || 5;
    const force = document.getElementById('force').checked;
    await fetch('/api/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({filter, concurrency, force})
    });
    document.getElementById('cancelBtn').style.display = '';
    startPolling();
}

async function cancelBatch() {
    await fetch('/api/cancel', {method: 'POST'});
}

function startPolling() {
    if (pollInterval) return;
    pollInterval = setInterval(async () => {
        const resp = await fetch('/api/progress');
        const prog = await resp.json();
        document.getElementById('statCurrent').textContent =
            prog.status === 'running' ? `Processing: ${prog.current || '...'}` : '';

        if (prog.status !== 'running') {
            clearInterval(pollInterval);
            pollInterval = null;
            document.getElementById('cancelBtn').style.display = 'none';
        }

        // Refresh stimuli data periodically
        await loadStimuli();
    }, 1500);
}

async function retryOne(sid) {
    await fetch('/api/retry', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({stimulus_ids: [sid]})
    });
    startPolling();
}

async function openDetail(sid) {
    const resp = await fetch(`/api/result/${sid}`);
    if (!resp.ok) { alert('No result yet'); return; }
    const data = await resp.json();
    const s = data.stimulus;
    const r = data.result;
    const imgUrl = `/api/image/${s.scene_id}`;

    const warns = (r.validation?.warnings || []).map(w => `<div style="color:#f4a623">&#9888; ${w}</div>`).join('');
    const rawDisc = r.raw_discrepancies ? JSON.stringify(r.raw_discrepancies, null, 2) : '(none)';
    const currentIntent = r.edited_intent || r.pipeline_intent || '';

    document.getElementById('modalBody').innerHTML = `
        <h2>${sid}</h2>
        <div class="modal-section">
            <h3>Scene: ${s.scene_id}</h3>
            <img src="${imgUrl}" alt="scene" onerror="this.style.display='none'">
        </div>
        <div class="modal-section">
            <h3>Narrator Text</h3>
            <pre>${s.narrator_text}</pre>
        </div>
        ${s.condition === 'correction' ? `
        <div class="modal-section">
            <h3>Error Type: ${r.error_type || '-'}</h3>
            <p>Expected animation: ${s.target_animation || '-'} | Got: ${r.animation_id_short || '-'}</p>
            <p>Expected entities: ${(s.target_entities||[]).join(', ')} | Got: ${(r.target_entities||[]).join(', ')}</p>
        </div>` : `
        <div class="modal-section">
            <h3>MISL Element: ${r.misl_element || '-'}</h3>
            <p>Expected: ${s.target_misl || '-'}</p>
        </div>`}
        <div class="modal-section">
            <h3>Description</h3>
            <pre>${r.description || '-'}</pre>
        </div>
        ${warns ? `<div class="modal-section"><h3>Validation</h3>${warns}</div>` : ''}
        <div class="modal-section">
            <h3>Pipeline Intent (editable)</h3>
            <textarea id="intentEdit">${currentIntent}</textarea>
        </div>
        <div class="modal-section">
            <h3>Raw Discrepancies</h3>
            <pre>${rawDisc}</pre>
        </div>
        <div class="modal-section">
            <p style="color:#999">Elapsed: ${r.elapsed_ms || 0}ms | Attempts: ${r.attempts || '-'}</p>
        </div>
        <div class="modal-actions">
            <button class="btn-primary" onclick="saveIntent('${sid}')">Save intent</button>
            <button class="btn-secondary" onclick="retryOne('${sid}');closeModal()">Retry</button>
            <button class="btn-secondary" onclick="closeModal()">Close</button>
        </div>
    `;
    document.getElementById('modalOverlay').classList.add('active');
}

function closeModal() {
    document.getElementById('modalOverlay').classList.remove('active');
}

async function saveIntent(sid) {
    const intent = document.getElementById('intentEdit').value;
    await fetch('/api/edit_intent', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({stimulus_id: sid, pipeline_intent: intent})
    });
    await loadStimuli();
    closeModal();
}

async function doExport() {
    const resp = await fetch('/api/export', {method: 'POST'});
    const data = await resp.json();
    alert(`Exported: ${data.exported_results}\nUpdated ${data.updated_stimuli} stimuli`);
}

async function writeToStimuli() {
    if (!confirm('Write pipeline_intent to study1_all_stimuli.json?')) return;
    const resp = await fetch('/api/export', {method: 'POST'});
    const data = await resp.json();
    alert(`Updated ${data.updated_stimuli} stimuli in study1_all_stimuli.json`);
}

// Keyboard close modal
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

// Init
loadStimuli();
loadDescStatus();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pipeline Batch Runner UI")
    parser.add_argument("--api-key", default=os.environ.get("GEMINI_API_KEY", ""))
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: No API key. Set GEMINI_API_KEY or use --api-key")
        sys.exit(1)

    app.config["API_KEY"] = args.api_key

    # Load previous results if any
    load_results()

    # Pre-load data
    load_stimuli()
    load_all_grammars()
    logger.info("Loaded %d stimuli, %d grammar definitions", len(load_stimuli()), len(load_all_grammars()))

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
