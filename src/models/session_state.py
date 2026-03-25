"""Per-session state for a WebSocket connection."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from src.models.animation_cache import AnimationCache
from src.models.assessment import SceneLog
from src.models.scene import SceneManifest
from src.models.story_state import StoryState
from src.models.student_profile import StudentProfile
from src.persistence import ensure_participant, load_student_profile


class SessionState:
    """Holds all mutable state for a single WebSocket session."""

    def __init__(self, api_key: str, participant_id: str) -> None:
        self.api_key = api_key
        self.participant_id = participant_id

        ensure_participant(participant_id)
        self.student_profile = load_student_profile(participant_id)

        self.story_state = StoryState(
            session_id="",
            participant_id=participant_id,
        )
        self.animation_cache = AnimationCache()

        # Current scene data
        self.current_scene: Optional[Dict[str, Any]] = None
        self.current_manifest: Optional[SceneManifest] = None

        # Per-scene assessment log (replaces NEG-based tracking)
        self.current_scene_log: Optional[SceneLog] = None

        # Story tracking
        self.current_story_index: int = 0
        self.completed_scene_ids: List[str] = []

        # Per-scene interaction state (reset on each new scene)
        self.conversation_history: List[Dict[str, Any]] = []
        self.animations_played_this_scene: List[str] = []
        self.narration_history: List[str] = []

        # Last animation played (for efficacy tracking)
        self.last_animation: Optional[Dict[str, Any]] = None

        # Pending animation: set after animation plays, cleared when outcome is determined.
        # Contains: target_id, misl_element, voice_text, decision_idx
        self.pending_animation: Optional[Dict[str, Any]] = None

        # Errors already escalated to voice — skip animation on repeat
        # Key: "target_id::misl_element"
        self.voice_escalated_errors: Dict[str, str] = {}

        # Character naming phase — True until child names the main character
        self.naming_phase: bool = True
        # Child-given character names (entity_id → name), persists across scenes
        self.character_names: Dict[str, str] = {}

        # Ending choice — ask "imagine an ending or continue?" at each scene end
        self.ending_phase: bool = False          # True after first scene completes, permanent
        self.awaiting_ending_choice: bool = False # True while waiting for child's response

        # Full story utterances — all accepted utterances across ALL scenes (never reset)
        self.story_utterances: List[str] = []

        # Initial scenes generated for the selection page
        self.initial_scenes: List[Dict[str, Any]] = []

        # Voice serialization lock — one voice at a time
        self._voice_lock = asyncio.Lock()

    def reset_scene_state(self) -> None:
        """Reset per-scene state when starting a new scene."""
        self.conversation_history = []
        self.animations_played_this_scene = []
        self.narration_history = []
        self.current_scene_log = None
        self.pending_animation = None
        self.voice_escalated_errors = {}
