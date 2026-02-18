"""Tests for src/analytics/session_report.py and POST /api/report endpoint."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analytics.session_report import (
    MODEL_ID,
    REPORT_SYSTEM_PROMPT,
    REPORT_USER_PROMPT,
    generate_report,
)
from src.models.student_profile import StudentProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_student_profile() -> StudentProfile:
    """Build a realistic student profile for testing."""
    p = StudentProfile(
        error_counts={
            "PROPERTY_COLOR": 8,
            "OMISSION": 5,
            "SPATIAL": 2,
            "ACTION": 1,
        },
        error_trend={
            "PROPERTY_COLOR": "decreasing",
            "OMISSION": "stable",
            "SPATIAL": "insufficient_data",
        },
        difficult_entities=["cat_01", "tree_02"],
        strong_areas=["IDENTITY", "QUANTITY"],
        scenes_completed=3,
        corrections_after_animation=12,
        total_utterances=20,
    )
    return p


def _make_session_log() -> dict:
    """Build a realistic session log for testing."""
    return {
        "session_id": "sess_test_001",
        "participant_id": "P01",
        "scenes": [
            {
                "scene_id": "scene_01",
                "narrative_text": "A fluffy orange cat sits on a mossy rock.",
                "utterances": [
                    {
                        "utterance_index": 1,
                        "transcription": "the cat is sitting",
                        "discrepancies": [
                            {
                                "type": "PROPERTY_COLOR",
                                "entity_id": "cat_01",
                                "sub_entity": "cat_01.body",
                                "details": "Missing color 'orange'",
                                "severity": 0.7,
                            },
                            {
                                "type": "OMISSION",
                                "entity_id": "rock_01",
                                "sub_entity": "rock_01",
                                "details": "Rock not mentioned",
                                "severity": 0.5,
                            },
                        ],
                        "scene_progress": 0.3,
                        "satisfied_targets": ["t1_identity"],
                        "animations_dispatched": 2,
                        "animations_cached": 0,
                        "animations_generated": 2,
                    },
                    {
                        "utterance_index": 2,
                        "transcription": "the orange cat is on the rock",
                        "discrepancies": [],
                        "scene_progress": 0.8,
                        "satisfied_targets": ["t1_identity", "t1_color", "t1_spatial"],
                        "animations_dispatched": 0,
                        "animations_cached": 0,
                        "animations_generated": 0,
                    },
                ],
                "animations_fired": [
                    {
                        "error_type": "PROPERTY_COLOR",
                        "entity_id": "cat_01",
                        "sub_entity": "cat_01.body",
                        "corrected_after": True,
                    },
                    {
                        "error_type": "OMISSION",
                        "entity_id": "rock_01",
                        "sub_entity": "rock_01",
                        "corrected_after": True,
                    },
                ],
            },
            {
                "scene_id": "scene_02",
                "narrative_text": "A grey speckled owl watches from a tall tree.",
                "utterances": [
                    {
                        "utterance_index": 1,
                        "transcription": "there is a bird in the tree",
                        "discrepancies": [
                            {
                                "type": "PROPERTY_COLOR",
                                "entity_id": "owl_01",
                                "sub_entity": "owl_01.body",
                                "details": "Missing color 'grey'",
                                "severity": 0.6,
                            },
                            {
                                "type": "IDENTITY",
                                "entity_id": "owl_01",
                                "sub_entity": "owl_01",
                                "details": "Called 'bird' instead of 'owl'",
                                "severity": 0.8,
                            },
                        ],
                        "scene_progress": 0.25,
                        "satisfied_targets": ["t2_spatial"],
                        "animations_dispatched": 2,
                        "animations_cached": 0,
                        "animations_generated": 2,
                    },
                    {
                        "utterance_index": 2,
                        "transcription": "the grey owl is watching from the tree",
                        "discrepancies": [],
                        "scene_progress": 0.75,
                        "satisfied_targets": ["t2_spatial", "t2_identity", "t2_color", "t2_action"],
                        "animations_dispatched": 0,
                        "animations_cached": 0,
                        "animations_generated": 0,
                    },
                ],
                "animations_fired": [
                    {
                        "error_type": "PROPERTY_COLOR",
                        "entity_id": "owl_01",
                        "sub_entity": "owl_01.body",
                        "corrected_after": True,
                    },
                    {
                        "error_type": "IDENTITY",
                        "entity_id": "owl_01",
                        "sub_entity": "owl_01",
                        "corrected_after": True,
                    },
                ],
            },
            {
                "scene_id": "scene_03",
                "narrative_text": "The rabbit hops over a small stream.",
                "utterances": [
                    {
                        "utterance_index": 1,
                        "transcription": "the rabbit is going to the water",
                        "discrepancies": [
                            {
                                "type": "ACTION",
                                "entity_id": "rabbit_01",
                                "sub_entity": "rabbit_01",
                                "details": "'going' instead of 'hopping'",
                                "severity": 0.4,
                            },
                        ],
                        "scene_progress": 0.5,
                        "satisfied_targets": ["t3_identity"],
                        "animations_dispatched": 1,
                        "animations_cached": 0,
                        "animations_generated": 1,
                    },
                    {
                        "utterance_index": 2,
                        "transcription": "the rabbit hops over the stream",
                        "discrepancies": [],
                        "scene_progress": 0.9,
                        "satisfied_targets": ["t3_identity", "t3_action", "t3_spatial"],
                        "animations_dispatched": 0,
                        "animations_cached": 0,
                        "animations_generated": 0,
                    },
                ],
                "animations_fired": [
                    {
                        "error_type": "ACTION",
                        "entity_id": "rabbit_01",
                        "sub_entity": "rabbit_01",
                        "corrected_after": True,
                    },
                ],
                "hesitation_events": [
                    {
                        "target_entity": "stream_01",
                        "animation_sent": True,
                    },
                ],
            },
        ],
    }


def _make_mock_client(report_text: str):
    """Create a mock genai.Client that returns report_text."""
    mock_response = MagicMock()
    mock_response.text = report_text
    mock_models = AsyncMock()
    mock_models.generate_content = AsyncMock(return_value=mock_response)
    mock_aio = MagicMock()
    mock_aio.models = mock_models
    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client


SAMPLE_REPORT = """\
# Session Report

