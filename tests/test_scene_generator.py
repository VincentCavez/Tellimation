"""Tests for scene_generator and scene_prompt."""

import asyncio
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.story_state import ActiveEntity, StoryState
from src.models.student_profile import StudentProfile
from src.generation.scene_generator import (
    _build_continuation_prompt,
    _build_entity_description,
    _build_initial_prompt,
    _build_scene_image_prompt,
    _dechroma_pixel,
    _detect_background_color,
    _extract_entity_sprite,
    _extract_background_sprite,
    _extract_json,
    _expand_rle_mask,
    _is_background_pixel,
    _is_rle_format,
    _generate_manifest,
    _generate_background_image,
    _assemble_sprite_code,
    _compute_entity_positions,
    _build_fallback_mask,
    _is_chroma_background,
    _sanitize_for_isolation,
    _validate_scene_response,
    generate_scene,
    IMAGE_MODEL_ID,
    MODEL_ID,
)
from src.generation.prompts.scene_prompt import (
    CONTINUATION_SCENE_USER_PROMPT,
    INITIAL_SCENE_USER_PROMPT,
    MANIFEST_SYSTEM_PROMPT,
    SCENE_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Fixtures: fake LLM responses
# ---------------------------------------------------------------------------

FAKE_INITIAL_RESPONSE = {
    "narrative_text": "A fluffy orange fox sat beside a tall mossy rock in a sunlit forest clearing.",
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
            {
                "id": "tree_01",
                "type": "tree",
                "properties": {"color": "green", "size": "large"},
                "position": {"x": 220, "y": 100},
                "emotion": None,
                "carried_over": False,
            },
        ],
        "relations": [
            {
                "entity_a": "fox_01",
                "entity_b": "rock_01",
                "type": "spatial",
                "preposition": "beside",
            }
        ],
        "actions": [
            {
                "entity_id": "fox_01",
                "verb": "sit",
                "tense": "present",
                "manner": "quietly",
            }
        ],
    },
    "neg": {
        "targets": [
            {
                "id": "t1_identity",
                "entity_id": "fox_01",
                "components": {
                    "identity": True,
                    "descriptors": ["orange", "fluffy", "small"],
                    "spatial": "beside rock_01",
                    "action": "sitting quietly",
                },
                "priority": 0.9,
                "tolerance": 0.3,
            }
        ],
        "min_coverage": 0.7,
        "skill_coverage_check": "PASS",
    },
    "sprite_code": {
        "fox_01": "const eid='fox_01';\nellip(80,135,10,7,230,140,30,eid+'.body');\ncirc(70,125,6,230,140,30,eid+'.head');\ntri(64,125,66,116,68,123,230,140,30,eid+'.head.ears.left');\ntri(72,123,74,116,76,125,230,140,30,eid+'.head.ears.right');\ncirc(67,124,1,0,0,0,eid+'.head.eyes.left');\ncirc(73,124,1,0,0,0,eid+'.head.eyes.right');\npx(70,127,0,0,0,eid+'.head.nose');\narc(92,130,8,0.5,1.8,230,140,30,eid+'.tail');",
        "rock_01": "const eid='rock_01';\nellip(140,145,14,8,130,130,125,eid+'.body');\nellip(140,142,12,5,140,140,135,eid+'.body.top');\npx(135,140,100,120,80,eid+'.body.moss1');\npx(138,139,100,120,80,eid+'.body.moss2');\npx(142,140,100,120,80,eid+'.body.moss3');\npx(145,141,100,120,80,eid+'.body.moss4');\nline(132,148,148,148,110,110,105,eid+'.shadow');\npx(140,138,160,160,155,eid+'.body.highlight');",
        "tree_01": "const eid='tree_01';\nrect(218,110,5,25,100,70,30,eid+'.trunk');\nrect(219,115,3,5,90,60,25,eid+'.trunk.bark');\ntri(200,115,240,115,220,75,34,120,34,eid+'.foliage');\ntri(205,100,235,100,220,68,28,110,28,eid+'.foliage.top');\ncirc(210,108,2,40,130,40,eid+'.foliage.leaf1');\ncirc(225,95,2,40,130,40,eid+'.foliage.leaf2');\ncirc(215,80,2,30,110,30,eid+'.foliage.leaf3');\npx(220,112,80,50,20,eid+'.trunk.knot');",
    },
    "carried_over_entities": [],
    "background_changed": True,
    "background_description": "A warm sunlit forest clearing with golden dappled light filtering through the canopy, soft mossy ground, and a pale blue sky.",
}

FAKE_CONTINUATION_RESPONSE = {
    "narrative_text": "The fox crept toward a shimmering blue pond, while a tiny green frog watched from a lily pad.",
    "branch_summary": "The fox discovers a hidden pond with a frog",
    "manifest": {
        "scene_id": "scene_02",
        "entities": [
            {
                "id": "fox_01",
                "type": "fox",
                "properties": {"color": "orange", "size": "small", "texture": "fluffy"},
                "position": {"x": 100, "y": 130, "spatial_ref": "beside pond_01"},
                "emotion": "curious",
                "carried_over": True,
            },
            {
                "id": "rock_01",
                "type": "rock",
                "properties": {"color": "grey", "size": "large", "texture": "mossy"},
                "position": {"x": 220, "y": 140},
                "emotion": None,
                "carried_over": True,
            },
            {
                "id": "pond_01",
                "type": "pond",
                "properties": {"color": "blue", "size": "medium"},
                "position": {"x": 140, "y": 150},
                "emotion": None,
                "carried_over": False,
            },
            {
                "id": "frog_01",
                "type": "frog",
                "properties": {"color": "green", "size": "tiny"},
                "position": {"x": 150, "y": 145, "spatial_ref": "on pond_01"},
                "emotion": "watchful",
                "carried_over": False,
            },
        ],
        "relations": [
            {
                "entity_a": "fox_01",
                "entity_b": "pond_01",
                "type": "spatial",
                "preposition": "beside",
            },
            {
                "entity_a": "frog_01",
                "entity_b": "pond_01",
                "type": "spatial",
                "preposition": "on",
            },
        ],
        "actions": [
            {
                "entity_id": "fox_01",
                "verb": "creep",
                "tense": "past",
                "manner": "slowly",
            },
            {
                "entity_id": "frog_01",
                "verb": "watch",
                "tense": "past",
                "manner": None,
            },
        ],
    },
    "neg": {
        "targets": [
            {
                "id": "t1_identity",
                "entity_id": "frog_01",
                "components": {
                    "identity": True,
                    "descriptors": ["green", "tiny"],
                    "spatial": "on pond_01",
                    "action": "watching",
                },
                "priority": 0.9,
                "tolerance": 0.3,
            }
        ],

        "min_coverage": 0.7,
        "skill_coverage_check": "PASS",
    },
    "sprite_code": {
        "pond_01": "const eid='pond_01';\nellip(140,152,22,6,50,100,200,eid+'.surface');\nellip(140,154,20,4,40,80,180,eid+'.surface.deep');\nellip(140,150,22,3,70,130,220,eid+'.surface.highlight');\npx(130,151,80,150,230,eid+'.surface.ripple1');\npx(145,151,80,150,230,eid+'.surface.ripple2');\npx(138,153,60,100,190,eid+'.surface.ripple3');\nellip(150,148,4,2,30,100,30,eid+'.lilypad');\npx(152,147,40,120,40,eid+'.lilypad.vein');",
        "frog_01": "const eid='frog_01';\nellip(150,146,4,3,50,160,50,eid+'.body');\ncirc(147,143,2,50,170,50,eid+'.head');\ncirc(146,141,1,0,0,0,eid+'.head.eyes.left');\ncirc(149,141,1,0,0,0,eid+'.head.eyes.right');\npx(147,144,30,130,30,eid+'.head.mouth');\nrect(146,149,2,2,40,140,40,eid+'.legs.front');\nrect(152,149,2,2,40,140,40,eid+'.legs.back');\npx(148,148,60,180,60,eid+'.body.belly');",
    },
    "carried_over_entities": ["fox_01", "rock_01"],
    "background_changed": False,
    "background_description": "A warm sunlit forest clearing with golden dappled light filtering through the canopy, soft mossy ground, and a pale blue sky.",
}


