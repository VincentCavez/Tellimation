"""Visual feature scanning module via Gemini 3.1 Pro.

Analyzes generated images to extract exhaustive visual properties:
  1. Per-element scan: each element image analyzed in isolation
  2. Composition scan: the composed scene analyzed for emergent properties

These properties feed downstream modules:
  - Actionable properties for animation/scaffolding selection
  - Discrepancy Assessment (child's narration vs visible reality)
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

from src.generation.prompts.feature_scan_prompt import (
    COMPOSITION_SCAN_SYSTEM_PROMPT,
    COMPOSITION_SCAN_USER_PROMPT,
    ELEMENT_SCAN_SYSTEM_PROMPT,
    ELEMENT_SCAN_USER_PROMPT,
)
from src.generation.utils import extract_json, get_response_text
from src.models.feature_scan import (
    ElementFeatures,
    FeatureScanResult,
    SceneCompositionFeatures,
    SceneFeatureScan,
)

logger = logging.getLogger(__name__)

MODEL_ID = "gemini-3.1-pro-preview"

# Retry defaults
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 30.0  # seconds


class FeatureScanError(Exception):
    """Raised when feature scanning fails after all retries."""


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def _load_image_bytes(path: Path) -> bytes:
    """Read an image file and return its bytes."""
    return path.read_bytes()


def _compose_scene_image(
    background_path: Path,
    element_paths: Dict[str, Path],
) -> bytes:
    """Composite element images onto the background for scene-level analysis.

    Places elements onto the background at reasonable positions for the
    VLM to analyze spatial relationships. Uses a simple left-to-right
    layout since exact positioning isn't critical for feature extraction —
    the VLM needs to see all elements in context together.

    Returns PNG bytes of the composed image.
    """
    bg = Image.open(background_path).convert("RGBA")
    bg_w, bg_h = bg.size

    elements = list(element_paths.items())
    if not elements:
        out = io.BytesIO()
        bg.save(out, format="PNG")
        return out.getvalue()

    # Distribute elements across the lower portion of the background
    usable_width = int(bg_w * 0.8)
    left_margin = int(bg_w * 0.1)
    spacing = usable_width // max(len(elements), 1)

    for i, (elem_name, elem_path) in enumerate(elements):
        try:
            elem_img = Image.open(elem_path).convert("RGBA")

            # Scale element to fit reasonably in the scene
            max_elem_h = int(bg_h * 0.4)
            max_elem_w = spacing - 10
            elem_img.thumbnail((max_elem_w, max_elem_h), Image.LANCZOS)

            # Position: spread horizontally, sit on the ground area (~60%)
            x = left_margin + i * spacing + (spacing - elem_img.width) // 2
            y = int(bg_h * 0.6) - elem_img.height

            # Remove green chroma-key before compositing
            elem_img = _remove_green_chroma(elem_img)

            bg.paste(elem_img, (x, y), elem_img)
        except Exception as exc:
            logger.warning(
                "[feature-scan] Failed to composite element '%s': %s",
                elem_name, exc,
            )

    out = io.BytesIO()
    bg.save(out, format="PNG")
    return out.getvalue()


def _remove_green_chroma(img: Image.Image, threshold: float = 60.0) -> Image.Image:
    """Remove green (#00FF00) chroma-key background from an RGBA image.

    Detects the actual background color from corner pixels, then makes
    matching pixels transparent.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    pixels = img.load()
    w, h = img.size

    # Detect background color from corners
    corners = []
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        corners.append(pixels[x, y][:3])

    bg_r = sorted(c[0] for c in corners)[len(corners) // 2]
    bg_g = sorted(c[1] for c in corners)[len(corners) // 2]
    bg_b = sorted(c[2] for c in corners)[len(corners) // 2]

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            dist = ((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2) ** 0.5
            if dist < threshold:
                pixels[x, y] = (r, g, b, 0)

    return img


# ---------------------------------------------------------------------------
# Detailed logging
# ---------------------------------------------------------------------------


def _log_element_features(elem: ElementFeatures) -> None:
    """Log detailed breakdown of an element's features to console."""
    hdr = f"[feature-scan] ── {elem.element_id} "
    logger.info("%s%s", hdr, "─" * max(0, 60 - len(hdr)))

    # Element-level structured properties
    for field in (
        "colors", "texture", "material", "hardness", "weight_appearance",
        "temperature_appearance", "shape", "size", "shine", "state",
        "pattern", "posture", "expression",
    ):
        val = getattr(elem, field)
        if val is not None and val != []:
            logger.info("[feature-scan]   %-22s %s", field + ":", val)

    if elem.extra_properties:
        logger.info("[feature-scan]   %-22s %s", "extra:", elem.extra_properties)

    if elem.actionable_properties:
        logger.info(
            "[feature-scan]   actionable (%d):     %s",
            len(elem.actionable_properties),
            elem.actionable_properties,
        )

    # Sub-parts
    logger.info(
        "[feature-scan]   sub-entities: %d parts", len(elem.parts),
    )
    for p in elem.parts:
        props_summary = []
        for field in (
            "colors", "texture", "material", "hardness", "weight_appearance",
            "temperature_appearance", "shape", "size", "shine", "state",
            "pattern", "contour",
        ):
            val = getattr(p, field)
            if val is not None and val != []:
                if isinstance(val, list):
                    props_summary.append(f"{field}={val}")
                else:
                    props_summary.append(f"{field}={val}")
        if p.extra_properties:
            props_summary.append(f"extra={p.extra_properties}")

        logger.info(
            "[feature-scan]     [%s] (parent: %s) → %s",
            p.part, p.parent, ", ".join(props_summary) if props_summary else "(none)",
        )


# ---------------------------------------------------------------------------
# Single element scan
# ---------------------------------------------------------------------------


async def _scan_element(
    client: Any,
    element_id: str,
    element_type: str,
    image_bytes: bytes,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    temperature: float = 0.4,
    thinking_budget: int = 2048,
) -> ElementFeatures:
    """Scan a single element image and extract visual properties.

    Sends the image to Gemini 3.1 Pro for analysis.

    Returns ElementFeatures with exhaustive property catalog.
    Raises FeatureScanError after all retries.
    """
    user_text = ELEMENT_SCAN_USER_PROMPT.format(
        element_id=element_id,
        element_type=element_type,
    )

    last_error: Optional[Exception] = None
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "[feature-scan] Element '%s' attempt %d/%d",
                element_id, attempt, max_retries,
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
                    system_instruction=ELEMENT_SCAN_SYSTEM_PROMPT,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=thinking_budget,
                    ),
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )

            raw_text = get_response_text(response)
            data = extract_json(raw_text)
            result = ElementFeatures.model_validate(data)

            logger.info(
                "[feature-scan] Element '%s': %d global props, %d parts, "
                "%d actionable",
                element_id,
                len(result.global_properties),
                len(result.parts),
                len(result.actionable_properties),
            )

            # Detailed property log for each element
            _log_element_features(result)

            return result

        except Exception as exc:
            last_error = exc
            logger.warning(
                "[feature-scan] Element '%s' attempt %d/%d failed: %s",
                element_id, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    raise FeatureScanError(
        f"Element scan failed for '{element_id}' after {max_retries} "
        f"attempts. Last error: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Composition scan
# ---------------------------------------------------------------------------


async def _scan_composition(
    client: Any,
    scene_id: str,
    element_names: List[str],
    composed_image_bytes: bytes,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    temperature: float = 0.4,
    thinking_budget: int = 2048,
) -> SceneCompositionFeatures:
    """Scan the composed scene image for emergent visual properties.

    Returns SceneCompositionFeatures.
    Raises FeatureScanError after all retries.
    """
    user_text = COMPOSITION_SCAN_USER_PROMPT.format(
        scene_id=scene_id,
        element_list=", ".join(element_names),
    )

    last_error: Optional[Exception] = None
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "[feature-scan] Composition '%s' attempt %d/%d",
                scene_id, attempt, max_retries,
            )

            response = await client.aio.models.generate_content(
                model=MODEL_ID,
                contents=[
                    types.Part.from_bytes(
                        data=composed_image_bytes, mime_type="image/png",
                    ),
                    user_text,
                ],
                config=types.GenerateContentConfig(
                    system_instruction=COMPOSITION_SCAN_SYSTEM_PROMPT,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=thinking_budget,
                    ),
                    temperature=temperature,
                    response_mime_type="application/json",
                ),
            )

            raw_text = get_response_text(response)
            data = extract_json(raw_text)
            result = SceneCompositionFeatures.model_validate(data)

            total_props = (
                len(result.spatial_relationships)
                + len(result.environment_properties)
                + len(result.relative_sizes)
                + len(result.depth_cues)
                + len(result.lighting_and_atmosphere)
            )
            logger.info(
                "[feature-scan] Composition '%s': %d total properties",
                scene_id, total_props,
            )

            # Detailed composition log
            logger.info("[feature-scan] ── Composition %s ──────────", scene_id)
            for rel in result.spatial_relationships:
                logger.info("[feature-scan]   spatial:     %s", rel)
            for env in result.environment_properties:
                logger.info("[feature-scan]   environment: %s", env)
            for sz in result.relative_sizes:
                logger.info("[feature-scan]   size:        %s", sz)
            for d in result.depth_cues:
                logger.info("[feature-scan]   depth:       %s", d)
            for lit in result.lighting_and_atmosphere:
                logger.info("[feature-scan]   lighting:    %s", lit)

            return result

        except Exception as exc:
            last_error = exc
            logger.warning(
                "[feature-scan] Composition '%s' attempt %d/%d failed: %s",
                scene_id, attempt, max_retries, exc,
            )
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    raise FeatureScanError(
        f"Composition scan failed for '{scene_id}' after {max_retries} "
        f"attempts. Last error: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Scene-level: scan all elements + composition
# ---------------------------------------------------------------------------


async def scan_scene_features(
    api_key: str,
    scene_id: str,
    image_paths: Dict[str, Path],
    element_types: Optional[Dict[str, str]] = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    temperature: float = 0.4,
    thinking_budget: int = 2048,
) -> SceneFeatureScan:
    """Scan all images for a single scene and extract visual properties.

    Args:
        api_key: Gemini API key.
        scene_id: Identifier for the scene.
        image_paths: Dict from generate_scene_images output:
            "bg" -> background path,
            "elem_<name>" -> element path.
        element_types: Optional mapping element_id -> type string
            (e.g., {"cat": "animal", "rock": "object"}). If not provided,
            defaults to "element" for all.
        max_retries: Max retries per scan call.
        initial_delay: Initial backoff delay.
        max_delay: Max backoff delay.
        temperature: LLM temperature (low for factual extraction).
        thinking_budget: Token budget for reasoning.

    Returns:
        SceneFeatureScan with element features and composition features.
    """
    client = genai.Client(api_key=api_key)
    element_types = element_types or {}

    retry_kwargs = dict(
        max_retries=max_retries,
        initial_delay=initial_delay,
        max_delay=max_delay,
        temperature=temperature,
        thinking_budget=thinking_budget,
    )

    # Identify element images (keys starting with "elem_")
    element_entries: Dict[str, Path] = {}
    bg_path: Optional[Path] = None
    for key, path in image_paths.items():
        if key == "bg":
            bg_path = path
        elif key.startswith("elem_"):
            elem_id = key[len("elem_"):]
            element_entries[elem_id] = path

    # 1. Scan all elements in parallel
    element_tasks = []
    element_ids = []
    for elem_id, elem_path in element_entries.items():
        element_ids.append(elem_id)
        img_bytes = _load_image_bytes(elem_path)
        etype = element_types.get(elem_id, "element")
        element_tasks.append(
            _scan_element(
                client,
                element_id=elem_id,
                element_type=etype,
                image_bytes=img_bytes,
                **retry_kwargs,
            )
        )

    # 2. Compose scene image for composition scan (if background exists)
    composition_task = None
    if bg_path is not None and element_entries:
        composed_bytes = _compose_scene_image(bg_path, element_entries)
        composition_task = _scan_composition(
            client,
            scene_id=scene_id,
            element_names=list(element_entries.keys()),
            composed_image_bytes=composed_bytes,
            **retry_kwargs,
        )

    # Run element scans + composition scan in parallel
    all_tasks = list(element_tasks)
    if composition_task is not None:
        all_tasks.append(composition_task)

    results = await asyncio.gather(*all_tasks, return_exceptions=True)

    # Collect element results
    element_features: List[ElementFeatures] = []
    for i, elem_id in enumerate(element_ids):
        result = results[i]
        if isinstance(result, Exception):
            logger.warning(
                "[feature-scan] Element '%s' scan failed: %s",
                elem_id, result,
            )
        else:
            element_features.append(result)

    # Collect composition result
    composition: Optional[SceneCompositionFeatures] = None
    if composition_task is not None:
        comp_result = results[len(element_ids)]
        if isinstance(comp_result, Exception):
            logger.warning(
                "[feature-scan] Composition scan for '%s' failed: %s",
                scene_id, comp_result,
            )
        else:
            composition = comp_result

    logger.info(
        "[feature-scan] Scene '%s': scanned %d/%d elements, "
        "composition=%s",
        scene_id,
        len(element_features),
        len(element_ids),
        "OK" if composition else "FAILED",
    )

    return SceneFeatureScan(
        scene_id=scene_id,
        elements=element_features,
        composition=composition,
    )


# ---------------------------------------------------------------------------
# Multi-scene: scan all scenes in a plot
# ---------------------------------------------------------------------------


async def scan_all_features(
    api_key: str,
    scene_image_map: Dict[str, Dict[str, Path]],
    element_type_map: Optional[Dict[str, Dict[str, str]]] = None,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    initial_delay: float = DEFAULT_INITIAL_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    temperature: float = 0.4,
    thinking_budget: int = 2048,
) -> FeatureScanResult:
    """Scan visual features for all scenes.

    Args:
        api_key: Gemini API key.
        scene_image_map: Output from generate_all_scene_images:
            scene_id -> {"bg": Path, "elem_<name>": Path, ...}.
        element_type_map: Optional mapping scene_id -> {elem_id -> type}.
        max_retries: Max retries per scan call.
        initial_delay: Initial backoff delay.
        max_delay: Max backoff delay.
        temperature: LLM temperature.
        thinking_budget: Token budget for reasoning.

    Returns:
        FeatureScanResult with SceneFeatureScan for each scene.
    """
    element_type_map = element_type_map or {}

    scan_kwargs = dict(
        api_key=api_key,
        max_retries=max_retries,
        initial_delay=initial_delay,
        max_delay=max_delay,
        temperature=temperature,
        thinking_budget=thinking_budget,
    )

    # Process scenes sequentially to avoid rate limits
    scene_scans: List[SceneFeatureScan] = []
    for scene_id, image_paths in scene_image_map.items():
        try:
            scan = await scan_scene_features(
                scene_id=scene_id,
                image_paths=image_paths,
                element_types=element_type_map.get(scene_id),
                **scan_kwargs,
            )
            scene_scans.append(scan)
        except Exception as exc:
            logger.error(
                "[feature-scan] Scene '%s' scan failed entirely: %s",
                scene_id, exc,
            )

    logger.info(
        "[feature-scan] Completed %d/%d scene scans",
        len(scene_scans), len(scene_image_map),
    )

    return FeatureScanResult(scenes=scene_scans)
