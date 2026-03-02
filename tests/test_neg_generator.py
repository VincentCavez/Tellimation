"""Tests for NEG generator (offline generation + live update)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.neg import NEG
from src.models.student_profile import StudentProfile
from src.generation.neg_generator import (
    NEG_MODEL_ID,
    _validate_neg_response,
    generate_neg_for_plot,
    update_neg_live,
)
from src.generation.prompts.neg_short_prompt import (
    NEG_SHORT_SYSTEM_PROMPT,
    NEG_SHORT_USER_PROMPT_TEMPLATE,
    NEG_UPDATE_SYSTEM_PROMPT,
    NEG_UPDATE_USER_PROMPT_TEMPLATE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_NEG_RESPONSE = {
    "scenes": [
        {
            "scene_id": "scene_01",
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
                    },
                    {
                        "id": "t2_spatial",
                        "entity_id": "rock_01",
                        "components": {
                            "identity": True,
                            "descriptors": ["grey", "mossy"],
                        },
                        "priority": 0.5,
                        "tolerance": 0.6,
                    },
                ],
                "min_coverage": 0.7,
                "skill_coverage_check": "PASS",
            },
        },
        {
            "scene_id": "scene_02",
            "neg": {
                "targets": [
                    {
                        "id": "t1_identity",
                        "entity_id": "frog_01",
                        "components": {
                            "identity": True,
                            "descriptors": ["green", "tiny"],
                            "spatial": "on pond_01",
                        },
                        "priority": 0.8,
                        "tolerance": 0.4,
                    }
                ],

                "min_coverage": 0.7,
                "skill_coverage_check": "PASS",
            },
        },
    ]
}

FAKE_UPDATE_RESPONSE = {
    "scenes": [
        {
            "scene_id": "scene_02",
            "neg": {
                "targets": [
                    {
                        "id": "t1_identity",
                        "entity_id": "frog_01",
                        "components": {
                            "identity": True,
                            "descriptors": ["green", "tiny"],
                            "spatial": "on pond_01",
                        },
                        "priority": 1.0,  # increased from 0.8
                        "tolerance": 0.2,  # decreased from 0.4
                    },
                    {
                        "id": "t2_color",
                        "entity_id": "frog_01",
                        "components": {
                            "identity": False,
                            "descriptors": ["green"],
                        },
                        "priority": 0.9,
                        "tolerance": 0.2,
                    },
                ],

                "min_coverage": 0.8,  # increased from 0.7
                "skill_coverage_check": "PASS",
            },
        }
    ]
}

FAKE_PLOT_SCENES = [
    {
        "manifest": {
            "scene_id": "scene_01",
            "entities": [
                {
                    "id": "fox_01",
                    "type": "fox",
                    "properties": {"color": "orange", "size": "small"},
                    "position": {"x": 80, "y": 130},
                },
                {
                    "id": "rock_01",
                    "type": "rock",
                    "properties": {"color": "grey", "size": "large"},
                    "position": {"x": 140, "y": 140},
                },
            ],
            "relations": [
                {"entity_a": "fox_01", "entity_b": "rock_01", "type": "spatial", "preposition": "beside"}
            ],
            "actions": [
                {"entity_id": "fox_01", "verb": "sit", "tense": "present", "manner": "quietly"}
            ],
        }
    },
    {
        "manifest": {
            "scene_id": "scene_02",
            "entities": [
                {
                    "id": "frog_01",
                    "type": "frog",
                    "properties": {"color": "green", "size": "tiny"},
                    "position": {"x": 150, "y": 145},
                },
                {
                    "id": "pond_01",
                    "type": "pond",
                    "properties": {"color": "blue", "size": "medium"},
                    "position": {"x": 140, "y": 150},
                },
            ],
            "relations": [
                {"entity_a": "frog_01", "entity_b": "pond_01", "type": "spatial", "preposition": "on"}
            ],
            "actions": [
                {"entity_id": "frog_01", "verb": "watch", "tense": "present"}
            ],
        }
    },
]


def _make_mock_client(response_dict):
    """Create a mock genai.Client that returns the given dict as JSON."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(response_dict)

    mock_models = AsyncMock()
    mock_models.generate_content = AsyncMock(return_value=mock_response)

    mock_aio = MagicMock()
    mock_aio.models = mock_models

    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client


# ---------------------------------------------------------------------------
# Tests: _validate_neg_response
# ---------------------------------------------------------------------------

