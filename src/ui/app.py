"""Tellimations FastAPI web server with WebSocket handler.

Orchestrates the pipeline:
  1. scene_neg_generator → manifest (Gemini 3 Flash)
  2. scene_generator → sprite_code
  3. transcription → assessment → decision logic (Gemini 3 Flash)
  4. tellimation → animation code (Gemini 3 Flash)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import copy
import random

from dotenv import load_dotenv

load_dotenv()
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from src.analytics.session_report import generate_report
from src.generation.scene_generator import STORY_THEMES, generate_scene_assets
from src.generation.scene_neg_generator import generate_scene_manifest
from src.interaction.decision_logic import (
    MAX_MISL_OPPORTUNITIES_PER_SCENE,
    get_accepted_utterances,
    get_misl_dimensions_suggested,
    process_assessment,
)
from src.interaction.discrepancy_assessment import (
    assess_corrections,
    assess_enrichment,
    assess_resolution,
    detect_misl_elements,
)
from src.interaction.misl_selector import select_misl_candidates
from src.models.assessment import SceneLog
from src.models.scene import SceneManifest
from src.models.session_state import SessionState
from src.models.student_profile import MISLDifficultyProfile, StudentProfile
from src.narration.transcription import transcribe_audio
from src.persistence import (
    save_scene,
    save_student_profile,
    create_story,
    append_study_log_entry,
    load_study_log,
)
from google import genai
from google.genai import types

from src.models.assessment import Discrepancy
from src.interaction.tellimation import _select_animation_for_discrepancy, select_discrepancy, load_animation_params
from config.misl import ANIMATION_ID_TO_TEMPLATE
from src.ui.animation_handler import _send_animation_message, execute_animation, execute_invocation_array, send_voice

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

MAX_SCENES = 5
INITIAL_SCENE_COUNT = 1  # number of scenes offered on the selection page

_SHORT_LLM_MODEL = "gemini-3-flash-preview"
_SHORT_LLM_TIMEOUT = 10

# Valid participant codes: admin + 20 participants
VALID_CODES = {
    "0027",  # admin
    "4831", "7249", "1563", "9087", "3412",
    "6758", "2094", "8376", "5620", "1947",
    "7503", "4186", "9641", "2835", "6072",
    "3498", "8215", "5769", "1324", "7950",
}


def _assemble_scene_dict(
    raw_data: Dict[str, Any],
    manifest: SceneManifest,
    assets: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the canonical scene dict from generation outputs."""
    return {
        "narrative_text": raw_data.get("narrative_text", ""),
        "scene_description": raw_data.get("scene_description", ""),
        "manifest": manifest.model_dump(),
        "sprite_code": assets["sprite_code"],
        "carried_over_entities": assets.get("carried_over_entities", []),
    }


def _apply_scene_to_session(
    session: "SessionState",
    scene: Dict[str, Any],
) -> None:
    """Hydrate session state from a scene dict (no WS interaction)."""
    session.current_scene = scene

    manifest_data = scene.get("manifest", {})

    # For HD scenes, manifest is the full scene_meta from the story def JSON.
    # Build a SceneManifest from it (needs scene_id + entities at minimum).
    scene_id = manifest_data.get("scene_id", f"scene_{scene.get('scene_number', 1)}")
    if "scene_id" not in manifest_data:
        manifest_data["scene_id"] = scene_id

    # Ensure entities list exists for SceneManifest validation
    if "entities" not in manifest_data:
        entities_in_scene = manifest_data.get("entities_in_scene", scene.get("entities_in_scene", []))
        characters = scene.get("entities", scene.get("characters", {}))
        manifest_data["entities"] = [
            {
                "id": eid,
                "type": eid,
                "name": characters.get(eid),
                "position": {"x": 0.5, "y": 0.5},
            }
            for eid in entities_in_scene
        ]

    session.current_manifest = SceneManifest.model_validate(manifest_data)
    session.reset_scene_state()

    session.current_scene_log = SceneLog(
        scene_id=scene_id,
        scene_manifest=manifest_data,
    )

    session.story_state.add_scene(
        scene_id=scene_id,
        narrative_text=scene.get("narrative_text", ""),
        manifest=manifest_data,
        sprite_code=scene.get("sprite_code"),
    )


def _find_data_dir() -> Path | None:
    p = BASE_DIR.resolve()
    for _ in range(8):
        if (p / "data").exists():
            return p / "data"
        p = p.parent
    return None


def _save_simulation_cache(scene: dict) -> None:
    data_dir = _find_data_dir()
    if data_dir is None:
        logger.warning("Could not find data/ directory to save simulation cache")
        return
    cache_path = data_dir / "simulation_scene_cache.json"
    cache_path.write_text(json.dumps(scene))
    logger.info("Simulation scene cache saved to %s", cache_path)

    # Also save manifest + NEG in a new numbered folder
    sim_dir = data_dir / "simulation_scenes"
    sim_dir.mkdir(exist_ok=True)
    existing = sorted(sim_dir.glob("scene_*"))
    next_num = 1
    if existing:
        try:
            next_num = int(existing[-1].name.split("_")[1]) + 1
        except (ValueError, IndexError):
            next_num = len(existing) + 1
    folder = sim_dir / f"scene_{next_num:03d}"
    folder.mkdir(exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps(scene.get("manifest", {}), indent=2))
    (folder / "neg.json").write_text(json.dumps(scene.get("neg", {}), indent=2))
    logger.info("Simulation scene manifest+NEG saved to %s", folder)

app = FastAPI(title="Tellimations")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Serve HD study images from data/study_gen/
_study_assets_dir = _find_data_dir()
if _study_assets_dir and (_study_assets_dir / "study_gen").is_dir():
    app.mount(
        "/study-assets",
        StaticFiles(directory=str(_study_assets_dir / "study_gen")),
        name="study-assets",
    )

# Serve oral instruction audio files
if _study_assets_dir and (_study_assets_dir / "oral_instructions").is_dir():
    app.mount(
        "/oral-instructions",
        StaticFiles(directory=str(_study_assets_dir / "oral_instructions")),
        name="oral-instructions",
    )


# ---------------------------------------------------------------------------
# HTML page routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=RedirectResponse)
async def root_redirect():
    return RedirectResponse(url="/study")


@app.get("/selection", response_class=HTMLResponse)
async def selection_page():
    return (TEMPLATES_DIR / "selection.html").read_text()


@app.get("/story", response_class=HTMLResponse)
async def story_page():
    return (TEMPLATES_DIR / "story.html").read_text()


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return (TEMPLATES_DIR / "admin.html").read_text()


# ---------------------------------------------------------------------------
# Study pages
# ---------------------------------------------------------------------------

@app.get("/study", response_class=HTMLResponse)
async def study_login_page():
    return (TEMPLATES_DIR / "study_login.html").read_text()


@app.get("/study/landing", response_class=HTMLResponse)
async def study_landing_page():
    return (TEMPLATES_DIR / "study_landing.html").read_text()


@app.get("/study/story", response_class=HTMLResponse)
async def study_story_page():
    return (TEMPLATES_DIR / "study_story.html").read_text()


# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/default-scenes")
async def api_default_scenes():
    """Serve pre-generated default scenes from data/default_scenes/ folder."""
    data_dir = _find_data_dir()
    if data_dir is None:
        return JSONResponse(status_code=404, content={"error": "data/ directory not found"})
    scenes_dir = data_dir / "default_scenes"
    if not scenes_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "No default scenes"})
    scenes = []
    for path in sorted(scenes_dir.glob("scene_*.json")):
        try:
            scenes.append(json.loads(path.read_text()))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping bad default scene %s: %s", path.name, exc)
    if not scenes:
        return JSONResponse(status_code=404, content={"error": "No default scenes"})
    return JSONResponse(content=scenes)


@app.post("/api/save-default-scenes")
async def api_save_default_scenes(request: Request):
    """Save default scenes as individual files in data/default_scenes/."""
    data_dir = _find_data_dir()
    if data_dir is None:
        return JSONResponse(status_code=500, content={"ok": False, "error": "data/ directory not found"})
    body = await request.json()
    scenes = body.get("scenes", [])
    if not scenes:
        return JSONResponse(status_code=400, content={"ok": False, "error": "No scenes provided"})
    scenes_dir = data_dir / "default_scenes"
    scenes_dir.mkdir(exist_ok=True)
    # Clear existing files first
    for old in scenes_dir.glob("scene_*.json"):
        old.unlink()
    for i, scene in enumerate(scenes):
        path = scenes_dir / f"scene_{i + 1}.json"
        path.write_text(json.dumps(scene))
    logger.info("Saved %d default scenes to %s", len(scenes), scenes_dir)
    return JSONResponse(content={"ok": True, "count": len(scenes)})


@app.post("/api/validate-code")
async def validate_code(request: Request):
    """Check if a participant code is valid."""
    body = await request.json()
    code = body.get("code", "").strip()
    if code in VALID_CODES:
        return JSONResponse(content={"valid": True})
    return JSONResponse(status_code=401, content={"valid": False})


# ---------------------------------------------------------------------------
# Study API endpoints
# ---------------------------------------------------------------------------

_STUDY_ASSIGNMENTS: Optional[Dict[str, Any]] = None
_STUDY_STORIES: Optional[Dict[str, Any]] = None
_CONFIG_DIR = BASE_DIR.resolve().parent.parent / "config"


