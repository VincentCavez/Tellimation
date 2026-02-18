"""Integration tests for the NarrationLoop orchestrator."""

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.animation_cache import AnimationCache, CachedAnimation
from src.models.neg import (
    ErrorExclusion,
    NEG,
    NarrativeTarget,
    TargetComponents,
)
from src.models.scene import Action, Entity, Position, Relation, SceneManifest
from src.models.story_state import StoryState
from src.models.student_profile import Discrepancy, StudentProfile
from src.narration.narration_loop import NarrationLoop


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_AUDIO = b"\x00\x01\x02\x03"


def _make_scene_manifest() -> SceneManifest:
    return SceneManifest(
        scene_id="scene_01",
        entities=[
            Entity(
                id="rabbit_01",
                type="rabbit",
                properties={"color": "brown", "size": "small", "texture": "fluffy"},
                position=Position(x=90, y=130, spatial_ref="beside rock_01"),
                emotion="curious",
            ),
            Entity(
                id="rock_01",
                type="rock",
                properties={"color": "grey", "size": "large", "texture": "mossy"},
                position=Position(x=160, y=140),
            ),
        ],
        relations=[
            Relation(
                entity_a="rabbit_01",
                entity_b="rock_01",
                type="spatial",
                preposition="beside",
            ),
        ],
        actions=[
            Action(entity_id="rabbit_01", verb="hop", tense="present", manner="quickly"),
        ],
    )


def _make_neg() -> NEG:
    return NEG(
        targets=[
            NarrativeTarget(
                id="t1_identity",
                entity_id="rabbit_01",
                components=TargetComponents(
                    identity=True,
                    descriptors=["brown", "small", "fluffy"],
                    spatial="beside rock_01",
                    action="hopping quickly",
                ),
                priority=0.9,
                tolerance=0.3,
            ),
            NarrativeTarget(
                id="t2_identity",
                entity_id="rock_01",
                components=TargetComponents(
                    identity=True,
                    descriptors=["grey", "large", "mossy"],
                ),
                priority=0.5,
                tolerance=0.5,
            ),
        ],
        error_exclusions=[
            ErrorExclusion(
                entity_id="rock_01",
                excluded=["QUANTITY", "ACTION", "MANNER"],
                reason="unique static object",
            ),
        ],
        min_coverage=0.7,
        skill_coverage_check="PASS",
    )


def _make_story_state() -> StoryState:
    return StoryState(
        session_id="test_session",
        participant_id="P01",
        skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
    )


class FakeWebSocket:
    """Collects messages for assertion."""

    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    async def send_json(self, data: Dict[str, Any]) -> None:
        self.messages.append(data)


# Responses for 3 consecutive utterances with increasing scene progress.

UTTERANCE_1_RESPONSE = {
    "transcription": "um there is a bunny",
    "discrepancies": [
        {
            "type": "PROPERTY_COLOR",
            "entity_id": "rabbit_01",
            "sub_entity": "rabbit_01.body",
            "details": "omitted brown",
            "severity": 0.6,
        },
        {
            "type": "ACTION",
            "entity_id": "rabbit_01",
            "sub_entity": "rabbit_01.legs",
            "details": "omitted hopping",
            "severity": 0.5,
        },
    ],
    "scene_progress": 0.2,
    "satisfied_targets": ["t1_identity"],
    "updated_history": ["um there is a bunny"],
    "profile_updates": {
        "errors_this_scene": {"PROPERTY_COLOR": 1, "ACTION": 1},
        "patterns": "omits adjectives and verbs",
    },
}

UTTERANCE_2_RESPONSE = {
    "transcription": "a brown bunny is hopping by the rock",
    "discrepancies": [
        {
            "type": "PROPERTY_SIZE",
            "entity_id": "rabbit_01",
            "sub_entity": "rabbit_01.body",
            "details": "omitted small",
            "severity": 0.3,
        },
    ],
    "scene_progress": 0.55,
    "satisfied_targets": ["t1_identity"],
    "updated_history": ["um there is a bunny", "a brown bunny is hopping by the rock"],
    "profile_updates": {
        "errors_this_scene": {"PROPERTY_SIZE": 1},
        "patterns": "improving on color, still omits size",
    },
}

UTTERANCE_3_RESPONSE = {
    "transcription": "the small brown fluffy rabbit hops quickly beside the big grey mossy rock",
    "discrepancies": [],
    "scene_progress": 0.95,
    "satisfied_targets": ["t1_identity", "t2_identity"],
    "updated_history": [
        "um there is a bunny",
        "a brown bunny is hopping by the rock",
        "the small brown fluffy rabbit hops quickly beside the big grey mossy rock",
    ],
    "profile_updates": {
        "errors_this_scene": {},
        "patterns": "excellent narration",
    },
}

