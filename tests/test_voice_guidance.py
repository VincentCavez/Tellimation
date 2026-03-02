"""Tests for voice guidance module (TTS + correction + branch narration)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.narration.voice_guidance import (
    TTS_MODEL_ID,
    TTS_VOICE,
    TEXT_MODEL_ID,
    generate_branch_narration,
    generate_correction_text,
    generate_scene_intro,
    text_to_speech,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tts_mock(audio_bytes: bytes = b"\x00\x01\x02\x03"):
    """Create a mock genai.Client returning TTS audio."""
    inline_data = MagicMock()
    inline_data.data = audio_bytes

    part = MagicMock()
    part.inline_data = inline_data

    content = MagicMock()
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content

    mock_response = MagicMock()
    mock_response.candidates = [candidate]

    mock_models = AsyncMock()
    mock_models.generate_content = AsyncMock(return_value=mock_response)

    mock_aio = MagicMock()
    mock_aio.models = mock_models

    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client


def _make_text_mock(text: str):
    """Create a mock genai.Client returning text."""
    mock_response = MagicMock()
    mock_response.text = text

    mock_models = AsyncMock()
    mock_models.generate_content = AsyncMock(return_value=mock_response)

    mock_aio = MagicMock()
    mock_aio.models = mock_models

    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client


# ---------------------------------------------------------------------------
# Tests: text_to_speech
# ---------------------------------------------------------------------------


class TestTextToSpeech:
    def test_returns_audio_bytes(self):
        audio = b"\x00\x01\x02\x03\x04\x05"
        mock_client = _make_tts_mock(audio)

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                text_to_speech("fake-key", "Hello child!")
            )

        assert result == audio

    def test_calls_tts_model(self):
        mock_client = _make_tts_mock()

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                text_to_speech("fake-key", "Hello!")
            )

        call_args = mock_client.aio.models.generate_content.call_args
        assert call_args.kwargs["model"] == TTS_MODEL_ID

    def test_uses_achernar_voice(self):
        mock_client = _make_tts_mock()

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                text_to_speech("fake-key", "Hello!")
            )

        call_args = mock_client.aio.models.generate_content.call_args
        config = call_args.kwargs["config"]
        voice_name = (
            config.speech_config.voice_config
            .prebuilt_voice_config.voice_name
        )
        assert voice_name == TTS_VOICE

    def test_raises_on_empty_response(self):
        mock_response = MagicMock()
        mock_response.candidates = []

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="no audio"):
                asyncio.get_event_loop().run_until_complete(
                    text_to_speech("fake-key", "Hello!")
                )


# ---------------------------------------------------------------------------
# Tests: generate_scene_intro
# ---------------------------------------------------------------------------


class TestGenerateSceneIntro:
    def test_returns_intro_string(self):
        mock_client = _make_text_mock("Let's tell the story of a brave rabbit!")

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_scene_intro(
                    api_key="fake",
                    narrative_text="A brave rabbit explores a magical forest.",
                    manifest={
                        "entities": [
                            {"id": "rabbit_01", "type": "rabbit"},
                            {"id": "tree_01", "type": "tree"},
                        ]
                    },
                )
            )

        assert len(result) > 0
        assert "rabbit" in result.lower()

    def test_prompt_includes_entities(self):
        mock_client = _make_text_mock("intro text")

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_scene_intro(
                    api_key="fake",
                    narrative_text="A fox near a river.",
                    manifest={
                        "entities": [
                            {"id": "fox_01", "type": "fox"},
                        ]
                    },
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs["contents"]
        assert "fox" in prompt
        assert "river" in prompt.lower()

    def test_returns_empty_on_failure(self):
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(side_effect=RuntimeError("fail"))
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_scene_intro("fake", "story", {"entities": []})
            )

        assert result == ""


# ---------------------------------------------------------------------------
# Tests: generate_correction_text
# ---------------------------------------------------------------------------


class TestGenerateCorrectionText:
    def test_returns_correction_string(self):
        mock_client = _make_text_mock("The cat is actually orange!")

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_correction_text(
                    api_key="fake",
                    entity_id="cat_01",
                    error_type="PROPERTY_COLOR",
                    discrepancy_details="Child said cat without color",
                    scene_manifest={"entities": [{"id": "cat_01", "type": "cat", "properties": {"color": "orange"}}]},
                    narrative_text="An orange cat sits on a rock.",
                )
            )

        assert "orange" in result.lower()

    def test_prompt_contains_context(self):
        mock_client = _make_text_mock("correction text")

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_correction_text(
                    api_key="fake",
                    entity_id="cat_01",
                    error_type="PROPERTY_COLOR",
                    discrepancy_details="omitted orange",
                    scene_manifest={"entities": [{"id": "cat_01", "type": "cat", "properties": {"color": "orange"}}]},
                    narrative_text="An orange cat sits on a rock.",
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs["contents"]
        assert "cat_01" in prompt
        assert "PROPERTY_COLOR" in prompt
        assert "orange cat" in prompt.lower()

    def test_returns_empty_on_failure(self):
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(side_effect=RuntimeError("API down"))
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_correction_text(
                    api_key="fake",
                    entity_id="cat_01",
                    error_type="PROPERTY_COLOR",
                    discrepancy_details="omitted orange",
                    scene_manifest={"entities": []},
                    narrative_text="",
                )
            )

        assert result == ""


# ---------------------------------------------------------------------------
# Tests: generate_branch_narration
# ---------------------------------------------------------------------------


class TestGenerateBranchNarration:
    def test_returns_narration_string(self):
        mock_client = _make_text_mock("Amazing! You could follow the rabbit or chase the fox!")

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_branch_narration(
                    api_key="fake",
                    branches=[
                        {"branch_summary": "The rabbit enters a cave"},
                        {"branch_summary": "The fox chases a butterfly"},
                        {"branch_summary": "The owl finds an acorn"},
                    ],
                )
            )

        assert len(result) > 0

    def test_prompt_includes_summaries(self):
        mock_client = _make_text_mock("narration")

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_branch_narration(
                    api_key="fake",
                    branches=[
                        {"branch_summary": "The rabbit enters a cave"},
                        {"branch_summary": "The fox chases a butterfly"},
                    ],
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs["contents"]
        assert "rabbit enters a cave" in prompt
        assert "fox chases a butterfly" in prompt

    def test_returns_empty_on_failure(self):
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(side_effect=RuntimeError("fail"))
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.narration.voice_guidance.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_branch_narration("fake", [])
            )

        assert result == ""