def _load_study_config() -> None:
    """Lazy-load study assignment and story config files."""
    global _STUDY_ASSIGNMENTS, _STUDY_STORIES
    if _STUDY_ASSIGNMENTS is None:
        path = _CONFIG_DIR / "study_assignments.json"
        if path.exists():
            _STUDY_ASSIGNMENTS = json.loads(path.read_text())
        else:
            _STUDY_ASSIGNMENTS = {}
    if _STUDY_STORIES is None:
        path = _CONFIG_DIR / "study_stories.json"
        if path.exists():
            _STUDY_STORIES = json.loads(path.read_text())
        else:
            _STUDY_STORIES = {}


@app.get("/api/study/instructions")
async def study_instructions():
    """Return written instruction paragraphs and oral audio URLs."""
    data_dir = _find_data_dir()
    paragraphs = []
    if data_dir:
        txt_path = data_dir / "written_instructions.txt"
        if txt_path.exists():
            text = txt_path.read_text().strip()
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    audio_files = [
        "/oral-instructions/1_1_intro.wav",
        "/oral-instructions/1_2_pictures.wav",
        "/oral-instructions/1_3_next.wav",
        "/oral-instructions/1_4_practice.wav",
    ]
    return JSONResponse(content={
        "paragraphs": paragraphs,
        "audio": audio_files,
        "post_training_paragraph": paragraphs[4] if len(paragraphs) > 4 else "",
        "post_training_audio": "/oral-instructions/2_end_practice.wav",
        "between_stories_paragraph": paragraphs[5] if len(paragraphs) > 5 else "",
        "between_stories_audio": "/oral-instructions/3_continue.wav",
        "end_paragraph": paragraphs[6] if len(paragraphs) > 6 else "",
        "end_audio": "/oral-instructions/4_end.wav",
    })


@app.post("/api/study/validate")
async def study_validate(request: Request):
    """Validate a study participant number (1-8)."""
    body = await request.json()
    num = body.get("number")
    try:
        num = int(num)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"valid": False})
    if 1 <= num <= 8:
        return JSONResponse(content={"valid": True})
    return JSONResponse(status_code=400, content={"valid": False})


def _parse_study_order(order: List[str]) -> tuple:
    """Parse +/- notation order into (order_labels, animated_set).

    E.g. ["A+", "C-", "B+", "D-"] -> (["A","C","B","D"], {"A","B"})
    """
    labels = []
    animated = set()
    for entry in order:
        label = entry[:-1]  # strip +/-
        labels.append(label)
        if entry.endswith("+"):
            animated.add(label)
    return labels, animated


def _load_study_scene(data_dir: Path, story_meta: Dict[str, Any], scene_num: int) -> Optional[Dict[str, Any]]:
    """Load a single study scene from disk.

    Supports two formats:
    1. Legacy JSON (scene_X.json with sprite_code) from scene_dir
    2. HD images (hd/ + assets/) from image_dir, with metadata from story_def
    """
    # --- Try HD image format first (image_dir) ---
    image_dir_rel = story_meta.get("image_dir", "")
    if image_dir_rel:
        image_dir = data_dir.parent / image_dir_rel
        bg_path = image_dir / "hd" / f"scene_{scene_num}_bg.png"
        if bg_path.exists():
            # Derive the story key from the image_dir (last component, e.g. "A")
            story_key = Path(image_dir_rel).name

            # Load metadata from story definition JSON
            scene_meta: Dict[str, Any] = {}
            characters: Dict[str, str] = {}
            story_def_rel = story_meta.get("story_def", "")
            if story_def_rel:
                story_def_path = data_dir.parent / story_def_rel
                if story_def_path.exists():
                    try:
                        story_def = json.loads(story_def_path.read_text())
                        characters = story_def.get("entities", story_def.get("characters", {}))
                        scenes = story_def.get("scenes", [])
                        if 0 < scene_num <= len(scenes):
                            scene_meta = scenes[scene_num - 1]
                    except (json.JSONDecodeError, OSError):
                        pass

            # Collect entity asset images for this scene
            asset_dir = image_dir / "assets"
            entity_map: Dict[str, str] = {}
            if asset_dir.is_dir():
                pattern = f"withoutbg-scene_{scene_num}_*.png"
                for f in asset_dir.glob(pattern):
                    # Extract entity name: withoutbg-scene_1_boy.png -> boy
                    parts = f.stem.split("_", 2)  # withoutbg-scene, 1, boy
                    entity_id = parts[2] if len(parts) > 2 else f.stem
                    entity_map[entity_id] = f"/study-assets/{story_key}/assets/{f.name}"

            # Order entities according to entities_in_scene (draw order / z-order)
            ordered_ids = scene_meta.get("entities_in_scene", [])
            entity_urls = []
            for eid in ordered_ids:
                if eid in entity_map:
                    entity_urls.append({"id": eid, "url": entity_map.pop(eid)})
            # Append any remaining assets not listed in entities_in_scene
            for eid, url in sorted(entity_map.items()):
                entity_urls.append({"id": eid, "url": url})

            return {
                "format": "hd",
                "background_url": f"/study-assets/{story_key}/hd/scene_{scene_num}_bg.png",
                "entity_urls": entity_urls,
                "narrative_text": scene_meta.get("full_scene_prompt", ""),
                "manifest": scene_meta,
                "characters": characters,
                "scene_number": scene_num,
            }

    # --- Fallback: legacy JSON scene ---
    scene_dir = story_meta.get("scene_dir", "")
    if not scene_dir:
        return None
    scene_path = data_dir.parent / scene_dir / f"scene_{scene_num}.json"
    if not scene_path.exists():
        return None
    try:
        return json.loads(scene_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load study scene %s: %s", scene_path, exc)
        return None


@app.get("/api/study/assignment")
async def study_assignment(participant: int):
    """Return the study assignment for a participant number."""
    _load_study_config()
    key = str(participant)
    assignment = (_STUDY_ASSIGNMENTS or {}).get(key)
    if not assignment:
        return JSONResponse(status_code=404, content={"error": "Unknown participant number"})

    stories_config = _STUDY_STORIES or {}
    raw_order = assignment["order"]
    order_labels, animated_set = _parse_study_order(raw_order)

    # Load first scene of each story for thumbnails
    data_dir = _find_data_dir()
    stories: Dict[str, Any] = {}
    for label in ["A", "B", "C", "D"]:
        story_meta = stories_config.get(label, {})
        scene_data: Dict[str, Any] = {
            "name": story_meta.get("name", f"Story {label}"),
            "scene_count": story_meta.get("scene_count", 0),
            "animated": label in animated_set,
        }
        if data_dir:
            full_scene = _load_study_scene(data_dir, story_meta, 1)
            if full_scene:
                if full_scene.get("format") == "hd":
                    scene_data["format"] = "hd"
                    # Use full scene image (with entities) for thumbnail
                    image_dir_rel = story_meta.get("image_dir", "")
                    story_key = Path(image_dir_rel).name if image_dir_rel else label
                    scene_data["thumbnail_url"] = f"/study-assets/{story_key}/hd/scene_1_full.png"
                else:
                    scene_data["sprite_code"] = full_scene.get("sprite_code", {})
        stories[label] = scene_data

    # Load training scenes (HD format)
    training_scenes: List[Dict[str, Any]] = []
    training_config = stories_config.get("training", [])
    for idx, t_entry in enumerate(training_config):
        image_dir_rel = t_entry.get("image_dir", "")
        if data_dir and image_dir_rel:
            image_dir = data_dir.parent / image_dir_rel
            story_key = Path(image_dir_rel).name
            full_path = image_dir / "hd" / "scene_1_full.png"
            if full_path.exists():
                training_scenes.append({
                    "name": t_entry.get("name", f"Practice {idx+1}"),
                    "thumbnail_url": f"/study-assets/{story_key}/hd/scene_1_full.png",
                    "format": "hd",
                })
            else:
                training_scenes.append({"name": t_entry.get("name", f"Practice {idx+1}")})

    return JSONResponse(content={
        "participant": participant,
        "order": order_labels,
        "raw_order": raw_order,
        "animated": list(animated_set),
        "stories": stories,
        "training_scenes": training_scenes,
    })


@app.get("/api/study/scene")
async def study_scene(story: str, scene: int):
    """Load a specific study scene for playback.

    Returns the full scene data (sprite_code, manifest, narrative_text, ground_truth).
    """
    _load_study_config()
    stories_config = _STUDY_STORIES or {}
    story_key = story.upper()
    story_meta = stories_config.get(story_key) or stories_config.get(story.lower())

    # Training: list of scene defs, indexed by scene number
    if story.lower() == "training" and isinstance(story_meta, list):
        if scene < 1 or scene > len(story_meta):
            return JSONResponse(status_code=400, content={"error": f"Training scene {scene} out of range (1-{len(story_meta)})"})
        story_meta = story_meta[scene - 1]
        story_meta.setdefault("scene_count", 1)
        scene = 1  # each training entry is a single scene

    if not story_meta or not isinstance(story_meta, dict):
        return JSONResponse(status_code=404, content={"error": f"Unknown story {story}"})

    scene_count = story_meta.get("scene_count", 0)
    if scene < 1 or scene > scene_count:
        return JSONResponse(status_code=400, content={"error": f"Scene {scene} out of range (1-{scene_count})"})

    data_dir = _find_data_dir()
    if not data_dir:
        return JSONResponse(status_code=500, content={"error": "data/ directory not found"})

    full_scene = _load_study_scene(data_dir, story_meta, scene)
    if not full_scene:
        return JSONResponse(status_code=404, content={"error": f"Scene {story}/{scene} not found on disk"})

    return JSONResponse(content=full_scene)


@app.get("/api/simulation-scene")
async def api_simulation_scene():
    """Return the last scene generated with participant_id=simulation."""
    data_dir = _find_data_dir()
    if data_dir is None:
        return JSONResponse(status_code=404, content={"error": "data/ directory not found"})
    cache_path = data_dir / "simulation_scene_cache.json"
    if not cache_path.exists():
        return JSONResponse(status_code=404, content={"error": "No simulation scene cached yet — generate one first"})
    return JSONResponse(content=json.loads(cache_path.read_text()))


@app.post("/api/report")
async def api_report(request: Request):
    """Generate a post-session SLP report."""
    body = await request.json()
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing api_key"},
        )

    session_log = body.get("session_log", {})
    profile_data = body.get("student_profile", {})
    profile = StudentProfile.model_validate(profile_data)

    try:
        report = await generate_report(
            api_key=api_key,
            session_log=session_log,
            student_profile=profile,
        )
        return JSONResponse(content={"report": report})
    except Exception as e:
        logger.exception("Failed to generate report")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


