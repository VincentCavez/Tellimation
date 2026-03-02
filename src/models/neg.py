from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Existing NEG models (used by scene_generator, narration loop, dispatcher)
# ---------------------------------------------------------------------------


class TargetComponents(BaseModel):
    identity: bool = False
    descriptors: List[str] = Field(default_factory=list)
    spatial: Optional[str] = None
    action: Optional[str] = None
    temporal: Optional[str] = None


class NarrativeTarget(BaseModel):
    id: str
    entity_id: str
    components: TargetComponents
    priority: float = 1.0
    tolerance: float = 0.5


class NEG(BaseModel):
    targets: List[NarrativeTarget] = Field(default_factory=list)
    min_coverage: float = 0.7
    skill_coverage_check: str = "PENDING"

    def get_targets_for_entity(self, entity_id: str) -> List[NarrativeTarget]:
        return [t for t in self.targets if t.entity_id == entity_id]


# ---------------------------------------------------------------------------
# Extended NEG models for the Narrative Expectation Module
# (standalone generation via narrative_expectation.py)
# ---------------------------------------------------------------------------


class ExpectedVocabulary(BaseModel):
    """Vocabulary the child is expected to use for a waypoint."""

    keywords: List[str] = Field(default_factory=list)
    acceptable_synonyms: List[str] = Field(default_factory=list)


class ExpectedGrammar(BaseModel):
    """Grammatical structure expected for a waypoint."""

    structure: str = ""
    tense: str = "present"
    complexity: str = "simple"


class AnticipatedTrap(BaseModel):
    """A likely error based on the student's history."""

    error_type: str
    entity_name: str
    description: str = ""
    probability: float = Field(default=0.5, ge=0.0, le=1.0)
    suggested_scaffolding: str = ""


class NarrativeWaypoint(BaseModel):
    """A single narration checkpoint the child should reach."""

    waypoint_id: str
    element_name: str
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    description: str = ""
    vocabulary: ExpectedVocabulary = Field(default_factory=ExpectedVocabulary)
    grammar: ExpectedGrammar = Field(default_factory=ExpectedGrammar)
    detail_level: str = "standard"
    is_critical: bool = False
    tolerance: float = Field(default=0.5, ge=0.0, le=1.0)


class ExpectedRelation(BaseModel):
    """A relation the child should express."""

    relation_id: str
    element_a: str
    element_b: str
    relation_type: str
    expected_expression: str = ""
    salience: float = Field(default=0.5, ge=0.0, le=1.0)


class SceneNEG(BaseModel):
    """Full Narrative Expectation Graph for a single scene."""

    scene_id: str
    waypoints: List[NarrativeWaypoint] = Field(default_factory=list)
    relations: List[ExpectedRelation] = Field(default_factory=list)
    anticipated_traps: List[AnticipatedTrap] = Field(default_factory=list)
    min_coverage: float = 0.7

    def get_waypoints_by_salience(self) -> List[NarrativeWaypoint]:
        return sorted(self.waypoints, key=lambda w: w.salience, reverse=True)

    def get_critical_waypoints(self) -> List[NarrativeWaypoint]:
        return [w for w in self.waypoints if w.is_critical]


class NEGGenerationResult(BaseModel):
    """Complete output of the Narrative Expectation Module."""

    scenes: List[SceneNEG] = Field(default_factory=list)