class TestValidateNegResponse:
    def test_validates_multi_scene_response(self):
        result = _validate_neg_response(FAKE_NEG_RESPONSE)
        assert "scene_01" in result
        assert "scene_02" in result
        assert isinstance(result["scene_01"], NEG)
        assert isinstance(result["scene_02"], NEG)

    def test_scene_01_has_correct_targets(self):
        result = _validate_neg_response(FAKE_NEG_RESPONSE)
        neg = result["scene_01"]
        assert len(neg.targets) == 2
        assert neg.targets[0].id == "t1_identity"
        assert neg.targets[0].entity_id == "fox_01"
        assert neg.targets[0].priority == 0.9

    def test_missing_scene_id_logged(self):
        data = {"scenes": [{"neg": {"targets": []}}]}
        result = _validate_neg_response(data)
        assert len(result) == 0

    def test_expected_scene_ids_warning(self):
        """Missing scene IDs are logged but don't raise."""
        result = _validate_neg_response(
            FAKE_NEG_RESPONSE,
            expected_scene_ids=["scene_01", "scene_02", "scene_03"],
        )
        assert "scene_01" in result
        assert "scene_02" in result
        assert "scene_03" not in result

    def test_empty_scenes_list(self):
        result = _validate_neg_response({"scenes": []})
        assert result == {}

    def test_missing_scenes_key_returns_empty(self):
        """When 'scenes' key is missing, returns empty dict."""
        result = _validate_neg_response({"not_scenes": []})
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: generate_neg_for_plot
# ---------------------------------------------------------------------------