# ---------------------------------------------------------------------------
# Helper to create a mock Gemini client
# ---------------------------------------------------------------------------

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
# Tests: prompt building
# ---------------------------------------------------------------------------

class TestPromptBuilding:
    def test_system_prompt_documents_primitives(self):
        assert "px(x, y, r, g, b, entityId)" in SCENE_SYSTEM_PROMPT
        assert "circ(" in SCENE_SYSTEM_PROMPT
        assert "ellip(" in SCENE_SYSTEM_PROMPT
        assert "rect(" in SCENE_SYSTEM_PROMPT
        assert "tri(" in SCENE_SYSTEM_PROMPT
        assert "line(" in SCENE_SYSTEM_PROMPT
        assert "thickLine(" in SCENE_SYSTEM_PROMPT
        assert "arc(" in SCENE_SYSTEM_PROMPT

    def test_system_prompt_documents_canvas_size(self):
        assert "1120" in SCENE_SYSTEM_PROMPT
        assert "720" in SCENE_SYSTEM_PROMPT

    def test_system_prompt_requires_hierarchical_ids(self):
        assert "rabbit_01.body" in SCENE_SYSTEM_PROMPT
        assert "rabbit_01.head.ears.left" in SCENE_SYSTEM_PROMPT
        assert "at least 8" in SCENE_SYSTEM_PROMPT

    def test_system_prompt_documents_neg_selfcheck(self):
        assert "skill_coverage_check" in SCENE_SYSTEM_PROMPT
        assert "ENRICH" in SCENE_SYSTEM_PROMPT

    def test_system_prompt_carried_over_rule(self):
        assert "carried_over: false" in SCENE_SYSTEM_PROMPT
        assert "ONLY for new entities" in SCENE_SYSTEM_PROMPT.lower() or \
               "ONLY for entities with" in SCENE_SYSTEM_PROMPT

    def test_initial_prompt_includes_theme_and_objectives(self):
        prompt = _build_initial_prompt(["descriptive_adjectives", "spatial_prepositions"], "a sunny beach with tide pools")
        assert "descriptive_adjectives" in prompt
        assert "spatial_prepositions" in prompt
        assert "a sunny beach with tide pools" in prompt

    def test_continuation_prompt_includes_story_context(self):
        state = StoryState(session_id="s1", participant_id="P01")
        state.add_scene(
            scene_id="scene_01",
            narrative_text="A fox appeared.",
            manifest={"entities": []},
            neg={"targets": []},
            sprite_code={"fox_01": "circ(50,50,10,200,100,30,'fox_01.body');"},
        )
        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 3},
            total_utterances=5,
            scenes_completed=1,
        )
        prompt = _build_continuation_prompt(
            state, profile, ["descriptive_adjectives"]
        )
        assert "scene_01" in prompt
        assert "A fox appeared" in prompt
        assert "fox_01" in prompt
        assert "PROPERTY_COLOR" in prompt
        assert "descriptive_adjectives" in prompt


# ---------------------------------------------------------------------------
# Tests: JSON extraction
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json(self):
        data = _extract_json('{"a": 1}')
        assert data == {"a": 1}

    def test_json_in_markdown_fence(self):
        text = '```json\n{"a": 1}\n```'
        data = _extract_json(text)
        assert data == {"a": 1}

    def test_json_in_plain_fence(self):
        text = '```\n{"a": 1}\n```'
        data = _extract_json(text)
        assert data == {"a": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not json at all")


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_validate_initial_response(self):
        result = _validate_scene_response(FAKE_INITIAL_RESPONSE)
        assert result["manifest"]["scene_id"] == "scene_01"
        assert len(result["manifest"]["entities"]) == 3
        assert result["neg"]["skill_coverage_check"] == "PASS"
        assert "fox_01" in result["sprite_code"]
        assert result["carried_over_entities"] == []
        assert result["background_changed"] is True
        assert result["background_description"] != ""

    def test_validate_continuation_response(self):
        result = _validate_scene_response(FAKE_CONTINUATION_RESPONSE)
        assert result["manifest"]["scene_id"] == "scene_02"
        assert "fox_01" in result["carried_over_entities"]
        assert "rock_01" in result["carried_over_entities"]
        assert result["background_changed"] is False
        # New entities have sprite code
        assert "pond_01" in result["sprite_code"]
        assert "frog_01" in result["sprite_code"]
        # Carried-over entities do NOT have sprite code
        assert "fox_01" not in result["sprite_code"]

    def test_validate_background_changed_defaults_true(self):
        """When background_changed is omitted, default to True (safe)."""
        data = dict(FAKE_INITIAL_RESPONSE)
        data.pop("background_changed", None)
        result = _validate_scene_response(data)
        assert result["background_changed"] is True

    def test_validate_background_changed_invalid_type(self):
        """When background_changed is not a bool, default to True."""
        data = dict(FAKE_INITIAL_RESPONSE)
        data["background_changed"] = "maybe"
        result = _validate_scene_response(data)
        assert result["background_changed"] is True

    def test_validate_background_description_defaults_empty(self):
        """When background_description is omitted, default to empty string."""
        data = dict(FAKE_INITIAL_RESPONSE)
        data.pop("background_description", None)
        result = _validate_scene_response(data)
        assert result["background_description"] == ""

    def test_validate_background_description_passthrough(self):
        """background_description flows through validation."""
        result = _validate_scene_response(FAKE_INITIAL_RESPONSE)
        assert "sunlit forest" in result["background_description"]

    def test_validate_missing_manifest_raises(self):
        with pytest.raises(ValueError, match="manifest"):
            _validate_scene_response({"neg": {}, "sprite_code": {}})

    def test_validate_missing_neg_returns_empty_neg(self):
        """When NEG is absent (new pipeline), an empty NEG is used."""
        result = _validate_scene_response({
            "manifest": {"scene_id": "s1", "entities": [], "relations": [], "actions": []},
        })
        assert result["neg"]["targets"] == []
        assert result["neg"]["min_coverage"] == 0.7
        assert result["neg"]["skill_coverage_check"] == "PENDING"

    def test_validate_neg_present_is_preserved(self):
        """When NEG is present, it flows through validation."""
        result = _validate_scene_response(FAKE_INITIAL_RESPONSE)
        assert len(result["neg"]["targets"]) > 0
        assert result["neg"]["skill_coverage_check"] == "PASS"


# ---------------------------------------------------------------------------
# Tests: generate_scene (mocked Gemini)
# ---------------------------------------------------------------------------

class TestGenerateScene:
    def test_initial_scene_returns_valid_scene(self):
        mock_client = _make_mock_client(FAKE_INITIAL_RESPONSE)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    story_state=None,
                    student_profile=None,
                    skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
                    theme="a playground in a park",
                    use_reference_images=False,
                )
            )

        # Validate structure
        assert result["manifest"]["scene_id"] == "scene_01"
        entities = result["manifest"]["entities"]
        assert len(entities) >= 2
        assert result["carried_over_entities"] == []

        # All entities should have sprite code (initial scene)
        for ent in entities:
            assert ent["id"] in result["sprite_code"]

        # NEG should be present
        assert result["neg"]["skill_coverage_check"] == "PASS"

        # Narrative text
        assert len(result["narrative_text"]) > 0
        assert len(result["branch_summary"]) > 0

    def test_continuation_scene_has_carried_over(self):
        # Set up story state with scene_01 entities
        state = StoryState(session_id="s1", participant_id="P01")
        state.add_scene(
            scene_id="scene_01",
            narrative_text="A fluffy orange fox sat beside a mossy rock.",
            manifest=FAKE_INITIAL_RESPONSE["manifest"],
            neg=FAKE_INITIAL_RESPONSE["neg"],
            sprite_code=FAKE_INITIAL_RESPONSE["sprite_code"],
        )
        # Populate entity types
        for ent_data in FAKE_INITIAL_RESPONSE["manifest"]["entities"]:
            eid = ent_data["id"]
            if eid in state.active_entities:
                state.active_entities[eid].type = ent_data["type"]

        profile = StudentProfile(
            error_counts={"PROPERTY_COLOR": 5, "SPATIAL": 2},
            total_utterances=10,
            scenes_completed=1,
        )

        mock_client = _make_mock_client(FAKE_CONTINUATION_RESPONSE)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    story_state=state,
                    student_profile=profile,
                    skill_objectives=["descriptive_adjectives", "spatial_prepositions"],
                    use_reference_images=False,
                )
            )

        # Carried-over entities should be listed
        assert "fox_01" in result["carried_over_entities"]
        assert "rock_01" in result["carried_over_entities"]

        # New entities have sprite code
        assert "pond_01" in result["sprite_code"]
        assert "frog_01" in result["sprite_code"]

        # Carried-over entities should NOT have new sprite code
        assert "fox_01" not in result["sprite_code"]

        # Story state should be updated (now 2 scenes)
        assert len(state.scenes) == 2
        assert state.scenes[1]["scene_id"] == "scene_02"

        # New entities should be in active_entities
        assert "pond_01" in state.active_entities
        assert "frog_01" in state.active_entities

        # Old entities should still be there
        assert "fox_01" in state.active_entities

    def test_initial_scene_does_not_require_story_state(self):
        mock_client = _make_mock_client(FAKE_INITIAL_RESPONSE)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    theme="a farm with animals in the morning",
                    use_reference_images=False,
                )
            )

        assert result["manifest"]["scene_id"] == "scene_01"

    def test_gemini_called_with_correct_model(self):
        mock_client = _make_mock_client(FAKE_INITIAL_RESPONSE)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client) as mock_cls:
            asyncio.get_event_loop().run_until_complete(
                generate_scene(api_key="test-key", theme="a playground in a park", use_reference_images=False)
            )

        # Verify Client was created with the API key
        mock_cls.assert_called_once_with(api_key="test-key")

        # Verify generate_content was called
        call_args = mock_client.aio.models.generate_content.call_args
        assert call_args is not None
        assert call_args.kwargs["model"] == "gemini-3-flash-preview"

    def test_neg_override_injected(self):
        """When neg_override is provided, it replaces the scene's NEG."""
        mock_client = _make_mock_client(FAKE_INITIAL_RESPONSE)
        custom_neg = {
            "targets": [
                {
                    "id": "t_custom",
                    "entity_id": "fox_01",
                    "components": {"identity": True},
                    "priority": 0.5,
                    "tolerance": 0.5,
                }
            ],
    
            "min_coverage": 0.8,
            "skill_coverage_check": "PASS",
        }

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    theme="a playground in a park",
                    use_reference_images=False,
                    neg_override=custom_neg,
                )
            )

        assert result["neg"]["targets"][0]["id"] == "t_custom"
        assert result["neg"]["min_coverage"] == 0.8

    def test_response_with_markdown_fences_parsed(self):
        """Gemini sometimes wraps JSON in markdown fences."""
        fenced_response = "```json\n" + json.dumps(FAKE_INITIAL_RESPONSE) + "\n```"
        mock_response = MagicMock()
        mock_response.text = fenced_response

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_scene(api_key="fake-key", theme="a playground in a park", use_reference_images=False)
            )

        assert result["manifest"]["scene_id"] == "scene_01"


