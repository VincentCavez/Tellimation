"""Mask generation module via Gemini 2.5 Flash.

For each element, sends the image to Gemini along with the part list
from the feature scanner. Gemini returns polygon contours per part.
Pillow converts these to binary mask PNGs (quantized to 2 colors).

Output structure:
  output_dir/
    scene_01_cat_eyes_mask.png
    scene_01_cat_tail_mask.png
    scene_01_cat_body_mask.png
    ...
    scene_01_mask_index.json
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw

from google import genai
from google.genai import types

from src.generation.prompts.mask_prompt import (
    MASK_SYSTEM_PROMPT,
    MASK_USER_PROMPT,
    build_parts_list,
)
from src.generation.utils import extract_json, get_response_text
from src.models.feature_scan import ElementFeatures, PartFeatures
from src.models.mask import (
    ElementMasks,
    MaskGenerationResult,
    MaskIndex,
    MaskIndexEntry,
    PartMask,
    SceneMasks,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-2.5-flash"

# Retry defaults
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 30.0  # seconds


class MaskGenerationError(Exception):
    """Raised when mask generation fails after all retries."""


# ---------------------------------------------------------------------------
# Pillow: polygon -> binary mask PNG (quantized to 2 colors)
# ---------------------------------------------------------------------------


def _polygon_to_mask(
    polygon: List[List[int]],
    width: int,
    height: int,
) -> Image.Image:
    """Render a polygon as a binary mask image.

    White (255) = inside the polygon, Black (0) = outside.
    Quantized to exactly 2 colors for minimal file size.

    Args:
        polygon: List of [x, y] vertices.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        PIL Image in mode "P" (palette), quantized to 2 colors.
    """
    mask = Image.new("L", (width, height), 0)
    if len(polygon) >= 3:
        draw = ImageDraw.Draw(mask)
        flat_coords = [(x, y) for x, y in polygon]
        draw.polygon(flat_coords, fill=255)

    # Quantize to 2 colors for clean binary mask
    quantized = mask.quantize(colors=2, method=Image.Quantize.MEDIANCUT)
    return quantized


def _save_mask_png(mask: Image.Image, path: Path) -> None:
    """Save a quantized mask image as PNG."""
    mask.save(path, format="PNG", optimize=True)


def _compute_bounding_box(polygon: List[List[int]]) -> List[int]:
    """Compute axis-aligned bounding box [x_min, y_min, x_max, y_max]."""
    if not polygon:
        return [0, 0, 0, 0]
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return [min(xs), min(ys), max(xs), max(ys)]


# ---------------------------------------------------------------------------
# LLM call: segment one element
# ---------------------------------------------------------------------------


async def _segment_element(
    client: Any,
    element_id: str,
    image_bytes: bytes,
    parts: List[PartFeatures],
    image_width: int,
    image_height: int,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    temperature: float = 0.3,
) -> ElementMasks:
    """Send an element image to Gemini for polygon segmentation.

    Args:
        client: Gemini client.
        element_id: Element identifier.
        image_bytes: PNG bytes of the element image.
        parts: List of PartFeatures from the feature scanner.
        image_width: Element image width.
        image_height: Element image height.
        max_retries: Max retries.
        initial_delay: Initial backoff delay.
        max_delay: Max backoff delay.
        temperature: LLM temperature (low for precision).

    Returns:
        ElementMasks with polygon data for each segmented part.

    Raises:
        MaskGenerationError after all retries.
    """
    parts_dicts = [{"part": p.part, "parent": p.parent} for p in parts]
    parts_list_str = build_parts_list(parts_dicts)

    user_text = MASK_USER_PROMPT.format(
        element_id=element_id,
        width=image_width,
        height=image_height,
        parts_list=parts_list_str,
    )

    last_error: Optional[Exception] = None
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "[mask-gen] Element '%s' attempt %d/%d (%d parts)",
                element_id, attempt, max_retries, len(parts),
            )

            response = await client.aio.models.generate_content(
                model=MODEL_ID,
                contents=[
                    types.Part.from_bytes(
                        data=image_bytes, mime_type="image/png",
                    ),
                    user_text,
                ],
                config=types.GenerateContentConfig(
                    system_instruction=MASK_SYSTEM_PROMPT,
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )

            raw_text = get_response_text(response)
            data = extract_json(raw_text)

            result = ElementMasks.model_validate(data)

            # Validate and fix bounding boxes
            for part_mask in result.parts:
                if part_mask.polygon and not part_mask.bounding_box:
                    part_mask.bounding_box = _compute_bounding_box(
                        part_mask.polygon
                    )

            # Validate polygons are within image bounds
            _validate_polygons(result, image_width, image_height)

            logger.info(
                "[mask-gen] Element '%s': %d parts segmented",
                element_id, len(result.parts),
            )
            return result

        except Exception as exc:
            last_error = exc
            logger.warning(
                "[mask-gen] Element '%s' attempt %d/%d failed: %s",
                element_id, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    raise MaskGenerationError(
        f"Mask generation failed for '{element_id}' after {max_retries} "
        f"attempts. Last error: {last_error}"
    ) from last_error


def _validate_polygons(
    masks: ElementMasks,
    width: int,
    height: int,
) -> None:
    """Clamp polygon coordinates to image bounds.

    Gemini may occasionally produce coordinates slightly outside the
    image. We clamp rather than reject to be robust.
    """
    for part in masks.parts:
        for point in part.polygon:
            point[0] = max(0, min(point[0], width - 1))
            point[1] = max(0, min(point[1], height - 1))
        # Recompute bounding box after clamping
        if part.polygon:
            part.bounding_box = _compute_bounding_box(part.polygon)


# ---------------------------------------------------------------------------
# Save masks as binary PNGs + generate index JSON
# ---------------------------------------------------------------------------


def _save_element_masks(
    element_masks: ElementMasks,
    scene_id: str,
    output_dir: Path,
) -> List[MaskIndexEntry]:
    """Render and save binary mask PNGs for all parts of an element.

    Returns a list of MaskIndexEntry for the index file.
    """
    entries: List[MaskIndexEntry] = []
    w = element_masks.image_width
    h = element_masks.image_height

    for part in element_masks.parts:
        if len(part.polygon) < 3:
            logger.warning(
                "[mask-gen] Skipping part '%s' of '%s': polygon has "
                "fewer than 3 vertices",
                part.part_name, element_masks.element_id,
            )
            continue

        # Build filename: scene_01_cat_eyes_mask.png
        safe_elem = element_masks.element_id.replace(" ", "_").lower()
        safe_part = part.part_name.replace(" ", "_").lower()
        filename = f"{scene_id}_{safe_elem}_{safe_part}_mask.png"
        filepath = output_dir / filename

        # Render polygon to binary mask
        mask_img = _polygon_to_mask(part.polygon, w, h)
        _save_mask_png(mask_img, filepath)

        # Update part with file reference
        part.mask_file = filename

        entries.append(MaskIndexEntry(
            mask_id=part.part_id,
            element_id=element_masks.element_id,
            part_name=part.part_name,
            parent=part.parent,
            mask_file=filename,
            bounding_box=part.bounding_box,
        ))

        logger.debug(
            "[mask-gen] Saved mask %s (%dx%d)", filename, w, h,
        )

    return entries


def _save_mask_index(
    scene_id: str,
    entries: List[MaskIndexEntry],
    output_dir: Path,
) -> Path:
    """Save the mask index JSON file.

    Returns the path to the saved index file.
    """
    index = MaskIndex(scene_id=scene_id, entries=entries)
    index_path = output_dir / f"{scene_id}_mask_index.json"
    index_path.write_text(
        json.dumps(index.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(
        "[mask-gen] Saved mask index %s (%d entries)",
        index_path.name, len(entries),
    )
    return index_path


# ---------------------------------------------------------------------------
# Scene-level: segment all elements + save masks
# ---------------------------------------------------------------------------


async def generate_scene_masks(
    api_key: str,
    scene_id: str,
    image_paths: Dict[str, Path],
    element_features: List[ElementFeatures],
    output_dir: Path,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    temperature: float = 0.3,
) -> SceneMasks:
    """Generate and save polygon masks for all elements in a scene.

    Args:
        api_key: Gemini API key.
        scene_id: Scene identifier.
        image_paths: Dict from generate_scene_images output:
            "elem_<name>" -> element image path.
        element_features: List of ElementFeatures from the feature scanner.
        output_dir: Directory to save mask PNGs and index JSON.
        max_retries: Max retries per segmentation call.
        initial_delay: Initial backoff delay.
        max_delay: Max backoff delay.
        temperature: LLM temperature.

    Returns:
        SceneMasks with polygon data for all elements.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client(api_key=api_key)

    retry_kwargs = dict(
        max_retries=max_retries,
        initial_delay=initial_delay,
        max_delay=max_delay,
        temperature=temperature,
    )

    # Build a lookup: element_id -> ElementFeatures
    features_by_id: Dict[str, ElementFeatures] = {
        ef.element_id: ef for ef in element_features
    }

    # Identify element images and match with features
    tasks = []
    task_element_ids = []

    for key, path in image_paths.items():
        if not key.startswith("elem_"):
            continue

        elem_id = key[len("elem_"):]
        features = features_by_id.get(elem_id)
        if not features or not features.parts:
            logger.warning(
                "[mask-gen] No features/parts for element '%s', skipping",
                elem_id,
            )
            continue

        # Read image to get dimensions
        img = Image.open(path)
        img_w, img_h = img.size
        img_bytes = path.read_bytes()

        tasks.append(
            _segment_element(
                client,
                element_id=elem_id,
                image_bytes=img_bytes,
                parts=features.parts,
                image_width=img_w,
                image_height=img_h,
                **retry_kwargs,
            )
        )
        task_element_ids.append(elem_id)

    if not tasks:
        logger.warning("[mask-gen] No elements to segment for scene '%s'", scene_id)
        return SceneMasks(scene_id=scene_id)

    # Segment all elements in parallel
    logger.info(
        "[mask-gen] Scene '%s': segmenting %d elements in parallel...",
        scene_id, len(tasks),
    )
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results: save masks + build index
    all_element_masks: List[ElementMasks] = []
    all_index_entries: List[MaskIndexEntry] = []

    for elem_id, result in zip(task_element_ids, results):
        if isinstance(result, Exception):
            logger.warning(
                "[mask-gen] Element '%s' segmentation failed: %s",
                elem_id, result,
            )
            continue

        all_element_masks.append(result)

        # Save binary mask PNGs
        entries = _save_element_masks(result, scene_id, output_dir)
        all_index_entries.extend(entries)

    # Save the index JSON
    if all_index_entries:
        _save_mask_index(scene_id, all_index_entries, output_dir)

    logger.info(
        "[mask-gen] Scene '%s': %d/%d elements masked, %d mask files",
        scene_id,
        len(all_element_masks),
        len(task_element_ids),
        len(all_index_entries),
    )

    return SceneMasks(scene_id=scene_id, elements=all_element_masks)


