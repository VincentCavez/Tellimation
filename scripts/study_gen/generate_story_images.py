#!/usr/bin/env python3
"""Generate HD and pixel-art scene images for study stories.

For each scene, generates:
  1. Background-only image (HD, no characters or key objects)
  2. Full-scene image (HD, background + all characters and objects)
  3. Pixel-art downscale of both (NEAREST neighbor)
  4. Character reference sheets (HD + pixel-art)

Uses the existing Gemini (Nano Banana 2) image generation pipeline.

Usage:
    python -m scripts.study_gen.generate_story_images                      # All scenes
    python -m scripts.study_gen.generate_story_images --scenes 1           # Scene 1 only
    python -m scripts.study_gen.generate_story_images --skip-pixel         # HD only
    python -m scripts.study_gen.generate_story_images --hd-only            # Alias for --skip-pixel
    python -m scripts.study_gen.generate_story_images --story A            # Default
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import io
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from google import genai
from google.genai import types

from src.generation.image_processing import (
    IMAGE_MODEL_ID,
    IMAGE_TIMEOUT,
    IMAGE_MAX_RETRIES,
    ART_W,
    ART_H,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("generate_story_images")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCENES_JSON = PROJECT_ROOT / "scripts" / "study_gen" / "story_a_scenes.json"
OUTPUT_BASE = PROJECT_ROOT / "data" / "study_gen"

# Rate limit delay between API calls (seconds)
API_DELAY = 2.0


# ---------------------------------------------------------------------------
# Prompt templates (matching existing codebase style)
# ---------------------------------------------------------------------------

BACKGROUND_PROMPT_PREFIX = """\
Create a BACKGROUND ONLY illustration — no characters, no objects, no entities. \
Just the environment and atmosphere. Clean children's illustration style.

## Scene environment
{scene_description}

## Style Guidelines — CRITICAL
- **Clean children's illustration style**: smooth gradients, clear shapes, \
  warm and friendly. Suitable for ages 7-11.
- **Flat side-view** (like a 2D storybook): no perspective.
- **The ground or floor surface MUST be clearly visible** at approximately \
  70% from the top of the image.
- **Rich details**: atmospheric gradients, clouds, distant elements, textures.
- **NO characters or objects** — purely the background environment.
- **NO text, labels, numbers, coordinates, or writing of any kind** in the image.
- **Warm, friendly, child-appropriate** feel.
"""

FULL_SCENE_PROMPT_PREFIX = """\
Create a complete scene illustration with all characters and objects. \
Clean children's illustration style.

## Scene
{scene_description}

## Style Guidelines — CRITICAL
- **Clean children's illustration style**: smooth gradients, clear shapes, \
  warm and friendly. Suitable for ages 7-11.
- **Flat side-view** (like a 2D storybook): no perspective.
- **The ground or floor surface MUST be clearly visible** at approximately \
  70% from the top of the image.
- **Rich details**: atmospheric gradients, clear character features, expressive poses.
- **Characters must be clearly visible and distinct**: each character should be \
  easy to identify and separate from the background.
- **NO text, labels, numbers, coordinates, or writing of any kind** in the image.
- **Warm, friendly, child-appropriate** feel.
- **NO ANTHROPOMORPHISM** for non-living objects: tables, wagons, and objects \
  must NOT have faces, eyes, or human expressions.
"""

CHARACTER_REF_PROMPT = """\
Create an illustration of the following character on a SOLID WHITE background. \
The white must be perfectly uniform — no gradients, no shading, no variation.

## Character
{character_description}

## Style Guidelines
- **Clean children's illustration style**: smooth shapes, clear outlines, rich colors. \
  Warm, friendly, suitable for ages 7-11.
- **Side view** (like a 2D storybook): flat side profile.
- **The character should fill most of the image** — center it, leave only a small \
  margin of white around it.
- **Show the full body** of the character from head to feet.
- **Rich color palette**: smooth shading with shadows and highlights.
- **Detailed**: clearly distinct body parts (head, body, limbs).
- **NO other elements**: no ground, no shadow, no text, no decorations. \
  ONLY the character on solid white.