## 1. Recurring Error Patterns
The child most frequently omitted color descriptors (PROPERTY_COLOR: 8 \
occurrences across 20 utterances, rate 0.40). OMISSION errors were the \
second most common (5 occurrences). The child consistently forgot to \
describe the color of animals (cat_01, owl_01).

SPATIAL errors were rare (2 occurrences), and ACTION errors occurred only \
once. IDENTITY and QUANTITY were strong areas with near-zero error rates.

## 2. Animation Effectiveness
| Error Type | Firings | Corrections | Rate |
|---|---|---|---|
| PROPERTY_COLOR | 2 | 2 | 100% |
| OMISSION | 1 | 1 | 100% |
| IDENTITY | 1 | 1 | 100% |
| ACTION | 1 | 1 | 100% |

All animation types proved effective — the child corrected after every \
animation (12 total corrections). The color_pop animation for \
PROPERTY_COLOR was particularly effective.

## 3. SKILL Progress (Scene by Scene)
- **Scene 1** (cat on rock): Started with PROPERTY_COLOR + OMISSION. \
  Corrected both after animation. Progress: 0.3 → 0.8.
- **Scene 2** (owl in tree): PROPERTY_COLOR + IDENTITY errors. Both \
  corrected. Progress: 0.25 → 0.75.
- **Scene 3** (rabbit + stream): Only ACTION error. Corrected quickly. \
  Progress: 0.5 → 0.9. One hesitation event (stream_01).

Trajectory: improving. Error count per scene decreased from 2 to 1.

## 4. Student-Profile Adaptation Impact
Scene 2 introduced more descriptive targets (owl with grey speckled \
pattern) matching the child's PROPERTY_COLOR weakness. Scene 3 shifted \
focus to ACTION verbs. The adaptation appears to have provided targeted \
practice.

## 5. Recommendations for Next Session
- Continue emphasising **descriptive_adjectives** (PROPERTY_COLOR still \
  the highest error rate at 0.40, though trending down)
- Introduce more complex spatial relationships to build on the child's \
  existing spatial strength
- Consider reducing animation intensity for PROPERTY_COLOR since the \
  trend is decreasing