# ---------------------------------------------------------------------------
# WebSocket adapter
# ---------------------------------------------------------------------------

class _WebSocketAdapter:
    """Adapts FastAPI WebSocket to a simpler interface."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send_json(self, data: Dict[str, Any]) -> None:
        try:
            if self._ws.client_state == WebSocketState.CONNECTED:
                await self._ws.send_json(data)
        except Exception:
            pass  # client disconnected

    async def send_bytes(self, data: bytes) -> None:
        try:
            if self._ws.client_state == WebSocketState.CONNECTED:
                await self._ws.send_bytes(data)
        except Exception:
            pass  # client disconnected



# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

@app.websocket("/ws/study")
async def study_websocket_endpoint(websocket: WebSocket):
    """WebSocket for study mode — reuses main handler with study_mode flag.

    Study mode differences:
    - Pre-generated scenes loaded via 'study_scene_loaded' message
    - 'animated' param controls whether animations/voice fire (+ vs - condition)
    - Assessment always runs and is logged regardless of condition
    """
    await websocket.accept()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    participant = websocket.query_params.get("participant", "")
    story_key = websocket.query_params.get("story", "")
    is_animated = websocket.query_params.get("animated", "0") == "1"

    if not api_key:
        await websocket.send_json({"type": "error", "message": "Missing API key"})
        await websocket.close()
        return

    session = SessionState(api_key, str(participant))
    session.student_profile.age = 8
    session.naming_phase = False  # Study: no naming phase
    session.awaiting_ending_choice = False  # Study: no ending choice
    # Store animation flag on session for use in audio handler
    session.study_animations_enabled = is_animated  # type: ignore[attr-defined]
    session.study_story_key = story_key  # type: ignore[attr-defined]
    session._study_pending_anim_log = None  # type: ignore[attr-defined]
    ws = _WebSocketAdapter(websocket)

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message:
                await _handle_study_audio(session, message["bytes"], ws)
                continue

            if "text" not in message:
                continue

            data = json.loads(message["text"])
            msg_type = data.get("type", "")

            if msg_type == "study_scene_loaded":
                # Flush pending animation as unresolved before switching scene
                if session._study_pending_anim_log:
                    pending = session._study_pending_anim_log
                    pending["displayed"] = pending.get("displayed", True)
                    pending["resolved"] = False  # scene changed → unresolved
                    _pid_flush = str(participant)
                    _is_training_flush = story_key.lower().startswith("training")
                    append_study_log_entry(_pid_flush, _is_training_flush, story_key, pending.pop("_scene_num", 1), pending)
                    session._study_pending_anim_log = None
                    session._study_previous_discrepancy = None

                scene = data.get("scene")
                if scene:
                    await _hydrate_scene(session, scene, ws)
                    # Log scene change
                    _pid = str(participant)
                    _story_key = story_key
                    _scene_num = scene.get("scene_number", 1)
                    _is_training = _story_key.lower().startswith("training")
                    append_study_log_entry(_pid, _is_training, _story_key, _scene_num, {
                        "event": "scene_loaded",
                        "story": _story_key,
                        "scene": _scene_num,
                    })
            elif msg_type == "interrupt":
                # Child started speaking again before animation was displayed
                if session._study_pending_anim_log:
                    session._study_pending_anim_log["displayed"] = False

            elif msg_type == "entity_moved":
                await _handle_entity_moved(session, data)

    except WebSocketDisconnect:
        logger.info("Study client disconnected: participant=%s story=%s",
                     participant, story_key)
    except Exception:
        logger.exception("Study WebSocket error for participant=%s", participant)
        try:
            await websocket.send_json({"type": "error", "message": "Internal server error"})
        except Exception:
            pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    participant_id = websocket.query_params.get("participant_id", "")
    child_age_str = websocket.query_params.get("child_age", "8")

    if not api_key:
        await websocket.send_json({"type": "error", "message": "Missing API key"})
        await websocket.close()
        return

    session = SessionState(api_key, participant_id)

    # Set child's age on the student profile
    try:
        session.student_profile.age = max(7, min(15, int(child_age_str)))
    except (ValueError, TypeError):
        session.student_profile.age = 8
    ws = _WebSocketAdapter(websocket)

    try:
        while True:
            message = await websocket.receive()

            # Handle binary audio data
            if "bytes" in message:
                await _handle_audio(session, message["bytes"], ws)
                continue

            # Handle text messages
            if "text" not in message:
                continue

            data = json.loads(message["text"])
            msg_type = data.get("type", "")

            if msg_type == "generate_initial_scenes":
                count = data.get("count", INITIAL_SCENE_COUNT)
                await _handle_generate_initial_scenes(session, ws, count=count)

            elif msg_type == "init_scene":
                # Client navigated to /story with a scene — re-hydrate
                scene = data.get("scene")
                story_idx = data.get("story_index", 0)
                if scene:
                    if story_idx:
                        session.current_story_index = story_idx
                    await _hydrate_scene(session, scene, ws)

            elif msg_type == "story_ready":
                # Fallback: scene already set, send initial guidance
                if session.current_scene and not session.conversation_history:
                    await _send_initial_guidance(session, ws)

            elif msg_type == "select_scene":
                index = data.get("index", 0)
                inline_scene = data.get("scene")
                await _handle_select_scene(session, ws, index, inline_scene=inline_scene)

            elif msg_type == "generate_one_more":
                await _handle_generate_one_more(session, ws)

            elif msg_type == "entity_moved":
                await _handle_entity_moved(session, data)

            elif msg_type == "audio":
                # Audio metadata header — next binary message is audio data
                # (kept for protocol compatibility, actual audio in binary frame)
                pass

    except WebSocketDisconnect:
        logger.info("Client disconnected: %s", participant_id)
    except Exception:
        logger.exception("WebSocket error for %s", participant_id)
        try:
            await websocket.send_json({"type": "error", "message": "Internal server error"})
        except Exception:
            pass
    finally:
        if participant_id:
            try:
                save_student_profile(participant_id, session.student_profile)
                logger.info("Saved profile on disconnect for %s", participant_id)
            except Exception:
                logger.exception("Failed to save profile on disconnect for %s",
                                 participant_id)


# ---------------------------------------------------------------------------
# Entity drag-and-drop
# ---------------------------------------------------------------------------

async def _handle_entity_moved(session: SessionState, data: Dict[str, Any]) -> None:
    """Update entity position after client drag-and-drop."""
    entity_id = data.get("entity_id", "")
    position = data.get("position")  # {x, y} normalized 0-1
    art_position = data.get("art_position")  # {x, y} art-grid top-left

    if not entity_id or not position:
        return

    norm_x = float(position.get("x", 0.5))
    norm_y = float(position.get("y", 0.5))

    # Update manifest
    if session.current_manifest:
        entity = session.current_manifest.get_entity(entity_id)
        if entity:
            entity.position.x = norm_x
            entity.position.y = norm_y

    # Update story_state
    pos_dict = {"x": norm_x, "y": norm_y}
    if entity_id in session.story_state.active_entities:
        session.story_state.active_entities[entity_id].last_position = pos_dict
    if entity_id in session.story_state.entity_history:
        session.story_state.entity_history[entity_id]["last_position"] = pos_dict

    # Update raw_sprite x/y in current_scene and sprite_archive
    if art_position:
        art_x = int(art_position.get("x", 0))
        art_y = int(art_position.get("y", 0))

        if session.current_scene:
            sc = session.current_scene.get("sprite_code", {})
            if (entity_id in sc
                    and isinstance(sc[entity_id], dict)
                    and sc[entity_id].get("format") == "raw_sprite"):
                sc[entity_id]["x"] = art_x
                sc[entity_id]["y"] = art_y

        if entity_id in session.story_state.sprite_archive:
            session.story_state.sprite_archive[entity_id]["x"] = art_x
            session.story_state.sprite_archive[entity_id]["y"] = art_y

    logger.debug("[drag] Entity %s moved to (%.2f, %.2f)", entity_id, norm_x, norm_y)


# ---------------------------------------------------------------------------
# Initial scene generation (selection page)
# ---------------------------------------------------------------------------

async def _handle_generate_initial_scenes(
    session: SessionState,
    ws: _WebSocketAdapter,
    count: int = INITIAL_SCENE_COUNT,
) -> None:
    """Generate scenes in parallel for the selection page."""
    n = max(1, min(count, 12))
    # Pick n DISTINCT themes so the 3 initial scenes are always diverse
    themes = random.sample(STORY_THEMES, min(n, len(STORY_THEMES)))

    async def _gen_one(index: int) -> None:
        try:
            theme = themes[index]
            t_scene = time.time()

            await ws.send_json({
                "type": "generation_step",
                "scene_index": index,
                "total_scenes": n,
                "step": "manifest",
            })

            t0 = time.time()
            manifest, raw_data = await generate_scene_manifest(
                api_key=session.api_key,
                story_state=None,
                student_profile=session.student_profile,
                theme=theme,
                previous_manifest=None,
            )
            logger.info(
                "[pipeline] Scene %d: manifest took %.1fs",
                index, time.time() - t0,
            )

            await ws.send_json({
                "type": "generation_step",
                "scene_index": index,
                "total_scenes": n,
                "step": "images",
            })

            async def _progress_cb(step: str) -> None:
                await ws.send_json({
                    "type": "generation_step",
                    "scene_index": index,
                    "total_scenes": n,
                    "step": step,
                })

            t1 = time.time()
            assets = await generate_scene_assets(
                api_key=session.api_key,
                manifest_data=raw_data,
                story_state=None,
                progress_callback=_progress_cb,
            )
            logger.info(
                "[pipeline] Scene %d: assets took %.1fs",
                index, time.time() - t1,
            )

            scene = _assemble_scene_dict(raw_data, manifest, assets)

            # Store in session for later selection
            session.initial_scenes.append({"index": index, "scene": scene})

            await ws.send_json({
                "type": "scene_ready",
                "scene": scene,
                "scene_index": index,
            })

            if session.participant_id == "simulation":
                _save_simulation_cache(scene)

            logger.info(
                "[pipeline] Scene %d: total %.1fs",
                index, time.time() - t_scene,
            )

        except Exception as e:
            logger.exception("Failed to generate initial scene %d", index)
            await ws.send_json({
                "type": "error",
                "message": f"Scene {index} failed: {e}",
            })

    t_all = time.time()
    await asyncio.gather(*[_gen_one(i) for i in range(n)])
    logger.info("[pipeline] All %d initial scenes took %.1fs", n, time.time() - t_all)

    await ws.send_json({"type": "initial_scenes_done"})


async def _handle_select_scene(
    session: SessionState,
    ws: _WebSocketAdapter,
    index: int,
    inline_scene: Optional[Dict[str, Any]] = None,
) -> None:
    """Handle scene selection from the selection page."""
    # Find the scene by index, or use inline scene (default-scenes case)
    scene = inline_scene
    if scene is None:
        for entry in session.initial_scenes:
            if entry["index"] == index:
                scene = entry["scene"]
                break

    if scene is None:
        await ws.send_json({"type": "error", "message": f"Scene {index} not found"})
        return

    # Deep-copy so mutations (drag & drop) never affect the originals
    scene = copy.deepcopy(scene)

    # Create a new story and persist
    story_idx, _ = create_story(session.participant_id)
    session.current_story_index = story_idx
    save_scene(session.participant_id, story_idx, scene)

    # Hydrate session
    _apply_scene_to_session(session, scene)

    await ws.send_json({"type": "scene_selected_ready", "scene": scene})


async def _handle_generate_one_more(
    session: SessionState,
    ws: _WebSocketAdapter,
) -> None:
    """Generate one additional scene for the selection page."""
    index = len(session.initial_scenes)
    try:
        theme = random.choice(STORY_THEMES)

        manifest, raw_data = await generate_scene_manifest(
            api_key=session.api_key,
            story_state=None,
            student_profile=session.student_profile,
            theme=theme,
            previous_manifest=None,
        )

        assets = await generate_scene_assets(
            api_key=session.api_key,
            manifest_data=raw_data,
            story_state=None,
        )

        scene = _assemble_scene_dict(raw_data, manifest, assets)

        session.initial_scenes.append({"index": index, "scene": scene})

        await ws.send_json({
            "type": "one_more_scene",
            "scene": scene,
            "index": index,
        })

    except Exception as e:
        logger.exception("Failed to generate one more scene")
        await ws.send_json({"type": "error", "message": str(e)})


# ---------------------------------------------------------------------------
# Scene generation (continuation)
# ---------------------------------------------------------------------------

async def _handle_generate_scene(
    session: SessionState,
    ws: _WebSocketAdapter,
    is_continuation: bool = False,
) -> None:
    """Generate a new scene using the 2-step pipeline.

    1. scene_neg_generator → manifest  (Gemini 3 Flash)
    2. scene_generator → sprite_code
    3. Send scene_ready to client
    """
    try:
        await ws.send_json({
            "type": "generation_progress",
            "status": "generating",
        })

        # Context for initial vs. continuation
        theme = ""
        previous_manifest = None

        # Capture accepted utterances from the current scene BEFORE reset
        current_accepted: List[str] = []
        if is_continuation and session.current_scene_log:
            current_accepted = get_accepted_utterances(session.current_scene_log)

        if not is_continuation:
            theme = random.choice(STORY_THEMES)
        else:
            if session.story_state.scenes:
                last = session.story_state.scenes[-1]
                previous_manifest = last.get("manifest")

        # Step 1: Generate manifest
        await ws.send_json({
            "type": "generation_step",
            "step": "manifest",
        })

        manifest, raw_data = await generate_scene_manifest(
            api_key=session.api_key,
            story_state=session.story_state if is_continuation else None,
            student_profile=session.student_profile,
            theme=theme,
            previous_manifest=previous_manifest,
            accepted_utterances=current_accepted if is_continuation else None,
        )

        # Step 2: Generate images + sprites
        await ws.send_json({
            "type": "generation_step",
            "step": "images",
        })

        async def _progress_cb(step: str) -> None:
            await ws.send_json({"type": "generation_step", "step": step})

        assets = await generate_scene_assets(
            api_key=session.api_key,
            manifest_data=raw_data,
            story_state=session.story_state if is_continuation else None,
            progress_callback=_progress_cb,
        )

        # Assemble scene data
        scene = _assemble_scene_dict(raw_data, manifest, assets)

        # Persist
        if not is_continuation:
            story_idx, _ = create_story(session.participant_id)
            session.current_story_index = story_idx
        save_scene(session.participant_id, session.current_story_index, scene)

        # Save previous scene's accepted utterances before reset wipes scene_log
        if is_continuation and current_accepted and session.story_state.scenes:
            session.story_state.scenes[-1]["accepted_utterances"] = current_accepted

        # Hydrate session state
        _apply_scene_to_session(session, scene)
        session.completed_scene_ids.append(manifest.scene_id)

        # Send scene to client
        await ws.send_json({
            "type": "scene_ready",
            "scene": scene,
        })

    except Exception as e:
        logger.exception("Failed to generate scene")
        await ws.send_json({"type": "error", "message": str(e)})


async def _hydrate_scene(
    session: SessionState,
    scene: Dict[str, Any],
    ws: _WebSocketAdapter,
) -> None:
    """Re-hydrate session from a scene dict (e.g. after page navigation)."""
    _apply_scene_to_session(session, scene)
    # In study mode, skip initial oral guidance — system never speaks
    if getattr(session, "study_animations_enabled", None) is not None:
        return
    await _send_initial_guidance(session, ws)


# ---------------------------------------------------------------------------
# Initial guidance (Level 0 — open invitation / story intro)
# ---------------------------------------------------------------------------

async def _generate_story_intro(
    session: SessionState,
    manifest: SceneManifest,
) -> str:
    """Generate a warm story intro that asks the child to name the main character."""
    main_char = manifest.get_main_character()
    char_type = main_char.type if main_char else "character"

    prompt = (
        f"You are introducing a storytelling activity to a child (age {session.student_profile.age}). "
        f"The scene shows a {char_type} and other elements. "
        f"Generate a warm, brief spoken introduction (max 40 words) that: "
        f"1) Says we're going to tell a story together about what's in this picture "
        f"2) Says it's up to THEM to imagine this story and the next scenes "
        f"3) Asks the child to give the {char_type} a name "
        f"Just the spoken text, nothing else."
    )

    try:
        client = genai.Client(api_key=session.api_key)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=_SHORT_LLM_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=256),
                    temperature=1.0,
                ),
            ),
            timeout=_SHORT_LLM_TIMEOUT,
        )
        text = response.text.strip().strip('"')
        logger.info("[intro] Generated story intro: %s", text)
        return text
    except Exception:
        logger.warning("[intro] Failed to generate intro, using fallback")
        return (
            f"We're going to tell a story together! "
            f"It's up to you to imagine this story and the next scenes. "
            f"First, what name would you like to give the {char_type}?"
        )


async def _extract_character_name(
    api_key: str,
    transcription: str,
    entity_type: str,
) -> str:
    """Extract a character name from the child's response."""
    prompt = (
        f"A child was asked to name a {entity_type} character in a story. "
        f"They said: \"{transcription}\". "
        f"Extract ONLY the name they gave (a single word or short name). "
        f"If they clearly said a name, return just the name (capitalize first letter). "
        f"If no clear name was given, return an empty string. "
        f"Just the name, nothing else."
    )

    try:
        client = genai.Client(api_key=api_key)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=_SHORT_LLM_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=256),
                    temperature=1.0,
                ),
            ),
            timeout=_SHORT_LLM_TIMEOUT,
        )

        name = response.text.strip()
        logger.debug("[naming] Raw response: '%s'", name)

        # Remove markdown code fences and language tags (be flexible with placement)
        name = re.sub(r'(json|text|code)?\s*```\s*', '', name, flags=re.IGNORECASE)
        name = re.sub(r'~~~\s*', '', name)
        logger.debug("[naming] After removing code fences: '%s'", name)

        # Remove all special JSON/formatting characters: {, }, ", ', :, =, etc.
        # This handles artifacts like {name: "Charlie"} or "Charlie"
        name = re.sub(r'[{}\[\]"\'`:=,;]', ' ', name)
        logger.debug("[naming] After removing special chars: '%s'", name)

        # Reserved words that are likely not real names
        reserved_words = {'json', 'text', 'code', 'name', 'value', 'true', 'false', 'null', 'the', 'a', 'an'}

        # Extract words (sequences of letters, hyphens, apostrophes)
        words = re.findall(r"[A-Za-z][A-Za-z\-']*", name)
        logger.debug("[naming] Extracted words: %s", words)

        # Filter: not reserved, valid length (1-20 characters)
        valid_names = [w for w in words if 1 <= len(w) <= 20 and w.lower() not in reserved_words]
        logger.debug("[naming] Valid names after filtering: %s", valid_names)

        name = valid_names[0] if valid_names else ""

        if not name:
            logger.warning("[naming] No valid name extracted from: '%s'", response.text.strip())
            return ""

        # Capitalize first letter
        name = name.capitalize()
        logger.info("[naming] Final extracted name: '%s' from transcription '%s'", name, transcription)
        return name
    except Exception as e:
        logger.warning("[naming] Name extraction failed: %s", e)
        return ""