# ---------------------------------------------------------------------------
# Tests: MANIFEST_SYSTEM_PROMPT content
# ---------------------------------------------------------------------------

class TestManifestPrompt:
    def test_manifest_prompt_does_not_contain_primitive_api(self):
        """MANIFEST_SYSTEM_PROMPT should NOT include sprite code rules."""
        assert "px(x, y, r, g, b, entityId)" not in MANIFEST_SYSTEM_PROMPT
        assert "rect(x, y, width, height" not in MANIFEST_SYSTEM_PROMPT
        assert "circ(cx, cy, radius" not in MANIFEST_SYSTEM_PROMPT

    def test_manifest_prompt_mentions_later_steps(self):
        assert "later step" in MANIFEST_SYSTEM_PROMPT.lower()

    def test_manifest_prompt_documents_canvas_size(self):
        assert "1120" in MANIFEST_SYSTEM_PROMPT
        assert "720" in MANIFEST_SYSTEM_PROMPT

    def test_manifest_prompt_requires_rich_descriptions(self):
        assert "distinctive_features" in MANIFEST_SYSTEM_PROMPT
        assert "texture" in MANIFEST_SYSTEM_PROMPT
        assert "pose" in MANIFEST_SYSTEM_PROMPT

    def test_manifest_prompt_does_not_include_neg(self):
        """MANIFEST_SYSTEM_PROMPT should NOT include NEG — it's generated separately."""
        assert "skill_coverage_check" not in MANIFEST_SYSTEM_PROMPT

        assert '"neg"' not in MANIFEST_SYSTEM_PROMPT

    def test_manifest_prompt_has_scene_description_field(self):
        assert "scene_description" in MANIFEST_SYSTEM_PROMPT

    def test_manifest_prompt_has_background_description_field(self):
        assert "background_description" in MANIFEST_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Fixtures for 3-step pipeline tests
# ---------------------------------------------------------------------------

FAKE_MANIFEST_RESPONSE = {
    "narrative_text": "A fluffy orange fox sat beside a tall mossy rock in a sunlit forest clearing.",
    "branch_summary": "A curious fox discovers a forest clearing",
    "scene_description": "A warm sunlit forest clearing with dappled golden light filtering through tall oak trees.",
    "manifest": {
        "scene_id": "scene_01",
        "entities": [
            {
                "id": "fox_01",
                "type": "fox",
                "properties": {
                    "color": "warm orange with cream underbelly",
                    "size": "small",
                    "texture": "fluffy soft fur",
                    "distinctive_features": "bushy tail with white tip",
                },
                "position": {"x": 80, "y": 230, "spatial_ref": "beside rock_01"},
                "emotion": "curious",
                "pose": "sitting upright with head tilted",
                "carried_over": False,
            },
            {
                "id": "rock_01",
                "type": "rock",
                "properties": {
                    "color": "grey with green moss patches",
                    "size": "large",
                    "texture": "rough and weathered",
                    "distinctive_features": "covered in bright green moss",
                },
                "position": {"x": 140, "y": 240},
                "emotion": None,
                "pose": None,
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
                    "descriptors": ["orange", "fluffy", "small"],
                    "spatial": "beside rock_01",
                    "action": "sitting quietly",
                },
                "priority": 0.9,
                "tolerance": 0.3,
            }
        ],
        "min_coverage": 0.7,
        "skill_coverage_check": "PASS",
    },
    "carried_over_entities": [],
    "background_changed": True,
    "background_description": "A warm sunlit forest clearing with golden dappled light filtering through the canopy.",
}

FAKE_SPRITE_CODE_RESPONSE = {
    "sprite_code": {
        "bg": "for(var y=0;y<170;y++) for(var x=0;x<PW;x++) px(x,y,135,190,220,'sky');",
        "fox_01": "const eid='fox_01';\nellip(80,230,22,12,200,80,48,eid+'.body');\ncirc(60,220,12,205,85,50,eid+'.head');\ncirc(57,218,2,15,10,8,eid+'.head.eyes.left');\ncirc(63,218,2,15,10,8,eid+'.head.eyes.right');\npx(57,216,255,255,255,eid+'.head.eyes.left');\npx(63,216,255,255,255,eid+'.head.eyes.right');\ntri(52,216,54,206,56,216,200,80,48,eid+'.head.ears.left');\ntri(64,216,66,206,68,216,200,80,48,eid+'.head.ears.right');\nellip(106,226,14,6,200,80,48,eid+'.tail');",
        "rock_01": "const eid='rock_01';\nellip(140,245,20,12,80,80,80,eid+'.body');\nellip(140,243,16,10,100,98,95,eid+'.body');\npx(130,240,60,110,50,eid+'.moss');\npx(135,239,60,110,50,eid+'.moss');\npx(140,241,60,110,50,eid+'.moss');\npx(145,240,60,110,50,eid+'.moss');\npx(150,241,60,110,50,eid+'.moss');\npx(140,238,118,115,110,eid+'.body.highlight');",
    }
}

