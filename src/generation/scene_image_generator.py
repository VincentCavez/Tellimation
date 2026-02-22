"""Standalone scene image generation via Gemini 3 Pro Image.

For each scene in a PlotGenerationResult, generates:
  1. An HD background (the environment without characters/objects)
  2. Individual element images with green chroma-key backgrounds

Images are saved with structured filenames:
  output_dir/
    scene_01_bg.png
    scene_01_elem_rabbit.png
    scene_01_elem_rock.png
    scene_02_bg.png
    ...

This module is independent of the NEG and sprite code pipelines.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

from google import genai
from google.genai import types

from src.generation.prompts.image_prompt import (
    build_background_prompt,
    build_element_prompt,
)
from src.models.plot import PlotGenerationResult, PlotScene

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3-pro-image-preview"

# Retry defaults
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 30.0  # seconds

# Image size constraints
MIN_IMAGE_DIMENSION = 64  # reject images smaller than this


class ImageGenerationError(Exception):
    """Raised when image generation fails after all retries."""


# ---------------------------------------------------------------------------
# Low-level: extract image bytes from Gemini response
# ---------------------------------------------------------------------------


def _extract_image_bytes(response: Any) -> Optional[bytes]:
    """Extract the first image from a Gemini response.

    Returns PNG image bytes, or None if no image data is found.
    """
    if response.candidates and response.candidates[0].content:
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                return part.inline_data.data
    return None


def _validate_image(
    image_bytes: bytes,
    label: str,
    min_dim: int = MIN_IMAGE_DIMENSION,
) -> Image.Image:
    """Open and validate image bytes.

    Raises ValueError if the image is too small or unreadable.
    """
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    if w < min_dim or h < min_dim:
        raise ValueError(
            f"{label}: image too small ({w}x{h}), minimum {min_dim}px"
        )
    return img


# ---------------------------------------------------------------------------
# Background generation
# ---------------------------------------------------------------------------


async def _generate_background(
    client: Any,
    scene: PlotScene,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
) -> bytes:
    """Generate an HD background image for a single scene.

    Returns PNG image bytes.
    Raises ImageGenerationError after all retries.
    """
    ground = scene.manifest.ground
    prompt = build_background_prompt(
        scene_description=scene.description,
        ground_type=ground.type,
        horizon_line=ground.horizon_line,
    )

    last_error: Optional[Exception] = None
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "[scene-img] %s bg attempt %d/%d",
                scene.scene_id, attempt, max_retries,
            )

            response = await client.aio.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio="16:9",
                    ),
                ),
            )

            raw = _extract_image_bytes(response)
            if raw is None:
                raise ValueError("No image data in response")

            _validate_image(raw, f"{scene.scene_id} bg")

            logger.info(
                "[scene-img] %s bg: got image (%d bytes)",
                scene.scene_id, len(raw),
            )
            return raw

        except Exception as exc:
            last_error = exc
            logger.warning(
                "[scene-img] %s bg attempt %d/%d failed: %s",
                scene.scene_id, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    raise ImageGenerationError(
        f"Background generation failed for {scene.scene_id} "
        f"after {max_retries} attempts. Last error: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Element generation
# ---------------------------------------------------------------------------


async def _generate_element(
    client: Any,
    scene: PlotScene,
    element_name: str,
    element_type: str,
    orientation: str,
    relative_size: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
) -> bytes:
    """Generate a single element image on green chroma-key.

    Returns PNG image bytes.
    Raises ImageGenerationError after all retries.
    """
    prompt = build_element_prompt(
        element_name=element_name,
        element_type=element_type,
        orientation=orientation,
        relative_size=relative_size,
        scene_description=scene.description,
    )

    last_error: Optional[Exception] = None
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "[scene-img] %s elem '%s' attempt %d/%d",
                scene.scene_id, element_name, attempt, max_retries,
            )

            response = await client.aio.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio="1:1",
                    ),
                ),
            )

            raw = _extract_image_bytes(response)
            if raw is None:
                raise ValueError("No image data in response")

            _validate_image(raw, f"{scene.scene_id} elem '{element_name}'")

            logger.info(
                "[scene-img] %s elem '%s': got image (%d bytes)",
                scene.scene_id, element_name, len(raw),
            )
            return raw

        except Exception as exc:
            last_error = exc
            logger.warning(
                "[scene-img] %s elem '%s' attempt %d/%d failed: %s",
                scene.scene_id, element_name, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    raise ImageGenerationError(
        f"Element generation failed for '{element_name}' in {scene.scene_id} "
        f"after {max_retries} attempts. Last error: {last_error}"
    ) from last_error


async def _generate_elements_parallel(
    client: Any,
    scene: PlotScene,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
) -> Dict[str, bytes]:
    """Generate images for all elements in a scene, in parallel.

    Returns dict mapping element_name -> PNG bytes.
    Elements that fail after all retries are logged but skipped.
    """
    if not scene.manifest.elements:
        return {}

    tasks = []
    element_names = []
    for elem in scene.manifest.elements:
        element_names.append(elem.name)
        tasks.append(
            _generate_element(
                client,
                scene,
                element_name=elem.name,
                element_type=elem.type,
                orientation=elem.orientation,
                relative_size=elem.relative_size,
                max_retries=max_retries,
                initial_delay=initial_delay,
                max_delay=max_delay,
            )
        )

    logger.info(
        "[scene-img] %s: generating %d elements in parallel...",
        scene.scene_id, len(tasks),
    )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    element_images: Dict[str, bytes] = {}
    for name, result in zip(element_names, results):
        if isinstance(result, Exception):
            logger.warning(
                "[scene-img] %s elem '%s': failed: %s",
                scene.scene_id, name, result,
            )
        elif isinstance(result, bytes):
            element_images[name] = result

    logger.info(
        "[scene-img] %s: generated %d/%d element images",
        scene.scene_id, len(element_images), len(element_names),
    )
    return element_images


# ---------------------------------------------------------------------------
# Scene-level: background + all elements
# ---------------------------------------------------------------------------


async def generate_scene_images(
    api_key: str,
    scene: PlotScene,
    output_dir: Path,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
) -> Dict[str, Path]:
    """Generate and save all images for a single scene.

    Generates the background and all element images concurrently,
    then saves them to output_dir with structured filenames.

    Args:
        api_key: Gemini API key.
        scene: PlotScene with manifest (elements, ground, etc.).
        output_dir: Directory to save images to (created if needed).
        max_retries: Max retries per image.
        initial_delay: Initial backoff delay.
        max_delay: Max backoff delay.

    Returns:
        Dict mapping descriptive key -> saved file path:
          "bg" -> Path to background image
          "elem_<name>" -> Path to element image
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    client = genai.Client(api_key=api_key)
    sid = scene.scene_id

    retry_kwargs = dict(
        max_retries=max_retries,
        initial_delay=initial_delay,
        max_delay=max_delay,
    )

    # Launch background and elements in parallel
    bg_task = _generate_background(client, scene, **retry_kwargs)
    elem_task = _generate_elements_parallel(client, scene, **retry_kwargs)

    bg_bytes, element_images = await asyncio.gather(
        bg_task, elem_task, return_exceptions=False,
    )

    saved: Dict[str, Path] = {}

    # Save background
    bg_path = output_dir / f"{sid}_bg.png"
    bg_path.write_bytes(bg_bytes)
    saved["bg"] = bg_path
    logger.info("[scene-img] Saved %s (%d bytes)", bg_path.name, len(bg_bytes))

    # Save elements
    for elem_name, img_bytes in element_images.items():
        safe_name = elem_name.replace(" ", "_").lower()
        elem_path = output_dir / f"{sid}_elem_{safe_name}.png"
        elem_path.write_bytes(img_bytes)
        saved[f"elem_{safe_name}"] = elem_path
        logger.info(
            "[scene-img] Saved %s (%d bytes)", elem_path.name, len(img_bytes),
        )

    return saved


