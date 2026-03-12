"""Assessment models for the discrepancy assessment module."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Gemini assessment response models
# ---------------------------------------------------------------------------

class FactualError(BaseModel):
    """A factual inaccuracy in the child's utterance vs. the scene manifest."""
    utterance_fragment: str
    manifest_ref: str
    explanation: str


class MISLOpportunity(BaseModel):
    """A MISL dimension absent from the utterance that could be grounded in the manifest."""
    dimension: str
    manifest_elements: List[str] = Field(default_factory=list)
    suggestion: str


class AssessmentResponse(BaseModel):
    """Structured output from the single-call Gemini assessment."""
    factual_errors: List[FactualError] = Field(default_factory=list)
    misl_opportunities: List[MISLOpportunity] = Field(default_factory=list)
    utterance_is_acceptable: bool = True


# ---------------------------------------------------------------------------
# Per-scene logging models
# ---------------------------------------------------------------------------

class SceneAssessmentEntry(BaseModel):
    """One assessment record within a scene log."""
    timestamp: float = 0.0
    utterance_text: str = ""
    audio_path: str = ""
    gemini_response: AssessmentResponse = Field(default_factory=AssessmentResponse)
    accepted: bool = True
    correction_triggered: bool = False
    misl_guidance_triggered: bool = False


class SceneStoryEntry(BaseModel):
    """An accepted utterance in the scene's story."""
    utterance_text: str = ""
    audio_path: str = ""


class SceneLog(BaseModel):
    """Per-scene log of all assessments and accepted story utterances."""
    scene_id: str = ""
    scene_manifest: Dict[str, Any] = Field(default_factory=dict)
    assessments: List[SceneAssessmentEntry] = Field(default_factory=list)
    story: List[SceneStoryEntry] = Field(default_factory=list)
    misl_opportunities_given: int = 0
