"""Study handler: animations whose discrepancy has no usable target.

Regression for the turn that died on `scene_data` (undefined) whenever the
chosen discrepancy had no target, which is what C3 always ended up with since
its target, an entity absent from the scene, was dropped by the sanitizer.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.models.assessment import Discrepancy


@pytest.fixture
def study(tmp_path, monkeypatch):
    """An app client with a temp data root, a stubbed transcription and no Sheet webhook."""
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    import src.persistence as persistence
    import src.ui.app as appmod

    monkeypatch.setattr(persistence, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(persistence, "GOOGLE_SHEET_WEBHOOK", "")

    async def text_as_audio(audio_bytes, known_names=None):
        """Stand-in transcription that decodes the audio bytes as text."""
        return audio_bytes.decode("utf-8")

    monkeypatch.setattr(appmod, "transcribe_audio", text_as_audio)
    monkeypatch.setattr(appmod, "assess_resolution", AsyncMock(return_value=None))
    monkeypatch.setattr(appmod, "detect_misl_elements", AsyncMock(return_value=[]))
    monkeypatch.setattr(appmod, "assess_enrichment", AsyncMock(return_value=[]))
    return appmod, TestClient(appmod.app), tmp_path


def _scene_with_absent_entity(client):
    """First scene of story A that lacks one of the story's entities."""
    for num in range(1, 6):
        scene = client.get("/api/study/scene", params={"story": "A", "scene": num}).json()
        present = scene["manifest"]["entities_in_scene"]
        absent = [e for e in scene["characters"] if e not in present]
        if absent:
            return num, scene, present, absent[0]
    pytest.skip("story A has no scene with an absent entity")


def _one_turn(appmod, client, monkeypatch, scene, num, targets):
    """Play one study turn with a stubbed C3 correction on the given targets and return the messages."""
    correction = Discrepancy(pass_type="correction", type="Count", target_entities=targets,
                             description="not in this picture", animation_id="C3")
    monkeypatch.setattr(appmod, "assess_corrections", AsyncMock(return_value=([correction], [])))
    messages = []
    with client.websocket_connect("/ws/study?participant=T1&story=A&animated=1") as ws:
        ws.send_json({"type": "study_scene_loaded", "story": "A", "scene_number": num, "scene": scene})
        ws.send_bytes(b"The bird is here.")
        while True:
            messages.append(json.loads(ws.receive()["text"]))
            if messages[-1]["type"] == "study_turn_done":
                break
    return messages


def _cycle(tmp_path, num):
    """Return the pipeline_cycle log entry of the given scene."""
    log = json.loads((tmp_path / "T1" / "study_log.json").read_text())
    return next(e for e in log["scenes"][f"A_scene_{num}"] if e["event"] == "pipeline_cycle")


def test_absent_entity_survives_and_gives_the_piece_its_silhouette(study, monkeypatch):
    """An absent entity targeted by C3 is kept and given the silhouette."""
    appmod, client, tmp_path = study
    num, scene, _, absent = _scene_with_absent_entity(client)
    messages = _one_turn(appmod, client, monkeypatch, scene, num, [absent])

    animation = next(m for m in messages if m["type"] == "animation")
    assert animation["template"] == "missing_piece"
    assert animation["target_id"] == absent
    assert absent in animation["params"]["pieceImageUrl"]
    assert _cycle(tmp_path, num)["selected"]["targets"] == [absent]


def test_no_target_falls_back_to_scene_entities_without_a_silhouette(study, monkeypatch):
    """A C3 target that is present is dropped and the scene entities are used without a silhouette."""
    appmod, client, tmp_path = study
    num, scene, present, _ = _scene_with_absent_entity(client)
    # A present entity is not a valid C3 target: it is dropped, leaving none
    messages = _one_turn(appmod, client, monkeypatch, scene, num, [present[0]])

    animation = next(m for m in messages if m["type"] == "animation")  # used to raise NameError
    assert animation["target_id"] == "|".join(present)
    assert not animation["params"].get("pieceImageUrl")
    assert _cycle(tmp_path, num)["selected"]["targets"] == []