- **NO text, labels, or writing of any kind.**
"""


# ---------------------------------------------------------------------------
# Image generation helpers
# ---------------------------------------------------------------------------

async def generate_image(
    client: Any,
    prompt: str,
    aspect_ratio: str = "16:9",
    label: str = "image",
    reference_images: Optional[List[bytes]] = None,
) -> Optional[bytes]:
    """Generate an image using the existing Gemini image model.

    Reuses IMAGE_MODEL_ID, IMAGE_TIMEOUT, IMAGE_MAX_RETRIES from image_processing.

    Args:
        reference_images: Optional list of image bytes to pass as style/character
            reference alongside the text prompt. Used for character consistency.
    """
    # Build multimodal contents: reference images first, then text prompt
    if reference_images:
        contents: List[Any] = []
        for i, img_bytes in enumerate(reference_images):
            contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
        contents.append(prompt)
    else:
        contents = prompt

    for attempt in range(1, IMAGE_MAX_RETRIES + 1):
        try:
            t0 = time.time()
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=IMAGE_MODEL_ID,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(
                            aspect_ratio=aspect_ratio,
                        ),
                    ),
                ),
                timeout=IMAGE_TIMEOUT,
            )
            elapsed = time.time() - t0

            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        logger.info(
                            "[%s] Attempt %d/%d: got %d bytes in %.1fs%s",
                            label, attempt, IMAGE_MAX_RETRIES,
                            len(part.inline_data.data), elapsed,
                            f" (with {len(reference_images)} ref images)" if reference_images else "",
                        )
                        return part.inline_data.data

            logger.warning(
                "[%s] Attempt %d/%d: no image data (%.1fs)",
                label, attempt, IMAGE_MAX_RETRIES, elapsed,
            )

        except Exception as exc:
            logger.warning(
                "[%s] Attempt %d/%d failed in %.1fs (%s): %s",
                label, attempt, IMAGE_MAX_RETRIES,
                time.time() - t0, type(exc).__name__, exc or "no details",
            )

        if attempt < IMAGE_MAX_RETRIES:
            await asyncio.sleep(API_DELAY)

    logger.error("[%s] All %d attempts exhausted", label, IMAGE_MAX_RETRIES)
    return None


def save_image(image_bytes: bytes, path: Path) -> None:
    """Save raw image bytes to a PNG file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(image_bytes))
    img.save(str(path), format="PNG")
    logger.info("Saved %s (%d bytes)", path, path.stat().st_size)


def downscale_to_pixel_art(image_bytes: bytes, target_w: int, target_h: int) -> Image.Image:
    """Downscale an image to pixel art using NEAREST neighbor.

    Preserves aspect ratio within target bounds.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if w <= 0 or h <= 0:
        return img

    scale = min(target_w / w, target_h / h)
    final_w = max(1, round(w * scale))
    final_h = max(1, round(h * scale))

    return img.resize((final_w, final_h), Image.NEAREST)


def save_pixel_art(image_bytes: bytes, path: Path, target_w: int = ART_W, target_h: int = ART_H) -> None:
    """Downscale and save as pixel art PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pixel_img = downscale_to_pixel_art(image_bytes, target_w, target_h)
    pixel_img.save(str(path), format="PNG")
    logger.info("Saved pixel art %s (%dx%d)", path, pixel_img.width, pixel_img.height)


# ---------------------------------------------------------------------------
# Main generation pipeline
# ---------------------------------------------------------------------------