FAKE_ANIMATION_RESPONSE = {
    "animation_type": "color_pop",
    "code": "function animate(buf, PW, PH, t) { /* color_pop */ }",
    "duration_ms": 1500,
}


def _make_mock_transcription_client(responses: List[dict]):
    """Mock genai.Client that returns successive transcription responses."""
    call_idx = {"n": 0}

    def make_client(**kwargs):
        mock_response = MagicMock()
        idx = call_idx["n"]
        call_idx["n"] += 1
        mock_response.text = json.dumps(responses[idx % len(responses)])

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio
        return mock_client

    return make_client


def _make_mock_animation_client():
    """Mock genai.Client for animation generation calls."""

    def make_client(**kwargs):
        mock_response = MagicMock()
        mock_response.text = json.dumps(FAKE_ANIMATION_RESPONSE)

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio
        return mock_client

    return make_client


# We need to patch genai.Client in BOTH modules (transcription and animation_generator).
# Since both create their own Client, we use side_effect to handle all calls.

def _make_unified_mock(transcription_responses: List[dict]):
    """Return a side_effect function that returns the right mock client
    depending on whether the call is for transcription or animation."""
    t_idx = {"n": 0}

    def client_factory(**kwargs):
        # Each Client() call gets a fresh mock.
        # We alternate: transcription calls come first in each on_audio_chunk,
        # animation calls come after.  We track by counting.
        mock_client = MagicMock()
        mock_aio = MagicMock()
        mock_models = AsyncMock()

        # We return a mock that can handle either transcription or animation.
        async def fake_generate(*args, **kw):
            mime = kw.get("config", None)
            contents = kw.get("contents", None)

            # If contents is a list (multimodal), it's a transcription call.
            if isinstance(contents, list):
                idx = t_idx["n"]
                t_idx["n"] += 1
                resp = MagicMock()
                resp.text = json.dumps(
                    transcription_responses[idx % len(transcription_responses)]
                )
                return resp
            else:
                # Animation generation call
                resp = MagicMock()
                resp.text = json.dumps(FAKE_ANIMATION_RESPONSE)
                return resp

        mock_models.generate_content = AsyncMock(side_effect=fake_generate)
        mock_aio.models = mock_models
        mock_client.aio = mock_aio
        return mock_client

    return client_factory


# ---------------------------------------------------------------------------
# Tests: single utterance
# ---------------------------------------------------------------------------

class TestSingleUtterance:
    def test_on_audio_chunk_returns_transcription(self):
        ws = FakeWebSocket()
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=ws,
        )

        factory = _make_unified_mock([UTTERANCE_1_RESPONSE])

        with patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):
            result = asyncio.get_event_loop().run_until_complete(
                loop.on_audio_chunk(FAKE_AUDIO)
            )

        assert result.transcription == "um there is a bunny"
        assert loop.scene_progress == 0.2
        assert "t1_identity" in loop.satisfied_targets

    def test_animations_sent_via_websocket(self):
        ws = FakeWebSocket()
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=ws,
        )

        factory = _make_unified_mock([UTTERANCE_1_RESPONSE])

        with patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):
            asyncio.get_event_loop().run_until_complete(
                loop.on_audio_chunk(FAKE_AUDIO)
            )

        # UTTERANCE_1 has 2 discrepancies (PROPERTY_COLOR 0.6, ACTION 0.5)
        # Both should produce animation messages
        anim_messages = [m for m in ws.messages if m["type"] == "animation"]
        assert len(anim_messages) == 2

        # Check animation message structure
        for msg in anim_messages:
            assert "code" in msg
            assert "duration_ms" in msg
            assert "entity_id" in msg
            assert msg["code"] != ""

    def test_student_profile_updated(self):
        ws = FakeWebSocket()
        profile = StudentProfile()
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=profile,
            animation_cache=AnimationCache(),
            websocket=ws,
        )

        factory = _make_unified_mock([UTTERANCE_1_RESPONSE])

        with patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):
            asyncio.get_event_loop().run_until_complete(
                loop.on_audio_chunk(FAKE_AUDIO)
            )

        # record_errors was called with the 2 discrepancies
        assert profile.total_utterances == 1
        assert profile.error_counts.get("PROPERTY_COLOR", 0) >= 1
        assert profile.error_counts.get("ACTION", 0) >= 1


# ---------------------------------------------------------------------------
# Tests: three-utterance integration scenario
# ---------------------------------------------------------------------------