async def _handle_naming(
    session: SessionState,
    ws: _WebSocketAdapter,
    transcription: str,
) -> None:
    """Handle the naming phase: extract name from child's utterance and store it."""
    main_char = session.current_manifest.get_main_character()
    if not main_char:
        session.naming_phase = False
        return

    name = await _extract_character_name(
        session.api_key, transcription, main_char.type,
    )

    if name:
        # Store the name everywhere
        session.character_names[main_char.id] = name
        # Update the manifest entity (in-memory)
        main_char.name = name
        # Update story state for persistence across scenes
        if main_char.id in session.story_state.active_entities:
            session.story_state.active_entities[main_char.id].name = name

        # Persist in student profile (keyed by entity type for cross-session use)
        session.student_profile.character_names[main_char.type] = name

        session.naming_phase = False

        confirm = (
            f"{name} the {main_char.type}! I love that name! "
            f"Now, tell me what you see in the picture."
        )

        session.conversation_history.append({
            "role": "system",
            "text": confirm,
            "action": "naming_confirmation",
        })
        await ws.send_json({
            "type": "assessment_result",
            "action": "oral_guidance",
            "guidance_text": confirm,
        })
        asyncio.ensure_future(send_voice(session, ws, confirm))
        logger.info("[naming] Character %s named '%s'", main_char.id, name)
    else:
        # Couldn't extract a name — ask again gently
        retry = f"What name would you like to give the {main_char.type}?"
        await ws.send_json({
            "type": "assessment_result",
            "action": "oral_guidance",
            "guidance_text": retry,
        })
        asyncio.ensure_future(send_voice(session, ws, retry))