async def generate_scene(
    client: Any,
    story_id: str,
    scene: Dict[str, Any],
    characters: Dict[str, str],
    skip_pixel: bool = False,
    prompts_log: List[Dict[str, Any]] = None,
    ref_images: Optional[Dict[str, bytes]] = None,
) -> None:
    """Generate background + full scene images for a single scene.

    Args:
        ref_images: Character reference image bytes (from generate_character_refs).
            Passed as style references to the full-scene generation for character
            consistency across scenes.
    """
    scene_num = scene["scene_number"]
    label = f"scene_{scene_num}"

    hd_dir = OUTPUT_BASE / story_id / "hd"
    pixel_dir = OUTPUT_BASE / story_id / "pixel"

    # --- Background-only image (no character refs needed) ---
    bg_prompt = BACKGROUND_PROMPT_PREFIX.format(scene_description=scene["background_prompt"])
    logger.info("[%s] Generating background...", label)

    bg_bytes = await generate_image(
        client, bg_prompt, aspect_ratio="16:9", label=f"{label}_bg",
    )

    if bg_bytes:
        save_image(bg_bytes, hd_dir / f"scene_{scene_num}_bg.png")
        if not skip_pixel:
            save_pixel_art(bg_bytes, pixel_dir / f"scene_{scene_num}_bg.png")
    else:
        logger.error("[%s] Background generation FAILED", label)

    if prompts_log is not None:
        prompts_log.append({
            "scene": scene_num,
            "type": "background",
            "prompt": bg_prompt,
            "success": bg_bytes is not None,
        })

    await asyncio.sleep(API_DELAY)

    # --- Full scene image (with background + character refs for consistency) ---
    # Build reference images list: background first, then characters
    scene_ref_images: Optional[List[bytes]] = None
    ref_parts: List[bytes] = []

    # Include the background as style reference so the full scene matches
    if bg_bytes:
        ref_parts.append(bg_bytes)

    # Add character refs for characters present in this scene
    present_chars: List[str] = []
    if ref_images:
        present = scene.get("entities_in_scene", [])
        for c in present:
            if c in ref_images:
                ref_parts.append(ref_images[c])
                present_chars.append(c)

    if ref_parts:
        scene_ref_images = ref_parts
        logger.info("[%s] Using %d reference images (1 background + %d characters: %s)",
                    label, len(ref_parts), len(present_chars), present_chars)

    # Add reference instruction to prompt when refs are available
    full_prompt = FULL_SCENE_PROMPT_PREFIX.format(scene_description=scene["full_scene_prompt"])
    if scene_ref_images:
        # Build explicit character-to-image mapping
        char_listing = "\n".join(
            f"  - Image {i + 2}: {characters.get(c, c)} ('{c}')"
            for i, c in enumerate(present_chars)
        )
        full_prompt += (
            "\n## Reference Images (CRITICAL)\n"
            "The attached images are reference sheets for this scene.\n"
            f"- Image 1: The background environment. You MUST use the same "
            "environment, colors, lighting, and composition, then add the "
            "characters on top of it.\n"
            f"- Character references (one image per character):\n{char_listing}\n"
            "\n## STRICT RULES\n"
            f"- This scene has EXACTLY {len(present_chars)} characters/entities: "
            f"{', '.join(present_chars)}.\n"
            "- Draw EACH character EXACTLY ONCE. Do NOT duplicate any character.\n"
            "- Each character MUST match the visual style, colors, proportions, and "
            "distinctive features shown in its reference image.\n"
            "- Maintain consistency — the background and characters should look the same "
            "across all scenes.\n"
        )

    logger.info("[%s] Generating full scene...", label)

    full_bytes = await generate_image(
        client, full_prompt, aspect_ratio="16:9", label=f"{label}_full",
        reference_images=scene_ref_images,
    )

    if full_bytes:
        save_image(full_bytes, hd_dir / f"scene_{scene_num}_full.png")
        if not skip_pixel:
            save_pixel_art(full_bytes, pixel_dir / f"scene_{scene_num}_full.png")
    else:
        logger.error("[%s] Full scene generation FAILED", label)

    if prompts_log is not None:
        prompts_log.append({
            "scene": scene_num,
            "type": "full_scene",
            "prompt": full_prompt,
            "success": full_bytes is not None,
            "ref_images_used": list(ref_images.keys()) if (scene_ref_images and ref_images) else [],
        })