class TestThreeUtteranceScenario:
    def test_full_scenario(self):
        """Simulate 3 audio chunks, verify progressive state updates."""
        ws = FakeWebSocket()
        profile = StudentProfile()
        cache = AnimationCache()

        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=profile,
            animation_cache=cache,
            websocket=ws,
        )

        responses = [UTTERANCE_1_RESPONSE, UTTERANCE_2_RESPONSE, UTTERANCE_3_RESPONSE]
        factory = _make_unified_mock(responses)

        with patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):

            # --- Utterance 1 ---
            r1 = asyncio.get_event_loop().run_until_complete(
                loop.on_audio_chunk(FAKE_AUDIO)
            )

            assert r1.transcription == "um there is a bunny"
            assert loop.scene_progress == 0.2
            assert not loop.is_scene_complete()
            assert profile.total_utterances == 1

            # 2 discrepancies → 2 animations dispatched
            anim_msgs_1 = [m for m in ws.messages if m["type"] == "animation"]
            assert len(anim_msgs_1) == 2

            progress_after_1 = loop.scene_progress

            # --- Utterance 2 ---
            ws.messages.clear()
            r2 = asyncio.get_event_loop().run_until_complete(
                loop.on_audio_chunk(FAKE_AUDIO)
            )

            assert r2.transcription == "a brown bunny is hopping by the rock"
            assert loop.scene_progress == 0.55
            assert loop.scene_progress > progress_after_1
            assert not loop.is_scene_complete()
            assert profile.total_utterances == 2

            # 1 discrepancy → 1 animation
            anim_msgs_2 = [m for m in ws.messages if m["type"] == "animation"]
            assert len(anim_msgs_2) == 1

            # --- Utterance 3 ---
            ws.messages.clear()
            r3 = asyncio.get_event_loop().run_until_complete(
                loop.on_audio_chunk(FAKE_AUDIO)
            )

            assert r3.transcription.startswith("the small brown")
            assert loop.scene_progress == 0.95
            assert loop.is_scene_complete()
            assert profile.total_utterances == 3

            # No discrepancies → no animation, but scene_complete sent
            anim_msgs_3 = [m for m in ws.messages if m["type"] == "animation"]
            assert len(anim_msgs_3) == 0

            complete_msgs = [m for m in ws.messages if m["type"] == "scene_complete"]
            assert len(complete_msgs) == 1

    def test_scene_progress_never_decreases(self):
        """Even if LLM reports lower progress, we keep the max."""
        ws = FakeWebSocket()
        # Responses with non-monotonic progress
        r1 = dict(UTTERANCE_1_RESPONSE)
        r1["scene_progress"] = 0.5
        r2 = dict(UTTERANCE_2_RESPONSE)
        r2["scene_progress"] = 0.3  # lower than r1!

        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=ws,
        )

        factory = _make_unified_mock([r1, r2])

        with patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):

            asyncio.get_event_loop().run_until_complete(loop.on_audio_chunk(FAKE_AUDIO))
            assert loop.scene_progress == 0.5

            asyncio.get_event_loop().run_until_complete(loop.on_audio_chunk(FAKE_AUDIO))
            # Should NOT decrease to 0.3
            assert loop.scene_progress == 0.5

    def test_satisfied_targets_accumulate(self):
        """Satisfied targets merge across utterances."""
        ws = FakeWebSocket()
        r1 = dict(UTTERANCE_1_RESPONSE)
        r1["satisfied_targets"] = ["t1_identity"]
        r2 = dict(UTTERANCE_2_RESPONSE)
        r2["satisfied_targets"] = ["t1_identity"]  # repeated, should not dup
        r3 = dict(UTTERANCE_3_RESPONSE)
        r3["satisfied_targets"] = ["t1_identity", "t2_identity"]

        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=ws,
        )

        factory = _make_unified_mock([r1, r2, r3])

        with patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):

            asyncio.get_event_loop().run_until_complete(loop.on_audio_chunk(FAKE_AUDIO))
            assert loop.satisfied_targets == ["t1_identity"]

            asyncio.get_event_loop().run_until_complete(loop.on_audio_chunk(FAKE_AUDIO))
            assert loop.satisfied_targets == ["t1_identity"]  # no dup

            asyncio.get_event_loop().run_until_complete(loop.on_audio_chunk(FAKE_AUDIO))
            assert set(loop.satisfied_targets) == {"t1_identity", "t2_identity"}


# ---------------------------------------------------------------------------
# Tests: animation cache interaction
# ---------------------------------------------------------------------------

