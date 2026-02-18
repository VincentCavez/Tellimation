"""Tests for branch_generator."""

import asyncio
import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.story_state import StoryState
from src.models.student_profile import StudentProfile
from src.generation.branch_generator import (
    _build_branch_directive,
    _build_profile_emphasis,
    generate_branches,
    generate_one_more,
)
from src.generation.prompts.scene_prompt import BRANCH_DIRECTIVE

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_SCENE_BASE = {
    "narrative_text": "A fluffy orange fox sat beside a tall mossy rock.",
    "branch_summary": "A curious fox discovers a forest clearing",
    "manifest": {
        "scene_id": "scene_01",
        "entities": [
            {
                "id": "fox_01",
                "type": "fox",
                "properties": {"color": "orange", "size": "small", "texture": "fluffy"},
                "position": {"x": 80, "y": 130, "spatial_ref": "beside rock_01"},
                "emotion": "curious",
                "carried_over": False,
            },
            {
                "id": "rock_01",
                "type": "rock",
                "properties": {"color": "grey", "size": "large", "texture": "mossy"},
                "position": {"x": 140, "y": 140},
                "emotion": None,
                "carried_over": False,
            },
        ],
        "relations": [
            {"entity_a": "fox_01", "entity_b": "rock_01", "type": "spatial", "preposition": "beside"}
        ],
        "actions": [
            {"entity_id": "fox_01", "verb": "sit", "tense": "present", "manner": "quietly"}
        ],
    },
    "neg": {
        "targets": [
            {
                "id": "t1_identity",
                "entity_id": "fox_01",
                "components": {
                    "identity": True,
                    "descriptors": ["orange", "fluffy"],
                    "spatial": "beside rock_01",
                    "action": "sitting quietly",
                },
                "priority": 0.9,
                "tolerance": 0.3,
            }
        ],
        "error_exclusions": [],
        "min_coverage": 0.7,
        "skill_coverage_check": "PASS",
    },
    "sprite_code": {
        "fox_01": "const eid='fox_01';\ncirc(80,130,10,230,140,30,eid+'.body');\ncirc(70,125,6,230,140,30,eid+'.head');\ntri(64,125,66,116,68,123,230,140,30,eid+'.head.ears.left');\ntri(72,123,74,116,76,125,230,140,30,eid+'.head.ears.right');\ncirc(67,124,1,0,0,0,eid+'.head.eyes.left');\ncirc(73,124,1,0,0,0,eid+'.head.eyes.right');\npx(70,127,0,0,0,eid+'.head.nose');\narc(92,130,8,0.5,1.8,230,140,30,eid+'.tail');",
        "rock_01": "const eid='rock_01';\nellip(140,145,14,8,130,130,125,eid+'.body');\nellip(140,142,12,5,140,140,135,eid+'.body.top');\npx(135,140,100,120,80,eid+'.body.moss1');\npx(138,139,100,120,80,eid+'.body.moss2');\npx(142,140,100,120,80,eid+'.body.moss3');\npx(145,141,100,120,80,eid+'.body.moss4');\nline(132,148,148,148,110,110,105,eid+'.shadow');\npx(140,138,160,160,155,eid+'.body.highlight');",
    },
    "carried_over_entities": [],
}


def _make_branch_response(branch_idx: int):
    """Create a unique fake response per branch index."""
    resp = copy.deepcopy(FAKE_SCENE_BASE)
    resp["manifest"]["scene_id"] = "scene_02"
    resp["branch_summary"] = f"Branch {branch_idx} story direction"
    resp["narrative_text"] = f"Branch {branch_idx}: something unique happens."
    # Mark entities as carried over for a continuation scene
    for ent in resp["manifest"]["entities"]:
        ent["carried_over"] = True
    resp["carried_over_entities"] = ["fox_01", "rock_01"]
    resp["sprite_code"] = {}
    return resp