async def generate_character_refs(
    client: Any,
    story_id: str,
    characters: Dict[str, str],
    skip_pixel: bool = False,
    prompts_log: List[Dict[str, Any]] = None,
) -> Dict[str, bytes]:
    """Generate character reference sheet images.

    Returns:
        Dict mapping character name -> raw image bytes (for use as style reference).
    """
    refs_dir = OUTPUT_BASE / story_id / "refs"
    ref_images: Dict[str, bytes] = {}

    for char_name, char_desc in characters.items():
        prompt = CHARACTER_REF_PROMPT.format(character_description=char_desc)
        logger.info("[refs] Generating reference for %s...", char_name)

        ref_bytes = await generate_image(
            client, prompt, aspect_ratio="1:1", label=f"ref_{char_name}",
        )

        if ref_bytes:
            ref_images[char_name] = ref_bytes
            save_image(ref_bytes, refs_dir / f"{char_name}.png")
            if not skip_pixel:
                save_pixel_art(ref_bytes, refs_dir / f"{char_name}_pixel.png", target_w=64, target_h=64)
        else:
            logger.error("[refs] Reference generation FAILED for %s", char_name)

        if prompts_log is not None:
            prompts_log.append({
                "character": char_name,
                "type": "reference",
                "prompt": prompt,
                "success": ref_bytes is not None,
            })

        await asyncio.sleep(API_DELAY)

    return ref_images


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Story A test images (HD + pixel art)")
    parser.add_argument("--story", type=str, default="A",
                        help="Story ID (default: A)")
    parser.add_argument("--scenes", type=int, nargs="*", default=None,
                        help="Generate only specific scene numbers (e.g., --scenes 1 2)")
    parser.add_argument("--skip-pixel", action="store_true",
                        help="Skip pixel art downscale step")
    parser.add_argument("--hd-only", action="store_true",
                        help="Alias for --skip-pixel")
    parser.add_argument("--skip-refs", action="store_true",
                        help="Skip character reference sheet generation")
    parser.add_argument("--load-refs", action="store_true",
                        help="Load existing ref images from disk instead of regenerating")
    parser.add_argument("--api-key", type=str, default=None,
                        help="Gemini API key (default: GEMINI_API_KEY env var)")
    args = parser.parse_args()

    skip_pixel = args.skip_pixel or args.hd_only

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.error("No API key. Set GEMINI_API_KEY or use --api-key.")
        sys.exit(1)

    # Load scene data
    with open(SCENES_JSON) as f:
        data = json.load(f)

    story_id = data["story_id"]
    characters = data["characters"]
    scenes = data["scenes"]

    if args.scenes:
        scenes = [s for s in scenes if s["scene_number"] in args.scenes]
        if not scenes:
            logger.error("No matching scenes found for --scenes %s", args.scenes)
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("STORY %s: %s", story_id, data["title"])
    logger.info("Scenes: %s | Pixel art: %s | Refs: %s",
                [s["scene_number"] for s in scenes],
                "skip" if skip_pixel else "yes",
                "skip" if args.skip_refs else "yes")
    logger.info("=" * 60)

    client = genai.Client(api_key=api_key)
    prompts_log: List[Dict[str, Any]] = []

    t_total = time.time()

    # Step 1: Generate character references FIRST (used as style refs for scenes)
    ref_images: Dict[str, bytes] = {}
    if args.load_refs:
        # Load existing refs from disk
        refs_dir = OUTPUT_BASE / story_id / "refs"
        logger.info("-" * 40)
        logger.info("Step 1: Loading existing character refs from %s", refs_dir)
        logger.info("-" * 40)
        for char_name in characters:
            ref_path = refs_dir / f"{char_name}.png"
            if ref_path.exists():
                ref_images[char_name] = ref_path.read_bytes()
                logger.info("Loaded ref: %s (%d bytes)", ref_path.name, len(ref_images[char_name]))
            else:
                logger.warning("Ref not found: %s", ref_path)
        logger.info("Loaded %d/%d character refs: %s",
                    len(ref_images), len(characters), list(ref_images.keys()))
    elif not args.skip_refs:
        logger.info("-" * 40)
        logger.info("Step 1: Character reference sheets (generated first for consistency)")
        logger.info("-" * 40)

        try:
            ref_images = await generate_character_refs(
                client, story_id, characters,
                skip_pixel=skip_pixel, prompts_log=prompts_log,
            )
            logger.info("Generated %d/%d character refs: %s",
                        len(ref_images), len(characters), list(ref_images.keys()))
        except Exception as exc:
            logger.error("Character refs FAILED: %s: %s", type(exc).__name__, exc)
    else:
        logger.info("Skipping character refs (--skip-refs)")

    # Step 2: Generate scenes (with character refs for full-scene consistency)
    for scene in scenes:
        logger.info("-" * 40)
        logger.info("Scene %d: %s", scene["scene_number"], scene["title"])
        logger.info("-" * 40)

        try:
            await generate_scene(
                client, story_id, scene, characters,
                skip_pixel=skip_pixel, prompts_log=prompts_log,
                ref_images=ref_images if ref_images else None,
            )
        except Exception as exc:
            logger.error("Scene %d FAILED: %s: %s",
                         scene["scene_number"], type(exc).__name__, exc)

    # Save prompts log
    log_path = OUTPUT_BASE / story_id / "prompts_log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as f:
        json.dump(prompts_log, f, indent=2)
    logger.info("Prompts log saved to %s", log_path)

    elapsed = time.time() - t_total
    logger.info("=" * 60)
    logger.info("ALL DONE in %.1fs", elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
