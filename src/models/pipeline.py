"""Pydantic models for the generation pipeline orchestrator.

Defines configuration, per-step state tracking, and the complete
session output that bundles all module results together.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.models.feature_scan import FeatureScanResult, SceneFeatureScan
from src.models.mask import MaskGenerationResult, SceneMasks
from src.models.neg import NEGGenerationResult
from src.models.plot import PlotCharacter, PlotGenerationResult, PlotSetting
from src.models.student_profile import StudentProfile


class PipelineStep(str, Enum):
    """Steps in the generation pipeline, in execution order."""

    PLOT = "plot"
    IMAGES = "images"
    FEATURES = "features"
    MASKS = "masks"
    NEG = "neg"


class PipelineConfig(BaseModel):
    """Configuration for a pipeline run."""

    api_key: str
    character: PlotCharacter
    setting: PlotSetting
    output_dir: Path

    session_id: str = "session_001"
    student_profile: Optional[StudentProfile] = None

    # Retry settings (shared across all modules)
    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 30.0

    # Resume control: skip steps already completed
    resume_from: Optional[PipelineStep] = None

    class Config:
        arbitrary_types_allowed = True


class SceneOutput(BaseModel):
    """Output data for a single scene within the pipeline."""

    scene_id: str
    image_paths: Dict[str, str] = Field(default_factory=dict)
    features: Optional[SceneFeatureScan] = None
    masks: Optional[SceneMasks] = None


class PipelineState(BaseModel):
    """Tracks the state of a pipeline run for checkpointing/resume.

    Serialized to pipeline_state.json in the session output directory.
    """

    session_id: str
    completed_steps: List[PipelineStep] = Field(default_factory=list)
    current_step: Optional[PipelineStep] = None
    error: Optional[str] = None

    # References to saved artifacts (relative to session dir)
    plot_file: Optional[str] = None
    neg_file: Optional[str] = None
    student_profile_file: Optional[str] = None
    scenes: Dict[str, SceneOutput] = Field(default_factory=dict)

    def is_step_completed(self, step: PipelineStep) -> bool:
        return step in self.completed_steps

    def mark_step_completed(self, step: PipelineStep) -> None:
        if step not in self.completed_steps:
            self.completed_steps.append(step)
        self.current_step = None

    def mark_step_started(self, step: PipelineStep) -> None:
        self.current_step = step
        self.error = None


class PipelineResult(BaseModel):
    """Complete output of a full pipeline run."""

    session_id: str
    plot: Optional[PlotGenerationResult] = None
    neg: Optional[NEGGenerationResult] = None
    features: Optional[FeatureScanResult] = None
    masks: Optional[MaskGenerationResult] = None
    student_profile: Optional[StudentProfile] = None
    state: PipelineState = Field(default_factory=lambda: PipelineState(session_id=""))
