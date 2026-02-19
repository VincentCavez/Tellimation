"""Tests for animation_generator and animation_prompt."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.animation_cache import AnimationCache, CachedAnimation
from src.generation.animation_generator import (
    _extract_json,
    _format_scene_context,
    _validate_animation_response,
    generate_animation,
)
from src.generation.prompts.animation_prompt import (
    ANIMATION_SYSTEM_PROMPT,
    ANIMATION_USER_PROMPT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_ANIMATION_RESPONSE = {
    "animation_type": "color_pop",
    "code": (
        "function animate(buf, PW, PH, t) {\n"
        "  const prefix = 'rabbit_01.body';\n"
        "  const glow = 0.7 + 0.3 * Math.sin(t * Math.PI * 6);\n"
        "  for (let i = 0; i < buf.length; i++) {\n"
        "    if (buf[i].e === prefix || buf[i].e.startsWith(prefix + '.')) {\n"
        "      buf[i].r = Math.min(255, Math.round(buf[i]._r * (1 + glow * 0.4)));\n"
        "      buf[i].g = Math.min(255, Math.round(buf[i]._g * (1 + glow * 0.4)));\n"
        "      buf[i].b = Math.min(255, Math.round(buf[i]._b * (1 + glow * 0.4)));\n"
        "    } else if (buf[i].e !== 'sky' && buf[i].e !== 'ground') {\n"
        "      const L = Math.round(buf[i]._r * 0.299 + buf[i]._g * 0.587 + buf[i]._b * 0.114);\n"
        "      buf[i].r = Math.round(L * 0.3);\n"
        "      buf[i].g = Math.round(L * 0.3);\n"
        "      buf[i].b = Math.round(L * 0.3);\n"
        "    }\n"
        "  }\n"
        "}"
    ),
    "duration_ms": 1500,
}

SCENE_CONTEXT = {
    "entities": [
        {
            "id": "rabbit_01",
            "type": "rabbit",
            "properties": {"color": "brown", "size": "small", "texture": "fluffy"},
            "position": {"x": 90, "y": 130},
        },
        {
            "id": "rock_01",
            "type": "rock",
            "properties": {"color": "grey", "size": "large"},
            "position": {"x": 160, "y": 140},
        },
    ],
    "relations": [
        {"entity_a": "rabbit_01", "entity_b": "rock_01", "preposition": "beside"}
    ],
    "actions": [
        {"entity_id": "rabbit_01", "verb": "hop", "manner": "quickly"}
    ],
}

ENTITY_BOUNDS = {"x": 80, "y": 120, "width": 20, "height": 25}


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
    def test_system_prompt_documents_function_signature(self):
        assert "function animate(buf, PW, PH, t)" in ANIMATION_SYSTEM_PROMPT

    def test_system_prompt_documents_pixel_fields(self):
        assert "_r" in ANIMATION_SYSTEM_PROMPT
        assert "_g" in ANIMATION_SYSTEM_PROMPT
        assert "_b" in ANIMATION_SYSTEM_PROMPT
        assert "buf[i].e" in ANIMATION_SYSTEM_PROMPT

    def test_system_prompt_documents_prefix_matching(self):
        assert "startsWith" in ANIMATION_SYSTEM_PROMPT

    def test_system_prompt_contains_all_error_types(self):
        for error_type in [
            "SPATIAL", "PROPERTY_COLOR", "PROPERTY_SIZE",
            "PROPERTY_WEIGHT", "PROPERTY_TEMPERATURE",
            "TEMPORAL", "IDENTITY", "QUANTITY", "ACTION",
            "MANNER", "OMISSION",
        ]:
            assert error_type in ANIMATION_SYSTEM_PROMPT

    def test_system_prompt_contains_animation_concepts(self):
        """The prompt must describe key animation concepts for each error type."""
        for concept in [
            "desaturate",       # PROPERTY_COLOR response
            "inflate",          # PROPERTY_SIZE response
            "frost",            # PROPERTY_TEMPERATURE response
            "afterimage",       # TEMPORAL response
            "pulse",            # QUANTITY response
            "motion lines",     # ACTION response
            "ghostly outline",  # EXISTENCE response
        ]:
            assert concept.lower() in ANIMATION_SYSTEM_PROMPT.lower(), (
                f"Missing concept: {concept}"
            )

    def test_system_prompt_contains_examples(self):
        # At least the 3 required examples
        assert "color_pop" in ANIMATION_SYSTEM_PROMPT
        assert "vibrating_pulse" in ANIMATION_SYSTEM_PROMPT or "shake" in ANIMATION_SYSTEM_PROMPT.lower()
        assert "settle" in ANIMATION_SYSTEM_PROMPT

    def test_system_prompt_requests_duration(self):
        assert "duration_ms" in ANIMATION_SYSTEM_PROMPT

    def test_user_prompt_has_placeholders(self):
        assert "{error_type}" in ANIMATION_USER_PROMPT
        assert "{entity_id}" in ANIMATION_USER_PROMPT
        assert "{sub_entity}" in ANIMATION_USER_PROMPT
        assert "{bbox_x}" in ANIMATION_USER_PROMPT
        assert "{scene_context}" in ANIMATION_USER_PROMPT
        assert "{discrepancy_details}" in ANIMATION_USER_PROMPT
        assert "{student_profile_context}" in ANIMATION_USER_PROMPT
        assert "{entity_details}" in ANIMATION_USER_PROMPT


# ---------------------------------------------------------------------------
# Tests: format_scene_context
# ---------------------------------------------------------------------------

class TestFormatSceneContext:
    def test_empty_context(self):
        result = _format_scene_context({})
        assert "no scene context" in result or "empty" in result

    def test_none_context(self):
        result = _format_scene_context(None)
        assert "no scene context" in result

    def test_with_entities(self):
        result = _format_scene_context(SCENE_CONTEXT)
        assert "rabbit_01" in result
        assert "rock_01" in result
        assert "rabbit" in result

    def test_with_relations(self):
        result = _format_scene_context(SCENE_CONTEXT)
        assert "beside" in result

    def test_with_actions(self):
        result = _format_scene_context(SCENE_CONTEXT)
        assert "hop" in result
        assert "quickly" in result


# ---------------------------------------------------------------------------
# Tests: extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json(self):
        data = _extract_json('{"a": 1}')
        assert data == {"a": 1}

    def test_fenced_json(self):
        data = _extract_json('```json\n{"a": 1}\n```')
        assert data == {"a": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not json")


# ---------------------------------------------------------------------------
# Tests: validate_animation_response
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_response(self):
        anim = _validate_animation_response(FAKE_ANIMATION_RESPONSE)
        assert isinstance(anim, CachedAnimation)
        assert "function animate" in anim.code
        assert anim.duration_ms == 1500
        assert anim.generated_for == "color_pop"

    def test_missing_code_raises(self):
        with pytest.raises(ValueError, match="code"):
            _validate_animation_response({"duration_ms": 1000})

    def test_empty_code_raises(self):
        with pytest.raises(ValueError, match="code"):
            _validate_animation_response({"code": "", "duration_ms": 1000})

    def test_default_duration(self):
        anim = _validate_animation_response({
            "code": "function animate(buf, PW, PH, t) {}",
        })
        assert anim.duration_ms == 1200

    def test_non_integer_duration_coerced(self):
        anim = _validate_animation_response({
            "code": "function animate(buf, PW, PH, t) {}",
            "duration_ms": 1500.5,
        })
        assert anim.duration_ms == 1500


# ---------------------------------------------------------------------------
# Tests: generate_animation (mocked Gemini)
# ---------------------------------------------------------------------------

class TestGenerateAnimation:
    def test_generates_animation_and_returns_cached(self):
        """Generate animation for PROPERTY_COLOR on rabbit_01.body.
        Verify code is non-empty string, cache is updated, second call
        returns cached result without API call.
        """
        cache = AnimationCache()
        mock_client = _make_mock_client(FAKE_ANIMATION_RESPONSE)

        with patch("src.generation.animation_generator.genai.Client", return_value=mock_client):
            # First call — should hit the API
            result = asyncio.get_event_loop().run_until_complete(
                generate_animation(
                    api_key="fake-key",
                    error_type="PROPERTY_COLOR",
                    entity_id="rabbit_01",
                    sub_entity="rabbit_01.body",
                    entity_bounds=ENTITY_BOUNDS,
                    scene_context=SCENE_CONTEXT,
                    animation_cache=cache,
                )
            )

        # Result should be a CachedAnimation with non-empty code
        assert isinstance(result, CachedAnimation)
        assert isinstance(result.code, str)
        assert len(result.code) > 0
        assert "function animate" in result.code
        assert result.duration_ms > 0
        assert result.generated_for == "rabbit_01.body"

        # Cache should now contain the animation
        assert cache.has("rabbit_01.body", "PROPERTY_COLOR")

        # Second call — should return from cache, NO API call
        with patch("src.generation.animation_generator.genai.Client") as mock_cls:
            result2 = asyncio.get_event_loop().run_until_complete(
                generate_animation(
                    api_key="fake-key",
                    error_type="PROPERTY_COLOR",
                    entity_id="rabbit_01",
                    sub_entity="rabbit_01.body",
                    entity_bounds=ENTITY_BOUNDS,
                    scene_context=SCENE_CONTEXT,
                    animation_cache=cache,
                )
            )
            # Client should NOT have been instantiated
            mock_cls.assert_not_called()

        # Same result
        assert result2.code == result.code
        assert result2.duration_ms == result.duration_ms

    def test_cache_prefix_match_prevents_api_call(self):
        """If cache has an entry for rabbit_01.body and we request
        rabbit_01.body.fur, the prefix match should return cached."""
        cache = AnimationCache()
        existing = CachedAnimation(
            code="function animate(buf, PW, PH, t) { /* cached */ }",
            duration_ms=1000,
            generated_for="rabbit_01.body",
        )
        cache.store("rabbit_01.body", "PROPERTY_COLOR", existing)

        with patch("src.generation.animation_generator.genai.Client") as mock_cls:
            result = asyncio.get_event_loop().run_until_complete(
                generate_animation(
                    api_key="fake-key",
                    error_type="PROPERTY_COLOR",
                    entity_id="rabbit_01",
                    sub_entity="rabbit_01.body.fur",
                    entity_bounds=ENTITY_BOUNDS,
                    scene_context=SCENE_CONTEXT,
                    animation_cache=cache,
                )
            )
            mock_cls.assert_not_called()

        assert result.code == existing.code

    def test_different_error_type_triggers_new_api_call(self):
        """Same entity but different error type should generate a new animation."""
        cache = AnimationCache()
        existing = CachedAnimation(
            code="function animate(buf, PW, PH, t) { /* color */ }",
            duration_ms=1000,
            generated_for="rabbit_01.body",
        )
        cache.store("rabbit_01.body", "PROPERTY_COLOR", existing)

        spatial_response = {
            "animation_type": "settle",
            "code": "function animate(buf, PW, PH, t) { /* settle */ }",
            "duration_ms": 1200,
        }
        mock_client = _make_mock_client(spatial_response)

        with patch("src.generation.animation_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_animation(
                    api_key="fake-key",
                    error_type="SPATIAL",
                    entity_id="rabbit_01",
                    sub_entity="rabbit_01.body",
                    entity_bounds=ENTITY_BOUNDS,
                    scene_context=SCENE_CONTEXT,
                    animation_cache=cache,
                )
            )

        assert "settle" in result.code
        assert cache.has("rabbit_01.body", "SPATIAL")
        assert cache.has("rabbit_01.body", "PROPERTY_COLOR")

    def test_gemini_called_with_correct_model(self):
        cache = AnimationCache()
        mock_client = _make_mock_client(FAKE_ANIMATION_RESPONSE)

        with patch("src.generation.animation_generator.genai.Client", return_value=mock_client) as mock_cls:
            asyncio.get_event_loop().run_until_complete(
                generate_animation(
                    api_key="test-key",
                    error_type="PROPERTY_COLOR",
                    entity_id="rabbit_01",
                    sub_entity="rabbit_01.body",
                    entity_bounds=ENTITY_BOUNDS,
                    scene_context=SCENE_CONTEXT,
                    animation_cache=cache,
                )
            )

        mock_cls.assert_called_once_with(api_key="test-key")
        call_args = mock_client.aio.models.generate_content.call_args
        assert call_args.kwargs["model"] == "gemini-3-flash-preview"

    def test_prompt_contains_error_and_entity(self):
        """Verify the user prompt sent to Gemini contains the error/entity details."""
        cache = AnimationCache()
        mock_client = _make_mock_client(FAKE_ANIMATION_RESPONSE)

        with patch("src.generation.animation_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_animation(
                    api_key="fake-key",
                    error_type="PROPERTY_COLOR",
                    entity_id="rabbit_01",
                    sub_entity="rabbit_01.body",
                    entity_bounds=ENTITY_BOUNDS,
                    scene_context=SCENE_CONTEXT,
                    animation_cache=cache,
                )
            )

        call_args = mock_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents") or call_args.args[0]
        assert "PROPERTY_COLOR" in prompt
        assert "rabbit_01.body" in prompt
        assert "rabbit_01" in prompt

    def test_response_with_markdown_fences(self):
        """Gemini sometimes wraps JSON in markdown fences."""
        cache = AnimationCache()
        fenced = "```json\n" + json.dumps(FAKE_ANIMATION_RESPONSE) + "\n```"

        mock_response = MagicMock()
        mock_response.text = fenced
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.animation_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_animation(
                    api_key="fake-key",
                    error_type="PROPERTY_COLOR",
                    entity_id="rabbit_01",
                    sub_entity="rabbit_01.body",
                    entity_bounds=ENTITY_BOUNDS,
                    scene_context=SCENE_CONTEXT,
                    animation_cache=cache,
                )
            )

        assert "function animate" in result.code