async def _send_initial_guidance(
    session: SessionState,
    ws: _WebSocketAdapter,
) -> None:
    """Send initial voice guidance when a scene starts."""
    if session.naming_phase:
        # First scene — generate intro asking for character name
        guidance_text = await _generate_story_intro(
            session, session.current_manifest,
        )
    else:
        guidance_text = "What do you see?"

    session.conversation_history.append({
        "role": "system",
        "text": guidance_text,
        "action": "oral_guidance",
    })

    await ws.send_json({
        "type": "assessment_result",
        "action": "oral_guidance",
        "guidance_text": guidance_text,
    })

    # TTS (fire-and-forget, serialized via voice lock)
    asyncio.ensure_future(send_voice(session, ws, guidance_text))


# ---------------------------------------------------------------------------
# Study audio handling
# ---------------------------------------------------------------------------

async def _handle_study_audio(
    session: SessionState,
    audio_bytes: bytes,
    ws: _WebSocketAdapter,
) -> None:
    """Study mode audio handler — restructured pipeline.

    Pipeline:
      1. Transcribe
      2. Resolution check + MISL detection (parallel)
      3. Update logs (resolution result + mention_counts)
      4. Path A: corrections + Path B: deterministic selector → enrichment (parallel)
      5. Animation handler: errors > suggestions, fixed priority
    """
    if not session.current_manifest or not session.current_scene_log:
        return

    try:
        # --- Study log identifiers ---
        _pid = session.participant_id
        _story_key = getattr(session, "study_story_key", "")
        _scene_num = session.current_scene.get("scene_number", 1) if session.current_scene else 1
        _is_training = _story_key.lower().startswith("training")

        # ── Flush pending animation log from previous cycle ──
        if session._study_pending_anim_log:
            pending = session._study_pending_anim_log
            if "displayed" not in pending:
                pending["displayed"] = True  # no interrupt → was displayed
            append_study_log_entry(_pid, _is_training, _story_key, pending.pop("_scene_num", _scene_num), pending)
            session._study_pending_anim_log = None

        _scene = session.current_scene or {}
        _sm = _scene.get("manifest") or _scene
        misl_targets = _sm.get("misl_targets") or _scene.get("misl_targets")
        scene_description = _sm.get("scene_description") or _scene.get("scene_description", "")
        entities_in_scene = _sm.get("entities_in_scene") or _scene.get("entities_in_scene", [])
        story_so_far = list(session.story_utterances)  # full story, not just scene

        # ── Step 1: Transcribe ──
        transcription = await transcribe_audio(
            api_key=session.api_key,
            audio_bytes=audio_bytes,
            narration_history=session.narration_history,
            narrative_text="",
        )
        if not transcription:
            return

        logger.info("\033[92m[TRANSCRIPTION]\033[0m %s", transcription)

        # Send transcription to client
        await ws.send_json({"type": "study_log", "tag": "TRANSCRIPTION", "text": transcription})

        # Log transcription
        append_study_log_entry(_pid, _is_training, _story_key, _scene_num, {
            "event": "transcription",
            "text": transcription,
        })

        # ── Step 2: Resolution check + MISL detection (parallel) ──
        previous_disc = getattr(session, "_study_previous_discrepancy", None)
        previous_rationale = previous_disc.description if previous_disc else None
        resolution_result, detected_misl = await asyncio.gather(
            assess_resolution(
                api_key=session.api_key,
                utterance_text=transcription,
                previous_rationale=previous_rationale,
                scene_description=scene_description,
            ),
            detect_misl_elements(
                api_key=session.api_key,
                utterance_text=transcription,
            ),
        )

        # ── Step 3: Update logs ──
        # 3a. Log resolution (caller logs animation_id/pass_type/misl_element from previous_disc)
        if resolution_result is not None:
            append_study_log_entry(_pid, _is_training, _story_key, _scene_num, {
                "event": "resolution",
                "resolved": resolution_result,
                "animation_id": previous_disc.animation_id if previous_disc else None,
                "pass_type": previous_disc.pass_type if previous_disc else None,
                "misl_element": previous_disc.misl_elements[0] if previous_disc and previous_disc.misl_elements else None,
            })

        # 3b. Update mention_counts from detected MISL elements
        for code in detected_misl:
            session.current_scene_log.mention_counts[code] = (
                session.current_scene_log.mention_counts.get(code, 0) + 1
            )

        # Load study log entries for deterministic selection
        _log_data = load_study_log(_pid, _is_training)
        _all_entries = []
        for entries in _log_data.get("scenes", {}).values():
            _all_entries.extend(entries)

        # ── Step 4: Parallel paths ──
        # Path A: Corrections (Gemini)
        async def _run_corrections():
            try:
                return await assess_corrections(
                    api_key=session.api_key,
                    utterance_text=transcription,
                    story_so_far=story_so_far,
                    scene_description=scene_description,
                    character_names=session.character_names,
                )
            except Exception as exc:
                logger.error("[study] Correction pass failed: %s", exc)
                return [], []

        # Path B: Deterministic selector → Enrichment (Gemini)
        async def _run_enrichment_path():
            if not misl_targets:
                return []
            macro_sel, micro_cands, sel_trace = select_misl_candidates(
                misl_targets=misl_targets,
                mention_counts=session.current_scene_log.mention_counts,
                study_log_entries=_all_entries,
            )
            # Store trace for logging
            session._study_selection_trace = sel_trace

            if macro_sel is None and micro_cands is None:
                return []

            # ── CH shortcut: if macro=CH and unnamed characters exist, force nametag ──
            # Filter out inanimate objects — only living beings get nametags
            _INANIMATE = {"balloon", "box", "train", "cart", "basket", "boat", "gate", "fence", "bridge", "kite", "ball", "cake", "cookie", "jar", "lamp", "lantern", "tent", "wagon", "wheel", "sled"}
            if macro_sel == "CH" and entities_in_scene:
                unnamed = [e for e in entities_in_scene if e not in session.character_names and e not in _INANIMATE]
                if unnamed:
                    target = unnamed[0]
                    logger.info("[study] CH shortcut: forcing I2 nametag on unnamed entity '%s'", target)
                    return [Discrepancy(
                        pass_type="suggestion",
                        type="Identity",
                        target_entities=[target],
                        misl_elements=["CH"],
                        description=f"Give a name to {target} to make the story more personal!",
                        animation_id="I2",
                    )]

            try:
                return await assess_enrichment(
                    api_key=session.api_key,
                    utterance_text=transcription,
                    story_so_far=story_so_far,
                    character_names=session.character_names,
                    misl_targets=misl_targets,
                    entities_in_scene=entities_in_scene,
                    macro_selected=macro_sel,
                    micro_candidates=micro_cands,
                )
            except Exception as exc:
                logger.error("[study] Enrichment pass failed: %s", exc)
                return []

        (corrections_result, suggestions) = await asyncio.gather(
            _run_corrections(), _run_enrichment_path()
        )
        corrections, name_assignments = corrections_result

        # Register name assignments
        if name_assignments:
            for na in name_assignments:
                session.character_names[na["entity_id"]] = na["name"]
            await ws.send_json({"type": "study_log", "tag": "NAMES", "text": str(name_assignments)})

        # Drop nametag suggestion if the entity was just named
        if name_assignments and suggestions:
            named_ids = {na["entity_id"] for na in name_assignments}
            suggestions = [s for s in suggestions if not (s.animation_id == "I2" and any(t in named_ids for t in s.target_entities))]

        session.narration_history.append(transcription)
        session.conversation_history.append({"role": "child", "text": transcription})
        session.student_profile.total_utterances += 1

        # Add to story_utterances only if accepted (no corrections)
        if not corrections:
            session.story_utterances.append(transcription)

        # Log corrections
        if corrections:
            append_study_log_entry(_pid, _is_training, _story_key, _scene_num, {
                "event": "corrections",
                "items": [
                    {"animation_id": d.animation_id, "targets": d.target_entities, "rationale": d.description}
                    for d in corrections
                ],
            })

        # Log suggestions
        if suggestions:
            append_study_log_entry(_pid, _is_training, _story_key, _scene_num, {
                "event": "suggestions",
                "items": [
                    {"animation_id": d.animation_id, "targets": d.target_entities, "rationale": d.description}
                    for d in suggestions
                ],
            })

        # Send mistakes/options to client
        if corrections:
            mistakes = [{"desc": d.description, "targets": d.target_entities} for d in corrections]
            await ws.send_json({"type": "study_log", "tag": "MISTAKES", "text": str(mistakes)})
        if suggestions:
            options = [{"desc": d.description, "targets": d.target_entities, "misl": d.misl_elements} for d in suggestions]
            await ws.send_json({"type": "study_log", "tag": "OPTIONS", "text": str(options)})

        # ── Step 5: Animation handler ──
        # Deterministic: errors > suggestions, fixed category priority
        from src.interaction.tellimation import _CATEGORY_PRIORITY

        # Build selection trace for logging
        sel_trace = getattr(session, "_study_selection_trace", {})

        # Update micro_gemini_selected in trace if Gemini picked one
        if suggestions and sel_trace.get("micro_candidates_shuffled") is not None:
            sel_trace["micro_gemini_selected"] = suggestions[0].misl_elements[0] if suggestions[0].misl_elements else None

        # Log the full pipeline cycle
        chosen_disc = None
        chosen_source = "no_action"
        animation_id = None

        if corrections:
            # Sort by category priority, pick highest
            sorted_corrections = sorted(
                corrections,
                key=lambda d: _CATEGORY_PRIORITY.get(d.type, 99),
            )
            chosen_disc = sorted_corrections[0]
            chosen_source = "error"
        elif suggestions:
            chosen_disc = suggestions[0]
            chosen_source = "suggestion"

        is_animated = getattr(session, "study_animations_enabled", False)
        is_control = not is_animated

        if chosen_disc:
            targets = chosen_disc.target_entities or []
            animation_id = _select_animation_for_discrepancy(chosen_disc)
            if not animation_id:
                chosen_disc = None

        # Log full pipeline trace
        append_study_log_entry(_pid, _is_training, _story_key, _scene_num, {
            "event": "pipeline_cycle",
            "mention_counts": dict(session.current_scene_log.mention_counts),
            "deterministic_selection": sel_trace,
            "errors_found": [
                {"animation_id": d.animation_id, "targets": d.target_entities, "category": d.type, "desc": d.description}
                for d in corrections
            ],
            "suggestion": {
                "misl_element": suggestions[0].misl_elements[0] if suggestions and suggestions[0].misl_elements else None,
                "rationale": suggestions[0].description if suggestions else None,
                "targets": suggestions[0].target_entities if suggestions else None,
                "animation_id": suggestions[0].animation_id if suggestions else None,
            } if suggestions else None,
            "selected": {
                "source": chosen_source,
                "animation_id": animation_id,
                "targets": chosen_disc.target_entities if chosen_disc else None,
            } if chosen_disc else {"source": "no_action", "animation_id": None, "targets": None},
            "action": "control_suppressed" if is_control and chosen_disc else ("triggered" if chosen_disc else "no_action"),
            "condition": "control" if is_control else "animation",
        })

        # Play animation (animated condition only)
        if is_animated and chosen_disc and animation_id:
            targets = chosen_disc.target_entities or []
            template = ANIMATION_ID_TO_TEMPLATE.get(animation_id, "spotlight")

            # For animations with no targets → use ALL scene entities
            if not targets:
                if scene_data and "entities_in_scene" in scene_data:
                    targets = scene_data["entities_in_scene"]

            # Log with rationale + animation
            tag = "CORRECTION" if chosen_source == "error" else "SUGGESTION"
            await ws.send_json({"type": "study_log", "tag": tag, "text": str({
                "rationale": chosen_disc.description,
                "targets": targets,
                "animation": animation_id,
            })})

            # Store animation log as pending — will be flushed at start of next cycle
            # with displayed=true (normal) or displayed=false (child spoke before display)
            session._study_pending_anim_log = {
                "event": "animation_played",
                "animation_id": animation_id,
                "targets": targets,
                "rationale": chosen_disc.description,
                "pass_type": chosen_disc.pass_type,
                "misl_element": chosen_disc.misl_elements[0] if chosen_disc.misl_elements else None,
                "_scene_num": _scene_num,  # internal, popped before logging
            }

            # Load params from grammar JSON (accentuated if last resolution was False)
            anim_params = load_animation_params(animation_id, _all_entries)

            # D4 interjection: inject the correct word from Gemini
            if template == "interjection" and chosen_disc.correction_word:
                anim_params["word"] = chosen_disc.correction_word

            # Send ONE animation — adapt params to template target expectations
            combined_prefix = "|".join(targets) if targets else ""
            if combined_prefix:
                if len(targets) >= 2 and template in ("magnetism", "repel", "causal_push"):
                    # Duo animations: separate A and B prefixes
                    anim_params["entityPrefixA"] = targets[0]
                    anim_params["entityPrefixB"] = targets[1]
                    anim_params["entityPrefix"] = targets[0]
                elif len(targets) >= 2 and template in ("sequential_glow",):
                    # Group animations: pass as array
                    anim_params["entityPrefixes"] = targets
                    anim_params["entityPrefix"] = targets[0]
                else:
                    anim_params["entityPrefix"] = "" if combined_prefix == "scene" else combined_prefix
                decision = {
                    "mode": "use_default",
                    "animation_id": animation_id,
                    "template": template,
                    "params": anim_params,
                    "duration_ms": 3000,
                }
                await _send_animation_message(ws, decision, combined_prefix, chosen_disc.misl_elements[0] if chosen_disc.misl_elements else "character")

        # Store the chosen discrepancy for next cycle's resolution check
        session._study_previous_discrepancy = chosen_disc

    except Exception:
        logger.exception("[study] Assessment/animation failed")


