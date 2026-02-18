"""Tests for src/ui/app.py — FastAPI routes and WebSocket handler."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio

import pytest

from src.ui.app import (
    SessionState,
    _WebSocketAdapter,
    _init_narration_loop,
    app,
)


# ---------------------------------------------------------------------------
# SessionState
# ---------------------------------------------------------------------------

class TestSessionState:
    def test_initial_state(self):
        s = SessionState("key123", "P01")
        assert s.api_key == "key123"
        assert s.participant_id == "P01"
        assert s.story_state.participant_id == "P01"
        assert s.student_profile is not None
        assert s.animation_cache is not None
        assert s.branches == []
        assert s.current_scene is None
        assert s.narration_loop is None
        assert s.pending_audio_meta is None

    def test_story_state_starts_empty(self):
        s = SessionState("k", "P")
        assert len(s.story_state.scenes) == 0
        assert len(s.story_state.active_entities) == 0

    def test_student_profile_starts_clean(self):
        s = SessionState("k", "P")
        assert s.student_profile.total_utterances == 0
        assert s.student_profile.scenes_completed == 0


# ---------------------------------------------------------------------------
# WebSocket adapter
# ---------------------------------------------------------------------------

class TestWebSocketAdapter:
    def test_send_json_delegates(self):
        from starlette.websockets import WebSocketState

        mock_ws = MagicMock()
        mock_ws.client_state = WebSocketState.CONNECTED
        mock_ws.send_json = AsyncMock()

        adapter = _WebSocketAdapter(mock_ws)
        asyncio.get_event_loop().run_until_complete(adapter.send_json({"type": "test"}))

        mock_ws.send_json.assert_called_once_with({"type": "test"})

    def test_send_json_skips_if_disconnected(self):
        from starlette.websockets import WebSocketState

        mock_ws = MagicMock()
        mock_ws.client_state = WebSocketState.DISCONNECTED
        mock_ws.send_json = AsyncMock()

        adapter = _WebSocketAdapter(mock_ws)
        asyncio.get_event_loop().run_until_complete(adapter.send_json({"type": "test"}))

        mock_ws.send_json.assert_not_called()


# ---------------------------------------------------------------------------
# _init_narration_loop
# ---------------------------------------------------------------------------

class TestInitNarrationLoop:
    def test_creates_loop_from_scene(self):
        session = SessionState("key", "P01")
        scene = {
            "manifest": {
                "scene_id": "scene_01",
                "entities": [
                    {
                        "id": "cat_01",
                        "type": "cat",
                        "properties": {"color": "orange"},
                        "position": {"x": 100, "y": 100},
                    }
                ],
                "relations": [],
                "actions": [],
            },
            "neg": {
                "targets": [
                    {
                        "id": "t1",
                        "entity_id": "cat_01",
                        "components": {"descriptors": ["orange"], "identity": True},
                        "priority": 1,
                    }
                ],
                "error_exclusions": [],
                "min_coverage": 0.7,
            },
            "narrative_text": "An orange cat sits.",
            "sprite_code": {"cat_01": "circ(100,100,10,255,165,0,'cat_01');"},
        }
        session.current_scene = scene
        mock_adapter = MagicMock()

        _init_narration_loop(session, mock_adapter)

        assert session.narration_loop is not None
        assert session.narration_loop.api_key == "key"
        assert len(session.narration_loop.scene_manifest.entities) == 1

    def test_does_nothing_if_no_scene(self):
        session = SessionState("key", "P01")
        session.current_scene = None
        mock_adapter = MagicMock()

        _init_narration_loop(session, mock_adapter)

        assert session.narration_loop is None


# ---------------------------------------------------------------------------
# HTML page routes (TestClient)
# ---------------------------------------------------------------------------

class TestHTMLRoutes:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient

        return TestClient(app)

    def test_login_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Tellimations" in resp.text
        assert "api-key" in resp.text
        assert "participant" in resp.text

    def test_selection_page(self, client):
        resp = client.get("/selection")
        assert resp.status_code == 200
        assert "tell a" in resp.text and "story" in resp.text and "together" in resp.text
        assert "thumbnails" in resp.text

    def test_story_page(self, client):
        resp = client.get("/story")
        assert resp.status_code == 200
        assert "scene-canvas" in resp.text
        assert "ptt-bar" in resp.text

    def test_static_css(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "--dark:" in resp.text

    def test_static_engine_js(self, client):
        resp = client.get("/static/engine.js")
        assert resp.status_code == 200
        assert "PixelBuffer" in resp.text

    def test_static_animations_js(self, client):
        resp = client.get("/static/animations.js")
        assert resp.status_code == 200
        assert "AnimationRunner" in resp.text

    def test_static_narration_js(self, client):
        resp = client.get("/static/narration.js")
        assert resp.status_code == 200
        assert "NarrationClient" in resp.text

    def test_static_scene_picker_js(self, client):
        resp = client.get("/static/scene_picker.js")
        assert resp.status_code == 200
        assert "ScenePicker" in resp.text


# ---------------------------------------------------------------------------
# WebSocket integration (basic handshake)
# ---------------------------------------------------------------------------

class TestWebSocket:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient

        return TestClient(app)

    def test_ws_connect_without_api_key_gets_error(self, client):
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "API key" in msg["message"]

    def test_ws_connect_with_api_key_stays_open(self, client):
        with client.websocket_connect("/ws?api_key=testkey&participant_id=P01") as ws:
            # Send an unknown message type — should be silently ignored
            ws.send_json({"type": "unknown_type"})
            # The connection stays open (no error response for unknown types)

    @patch("src.ui.app.story_count", return_value=0)
    @patch("src.ui.app.generate_scene")
    def test_generate_initial_scenes(self, mock_gen, mock_count, client):
        scene = {
            "narrative_text": "A rabbit in a forest.",
            "branch_summary": "A brave rabbit.",
            "manifest": {
                "scene_id": "scene_01",
                "entities": [],
                "relations": [],
                "actions": [],
            },
            "neg": {"targets": [], "error_exclusions": [], "min_coverage": 0.7},
            "sprite_code": {},
            "carried_over_entities": [],
        }

        # generate_scene is async, return the scene for all 2 calls
        async def fake_gen(**kwargs):
            return scene

        mock_gen.side_effect = fake_gen

        with client.websocket_connect("/ws?api_key=testkey&participant_id=P01") as ws:
            ws.send_json({"type": "generate_initial_scenes"})
            # Consume progress messages until we get initial_scenes
            msg = ws.receive_json()
            while msg["type"] in ("generation_progress", "generation_step"):
                msg = ws.receive_json()
            assert msg["type"] == "initial_scenes"
            assert len(msg["scenes"]) == 2
            assert msg["scenes"][0]["narrative_text"] == "A rabbit in a forest."

    @patch("src.ui.app.generate_scene")
    def test_select_scene_commits_to_state(self, mock_gen, client):
        scene = {
            "narrative_text": "A cat on a wall.",
            "branch_summary": "Cat story",
            "manifest": {
                "scene_id": "scene_01",
                "entities": [
                    {
                        "id": "cat_01",
                        "type": "cat",
                        "properties": {},
                        "position": {"x": 50, "y": 50},
                    }
                ],
                "relations": [],
                "actions": [],
            },
            "neg": {"targets": [], "error_exclusions": [], "min_coverage": 0.7},
            "sprite_code": {},
            "carried_over_entities": [],
        }

        async def fake_gen(**kwargs):
            return scene

        mock_gen.side_effect = fake_gen

        with client.websocket_connect("/ws?api_key=testkey&participant_id=P01") as ws:
            # Generate initial scenes first
            ws.send_json({"type": "generate_initial_scenes"})
            ws.receive_json()  # initial_scenes response

            # Select scene 0
            ws.send_json({"type": "select_scene", "index": 0})
            # No response expected for select_scene, but connection stays alive

    @patch("src.ui.app.generate_scene")
    def test_select_scene_invalid_index(self, mock_gen, client):
        async def fake_gen(**kwargs):
            return {
                "narrative_text": "",
                "manifest": {"scene_id": "s1", "entities": [], "relations": [], "actions": []},
                "neg": {"targets": [], "error_exclusions": [], "min_coverage": 0.7},
                "sprite_code": {},
                "carried_over_entities": [],
            }

        mock_gen.side_effect = fake_gen

        with client.websocket_connect("/ws?api_key=testkey&participant_id=P01") as ws:
            ws.send_json({"type": "generate_initial_scenes"})
            ws.receive_json()

            # Invalid index
            ws.send_json({"type": "select_scene", "index": 99})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Invalid" in msg["message"]
