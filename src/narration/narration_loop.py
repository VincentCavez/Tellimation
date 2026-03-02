"""Real-time narration loop orchestrator.

Orchestrates: audio -> transcription -> dispatch -> animation generation -> WebSocket.
Voice is serialized (one at a time) via an asyncio lock.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Protocol, Tuple

from src.models.animation_cache import AnimationCache, CachedAnimation
from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.story_state import StoryState
from src.models.student_profile import Discrepancy, StudentProfile
from src.generation.animation_generator import generate_animation
from src.narration.dispatcher import AnimationCommand, dispatch
from src.narration.transcription import TranscriptionResult, transcribe_and_detect

logger = logging.getLogger(__name__)

MAX_ANIMATIONS_PER_ERROR = 3


class WebSocketLike(Protocol):
    """Minimal protocol for the WebSocket connection."""

    async def send_json(self, data: Dict[str, Any]) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...


class NarrationLoop:
    """Orchestrates the real-time narration loop for a single scene.

    Lifecycle:
        1. Construct with scene data and shared state.
        2. Call ``on_audio_chunk`` for each push-to-talk utterance.
        3. Check ``is_scene_complete()`` after each utterance.
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
        narrative_text: str = "",
    ) -> None:
        self.api_key = api_key
        self.scene_manifest = scene_manifest
        self.neg = neg
        self.story_state = story_state
        self.student_profile = student_profile
        self.animation_cache = animation_cache
        self.ws = websocket
        self.narrative_text = narrative_text

        # Mutable state
        self.narration_history: List[str] = []
        self.satisfied_targets: List[str] = []
        self.scene_progress: float = 0.0

        # Per-error animation counter: (entity_id, error_type) -> count
        self._error_animation_counts: Dict[Tuple[str, str], int] = {}

        # Voice serialization lock — one voice at a time
        self._voice_lock = asyncio.Lock()

        # Session log for post-session analytics
        self._session_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def on_audio_chunk(self, audio_bytes: bytes) -> TranscriptionResult:
        """Process one push-to-talk utterance.

        1. Transcribe + detect discrepancies via Gemini.
        2. Send transcription to client immediately.
        3. Update student_profile with errors.
        4. Update satisfied_targets and scene_progress.
        5. Dispatch animations (max 3 per error).
        6. Beyond 3rd animation -> verbal correction.
        7. Send scene_complete if threshold reached.

        Returns:
            The TranscriptionResult from the LLM.
        """
        # 1. Transcribe + detect
        result = await transcribe_and_detect(
            api_key=self.api_key,
            audio_bytes=audio_bytes,
            neg=self.neg,
            narration_history=self.narration_history,
            student_profile=self.student_profile,
            narrative_text=self.narrative_text,
        )

        # 2. Send transcription to client IMMEDIATELY (before animations)
        await self.ws.send_json({
            "type": "transcription",
            "transcription": result.transcription,
            "scene_progress": max(self.scene_progress, result.scene_progress),
        })

        # 3. Update student_profile
        self.student_profile.record_errors(result.discrepancies)

        # 4. Update narration state
        prev_satisfied = set(self.satisfied_targets)
        self.narration_history = result.updated_history
        self._merge_satisfied_targets(result.satisfied_targets)
        self.scene_progress = max(self.scene_progress, result.scene_progress)

        # 4b. Track corrections — newly satisfied targets may indicate
        #     the child corrected after a previous animation
        newly_satisfied = set(self.satisfied_targets) - prev_satisfied
        if newly_satisfied:
            self._record_corrections_for_targets(newly_satisfied)

        # 5. Dispatch animations (with per-error limit)
        entity_bounds = self._compute_entity_bounds()
        scene_ctx = self.scene_manifest.model_dump()
        commands = dispatch(
            result.discrepancies,
            self.animation_cache,
            entity_bounds,
            scene_ctx,
        )

        errors_needing_correction: List[AnimationCommand] = []

        for cmd in commands:
            key = (cmd.entity_id, cmd.error_type)
            count = self._error_animation_counts.get(key, 0)

            if count >= MAX_ANIMATIONS_PER_ERROR:
                # Already at limit -> queue verbal correction
                errors_needing_correction.append(cmd)
                continue

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
                self._error_animation_counts[key] = count + 1
                self.student_profile.record_animation(
                    cmd.entity_id, cmd.error_type, animation.generated_for
                )

        # 6. Verbal corrections for errors past 3 animations
        for cmd in errors_needing_correction:
            try:
                from src.narration.voice_guidance import generate_correction_text

                correction_text = await generate_correction_text(
                    api_key=self.api_key,
                    entity_id=cmd.entity_id,
                    error_type=cmd.error_type,
                    discrepancy_details=cmd.discrepancy_details or "",
                    scene_manifest=scene_ctx,
                    narrative_text=self.narrative_text,
                )
                if correction_text:
                    if self._has_unsatisfied_targets():
                        correction_text += " What else happens in this scene?"
                    await self._send_voice_safe(correction_text, "correction")
            except Exception:
                logger.exception(
                    "Failed to generate correction for %s/%s",
                    cmd.entity_id, cmd.error_type,
                )

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
            "animations_limited": len(errors_needing_correction),
        })

        return result

    def is_scene_complete(self) -> bool:
        """Whether the child has narrated enough (progress >= min_coverage)."""
        return self.scene_progress >= self.neg.min_coverage

    @property
    def session_log(self) -> List[Dict[str, Any]]:
        """Return the session log for post-session analytics."""
        return list(self._session_log)

    # ------------------------------------------------------------------
    # Voice guidance (serialized)
    # ------------------------------------------------------------------

    async def _send_voice_safe(self, text: str, purpose: str) -> None:
        """Send TTS with serialization -- one voice at a time."""
        async with self._voice_lock:
            await self._send_voice(text, purpose)

    async def _send_voice(self, text: str, purpose: str) -> None:
        """Generate TTS audio for *text* and send to the client.

        Sends a JSON header (``voice_audio``) followed by raw PCM bytes.

        Args:
            text: The text to speak.
            purpose: One of ``"intro"``, ``"correction"``,
                ``"branch_summary"``.
        """
        try:
            from src.narration.voice_guidance import text_to_speech

            tts_prompt = f"Say warmly and encouragingly: {text}"
            audio_bytes = await text_to_speech(self.api_key, tts_prompt)

            await self.ws.send_json({
                "type": "voice_audio",
                "purpose": purpose,
                "text": text,
                "sample_rate": 24000,
                "sample_width": 2,
                "channels": 1,
            })
            await self.ws.send_bytes(audio_bytes)
        except Exception:
            logger.exception("Failed to send voice (%s): %s", purpose, text[:80])

    def _has_unsatisfied_targets(self) -> bool:
        """Check if there are NEG targets not yet satisfied."""
        satisfied = set(self.satisfied_targets)
        return any(t.id not in satisfied for t in self.neg.targets)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _record_corrections_for_targets(self, newly_satisfied: set) -> None:
        """Check if animations were played for newly satisfied targets and record corrections.

        When a target becomes satisfied, it may indicate the child corrected
        after seeing an animation. We look through the NEG to find which
        entity each target refers to, then check if we played an animation
        for that entity.
        """
        # Build a map: target_id -> entity_id from the NEG
        target_entities: Dict[str, str] = {}
        for target in self.neg.targets:
            target_entities[target.id] = target.entity_id

        for target_id in newly_satisfied:
            entity_id = target_entities.get(target_id, "")
            if not entity_id:
                continue
            # Check animation history for any animation on this entity
            for entry in reversed(self.student_profile.animation_history):
                if entry["entity_id"] == entity_id and not entry["corrected"]:
                    self.student_profile.record_correction(
                        entity_id, entry["error_type"]
                    )
                    break

    def _merge_satisfied_targets(self, new_targets: List[str]) -> None:
        """Merge newly satisfied targets into the cumulative set."""
        existing = set(self.satisfied_targets)
        for t in new_targets:
            if t not in existing:
                self.satisfied_targets.append(t)
                existing.add(t)

    def _compute_entity_bounds(self) -> Dict[str, Dict[str, int]]:
        """Compute bounding boxes in art-grid coordinates.

        Manifest positions are in source coordinates (0–1119, 0–719).
        We convert to art-grid coords (source // K) using width_hint/height_hint.
        """
        K = 4  # pixel-art aggregation factor (must match engine.js / scene_generator.py)
        bounds: Dict[str, Dict[str, int]] = {}
        for ent in self.scene_manifest.entities:
            art_cx = ent.position.x // K
            art_cy = ent.position.y // K
            art_w = max(1, (ent.width_hint or 50) // K)
            art_h = max(1, (ent.height_hint or 60) // K)
            bounds[ent.id] = {
                "x": max(0, art_cx - art_w // 2),
                "y": max(0, art_cy - art_h // 2),
                "width": art_w,
                "height": art_h,
            }
        return bounds

    def _extract_sprite_info(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Extract sub-entity IDs, per-part stats, and actual bbox from stored sprite.

        Returns a dict with:
          - x, y, w, h: actual bounding box in art-grid coordinates
          - sub_entity_ids: sorted list of unique sub-entity ID strings from mask
          - sub_entity_stats: per-ID dict with pixel_count, avg_color, bbox
        Returns None if sprite data is unavailable or not in raw_sprite format.
        """
        sprite_data = self.story_state.get_entity_sprite(entity_id)
        if not sprite_data or not isinstance(sprite_data, dict):
            return None
        if sprite_data.get("format") != "raw_sprite":
            return None

        mask = sprite_data.get("mask", [])
        pixels = sprite_data.get("pixels", [])
        w = sprite_data.get("w", 0)
        h = sprite_data.get("h", 0)
        if not mask or w == 0:
            return None

        # Unique sub-entity IDs from the mask
        sub_entity_ids = sorted(set(m for m in mask if m))

        # Per-sub-entity stats: pixel count, average color, bounding box
        sub_stats: Dict[str, Dict[str, Any]] = {}
        for sid in sub_entity_ids:
            px_indices = [i for i, m in enumerate(mask) if m == sid]
            colors = [
                pixels[i] for i in px_indices
                if i < len(pixels) and pixels[i] is not None
            ]
            if colors:
                avg_r = sum(c[0] for c in colors) // len(colors)
                avg_g = sum(c[1] for c in colors) // len(colors)
                avg_b = sum(c[2] for c in colors) // len(colors)
            else:
                avg_r = avg_g = avg_b = 0

            xs = [i % w for i in px_indices]
            ys = [i // w for i in px_indices]
            sub_stats[sid] = {
                "pixel_count": len(px_indices),
                "avg_color": (avg_r, avg_g, avg_b),
                "bbox": {
                    "x_min": min(xs) if xs else 0,
                    "y_min": min(ys) if ys else 0,
                    "x_max": max(xs) if xs else 0,
                    "y_max": max(ys) if ys else 0,
                },
            }

        return {
            "x": sprite_data.get("x", 0),
            "y": sprite_data.get("y", 0),
            "w": w,
            "h": h,
            "sub_entity_ids": sub_entity_ids,
            "sub_entity_stats": sub_stats,
        }

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

        # Extract sprite info (sub-entity IDs, per-part stats, actual bbox)
        sprite_info = self._extract_sprite_info(cmd.entity_id)
        if sprite_info:
            # Use actual sprite bbox instead of manifest hint-based estimate
            bounds = {
                "x": sprite_info["x"],
                "y": sprite_info["y"],
                "width": sprite_info["w"],
                "height": sprite_info["h"],
            }

        try:
            animation = await generate_animation(
                api_key=self.api_key,
                error_type=cmd.error_type,
                entity_id=cmd.entity_id,
                sub_entity=cmd.sub_entity,
                entity_bounds=bounds,
                scene_context=scene_context,
                animation_cache=self.animation_cache,
                student_profile=self.student_profile,
                discrepancy_details=cmd.discrepancy_details,
                entity_sprite_info=sprite_info,
            )
            return animation
        except Exception:
            logger.exception(
                "Failed to generate animation for %s/%s",
                cmd.sub_entity, cmd.error_type,
            )
            return None