# Build a real tiny 4x4 PNG so Pillow can open it in _downscale_to_canvas
def _make_tiny_png() -> bytes:
    import io as _io
    from PIL import Image as _Img
    img = _Img.new("RGB", (4, 4), (100, 150, 200))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

FAKE_IMAGE_BYTES = _make_tiny_png()


# ---------------------------------------------------------------------------
# Tests: 3-step pipeline functions
# ---------------------------------------------------------------------------

class TestGenerateManifest:
    def test_returns_manifest_and_neg(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps(FAKE_MANIFEST_RESPONSE)
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        result = asyncio.get_event_loop().run_until_complete(
            _generate_manifest(mock_client, "test prompt")
        )

        assert "manifest" in result
        assert "neg" in result
        assert "scene_description" in result
        assert result["manifest"]["scene_id"] == "scene_01"

    def test_uses_manifest_system_prompt(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps(FAKE_MANIFEST_RESPONSE)
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        asyncio.get_event_loop().run_until_complete(
            _generate_manifest(mock_client, "test prompt")
        )

        call_args = mock_models.generate_content.call_args
        config = call_args.kwargs["config"]
        assert "scene architect" in config.system_instruction.lower()

    def test_missing_manifest_raises(self):
        bad_response = {"neg": {"targets": []}}
        mock_response = MagicMock()
        mock_response.text = json.dumps(bad_response)
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with pytest.raises(ValueError, match="manifest"):
            asyncio.get_event_loop().run_until_complete(
                _generate_manifest(mock_client, "test prompt")
            )

    def test_missing_neg_does_not_raise(self):
        """Manifest without NEG should succeed — NEG is now optional."""
        response_no_neg = dict(FAKE_MANIFEST_RESPONSE)
        response_no_neg = {k: v for k, v in response_no_neg.items() if k != "neg"}
        mock_response = MagicMock()
        mock_response.text = json.dumps(response_no_neg)
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        result = asyncio.get_event_loop().run_until_complete(
            _generate_manifest(mock_client, "test prompt")
        )
        assert "manifest" in result
        assert "neg" not in result  # NEG is absent, not injected by _generate_manifest


class TestBuildSceneImagePrompt:
    def test_includes_scene_description(self):
        prompt = _build_scene_image_prompt(FAKE_MANIFEST_RESPONSE)
        assert "warm sunlit forest clearing" in prompt

    def test_is_background_only(self):
        prompt = _build_scene_image_prompt(FAKE_MANIFEST_RESPONSE)
        assert "BACKGROUND ONLY" in prompt
        assert "no characters" in prompt.lower()

    def test_prefers_background_description(self):
        """When background_description is present, use it instead of scene_description."""
        data = dict(FAKE_MANIFEST_RESPONSE)
        data["background_description"] = "A deep blue night sky with twinkling stars."
        data["scene_description"] = "A fox sits beside a rock in a moonlit clearing."
        prompt = _build_scene_image_prompt(data)
        assert "deep blue night sky" in prompt
        assert "fox" not in prompt.lower()

    def test_falls_back_to_scene_description(self):
        """When background_description is empty, fall back to scene_description."""
        data = dict(FAKE_MANIFEST_RESPONSE)
        data["background_description"] = ""
        data["scene_description"] = "A warm sunlit clearing."
        prompt = _build_scene_image_prompt(data)
        assert "warm sunlit clearing" in prompt

    def test_falls_back_when_background_description_missing(self):
        """When background_description key is missing, fall back to scene_description."""
        data = dict(FAKE_MANIFEST_RESPONSE)
        data.pop("background_description", None)
        data["scene_description"] = "Golden meadow at dawn."
        prompt = _build_scene_image_prompt(data)
        assert "Golden meadow at dawn" in prompt


class TestGenerateBackgroundImage:
    def test_returns_image_bytes(self):
        mock_inline = MagicMock()
        mock_inline.inline_data = MagicMock()
        mock_inline.inline_data.data = FAKE_IMAGE_BYTES

        mock_content = MagicMock()
        mock_content.parts = [mock_inline]
        mock_candidate = MagicMock()
        mock_candidate.content = mock_content

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        result = asyncio.get_event_loop().run_until_complete(
            _generate_background_image(mock_client, FAKE_MANIFEST_RESPONSE)
        )

        assert result is not None
        assert isinstance(result, bytes)
        assert result[:4] == b"\x89PNG"

    def test_uses_image_model(self):
        mock_inline = MagicMock()
        mock_inline.inline_data = MagicMock()
        mock_inline.inline_data.data = FAKE_IMAGE_BYTES

        mock_content = MagicMock()
        mock_content.parts = [mock_inline]
        mock_candidate = MagicMock()
        mock_candidate.content = mock_content

        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        asyncio.get_event_loop().run_until_complete(
            _generate_background_image(mock_client, FAKE_MANIFEST_RESPONSE)
        )

        call_args = mock_models.generate_content.call_args
        assert call_args.kwargs["model"] == IMAGE_MODEL_ID

    def test_returns_none_on_failure(self):
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(side_effect=RuntimeError("API error"))
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        result = asyncio.get_event_loop().run_until_complete(
            _generate_background_image(mock_client, FAKE_MANIFEST_RESPONSE)
        )

        assert result is None


# ---------------------------------------------------------------------------
# Tests: Pixel extraction (chroma-key and background quantize)
# ---------------------------------------------------------------------------

def _make_chroma_key_image(width=64, height=64) -> bytes:
    """Create a test image: red chroma-key background with a blue square in the center."""
    from PIL import Image as _Img
    img = _Img.new("RGB", (width, height), (255, 0, 0))  # #FF0000 red chroma-key
    # Draw a blue square (16×16) in the center (non-red entity pixels)
    for y in range(24, 40):
        for x in range(24, 40):
            img.putpixel((x, y), (30, 50, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_background_image(width=280, height=180) -> bytes:
    """Create a test background image with sky (blue) and ground (green)."""
    from PIL import Image as _Img
    img = _Img.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            if y < 108:  # sky
                img.putpixel((x, y), (100, 150, 220))
            else:  # ground
                img.putpixel((x, y), (50, 120, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestExtractEntitySprite:
    def test_chroma_key_removal(self):
        """Red chroma-key pixels should become None, entity pixels should be preserved."""
        img_bytes = _make_chroma_key_image()
        result = _extract_entity_sprite(img_bytes, 32, 32)

        # Output fits within target; actual size depends on content aspect ratio
        assert result["w"] <= 32
        assert result["h"] <= 32
        assert len(result["pixels"]) == result["w"] * result["h"]

        # Check that some pixels are None (red background) and some are not
        none_count = sum(1 for p in result["pixels"] if p is None)
        visible_count = sum(1 for p in result["pixels"] if p is not None)
        assert none_count > 0, "Should have transparent (red chroma-key) pixels"
        assert visible_count > 0, "Should have visible (non-red) pixels"

    def test_visible_pixels_have_correct_colors(self):
        """Non-red-chroma pixels should retain their approximate RGB values."""
        img_bytes = _make_chroma_key_image()
        result = _extract_entity_sprite(img_bytes, 32, 32)

        visible = [p for p in result["pixels"] if p is not None]
        assert len(visible) > 0
        # The majority of visible pixels should be bluish (from the blue square
        # in the center). A few edge pixels may have blended colors from LANCZOS
        # resampling near the content crop margin.
        blue_count = sum(1 for p in visible if p[2] > 100 and p[0] < 100)
        assert blue_count > len(visible) * 0.9, (
            f"Expected >90% blue pixels, got {blue_count}/{len(visible)}"
        )

    def test_output_dimensions_fit_within_target(self):
        """Output should fit within target dimensions, preserving aspect ratio."""
        img_bytes = _make_chroma_key_image(128, 128)
        result = _extract_entity_sprite(img_bytes, 20, 25)

        # Content is square (16×16 blue square), so it should scale
        # to fit within 20×25 → 20×20 (limited by width)
        assert result["w"] <= 20
        assert result["h"] <= 25
        assert len(result["pixels"]) == result["w"] * result["h"]


class TestIsChromaBackground:
    """Test the channel-based red chroma-key detection."""

    def test_exact_red(self):
        assert _is_chroma_background(255, 0, 0) is True

    def test_approximate_reds(self):
        assert _is_chroma_background(200, 20, 30) is True
        assert _is_chroma_background(180, 50, 40) is True
        assert _is_chroma_background(230, 10, 15) is True

    def test_non_red_colors(self):
        assert _is_chroma_background(0, 255, 0) is False    # green
        assert _is_chroma_background(50, 50, 200) is False   # blue
        assert _is_chroma_background(200, 200, 200) is False # grey
        assert _is_chroma_background(0, 0, 0) is False       # black
        assert _is_chroma_background(255, 255, 255) is False # white

    def test_reddish_entity_colors_preserved(self):
        """Colors that are reddish but have significant G or B should not be removed."""
        assert _is_chroma_background(180, 130, 50) is False   # orange-yellow (g > 120)
        assert _is_chroma_background(150, 50, 130) is False   # purple (b > 120)
        assert _is_chroma_background(130, 100, 100) is False  # muted red (r not > g+30)

    def test_borderline_thresholds(self):
        # Just over r threshold (121), g and b low → detected
        assert _is_chroma_background(121, 50, 50) is True
        # r just at threshold (120) → not detected (r > 120 fails)
        assert _is_chroma_background(120, 50, 50) is False
        # g exactly at threshold (120) → not detected (g < 120 fails)
        assert _is_chroma_background(200, 120, 50) is False
        # g just below threshold (119) → detected
        assert _is_chroma_background(200, 119, 50) is True
        # b just at threshold (120) → not detected
        assert _is_chroma_background(200, 50, 120) is False
        # b just below threshold (119) → detected
        assert _is_chroma_background(200, 50, 119) is True


class TestDechromaPixel:
    """Test the _dechroma_pixel function that protects reddish sprite pixels."""

    def test_pure_red_is_shifted(self):
        """Pure red (255, 0, 0) should be shifted to not match chroma."""
        result = _dechroma_pixel(255, 0, 0)
        assert result != [255, 0, 0], "Pure red pixel should be shifted"
        assert not _is_chroma_background(*result), "Shifted pixel must not match chroma"

    def test_dark_red_is_shifted(self):
        """Dark red (200, 30, 20) should be shifted."""
        result = _dechroma_pixel(200, 30, 20)
        assert not _is_chroma_background(*result), "Shifted pixel must not match chroma"
        # Red channel preserved
        assert result[0] == 200

    def test_bright_red_shifted(self):
        """Bright red (230, 10, 15) should be shifted."""
        result = _dechroma_pixel(230, 10, 15)
        assert not _is_chroma_background(*result)
        assert result[0] == 230

    def test_non_red_unchanged(self):
        """Normal colors should not be modified."""
        assert _dechroma_pixel(100, 150, 200) == [100, 150, 200]
        assert _dechroma_pixel(50, 200, 50) == [50, 200, 50]
        assert _dechroma_pixel(0, 0, 0) == [0, 0, 0]
        assert _dechroma_pixel(255, 255, 255) == [255, 255, 255]

    def test_orange_unchanged(self):
        """Orange with enough green (200, 130, 50) doesn't match chroma, should be unchanged."""
        assert _dechroma_pixel(200, 130, 50) == [200, 130, 50]

    def test_dark_orange_shifted(self):
        """Dark orange (200, 100, 50) matches chroma, should be shifted."""
        result = _dechroma_pixel(200, 100, 50)
        assert not _is_chroma_background(*result)
        assert result[0] == 200  # red preserved

    def test_reddish_with_high_green_unchanged(self):
        """Reddish pixel with g >= 120 doesn't match chroma, should be unchanged."""
        assert _dechroma_pixel(180, 130, 50) == [180, 130, 50]

    def test_borderline_red_shifted(self):
        """Borderline case (121, 50, 50) matches chroma, should be shifted."""
        result = _dechroma_pixel(121, 50, 50)
        assert not _is_chroma_background(*result)

    def test_shift_preserves_red_and_blue(self):
        """Only green channel should change; red and blue are preserved."""
        result = _dechroma_pixel(255, 0, 0)
        assert result[0] == 255  # red preserved
        assert result[2] == 0    # blue preserved
        assert result[1] > 0     # green was increased


class TestDetectBackgroundColor:
    """Test automatic background color detection from corner pixels."""

    def test_uniform_red_corners(self):
        """Uniform pure red background → detects (255, 0, 0)."""
        from PIL import Image as _Img
        img = _Img.new("RGB", (64, 64), (255, 0, 0))
        # Put a blue sprite in the center (corners stay red)
        for y in range(20, 44):
            for x in range(20, 44):
                img.putpixel((x, y), (30, 50, 200))
        assert _detect_background_color(img) == (255, 0, 0)

    def test_impure_red_corners(self):
        """Impure red background like Gemini produces → detects correct color."""
        from PIL import Image as _Img
        img = _Img.new("RGB", (64, 64), (254, 52, 47))
        for y in range(20, 44):
            for x in range(20, 44):
                img.putpixel((x, y), (100, 80, 60))
        result = _detect_background_color(img)
        assert result == (254, 52, 47)

    def test_pinkish_red_corners(self):
        """Pinkish red background → detects correct color."""
        from PIL import Image as _Img
        img = _Img.new("RGB", (1024, 1024), (254, 18, 59))
        result = _detect_background_color(img)
        assert result == (254, 18, 59)

    def test_green_background(self):
        """Green background → detects green."""
        from PIL import Image as _Img
        img = _Img.new("RGB", (32, 32), (0, 255, 0))
        result = _detect_background_color(img)
        assert result == (0, 255, 0)

    def test_small_image(self):
        """Works on tiny images (minimum 2×2 needed for corner sampling)."""
        from PIL import Image as _Img
        img = _Img.new("RGB", (2, 2), (200, 30, 10))
        result = _detect_background_color(img)
        assert result == (200, 30, 10)


class TestIsBackgroundPixel:
    """Test Euclidean distance-based background pixel detection."""

    def test_identical_pixel(self):
        """Pixel identical to background → background."""
        assert _is_background_pixel(254, 52, 47, 254, 52, 47) is True

    def test_very_close_pixel(self):
        """Pixel very close to background → background."""
        assert _is_background_pixel(250, 48, 50, 254, 52, 47) is True

    def test_far_pixel(self):
        """Brown sprite pixel far from red background → not background."""
        assert _is_background_pixel(140, 80, 70, 254, 0, 0) is False

    def test_dark_brown_far_from_impure_red(self):
        """Dark brown (140,80,70) far from impure red bg (254,52,47) → not background."""
        # dist = sqrt((140-254)^2 + (80-52)^2 + (70-47)^2) ≈ 120
        assert _is_background_pixel(140, 80, 70, 254, 52, 47) is False

    def test_green_far_from_red(self):
        """Green pixel far from any red → not background."""
        assert _is_background_pixel(30, 200, 40, 255, 0, 0) is False

    def test_custom_threshold(self):
        """Custom threshold changes detection sensitivity."""
        # dist between (254,0,0) and (200,50,50) ≈ 86.6
        assert _is_background_pixel(200, 50, 50, 254, 0, 0, threshold=90) is True
        assert _is_background_pixel(200, 50, 50, 254, 0, 0, threshold=80) is False

    def test_black_far_from_red(self):
        """Black pixel (0,0,0) is far from red background."""
        assert _is_background_pixel(0, 0, 0, 255, 0, 0) is False

    def test_white_far_from_red(self):
        """White pixel (255,255,255) is far from red background."""
        assert _is_background_pixel(255, 255, 255, 255, 0, 0) is False


class TestExtractEntitySpriteRembg:
    """Test rembg-based extraction with larger images that work with U2Net."""

    @staticmethod
    def _make_test_image(bg_color, fg_color, size=256, fg_size=120):
        """Create a test image with a centered filled circle on a solid background.

        Uses a circle (not a square) so rembg's neural network can segment it.
        Size must be large enough (>= 128) for U2Net to work properly.
        """
        from PIL import Image as _Img, ImageDraw
        img = _Img.new("RGB", (size, size), bg_color)
        draw = ImageDraw.Draw(img)
        cx, cy = size // 2, size // 2
        r = fg_size // 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fg_color)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_red_bg_preserves_blue_entity(self):
        """Blue entity on red background should be preserved."""
        img_bytes = self._make_test_image((255, 0, 0), (30, 50, 200))
        result = _extract_entity_sprite(img_bytes, 64, 64)
        visible = [p for p in result["pixels"] if p is not None]
        assert len(visible) > 0, "Blue entity pixels should be preserved"

    def test_red_bg_preserves_green_entity(self):
        """Green entity on red background should be preserved."""
        img_bytes = self._make_test_image((255, 0, 0), (30, 200, 40))
        result = _extract_entity_sprite(img_bytes, 64, 64)
        visible = [p for p in result["pixels"] if p is not None]
        assert len(visible) > 0, "Green pixels should not be removed"

    def test_output_has_some_transparent_pixels(self):
        """Background should be at least partially removed (some None pixels)."""
        img_bytes = self._make_test_image((255, 0, 0), (30, 50, 200))
        result = _extract_entity_sprite(img_bytes, 64, 64)
        none_count = sum(1 for p in result["pixels"] if p is None)
        # rembg should remove at least some background
        assert none_count > 0, "rembg should remove at least some background pixels"

    def test_output_dimensions_fit_target(self):
        """Output dimensions should fit within the target."""
        img_bytes = self._make_test_image((255, 0, 0), (80, 130, 50))
        result = _extract_entity_sprite(img_bytes, 50, 40)
        assert result["w"] <= 50, f"Width {result['w']} exceeds target 50"
        assert result["h"] <= 40, f"Height {result['h']} exceeds target 40"
        assert result["w"] > 0 and result["h"] > 0

    def test_preserves_orange_entity_pixels(self):
        """Reddish entity pixels (orange) should NOT be removed."""
        img_bytes = self._make_test_image((255, 0, 0), (180, 130, 50))
        result = _extract_entity_sprite(img_bytes, 64, 64)
        visible = [p for p in result["pixels"] if p is not None]
        assert len(visible) > 0, "Orange pixels should not be removed"


class TestExtractBackgroundSprite:
    def test_returns_image_background(self):
        img_bytes = _make_background_image()
        result = _extract_background_sprite(img_bytes)

        assert result["format"] == "image_background"
        assert result["width"] == 1120
        assert result["height"] == 720
        assert result["x"] == 0
        assert result["y"] == 0

    def test_has_base64_image(self):
        import base64
        img_bytes = _make_background_image()
        result = _extract_background_sprite(img_bytes)

        assert "image_base64" in result
        b64 = result["image_base64"]
        assert isinstance(b64, str)
        assert len(b64) > 100  # reasonable PNG size

        # Verify it decodes to valid PNG bytes
        decoded = base64.b64decode(b64)
        assert decoded[:4] == b'\x89PNG'

    def test_downscales_to_canvas_size(self):
        """Even if input is larger, output should be 1120x720 (upscaled from 560x360)."""
        import base64
        from PIL import Image as _Img
        # Create a large image
        img = _Img.new("RGB", (1920, 1080), (100, 150, 220))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        result = _extract_background_sprite(buf.getvalue())
        assert result["width"] == 1120
        assert result["height"] == 720

        # Decode and verify actual dimensions
        decoded = base64.b64decode(result["image_base64"])
        out_img = _Img.open(io.BytesIO(decoded))
        assert out_img.size == (1120, 720)


class TestFallbackMask:
    def test_visible_pixels_get_entity_id(self):
        pixels = [[100, 50, 30], None, [200, 100, 50], None]
        mask = _build_fallback_mask("fox_01", pixels)
        assert mask == ["fox_01", None, "fox_01", None]

    def test_all_none_pixels(self):
        pixels = [None, None, None]
        mask = _build_fallback_mask("entity", pixels)
        assert mask == [None, None, None]


class TestIsRleFormat:
    def test_rle_format(self):
        assert _is_rle_format([["fox_01.head", 5], [None, 3]]) is True

    def test_rle_tuples(self):
        assert _is_rle_format([("fox_01.head", 5), (None, 3)]) is True

    def test_legacy_flat_format(self):
        assert _is_rle_format(["fox_01.head", "fox_01.head", None]) is False

    def test_empty_list(self):
        assert _is_rle_format([]) is False

    def test_legacy_starts_with_none(self):
        assert _is_rle_format([None, "fox_01.head", None]) is False


class TestExpandRleMask:
    def test_single_run(self):
        """One run covering the entire sprite."""
        rle = [["fox_01.body", 10]]
        pixels = [[100, 50, 30]] * 10
        mask = _expand_rle_mask(rle, 10, "fox_01", pixels)
        assert mask == ["fox_01.body"] * 10

    def test_multi_runs(self):
        """Alternating null and entity IDs."""
        rle = [[None, 3], ["fox_01.head", 2], [None, 1], ["fox_01.body", 4]]
        pixels = [None, None, None, [100, 50, 30], [110, 60, 40],
                  None, [200, 100, 50], [210, 110, 60], [220, 120, 70], [230, 130, 80]]
        mask = _expand_rle_mask(rle, 10, "fox_01", pixels)
        assert mask == [None, None, None, "fox_01.head", "fox_01.head",
                        None, "fox_01.body", "fox_01.body", "fox_01.body", "fox_01.body"]

    def test_sub_entities(self):
        """Multiple sub-entity IDs in order."""
        rle = [["fox_01.head", 3], ["fox_01.head.eyes.left", 1],
               ["fox_01.body", 4], ["fox_01.tail", 2]]
        pixels = [[1, 1, 1]] * 10
        mask = _expand_rle_mask(rle, 10, "fox_01", pixels)
        assert mask[0] == "fox_01.head"
        assert mask[3] == "fox_01.head.eyes.left"
        assert mask[4] == "fox_01.body"
        assert mask[8] == "fox_01.tail"

    def test_padding_when_short(self):
        """If RLE sum < total_pixels, pad with None and sync with pixels."""
        rle = [["fox_01.body", 3]]
        pixels = [[1, 1, 1]] * 3 + [None, None] + [[2, 2, 2]] * 2
        mask = _expand_rle_mask(rle, 7, "fox_01", pixels)
        assert len(mask) == 7
        # First 3: from RLE
        assert mask[:3] == ["fox_01.body"] * 3
        # 4th, 5th: None (transparent pixels)
        assert mask[3] is None
        assert mask[4] is None
        # 6th, 7th: visible but no RLE → root entity
        assert mask[5] == "fox_01"
        assert mask[6] == "fox_01"

    def test_truncation_when_long(self):
        """If RLE sum > total_pixels, truncate."""
        rle = [["fox_01.body", 20]]
        pixels = [[1, 1, 1]] * 5
        mask = _expand_rle_mask(rle, 5, "fox_01", pixels)
        assert len(mask) == 5
        assert mask == ["fox_01.body"] * 5

    def test_transparency_sync(self):
        """Visible pixels with null mask → root entity ID."""
        rle = [[None, 10]]
        pixels = [None, None, [100, 50, 30], None, [200, 100, 50],
                  None, None, None, [50, 50, 50], None]
        mask = _expand_rle_mask(rle, 10, "fox_01", pixels)
        # Transparent pixels stay None
        assert mask[0] is None
        assert mask[1] is None
        assert mask[3] is None
        # Visible pixels get root entity ID
        assert mask[2] == "fox_01"
        assert mask[4] == "fox_01"
        assert mask[8] == "fox_01"

    def test_null_string_handling(self):
        """String 'null' should be treated as None."""
        rle = [["null", 3], ["fox_01.body", 2]]
        pixels = [None, None, None, [1, 1, 1], [2, 2, 2]]
        mask = _expand_rle_mask(rle, 5, "fox_01", pixels)
        assert mask[:3] == [None, None, None]
        assert mask[3:] == ["fox_01.body", "fox_01.body"]

    def test_invalid_items_skipped(self):
        """Malformed RLE items should be skipped gracefully."""
        rle = [["fox_01.body", 3], "bad_item", [None, 2]]
        pixels = [[1, 1, 1]] * 3 + [None, None]
        mask = _expand_rle_mask(rle, 5, "fox_01", pixels)
        assert len(mask) == 5
        assert mask[:3] == ["fox_01.body"] * 3
        assert mask[3] is None
        assert mask[4] is None


class TestAssembleSpriteCode:
    def test_assembles_background_and_entities(self):
        bg_sprite = {
            "format": "image_background",
            "x": 0, "y": 0,
            "width": 1120, "height": 720,
            "image_base64": "iVBORw0KGgoAAAANS...",  # truncated, just needs to exist
        }
        entity_sprites = {
            "fox_01": {
                "pixels": [[200, 80, 48], None, [215, 100, 60], None],
                "w": 2, "h": 2,
            }
        }
        entity_masks = {
            "fox_01": ["fox_01.body", None, "fox_01.body", None],
        }
        manifest_data = {
            "manifest": {
                "entities": [
                    {
                        "id": "fox_01",
                        "type": "fox",
                        "position": {"x": 80, "y": 115},
                        "width_hint": 2,
                        "height_hint": 2,
                    }
                ]
            }
        }

        entity_positions = _compute_entity_positions(manifest_data)
        result = _assemble_sprite_code(
            bg_sprite, entity_sprites, entity_masks, entity_positions
        )

        assert "bg" in result
        assert result["bg"]["format"] == "image_background"
        assert "fox_01" in result
        assert result["fox_01"]["format"] == "raw_sprite"
        assert result["fox_01"]["w"] == 2
        assert result["fox_01"]["h"] == 2
        assert result["fox_01"]["pixels"] == [[200, 80, 48], None, [215, 100, 60], None]
        assert result["fox_01"]["mask"] == ["fox_01.body", None, "fox_01.body", None]

    def test_computes_topleft_from_center_position(self):
        entity_sprites = {
            "obj_01": {"pixels": [None] * 100, "w": 10, "h": 10}
        }
        manifest_data = {
            "manifest": {
                "entities": [
                    {"id": "obj_01", "type": "obj", "position": {"x": 100, "y": 100},
                     "width_hint": 10, "height_hint": 10}
                ]
            }
        }
        entity_positions = _compute_entity_positions(manifest_data)
        result = _assemble_sprite_code(None, entity_sprites, {}, entity_positions)
        assert result["obj_01"]["x"] == 95   # 100 - 10//2
        assert result["obj_01"]["y"] == 95   # 100 - 10//2


class TestComputeEntityPositions:
    """Test _compute_entity_positions with edge clamping."""

    def _manifest(self, x, y, w_hint, h_hint, eid="obj_01"):
        return {
            "manifest": {
                "entities": [
                    {"id": eid, "type": "obj",
                     "position": {"x": x, "y": y},
                     "width_hint": w_hint, "height_hint": h_hint}
                ]
            }
        }

    def test_center_converts_to_topleft(self):
        positions = _compute_entity_positions(self._manifest(280, 180, 100, 60))
        assert positions["obj_01"]["x"] == 230  # 280 - 100//2
        assert positions["obj_01"]["y"] == 150  # 180 - 60//2

    def test_no_clamping_when_within_bounds(self):
        positions = _compute_entity_positions(self._manifest(280, 180, 40, 40))
        assert positions["obj_01"]["x"] == 260  # 280 - 20
        assert positions["obj_01"]["y"] == 160  # 180 - 20

    def test_clamps_right_edge(self):
        # center=1080, w=160 → top-left x = 1080-80 = 1000, x+w = 1160 > 1120
        positions = _compute_entity_positions(self._manifest(1080, 360, 160, 60))
        assert positions["obj_01"]["x"] == 960  # clamped: 1120 - 160
        assert positions["obj_01"]["x"] + positions["obj_01"]["w"] <= 1120

    def test_clamps_left_edge(self):
        # center=20, w=100 → top-left x = 20-50 = -30
        positions = _compute_entity_positions(self._manifest(20, 180, 100, 60))
        assert positions["obj_01"]["x"] == 0

    def test_clamps_top_edge(self):
        # center_y=20, h=100 → top-left y = 20-50 = -30
        positions = _compute_entity_positions(self._manifest(280, 20, 60, 100))
        assert positions["obj_01"]["y"] == 0

    def test_clamps_bottom_edge(self):
        # center_y=700, h=100 → top-left y = 700-50 = 650, y+h = 750 > 720
        positions = _compute_entity_positions(self._manifest(560, 700, 60, 100))
        assert positions["obj_01"]["y"] == 620  # clamped: 720 - 100
        assert positions["obj_01"]["y"] + positions["obj_01"]["h"] <= 720

    def test_clamps_corner(self):
        # both right and bottom overflow
        positions = _compute_entity_positions(self._manifest(1100, 700, 80, 80))
        assert positions["obj_01"]["x"] + positions["obj_01"]["w"] <= 1120
        assert positions["obj_01"]["y"] + positions["obj_01"]["h"] <= 720
        assert positions["obj_01"]["x"] >= 0
        assert positions["obj_01"]["y"] >= 0

    def test_uses_actual_sprite_dimensions(self):
        # Actual sprite is larger than hint → clamping uses actual size
        entity_sprites = {"obj_01": {"pixels": [None] * 8000, "w": 100, "h": 80}}
        positions = _compute_entity_positions(
            self._manifest(1100, 360, 50, 60), entity_sprites
        )
        assert positions["obj_01"]["x"] == 1020  # clamped: 1120 - 100
        assert positions["obj_01"]["x"] + positions["obj_01"]["w"] <= 1120


FAKE_MASK_RESPONSE = {
    "mask": [
        ["fox_01.body", 2],
        [None, 1],
        ["fox_01.head", 2],
        [None, 11],
    ]
}


class TestPipelineIntegration:
    """Test the full 5-step pipeline with all mocks."""

    def _make_chroma_entity_image(self):
        """Create a small chroma-key entity image."""
        return _make_chroma_key_image(64, 64)

    def _make_pipeline_mock_client(self):
        """Create a mock client that handles all pipeline steps.

        Step 1: manifest (text, MODEL_ID)
        Step 2a: background image (IMAGE_MODEL_ID)
        Step 2b: entity images × N (IMAGE_MODEL_ID)
        Step 3: mask generation × N (MODEL_ID)
        """
        # Manifest response
        manifest_resp = MagicMock()
        manifest_resp.text = json.dumps(FAKE_MANIFEST_RESPONSE)

        # Image responses (background + entities)
        chroma_img = self._make_chroma_entity_image()
        bg_img = _make_background_image()

        def _make_image_resp(img_bytes):
            mock_inline = MagicMock()
            mock_inline.inline_data = MagicMock()
            mock_inline.inline_data.data = img_bytes
            mock_content = MagicMock()
            mock_content.parts = [mock_inline]
            mock_candidate = MagicMock()
            mock_candidate.content = mock_content
            resp = MagicMock()
            resp.candidates = [mock_candidate]
            return resp

        bg_resp = _make_image_resp(bg_img)
        entity_resp = _make_image_resp(chroma_img)

        # Mask response
        mask_resp = MagicMock()
        mask_resp.text = json.dumps(FAKE_MASK_RESPONSE)

        manifest_call_done = {"done": False}

        async def fake_generate(*args, **kwargs):
            model = kwargs.get("model", "")
            if model == IMAGE_MODEL_ID:
                # Determine if background or entity by checking aspect ratio config
                config = kwargs.get("config", None)
                if config and hasattr(config, "image_config"):
                    img_cfg = config.image_config
                    if hasattr(img_cfg, "aspect_ratio") and img_cfg.aspect_ratio == "16:9":
                        return bg_resp
                return entity_resp
            elif not manifest_call_done["done"]:
                manifest_call_done["done"] = True
                return manifest_resp
            else:
                # Mask calls
                return mask_resp

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(side_effect=fake_generate)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio
        return mock_client

    def test_full_pipeline_returns_complete_scene(self):
        mock_client = self._make_pipeline_mock_client()

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    story_state=None,
                    theme="a playground in a park",
                    use_reference_images=True,
                )
            )

        assert result["manifest"]["scene_id"] == "scene_01"
        assert len(result["narrative_text"]) > 0
        assert result["scene_description"] != ""
        assert "background_description" in result

        # Should have sprite_code with bg and entities
        sc = result["sprite_code"]
        assert "bg" in sc
        assert sc["bg"]["format"] == "image_background"

        # Entities should be raw_sprite format
        assert "fox_01" in sc
        assert sc["fox_01"]["format"] == "raw_sprite"
        assert "rock_01" in sc
        assert sc["rock_01"]["format"] == "raw_sprite"

    def test_pipeline_makes_multiple_api_calls(self):
        mock_client = self._make_pipeline_mock_client()

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    story_state=None,
                    theme="a playground in a park",
                    use_reference_images=True,
                )
            )

        # Should make multiple calls:
        # 1 (manifest) + 1 (bg image) + N (entity images) + N (masks)
        # FAKE_MANIFEST_RESPONSE has 2 non-carried-over entities
        # So: 1 + 1 + 2 + 2 = 6 calls
        assert mock_client.aio.models.generate_content.call_count == 6

    def test_pipeline_raises_on_manifest_error(self):
        """If manifest generation fails on all retries, exception propagates."""
        async def always_fail(*args, **kwargs):
            raise RuntimeError("Manifest generation failed")

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(side_effect=always_fail)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Manifest generation failed"):
                asyncio.get_event_loop().run_until_complete(
                    generate_scene(
                        api_key="fake-key",
                        theme="a playground in a park",
                        use_reference_images=True,
                    )
                )

    def test_pipeline_commits_to_story_state(self):
        mock_client = self._make_pipeline_mock_client()
        state = StoryState(session_id="s1", participant_id="P01")

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    story_state=state,
                    theme="a playground in a park",
                    use_reference_images=True,
                    commit_to_state=True,
                )
            )

        assert len(state.scenes) == 1
        assert state.scenes[0]["scene_id"] == "scene_01"
        assert "fox_01" in state.active_entities


