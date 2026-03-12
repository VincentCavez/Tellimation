"""Tellimations FastAPI web server with WebSocket handler.

Orchestrates the v3 pipeline:
  1. scene_neg_generator → manifest + NEG (Gemini 3.1 Pro)
  2. scene_generator → sprite_code (Nano Banana 2 images)
  3. discrepancy_assessment → per-utterance decision (Gemini 3 Flash)
  4. tellimation → animation code (Gemini 3 Flash)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from src.analytics.session_report import generate_report
from src.generation.scene_generator import STORY_THEMES, generate_scene_assets
from src.generation.scene_neg_generator import generate_scene_and_neg
from src.interaction.discrepancy_assessment import assess_and_respond
from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.session_state import SessionState
from src.models.student_profile import StudentProfile
from src.narration.transcription import transcribe_and_detect
from src.persistence import (
    save_scene,
    save_student_profile,
    create_story,
)
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


def _assemble_scene_dict(
    raw_data: Dict[str, Any],
    manifest: SceneManifest,
    neg: NEG,
    assets: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the canonical scene dict from generation outputs."""
    return {
        "narrative_text": raw_data.get("narrative_text", ""),
        "scene_description": raw_data.get("scene_description", ""),
        "manifest": manifest.model_dump(),
        "neg": neg.model_dump(),
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
    session.current_neg = NEG.model_validate(scene.get("neg", {"targets": []}))
    session.reset_scene_state()

    session.story_state.add_scene(
        scene_id=scene["manifest"]["scene_id"],
        narrative_text=scene.get("narrative_text", ""),
        manifest=scene["manifest"],
        neg=scene.get("neg", {}),
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


# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

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

    api_key = websocket.query_params.get("api_key", "")
    participant_id = websocket.query_params.get("participant_id", "")
    child_age_str = websocket.query_params.get("child_age", "8")

    if not api_key:
        await websocket.send_json({"type": "error", "message": "Missing API key"})
        await websocket.close()
        return

    session = SessionState(api_key, participant_id)

    # Set child's age on the student profile
    try:
        session.student_profile.age = max(4, min(15, int(child_age_str)))
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
                await _handle_generate_initial_scenes(session, ws)

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
                await _handle_select_scene(session, ws, index)

            elif msg_type == "generate_one_more":
                await _handle_generate_one_more(session, ws)

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
# Initial scene generation (selection page)
# ---------------------------------------------------------------------------

async def _handle_generate_initial_scenes(
    session: SessionState,
    ws: _WebSocketAdapter,
) -> None:
    """Generate INITIAL_SCENE_COUNT scenes in parallel for the selection page."""
    n = INITIAL_SCENE_COUNT
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
            manifest, neg, raw_data = await generate_scene_and_neg(
                api_key=session.api_key,
                story_state=None,
                student_profile=session.student_profile,
                theme=theme,
                previous_manifest=None,
                previous_neg=None,
            )
            logger.info(
                "[pipeline] Scene %d: manifest+NEG took %.1fs",
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

            scene = _assemble_scene_dict(raw_data, manifest, neg, assets)

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
) -> None:
    """Handle scene selection from the selection page."""
    # Find the scene by index
    scene = None
    for entry in session.initial_scenes:
        if entry["index"] == index:
            scene = entry["scene"]
            break

    if scene is None:
        await ws.send_json({"type": "error", "message": f"Scene {index} not found"})
        return

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

        manifest, neg, raw_data = await generate_scene_and_neg(
            api_key=session.api_key,
            story_state=None,
            student_profile=session.student_profile,
            theme=theme,
            previous_manifest=None,
            previous_neg=None,
        )

        assets = await generate_scene_assets(
            api_key=session.api_key,
            manifest_data=raw_data,
            story_state=None,
        )

        scene = _assemble_scene_dict(raw_data, manifest, neg, assets)

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

    1. scene_neg_generator → manifest + NEG  (Gemini 3.1 Pro)
    2. scene_generator → sprite_code         (Nano Banana 2)
    3. Send scene_ready to client
    4. Send initial oral guidance ("What do you see?")
    """
    try:
        await ws.send_json({
            "type": "generation_progress",
            "status": "generating",
        })

        # Context for initial vs. continuation
        theme = ""
        previous_manifest = None
        previous_neg = None

        if not is_continuation:
            theme = random.choice(STORY_THEMES)
        else:
            if session.story_state.scenes:
                last = session.story_state.scenes[-1]
                previous_manifest = last.get("manifest")
                previous_neg = last.get("neg")

        # Step 1: Co-generate manifest + NEG
        await ws.send_json({
            "type": "generation_step",
            "step": "manifest_neg",
        })

        manifest, neg, raw_data = await generate_scene_and_neg(
            api_key=session.api_key,
            story_state=session.story_state if is_continuation else None,
            student_profile=session.student_profile,
            theme=theme,
            previous_manifest=previous_manifest,
            previous_neg=previous_neg,
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
        scene = _assemble_scene_dict(raw_data, manifest, neg, assets)

        # Persist
        if not is_continuation:
            story_idx, _ = create_story(session.participant_id)
            session.current_story_index = story_idx
        save_scene(session.participant_id, session.current_story_index, scene)

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
# Initial guidance (Level 0 — open invitation)
# ---------------------------------------------------------------------------

async def _send_initial_guidance(
    session: SessionState,
    ws: _WebSocketAdapter,
) -> None:
    """Send the Level 0 open invitation: 'What do you see?'"""
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
    """Handle an audio utterance: transcribe → assess → execute.

    Full interactive loop per utterance:
      1. Transcribe audio + detect discrepancies
      2. Update student profile + conversation history
      3. Discrepancy assessment → decision
      4. Execute decision (animate / oral_guidance / next_scene / wait)
    """
    if session.current_neg is None or session.current_manifest is None:
        await ws.send_json({"type": "error", "message": "No active scene"})
        return

    try:
        # 1. Transcribe + detect discrepancies
        result = await transcribe_and_detect(
            api_key=session.api_key,
            audio_bytes=audio_bytes,
            neg=session.current_neg,
            narration_history=session.narration_history,
            student_profile=session.student_profile,
            narrative_text=(
                session.current_scene.get("narrative_text", "")
                if session.current_scene else ""
            ),
        )

        # Send transcription to client
        await ws.send_json({
            "type": "transcription",
            "text": result.transcription,
            "scene_progress": result.scene_progress,
        })

        # 2. Update state
        if result.updated_history:
            session.narration_history = result.updated_history
        if result.transcription:
            session.narration_history.append(result.transcription)

        session.satisfied_targets = list(set(
            session.satisfied_targets + result.satisfied_targets
        ))
        session.scene_progress = result.scene_progress

        # Update student profile error counts
        if result.profile_updates:
            for error_type, count in result.profile_updates.errors_this_scene.items():
                current = session.student_profile.error_counts.get(error_type, 0)
                session.student_profile.error_counts[error_type] = current + count
        session.student_profile.total_utterances += 1

        # Add child utterance to conversation history
        session.conversation_history.append({
            "role": "child",
            "text": result.transcription,
        })

        # 3. Discrepancy assessment
        decision = await assess_and_respond(
            api_key=session.api_key,
            student_profile=session.student_profile,
            neg=session.current_neg,
            conversation_history=session.conversation_history,
            animations_played=session.animations_played_this_scene,
        )

        # Send assessment result to client
        await ws.send_json({
            "type": "assessment_result",
            "action": decision.action,
            "target_id": decision.target_id,
            "guidance_text": decision.guidance_text,
            "reasoning": decision.reasoning,
        })

        # 4. Execute decision
        if decision.action == "animate" and decision.target_id:
            await execute_animation(
                session, ws, decision.target_id,
                misl_element=decision.misl_element or "character",
                problematic_segment=decision.problematic_segment,
            )

        elif decision.action == "oral_guidance" and decision.guidance_text:
            session.conversation_history.append({
                "role": "system",
                "text": decision.guidance_text,
                "action": "oral_guidance",
            })
            asyncio.ensure_future(
                send_voice(session, ws, decision.guidance_text)
            )

        elif decision.action == "next_scene":
            if len(session.completed_scene_ids) < MAX_SCENES:
                session.student_profile.scenes_completed += 1
                await _handle_generate_scene(session, ws, is_continuation=True)
            else:
                await ws.send_json({"type": "story_complete"})

        # "wait" → no action

        # Persist profile after every utterance
        save_student_profile(session.participant_id, session.student_profile)

    except Exception as e:
        logger.exception("Audio processing error")
        await ws.send_json({"type": "error", "message": str(e)})


