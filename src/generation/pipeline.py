"""Pipeline orchestrator for the Tellimations generation modules.

Executes the full generation pipeline in order:
  1. PLOT:     (character, setting) -> PlotGenerationResult
  2. IMAGES:   (plot) -> background HD + element images per scene
  3. FEATURES: (images) -> exhaustive visual properties per element
  4. MASKS:    (images, features) -> binary polygon masks per part
  5. NEG:      (plot, features, student_profile) -> Narrative Expectation Graph

All steps are sequential: each depends on the previous. The NEG step
runs after FEATURES so it can be grounded in what is actually visible
in the generated images, not just the plot descriptions.

Supports checkpointing: each completed step is saved to disk.
On resume, completed steps are skipped and data is loaded from disk.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.generation.feature_scanner import scan_scene_features
from src.generation.mask_generator import generate_scene_masks
from src.generation.narrative_expectation import generate_neg
from src.generation.plot_scene_generator import generate_plot
from src.generation.scene_image_generator import generate_scene_images
from src.models.feature_scan import (
    ElementFeatures,
    FeatureScanResult,
    SceneFeatureScan,
)
from src.models.mask import MaskGenerationResult, SceneMasks
from src.models.neg import NEGGenerationResult
from src.models.pipeline import (
    PipelineConfig,
    PipelineResult,
    PipelineState,
    PipelineStep,
    SceneOutput,
)
from src.models.plot import PlotGenerationResult, PlotScene
from src.models.student_profile import StudentProfile

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Raised when the pipeline fails at any step."""


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _session_dir(config: PipelineConfig) -> Path:
    """Return the session output directory."""
    return config.output_dir / config.session_id


def _scene_dir(config: PipelineConfig, scene_id: str) -> Path:
    """Return the directory for a specific scene's artifacts."""
    return _session_dir(config) / "scenes" / scene_id


def _save_json(data: Any, path: Path) -> None:
    """Serialize a Pydantic model or dict to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(data, "model_dump"):
        payload = data.model_dump()
    else:
        payload = data
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _load_json(path: Path) -> Dict[str, Any]:
    """Load a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state: PipelineState, config: PipelineConfig) -> None:
    """Save pipeline state for checkpointing."""
    _save_json(state, _session_dir(config) / "pipeline_state.json")