def _make_mock_client_for_branches(n_branches: int):
    """Create a mock client that returns different responses for each call."""
    responses = []
    for i in range(1, n_branches + 1):
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(_make_branch_response(i))
        responses.append(mock_resp)

    call_count = {"n": 0}

    async def fake_generate(*args, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        return responses[idx % len(responses)]

    mock_models = AsyncMock()
    mock_models.generate_content = AsyncMock(side_effect=fake_generate)
    mock_aio = MagicMock()
    mock_aio.models = mock_models
    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client


def _make_story_state_with_scene():
    """Create a StoryState with one completed scene."""
    state = StoryState(
        session_id="s1",
        participant_id="P01",
        skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
    )
    state.add_scene(
        scene_id="scene_01",
        narrative_text="A fluffy orange fox sat beside a mossy rock.",
        manifest=FAKE_SCENE_BASE["manifest"],
        neg=FAKE_SCENE_BASE["neg"],
        sprite_code=FAKE_SCENE_BASE["sprite_code"],
    )
    for ent_data in FAKE_SCENE_BASE["manifest"]["entities"]:
        eid = ent_data["id"]
        if eid in state.active_entities:
            state.active_entities[eid].type = ent_data["type"]
    return state


# ---------------------------------------------------------------------------
# Tests: branch directive building
# ---------------------------------------------------------------------------

class TestBranchDirective:
    def test_directive_contains_branch_index(self):
        directive = _build_branch_directive(2, 3, None)
        assert "branch 2" in directive
        assert "3" in directive

    def test_directive_contains_flavor(self):
        d1 = _build_branch_directive(1, 3, None)
        d2 = _build_branch_directive(2, 3, None)
        d3 = _build_branch_directive(3, 3, None)
        assert "calm" in d1.lower() or "reflective" in d1.lower() or "peaceful" in d1.lower()
        assert "exciting" in d2.lower() or "adventurous" in d2.lower() or "action" in d2.lower()
        assert "whimsical" in d3.lower() or "humorous" in d3.lower() or "funny" in d3.lower()

    def test_directive_fallback_flavor_for_high_index(self):
        d = _build_branch_directive(5, 5, None)
        assert "surprise" in d.lower() or "original" in d.lower()

    def test_directive_includes_preview_entities_instruction(self):
        d = _build_branch_directive(1, 3, None)
        assert "preview_entities" in d

    def test_directive_with_profile_includes_emphasis(self):
        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 8, "SPATIAL": 1},
            total_utterances=10,
            scenes_completed=3,
        )
        d = _build_branch_directive(1, 3, profile)
        assert "descriptive_adjectives" in d or "PROPERTY_COLOR" in d

    def test_directive_without_profile_has_no_emphasis(self):
        d = _build_branch_directive(1, 3, None)
        assert "Student Profile" not in d


# ---------------------------------------------------------------------------
# Tests: profile emphasis
# ---------------------------------------------------------------------------

class TestProfileEmphasis:
    def test_empty_profile_returns_empty(self):
        profile = StudentProfile()
        assert _build_profile_emphasis(profile, 1) == ""

    def test_none_profile_returns_empty(self):
        assert _build_profile_emphasis(None, 1) == ""

    def test_profile_no_weak_areas(self):
        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 1},
            total_utterances=20,
            scenes_completed=5,
        )
        result = _build_profile_emphasis(profile, 1)
        assert "doing well" in result.lower()

    def test_profile_rotates_weak_areas(self):
        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 5, "SPATIAL": 4},
            total_utterances=10,
            scenes_completed=3,
        )
        e1 = _build_profile_emphasis(profile, 1)
        e2 = _build_profile_emphasis(profile, 2)
        # Different branches should emphasize different error types
        # (rotation depends on get_weak_areas() order)
        assert "PROPERTY_COLOR" in e1 or "SPATIAL" in e1
        assert "PROPERTY_COLOR" in e2 or "SPATIAL" in e2

    def test_increasing_trend_noted(self):
        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 8},
            error_trend={"PROPERTY_COLOR": "increasing"},
            total_utterances=10,
            scenes_completed=3,
        )
        result = _build_profile_emphasis(profile, 1)
        assert "INCREASING" in result or "increasing" in result.lower()

    def test_decreasing_trend_noted(self):
        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 8},
            error_trend={"PROPERTY_COLOR": "decreasing"},
            total_utterances=10,
            scenes_completed=3,
        )
        result = _build_profile_emphasis(profile, 1)
        assert "decreasing" in result.lower()


# ---------------------------------------------------------------------------
# Tests: generate_branches
# ---------------------------------------------------------------------------

