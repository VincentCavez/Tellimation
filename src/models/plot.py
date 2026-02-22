"""Pydantic models for plot and scene manifest generation.

These models use relative coordinates (0.0-1.0) instead of absolute pixel
positions, and include z-index, orientation, and ground plane concepts
that are specific to the plot generation module.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class PlotCharacter(BaseModel):
    """Input: main character definition."""

    name: str
    type: str
    traits: Dict[str, str] = Field(default_factory=dict)


class PlotSetting(BaseModel):
    """Input: setting definition."""

    lieu: str
    ambiance: str
    epoch: str = "present"


class PlotRelativePosition(BaseModel):
    """Position as relative coordinates (0.0-1.0) within the scene frame."""

    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)


class PlotElement(BaseModel):
    """An element (character or object) within a plot scene."""

    name: str
    type: str
    position: PlotRelativePosition
    orientation: str = "face_right"
    relative_size: str = "medium"
    z_index: int = 0


class PlotRelation(BaseModel):
    """Spatial relation between two elements in a scene."""

    element_a: str
    element_b: str
    preposition: str


class PlotGround(BaseModel):
    """Ground/terrain definition for a scene."""

    type: str = "herbe"
    horizon_line: float = Field(default=0.6, ge=0.0, le=1.0)


class PlotSceneManifest(BaseModel):
    """Manifest for a single scene within the plot."""

    elements: List[PlotElement] = Field(default_factory=list)
    relations: List[PlotRelation] = Field(default_factory=list)
    ground: PlotGround = Field(default_factory=PlotGround)


class PlotScene(BaseModel):
    """A single scene in the story plot."""

    scene_id: str
    description: str
    key_events: List[str] = Field(default_factory=list)
    elements_involved: List[str] = Field(default_factory=list)
    manifest: PlotSceneManifest = Field(default_factory=PlotSceneManifest)


class PlotGenerationResult(BaseModel):
    """Complete output of the plot generation LLM call."""

    plot: List[PlotScene] = Field(default_factory=list)