class TestCacheInteraction:
    def test_second_same_error_uses_cache(self):
        """After first generation, the cache should prevent a second LLM call."""
        ws = FakeWebSocket()
        cache = AnimationCache()

        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=cache,
            websocket=ws,
        )

        # Same discrepancy twice
        responses = [UTTERANCE_1_RESPONSE, UTTERANCE_1_RESPONSE]
        factory = _make_unified_mock(responses)

        with patch("src.narration.transcription.genai.Client", side_effect=factory) as t_mock, \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory) as a_mock:

            # First call — animations generated
            asyncio.get_event_loop().run_until_complete(loop.on_audio_chunk(FAKE_AUDIO))

            # After first call, cache should have entries
            assert cache.has("rabbit_01.body", "PROPERTY_COLOR")
            assert cache.has("rabbit_01.legs", "ACTION")

            # Reset ws messages
            ws.messages.clear()

            # Second call — should use cache, no animation generation calls
            asyncio.get_event_loop().run_until_complete(loop.on_audio_chunk(FAKE_AUDIO))

        # Still get animation WS messages (from cache)
        anim_msgs = [m for m in ws.messages if m["type"] == "animation"]
        assert len(anim_msgs) == 2


# ---------------------------------------------------------------------------
# Tests: hesitation / idle timeout
# ---------------------------------------------------------------------------

class TestHesitation:
    def test_idle_timeout_sends_omission(self):
        ws = FakeWebSocket()
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=ws,
        )

        factory = _make_unified_mock([])  # no transcription calls expected

        with patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):

            cmd = asyncio.get_event_loop().run_until_complete(
                loop.on_idle_timeout()
            )

        assert cmd is not None
        assert cmd.error_type == "OMISSION"
        # Should target rabbit_01 (highest priority unsatisfied target)
        assert cmd.entity_id == "rabbit_01"

        # WS should have an animation message
        anim_msgs = [m for m in ws.messages if m["type"] == "animation"]
        assert len(anim_msgs) == 1
        assert anim_msgs[0]["error_type"] == "OMISSION"

    def test_idle_timeout_returns_none_when_all_satisfied(self):
        ws = FakeWebSocket()
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=ws,
        )
        # Pre-satisfy all targets
        loop.satisfied_targets = ["t1_identity", "t2_identity"]

        cmd = asyncio.get_event_loop().run_until_complete(
            loop.on_idle_timeout()
        )

        assert cmd is None
        assert len(ws.messages) == 0


# ---------------------------------------------------------------------------
# Tests: session log
# ---------------------------------------------------------------------------

class TestSessionLog:
    def test_session_log_records_utterances(self):
        ws = FakeWebSocket()
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=ws,
        )

        factory = _make_unified_mock([UTTERANCE_1_RESPONSE, UTTERANCE_2_RESPONSE])

        with patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):

            asyncio.get_event_loop().run_until_complete(loop.on_audio_chunk(FAKE_AUDIO))
            asyncio.get_event_loop().run_until_complete(loop.on_audio_chunk(FAKE_AUDIO))

        log = loop.session_log
        assert len(log) == 2

        assert log[0]["transcription"] == "um there is a bunny"
        assert log[0]["animations_dispatched"] == 2
        assert log[0]["scene_progress"] == 0.2

        assert log[1]["transcription"] == "a brown bunny is hopping by the rock"
        assert log[1]["animations_dispatched"] == 1
        assert log[1]["scene_progress"] == 0.55

    def test_hesitation_logged(self):
        ws = FakeWebSocket()
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=ws,
        )

        factory = _make_unified_mock([])

        with patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):

            asyncio.get_event_loop().run_until_complete(loop.on_idle_timeout())

        log = loop.session_log
        assert len(log) == 1
        assert log[0]["event"] == "hesitation"
        assert log[0]["target_entity"] == "rabbit_01"


# ---------------------------------------------------------------------------
# Tests: is_scene_complete
# ---------------------------------------------------------------------------

class TestIsSceneComplete:
    def test_not_complete_below_threshold(self):
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),  # min_coverage=0.7
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=FakeWebSocket(),
        )
        loop.scene_progress = 0.5
        assert not loop.is_scene_complete()

    def test_complete_at_threshold(self):
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=FakeWebSocket(),
        )
        loop.scene_progress = 0.7
        assert loop.is_scene_complete()

    def test_complete_above_threshold(self):
        loop = NarrationLoop(
            api_key="fake-key",
            scene_manifest=_make_scene_manifest(),
            neg=_make_neg(),
            story_state=_make_story_state(),
            student_profile=StudentProfile(),
            animation_cache=AnimationCache(),
            websocket=FakeWebSocket(),
        )
        loop.scene_progress = 0.95
        assert loop.is_scene_complete()
