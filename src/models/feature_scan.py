"""Pydantic models for the visual feature scanning module.

These models capture the exhaustive visual properties extracted from
generated images by Gemini 3.1 Pro. They serve two downstream purposes:
  - Determining actionable properties for animation/scaffolding
  - Feeding the Discrepancy Assessment (child's narration vs visible reality)
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class PartFeatures(BaseModel):
    """Visual properties of a single identifiable part of an element."""

    part: str
    parent: str
    properties: List[str] = Field(default_factory=list)


class ElementFeatures(BaseModel):
    """Complete visual feature scan for a single element."""

    element_id: str
    global_properties: List[str] = Field(default_factory=list)
    parts: List[PartFeatures] = Field(default_factory=list)
    actionable_properties: List[str] = Field(default_factory=list)

    def get_part(self, part_name: str) -> Optional[PartFeatures]:
        """Return the PartFeatures for a given part name, or None."""
        for p in self.parts:
            if p.part == part_name:
                return p
        return None

    def all_properties(self) -> List[str]:
        """Return a flat list of all properties (global + all parts)."""
        props = list(self.global_properties)
        for p in self.parts:
            props.extend(p.properties)
        return props


class SceneCompositionFeatures(BaseModel):
    """Visual properties observed in the composed scene (background + elements).

    Captures spatial relationships, relative sizes, environmental context,
    and other properties only visible when elements are placed together.
    """

    scene_id: str
    spatial_relationships: List[str] = Field(default_factory=list)
    environment_properties: List[str] = Field(default_factory=list)
    relative_sizes: List[str] = Field(default_factory=list)
    depth_cues: List[str] = Field(default_factory=list)
    lighting_and_atmosphere: List[str] = Field(default_factory=list)


class SceneFeatureScan(BaseModel):
    """Complete feature scan result for a single scene."""

    scene_id: str
    elements: List[ElementFeatures] = Field(default_factory=list)
    composition: Optional[SceneCompositionFeatures] = None

    def get_element(self, element_id: str) -> Optional[ElementFeatures]:
        """Return the ElementFeatures for a given element_id, or None."""
        for e in self.elements:
            if e.element_id == element_id:
                return e
        return None


class FeatureScanResult(BaseModel):
    """Complete output of the feature scanning module across all scenes."""

    scenes: List[SceneFeatureScan] = Field(default_factory=list)