def _load_state(config: PipelineConfig) -> Optional[PipelineState]:
    """Load pipeline state if it exists."""
    path = _session_dir(config) / "pipeline_state.json"
    if path.exists():
        try:
            data = _load_json(path)
            return PipelineState.model_validate(data)
        except Exception as exc:
            logger.warning("[pipeline] Failed to load state: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Step 1: Plot generation
# ---------------------------------------------------------------------------


async def _step_plot(
    config: PipelineConfig,
    state: PipelineState,
) -> PlotGenerationResult:
    """Generate the story plot."""
    logger.info("[pipeline] Step 1/5: Generating plot...")
    state.mark_step_started(PipelineStep.PLOT)
    _save_state(state, config)

    plot = await generate_plot(
        api_key=config.api_key,
        character=config.character,
        setting=config.setting,
        max_retries=config.max_retries,
        initial_delay=config.initial_delay,
        max_delay=config.max_delay,
    )

    # Save plot
    plot_path = _session_dir(config) / "plot.json"
    _save_json(plot, plot_path)
    state.plot_file = "plot.json"

    # Save scene manifests
    manifest_path = _session_dir(config) / "scene_manifest.json"
    _save_json(
        [scene.model_dump() for scene in plot.plot],
        manifest_path,
    )

    state.mark_step_completed(PipelineStep.PLOT)
    _save_state(state, config)

    logger.info(
        "[pipeline] Plot generated: %d scenes", len(plot.plot),
    )
    return plot


def _load_plot(config: PipelineConfig) -> PlotGenerationResult:
    """Load a previously saved plot."""
    path = _session_dir(config) / "plot.json"
    data = _load_json(path)
    return PlotGenerationResult.model_validate(data)


# ---------------------------------------------------------------------------
# Step 2: Image generation
# ---------------------------------------------------------------------------


async def _step_images(
    config: PipelineConfig,
    state: PipelineState,
    plot: PlotGenerationResult,
) -> Dict[str, Dict[str, Path]]:
    """Generate images for all scenes."""
    logger.info("[pipeline] Step 2/5: Generating images...")
    state.mark_step_started(PipelineStep.IMAGES)
    _save_state(state, config)

    all_image_paths: Dict[str, Dict[str, Path]] = {}

    for scene in plot.plot:
        sid = scene.scene_id
        scene_base = _scene_dir(config, sid)

        # Generate images into structured subdirectories
        elements_dir = scene_base / "elements"
        elements_dir.mkdir(parents=True, exist_ok=True)

        image_paths = await generate_scene_images(
            api_key=config.api_key,
            scene=scene,
            output_dir=scene_base,
            max_retries=config.max_retries,
            initial_delay=config.initial_delay,
            max_delay=config.max_delay,
        )

        # Reorganize: move element images to elements/ subdirectory,
        # rename background to background.png
        reorganized: Dict[str, Path] = {}

        for key, path in image_paths.items():
            if key == "bg":
                new_path = scene_base / "background.png"
                if path != new_path:
                    path.rename(new_path)
                reorganized["bg"] = new_path
            elif key.startswith("elem_"):
                elem_name = key[len("elem_"):]
                new_path = elements_dir / f"{elem_name}.png"
                if path != new_path:
                    path.rename(new_path)
                reorganized[key] = new_path

        all_image_paths[sid] = reorganized

        # Record in state
        state.scenes[sid] = SceneOutput(
            scene_id=sid,
            image_paths={k: str(v) for k, v in reorganized.items()},
        )

    # Clean up any leftover files from generate_scene_images
    # (original files were renamed, so nothing to clean)

    state.mark_step_completed(PipelineStep.IMAGES)
    _save_state(state, config)

    logger.info(
        "[pipeline] Images generated for %d scenes", len(all_image_paths),
    )
    return all_image_paths


def _load_image_paths(
    config: PipelineConfig,
    state: PipelineState,
) -> Dict[str, Dict[str, Path]]:
    """Reconstruct image paths from saved state."""
    result: Dict[str, Dict[str, Path]] = {}
    for sid, scene_output in state.scenes.items():
        paths: Dict[str, Path] = {}
        for key, path_str in scene_output.image_paths.items():
            paths[key] = Path(path_str)
        result[sid] = paths
    return result


# ---------------------------------------------------------------------------
# Step 3: Feature scanning
# ---------------------------------------------------------------------------


async def _step_features(
    config: PipelineConfig,
    state: PipelineState,
    plot: PlotGenerationResult,
    image_paths: Dict[str, Dict[str, Path]],
) -> FeatureScanResult:
    """Scan visual features for all scenes."""
    logger.info("[pipeline] Step 3/5: Scanning features...")
    state.mark_step_started(PipelineStep.FEATURES)
    _save_state(state, config)

    all_scene_scans: List[SceneFeatureScan] = []

    for scene in plot.plot:
        sid = scene.scene_id
        paths = image_paths.get(sid, {})
        if not paths:
            logger.warning("[pipeline] No images for scene '%s', skipping", sid)
            continue

        # Build element_types from the plot manifest
        element_types: Dict[str, str] = {}
        for elem in scene.manifest.elements:
            safe_name = elem.name.replace(" ", "_").lower()
            element_types[safe_name] = elem.type

        scan = await scan_scene_features(
            api_key=config.api_key,
            scene_id=sid,
            image_paths=paths,
            element_types=element_types,
            max_retries=config.max_retries,
            initial_delay=config.initial_delay,
            max_delay=config.max_delay,
        )

        all_scene_scans.append(scan)

        # Save per-element features
        features_dir = _scene_dir(config, sid) / "features"
        features_dir.mkdir(parents=True, exist_ok=True)
        for ef in scan.elements:
            feat_path = features_dir / f"{ef.element_id}_features.json"
            _save_json(ef, feat_path)

        # Save composition features
        if scan.composition:
            comp_path = features_dir / "composition_features.json"
            _save_json(scan.composition, comp_path)

        # Update state
        if sid in state.scenes:
            state.scenes[sid].features = scan

    features_result = FeatureScanResult(scenes=all_scene_scans)

    state.mark_step_completed(PipelineStep.FEATURES)
    _save_state(state, config)

    total_elements = sum(len(s.elements) for s in all_scene_scans)
    logger.info(
        "[pipeline] Features scanned: %d scenes, %d elements",
        len(all_scene_scans), total_elements,
    )
    return features_result


def _load_features(
    config: PipelineConfig,
    state: PipelineState,
) -> FeatureScanResult:
    """Reconstruct features from saved state."""
    scenes = []
    for sid, scene_output in state.scenes.items():
        if scene_output.features:
            scenes.append(scene_output.features)
    return FeatureScanResult(scenes=scenes)


# ---------------------------------------------------------------------------
# Step 4: Mask generation
# ---------------------------------------------------------------------------


async def _step_masks(
    config: PipelineConfig,
    state: PipelineState,
    image_paths: Dict[str, Dict[str, Path]],
    features: FeatureScanResult,
) -> MaskGenerationResult:
    """Generate polygon masks for all elements."""
    logger.info("[pipeline] Step 4/5: Generating masks...")
    state.mark_step_started(PipelineStep.MASKS)
    _save_state(state, config)

    all_scene_masks: List[SceneMasks] = []

    # Build features lookup: scene_id -> List[ElementFeatures]
    features_by_scene: Dict[str, List[ElementFeatures]] = {}
    for scene_scan in features.scenes:
        features_by_scene[scene_scan.scene_id] = scene_scan.elements

    for sid, paths in image_paths.items():
        element_features = features_by_scene.get(sid, [])
        if not element_features:
            logger.warning(
                "[pipeline] No features for scene '%s', skipping masks", sid,
            )
            continue

        masks_dir = _scene_dir(config, sid) / "masks"
        masks_dir.mkdir(parents=True, exist_ok=True)

        masks = await generate_scene_masks(
            api_key=config.api_key,
            scene_id=sid,
            image_paths=paths,
            element_features=element_features,
            output_dir=masks_dir,
            max_retries=config.max_retries,
            initial_delay=config.initial_delay,
            max_delay=config.max_delay,
        )

        all_scene_masks.append(masks)

        # Update state
        if sid in state.scenes:
            state.scenes[sid].masks = masks

    masks_result = MaskGenerationResult(scenes=all_scene_masks)

    state.mark_step_completed(PipelineStep.MASKS)
    _save_state(state, config)

    total_masks = sum(
        len(part.parts)
        for sm in all_scene_masks
        for part in sm.elements
    )
    logger.info(
        "[pipeline] Masks generated: %d scenes, %d masks total",
        len(all_scene_masks), total_masks,
    )
    return masks_result


# ---------------------------------------------------------------------------
# Step 5: NEG generation
# ---------------------------------------------------------------------------


async def _step_neg(
    config: PipelineConfig,
    state: PipelineState,
    plot: PlotGenerationResult,
    features: Optional[FeatureScanResult] = None,
) -> NEGGenerationResult:
    """Generate the Narrative Expectation Graph.

    When features are provided, the NEG is grounded in the actual
    visual properties extracted from the generated images.
    """
    logger.info("[pipeline] Step 5/5: Generating NEG...")
    state.mark_step_started(PipelineStep.NEG)
    _save_state(state, config)

    neg = await generate_neg(
        api_key=config.api_key,
        plot=plot,
        student_profile=config.student_profile,
        features=features,
        max_retries=config.max_retries,
        initial_delay=config.initial_delay,
        max_delay=config.max_delay,
    )

    # Save NEG
    neg_path = _session_dir(config) / "neg.json"
    _save_json(neg, neg_path)
    state.neg_file = "neg.json"

    # Save student profile if provided
    if config.student_profile:
        profile_path = _session_dir(config) / "student_profile.json"
        _save_json(config.student_profile, profile_path)
        state.student_profile_file = "student_profile.json"

    state.mark_step_completed(PipelineStep.NEG)
    _save_state(state, config)

    total_waypoints = sum(len(s.waypoints) for s in neg.scenes)
    logger.info(
        "[pipeline] NEG generated: %d scenes, %d waypoints",
        len(neg.scenes), total_waypoints,
    )
    return neg


def _load_neg(config: PipelineConfig) -> NEGGenerationResult:
    """Load a previously saved NEG."""
    path = _session_dir(config) / "neg.json"
    data = _load_json(path)
    return NEGGenerationResult.model_validate(data)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def _should_run_step(
    step: PipelineStep,
    state: PipelineState,
    resume_from: Optional[PipelineStep],
) -> bool:
    """Determine whether a step should run or be skipped.

    A step is skipped if:
      - It was already completed AND we are not resuming from it or earlier.
    """
    if resume_from is not None:
        step_order = list(PipelineStep)
        resume_idx = step_order.index(resume_from)
        step_idx = step_order.index(step)
        # Run this step if it's at or after the resume point
        if step_idx >= resume_idx:
            return True
        # Before resume point: skip if completed
        return not state.is_step_completed(step)

    # No resume_from: skip completed steps
    return not state.is_step_completed(step)


async def run_pipeline(config: PipelineConfig) -> PipelineResult:
    """Execute the full generation pipeline.

    Pipeline order (all sequential):
      1. PLOT:     character + setting -> plot with scene manifests
      2. IMAGES:   plot -> HD backgrounds + element images
      3. FEATURES: images -> visual property catalog per element
      4. MASKS:    images + features -> binary polygon masks
      5. NEG:      plot + features + student_profile -> narrative expectation graph

    The NEG runs after FEATURES so it can be grounded in what is
    actually visible in the generated images — not just the plot
    descriptions. This ensures waypoints and vocabulary match the
    real visual properties of each element.

    Supports resume: if a previous run was interrupted, completed
    steps are loaded from disk and skipped.

    Args:
        config: Pipeline configuration with API key, character,
            setting, output directory, and retry parameters.

    Returns:
        PipelineResult with all generated artifacts.

    Raises:
        PipelineError: If any step fails fatally.
    """
    session_dir = _session_dir(config)
    session_dir.mkdir(parents=True, exist_ok=True)

    # Load or create state
    state = _load_state(config) or PipelineState(session_id=config.session_id)
    resume_from = config.resume_from

    result = PipelineResult(
        session_id=config.session_id,
        student_profile=config.student_profile,
        state=state,
    )

    logger.info(
        "[pipeline] Starting pipeline for session '%s' in %s",
        config.session_id, session_dir,
    )
    if state.completed_steps:
        logger.info(
            "[pipeline] Previously completed: %s",
            [s.value for s in state.completed_steps],
        )

    # -----------------------------------------------------------------------
    # Step 1: Plot
    # -----------------------------------------------------------------------

    plot: Optional[PlotGenerationResult] = None

    if _should_run_step(PipelineStep.PLOT, state, resume_from):
        try:
            plot = await _step_plot(config, state)
        except Exception as exc:
            state.error = f"PLOT failed: {exc}"
            _save_state(state, config)
            raise PipelineError(f"Plot generation failed: {exc}") from exc
    else:
        logger.info("[pipeline] Skipping step PLOT (already completed)")
        plot = _load_plot(config)

    result.plot = plot

    # -----------------------------------------------------------------------
    # Step 2: Images
    # -----------------------------------------------------------------------

    image_paths: Dict[str, Dict[str, Path]] = {}

    if _should_run_step(PipelineStep.IMAGES, state, resume_from):
        try:
            image_paths = await _step_images(config, state, plot)
        except Exception as exc:
            state.error = f"IMAGES failed: {exc}"
            _save_state(state, config)
            raise PipelineError(f"Image generation failed: {exc}") from exc
    else:
        logger.info("[pipeline] Skipping step IMAGES (already completed)")
        image_paths = _load_image_paths(config, state)

    # -----------------------------------------------------------------------
    # Step 3: Features
    # -----------------------------------------------------------------------

    features: Optional[FeatureScanResult] = None

    if _should_run_step(PipelineStep.FEATURES, state, resume_from):
        try:
            features = await _step_features(config, state, plot, image_paths)
        except Exception as exc:
            state.error = f"FEATURES failed: {exc}"
            _save_state(state, config)
            raise PipelineError(f"Feature scanning failed: {exc}") from exc
    else:
        logger.info("[pipeline] Skipping step FEATURES (already completed)")
        features = _load_features(config, state)

    result.features = features

    # -----------------------------------------------------------------------
    # Step 4: Masks
    # -----------------------------------------------------------------------

    masks: Optional[MaskGenerationResult] = None

    if _should_run_step(PipelineStep.MASKS, state, resume_from):
        try:
            masks = await _step_masks(config, state, image_paths, features)
        except Exception as exc:
            state.error = f"MASKS failed: {exc}"
            _save_state(state, config)
            raise PipelineError(f"Mask generation failed: {exc}") from exc
    else:
        logger.info("[pipeline] Skipping step MASKS (already completed)")

    result.masks = masks

    # -----------------------------------------------------------------------
    # Step 5: NEG (after features — grounded in visual ground truth)
    # -----------------------------------------------------------------------

    if _should_run_step(PipelineStep.NEG, state, resume_from):
        try:
            neg = await _step_neg(config, state, plot, features)
            result.neg = neg
        except Exception as exc:
            state.error = f"NEG failed: {exc}"
            _save_state(state, config)
            raise PipelineError(f"NEG generation failed: {exc}") from exc
    else:
        logger.info("[pipeline] Skipping step NEG (already completed)")
        result.neg = _load_neg(config)

    # -----------------------------------------------------------------------
    # Done
    # -----------------------------------------------------------------------

    logger.info(
        "[pipeline] Pipeline completed for session '%s'. "
        "Steps: %s",
        config.session_id,
        [s.value for s in state.completed_steps],
    )

    result.state = state
    return result
