from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.models.scene import SceneManifest


class ActiveEntity(BaseModel):
    type: str
    sprite_code: str = ""
    first_appeared: str = ""
    last_position: Dict = Field(default_factory=dict)


class StoryState(BaseModel):
    session_id: str = ""
    participant_id: str = ""
    skill_objectives: List[str] = Field(default_factory=list)
    scenes: List[Dict] = Field(default_factory=list)
    active_entities: Dict[str, ActiveEntity] = Field(default_factory=dict)

    def add_scene(
        self,
        scene_id: str,
        narrative_text: str,
        manifest: Dict,
        neg: Dict,
        sprite_code: Optional[Dict[str, str]] = None,
    ) -> None:
        self.scenes.append({
            "scene_id": scene_id,
            "narrative_text": narrative_text,
            "manifest": manifest,
            "neg": neg,
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

    def get_entity_sprite(self, entity_id: str) -> Optional[str]:
        entity = self.active_entities.get(entity_id)
        if entity is None:
            return None
        return entity.sprite_code or None

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