# ---------------------------------------------------------------------------
# Plot-level: all scenes
# ---------------------------------------------------------------------------


async def generate_all_scene_images(
    api_key: str,
    plot: PlotGenerationResult,
    output_dir: Path,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    parallel_scenes: bool = False,
) -> Dict[str, Dict[str, Path]]:
    """Generate images for every scene in a plot.

    Args:
        api_key: Gemini API key.
        plot: Complete plot with scenes and manifests.
        output_dir: Root output directory. Each scene's images are saved
            directly in this directory with scene_id prefixed filenames.
        max_retries: Max retries per image.
        initial_delay: Initial backoff delay.
        max_delay: Max backoff delay.
        parallel_scenes: If True, generate all scenes concurrently.
            If False (default), generate scenes sequentially to reduce
            API rate-limit pressure.

    Returns:
        Dict mapping scene_id -> {key -> Path} for all saved images.
    """
    all_results: Dict[str, Dict[str, Path]] = {}

    gen_kwargs = dict(
        api_key=api_key,
        output_dir=output_dir,
        max_retries=max_retries,
        initial_delay=initial_delay,
        max_delay=max_delay,
    )

    if parallel_scenes:
        tasks = [
            generate_scene_images(scene=scene, **gen_kwargs)
            for scene in plot.plot
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for scene, result in zip(plot.plot, results):
            if isinstance(result, Exception):
                logger.error(
                    "[scene-img] Scene %s failed: %s", scene.scene_id, result,
                )
            else:
                all_results[scene.scene_id] = result
    else:
        for scene in plot.plot:
            try:
                result = await generate_scene_images(scene=scene, **gen_kwargs)
                all_results[scene.scene_id] = result
            except Exception as exc:
                logger.error(
                    "[scene-img] Scene %s failed: %s", scene.scene_id, exc,
                )

    logger.info(
        "[scene-img] Completed %d/%d scenes",
        len(all_results), len(plot.plot),
    )
    return all_results