# ---------------------------------------------------------------------------
# Audio handling (interactive loop)
# ---------------------------------------------------------------------------

async def _handle_audio(
    session: SessionState,
    audio_bytes: bytes,
    ws: _WebSocketAdapter,
) -> None:
    """Handle an audio utterance — restructured pipeline.

    Animation-first flow:
      - Animation fires immediately. Voice is DEFERRED.
      - On the NEXT utterance, we check if the child self-corrected.
      - If corrected → success (no voice). If not → failure (fire voice as escalation).

    Pipeline:
      1. Transcribe
      2. Resolve pending animation outcome from previous utterance
      3. Resolution check + MISL detection (parallel)
      4. Update mention_counts
      5. Path A: corrections + Path B: deterministic selector → enrichment (parallel)
      6. process_assessment → deterministic action routing
      7. Execute action (animation + voice)
    """
    if session.current_manifest is None or session.current_scene_log is None:
        await ws.send_json({"type": "error", "message": "No active scene"})
        return

    try:
        # ── Special phases: naming / ending choice need only transcription ──
        if session.naming_phase or session.awaiting_ending_choice:
            transcription = await transcribe_audio(
                api_key=session.api_key,
                audio_bytes=audio_bytes,
                narration_history=session.narration_history,
                narrative_text=(
                    session.current_scene.get("narrative_text", "")
                    if session.current_scene else ""
                ),
            )
            if not transcription:
                return
            if session.naming_phase:
                await _handle_naming(session, ws, transcription)
            else:
                await _handle_ending_choice(session, ws, transcription)
            return

        _scene = session.current_scene or {}
        _sm = _scene.get("manifest") or _scene
        misl_targets = _sm.get("misl_targets") or _scene.get("misl_targets")
        scene_description = _sm.get("scene_description") or _scene.get("scene_description", "")
        entities_in_scene = _sm.get("entities_in_scene") or _scene.get("entities_in_scene", [])
        story_so_far = list(session.story_utterances)  # full story, not just scene

        # ── Step 1: Transcribe ──
        transcription = await transcribe_audio(
            api_key=session.api_key,
            audio_bytes=audio_bytes,
            narration_history=session.narration_history,
            narrative_text=(
                session.current_scene.get("narrative_text", "")
                if session.current_scene else ""
            ),
        )
        if not transcription:
            return

        logger.info("\033[92m[TRANSCRIPTION]\033[0m %s", transcription)

        # Add child utterance to conversation + narration history
        session.narration_history.append(transcription)
        session.conversation_history.append({
            "role": "child",
            "text": transcription,
        })
        session.student_profile.total_utterances += 1

        # ── Step 2: Resolve pending animation outcome ──
        if session.pending_animation is not None:
            voice_fired = await _resolve_pending_animation(session, ws, transcription)
            if voice_fired:
                save_student_profile(session.participant_id, session.student_profile)
                return

        # ── Step 3: Resolution check + MISL detection (parallel) ──
        _prev_disc_for_resolution = getattr(session, "_interactive_previous_discrepancy", None)
        _prev_rationale = _prev_disc_for_resolution.description if _prev_disc_for_resolution else None
        resolution_result, detected_misl = await asyncio.gather(
            assess_resolution(
                api_key=session.api_key,
                utterance_text=transcription,
                previous_rationale=_prev_rationale,
                scene_description=scene_description,
            ),
            detect_misl_elements(
                api_key=session.api_key,
                utterance_text=transcription,
            ),
        )

        # ── Step 4: Update mention_counts ──
        for code in detected_misl:
            session.current_scene_log.mention_counts[code] = (
                session.current_scene_log.mention_counts.get(code, 0) + 1
            )

        # ── Step 5: Parallel paths ──
        async def _run_corrections():
            try:
                return await assess_corrections(
                    api_key=session.api_key,
                    utterance_text=transcription,
                    story_so_far=story_so_far,
                    scene_description=scene_description,
                    character_names=session.character_names,
                )
            except Exception as exc:
                logger.error("[audio] Correction pass failed: %s", exc)
                return [], []

        async def _run_enrichment_path():
            if not misl_targets:
                # No misl_targets → skip enrichment (no legacy fallback)
                return []

            macro_sel, micro_cands, sel_trace = select_misl_candidates(
                misl_targets=misl_targets,
                mention_counts=session.current_scene_log.mention_counts,
                study_log_entries=[],  # interactive mode has no study log
            )
            session._interactive_selection_trace = sel_trace

            if macro_sel is None and micro_cands is None:
                return []

            # ── CH shortcut: if macro=CH and unnamed characters exist, force nametag ──
            _INANIMATE = {"balloon", "box", "train", "cart", "basket", "boat", "gate", "fence", "bridge", "kite", "ball", "cake", "cookie", "jar", "lamp", "lantern", "tent", "wagon", "wheel", "sled"}
            if macro_sel == "CH" and entities_in_scene:
                unnamed = [e for e in entities_in_scene if e not in session.character_names and e not in _INANIMATE]
                if unnamed:
                    target = unnamed[0]
                    logger.info("[audio] CH shortcut: forcing I2 nametag on unnamed entity '%s'", target)
                    return [Discrepancy(
                        pass_type="suggestion",
                        type="Identity",
                        target_entities=[target],
                        misl_elements=["CH"],
                        description=f"Give a name to {target} to make the story more personal!",
                        animation_id="I2",
                    )]

            try:
                return await assess_enrichment(
                    api_key=session.api_key,
                    utterance_text=transcription,
                    story_so_far=story_so_far,
                    character_names=session.character_names,
                    misl_targets=misl_targets,
                    entities_in_scene=entities_in_scene,
                    macro_selected=macro_sel,
                    micro_candidates=micro_cands,
                )
            except Exception as exc:
                logger.error("[audio] Enrichment pass failed: %s", exc)
                return []

        (corrections_result, suggestions) = await asyncio.gather(
            _run_corrections(), _run_enrichment_path()
        )
        corrections, name_assignments = corrections_result

        # Add to story_utterances only if accepted (no corrections)
        if not corrections:
            session.story_utterances.append(transcription)

        # Register name assignments
        if name_assignments and session.character_names is not None:
            for na in name_assignments:
                session.character_names[na["entity_id"]] = na["name"]
                logger.info("[assessment] Registered name: %s → %s", na["entity_id"], na["name"])

        # Drop nametag suggestion if the entity was just named
        if name_assignments and suggestions:
            named_ids = {na["entity_id"] for na in name_assignments}
            suggestions = [s for s in suggestions if not (s.animation_id == "I2" and any(t in named_ids for t in s.target_entities))]

        # Build unified AssessmentResponse for process_assessment
        from src.models.assessment import AssessmentResponse, FactualError, MISLOpportunity
        factual_errors = [
            FactualError(utterance_fragment="", manifest_ref=", ".join(d.target_entities), explanation=d.description)
            for d in corrections
        ]
        misl_opportunities = [
            MISLOpportunity(
                dimension=d.misl_elements[0] if d.misl_elements else d.type,
                manifest_elements=d.target_entities,
                suggestion=d.description,
            )
            for d in suggestions
        ]
        assessment = AssessmentResponse(
            transcription=transcription,
            factual_errors=factual_errors,
            misl_opportunities=misl_opportunities,
            discrepancies=corrections + suggestions,
            utterance_is_acceptable=len(corrections) == 0,
            name_assignments=name_assignments or [],
            resolution=resolution_result,
        )

        # ── Console log: assessment results ──
        for disc in assessment.discrepancies:
            tag = "\033[91m[CORRECTION]\033[0m" if disc.pass_type == "correction" else "\033[93m[SUGGESTION]\033[0m"
            logger.info(
                "%s type=%s  targets=%s  misl=%s  desc=%s",
                tag, disc.type,
                disc.target_entities, disc.misl_elements,
                disc.description,
            )

        # ── Step 6: Deterministic decision ──
        action, action_data = process_assessment(
            utterance_text=transcription,
            response=assessment,
            scene_log=session.current_scene_log,
            misl_difficulty_profile=session.student_profile.misl_difficulty_profile,
        )

        logger.info("\033[96m[DECISION]\033[0m action=%s", action)

        # ── Step 7: Execute action ──
        discrepancies: List[Discrepancy] = []
        if action_data and "discrepancies" in action_data:
            for d in action_data["discrepancies"]:
                if isinstance(d, dict):
                    discrepancies.append(Discrepancy(**d))
                elif isinstance(d, Discrepancy):
                    discrepancies.append(d)

        if action == "correct":
            errors = action_data["factual_errors"]
            first_error = errors[0] if errors else {}
            target_id = first_error.get("manifest_ref", "").split(".")[0]
            explanation = first_error.get("explanation", "")

            if not target_id and discrepancies:
                first_disc = discrepancies[0]
                target_id = first_disc.target_entities[0] if first_disc.target_entities else ""
                explanation = explanation or first_disc.description

            await ws.send_json({
                "type": "assessment_result",
                "action": "correct",
                "target_id": target_id,
                "errors": errors,
            })

            misl_el = "character"
            if discrepancies and discrepancies[0].misl_elements:
                misl_el = discrepancies[0].misl_elements[0]
            esc_key = f"{target_id}::{misl_el}"

            if esc_key in session.voice_escalated_errors:
                if explanation:
                    session.conversation_history.append({
                        "role": "system",
                        "text": explanation,
                        "action": "repeat_voice_guidance",
                    })
                    asyncio.ensure_future(send_voice(session, ws, explanation))

            elif discrepancies:
                decision = await execute_invocation_array(
                    session, ws, discrepancies,
                )

                if decision and explanation:
                    _set_pending_animation(
                        session, target_id, misl_el, explanation, decision,
                    )
                elif explanation:
                    await ws.send_json({
                        "type": "correction_text",
                        "guidance_text": explanation,
                        "target_id": target_id,
                    })
                    session.conversation_history.append({
                        "role": "system",
                        "text": explanation,
                        "action": "correction",
                    })

            elif target_id:
                decision = await execute_animation(
                    session, ws, target_id,
                    misl_element=misl_el,
                    problematic_segment=first_error.get("utterance_fragment"),
                )

                if decision and explanation:
                    _set_pending_animation(
                        session, target_id, misl_el, explanation, decision,
                    )
                elif explanation:
                    await ws.send_json({
                        "type": "correction_text",
                        "guidance_text": explanation,
                        "target_id": target_id,
                    })
                    session.conversation_history.append({
                        "role": "system",
                        "text": explanation,
                        "action": "correction",
                    })

            elif explanation:
                await ws.send_json({
                    "type": "correction_text",
                    "guidance_text": explanation,
                    "target_id": "",
                })
                session.conversation_history.append({
                    "role": "system",
                    "text": explanation,
                    "action": "correction",
                })
                asyncio.ensure_future(send_voice(session, ws, explanation))

            # Store discrepancy for next resolution check
            session._interactive_previous_discrepancy = discrepancies[0] if discrepancies else None

        elif action == "accept_and_guide":
            opps = action_data["misl_opportunities"]
            first_opp = opps[0] if opps else {}
            suggestion = first_opp.get("suggestion", "")
            elements = first_opp.get("manifest_elements", [])
            target_id = elements[0] if elements else ""
            misl_dim = first_opp.get("dimension", "character")

            suggestion_discrepancies = [d for d in discrepancies if d.pass_type == "suggestion"]
            if not target_id and suggestion_discrepancies:
                first_sd = suggestion_discrepancies[0]
                target_id = first_sd.target_entities[0] if first_sd.target_entities else ""
                suggestion = suggestion or first_sd.description

            is_last_opportunity = (
                session.current_scene_log.misl_opportunities_given
                >= MAX_MISL_OPPORTUNITIES_PER_SCENE
            )

            if is_last_opportunity:
                logger.info("[scene] Last MISL opportunity given → auto-advancing")
                await _advance_scene(session, ws)
            else:
                await ws.send_json({
                    "type": "assessment_result",
                    "action": "guide",
                    "target_id": target_id,
                    "dimension": misl_dim,
                })

                esc_key = f"{target_id}::{misl_dim}"

                if esc_key in session.voice_escalated_errors:
                    if suggestion:
                        session.conversation_history.append({
                            "role": "system",
                            "text": suggestion,
                            "action": "repeat_voice_guidance",
                        })
                        asyncio.ensure_future(send_voice(session, ws, suggestion))

                elif suggestion_discrepancies:
                    decision = await execute_invocation_array(
                        session, ws, suggestion_discrepancies,
                    )

                    if decision and suggestion:
                        _set_pending_animation(
                            session, target_id, misl_dim, suggestion, decision,
                        )
                    elif suggestion:
                        await ws.send_json({
                            "type": "guidance_text",
                            "guidance_text": suggestion,
                            "target_id": target_id,
                            "dimension": misl_dim,
                        })
                        session.conversation_history.append({
                            "role": "system",
                            "text": suggestion,
                            "action": "misl_guidance",
                        })

                elif target_id:
                    decision = await execute_animation(
                        session, ws, target_id,
                        misl_element=misl_dim,
                    )

                    if decision and suggestion:
                        _set_pending_animation(
                            session, target_id, misl_dim, suggestion, decision,
                        )
                    elif suggestion:
                        await ws.send_json({
                            "type": "guidance_text",
                            "guidance_text": suggestion,
                            "target_id": target_id,
                            "dimension": misl_dim,
                        })
                        session.conversation_history.append({
                            "role": "system",
                            "text": suggestion,
                            "action": "misl_guidance",
                        })

                elif suggestion:
                    await ws.send_json({
                        "type": "guidance_text",
                        "guidance_text": suggestion,
                        "target_id": "",
                        "dimension": misl_dim,
                    })
                    session.conversation_history.append({
                        "role": "system",
                        "text": suggestion,
                        "action": "misl_guidance",
                    })
                    asyncio.ensure_future(send_voice(session, ws, suggestion))

            # Store discrepancy for next resolution check
            session._interactive_previous_discrepancy = suggestion_discrepancies[0] if suggestion_discrepancies else None

        elif action == "accept_and_advance":
            await _advance_scene(session, ws)
            session._interactive_previous_discrepancy = None

        # Persist profile after every utterance
        save_student_profile(session.participant_id, session.student_profile)

    except Exception as e:
        logger.exception("Audio processing error")
        await ws.send_json({"type": "error", "message": str(e)})


