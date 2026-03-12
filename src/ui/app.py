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
from fastapi.responses import HTMLResponse, JSONResponse
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
from src.interaction.discrepancy_assessment import assess_utterance
from src.models.assessment import SceneLog
from src.models.scene import SceneManifest
from src.models.session_state import SessionState
from src.models.student_profile import StudentProfile
from src.narration.transcription import transcribe_audio
from src.persistence import (
    save_scene,
    save_student_profile,
    create_story,
)
from google import genai
from google.genai import types

from src.ui.animation_handler import execute_animation, send_voice

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
    session.current_manifest = SceneManifest.model_validate(scene["manifest"])
    session.reset_scene_state()

    # Initialize per-scene assessment log
    session.current_scene_log = SceneLog(
        scene_id=scene["manifest"].get("scene_id", ""),
        scene_manifest=scene["manifest"],
    )

    session.story_state.add_scene(
        scene_id=scene["manifest"]["scene_id"],
        narrative_text=scene.get("narrative_text", ""),
        manifest=scene["manifest"],
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


# ---------------------------------------------------------------------------
# HTML page routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def login_page():
    return (TEMPLATES_DIR / "login.html").read_text()


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
                    temperature=0.7,
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
                    temperature=0.0,
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
# Audio handling (interactive loop)
# ---------------------------------------------------------------------------

async def _handle_audio(
    session: SessionState,
    audio_bytes: bytes,
    ws: _WebSocketAdapter,
) -> None:
    """Handle an audio utterance: transcribe → assess → decide → execute.

    Animation-first flow:
      - Animation fires immediately. Voice is DEFERRED.
      - On the NEXT utterance, we check if the child self-corrected.
      - If corrected → success (no voice). If not → failure (fire voice as escalation).

    Pipeline:
      0. Check pending animation outcome from previous utterance
      1. transcribe_audio → text
      2. assess_utterance → factual_errors / misl_opportunities
      3. process_assessment → deterministic action
      4. Execute action (correct / accept_and_guide / accept_and_advance)
    """
    if session.current_manifest is None or session.current_scene_log is None:
        await ws.send_json({"type": "error", "message": "No active scene"})
        return

    try:
        # 1. Transcribe audio
        transcription = await transcribe_audio(
            api_key=session.api_key,
            audio_bytes=audio_bytes,
            narration_history=session.narration_history,
            narrative_text=(
                session.current_scene.get("narrative_text", "")
                if session.current_scene else ""
            ),
        )

        # Send transcription to client
        await ws.send_json({
            "type": "transcription",
            "text": transcription,
        })

        if not transcription:
            return

        # ── Naming phase: first utterance is the character's name ──
        if session.naming_phase:
            await _handle_naming(session, ws, transcription)
            return

        # ── Ending choice phase: child responds to "imagine an ending?" ──
        if session.awaiting_ending_choice:
            await _handle_ending_choice(session, ws, transcription)
            return

        # Add child utterance to conversation + narration history
        session.narration_history.append(transcription)
        session.conversation_history.append({
            "role": "child",
            "text": transcription,
        })
        session.student_profile.total_utterances += 1

        # ── Step 0: Resolve pending animation outcome ──
        if session.pending_animation is not None:
            voice_fired = await _resolve_pending_animation(session, ws, transcription)
            if voice_fired:
                # Voice escalation sent — don't assess this utterance for new
                # errors (would trigger a simultaneous animation).
                save_student_profile(session.participant_id, session.student_profile)
                return

        # 2. Assess utterance against manifest + MISL
        story_so_far = get_accepted_utterances(session.current_scene_log)
        misl_already = get_misl_dimensions_suggested(session.current_scene_log)

        assessment = await assess_utterance(
            api_key=session.api_key,
            manifest=session.current_manifest,
            utterance_text=transcription,
            story_so_far=story_so_far,
            misl_already_suggested=misl_already,
            misl_difficulty_profile=session.student_profile.misl_difficulty_profile,
            character_names=session.character_names,
        )

        # 3. Deterministic decision
        action, action_data = process_assessment(
            utterance_text=transcription,
            response=assessment,
            scene_log=session.current_scene_log,
            misl_difficulty_profile=session.student_profile.misl_difficulty_profile,
        )

        # 4. Execute action
        if action == "correct":
            # Factual errors — animate errored entity, defer voice
            errors = action_data["factual_errors"]
            first_error = errors[0]
            target_id = first_error.get("manifest_ref", "").split(".")[0]
            explanation = first_error.get("explanation", "")

            # Send assessment_result WITHOUT guidance_text (text will come later after animation)
            await ws.send_json({
                "type": "assessment_result",
                "action": "correct",
                "target_id": target_id,
                "errors": errors,
            })

            misl_el = "character"
            esc_key = f"{target_id}::{misl_el}"

            if esc_key in session.voice_escalated_errors:
                # Same error after voice → skip animation, voice only
                if explanation:
                    session.conversation_history.append({
                        "role": "system",
                        "text": explanation,
                        "action": "repeat_voice_guidance",
                    })
                    asyncio.ensure_future(send_voice(session, ws, explanation))

            elif target_id:
                decision = await execute_animation(
                    session, ws, target_id,
                    misl_element=misl_el,
                    problematic_segment=first_error.get("utterance_fragment"),
                )

                # Animation-first: NO text during animation.
                # Voice + text only fires if child doesn't self-correct (see _resolve_pending_animation).
                if decision and explanation:
                    _set_pending_animation(
                        session, target_id, misl_el, explanation, decision,
                    )
                elif explanation:
                    # Animation failed entirely, send correction text now
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
                # No target to animate — send correction text immediately
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

        elif action == "accept_and_guide":
            # MISL guidance — animate, defer text display
            opps = action_data["misl_opportunities"]
            first_opp = opps[0]
            suggestion = first_opp.get("suggestion", "")
            elements = first_opp.get("manifest_elements", [])
            target_id = elements[0] if elements else ""
            misl_dim = first_opp.get("dimension", "character")

            # Was this the LAST MISL opportunity for this scene?
            is_last_opportunity = (
                session.current_scene_log.misl_opportunities_given
                >= MAX_MISL_OPPORTUNITIES_PER_SCENE
            )

            if is_last_opportunity:
                # ── Last suggestion: skip animation, advance directly ──
                logger.info("[scene] Last MISL opportunity given → auto-advancing")
                await _advance_scene(session, ws)
            else:
                # Send assessment_result WITHOUT guidance_text (text will come later after animation)
                await ws.send_json({
                    "type": "assessment_result",
                    "action": "guide",
                    "target_id": target_id,
                    "dimension": misl_dim,
                })

                esc_key = f"{target_id}::{misl_dim}"

                if esc_key in session.voice_escalated_errors:
                    # Same suggestion after voice → skip animation, voice only
                    if suggestion:
                        session.conversation_history.append({
                            "role": "system",
                            "text": suggestion,
                            "action": "repeat_voice_guidance",
                        })
                        asyncio.ensure_future(send_voice(session, ws, suggestion))

                elif target_id:
                    decision = await execute_animation(
                        session, ws, target_id,
                        misl_element=misl_dim,
                    )

                    # Animation-first: NO text during animation.
                    # Voice + text only fires if child doesn't self-correct.
                    if decision and suggestion:
                        _set_pending_animation(
                            session, target_id, misl_dim, suggestion, decision,
                        )
                    elif suggestion:
                        # Animation failed entirely → send guidance text now
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
                    # No target to animate — send guidance text immediately
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

        elif action == "accept_and_advance":
            # Scene complete (no MISL opportunities left) — advance immediately
            await _advance_scene(session, ws)

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
                    temperature=0.0,
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
        story_so_far = get_accepted_utterances(session.current_scene_log)
        misl_already = get_misl_dimensions_suggested(session.current_scene_log)

        quick_assessment = await assess_utterance(
            api_key=session.api_key,
            manifest=session.current_manifest,
            utterance_text=transcription,
            story_so_far=story_so_far,
            misl_already_suggested=misl_already,
            misl_difficulty_profile=session.student_profile.misl_difficulty_profile,
            character_names=session.character_names,
        )

        corrected = quick_assessment.utterance_is_acceptable
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