- Maintain ACTION verb scaffolding at current level
"""


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------

class TestPromptContent:
    def test_system_prompt_has_five_sections(self):
        assert "## 1. Recurring Error Patterns" in REPORT_SYSTEM_PROMPT
        assert "## 2. Animation Effectiveness" in REPORT_SYSTEM_PROMPT
        assert "## 3. SKILL Progress" in REPORT_SYSTEM_PROMPT
        assert "## 4. Student-Profile Adaptation Impact" in REPORT_SYSTEM_PROMPT
        assert "## 5. Recommendations for Next Session" in REPORT_SYSTEM_PROMPT

    def test_system_prompt_mentions_slp(self):
        assert "SLP" in REPORT_SYSTEM_PROMPT or "speech-language" in REPORT_SYSTEM_PROMPT

    def test_system_prompt_requires_markdown(self):
        assert "Markdown" in REPORT_SYSTEM_PROMPT

    def test_system_prompt_mentions_correction_rate(self):
        assert "correction" in REPORT_SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_skill(self):
        assert "SKILL" in REPORT_SYSTEM_PROMPT

    def test_user_prompt_has_placeholders(self):
        assert "{session_log_json}" in REPORT_USER_PROMPT
        assert "{student_profile_json}" in REPORT_USER_PROMPT

    def test_user_prompt_format_succeeds(self):
        result = REPORT_USER_PROMPT.format(
            session_log_json='{"test": 1}',
            student_profile_json='{"test": 2}',
        )
        assert '{"test": 1}' in result
        assert '{"test": 2}' in result


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    @pytest.fixture
    def profile(self):
        return _make_student_profile()

    @pytest.fixture
    def session_log(self):
        return _make_session_log()

    @patch("src.analytics.session_report.genai.Client")
    def test_returns_markdown_report(self, mock_client_cls, profile, session_log):
        mock_client = _make_mock_client(SAMPLE_REPORT)
        mock_client_cls.return_value = mock_client

        import asyncio
        report = asyncio.get_event_loop().run_until_complete(
            generate_report("test-key", session_log, profile)
        )

        assert isinstance(report, str)
        assert "# Session Report" in report
        assert "## 1. Recurring Error Patterns" in report
        assert "## 2. Animation Effectiveness" in report
        assert "## 3. SKILL Progress" in report
        assert "## 4. Student-Profile Adaptation Impact" in report
        assert "## 5. Recommendations for Next Session" in report

    @patch("src.analytics.session_report.genai.Client")
    def test_strips_markdown_fences(self, mock_client_cls, profile, session_log):
        fenced = "```markdown\n" + SAMPLE_REPORT + "\n```"
        mock_client = _make_mock_client(fenced)
        mock_client_cls.return_value = mock_client

        import asyncio
        report = asyncio.get_event_loop().run_until_complete(
            generate_report("test-key", session_log, profile)
        )

        assert not report.startswith("```")
        assert "# Session Report" in report

    @patch("src.analytics.session_report.genai.Client")
    def test_strips_md_fences(self, mock_client_cls, profile, session_log):
        fenced = "```md\n" + SAMPLE_REPORT + "\n```"
        mock_client = _make_mock_client(fenced)
        mock_client_cls.return_value = mock_client

        import asyncio
        report = asyncio.get_event_loop().run_until_complete(
            generate_report("test-key", session_log, profile)
        )

        assert not report.startswith("```")
        assert "# Session Report" in report

    @patch("src.analytics.session_report.genai.Client")
    def test_gemini_called_with_correct_model(self, mock_client_cls, profile, session_log):
        mock_client = _make_mock_client(SAMPLE_REPORT)
        mock_client_cls.return_value = mock_client

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            generate_report("test-key", session_log, profile)
        )

        call_args = mock_client.aio.models.generate_content.call_args
        assert call_args.kwargs["model"] == MODEL_ID

    @patch("src.analytics.session_report.genai.Client")
    def test_gemini_called_with_medium_thinking(self, mock_client_cls, profile, session_log):
        mock_client = _make_mock_client(SAMPLE_REPORT)
        mock_client_cls.return_value = mock_client

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            generate_report("test-key", session_log, profile)
        )

        call_args = mock_client.aio.models.generate_content.call_args
        config = call_args.kwargs["config"]
        assert config.thinking_config.thinking_budget == 1024

    @patch("src.analytics.session_report.genai.Client")
    def test_user_prompt_contains_session_data(self, mock_client_cls, profile, session_log):
        mock_client = _make_mock_client(SAMPLE_REPORT)
        mock_client_cls.return_value = mock_client

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            generate_report("test-key", session_log, profile)
        )

        call_args = mock_client.aio.models.generate_content.call_args
        user_prompt = call_args.kwargs["contents"]
        # Session log data should be in the prompt
        assert "scene_01" in user_prompt
        assert "PROPERTY_COLOR" in user_prompt
        assert "cat_01" in user_prompt
        # Student profile data should be in the prompt
        assert "corrections_after_animation" in user_prompt
        assert "difficult_entities" in user_prompt

    @patch("src.analytics.session_report.genai.Client")
    def test_system_prompt_passed_as_instruction(self, mock_client_cls, profile, session_log):
        mock_client = _make_mock_client(SAMPLE_REPORT)
        mock_client_cls.return_value = mock_client

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            generate_report("test-key", session_log, profile)
        )

        call_args = mock_client.aio.models.generate_content.call_args
        config = call_args.kwargs["config"]
        assert "SLP" in config.system_instruction or "speech-language" in config.system_instruction

    @patch("src.analytics.session_report.genai.Client")
    def test_api_key_forwarded(self, mock_client_cls, profile, session_log):
        mock_client = _make_mock_client(SAMPLE_REPORT)
        mock_client_cls.return_value = mock_client

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            generate_report("my-secret-key", session_log, profile)
        )

        mock_client_cls.assert_called_once_with(api_key="my-secret-key")

    @patch("src.analytics.session_report.genai.Client")
    def test_empty_session_log(self, mock_client_cls, profile):
        mock_client = _make_mock_client("# Session Report\n\nNo data.")
        mock_client_cls.return_value = mock_client

        import asyncio
        report = asyncio.get_event_loop().run_until_complete(
            generate_report("test-key", {}, profile)
        )

        assert "Session Report" in report

    @patch("src.analytics.session_report.genai.Client")
    def test_empty_profile(self, mock_client_cls, session_log):
        mock_client = _make_mock_client(SAMPLE_REPORT)
        mock_client_cls.return_value = mock_client

        empty_profile = StudentProfile()

        import asyncio
        report = asyncio.get_event_loop().run_until_complete(
            generate_report("test-key", session_log, empty_profile)
        )

        assert "Session Report" in report


# ---------------------------------------------------------------------------
# POST /api/report endpoint
# ---------------------------------------------------------------------------

class TestReportEndpoint:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from src.ui.app import app
        return TestClient(app)

    @patch("src.ui.app.generate_report")
    def test_returns_report(self, mock_gen_report, client):
        async def fake_report(api_key, session_log, student_profile):
            return SAMPLE_REPORT

        mock_gen_report.side_effect = fake_report

        resp = client.post("/api/report", json={
            "api_key": "test-key",
            "session_log": _make_session_log(),
            "student_profile": _make_student_profile().model_dump(),
        })

        assert resp.status_code == 200
        data = resp.json()
        assert "report" in data
        assert "Session Report" in data["report"]
        assert "Recurring Error Patterns" in data["report"]

    @patch("src.ui.app.generate_report")
    def test_missing_api_key_returns_400(self, mock_gen_report, client):
        resp = client.post("/api/report", json={
            "session_log": {},
            "student_profile": {},
        })

        assert resp.status_code == 400
        assert "api_key" in resp.json()["error"]
        mock_gen_report.assert_not_called()

    @patch("src.ui.app.generate_report")
    def test_generation_error_returns_500(self, mock_gen_report, client):
        async def fail(*args, **kwargs):
            raise RuntimeError("Gemini API error")

        mock_gen_report.side_effect = fail

        resp = client.post("/api/report", json={
            "api_key": "test-key",
            "session_log": {},
            "student_profile": {},
        })

        assert resp.status_code == 500
        assert "error" in resp.json()

    @patch("src.ui.app.generate_report")
    def test_empty_body_with_key(self, mock_gen_report, client):
        async def fake_report(api_key, session_log, student_profile):
            return "# Session Report\n\nMinimal."

        mock_gen_report.side_effect = fake_report

        resp = client.post("/api/report", json={
            "api_key": "test-key",
        })

        assert resp.status_code == 200
        assert "report" in resp.json()

    @patch("src.ui.app.generate_report")
    def test_profile_validated_from_dict(self, mock_gen_report, client):
        received_profile = []

        async def capture_report(api_key, session_log, student_profile):
            received_profile.append(student_profile)
            return "# Report"

        mock_gen_report.side_effect = capture_report

        profile_dict = _make_student_profile().model_dump()
        resp = client.post("/api/report", json={
            "api_key": "test-key",
            "session_log": {},
            "student_profile": profile_dict,
        })

        assert resp.status_code == 200
        assert len(received_profile) == 1
        p = received_profile[0]
        assert p.error_counts["PROPERTY_COLOR"] == 8
        assert p.total_utterances == 20
        assert "cat_01" in p.difficult_entities