# ---------------------------------------------------------------------------
# Multi-scene: generate masks for all scenes
# ---------------------------------------------------------------------------


async def generate_all_masks(
    api_key: str,
    scene_image_map: Dict[str, Dict[str, Path]],
    scene_features_map: Dict[str, List[ElementFeatures]],
    output_dir: Path,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    temperature: float = 0.3,
) -> MaskGenerationResult:
    """Generate polygon masks for all elements across all scenes.

    Args:
        api_key: Gemini API key.
        scene_image_map: Output from generate_all_scene_images:
            scene_id -> {"bg": Path, "elem_<name>": Path, ...}.
        scene_features_map: Mapping scene_id -> list of ElementFeatures
            from the feature scanner.
        output_dir: Root output directory for mask PNGs and index files.
        max_retries: Max retries per segmentation call.
        initial_delay: Initial backoff delay.
        max_delay: Max backoff delay.
        temperature: LLM temperature.

    Returns:
        MaskGenerationResult with SceneMasks for each scene.
    """
    gen_kwargs = dict(
        api_key=api_key,
        output_dir=output_dir,
        max_retries=max_retries,
        initial_delay=initial_delay,
        max_delay=max_delay,
        temperature=temperature,
    )

    # Process scenes sequentially to avoid rate limits
    scene_masks_list: List[SceneMasks] = []
    for scene_id, image_paths in scene_image_map.items():
        features = scene_features_map.get(scene_id, [])
        if not features:
            logger.warning(
                "[mask-gen] No features for scene '%s', skipping", scene_id,
            )
            continue

        try:
            masks = await generate_scene_masks(
                scene_id=scene_id,
                image_paths=image_paths,
                element_features=features,
                **gen_kwargs,
            )
            scene_masks_list.append(masks)
        except Exception as exc:
            logger.error(
                "[mask-gen] Scene '%s' mask generation failed: %s",
                scene_id, exc,
            )

    logger.info(
        "[mask-gen] Completed %d/%d scene mask generations",
        len(scene_masks_list), len(scene_image_map),
    )

    return MaskGenerationResult(scenes=scene_masks_list)