# ---------------------------------------------------------------------------
# _sanitize_for_isolation — strip cross-entity references
# ---------------------------------------------------------------------------

class TestSanitizeForIsolation:
    """Test _sanitize_for_isolation strips cross-entity references."""

    def test_empty_string(self):
        assert _sanitize_for_isolation("") == ""

    def test_none_returns_none(self):
        assert _sanitize_for_isolation(None) is None

    def test_no_references_unchanged(self):
        text = "crouched low with haunches tensed, ready to spring, ears pinned flat"
        assert _sanitize_for_isolation(text) == text

    def test_strips_against_the_tree_trunk(self):
        text = "standing on hind legs with front paws resting against the tree trunk, head tilted up"
        result = _sanitize_for_isolation(text)
        assert "tree" not in result.lower()
        assert "head tilted up" in result

    def test_strips_pinned_against_bark(self):
        text = "pinned flat against the rough bark, fluttering slightly in the breeze"
        result = _sanitize_for_isolation(text)
        assert "bark" not in result.lower()
        assert "fluttering" in result

    def test_strips_sprouting_from_roots(self):
        text = "sprouting upward from the gnarled roots of the oak"
        result = _sanitize_for_isolation(text)
        assert "roots" not in result.lower()
        assert "oak" not in result.lower()

    def test_strips_stuck_to_tree(self):
        text = "the map pulses with a soft blue light and is stuck to the tree by a silver pin"
        result = _sanitize_for_isolation(text)
        assert "tree" not in result.lower()
        assert "blue light" in result

    def test_strips_on_the_rock(self):
        text = "sitting on the mossy rock, tail curled around body"
        result = _sanitize_for_isolation(text)
        assert "rock" not in result.lower()
        assert "tail curled" in result

    def test_strips_beside_the_fence(self):
        text = "standing upright beside the wooden fence, looking ahead"
        result = _sanitize_for_isolation(text)
        assert "fence" not in result.lower()
        assert "looking ahead" in result

    def test_strips_leaning_against(self):
        text = "leaning against the tall oak, arms crossed"
        result = _sanitize_for_isolation(text)
        assert "oak" not in result.lower()
        assert "arms crossed" in result

    def test_strips_hanging_from(self):
        text = "hanging from the branch, swinging gently"
        result = _sanitize_for_isolation(text)
        assert "branch" not in result.lower()
        assert "swinging gently" in result

    def test_preserves_intrinsic_on(self):
        """'on' as part of intrinsic description should ideally be kept."""
        text = "standing on hind legs, front paws raised"
        result = _sanitize_for_isolation(text)
        # "on hind legs" doesn't match "on the <noun>" pattern
        assert "hind legs" in result


