"""Tests for scene_generator and scene_prompt."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.neg import NEG
from src.models.scene import SceneManifest
from src.models.story_state import ActiveEntity, StoryState
from src.models.student_profile import StudentProfile
from src.generation.scene_generator import (
    _build_continuation_prompt,
    _build_initial_prompt,
    _build_scene_image_prompt,
    _build_sprite_user_prompt,
    _extract_json,
    _generate_manifest,
    _generate_scene_image,
    _generate_sprite_code,
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
        "error_exclusions": [
            {
                "entity_id": "rock_01",
                "excluded": ["QUANTITY", "ACTION", "MANNER"],
                "reason": "unique static object",
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
        "error_exclusions": [],
        "min_coverage": 0.7,
        "skill_coverage_check": "PASS",
    },
    "sprite_code": {
        "pond_01": "const eid='pond_01';\nellip(140,152,22,6,50,100,200,eid+'.surface');\nellip(140,154,20,4,40,80,180,eid+'.surface.deep');\nellip(140,150,22,3,70,130,220,eid+'.surface.highlight');\npx(130,151,80,150,230,eid+'.surface.ripple1');\npx(145,151,80,150,230,eid+'.surface.ripple2');\npx(138,153,60,100,190,eid+'.surface.ripple3');\nellip(150,148,4,2,30,100,30,eid+'.lilypad');\npx(152,147,40,120,40,eid+'.lilypad.vein');",
        "frog_01": "const eid='frog_01';\nellip(150,146,4,3,50,160,50,eid+'.body');\ncirc(147,143,2,50,170,50,eid+'.head');\ncirc(146,141,1,0,0,0,eid+'.head.eyes.left');\ncirc(149,141,1,0,0,0,eid+'.head.eyes.right');\npx(147,144,30,130,30,eid+'.head.mouth');\nrect(146,149,2,2,40,140,40,eid+'.legs.front');\nrect(152,149,2,2,40,140,40,eid+'.legs.back');\npx(148,148,60,180,60,eid+'.body.belly');",
    },
    "carried_over_entities": ["fox_01", "rock_01"],
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
        assert "280" in SCENE_SYSTEM_PROMPT
        assert "180" in SCENE_SYSTEM_PROMPT

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

    def test_initial_prompt_includes_seed_and_objectives(self):
        prompt = _build_initial_prompt(["descriptive_adjectives", "spatial_prepositions"], 2)
        assert "descriptive_adjectives" in prompt
        assert "spatial_prepositions" in prompt
        assert "2" in prompt

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

    def test_validate_continuation_response(self):
        result = _validate_scene_response(FAKE_CONTINUATION_RESPONSE)
        assert result["manifest"]["scene_id"] == "scene_02"
        assert "fox_01" in result["carried_over_entities"]
        assert "rock_01" in result["carried_over_entities"]
        # New entities have sprite code
        assert "pond_01" in result["sprite_code"]
        assert "frog_01" in result["sprite_code"]
        # Carried-over entities do NOT have sprite code
        assert "fox_01" not in result["sprite_code"]

    def test_validate_missing_manifest_raises(self):
        with pytest.raises(ValueError, match="manifest"):
            _validate_scene_response({"neg": {}, "sprite_code": {}})

    def test_validate_missing_neg_raises(self):
        with pytest.raises(ValueError, match="neg"):
            _validate_scene_response({
                "manifest": {"scene_id": "s1", "entities": [], "relations": [], "actions": []},
            })


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
                    seed_index=1,
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
                    seed_index=3,
                    use_reference_images=False,
                )
            )

        assert result["manifest"]["scene_id"] == "scene_01"

    def test_gemini_called_with_correct_model(self):
        mock_client = _make_mock_client(FAKE_INITIAL_RESPONSE)

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client) as mock_cls:
            asyncio.get_event_loop().run_until_complete(
                generate_scene(api_key="test-key", seed_index=1, use_reference_images=False)
            )

        # Verify Client was created with the API key
        mock_cls.assert_called_once_with(api_key="test-key")

        # Verify generate_content was called
        call_args = mock_client.aio.models.generate_content.call_args
        assert call_args is not None
        assert call_args.kwargs["model"] == "gemini-3-flash-preview"

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
                generate_scene(api_key="fake-key", seed_index=1, use_reference_images=False)
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
        assert "280" in MANIFEST_SYSTEM_PROMPT
        assert "180" in MANIFEST_SYSTEM_PROMPT

    def test_manifest_prompt_requires_rich_descriptions(self):
        assert "distinctive_features" in MANIFEST_SYSTEM_PROMPT
        assert "texture" in MANIFEST_SYSTEM_PROMPT
        assert "pose" in MANIFEST_SYSTEM_PROMPT

    def test_manifest_prompt_has_neg_selfcheck(self):
        assert "skill_coverage_check" in MANIFEST_SYSTEM_PROMPT
        assert "ENRICH" in MANIFEST_SYSTEM_PROMPT

    def test_manifest_prompt_has_scene_description_field(self):
        assert "scene_description" in MANIFEST_SYSTEM_PROMPT


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
        "error_exclusions": [
            {"entity_id": "rock_01", "excluded": ["QUANTITY", "ACTION"], "reason": "unique static object"}
        ],
        "min_coverage": 0.7,
        "skill_coverage_check": "PASS",
    },
    "carried_over_entities": [],
}

FAKE_SPRITE_CODE_RESPONSE = {
    "sprite_code": {
        "bg": "for(var y=0;y<170;y++) for(var x=0;x<PW;x++) px(x,y,135,190,220,'sky');",
        "fox_01": "const eid='fox_01';\nellip(80,230,22,12,200,80,48,eid+'.body');\ncirc(60,220,12,205,85,50,eid+'.head');\ncirc(57,218,2,15,10,8,eid+'.head.eyes.left');\ncirc(63,218,2,15,10,8,eid+'.head.eyes.right');\npx(57,216,255,255,255,eid+'.head.eyes.left');\npx(63,216,255,255,255,eid+'.head.eyes.right');\ntri(52,216,54,206,56,216,200,80,48,eid+'.head.ears.left');\ntri(64,216,66,206,68,216,200,80,48,eid+'.head.ears.right');\nellip(106,226,14,6,200,80,48,eid+'.tail');",
        "rock_01": "const eid='rock_01';\nellip(140,245,20,12,80,80,80,eid+'.body');\nellip(140,243,16,10,100,98,95,eid+'.body');\npx(130,240,60,110,50,eid+'.moss');\npx(135,239,60,110,50,eid+'.moss');\npx(140,241,60,110,50,eid+'.moss');\npx(145,240,60,110,50,eid+'.moss');\npx(150,241,60,110,50,eid+'.moss');\npx(140,238,118,115,110,eid+'.body.highlight');",
    }
}

FAKE_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # Fake PNG header


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


class TestBuildSceneImagePrompt:
    def test_includes_scene_description(self):
        prompt = _build_scene_image_prompt(FAKE_MANIFEST_RESPONSE)
        assert "warm sunlit forest clearing" in prompt

    def test_includes_entity_descriptions(self):
        prompt = _build_scene_image_prompt(FAKE_MANIFEST_RESPONSE)
        assert "fox" in prompt.lower()
        assert "rock" in prompt.lower()

    def test_includes_emotions_and_poses(self):
        prompt = _build_scene_image_prompt(FAKE_MANIFEST_RESPONSE)
        assert "curious" in prompt
        assert "sitting upright" in prompt


class TestGenerateSceneImage:
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
            _generate_scene_image(mock_client, FAKE_MANIFEST_RESPONSE)
        )

        assert result == FAKE_IMAGE_BYTES

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
            _generate_scene_image(mock_client, FAKE_MANIFEST_RESPONSE)
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
            _generate_scene_image(mock_client, FAKE_MANIFEST_RESPONSE)
        )

        assert result is None


class TestGenerateSpriteCode:
    def test_returns_sprite_code_dict(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps(FAKE_SPRITE_CODE_RESPONSE)
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        result = asyncio.get_event_loop().run_until_complete(
            _generate_sprite_code(
                mock_client, FAKE_MANIFEST_RESPONSE, FAKE_IMAGE_BYTES, [], None
            )
        )

        assert "bg" in result
        assert "fox_01" in result
        assert "rock_01" in result

    def test_uses_multimodal_input_with_image(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps(FAKE_SPRITE_CODE_RESPONSE)
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        asyncio.get_event_loop().run_until_complete(
            _generate_sprite_code(
                mock_client, FAKE_MANIFEST_RESPONSE, FAKE_IMAGE_BYTES, [], None
            )
        )

        call_args = mock_models.generate_content.call_args
        contents = call_args.kwargs["contents"]
        # Should be a list with at least 2 items: text prompt + image
        assert isinstance(contents, list)
        assert len(contents) >= 2

    def test_works_without_image(self):
        """If image generation fails, sprite code should still be generated."""
        mock_response = MagicMock()
        mock_response.text = json.dumps(FAKE_SPRITE_CODE_RESPONSE)
        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        result = asyncio.get_event_loop().run_until_complete(
            _generate_sprite_code(
                mock_client, FAKE_MANIFEST_RESPONSE, None, [], None  # No image
            )
        )

        assert "fox_01" in result

        # Verify contents is text-only (no image part)
        call_args = mock_models.generate_content.call_args
        contents = call_args.kwargs["contents"]
        assert isinstance(contents, list)
        assert len(contents) == 1  # Just the text prompt


class TestPipelineIntegration:
    """Test the full 3-step pipeline with all mocks."""

    def _make_pipeline_mock_client(self):
        """Create a mock client that handles all 3 pipeline steps."""
        # Step 1: manifest response
        manifest_resp = MagicMock()
        manifest_resp.text = json.dumps(FAKE_MANIFEST_RESPONSE)

        # Step 2: image response
        mock_inline = MagicMock()
        mock_inline.inline_data = MagicMock()
        mock_inline.inline_data.data = FAKE_IMAGE_BYTES
        mock_content = MagicMock()
        mock_content.parts = [mock_inline]
        mock_candidate = MagicMock()
        mock_candidate.content = mock_content
        image_resp = MagicMock()
        image_resp.candidates = [mock_candidate]

        # Step 3: sprite code response
        sprite_resp = MagicMock()
        sprite_resp.text = json.dumps(FAKE_SPRITE_CODE_RESPONSE)

        call_count = {"n": 0}

        async def fake_generate(*args, **kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            model = kwargs.get("model", "")
            if model == IMAGE_MODEL_ID:
                return image_resp
            elif idx == 0:
                return manifest_resp
            else:
                return sprite_resp

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
                    seed_index=1,
                    use_reference_images=True,
                )
            )

        assert result["manifest"]["scene_id"] == "scene_01"
        assert "bg" in result["sprite_code"]
        assert "fox_01" in result["sprite_code"]
        assert "rock_01" in result["sprite_code"]
        assert len(result["narrative_text"]) > 0
        assert result["scene_description"] != ""

    def test_pipeline_makes_3_api_calls(self):
        mock_client = self._make_pipeline_mock_client()

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    story_state=None,
                    seed_index=1,
                    use_reference_images=True,
                )
            )

        # Should make 3 calls: manifest, image, sprite code
        assert mock_client.aio.models.generate_content.call_count == 3

    def test_pipeline_falls_back_on_manifest_error(self):
        """If manifest generation fails, should fall back to legacy."""
        # Create a client where step 1 fails, then legacy works
        fail_then_succeed_count = {"n": 0}
        legacy_resp = MagicMock()
        legacy_resp.text = json.dumps(FAKE_INITIAL_RESPONSE)

        async def fail_then_legacy(*args, **kwargs):
            idx = fail_then_succeed_count["n"]
            fail_then_succeed_count["n"] += 1
            if idx == 0:
                raise RuntimeError("Manifest generation failed")
            return legacy_resp

        mock_models = AsyncMock()
        mock_models.generate_content = AsyncMock(side_effect=fail_then_legacy)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    seed_index=1,
                    use_reference_images=True,
                )
            )

        # Should still return a valid scene (via legacy fallback)
        assert result["manifest"]["scene_id"] == "scene_01"
        assert "fox_01" in result["sprite_code"]

    def test_pipeline_commits_to_story_state(self):
        mock_client = self._make_pipeline_mock_client()
        state = StoryState(session_id="s1", participant_id="P01")

        with patch("src.generation.scene_generator.genai.Client", return_value=mock_client):
            asyncio.get_event_loop().run_until_complete(
                generate_scene(
                    api_key="fake-key",
                    story_state=state,
                    seed_index=1,
                    use_reference_images=True,
                    commit_to_state=True,
                )
            )

        assert len(state.scenes) == 1
        assert state.scenes[0]["scene_id"] == "scene_01"
        assert "fox_01" in state.active_entities
