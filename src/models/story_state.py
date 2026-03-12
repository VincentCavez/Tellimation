from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

from src.models.scene import SceneManifest


class ActiveEntity(BaseModel):
    type: str
    name: str = ""  # Child-given name (persists across scenes)
    sprite_code: Union[str, Dict[str, Any]] = ""
    first_appeared: str = ""
    last_position: Dict = Field(default_factory=dict)


class StoryState(BaseModel):
    session_id: str = ""
    participant_id: str = ""
    scenes: List[Dict] = Field(default_factory=list)
    active_entities: Dict[str, ActiveEntity] = Field(default_factory=dict)

    # All entity sprites ever generated (entity_id -> raw_sprite dict).
    # Unlike active_entities which only holds the latest, this accumulates.
    sprite_archive: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    # Metadata for ALL entities ever seen (not just currently active).
    # Keyed by entity_id -> {type, properties, pose, emotion, orientation,
    #                         first_appeared, last_appeared, last_position}
    entity_history: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    def add_scene(
        self,
        scene_id: str,
        narrative_text: str,
        manifest: Dict,
        sprite_code: Optional[Dict[str, Any]] = None,
        accepted_utterances: Optional[List[str]] = None,
    ) -> None:
        self.scenes.append({
            "scene_id": scene_id,
            "narrative_text": narrative_text,
            "manifest": manifest,
            "accepted_utterances": accepted_utterances or [],
        })
        if sprite_code:
            for entity_id, code in sprite_code.items():
                if entity_id in self.active_entities:
                    self.active_entities[entity_id].sprite_code = code
                else:
                    self.active_entities[entity_id] = ActiveEntity(
                        type="",
                        sprite_code=code,
                        first_appeared=scene_id,
                    )
                # Archive every entity sprite (skip background)
                if entity_id != "bg" and isinstance(code, dict):
                    self.sprite_archive[entity_id] = code

        # Archive entity metadata from manifest
        for ent in manifest.get("entities", []):
            eid = ent.get("id", "")
            if not eid:
                continue
            pos = ent.get("position", {})
            self.entity_history[eid] = {
                "type": ent.get("type", ""),
                "properties": ent.get("properties", {}),
                "pose": ent.get("pose", ""),
                "emotion": ent.get("emotion", ""),
                "orientation": ent.get("orientation", ""),
                "first_appeared": self.entity_history.get(eid, {}).get(
                    "first_appeared", scene_id
                ),
                "last_appeared": scene_id,
                "last_position": {
                    "x": pos.get("x", 0.5),
                    "y": pos.get("y", 0.7),
                },
            }

    def get_entity_sprite(self, entity_id: str) -> Optional[Union[str, Dict[str, Any]]]:
        entity = self.active_entities.get(entity_id)
        if entity is None:
            return None
        return entity.sprite_code or None

    def get_archived_sprite(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get a sprite from the archive (includes entities no longer active)."""
        return self.sprite_archive.get(entity_id)

    def get_inactive_entities(self) -> Dict[str, Dict[str, Any]]:
        """Return entities in history but NOT in active_entities."""
        return {
            eid: info for eid, info in self.entity_history.items()
            if eid not in self.active_entities
        }

    def carry_over_entities(
        self, new_manifest: SceneManifest
    ) -> Tuple[List[str], List[str]]:
        """Return (carried_over_ids, new_ids) based on the new manifest."""
        carried_over: List[str] = []
        new: List[str] = []
        for entity in new_manifest.entities:
            if entity.carried_over and entity.id in self.active_entities:
                carried_over.append(entity.id)
                self.active_entities[entity.id].last_position = {
                    "x": entity.position.x,
                    "y": entity.position.y,
                }
            else:
                new.append(entity.id)
        return carried_over, new
