from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Background / Zone models
# ---------------------------------------------------------------------------


class BackgroundZone(BaseModel):
    """Logical depth zone within the scene."""

    id: str                  # "sky", "background", "midground", "foreground"
    y_start: float           # normalized 0-1 (top of zone, from top of canvas)
    y_end: float             # normalized 0-1 (bottom of zone)
    scale_hint: float = 1.0  # default entity scale in this zone


class StructuralElement(BaseModel):
    """A non-entity background element with position data."""

    name: str                          # e.g. "wooden counter", "tiled floor"
    x: float = 0.5                     # normalized 0-1, horizontal center
    y: float = 0.5                     # normalized 0-1, vertical center
    zone: str = "background"           # "sky", "background", "midground", "foreground"


class Background(BaseModel):
    """Scene background metadata — environment, zones, structural elements."""

    environment_type: str = "outdoor"  # "outdoor", "indoor", "themed_outdoor"
    ground_line: float = 0.7           # normalized 0-1 from top
    zones: List[BackgroundZone] = Field(default_factory=list)
    structural_elements: List[StructuralElement] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Entity models
# ---------------------------------------------------------------------------


class Position(BaseModel):
    x: float                                 # 0.0-1.0, normalized horizontal center
    y: float                                 # 0.0-1.0, normalized vertical center (0=top, 1=bottom)
    spatial_ref: Optional[str] = None
    zone: Optional[str] = None               # "foreground", "midground", "background"
    depth_order: Optional[int] = None        # rendering order (0=back, higher=front)
    ground_contact: bool = True              # whether entity touches ground


class Entity(BaseModel):
    id: str
    type: str
    name: Optional[str] = None  # Child-given name (not used for sprite generation)
    properties: Dict[str, str] = Field(default_factory=dict)
    position: Position
    emotion: Optional[str] = None
    pose: Optional[str] = None
    carried_over: bool = False
    width_hint: Optional[float] = None         # 0.0-1.0, proportion of canvas width
    height_hint: Optional[float] = None        # 0.0-1.0, proportion of canvas height
    orientation: Optional[str] = None          # "facing_left", "facing_right", "facing_viewer", or "facing:<entity_id>"
    scale_factor: Optional[float] = None       # 0.5-1.5 relative scale hint
    sensory: Optional[Dict[str, str]] = None   # {"temperature": "cold", "sound": "chirping"}


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


# ---------------------------------------------------------------------------
# Scene manifest
# ---------------------------------------------------------------------------


class SceneManifest(BaseModel):
    scene_id: str
    entities: List[Entity] = Field(default_factory=list)
    relations: List[Relation] = Field(default_factory=list)
    actions: List[Action] = Field(default_factory=list)
    background: Optional[Background] = None

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        for e in self.entities:
            if e.id == entity_id:
                return e
        return None

    def entity_ids(self) -> List[str]:
        return [e.id for e in self.entities]

    def get_main_character(self) -> Optional[Entity]:
        """Return the main character (first entity — scene gen puts it first)."""
        if self.entities:
            return self.entities[0]
        return None