async def _advance_scene(session: SessionState, ws: _WebSocketAdapter) -> None:
    """Intercept scene advance to ask the child about ending their story."""
    session.ending_phase = True  # permanent once first scene completes
    await _ask_ending_choice(session, ws)


async def _do_advance_scene(session: SessionState, ws: _WebSocketAdapter) -> None:
    """Signal client that the scene is transitioning, then generate the next scene."""
    await ws.send_json({"type": "scene_transitioning"})

    if len(session.completed_scene_ids) < MAX_SCENES:
        session.student_profile.scenes_completed += 1
        await _handle_generate_scene(session, ws, is_continuation=True)
    else:
        await ws.send_json({"type": "story_complete"})


async def _ask_ending_choice(session: SessionState, ws: _WebSocketAdapter) -> None:
    """Ask the child if they want to imagine an ending or keep going."""
    session.awaiting_ending_choice = True
    await ws.send_json({"type": "ending_choice_prompt"})
    prompt_text = (
        "Would you like to imagine how the story ends? "
        "Tell me the ending! Or if you want to keep going, just say so."
    )
    asyncio.ensure_future(send_voice(session, ws, prompt_text))


async def _classify_ending_intent(api_key: str, transcription: str) -> str:
    """Classify whether the child wants to end the story or continue.

    Returns 'ending' or 'continue'.
    """
    prompt = (
        "A child is telling a story and was asked if they want to imagine "
        "an ending or keep going. They said:\n"
        f'"{transcription}"\n\n'
        "Classify their intent as exactly one of:\n"
        "- ENDING — they are narrating a conclusion, saying goodbye to characters, "
        "or expressing they want to finish the story\n"
        "- CONTINUE — they want to keep going, add more, or their response is "
        "a new story sentence (not an ending)\n\n"
        "Reply with a single word: ENDING or CONTINUE"
    )

    try:
        client = genai.Client(api_key=api_key)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=_SHORT_LLM_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=256),
                    temperature=1.0,
                ),
            ),
            timeout=_SHORT_LLM_TIMEOUT,
        )
        raw = response.text.strip().upper()
        logger.info("[ending] Classification raw: '%s'", raw)
        if "ENDING" in raw:
            return "ending"
        return "continue"
    except Exception:
        logger.warning("[ending] Classification failed, defaulting to continue")
        return "continue"


