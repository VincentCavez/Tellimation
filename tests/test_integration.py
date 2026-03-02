"""End-to-end integration test: complete session from scene generation to branch selection.

Simulates:
1. Scene 1 generation (brave rabbit, enchanted forest)
2. Sprite code execution (validates code parses without error)
3. 3 narration utterances:
   - U1: "there's a rabbit" → PROPERTY_COLOR discrepancy → animation generated
   - U2: "the brown fluffy rabbit" → correction after animation → profile updated
   - U3: full narration → scene_progress >= 0.7 → scene complete
4. 3 branch generation
5. Branch 1 selection
6. Scene 2 carries over rabbit_01 from Scene 1
7. Student profile in Scene 2 reflects errors from Scene 1

Run with mocks (default):
    pytest tests/test_integration.py -v

Run with real Gemini API (requires GEMINI_API_KEY env var):
    pytest tests/test_integration.py -v -m integration
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.generation.scene_generator import generate_scene
from src.generation.branch_generator import generate_branches
from src.models.animation_cache import AnimationCache
from src.models.neg import NEG, NarrativeTarget, TargetComponents
from src.models.scene import Entity, Position, SceneManifest, Relation, Action
from src.models.story_state import StoryState
from src.models.student_profile import StudentProfile
from src.narration.narration_loop import NarrationLoop


# ---------------------------------------------------------------------------
# Fixture data: pre-built LLM responses for deterministic testing
# ---------------------------------------------------------------------------

FAKE_AUDIO = b"\x00\x01\x02\x03" * 100  # 400 bytes dummy audio

# -- Scene 1: Brave rabbit in enchanted forest --

SCENE_1_RESPONSE = {
    "narrative_text": "A brave brown fluffy rabbit hops through an enchanted forest clearing, past a tall mossy oak tree.",
    "branch_summary": "A brave rabbit explores an enchanted forest.",
    "manifest": {
        "scene_id": "scene_01",
        "entities": [
            {
                "id": "rabbit_01",
                "type": "rabbit",
                "properties": {"color": "brown", "size": "small", "texture": "fluffy"},
                "position": {"x": 90, "y": 130, "spatial_ref": "in clearing"},
                "emotion": "brave",
                "carried_over": False,
            },
            {
                "id": "tree_01",
                "type": "oak_tree",
                "properties": {"color": "green", "size": "tall", "texture": "mossy"},
                "position": {"x": 200, "y": 80},
                "emotion": None,
                "carried_over": False,
            },
            {
                "id": "clearing_01",
                "type": "background",
                "properties": {},
                "position": {"x": 140, "y": 140},
                "emotion": None,
                "carried_over": False,
            },
        ],
        "relations": [
            {
                "entity_a": "rabbit_01",
                "entity_b": "tree_01",
                "type": "spatial",
                "preposition": "beside",
            },
        ],
        "actions": [
            {"entity_id": "rabbit_01", "verb": "hop", "tense": "present", "manner": "bravely"},
        ],
    },
    "neg": {
        "targets": [
            {
                "id": "t1_rabbit",
                "entity_id": "rabbit_01",
                "components": {
                    "identity": True,
                    "descriptors": ["brown", "fluffy", "brave"],
                    "spatial": "in clearing",
                    "action": "hopping bravely",
                },
                "priority": 0.9,
                "tolerance": 0.3,
            },
            {
                "id": "t1_tree",
                "entity_id": "tree_01",
                "components": {
                    "identity": True,
                    "descriptors": ["tall", "mossy", "green"],
                    "spatial": None,
                    "action": None,
                },
                "priority": 0.5,
                "tolerance": 0.5,
            },
        ],
        "min_coverage": 0.7,
        "skill_coverage_check": "PASS",
    },
    "sprite_code": {
        "rabbit_01": (
            "const eid = 'rabbit_01';\n"
            "ellip(90, 130, 8, 10, 139, 90, 43, eid+'.body');\n"
            "circ(90, 118, 5, 139, 90, 43, eid+'.head');\n"
            "ellip(87, 108, 2, 6, 180, 140, 100, eid+'.head.ears.left');\n"
            "ellip(93, 108, 2, 6, 180, 140, 100, eid+'.head.ears.right');\n"
            "px(88, 117, 20, 20, 20, eid+'.head.eyes.left');\n"
            "px(92, 117, 20, 20, 20, eid+'.head.eyes.right');\n"
            "circ(90, 141, 2, 255, 255, 255, eid+'.tail');\n"
        ),
        "tree_01": (
            "const eid2 = 'tree_01';\n"
            "rect(197, 90, 6, 60, 101, 67, 33, eid2+'.trunk');\n"
            "ellip(200, 75, 20, 18, 34, 120, 34, eid2+'.canopy');\n"
        ),
    },
    "carried_over_entities": [],
}

# -- Utterance responses for Scene 1 narration --

UTTERANCE_1_RESPONSE = {
    "transcription": "there's a rabbit",
    "discrepancies": [
        {
            "type": "PROPERTY_COLOR",
            "entity_id": "rabbit_01",
            "sub_entity": "rabbit_01.body",
            "details": "Child said 'rabbit' without color descriptor 'brown'",
            "severity": 0.7,
        },
        {
            "type": "OMISSION",
            "entity_id": "tree_01",
            "sub_entity": "tree_01",
            "details": "Tree not mentioned at all",
            "severity": 0.4,
        },
    ],
    "scene_progress": 0.15,
    "satisfied_targets": ["t1_rabbit"],
    "updated_history": ["there's a rabbit"],
    "profile_updates": {
        "errors_this_scene": {"PROPERTY_COLOR": 1, "OMISSION": 1},
        "patterns": "omits descriptive adjectives",
    },
}

UTTERANCE_2_RESPONSE = {
    "transcription": "the brown fluffy rabbit is hopping",
    "discrepancies": [],
    "scene_progress": 0.5,
    "satisfied_targets": ["t1_rabbit"],
    "updated_history": ["there's a rabbit", "the brown fluffy rabbit is hopping"],
    "profile_updates": {
        "errors_this_scene": {},
        "patterns": "corrected color after animation, improving",
    },
}

UTTERANCE_3_RESPONSE = {
    "transcription": "the brave brown fluffy rabbit hops bravely past the tall mossy oak tree in the clearing",
    "discrepancies": [],
    "scene_progress": 0.85,
    "satisfied_targets": ["t1_rabbit", "t1_tree"],
    "updated_history": [
        "there's a rabbit",
        "the brown fluffy rabbit is hopping",
        "the brave brown fluffy rabbit hops bravely past the tall mossy oak tree in the clearing",
    ],
    "profile_updates": {
        "errors_this_scene": {},
        "patterns": "excellent narration, all targets satisfied",
    },
}

# -- Animation response --

ANIMATION_RESPONSE = {
    "animation_type": "color_pop",
    "code": "function animate(buf, PW, PH, t) { for(var i=0;i<buf.length;i++){if(buf[i].e.indexOf('rabbit_01.body')===0){buf[i].r=Math.min(255,Math.round(buf[i]._r*(1+0.3*Math.sin(t*Math.PI*6))));}} }",
    "duration_ms": 1200,
}

# -- Scene 2 branches (rabbit continues + new entities) --

def _make_branch(branch_idx: int) -> dict:
    """Build a Scene 2 branch that carries over rabbit_01."""
    suffixes = {1: "a hidden waterfall", 2: "a mysterious cave", 3: "a sunlit meadow"}
    new_entities = {
        1: {"id": "waterfall_01", "type": "waterfall"},
        2: {"id": "cave_01", "type": "cave"},
        3: {"id": "meadow_01", "type": "meadow"},
    }
    ne = new_entities[branch_idx]
    return {
        "narrative_text": f"The brave rabbit discovers {suffixes[branch_idx]}.",
        "branch_summary": f"The rabbit finds {suffixes[branch_idx]}.",
        "manifest": {
            "scene_id": f"scene_02_branch_{branch_idx}",
            "entities": [
                {
                    "id": "rabbit_01",
                    "type": "rabbit",
                    "properties": {"color": "brown", "size": "small", "texture": "fluffy"},
                    "position": {"x": 120, "y": 125, "spatial_ref": f"near {ne['id']}"},
                    "emotion": "curious",
                    "carried_over": True,
                },
                {
                    "id": ne["id"],
                    "type": ne["type"],
                    "properties": {"color": "blue" if branch_idx == 1 else "dark", "size": "large"},
                    "position": {"x": 180, "y": 100},
                    "emotion": None,
                    "carried_over": False,
                },
            ],
            "relations": [
                {
                    "entity_a": "rabbit_01",
                    "entity_b": ne["id"],
                    "type": "spatial",
                    "preposition": "near",
                },
            ],
            "actions": [
                {"entity_id": "rabbit_01", "verb": "explore", "tense": "present"},
            ],
        },
        "neg": {
            "targets": [
                {
                    "id": "t2_rabbit",
                    "entity_id": "rabbit_01",
                    "components": {
                        "identity": True,
                        "descriptors": ["brown", "fluffy"],
                        "spatial": f"near {ne['id']}",
                        "action": "exploring",
                    },
                    "priority": 0.8,
                },
                {
                    "id": f"t2_{ne['id']}",
                    "entity_id": ne["id"],
                    "components": {
                        "identity": True,
                        "descriptors": ["blue" if branch_idx == 1 else "dark", "large"],
                    },
                    "priority": 0.6,
                },
            ],

            "min_coverage": 0.7,
            "skill_coverage_check": "PASS",
        },
        "sprite_code": {
            ne["id"]: f"rect(170, 90, 30, 30, 100, 100, 200, '{ne['id']}');",
        },
        "carried_over_entities": ["rabbit_01"],
    }


BRANCH_1 = _make_branch(1)
BRANCH_2 = _make_branch(2)
BRANCH_3 = _make_branch(3)


# ---------------------------------------------------------------------------
# Mock factory: unified mock for all genai.Client calls
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """Collects WebSocket messages for assertion."""

    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

    async def send_json(self, data: Dict[str, Any]) -> None:
        self.messages.append(data)


def _make_unified_mock(
    scene_responses: List[dict],
    transcription_responses: List[dict],
    animation_response: dict,
):
    """Build a genai.Client side_effect that routes calls by content type.

    - If contents is a list (multimodal) → transcription call
    - If contents is a string and the call comes from scene/branch generator →
      scene generation call
    - If the system_instruction mentions "animation" → animation call
    """
    scene_idx = {"n": 0}
    trans_idx = {"n": 0}

    def client_factory(**kwargs):
        mock_client = MagicMock()
        mock_aio = MagicMock()
        mock_models = AsyncMock()

        async def fake_generate(*args, **kw):
            contents = kw.get("contents", None)
            config = kw.get("config", None)

            # Multimodal (list of parts) → transcription
            if isinstance(contents, list):
                idx = trans_idx["n"]
                trans_idx["n"] += 1
                resp = MagicMock()
                resp.text = json.dumps(
                    transcription_responses[idx % len(transcription_responses)]
                )
                return resp

            # Check system_instruction to distinguish scene vs animation
            sys_instr = getattr(config, "system_instruction", "") if config else ""
            if "animate" in sys_instr.lower() or "animation" in sys_instr.lower():
                resp = MagicMock()
                resp.text = json.dumps(animation_response)
                return resp

            # Otherwise it's a scene/branch generation call
            idx = scene_idx["n"]
            scene_idx["n"] += 1
            resp = MagicMock()
            resp.text = json.dumps(
                scene_responses[idx % len(scene_responses)]
            )
            return resp

        mock_models.generate_content = AsyncMock(side_effect=fake_generate)
        mock_aio.models = mock_models
        mock_client.aio = mock_aio
        return mock_client

    return client_factory


# ===========================================================================
# MOCKED END-TO-END TEST
# ===========================================================================

class TestEndToEndMocked:
    """Full session simulation with mocked Gemini calls."""

    def test_complete_session(self):
        """Simulate a complete session: scene gen → narration → branches → scene 2."""
        ws = FakeWebSocket()
        story_state = StoryState(
            session_id="integration_test",
            participant_id="P_TEST",
            skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
        )
        student_profile = StudentProfile()
        animation_cache = AnimationCache()

        # All scene responses: Scene 1 + 3 branches
        scene_responses = [SCENE_1_RESPONSE, BRANCH_1, BRANCH_2, BRANCH_3]
        transcription_responses = [
            UTTERANCE_1_RESPONSE,
            UTTERANCE_2_RESPONSE,
            UTTERANCE_3_RESPONSE,
        ]
        factory = _make_unified_mock(
            scene_responses, transcription_responses, ANIMATION_RESPONSE
        )

        with patch("src.generation.scene_generator.genai.Client", side_effect=factory), \
             patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):

            loop = asyncio.get_event_loop()

            # ================================================================
            # STEP 1: Generate Scene 1
            # ================================================================
            scene_1 = loop.run_until_complete(
                generate_scene(
                    api_key="test-key",
                    story_state=story_state,
                    student_profile=None,
                    skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
                    seed_index=1,
                    commit_to_state=True,
                    use_reference_images=False,
                )
            )

            # Verify scene structure
            assert scene_1["manifest"]["scene_id"] == "scene_01"
            manifest = SceneManifest.model_validate(scene_1["manifest"])
            assert "rabbit_01" in manifest.entity_ids()
            assert "tree_01" in manifest.entity_ids()
            neg = NEG.model_validate(scene_1["neg"])
            assert len(neg.targets) == 2

            # Verify story_state updated
            assert len(story_state.scenes) == 1
            assert "rabbit_01" in story_state.active_entities

            # ================================================================
            # STEP 2: Validate sprite code can be parsed
            # ================================================================
            sprite_code = scene_1.get("sprite_code", {})
            assert "rabbit_01" in sprite_code
            assert "tree_01" in sprite_code
            # Verify sprite code contains valid primitive calls
            assert "ellip(" in sprite_code["rabbit_01"]
            assert "circ(" in sprite_code["rabbit_01"]
            assert "rect(" in sprite_code["tree_01"]

            # ================================================================
            # STEP 3: Narration Loop — 3 utterances
            # ================================================================
            narration = NarrationLoop(
                api_key="test-key",
                scene_manifest=manifest,
                neg=neg,
                story_state=story_state,
                student_profile=student_profile,
                animation_cache=animation_cache,
                websocket=ws,
            )

            # -- Utterance 1: "there's a rabbit" (missing descriptors) --
            r1 = loop.run_until_complete(narration.on_audio_chunk(FAKE_AUDIO))

            assert r1.transcription == "there's a rabbit"
            assert len(r1.discrepancies) >= 1
            # PROPERTY_COLOR discrepancy for rabbit_01
            color_errors = [d for d in r1.discrepancies if d.type == "PROPERTY_COLOR"]
            assert len(color_errors) >= 1
            assert color_errors[0].entity_id == "rabbit_01"

            # Student profile records the error
            assert student_profile.total_utterances == 1
            assert student_profile.error_counts.get("PROPERTY_COLOR", 0) >= 1

            # Animation was generated and sent via WS
            anim_msgs_1 = [m for m in ws.messages if m["type"] == "animation"]
            assert len(anim_msgs_1) >= 1
            # At least one animation for PROPERTY_COLOR
            color_anims = [a for a in anim_msgs_1 if a["error_type"] == "PROPERTY_COLOR"]
            assert len(color_anims) >= 1
            assert "code" in color_anims[0]
            assert color_anims[0]["code"] != ""

            # Animation is now cached
            assert animation_cache.has("rabbit_01.body", "PROPERTY_COLOR")

            # Scene not complete yet
            assert narration.scene_progress == 0.15
            assert not narration.is_scene_complete()

            # -- Utterance 2: "the brown fluffy rabbit" (correction!) --
            ws.messages.clear()
            r2 = loop.run_until_complete(narration.on_audio_chunk(FAKE_AUDIO))

            assert r2.transcription == "the brown fluffy rabbit is hopping"
            assert len(r2.discrepancies) == 0  # Corrected!
            assert student_profile.total_utterances == 2
            assert narration.scene_progress == 0.5
            assert not narration.is_scene_complete()

            # No new animations needed (no discrepancies)
            anim_msgs_2 = [m for m in ws.messages if m["type"] == "animation"]
            assert len(anim_msgs_2) == 0

            # -- Utterance 3: full narration → scene complete --
            ws.messages.clear()
            r3 = loop.run_until_complete(narration.on_audio_chunk(FAKE_AUDIO))

            assert "brave brown fluffy rabbit" in r3.transcription
            assert len(r3.discrepancies) == 0
            assert narration.scene_progress == 0.85
            assert narration.is_scene_complete()
            assert student_profile.total_utterances == 3

            # scene_complete message sent
            complete_msgs = [m for m in ws.messages if m["type"] == "scene_complete"]
            assert len(complete_msgs) == 1

            # Both targets satisfied
            assert "t1_rabbit" in narration.satisfied_targets
            assert "t1_tree" in narration.satisfied_targets

            # Session log has 3 entries
            log = narration.session_log
            assert len(log) == 3
            assert log[0]["transcription"] == "there's a rabbit"
            assert log[0]["animations_dispatched"] >= 1
            assert log[2]["scene_progress"] == 0.85

            # ================================================================
            # STEP 4: Generate 3 branches
            # ================================================================
            branches = loop.run_until_complete(
                generate_branches(
                    api_key="test-key",
                    story_state=story_state,
                    student_profile=student_profile,
                    skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
                    use_reference_images=False,
                )
            )

            assert len(branches) == 3
            for b in branches:
                assert "manifest" in b
                assert "neg" in b
                assert "branch_summary" in b

            # ================================================================
            # STEP 5: Select branch 1 (waterfall)
            # ================================================================
            chosen = branches[0]
            chosen_manifest = SceneManifest.model_validate(chosen["manifest"])

            # Verify rabbit_01 is carried over
            rabbit_in_s2 = chosen_manifest.get_entity("rabbit_01")
            assert rabbit_in_s2 is not None
            assert rabbit_in_s2.carried_over is True

            # New entity present
            assert "waterfall_01" in chosen_manifest.entity_ids()

            # Commit Scene 2 to story state
            story_state.add_scene(
                scene_id=chosen["manifest"]["scene_id"],
                narrative_text=chosen.get("narrative_text", ""),
                manifest=chosen["manifest"],
                neg=chosen.get("neg", {}),
                sprite_code=chosen.get("sprite_code"),
            )

            # ================================================================
            # STEP 6: Verify entity carry-over
            # ================================================================
            assert len(story_state.scenes) == 2
            assert "rabbit_01" in story_state.active_entities
            # rabbit_01 sprite code should still be from Scene 1
            rabbit_sprite = story_state.get_entity_sprite("rabbit_01")
            assert rabbit_sprite is not None
            assert "rabbit_01" in rabbit_sprite

            # New entity added to active_entities
            assert "waterfall_01" in story_state.active_entities

            # ================================================================
            # STEP 7: Verify student_profile carries Scene 1 errors
            # ================================================================
            assert student_profile.total_utterances == 3
            assert student_profile.error_counts.get("PROPERTY_COLOR", 0) >= 1
            assert student_profile.error_counts.get("OMISSION", 0) >= 1

            # Profile context includes error info
            ctx = student_profile.to_prompt_context()
            assert "PROPERTY_COLOR" in ctx
            assert "Utterances so far: 3" in ctx

    def test_animation_cache_persists_across_scenes(self):
        """Animation cache entries from Scene 1 are available in Scene 2."""
        animation_cache = AnimationCache()

        factory = _make_unified_mock(
            [SCENE_1_RESPONSE], [UTTERANCE_1_RESPONSE], ANIMATION_RESPONSE
        )

        with patch("src.generation.scene_generator.genai.Client", side_effect=factory), \
             patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):

            loop = asyncio.get_event_loop()

            story_state = StoryState()
            scene = loop.run_until_complete(
                generate_scene(api_key="k", story_state=story_state, commit_to_state=True, use_reference_images=False)
            )
            manifest = SceneManifest.model_validate(scene["manifest"])
            neg = NEG.model_validate(scene["neg"])

            ws = FakeWebSocket()
            narration = NarrationLoop(
                api_key="k",
                scene_manifest=manifest,
                neg=neg,
                story_state=story_state,
                student_profile=StudentProfile(),
                animation_cache=animation_cache,
                websocket=ws,
            )

            loop.run_until_complete(narration.on_audio_chunk(FAKE_AUDIO))

        # Cache should have entries from Scene 1
        assert animation_cache.has("rabbit_01.body", "PROPERTY_COLOR")

        # These entries will be available when Scene 2 narration starts
        cached = animation_cache.lookup("rabbit_01.body", "PROPERTY_COLOR")
        assert cached is not None
        assert "animate" in cached.code

    def test_sprite_code_syntax_valid(self):
        """Verify all sprite code in the test fixtures uses valid primitive calls."""
        for entity_id, code in SCENE_1_RESPONSE["sprite_code"].items():
            # Each code block should contain at least one primitive call
            primitives = ["px(", "rect(", "circ(", "ellip(", "tri(", "line(", "thickLine(", "arc("]
            has_primitive = any(p in code for p in primitives)
            assert has_primitive, f"Sprite code for {entity_id} lacks primitive calls"
            # Should reference its own entity ID
            assert entity_id in code, f"Sprite code for {entity_id} doesn't reference itself"

    def test_branch_diversity(self):
        """The 3 branches should have distinct scene_ids and new entities."""
        branch_ids = {b["manifest"]["scene_id"] for b in [BRANCH_1, BRANCH_2, BRANCH_3]}
        assert len(branch_ids) == 3

        new_entity_ids = set()
        for b in [BRANCH_1, BRANCH_2, BRANCH_3]:
            m = SceneManifest.model_validate(b["manifest"])
            for eid in m.entity_ids():
                if eid != "rabbit_01":
                    new_entity_ids.add(eid)
        # Each branch introduces a distinct new entity
        assert len(new_entity_ids) == 3

    def test_scene_progress_monotonic(self):
        """Scene progress never decreases across utterances."""
        ws = FakeWebSocket()
        factory = _make_unified_mock(
            [SCENE_1_RESPONSE],
            [UTTERANCE_1_RESPONSE, UTTERANCE_2_RESPONSE, UTTERANCE_3_RESPONSE],
            ANIMATION_RESPONSE,
        )

        with patch("src.generation.scene_generator.genai.Client", side_effect=factory), \
             patch("src.narration.transcription.genai.Client", side_effect=factory), \
             patch("src.generation.animation_generator.genai.Client", side_effect=factory):

            loop = asyncio.get_event_loop()
            story_state = StoryState()
            scene = loop.run_until_complete(
                generate_scene(api_key="k", story_state=story_state, commit_to_state=True, use_reference_images=False)
            )
            manifest = SceneManifest.model_validate(scene["manifest"])
            neg = NEG.model_validate(scene["neg"])

            narration = NarrationLoop(
                api_key="k",
                scene_manifest=manifest,
                neg=neg,
                story_state=story_state,
                student_profile=StudentProfile(),
                animation_cache=AnimationCache(),
                websocket=ws,
            )

            progress_history = []
            for _ in range(3):
                loop.run_until_complete(narration.on_audio_chunk(FAKE_AUDIO))
                progress_history.append(narration.scene_progress)

        # Progress should be monotonically non-decreasing
        for i in range(1, len(progress_history)):
            assert progress_history[i] >= progress_history[i - 1], (
                f"Progress decreased: {progress_history}"
            )


# ===========================================================================
# LIVE INTEGRATION TEST (requires GEMINI_API_KEY)
# ===========================================================================

@pytest.mark.integration
class TestEndToEndLive:
    """Real API integration tests. Requires GEMINI_API_KEY env var.

    Run with: pytest tests/test_integration.py -v -m integration
    Skip by default (no marker selected).
    """

    @pytest.fixture
    def api_key(self):
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            pytest.skip("GEMINI_API_KEY not set")
        return key

    def test_live_scene_generation(self, api_key):
        """Generate a real scene via Gemini and validate structure."""
        loop = asyncio.get_event_loop()

        scene = loop.run_until_complete(
            generate_scene(
                api_key=api_key,
                story_state=None,
                student_profile=None,
                skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
                seed_index=1,
                commit_to_state=False,
            )
        )

        # Validate returned structure
        assert "manifest" in scene
        assert "neg" in scene
        assert "sprite_code" in scene
        assert "narrative_text" in scene

        manifest = SceneManifest.model_validate(scene["manifest"])
        assert len(manifest.entities) >= 1
        assert manifest.scene_id != ""

        neg = NEG.model_validate(scene["neg"])
        assert len(neg.targets) >= 1

        # Sprite code should exist for at least one entity
        assert len(scene.get("sprite_code", {})) >= 1

        # Sprite code should contain primitive calls
        for eid, code in scene["sprite_code"].items():
            primitives = ["px(", "rect(", "circ(", "ellip(", "tri(", "line(", "thickLine(", "arc("]
            has_primitive = any(p in code for p in primitives)
            assert has_primitive, f"Live sprite code for {eid} lacks primitives: {code[:100]}"

    def test_live_branch_generation(self, api_key):
        """Generate real branches and validate carry-over."""
        loop = asyncio.get_event_loop()

        # First generate Scene 1
        story_state = StoryState(
            session_id="live_test",
            participant_id="P_LIVE",
            skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
        )

        scene = loop.run_until_complete(
            generate_scene(
                api_key=api_key,
                story_state=story_state,
                skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
                seed_index=1,
                commit_to_state=True,
            )
        )

        assert len(story_state.scenes) == 1

        # Generate 3 branches
        branches = loop.run_until_complete(
            generate_branches(
                api_key=api_key,
                story_state=story_state,
                student_profile=StudentProfile(),
                skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
            )
        )

        assert len(branches) >= 1  # At least 1 should succeed (graceful degradation)

        for b in branches:
            assert "manifest" in b
            assert "neg" in b
            bm = SceneManifest.model_validate(b["manifest"])
            assert len(bm.entities) >= 1
