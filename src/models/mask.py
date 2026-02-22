"""Pydantic models for the mask generation module.

These models represent polygon-based segmentation masks for element parts.
Each mask is a closed polygon (list of [x, y] vertices) in pixel coordinates
matching the original element image resolution.

Downstream uses:
  - Hit-testing (click on a part to interact)
  - Targeted animation (animate only eyes, tail, etc.)
  - Visual effects (glow, pulse on specific parts)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


class PartMask(BaseModel):
    """Polygon mask for a single identifiable part of an element."""

    part_id: str
    part_name: str
    parent: str
    polygon: List[List[int]] = Field(default_factory=list)
    bounding_box: Optional[List[int]] = None
    mask_file: Optional[str] = None

    def contains_point(self, x: int, y: int) -> bool:
        """Check if a point is inside the polygon (ray-casting algorithm)."""
        n = len(self.polygon)
        if n < 3:
            return False
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = self.polygon[i]
            xj, yj = self.polygon[j]
            if ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / (yj - yi) + xi
            ):
                inside = not inside
            j = i
        return inside


class ElementMasks(BaseModel):
    """All part masks for a single element."""

    element_id: str
    image_width: int
    image_height: int
    parts: List[PartMask] = Field(default_factory=list)

    def get_part(self, part_name: str) -> Optional[PartMask]:
        """Return the PartMask for a given part name, or None."""
        for p in self.parts:
            if p.part_name == part_name:
                return p
        return None

    def hit_test(self, x: int, y: int) -> Optional[PartMask]:
        """Return the first part whose polygon contains the given point."""
        for p in self.parts:
            if p.contains_point(x, y):
                return p
        return None


class SceneMasks(BaseModel):
    """All element masks for a single scene."""

    scene_id: str
    elements: List[ElementMasks] = Field(default_factory=list)

    def get_element(self, element_id: str) -> Optional[ElementMasks]:
        """Return the ElementMasks for a given element_id, or None."""
        for e in self.elements:
            if e.element_id == element_id:
                return e
        return None


class MaskIndex(BaseModel):
    """Index file mapping mask IDs to their metadata and file paths."""

    scene_id: str
    entries: List[MaskIndexEntry] = Field(default_factory=list)


class MaskIndexEntry(BaseModel):
    """A single entry in the mask index."""

    mask_id: str
    element_id: str
    part_name: str
    parent: str
    mask_file: str
    bounding_box: Optional[List[int]] = None


# Fix forward reference: MaskIndex references MaskIndexEntry
# but MaskIndexEntry is defined after. Rebuild model.
MaskIndex.model_rebuild()


class MaskGenerationResult(BaseModel):
    """Complete output of the mask generation module across all scenes."""

    scenes: List[SceneMasks] = Field(default_factory=list)