class TestGenerateBranches:
    def test_generates_3_branches(self):
        state = _make_story_state_with_scene()
        mock_client = _make_mock_client_for_branches(3)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            branches = asyncio.get_event_loop().run_until_complete(
                generate_branches(
                    api_key="fake-key",
                    story_state=state,
                    skill_objectives=["descriptive_adjectives"],
                    n_branches=3,
                    use_reference_images=False,
                )
            )

        assert len(branches) == 3

    def test_branches_have_different_summaries(self):
        state = _make_story_state_with_scene()
        mock_client = _make_mock_client_for_branches(3)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            branches = asyncio.get_event_loop().run_until_complete(
                generate_branches(
                    api_key="fake-key",
                    story_state=state,
                    n_branches=3,
                    use_reference_images=False,
                )
            )

        summaries = [b["branch_summary"] for b in branches]
        assert len(set(summaries)) == 3, f"Expected 3 unique summaries, got: {summaries}"

    def test_branches_have_valid_manifests(self):
        state = _make_story_state_with_scene()
        mock_client = _make_mock_client_for_branches(3)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            branches = asyncio.get_event_loop().run_until_complete(
                generate_branches(
                    api_key="fake-key",
                    story_state=state,
                    n_branches=3,
                    use_reference_images=False,
                )
            )

        for branch in branches:
            manifest = branch["manifest"]
            assert "scene_id" in manifest
            assert "entities" in manifest
            assert len(manifest["entities"]) >= 1
            assert "neg" in branch
            assert branch["neg"]["skill_coverage_check"] == "PASS"

    def test_branches_do_not_mutate_story_state(self):
        state = _make_story_state_with_scene()
        scenes_before = len(state.scenes)
        entities_before = set(state.active_entities.keys())

        mock_client = _make_mock_client_for_branches(3)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_branches(
                    api_key="fake-key",
                    story_state=state,
                    n_branches=3,
                    use_reference_images=False,
                )
            )

        assert len(state.scenes) == scenes_before
        assert set(state.active_entities.keys()) == entities_before

    def test_branches_called_with_correct_model(self):
        state = _make_story_state_with_scene()
        mock_client = _make_mock_client_for_branches(3)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client) as mock_cls:
            asyncio.get_event_loop().run_until_complete(
                generate_branches(
                    api_key="test-key",
                    story_state=state,
                    n_branches=3,
                    use_reference_images=False,
                )
            )

        # All 3 calls should use the same API key
        for call in mock_cls.call_args_list:
            assert call.kwargs.get("api_key") == "test-key"

    def test_partial_failure_returns_successful_branches(self):
        """If one branch fails, the others should still be returned."""
        state = _make_story_state_with_scene()

        responses = []
        for i in range(3):
            if i == 1:
                responses.append(None)  # will cause failure
            else:
                mock_resp = MagicMock()
                mock_resp.text = json.dumps(_make_branch_response(i + 1))
                responses.append(mock_resp)

        call_count = {"n": 0}

        async def fake_generate(*args, **kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            if responses[idx % len(responses)] is None:
                raise RuntimeError("Simulated API failure")
            return responses[idx % len(responses)]

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(side_effect=fake_generate)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            branches = asyncio.get_event_loop().run_until_complete(
                generate_branches(
                    api_key="fake-key",
                    story_state=state,
                    n_branches=3,
                    use_reference_images=False,
                )
            )

        # 2 out of 3 should succeed
        assert len(branches) == 2

    def test_branch_prompts_include_directive(self):
        """Verify that the extra_prompt (branch directive) is passed to the LLM."""
        state = _make_story_state_with_scene()
        mock_client = _make_mock_client_for_branches(3)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_branches(
                    api_key="fake-key",
                    story_state=state,
                    n_branches=3,
                    use_reference_images=False,
                )
            )

        # Check that generate_content was called 3 times
        assert mock_client.aio.models.generate_content.call_count == 3

        # Each call's prompt (contents arg) should contain branch directive text
        for call in mock_client.aio.models.generate_content.call_args_list:
            prompt = call.kwargs.get("contents") or call.args[0]
            assert "Branch generation context" in prompt
            assert "preview_entities" in prompt


# ---------------------------------------------------------------------------
# Tests: generate_one_more
# ---------------------------------------------------------------------------

class TestGenerateOneMore:
    def test_returns_single_branch(self):
        state = _make_story_state_with_scene()
        existing = [_make_branch_response(i) for i in range(1, 4)]

        mock_resp = MagicMock()
        mock_resp.text = json.dumps(_make_branch_response(4))
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_resp)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_one_more(
                    api_key="fake-key",
                    existing_branches=existing,
                    story_state=state,
                    use_reference_images=False,
                )
            )

        assert "manifest" in result
        assert "neg" in result
        assert "branch_summary" in result

    def test_seed_index_is_n_plus_1(self):
        """The new branch should get seed_index = len(existing) + 1."""
        state = _make_story_state_with_scene()
        existing = [_make_branch_response(i) for i in range(1, 4)]

        mock_resp = MagicMock()
        mock_resp.text = json.dumps(_make_branch_response(4))
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_resp)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_one_more(
                    api_key="fake-key",
                    existing_branches=existing,
                    story_state=state,
                    use_reference_images=False,
                )
            )

        # The prompt should mention branch 4 of 4
        call_args = mock_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents") or call_args.args[0]
        assert "branch 4" in prompt

    def test_does_not_mutate_story_state(self):
        state = _make_story_state_with_scene()
        existing = [_make_branch_response(i) for i in range(1, 4)]
        scenes_before = len(state.scenes)

        mock_resp = MagicMock()
        mock_resp.text = json.dumps(_make_branch_response(4))
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_resp)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_one_more(
                    api_key="fake-key",
                    existing_branches=existing,
                    story_state=state,
                    use_reference_images=False,
                )
            )

        assert len(state.scenes) == scenes_before
