"""Invocation array models for the structured animation pipeline."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class AnimationInvocation(BaseModel):
    """A single animation invocation in the sequence."""
    animation_id: str
    targets: List[str] = Field(default_factory=list)
    parameter_overrides: Dict[str, Any] = Field(default_factory=dict)


class InvocationArray(BaseModel):
    """Structured sequence of animations to play, corrections first."""
    sequence: List[AnimationInvocation] = Field(default_factory=list)
