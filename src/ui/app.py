"""Tellimations FastAPI web server with WebSocket handler."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from src.analytics.session_report import generate_report
from src.generation.scene_generator import (
    STORY_THEMES,
    generate_scene,
)
from src.models.animation_cache import AnimationCache
from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.story_state import StoryState
from src.models.student_profile import StudentProfile
from src.narration.narration_loop import NarrationLoop
from src.persistence import (
    ensure_participant,
    load_student_profile,
    load_story_first_scenes,
    save_scene,
    save_student_profile,
    story_count,
    create_story,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Maximum number of scenes per story (configurable)
MAX_SCENES = 5

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


# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/report")
async def api_report(request: Request):
    """Generate a post-session SLP report.

    Expects JSON body with:
        api_key: Gemini API key.
        session_log: Full session log dict.
        student_profile: StudentProfile dict.
    """
    body = await request.json()
    api_key = body.get("api_key", "")
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
# WebSocket adapter (so NarrationLoop can send via its protocol)
# ---------------------------------------------------------------------------

class _WebSocketAdapter:
    """Adapts FastAPI WebSocket to the WebSocketLike protocol."""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send_json(self, data: Dict[str, Any]) -> None:
        if self._ws.client_state == WebSocketState.CONNECTED:
            await self._ws.send_json(data)

    async def send_bytes(self, data: bytes) -> None:
        if self._ws.client_state == WebSocketState.CONNECTED:
            await self._ws.send_bytes(data)


# ---------------------------------------------------------------------------
# Per-session state
# ---------------------------------------------------------------------------

class SessionState:
    """Holds all mutable state for a single WebSocket session."""

    def __init__(self, api_key: str, participant_id: str) -> None:
        self.api_key = api_key
        self.participant_id = participant_id

        # Ensure participant directory exists and load persisted profile
        ensure_participant(participant_id)
        self.student_profile = load_student_profile(participant_id)

        self.story_state = StoryState(
            session_id="",
            participant_id=participant_id,
        )
        self.animation_cache = AnimationCache()
        self.current_scene: Optional[Dict[str, Any]] = None
        self.narration_loop: Optional[NarrationLoop] = None
        self.pending_audio_meta: Optional[Dict[str, Any]] = None
        # Current story index (set when a story is created/selected)
        self.current_story_index: int = 0
        # Initial scene candidates (for selection page)
        self.initial_scenes: List[Dict[str, Any]] = []
        # Completed scene IDs
        self.completed_scene_ids: List[str] = []


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # Read credentials from query params
    api_key = websocket.query_params.get("api_key", "")
    participant_id = websocket.query_params.get("participant_id", "")

    if not api_key:
        await websocket.send_json({"type": "error", "message": "Missing API key"})
        await websocket.close()
        return

    session = SessionState(api_key, participant_id)
    ws_adapter = _WebSocketAdapter(websocket)

    try:
        while True:
            message = await websocket.receive()

            # Handle binary audio data
            if "bytes" in message:
                await _handle_binary(session, message["bytes"], ws_adapter, websocket)
                continue

            # Handle text messages
            if "text" not in message:
                continue

            data = json.loads(message["text"])
            msg_type = data.get("type", "")

            if msg_type == "generate_initial_scenes":
                await _handle_generate_initial(session, websocket)

            elif msg_type == "select_scene":
                await _handle_select_scene(session, data, websocket, ws_adapter)

            elif msg_type == "init_scene":
                # Client navigated to /story with a chosen scene — re-hydrate
                # the session state and initialize the narration loop.
                scene = data.get("scene")
                story_idx = data.get("story_index", 0)
                if scene:
                    session.current_scene = scene
                    if story_idx:
                        session.current_story_index = story_idx
                    session.story_state.add_scene(
                        scene_id=scene["manifest"]["scene_id"],
                        narrative_text=scene.get("narrative_text", ""),
                        manifest=scene["manifest"],
                        neg=scene.get("neg", {}),
                        sprite_code=scene.get("sprite_code"),
                    )
                    _init_narration_loop(session, ws_adapter)

            elif msg_type == "story_ready":
                # Fallback: set up narration loop if scene was already set
                if session.current_scene and session.narration_loop is None:
                    _init_narration_loop(session, ws_adapter)

            elif msg_type == "audio":
                # Audio metadata header — next binary message is the audio data
                session.pending_audio_meta = data

    except WebSocketDisconnect:
        logger.info("Client disconnected: %s", participant_id)
    except Exception:
        logger.exception("WebSocket error for %s", participant_id)
        try:
            await websocket.send_json({"type": "error", "message": "Internal server error"})
        except Exception:
            pass
    finally:
        # Persist student profile on disconnect
        if participant_id:
            try:
                save_student_profile(participant_id, session.student_profile)
                logger.info("Saved profile on disconnect for %s", participant_id)
            except Exception:
                logger.exception("Failed to save profile on disconnect for %s", participant_id)


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------

async def _handle_generate_initial(
    session: SessionState,
    websocket: WebSocket,
) -> None:
    """Generate initial scene candidates, or return existing stories.

    Scenes are generated in parallel and streamed to the client
    one-by-one as they complete.
    """
    try:
        pid = session.participant_id
        existing_count = story_count(pid)

        # If participant already has >= 2 stories, send them instead of generating
        if existing_count >= 2:
            existing_scenes = load_story_first_scenes(pid)
            if len(existing_scenes) >= 2:
                session.initial_scenes = existing_scenes
                await websocket.send_json({
                    "type": "initial_scenes",
                    "scenes": existing_scenes,
                    "from_disk": True,
                })
                return

        # Generate fresh scenes in PARALLEL with progress
        total_scenes = 1

        # Notify client that generation is starting
        await websocket.send_json({
            "type": "generation_progress",
            "scene_index": 0,
            "total_scenes": total_scenes,
            "status": "generating",
        })

        # Build progress callbacks — each scene sends its own progress
        def _make_progress_cb(seed: int):
            async def progress_cb(step_name: str) -> None:
                await websocket.send_json({
                    "type": "generation_step",
                    "scene_index": seed,
                    "total_scenes": total_scenes,
                    "step": step_name,
                })
            return progress_cb

        # Pick N random themes from the pool (no duplicates)
        import random
        themes = random.sample(STORY_THEMES, min(total_scenes, len(STORY_THEMES)))

        # Launch all scenes in parallel
        futures = {
            asyncio.ensure_future(
                generate_scene(
                    api_key=session.api_key,
                    story_state=None,
                    student_profile=session.student_profile,
                    theme=theme,
                    commit_to_state=False,
                    skip_masks=True,
                    progress_callback=_make_progress_cb(idx),
                )
            ): idx
            for idx, theme in enumerate(themes, start=1)
        }

        # Stream scenes to client as they complete
        scenes: List[Dict[str, Any]] = []
        for coro in asyncio.as_completed(futures.keys()):
            try:
                result = await coro
            except Exception as exc:
                logger.warning("Initial scene generation failed: %s", exc)
                continue

            idx = len(scenes)
            img = _pop_reference_image(result)
            ent_imgs = _pop_entity_images(result)

            # Save to disk immediately
            story_idx, _ = create_story(pid)
            save_scene(pid, story_idx, result,
                       reference_image=img, entity_images=ent_imgs)
            result["_story_index"] = story_idx

            scenes.append(result)

            # Stream this scene to the client right away
            await websocket.send_json({
                "type": "scene_ready",
                "scene": result,
                "index": idx,
                "total": total_scenes,
            })

        session.initial_scenes = scenes

        # Final signal: all scenes done
        await websocket.send_json({
            "type": "initial_scenes_done",
            "total": len(scenes),
        })

    except Exception as e:
        logger.exception("Failed to generate initial scenes")
        await websocket.send_json({"type": "error", "message": str(e)})


async def _handle_select_scene(
    session: SessionState,
    data: Dict[str, Any],
    websocket: WebSocket,
    ws_adapter: _WebSocketAdapter,
) -> None:
    """Handle scene selection from thumbnails."""
    index = data.get("index", 0)
    if index < 0 or index >= len(session.initial_scenes):
        await websocket.send_json({"type": "error", "message": "Invalid scene index"})
        return

    scene = session.initial_scenes[index]
    session.current_scene = scene

    # Check if this is an existing story loaded from disk
    story_idx = scene.pop("_story_index", None)
    if story_idx is not None:
        session.current_story_index = story_idx
    else:
        story_idx, _ = create_story(session.participant_id)
        session.current_story_index = story_idx
        save_scene(session.participant_id, story_idx, scene)

    # Send scene to client
    await websocket.send_json({
        "type": "scene_selected_ready",
        "scene": scene,
    })

    # Commit to story state
    scene_id = scene["manifest"]["scene_id"]
    session.story_state.add_scene(
        scene_id=scene_id,
        narrative_text=scene.get("narrative_text", ""),
        manifest=scene["manifest"],
        neg=scene.get("neg", {}),
        sprite_code=scene.get("sprite_code"),
    )
    session.completed_scene_ids.append(scene_id)

    _init_narration_loop(session, ws_adapter)


async def _handle_binary(
    session: SessionState,
    audio_bytes: bytes,
    ws_adapter: _WebSocketAdapter,
    websocket: WebSocket,
) -> None:
    """Handle binary audio data from the client."""
    if session.narration_loop is None:
        await websocket.send_json({"type": "error", "message": "No active narration loop"})
        return

    try:
        # Transcription is now sent immediately inside narration_loop.on_audio_chunk()
        await session.narration_loop.on_audio_chunk(audio_bytes)
        # Persist profile after every utterance
        save_student_profile(session.participant_id, session.student_profile)
    except Exception as e:
        logger.exception("Audio processing error")
        await websocket.send_json({"type": "error", "message": str(e)})

    session.pending_audio_meta = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pop_reference_image(scene: Dict[str, Any]) -> Optional[bytes]:
    """Extract and remove the binary reference image from a scene dict.

    The image bytes cannot be serialized to JSON (for WebSocket or disk),
    so we pop them out and return them separately.
    """
    return scene.pop("_reference_image_bytes", None)


def _pop_entity_images(scene: Dict[str, Any]) -> Optional[Dict[str, bytes]]:
    """Extract and remove entity image bytes from a scene dict.

    Entity images are binary PNG data (one per entity) attached by the
    pipeline for debugging/persistence.  They cannot be JSON-serialized,
    so we pop them before sending over WebSocket.
    """
    return scene.pop("_entity_image_bytes", None)


def _init_narration_loop(
    session: SessionState,
    ws_adapter: _WebSocketAdapter,
) -> None:
    """Initialize a NarrationLoop for the current scene and send intro voice."""
    scene = session.current_scene
    if scene is None:
        return

    manifest = SceneManifest.model_validate(scene["manifest"])
    neg = NEG.model_validate(scene.get("neg", {"targets": []}))

    session.narration_loop = NarrationLoop(
        api_key=session.api_key,
        scene_manifest=manifest,
        neg=neg,
        story_state=session.story_state,
        student_profile=session.student_profile,
        animation_cache=session.animation_cache,
        websocket=ws_adapter,
        narrative_text=scene.get("narrative_text", ""),
    )

    # Scene intro voice (fire once, serialized via voice lock)
    asyncio.ensure_future(_send_scene_intro(session))


async def _send_scene_intro(session: SessionState) -> None:
    """Generate and send introductory voice for the current scene."""
    scene = session.current_scene
    if scene is None or session.narration_loop is None:
        return
    try:
        from src.narration.voice_guidance import generate_scene_intro

        intro_text = await generate_scene_intro(
            api_key=session.api_key,
            narrative_text=scene.get("narrative_text", ""),
            manifest=scene.get("manifest", {}),
        )
        if intro_text:
            await session.narration_loop._send_voice_safe(intro_text, "intro")
    except Exception:
        logger.warning("Failed to generate scene intro voice")


