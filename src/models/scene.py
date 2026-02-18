from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Position(BaseModel):
    x: int
    y: int
    spatial_ref: Optional[str] = None


class Entity(BaseModel):
    id: str
    type: str
    properties: Dict[str, str] = Field(default_factory=dict)
    position: Position
    emotion: Optional[str] = None
    pose: Optional[str] = None
    carried_over: bool = False
    width_hint: Optional[int] = None
    height_hint: Optional[int] = None


class Relation(BaseModel):
    entity_a: str
    entity_b: str
    type: str
    preposition: str


class Action(BaseModel):
    entity_id: str
    verb: str
    tense: str = "present"
    manner: Optional[str] = None


class SceneManifest(BaseModel):
    scene_id: str
    entities: List[Entity] = Field(default_factory=list)
    relations: List[Relation] = Field(default_factory=list)
    actions: List[Action] = Field(default_factory=list)

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        for e in self.entities:
            if e.id == entity_id:
                return e
        return None

    def entity_ids(self) -> List[str]:
        return [e.id for e in self.entities]