class TestGenerateNegForPlot:
    def test_generates_negs_for_all_scenes(self):
        mock_client = _make_mock_client(FAKE_NEG_RESPONSE)

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_neg_for_plot(
                    api_key="fake-key",
                    plot_scenes=FAKE_PLOT_SCENES,
                )
            )

        assert "scene_01" in result
        assert "scene_02" in result
        assert isinstance(result["scene_01"], NEG)
        assert len(result["scene_01"].targets) == 2

    def test_uses_correct_model(self):
        mock_client = _make_mock_client(FAKE_NEG_RESPONSE)

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client) as mock_cls:
            asyncio.get_event_loop().run_until_complete(
                generate_neg_for_plot(
                    api_key="test-key",
                    plot_scenes=FAKE_PLOT_SCENES,
                )
            )

        mock_cls.assert_called_once_with(api_key="test-key")
        call_args = mock_client.aio.models.generate_content.call_args
        assert call_args.kwargs["model"] == NEG_MODEL_ID

    def test_uses_neg_system_prompt(self):
        mock_client = _make_mock_client(FAKE_NEG_RESPONSE)

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_neg_for_plot(
                    api_key="fake-key",
                    plot_scenes=FAKE_PLOT_SCENES,
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        config = call_args.kwargs["config"]
        assert "assessment designer" in config.system_instruction.lower()

    def test_includes_skill_objectives_in_prompt(self):
        mock_client = _make_mock_client(FAKE_NEG_RESPONSE)

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_neg_for_plot(
                    api_key="fake-key",
                    plot_scenes=FAKE_PLOT_SCENES,
                    skill_objectives=["quantity", "temporal_sequences"],
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        user_prompt = call_args.kwargs["contents"]
        assert "quantity" in user_prompt
        assert "temporal_sequences" in user_prompt

    def test_default_skill_objectives(self):
        mock_client = _make_mock_client(FAKE_NEG_RESPONSE)

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_neg_for_plot(
                    api_key="fake-key",
                    plot_scenes=FAKE_PLOT_SCENES,
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        user_prompt = call_args.kwargs["contents"]
        assert "descriptive_adjectives" in user_prompt
        assert "spatial_prepositions" in user_prompt
        assert "action_verbs" in user_prompt

    def test_retries_on_failure(self):
        """Should retry and succeed on second attempt."""
        good_response = MagicMock()
        good_response.text = json.dumps(FAKE_NEG_RESPONSE)

        call_count = {"n": 0}

        async def fail_then_succeed(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Temporary failure")
            return good_response

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(side_effect=fail_then_succeed)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_neg_for_plot(
                    api_key="fake-key",
                    plot_scenes=FAKE_PLOT_SCENES,
                )
            )

        assert "scene_01" in result
        assert call_count["n"] == 2  # failed once, succeeded on retry

    def test_raises_after_max_retries(self):
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(
            side_effect=RuntimeError("Persistent failure")
        )
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Persistent failure"):
                asyncio.get_event_loop().run_until_complete(
                    generate_neg_for_plot(
                        api_key="fake-key",
                        plot_scenes=FAKE_PLOT_SCENES,
                    )
                )


# ---------------------------------------------------------------------------
# Tests: update_neg_live
# ---------------------------------------------------------------------------

class TestUpdateNegLive:
    def _make_remaining_negs(self):
        result = _validate_neg_response(FAKE_NEG_RESPONSE)
        # Only scene_02 remains (scene_01 already played)
        return {"scene_02": result["scene_02"]}

    def test_updates_negs_for_remaining_scenes(self):
        mock_client = _make_mock_client(FAKE_UPDATE_RESPONSE)
        remaining = self._make_remaining_negs()
        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 8, "SPATIAL": 2},
            total_utterances=15,
            scenes_completed=1,
        )

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                update_neg_live(
                    api_key="fake-key",
                    remaining_negs=remaining,
                    student_profile=profile,
                    completed_scene_ids=["scene_01"],
                )
            )

        assert "scene_02" in result
        neg = result["scene_02"]
        assert isinstance(neg, NEG)
        # Priority should be increased (1.0 in update response)
        assert neg.targets[0].priority == 1.0
        # Tolerance should be decreased (0.2 in update response)
        assert neg.targets[0].tolerance == 0.2
        # New target added
        assert len(neg.targets) == 2
        assert neg.targets[1].id == "t2_color"

    def test_uses_correct_model(self):
        mock_client = _make_mock_client(FAKE_UPDATE_RESPONSE)
        remaining = self._make_remaining_negs()
        profile = StudentProfile()

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                update_neg_live(
                    api_key="test-key",
                    remaining_negs=remaining,
                    student_profile=profile,
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        assert call_args.kwargs["model"] == NEG_MODEL_ID

    def test_uses_update_system_prompt(self):
        mock_client = _make_mock_client(FAKE_UPDATE_RESPONSE)
        remaining = self._make_remaining_negs()
        profile = StudentProfile()

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                update_neg_live(
                    api_key="fake-key",
                    remaining_negs=remaining,
                    student_profile=profile,
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        config = call_args.kwargs["config"]
        assert "adaptive assessment tuner" in config.system_instruction.lower()

    def test_returns_original_on_all_failures(self):
        """On persistent failure, returns original NEGs unchanged."""
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(
            side_effect=RuntimeError("API down")
        )
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        remaining = self._make_remaining_negs()
        original_priority = remaining["scene_02"].targets[0].priority
        profile = StudentProfile()

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                update_neg_live(
                    api_key="fake-key",
                    remaining_negs=remaining,
                    student_profile=profile,
                )
            )

        # Should return original NEGs unchanged
        assert "scene_02" in result
        assert result["scene_02"].targets[0].priority == original_priority

    def test_includes_completed_scenes_in_prompt(self):
        mock_client = _make_mock_client(FAKE_UPDATE_RESPONSE)
        remaining = self._make_remaining_negs()
        profile = StudentProfile()

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                update_neg_live(
                    api_key="fake-key",
                    remaining_negs=remaining,
                    student_profile=profile,
                    completed_scene_ids=["scene_01"],
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        user_prompt = call_args.kwargs["contents"]
        assert "scene_01" in user_prompt

    def test_preserves_scene_ids(self):
        """Updated NEGs must have the same scene_ids as input."""
        mock_client = _make_mock_client(FAKE_UPDATE_RESPONSE)
        remaining = self._make_remaining_negs()
        profile = StudentProfile()

        with patch("src.generation.neg_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                update_neg_live(
                    api_key="fake-key",
                    remaining_negs=remaining,
                    student_profile=profile,
                )
            )

        assert set(result.keys()) == set(remaining.keys())


# ---------------------------------------------------------------------------
# Tests: prompt content
# ---------------------------------------------------------------------------

class TestNegPrompts:
    def test_system_prompt_mentions_error_types(self):
        assert "SPATIAL" in NEG_SHORT_SYSTEM_PROMPT
        assert "PROPERTY_COLOR" in NEG_SHORT_SYSTEM_PROMPT
        assert "QUANTITY" in NEG_SHORT_SYSTEM_PROMPT
        assert "IDENTITY" in NEG_SHORT_SYSTEM_PROMPT

    def test_system_prompt_has_skill_coverage(self):
        assert "skill_coverage_check" in NEG_SHORT_SYSTEM_PROMPT
        assert "descriptive_adjectives" in NEG_SHORT_SYSTEM_PROMPT
        assert "spatial_prepositions" in NEG_SHORT_SYSTEM_PROMPT

    def test_user_prompt_template_has_placeholders(self):
        assert "{plot_json}" in NEG_SHORT_USER_PROMPT_TEMPLATE
        assert "{skill_objectives}" in NEG_SHORT_USER_PROMPT_TEMPLATE

    def test_update_system_prompt_mentions_adaptation(self):
        assert "overrepresented" in NEG_UPDATE_SYSTEM_PROMPT.lower()
        assert "decreasing" in NEG_UPDATE_SYSTEM_PROMPT.lower()
        assert "difficult entities" in NEG_UPDATE_SYSTEM_PROMPT.lower()

    def test_update_user_prompt_template_has_placeholders(self):
        assert "{remaining_negs_json}" in NEG_UPDATE_USER_PROMPT_TEMPLATE
        assert "{student_profile}" in NEG_UPDATE_USER_PROMPT_TEMPLATE
        assert "{completed_scenes}" in NEG_UPDATE_USER_PROMPT_TEMPLATE

    def test_model_id_is_pro(self):
        assert NEG_MODEL_ID == "gemini-3.1-pro-preview"
