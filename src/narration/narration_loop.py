"""Real-time narration loop orchestrator.

Orchestrates: audio → transcription → dispatch → animation generation → WebSocket.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Protocol

from src.models.animation_cache import AnimationCache, CachedAnimation
from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.story_state import StoryState
from src.models.student_profile import Discrepancy, StudentProfile
from src.generation.animation_generator import generate_animation
from src.narration.dispatcher import AnimationCommand, dispatch, dispatch_hesitation
from src.narration.transcription import TranscriptionResult, transcribe_and_detect

logger = logging.getLogger(__name__)

HESITATION_TIMEOUT_S = 10


class WebSocketLike(Protocol):
    """Minimal protocol for the WebSocket connection."""

    async def send_json(self, data: Dict[str, Any]) -> None: ...


class NarrationLoop:
    """Orchestrates the real-time narration loop for a single scene.

    Lifecycle:
        1. Construct with scene data and shared state.
        2. Call ``on_audio_chunk`` for each push-to-talk utterance.
        3. Call ``on_idle_timeout`` if the child is silent for > 10s.
        4. Check ``is_scene_complete()`` after each utterance.
    """

    def __init__(
        self,
        api_key: str,
        scene_manifest: SceneManifest,
        neg: NEG,
        story_state: StoryState,
        student_profile: StudentProfile,
        animation_cache: AnimationCache,
        websocket: WebSocketLike,
    ) -> None:
        self.api_key = api_key
        self.scene_manifest = scene_manifest
        self.neg = neg
        self.story_state = story_state
        self.student_profile = student_profile
        self.animation_cache = animation_cache
        self.ws = websocket

        # Mutable state
        self.narration_history: List[str] = []
        self.satisfied_targets: List[str] = []
        self.scene_progress: float = 0.0
        self.last_audio_time: float = time.monotonic()

        # Session log for post-session analytics
        self._session_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def on_audio_chunk(self, audio_bytes: bytes) -> TranscriptionResult:
        """Process one push-to-talk utterance.

        1. Transcribe + detect discrepancies via Gemini.
        2. Update student_profile with errors.
        3. Update satisfied_targets and scene_progress.
        4. Dispatch top discrepancies to animation system.
        5. Generate missing animations on-the-fly.
        6. Send animations to client via WebSocket.
        7. Send scene_complete if threshold reached.

        Returns:
            The TranscriptionResult from the LLM.
        """
        self.last_audio_time = time.monotonic()

        # 1. Transcribe + detect
        result = await transcribe_and_detect(
            api_key=self.api_key,
            audio_bytes=audio_bytes,
            neg=self.neg,
            narration_history=self.narration_history,
            student_profile=self.student_profile,
        )

        # 2. Update student_profile
        self.student_profile.record_errors(result.discrepancies)

        # 3. Update narration state
        self.narration_history = result.updated_history
        self._merge_satisfied_targets(result.satisfied_targets)
        self.scene_progress = max(self.scene_progress, result.scene_progress)

        # 4. Dispatch
        entity_bounds = self._compute_entity_bounds()
        scene_ctx = self.scene_manifest.model_dump()
        commands = dispatch(
            result.discrepancies,
            self.animation_cache,
            entity_bounds,
            scene_ctx,
        )

        # 5 & 6. Generate missing animations + send all to client
        for cmd in commands:
            animation = await self._resolve_animation(cmd, entity_bounds, scene_ctx)
            if animation is not None:
                await self.ws.send_json({
                    "type": "animation",
                    "code": animation.code,
                    "duration_ms": animation.duration_ms,
                    "entity_id": cmd.entity_id,
                    "sub_entity": cmd.sub_entity,
                    "error_type": cmd.error_type,
                })

        # 7. Scene complete?
        if self.is_scene_complete():
            await self.ws.send_json({"type": "scene_complete"})

        # Log for analytics
        self._session_log.append({
            "utterance_index": len(self.narration_history),
            "transcription": result.transcription,
            "discrepancies": [d.model_dump() for d in result.discrepancies],
            "scene_progress": self.scene_progress,
            "satisfied_targets": list(self.satisfied_targets),
            "animations_dispatched": len(commands),
            "animations_cached": sum(1 for c in commands if c.cached),
            "animations_generated": sum(1 for c in commands if not c.cached),
        })

        return result

    async def on_idle_timeout(self) -> Optional[AnimationCommand]:
        """Handle hesitation event (child silent > 10s).

        Dispatches an OMISSION animation for the highest-priority
        unsatisfied target.

        Returns:
            The AnimationCommand sent, or None if all targets are satisfied.
        """
        cmd = dispatch_hesitation(self.neg, self.satisfied_targets)
        if cmd is None:
            return None

        entity_bounds = self._compute_entity_bounds()
        scene_ctx = self.scene_manifest.model_dump()
        animation = await self._resolve_animation(cmd, entity_bounds, scene_ctx)

        if animation is not None:
            await self.ws.send_json({
                "type": "animation",
                "code": animation.code,
                "duration_ms": animation.duration_ms,
                "entity_id": cmd.entity_id,
                "sub_entity": cmd.sub_entity,
                "error_type": cmd.error_type,
            })

        self._session_log.append({
            "event": "hesitation",
            "target_entity": cmd.entity_id,
            "animation_sent": animation is not None,
        })

        return cmd

    def is_scene_complete(self) -> bool:
        """Whether the child has narrated enough (progress >= min_coverage)."""
        return self.scene_progress >= self.neg.min_coverage

    @property
    def session_log(self) -> List[Dict[str, Any]]:
        """Return the session log for post-session analytics."""
        return list(self._session_log)

    def seconds_since_last_audio(self) -> float:
        """Seconds elapsed since the last audio chunk was received."""
        return time.monotonic() - self.last_audio_time

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _merge_satisfied_targets(self, new_targets: List[str]) -> None:
        """Merge newly satisfied targets into the cumulative set."""
        existing = set(self.satisfied_targets)
        for t in new_targets:
            if t not in existing:
                self.satisfied_targets.append(t)
                existing.add(t)

    def _compute_entity_bounds(self) -> Dict[str, Dict[str, int]]:
        """Compute bounding boxes from the manifest positions.

        This is a rough approximation — entities are assumed to be
        ~40x40 pixels centered on their position.  In production the
        pixel buffer would provide exact bounds.
        """
        bounds: Dict[str, Dict[str, int]] = {}
        for ent in self.scene_manifest.entities:
            bounds[ent.id] = {
                "x": max(0, ent.position.x - 20),
                "y": max(0, ent.position.y - 20),
                "width": 40,
                "height": 40,
            }
        return bounds

    async def _resolve_animation(
        self,
        cmd: AnimationCommand,
        entity_bounds: Dict[str, Dict[str, int]],
        scene_context: Dict[str, Any],
    ) -> Optional[CachedAnimation]:
        """Resolve an AnimationCommand to a CachedAnimation.

        If the command already has a cached animation, return it.
        Otherwise, generate one on-the-fly via the LLM.
        """
        if cmd.cached and cmd.animation is not None:
            return cmd.animation

        bounds = entity_bounds.get(cmd.entity_id, {
            "x": 0, "y": 0, "width": 40, "height": 40,
        })

        try:
            animation = await generate_animation(
                api_key=self.api_key,
                error_type=cmd.error_type,
                entity_id=cmd.entity_id,
                sub_entity=cmd.sub_entity,
                entity_bounds=bounds,
                scene_context=scene_context,
                animation_cache=self.animation_cache,
            )
            return animation
        except Exception:
            logger.exception(
                "Failed to generate animation for %s/%s",
                cmd.sub_entity, cmd.error_type,
            )
            return None