# ---------------------------------------------------------------------------
# _build_entity_description — sanitized entity descriptions
# ---------------------------------------------------------------------------

class TestBuildEntityDescription:
    """Test _build_entity_description sanitizes cross-entity references."""

    def test_basic_entity(self):
        entity = {
            "type": "cat",
            "properties": {
                "color": "orange",
                "size": "small",
                "texture": "fluffy",
            },
        }
        desc = _build_entity_description(entity)
        assert "orange" in desc
        assert "cat" in desc

    def test_pose_with_cross_reference_is_sanitized(self):
        entity = {
            "type": "fox",
            "properties": {
                "color": "orange",
                "size": "small",
                "texture": "fluffy",
            },
            "pose": "standing on hind legs with paws resting against the tree trunk, head tilted up",
        }
        desc = _build_entity_description(entity)
        assert "fox" in desc
        assert "tree" not in desc.lower()
        assert "trunk" not in desc.lower()

    def test_distinctive_features_with_cross_reference_is_sanitized(self):
        entity = {
            "type": "map",
            "properties": {
                "color": "yellow",
                "size": "small",
                "texture": "parchment",
                "distinctive_features": "stuck to the tree by a silver pin",
            },
        }
        desc = _build_entity_description(entity)
        assert "map" in desc
        assert "tree" not in desc.lower()

    def test_clean_pose_preserved(self):
        entity = {
            "type": "rabbit",
            "properties": {
                "color": "brown",
                "size": "small",
                "texture": "fluffy",
            },
            "pose": "crouched low, ears pinned flat, ready to spring",
            "emotion": "alert",
        }
        desc = _build_entity_description(entity)
        assert "crouched low" in desc
        assert "ears pinned flat" in desc
        assert "alert" in desc


