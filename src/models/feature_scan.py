"""Pydantic models for the visual feature scanning module.

These models capture the exhaustive visual properties extracted from
generated images by Gemini 3.1 Pro. They serve two downstream purposes:
  - Determining actionable properties for animation/scaffolding
  - Feeding the Discrepancy Assessment (child's narration vs visible reality)
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, computed_field


class PartFeatures(BaseModel):
    """Visual properties of a single identifiable part of an element.

    Each property category corresponds to a SKILL error type, enabling
    precise error exclusion and targeted discrepancy detection.
    """

    part: str
    parent: str
    colors: List[str] = Field(default_factory=list)
    texture: Optional[str] = None
    material: Optional[str] = None
    hardness: Optional[str] = None
    weight_appearance: Optional[str] = None
    temperature_appearance: Optional[str] = None
    shape: Optional[str] = None
    size: Optional[str] = None
    shine: Optional[str] = None
    state: Optional[str] = None
    pattern: Optional[str] = None
    contour: Optional[str] = None
    extra_properties: List[str] = Field(default_factory=list)

    @computed_field
    @property
    def properties(self) -> List[str]:
        """Flat list of all properties (backward-compatible)."""
        props: List[str] = []
        props.extend(self.colors)
        for field_name in (
            "texture", "material", "hardness", "weight_appearance",
            "temperature_appearance", "shape", "size", "shine",
            "state", "pattern", "contour",
        ):
            val = getattr(self, field_name)
            if val is not None:
                props.append(val)
        props.extend(self.extra_properties)
        return props


class ElementFeatures(BaseModel):
    """Complete visual feature scan for a single element."""

    element_id: str
    colors: List[str] = Field(default_factory=list)
    texture: Optional[str] = None
    material: Optional[str] = None
    hardness: Optional[str] = None
    weight_appearance: Optional[str] = None
    temperature_appearance: Optional[str] = None
    shape: Optional[str] = None
    size: Optional[str] = None
    shine: Optional[str] = None
    state: Optional[str] = None
    pattern: Optional[str] = None
    posture: Optional[str] = None
    expression: Optional[str] = None
    extra_properties: List[str] = Field(default_factory=list)
    parts: List[PartFeatures] = Field(default_factory=list)
    actionable_properties: List[str] = Field(default_factory=list)

    @computed_field
    @property
    def global_properties(self) -> List[str]:
        """Flat list of element-level properties (backward-compatible)."""
        props: List[str] = []
        props.extend(self.colors)
        for field_name in (
            "texture", "material", "hardness", "weight_appearance",
            "temperature_appearance", "shape", "size", "shine",
            "state", "pattern", "posture", "expression",
        ):
            val = getattr(self, field_name)
            if val is not None:
                props.append(val)
        props.extend(self.extra_properties)
        return props

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
