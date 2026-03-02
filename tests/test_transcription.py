"""Tests for transcription + discrepancy detection."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.neg import NEG, NarrativeTarget, TargetComponents
from src.models.student_profile import Discrepancy, StudentProfile
from src.narration.transcription import (
    TranscriptionResult,
    ProfileUpdates,
    _build_user_prompt,
    _extract_json,
    _parse_discrepancies,
    _parse_profile_updates,
    _validate_transcription_response,
    transcribe_and_detect,
)
from src.generation.prompts.transcription_prompt import (
    TRANSCRIPTION_SYSTEM_PROMPT,
    TRANSCRIPTION_USER_PROMPT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_neg() -> NEG:
    """Build a realistic NEG for testing."""
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
        min_coverage=0.7,
        skill_coverage_check="PASS",
    )


FAKE_LLM_RESPONSE = {
    "transcription": "um there is a bunny next to a rock",
    "discrepancies": [
        {
            "type": "PROPERTY_COLOR",
            "entity_id": "rabbit_01",
            "sub_entity": "rabbit_01.body",
            "details": "Child said 'bunny' without color descriptor 'brown'",
            "severity": 0.5,
        },
        {
            "type": "PROPERTY_SIZE",
            "entity_id": "rabbit_01",
            "sub_entity": "rabbit_01.body",
            "details": "Child omitted size descriptor 'small'",
            "severity": 0.3,
        },
        {
            "type": "ACTION",
            "entity_id": "rabbit_01",
            "sub_entity": "rabbit_01.legs",
            "details": "Child said 'is' instead of 'hopping'",
            "severity": 0.6,
        },
        {
            "type": "ACTION",
            "entity_id": "rock_01",
            "sub_entity": "rock_01",
            "details": "Spurious: rock has no action (should be filtered)",
            "severity": 0.4,
        },
        {
            "type": "QUANTITY",
            "entity_id": "rabbit_01",
            "sub_entity": "rabbit_01",
            "details": "Spurious: rabbit is unique (should be filtered)",
            "severity": 0.2,
        },
    ],
    "scene_progress": 0.35,
    "satisfied_targets": ["t1_identity"],
    "updated_history": ["um there is a bunny next to a rock"],
    "profile_updates": {
        "errors_this_scene": {"PROPERTY_COLOR": 1, "PROPERTY_SIZE": 1, "ACTION": 1},
        "patterns": "Child omits descriptive adjectives and uses generic verbs",
    },
    "voice_guidance": "Can you describe the color of the bunny?",
}

FAKE_AUDIO = b"\x00\x01\x02\x03"  # Dummy audio bytes


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
# Tests: prompt content
# ---------------------------------------------------------------------------

class TestPromptContent:
    def test_system_prompt_documents_error_taxonomy(self):
        for error_type in [
            "PROPERTY_COLOR", "PROPERTY_SIZE", "PROPERTY_WEIGHT",
            "PROPERTY_TEMPERATURE", "PROPERTY_STATE",
            "SPATIAL", "IDENTITY", "QUANTITY", "ACTION", "MANNER",
            "TEMPORAL", "RELATIONAL", "EXISTENCE", "REDUNDANCY", "OMISSION",
        ]:
            assert error_type in TRANSCRIPTION_SYSTEM_PROMPT

    def test_system_prompt_has_examples(self):
        # Should have concrete examples for error types
        assert "orange cat" in TRANSCRIPTION_SYSTEM_PROMPT or "the cat" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "tiny frog" in TRANSCRIPTION_SYSTEM_PROMPT or "the frog" in TRANSCRIPTION_SYSTEM_PROMPT

    def test_system_prompt_documents_severity(self):
        assert "severity" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "0.0" in TRANSCRIPTION_SYSTEM_PROMPT or "0.1" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "1.0" in TRANSCRIPTION_SYSTEM_PROMPT

    def test_system_prompt_documents_json_schema(self):
        assert "transcription" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "discrepancies" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "scene_progress" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "satisfied_targets" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "updated_history" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "profile_updates" in TRANSCRIPTION_SYSTEM_PROMPT

    def test_system_prompt_explains_neg(self):
        assert "NEG" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "Narrative Expectation Graph" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "targets" in TRANSCRIPTION_SYSTEM_PROMPT

    def test_system_prompt_age_appropriate(self):
        assert "7-11" in TRANSCRIPTION_SYSTEM_PROMPT
        assert "AGE-APPROPRIATE" in TRANSCRIPTION_SYSTEM_PROMPT

    def test_user_prompt_has_placeholders(self):
        assert "{neg_json}" in TRANSCRIPTION_USER_PROMPT
        assert "{narration_history}" in TRANSCRIPTION_USER_PROMPT
        assert "{student_profile}" in TRANSCRIPTION_USER_PROMPT
        assert "{narrative_text}" in TRANSCRIPTION_USER_PROMPT


# ---------------------------------------------------------------------------
# Tests: _build_user_prompt
# ---------------------------------------------------------------------------

class TestBuildUserPrompt:
    def test_includes_neg_json(self):
        neg = _make_neg()
        prompt = _build_user_prompt(neg, [], None)
        assert "rabbit_01" in prompt
        assert "rock_01" in prompt
        assert "t1_identity" in prompt

    def test_includes_narration_history(self):
        neg = _make_neg()
        history = ["the rabbit is brown", "it hops on the rock"]
        prompt = _build_user_prompt(neg, history, None)
        assert "the rabbit is brown" in prompt
        assert "it hops on the rock" in prompt

    def test_empty_history(self):
        neg = _make_neg()
        prompt = _build_user_prompt(neg, [], None)
        assert "first" in prompt.lower() or "no previous" in prompt.lower()

    def test_includes_student_profile(self):
        neg = _make_neg()
        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 5},
            total_utterances=10,
            scenes_completed=2,
        )
        prompt = _build_user_prompt(neg, [], profile)
        assert "PROPERTY_COLOR" in prompt
        assert "Utterances" in prompt

    def test_no_profile(self):
        neg = _make_neg()
        prompt = _build_user_prompt(neg, [], None)
        assert "no profile" in prompt.lower() or "first interaction" in prompt.lower()

    def test_includes_narrative_text(self):
        neg = _make_neg()
        prompt = _build_user_prompt(neg, [], None, narrative_text="The brave rabbit hopped onto the mossy rock.")
        assert "brave rabbit" in prompt
        assert "mossy rock" in prompt

    def test_no_narrative_text(self):
        neg = _make_neg()
        prompt = _build_user_prompt(neg, [], None, narrative_text="")
        assert "no narrative" in prompt.lower() or "(no narrative" in prompt.lower()


# ---------------------------------------------------------------------------
# Tests: _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json(self):
        data = _extract_json('{"a": 1}')
        assert data == {"a": 1}

    def test_fenced_json(self):
        data = _extract_json('```json\n{"a": 1}\n```')
        assert data == {"a": 1}

    def test_invalid_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not json")


# ---------------------------------------------------------------------------
# Tests: _parse_discrepancies
# ---------------------------------------------------------------------------

class TestParseDiscrepancies:
    def test_parses_valid_list(self):
        raw = [
            {"type": "PROPERTY_COLOR", "entity_id": "cat_01", "severity": 0.7},
            {"type": "SPATIAL", "entity_id": "cat_01", "sub_entity": "cat_01.body"},
        ]
        result = _parse_discrepancies(raw)
        assert len(result) == 2
        assert result[0].type == "PROPERTY_COLOR"
        assert result[0].severity == 0.7
        assert result[1].sub_entity == "cat_01.body"

    def test_defaults_for_missing_fields(self):
        raw = [{"type": "SPATIAL"}]
        result = _parse_discrepancies(raw)
        assert result[0].entity_id == ""
        assert result[0].sub_entity == ""
        assert result[0].severity == 0.5

    def test_empty_list(self):
        assert _parse_discrepancies([]) == []

    def test_missing_type_defaults_to_omission(self):
        raw = [{"entity_id": "cat_01"}]
        result = _parse_discrepancies(raw)
        assert result[0].type == "OMISSION"


# ---------------------------------------------------------------------------
# Tests: _parse_profile_updates
# ---------------------------------------------------------------------------

class TestParseProfileUpdates:
    def test_valid_dict(self):
        raw = {
            "errors_this_scene": {"PROPERTY_COLOR": 2},
            "patterns": "omits colors",
        }
        result = _parse_profile_updates(raw)
        assert result.errors_this_scene == {"PROPERTY_COLOR": 2}
        assert result.patterns == "omits colors"

    def test_none_returns_default(self):
        result = _parse_profile_updates(None)
        assert result.errors_this_scene == {}
        assert result.patterns == ""

    def test_non_dict_returns_default(self):
        result = _parse_profile_updates("invalid")
        assert result.errors_this_scene == {}


# ---------------------------------------------------------------------------
# Tests: _validate_transcription_response
# ---------------------------------------------------------------------------

class TestValidateResponse:
    def test_valid_response(self):
        result = _validate_transcription_response(FAKE_LLM_RESPONSE)
        assert isinstance(result, TranscriptionResult)
        assert result.transcription == "um there is a bunny next to a rock"
        assert len(result.discrepancies) == 5
        assert result.scene_progress == 0.35
        assert "t1_identity" in result.satisfied_targets
        assert len(result.updated_history) == 1
        assert result.profile_updates.patterns != ""

    def test_clamps_scene_progress(self):
        data = dict(FAKE_LLM_RESPONSE)
        data["scene_progress"] = 1.5
        result = _validate_transcription_response(data)
        assert result.scene_progress == 1.0

        data["scene_progress"] = -0.5
        result = _validate_transcription_response(data)
        assert result.scene_progress == 0.0

    def test_handles_missing_fields(self):
        result = _validate_transcription_response({})
        assert result.transcription == ""
        assert result.discrepancies == []
        assert result.scene_progress == 0.0
        assert result.satisfied_targets == []
        assert result.updated_history == []

    def test_handles_non_list_discrepancies(self):
        result = _validate_transcription_response({"discrepancies": "oops"})
        assert result.discrepancies == []

    def test_handles_non_numeric_progress(self):
        result = _validate_transcription_response({"scene_progress": "half"})
        assert result.scene_progress == 0.0

    def test_parses_voice_guidance(self):
        result = _validate_transcription_response(FAKE_LLM_RESPONSE)
        assert result.voice_guidance == "Can you describe the color of the bunny?"

    def test_voice_guidance_defaults_to_empty(self):
        result = _validate_transcription_response({})
        assert result.voice_guidance == ""

    def test_voice_guidance_non_string_defaults_to_empty(self):
        result = _validate_transcription_response({"voice_guidance": 42})
        assert result.voice_guidance == ""


# ---------------------------------------------------------------------------
# Tests: TranscriptionResult model
# ---------------------------------------------------------------------------

class TestTranscriptionResultModel:
    def test_json_roundtrip(self):
        result = TranscriptionResult(
            transcription="the brown rabbit hops",
            discrepancies=[
                Discrepancy(type="PROPERTY_SIZE", entity_id="rabbit_01", severity=0.3),
            ],
            scene_progress=0.6,
            satisfied_targets=["t1_identity"],
            updated_history=["the brown rabbit hops"],
            profile_updates=ProfileUpdates(
                errors_this_scene={"PROPERTY_SIZE": 1},
                patterns="omits size",
            ),
            voice_guidance="What color is the rabbit?",
        )
        data = result.model_dump()
        restored = TranscriptionResult.model_validate(data)
        assert restored.transcription == result.transcription
        assert len(restored.discrepancies) == 1
        assert restored.scene_progress == 0.6
        assert restored.profile_updates.patterns == "omits size"
        assert restored.voice_guidance == "What color is the rabbit?"


# ---------------------------------------------------------------------------
# Tests: transcribe_and_detect (mocked Gemini)
# ---------------------------------------------------------------------------

class TestTranscribeAndDetect:
    def test_parses_response_and_filters_exclusions(self):
        """Core test: mock Gemini, verify parsing — all discrepancies pass through."""
        neg = _make_neg()
        mock_client = _make_mock_client(FAKE_LLM_RESPONSE)

        with patch("src.narration.transcription.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                transcribe_and_detect(
                    api_key="fake-key",
                    audio_bytes=FAKE_AUDIO,
                    neg=neg,
                    narration_history=[],
                    student_profile=None,
                )
            )

        assert isinstance(result, TranscriptionResult)
        assert result.transcription == "um there is a bunny next to a rock"

        # All 5 discrepancies from the LLM response pass through
        # (no error_exclusions filtering)
        assert len(result.discrepancies) == 5

        remaining_types = [(d.entity_id, d.type) for d in result.discrepancies]
        assert ("rabbit_01", "PROPERTY_COLOR") in remaining_types
        assert ("rabbit_01", "PROPERTY_SIZE") in remaining_types
        assert ("rabbit_01", "ACTION") in remaining_types
        assert ("rock_01", "ACTION") in remaining_types
        assert ("rabbit_01", "QUANTITY") in remaining_types

        # Other fields
        assert result.scene_progress == 0.35
        assert "t1_identity" in result.satisfied_targets

    def test_second_call_with_history(self):
        """Verify narration history is passed correctly."""
        neg = _make_neg()
        mock_client = _make_mock_client(FAKE_LLM_RESPONSE)

        with patch("src.narration.transcription.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                transcribe_and_detect(
                    api_key="fake-key",
                    audio_bytes=FAKE_AUDIO,
                    neg=neg,
                    narration_history=["the rabbit is brown", "it hops"],
                    student_profile=None,
                )
            )

        # Verify the prompt sent to Gemini includes the history
        call_args = mock_client.aio.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args.args[0]
        # contents is a list [audio_part, text_part]
        # The text part should contain the history
        text_content = str(contents)
        assert "the rabbit is brown" in text_content
        assert "it hops" in text_content

    def test_with_student_profile(self):
        """Verify student profile is included in the prompt."""
        neg = _make_neg()
        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 8, "SPATIAL": 2},
            total_utterances=15,
            scenes_completed=3,
        )
        mock_client = _make_mock_client(FAKE_LLM_RESPONSE)

        with patch("src.narration.transcription.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                transcribe_and_detect(
                    api_key="fake-key",
                    audio_bytes=FAKE_AUDIO,
                    neg=neg,
                    narration_history=[],
                    student_profile=profile,
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args.args[0]
        text_content = str(contents)
        assert "PROPERTY_COLOR" in text_content
        assert "15" in text_content  # total_utterances

    def test_gemini_called_with_correct_model_and_low_thinking(self):
        neg = _make_neg()
        mock_client = _make_mock_client(FAKE_LLM_RESPONSE)

        with patch("src.narration.transcription.genai.Client", return_value=mock_client) as mock_cls:
            asyncio.get_event_loop().run_until_complete(
                transcribe_and_detect(
                    api_key="test-key",
                    audio_bytes=FAKE_AUDIO,
                    neg=neg,
                )
            )

        mock_cls.assert_called_once_with(api_key="test-key")

        call_args = mock_client.aio.models.generate_content.call_args
        assert call_args.kwargs["model"] == "gemini-3-flash-preview"

        config = call_args.kwargs["config"]
        # thinking_budget should be low (256)
        assert config.thinking_config.thinking_budget == 256

    def test_multimodal_content_structure(self):
        """Verify audio and text are sent as separate parts."""
        neg = _make_neg()
        mock_client = _make_mock_client(FAKE_LLM_RESPONSE)

        with patch("src.narration.transcription.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                transcribe_and_detect(
                    api_key="fake-key",
                    audio_bytes=FAKE_AUDIO,
                    neg=neg,
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args.args[0]

        # Should be a list with 2 parts: audio + text
        assert isinstance(contents, list)
        assert len(contents) == 2

    def test_no_discrepancies_response(self):
        """Perfect narration — no discrepancies returned."""
        neg = _make_neg()
        clean_response = {
            "transcription": "the small brown fluffy rabbit hops quickly beside the large grey mossy rock",
            "discrepancies": [],
            "scene_progress": 0.95,
            "satisfied_targets": ["t1_identity", "t2_identity"],
            "updated_history": [
                "the small brown fluffy rabbit hops quickly beside the large grey mossy rock"
            ],
            "profile_updates": {
                "errors_this_scene": {},
                "patterns": "Excellent narration with rich descriptors",
            },
        }
        mock_client = _make_mock_client(clean_response)

        with patch("src.narration.transcription.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                transcribe_and_detect(
                    api_key="fake-key",
                    audio_bytes=FAKE_AUDIO,
                    neg=neg,
                )
            )

        assert result.transcription != ""
        assert len(result.discrepancies) == 0
        assert result.scene_progress == 0.95
        assert len(result.satisfied_targets) == 2

    def test_narrative_text_passed_to_prompt(self):
        """Verify narrative_text is included in the prompt sent to Gemini."""
        neg = _make_neg()
        mock_client = _make_mock_client(FAKE_LLM_RESPONSE)

        with patch("src.narration.transcription.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                transcribe_and_detect(
                    api_key="fake-key",
                    audio_bytes=FAKE_AUDIO,
                    neg=neg,
                    narration_history=[],
                    narrative_text="The brave rabbit hopped onto the mossy rock.",
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        contents = call_args.kwargs.get("contents") or call_args.args[0]
        text_content = str(contents)
        assert "brave rabbit" in text_content
        assert "mossy rock" in text_content

    def test_voice_guidance_parsed_in_response(self):
        """Verify voice_guidance field is parsed from LLM response."""
        neg = _make_neg()
        mock_client = _make_mock_client(FAKE_LLM_RESPONSE)

        with patch("src.narration.transcription.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                transcribe_and_detect(
                    api_key="fake-key",
                    audio_bytes=FAKE_AUDIO,
                    neg=neg,
                )
            )

        assert result.voice_guidance == "Can you describe the color of the bunny?"

    def test_markdown_fenced_response(self):
        """Gemini wraps JSON in markdown fences."""
        neg = _make_neg()
        fenced = "```json\n" + json.dumps(FAKE_LLM_RESPONSE) + "\n```"

        mock_response = MagicMock()
        mock_response.text = fenced
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.narration.transcription.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                transcribe_and_detect(
                    api_key="fake-key",
                    audio_bytes=FAKE_AUDIO,
                    neg=neg,
                )
            )

        assert result.transcription == "um there is a bunny next to a rock"
        # All discrepancies pass through (no exclusion filtering)
        assert len(result.discrepancies) == 5