# ---------------------------------------------------------------------------
# Background carry-over — StoryState stores and retrieves bg
# ---------------------------------------------------------------------------

class TestBackgroundCarryOver:
    """Test that backgrounds are stored in StoryState and reusable."""

    def test_story_state_stores_bg(self):
        """add_scene stores the bg sprite_code entry."""
        state = StoryState(session_id="s1", participant_id="P01")
        fake_bg = {
            "format": "image_background",
            "x": 0, "y": 0,
            "width": 1120, "height": 720,
            "image_base64": "AAAA",
        }
        state.add_scene(
            scene_id="scene_01",
            narrative_text="test",
            manifest=FAKE_INITIAL_RESPONSE["manifest"],
            neg=FAKE_INITIAL_RESPONSE["neg"],
            sprite_code={"bg": fake_bg, "fox_01": "some code"},
        )
        stored = state.get_entity_sprite("bg")
        assert stored is not None
        assert stored["format"] == "image_background"
        assert stored["image_base64"] == "AAAA"

    def test_story_state_no_bg_returns_none(self):
        """get_entity_sprite returns None when no bg stored."""
        state = StoryState(session_id="s1", participant_id="P01")
        assert state.get_entity_sprite("bg") is None

    def test_validate_background_changed_false_passthrough(self):
        """background_changed=false flows through validation."""
        result = _validate_scene_response(FAKE_CONTINUATION_RESPONSE)
        assert result["background_changed"] is False
