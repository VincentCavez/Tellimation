from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


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


class ErrorExclusion(BaseModel):
    entity_id: str
    excluded: List[str] = Field(default_factory=list)
    reason: str = ""


class NEG(BaseModel):
    targets: List[NarrativeTarget] = Field(default_factory=list)
    error_exclusions: List[ErrorExclusion] = Field(default_factory=list)
    min_coverage: float = 0.7
    skill_coverage_check: str = "PENDING"

    def get_targets_for_entity(self, entity_id: str) -> List[NarrativeTarget]:
        return [t for t in self.targets if t.entity_id == entity_id]

    def is_error_excluded(self, entity_id: str, error_type: str) -> bool:
        for ex in self.error_exclusions:
            if ex.entity_id == entity_id and error_type in ex.excluded:
                return True
        return False