async def _handle_ending_choice(
    session: SessionState, ws: _WebSocketAdapter, transcription: str,
) -> None:
    """Process the child's response to the ending choice prompt."""
    session.awaiting_ending_choice = False

    intent = await _classify_ending_intent(session.api_key, transcription)
    logger.info("[ending] Child intent: %s", intent)

    if intent == "ending":
        # Add the ending utterance to the story log
        session.narration_history.append(transcription)
        session.conversation_history.append({
            "role": "child",
            "text": transcription,
        })

        # Congratulate and signal story end
        congrats = (
            "What a wonderful ending! You did an amazing job telling this story. "
            "I loved every part of it!"
        )
        await ws.send_json({"type": "story_ended"})
        asyncio.ensure_future(send_voice(session, ws, congrats))
    else:
        # Child wants to continue — proceed with normal scene advance
        await _do_advance_scene(session, ws)


def _set_pending_animation(
    session: SessionState,
    target_id: str,
    misl_element: str,
    voice_text: str,
    decision: Dict[str, Any],
) -> None:
    """Set a pending animation so voice is deferred until next utterance."""
    decisions = getattr(session.student_profile, "animation_decisions", [])
    session.pending_animation = {
        "target_id": target_id,
        "misl_element": misl_element,
        "voice_text": voice_text,
        "decision_idx": len(decisions) - 1 if decisions else -1,
    }


async def _resolve_pending_animation(
    session: SessionState,
    ws: _WebSocketAdapter,
    transcription: str,
) -> bool:
    """Check if the child self-corrected after the previous animation.

    Success = the new utterance is acceptable (child corrected without voice).
    Failure = still has issues → fire deferred voice as escalation.

    Returns True if voice escalation was fired (caller should skip new assessment).
    """
    pending = session.pending_animation
    session.pending_animation = None

    if pending is None:
        return False

    # Quick re-assess to check if the child's new utterance is now acceptable
    try:
        _sc = session.current_scene or {}
        _scm = _sc.get("manifest") or _sc
        scene_desc = _scm.get("scene_description") or _sc.get("scene_description", "")

        quick_corrections, _ = await assess_corrections(
            api_key=session.api_key,
            utterance_text=transcription,
            story_so_far=list(session.story_utterances),
            scene_description=scene_desc,
            character_names=session.character_names,
        )

        corrected = len(quick_corrections) == 0
    except Exception:
        logger.warning("[pending_anim] Quick assessment failed, treating as not corrected")
        corrected = False

    # Mark the decision outcome
    decisions = getattr(session.student_profile, "animation_decisions", [])
    decision_idx = pending.get("decision_idx", -1)
    if 0 <= decision_idx < len(decisions):
        d = decisions[decision_idx]
        if hasattr(d, "outcome"):
            d.outcome = "success" if corrected else "failure"
            d.escalated_to_voice = not corrected

    if corrected:
        logger.info("[pending_anim] Child self-corrected after animation → success")
        session.student_profile.corrections_after_animation += 1
        # Also mark in legacy animation_efficacy
        if session.last_animation:
            for entry in reversed(session.student_profile.animation_efficacy):
                if entry.get("target_id") == pending["target_id"]:
                    entry["led_to_correction"] = True
                    break
        return False
    else:
        logger.info("[pending_anim] Child did NOT self-correct → failure, firing voice")
        voice_text = pending.get("voice_text", "")
        if voice_text:
            session.conversation_history.append({
                "role": "system",
                "text": voice_text,
                "action": "escalated_voice_guidance",
            })
            asyncio.ensure_future(send_voice(session, ws, voice_text))
            # Record escalation — skip animation on repeat of same error
            esc_key = f"{pending['target_id']}::{pending['misl_element']}"
            session.voice_escalated_errors[esc_key] = voice_text
            return True
        return False


