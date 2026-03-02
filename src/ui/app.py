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
from src.generation.branch_generator import generate_branches, generate_one_more
from src.generation.neg_generator import generate_neg_for_plot
from src.generation.scene_generator import (
    generate_scene,
    generate_masks_for_scene,
    generate_features_for_scene,
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
        self.branches: List[Dict[str, Any]] = []
        self.current_scene: Optional[Dict[str, Any]] = None
        self.narration_loop: Optional[NarrationLoop] = None
        self.pending_audio_meta: Optional[Dict[str, Any]] = None
        # Current story index (set when a story is created/selected)
        self.current_story_index: int = 0
        # Reference images extracted from generated scenes (parallel to branches)
        self.branch_images: Dict[int, bytes] = {}
        # Entity images extracted from generated scenes (parallel to branches)
        self.branch_entity_images: Dict[int, Dict[str, bytes]] = {}
        # Background mask generation tasks (index → asyncio.Task)
        self.mask_tasks: Dict[int, asyncio.Task] = {}
        # Set of branch indices whose masks are fully generated
        self.masks_ready: set = set()
        # NEG map: scene_id -> NEG dict (generated offline, updated live)
        self.scene_negs: Dict[str, Dict[str, Any]] = {}
        # Completed scene IDs (for NEG live update context)
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

            elif msg_type == "generate_one_more":
                await _handle_generate_one_more(session, websocket)

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

            elif msg_type == "generate_branches":
                await _handle_generate_branches(session, websocket)

            elif msg_type == "select_branch":
                await _handle_select_branch(session, data, websocket, ws_adapter)

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
# Background mask + NEG generation
# ---------------------------------------------------------------------------


def _extract_masks_summary(scene: Dict[str, Any]) -> Dict[str, List[str]]:
    """Extract unique sub-entity IDs per entity from sprite_code masks.

    Returns:
        Dict mapping entity_id -> sorted list of sub-entity IDs.
        Example: {"turtle_01": ["turtle_01.body", "turtle_01.head", "turtle_01.shell"]}
    """
    summary: Dict[str, List[str]] = {}
    for eid, entry in scene.get("sprite_code", {}).items():
        if eid == "bg" or not isinstance(entry, dict):
            continue
        mask = entry.get("mask", [])
        unique_ids = sorted(set(m for m in mask if m is not None))
        if unique_ids:
            summary[eid] = unique_ids
    return summary


async def _generate_masks_background(
    session: SessionState,
    index: int,
    websocket: WebSocket,
) -> None:
    """Generate masks then NEG for a branch scene in the background.

    Called right after a scene is streamed to the client.  Chains:
    1. Mask generation (sub-entity IDs for each entity)
    2. NEG generation (uses masks + student_profile + skill files)

    When masks are ready, sends ``masks_ready``.
    When NEG is ready, sends ``neg_ready``.
    """
    scene = session.branches[index]

    # Step 1: Masks
    try:
        await generate_masks_for_scene(session.api_key, scene)
        session.masks_ready.add(index)
        logger.info("Background masks ready for branch %d", index)
        await websocket.send_json({
            "type": "masks_ready",
            "index": index,
        })
    except Exception:
        session.masks_ready.add(index)
        logger.warning(
            "Background mask generation failed for branch %d, using fallback",
            index,
        )
        await websocket.send_json({
            "type": "masks_ready",
            "index": index,
        })

    # Step 2: NEG (uses masks + student_profile)
    try:
        masks_summary = _extract_masks_summary(scene)
        neg_map = await generate_neg_for_plot(
            api_key=session.api_key,
            plot_scenes=[scene],
            student_profile=session.student_profile,
            masks_summary=masks_summary,
        )
        scene_id = scene.get("manifest", {}).get("scene_id", "")
        if scene_id in neg_map:
            neg_dict = neg_map[scene_id].model_dump()
            scene["neg"] = neg_dict
            session.scene_negs[scene_id] = neg_dict
        logger.info("Background NEG ready for branch %d", index)
        await websocket.send_json({
            "type": "neg_ready",
            "index": index,
        })
    except Exception:
        logger.warning("Background NEG generation failed for branch %d", index)


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------

async def _handle_generate_initial(
    session: SessionState,
    websocket: WebSocket,
) -> None:
    """Generate 2 initial scene candidates, or return existing stories.

    Scenes are generated in parallel with skip_masks=True for speed,
    and streamed to the client one-by-one as they complete.
    """
    try:
        pid = session.participant_id
        existing_count = story_count(pid)

        # If participant already has >= 2 stories, send them instead of generating
        if existing_count >= 2:
            existing_scenes = load_story_first_scenes(pid)
            if len(existing_scenes) >= 2:
                session.branches = existing_scenes
                # From-disk scenes already have masks
                session.masks_ready = set(range(len(existing_scenes)))
                await websocket.send_json({
                    "type": "initial_scenes",
                    "scenes": existing_scenes,
                    "from_disk": True,
                })
                return

        # Generate fresh scenes in PARALLEL with progress
        total_scenes = 1
        session.branch_images = {}
        session.branch_entity_images = {}

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

        # Launch all scenes in parallel with skip_masks=True (masks deferred)
        futures = {
            asyncio.ensure_future(
                generate_scene(
                    api_key=session.api_key,
                    story_state=None,
                    student_profile=session.student_profile,
                    seed_index=seed,
                    commit_to_state=False,
                    skip_masks=True,
                    progress_callback=_make_progress_cb(seed),
                )
            ): seed
            for seed in range(1, total_scenes + 1)
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
            if img:
                session.branch_images[idx] = img
            ent_imgs = _pop_entity_images(result)
            if ent_imgs:
                session.branch_entity_images[idx] = ent_imgs

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

            # Fire off mask generation in the background for this scene
            mask_idx = idx  # capture for closure
            session.mask_tasks[mask_idx] = asyncio.ensure_future(
                _generate_masks_background(session, mask_idx, websocket)
            )

        session.branches = scenes

        # NEG generation is now chained inside _generate_masks_background
        # (runs after masks are ready, with full context: masks + student_profile)

        # Final signal: all scenes done (NEG still generating in background)
        await websocket.send_json({
            "type": "initial_scenes_done",
            "total": len(scenes),
        })

    except Exception as e:
        logger.exception("Failed to generate initial scenes")
        await websocket.send_json({"type": "error", "message": str(e)})


async def _handle_generate_one_more(
    session: SessionState,
    websocket: WebSocket,
) -> None:
    """Generate one additional scene candidate (with skip_masks for speed)."""
    try:
        scene = await generate_one_more(
            api_key=session.api_key,
            existing_branches=session.branches,
            story_state=session.story_state if session.story_state.scenes else StoryState(),
            student_profile=session.student_profile,
            skip_masks=True,
        )
        idx = len(session.branches)
        img = _pop_reference_image(scene)
        if img:
            session.branch_images[idx] = img
        ent_imgs = _pop_entity_images(scene)
        if ent_imgs:
            session.branch_entity_images[idx] = ent_imgs

        # Save immediately as a new story folder
        story_idx, _ = create_story(session.participant_id)
        save_scene(session.participant_id, story_idx, scene,
                   reference_image=img, entity_images=ent_imgs)
        scene["_story_index"] = story_idx

        session.branches.append(scene)
        await websocket.send_json({"type": "one_more_scene", "scene": scene, "index": idx})

        # Fire off mask generation in the background
        session.mask_tasks[idx] = asyncio.ensure_future(
            _generate_masks_background(session, idx, websocket)
        )

    except Exception as e:
        logger.exception("Failed to generate one more scene")
        await websocket.send_json({"type": "error", "message": str(e)})


async def _handle_select_scene(
    session: SessionState,
    data: Dict[str, Any],
    websocket: WebSocket,
    ws_adapter: _WebSocketAdapter,
) -> None:
    """Handle scene selection from thumbnails.

    Generates masks in the background (deferred from initial generation)
    before starting the narration loop.
    """
    index = data.get("index", 0)
    if index < 0 or index >= len(session.branches):
        await websocket.send_json({"type": "error", "message": "Invalid scene index"})
        return

    scene = session.branches[index]
    session.current_scene = scene

    # Retrieve the reference image and entity images for this branch (if any)
    ref_image = session.branch_images.pop(index, None)
    ent_images = session.branch_entity_images.pop(index, None)

    # Check if this is an existing story loaded from disk
    story_idx = scene.pop("_story_index", None)
    if story_idx is not None:
        # Resuming an existing story — no new folder needed
        session.current_story_index = story_idx
    else:
        # New story — create a story folder and persist the first scene
        story_idx, _ = create_story(session.participant_id)
        session.current_story_index = story_idx
        save_scene(
            session.participant_id, story_idx, scene,
            reference_image=ref_image,
            entity_images=ent_images,
        )

    # Generate masks + features in parallel
    if index in session.mask_tasks:
        mask_task = session.mask_tasks.pop(index)
        if not mask_task.done():
            logger.info("Waiting for background mask generation for branch %d", index)
            try:
                await mask_task
            except Exception:
                logger.warning("Background mask task failed for branch %d", index)
        # Masks are ready, just run features
        try:
            await generate_features_for_scene(session.api_key, scene)
            logger.info("Features extracted for selected scene")
        except Exception:
            logger.warning("Feature extraction failed for selected scene")
    elif index not in session.masks_ready:
        # No background task — generate masks + features in parallel
        mask_ok = True
        try:
            await asyncio.gather(
                generate_masks_for_scene(session.api_key, scene),
                generate_features_for_scene(session.api_key, scene),
            )
            session.masks_ready.add(index)
            logger.info("Masks + features generated for selected scene")
        except Exception:
            logger.warning("Mask/feature generation failed for selected scene")
    else:
        # Masks already ready, just run features
        try:
            await generate_features_for_scene(session.api_key, scene)
            logger.info("Features extracted for selected scene")
        except Exception:
            logger.warning("Feature extraction failed for selected scene")

    # Send updated scene (with real masks + features) back to client
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


async def _handle_generate_branches(
    session: SessionState,
    websocket: WebSocket,
) -> None:
    """Generate 3 branch candidates after scene completion.

    Uses skip_masks=True for speed — masks are generated later when
    the child selects a branch.

    Before generating branches, updates NEGs for remaining scenes if
    the student profile shows error patterns worth adapting to.
    """
    try:
        branches = await generate_branches(
            api_key=session.api_key,
            story_state=session.story_state,
            student_profile=session.student_profile,
            skip_masks=True,
        )

        session.branch_images = {}
        session.branch_entity_images = {}
        for i, b in enumerate(branches):
            img = _pop_reference_image(b)
            if img:
                session.branch_images[i] = img
            ent_imgs = _pop_entity_images(b)
            if ent_imgs:
                session.branch_entity_images[i] = ent_imgs
        session.branches = branches
        await websocket.send_json({"type": "branches", "scenes": branches})

        # Fire mask + NEG background tasks for each branch
        for i in range(len(branches)):
            session.mask_tasks[i] = asyncio.ensure_future(
                _generate_masks_background(session, i, websocket)
            )

        # Voice narration of branch summaries (fire-and-forget)
        asyncio.ensure_future(
            _send_branch_narration(session, websocket, branches)
        )

    except Exception as e:
        logger.exception("Failed to generate branches")
        await websocket.send_json({"type": "error", "message": str(e)})


async def _handle_select_branch(
    session: SessionState,
    data: Dict[str, Any],
    websocket: WebSocket,
    ws_adapter: _WebSocketAdapter,
) -> None:
    """Handle branch selection on the story page.

    Generates masks (deferred from branch generation) before starting
    the narration loop.
    """
    index = data.get("index", 0)
    if index < 0 or index >= len(session.branches):
        await websocket.send_json({"type": "error", "message": "Invalid branch index"})
        return

    scene = session.branches[index]
    session.current_scene = scene

    # Retrieve the reference image and entity images for this branch (if any)
    ref_image = session.branch_images.pop(index, None)
    ent_images = session.branch_entity_images.pop(index, None)

    # Persist the scene to the current story folder
    if session.current_story_index:
        save_scene(
            session.participant_id, session.current_story_index, scene,
            reference_image=ref_image,
            entity_images=ent_images,
        )

    # Generate masks + features in parallel
    if index in session.mask_tasks:
        mask_task = session.mask_tasks.pop(index)
        if not mask_task.done():
            logger.info("Waiting for background mask generation for branch %d", index)
            try:
                await mask_task
            except Exception:
                logger.warning("Background mask task failed for branch %d", index)
        # Masks are ready, just run features
        try:
            await generate_features_for_scene(session.api_key, scene)
            logger.info("Features extracted for selected branch")
        except Exception:
            logger.warning("Feature extraction failed for selected branch")
    elif index not in session.masks_ready:
        # No background task — generate masks + features in parallel
        try:
            await asyncio.gather(
                generate_masks_for_scene(session.api_key, scene),
                generate_features_for_scene(session.api_key, scene),
            )
            session.masks_ready.add(index)
            logger.info("Masks + features generated for selected branch")
        except Exception:
            logger.warning("Mask/feature generation failed for selected branch")
    else:
        # Masks already ready, just run features
        try:
            await generate_features_for_scene(session.api_key, scene)
            logger.info("Features extracted for selected branch")
        except Exception:
            logger.warning("Feature extraction failed for selected branch")

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

    # Persist student profile after each scene progression
    save_student_profile(session.participant_id, session.student_profile)

    # Send the new scene (with real masks + features) to the client
    await websocket.send_json({"type": "new_scene", "scene": scene})

    _init_narration_loop(session, ws_adapter)


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


async def _send_branch_narration(
    session: SessionState,
    websocket: WebSocket,
    branches: List[Dict[str, Any]],
) -> None:
    """Generate and send TTS narration of branch summaries (serialized)."""
    try:
        from src.narration.voice_guidance import generate_branch_narration

        narration_text = await generate_branch_narration(
            api_key=session.api_key,
            branches=branches,
        )
        if narration_text and session.narration_loop:
            await session.narration_loop._send_voice_safe(
                narration_text, "branch_summary"
            )
    except Exception:
        logger.warning("Failed to generate branch narration audio")
