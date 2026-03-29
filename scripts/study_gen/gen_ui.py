#!/usr/bin/env python3
"""Merged Flask UI for batch scene generation + entity extraction.

Phase 1: Batch generation of scenes (bg + refs + composition)
Phase 2: Per-scene entity extraction onto white backgrounds

Usage:
    python -m scripts.study_gen.gen_ui --api-key YOUR_KEY
    python -m scripts.study_gen.gen_ui  # uses GEMINI_API_KEY env var
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import sys
import threading
import time
import uuid

from dotenv import load_dotenv
load_dotenv()
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, render_template_string, request, send_file, jsonify
from PIL import Image

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("gen_ui")

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------

STORIES_DIR = PROJECT_ROOT / "data" / "study_scenes"
TRAINING_DIR = PROJECT_ROOT / "data" / "training"
PROLIFIC_DIR = PROJECT_ROOT / "data" / "prolific_scenes"
OUTPUT_BASE = PROJECT_ROOT / "data" / "prolific_gen"
API_DELAY = 2.0
BATCH_CONCURRENCY = 3

STORY_FILES: Dict[str, Any] = {
    "A": "story_a_balloon_seller.json",
    "B": "story_b_market_mixup.json",
    "C": "story_c_night_garden.json",
    "D": "story_d_runaway_train.json",
    "T1": ("training", "training_1.json"),
    "T2": ("training", "training_2.json"),
}

# Auto-discover prolific scenes
for _f in sorted(PROLIFIC_DIR.glob("*.json")):
    STORY_FILES[_f.stem] = ("prolific", _f.name)

# ---------------------------------------------------------------------------
# Global art style (consistent across all stories and all prompts)
# ---------------------------------------------------------------------------

ART_STYLE = """\
Soft cartoon vector illustration with rounded brown outlines, smooth gradients, \
and gentle cel-shading. Saturated but warm color palette (rich greens, warm yellows, \
soft blues, earthy browns). Trees and foliage have round organic shapes. Grass is \
textured with small wildflowers and clovers. Skies are soft blue with fluffy white \
clouds. Lighting is warm and even — no harsh shadows. Surfaces have subtle texture \
(wood grain, stone patterns). Overall feel: cozy, inviting children's picture book \
similar to classic storybook apps. Flat side-view composition like a 2D parallax \
scene — no 3D perspective."""

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

BACKGROUND_PROMPT_PREFIX = """\
Create a BACKGROUND ONLY illustration — no characters, no objects, no entities. \
Just the environment and atmosphere.

## Scene environment
{scene_description}

## Art Style — MUST FOLLOW EXACTLY
{art_style}

## Additional Rules
- **The ground or floor surface MUST be clearly visible** at approximately \
  70% from the top of the image.
- **Rich details**: atmospheric gradients, clouds, distant elements, textures.
- **NO characters, people, or animals** — purely the background environment.
- **NO text, labels, numbers, coordinates, or writing of any kind** in the image.
"""

FULL_SCENE_PROMPT_PREFIX = """\
Create a complete scene illustration with all characters and objects.

## Scene
{scene_description}

## Art Style — MUST FOLLOW EXACTLY
{art_style}

## Additional Rules
- **The ground or floor surface MUST be clearly visible** at approximately \
  70% from the top of the image.
- **Rich details**: atmospheric gradients, clear character features, expressive poses.
- **Characters must be clearly visible and distinct**: each character should be \
  easy to identify and separate from the background.
- **NO text, labels, numbers, coordinates, or writing of any kind** in the image.
- **NO ANTHROPOMORPHISM** for non-living objects: tables, wagons, and objects \
  must NOT have faces, eyes, or human expressions.
"""

CHARACTER_REF_PROMPT = """\
Create an illustration of the following entity on a SOLID WHITE background. \
The white must be perfectly uniform — no gradients, no shading, no variation.

## Entity
{character_description}

## Art Style — MUST FOLLOW EXACTLY
{art_style}

## Additional Rules
- **Side view** (like a 2D storybook): flat side profile.
- **The entity should fill most of the image** — center it, leave only a small \
  margin of white around it.
- If the entity is a person or animal, show the full body from head to feet \
  with clearly distinct body parts.
- If the entity is an object (box, table, wagon, etc.), draw it realistically \
  as an inanimate object. **NO ANTHROPOMORPHISM**: objects must NOT have faces, \
  eyes, mouths, arms, legs, or any human/animal features. A box is just a box.
- **NO other elements**: no ground, no shadow, no text, no decorations. \
  ONLY the entity on solid white.
- **NO text, labels, or writing of any kind.**
"""

EXTRACT_PROMPT = """\
Edit the attached image. Replace EVERYTHING with solid white (#FFFFFF) EXCEPT \
the character/object described below. The result must be the EXACT SAME image \
dimensions with the character/object in the EXACT SAME position, size, and pose.

## Character/object to KEEP
{character_description}

## CRITICAL RULES — NO EXCEPTIONS
- **DO NOT move, resize, re-draw, or re-interpret the character/object.** \
  It must remain pixel-perfect in its original position.
- **Replace ALL other pixels with solid white (#FFFFFF).** This includes: \
  the background, the ground/floor, other characters, other objects, shadows, \
  and any element that is not the specified character/object.
- **Keep the EXACT same image dimensions** (same width and height as the input).
- **The character/object must stay at its EXACT original position** in the image — \
  do not center it, do not crop, do not reframe.
- **Do NOT add anything**: no text, no labels, no outlines, no shadows, no ground line.
- This is an IMAGE EDITING task, not a generation task. Edit the existing image.
"""

# ---------------------------------------------------------------------------
# Image generation helpers
# ---------------------------------------------------------------------------

client: Optional[Any] = None


async def generate_image(
    prompt: str,
    aspect_ratio: str = "16:9",
    label: str = "image",
    reference_images: Optional[List[bytes]] = None,
) -> Optional[bytes]:
    if reference_images:
        contents: List[Any] = []
        for img_bytes in reference_images:
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
                        image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
                    ),
                ),
                timeout=IMAGE_TIMEOUT,
            )
            elapsed = time.time() - t0

            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        logger.info("[%s] Attempt %d/%d: got %d bytes in %.1fs",
                                    label, attempt, IMAGE_MAX_RETRIES, len(part.inline_data.data), elapsed)
                        return part.inline_data.data

            logger.warning("[%s] Attempt %d/%d: no image data (%.1fs)", label, attempt, IMAGE_MAX_RETRIES, elapsed)
        except Exception as exc:
            logger.warning("[%s] Attempt %d/%d failed (%.1fs): %s", label, attempt, IMAGE_MAX_RETRIES, time.time() - t0, exc)

        if attempt < IMAGE_MAX_RETRIES:
            await asyncio.sleep(API_DELAY)

    return None


async def extract_character(
    scene_image_bytes: bytes,
    character_desc: str,
) -> Optional[bytes]:
    prompt = EXTRACT_PROMPT.format(character_description=character_desc)
    contents = [
        types.Part.from_bytes(data=scene_image_bytes, mime_type="image/png"),
        prompt,
    ]
    src_img = Image.open(io.BytesIO(scene_image_bytes))
    src_w, src_h = src_img.size
    ratio = src_w / src_h
    if abs(ratio - 16 / 9) < 0.1:
        aspect = "16:9"
    elif abs(ratio - 4 / 3) < 0.1:
        aspect = "4:3"
    elif abs(ratio - 1) < 0.1:
        aspect = "1:1"
    else:
        aspect = "16:9"

    for attempt in range(1, IMAGE_MAX_RETRIES + 1):
        try:
            t0 = time.time()
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=IMAGE_MODEL_ID,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(aspect_ratio=aspect),
                    ),
                ),
                timeout=IMAGE_TIMEOUT,
            )
            elapsed = time.time() - t0
            if response.candidates and response.candidates[0].content:
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        logger.info("[extract] Attempt %d/%d: got %d bytes in %.1fs",
                                    attempt, IMAGE_MAX_RETRIES, len(part.inline_data.data), elapsed)
                        return part.inline_data.data
            logger.warning("[extract] Attempt %d/%d: no image data (%.1fs)", attempt, IMAGE_MAX_RETRIES, elapsed)
        except Exception as exc:
            logger.warning("[extract] Attempt %d/%d failed (%.1fs): %s",
                           attempt, IMAGE_MAX_RETRIES, time.time() - t0, exc)
        if attempt < IMAGE_MAX_RETRIES:
            await asyncio.sleep(API_DELAY)
    return None


def save_image(image_bytes: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(image_bytes))
    img.save(str(path), format="PNG")


# ---------------------------------------------------------------------------
# Ref sharing across variants
# ---------------------------------------------------------------------------

def find_sibling_refs(story_id: str, characters: Dict[str, str]) -> Dict[str, bytes]:
    """Find existing refs from sibling variants of the same animation.

    For story_id like 'study1_A1_B', checks:
      1. prolific_gen/study1_A1_B/refs/
      2. prolific_gen/study1_A1/refs/   (old format without variant suffix)
      3. prolific_gen/study1_A1_A/refs/  (variant A)
      4. prolific_gen/study1_A1_C/refs/, study1_A1_D/refs/  (other variants)
    """
    ref_images: Dict[str, bytes] = {}
    needed = set(characters.keys())

    # Extract base animation name (e.g. 'study1_A1' from 'study1_A1_B')
    parts = story_id.rsplit("_", 1)
    if len(parts) == 2 and len(parts[1]) == 1:
        base = parts[0]
        variant = parts[1]
    else:
        base = story_id
        variant = None

    # Candidate directories to check, in priority order
    candidates = [OUTPUT_BASE / story_id / "refs"]
    if variant:
        candidates.append(OUTPUT_BASE / base / "refs")  # old format
        for v in ["A", "B", "C", "D"]:
            if v != variant:
                candidates.append(OUTPUT_BASE / f"{base}_{v}" / "refs")

    for refs_dir in candidates:
        if not refs_dir.exists():
            continue
        for char_name in list(needed):
            ref_path = refs_dir / f"{char_name}.png"
            if ref_path.exists():
                ref_images[char_name] = ref_path.read_bytes()
                needed.discard(char_name)
                logger.info("Found existing ref for '%s' at %s", char_name, ref_path)
        if not needed:
            break

    return ref_images


# ---------------------------------------------------------------------------
# Scene generation pipeline (used by both single + batch)
# ---------------------------------------------------------------------------

async def generate_single_scene(
    story_id: str,
    scene: Dict[str, Any],
    characters: Dict[str, str],
    ref_images: Dict[str, bytes],
    mode: str = "all",
) -> Dict[str, Any]:
    """Generate a single scene. Returns result dict with paths."""
    scene_num = scene["scene_number"]
    t0 = time.time()
    result: Dict[str, Any] = {"scene": scene_num, "story_id": story_id, "refs": []}

    hd_dir = OUTPUT_BASE / story_id / "hd"
    refs_dir = OUTPUT_BASE / story_id / "refs"

    gen_refs = mode == "all" and not ref_images
    gen_bg = mode in ("all", "bg_only")
    gen_full = mode in ("all", "full_only")

    # Generate entity refs if needed
    if gen_refs:
        logger.info("[%s] Generating entity references...", story_id)
        for char_name, char_desc in characters.items():
            prompt = CHARACTER_REF_PROMPT.format(character_description=char_desc, art_style=ART_STYLE)
            ref_bytes = await generate_image(prompt, aspect_ratio="1:1", label=f"{story_id}/ref_{char_name}")
            if ref_bytes:
                ref_images[char_name] = ref_bytes
                hd_path = refs_dir / f"{char_name}.png"
                save_image(ref_bytes, hd_path)
                result["refs"].append({"name": char_name, "hd": str(hd_path)})
            else:
                logger.error("[%s] Ref generation FAILED for %s", story_id, char_name)
            await asyncio.sleep(API_DELAY)
    else:
        # Copy sibling refs into this variant's refs dir if not already there
        for char_name, ref_bytes in ref_images.items():
            local_ref = refs_dir / f"{char_name}.png"
            if not local_ref.exists():
                save_image(ref_bytes, local_ref)

    # Return existing refs info
    if not result["refs"] and ref_images:
        for char_name in ref_images:
            hd_path = refs_dir / f"{char_name}.png"
            if hd_path.exists():
                result["refs"].append({"name": char_name, "hd": str(hd_path)})

    # Generate background
    bg_bytes: Optional[bytes] = None
    bg_hd_path = hd_dir / f"scene_{scene_num}_bg.png"

    if bg_hd_path.exists() and mode != "bg_only":
        # Reuse existing background (unless explicitly regenerating bg)
        bg_bytes = bg_hd_path.read_bytes()
        result["bg_hd"] = str(bg_hd_path)
        logger.info("[%s] Reusing existing background", story_id)
    elif gen_bg:
        bg_prompt = BACKGROUND_PROMPT_PREFIX.format(
            scene_description=scene["background_prompt"], art_style=ART_STYLE)
        bg_bytes = await generate_image(bg_prompt, aspect_ratio="16:9",
                                        label=f"{story_id}/scene_{scene_num}_bg")
        if bg_bytes:
            save_image(bg_bytes, bg_hd_path)
            result["bg_hd"] = str(bg_hd_path)
        await asyncio.sleep(API_DELAY)

    if not gen_full:
        full_hd_path = hd_dir / f"scene_{scene_num}_full.png"
        if full_hd_path.exists():
            result["full_hd"] = str(full_hd_path)
        result["elapsed"] = time.time() - t0
        result["has_images"] = True
        return result

    # Generate composed scene with composition_instructions (fallback to full_scene_prompt)
    ref_parts: List[bytes] = []
    present_chars: List[str] = []

    if bg_bytes:
        ref_parts.append(bg_bytes)

    present = scene.get("entities_in_scene", [])
    for c in present:
        if c in ref_images:
            ref_parts.append(ref_images[c])
            present_chars.append(c)

    comp_text = scene.get("composition_instructions") or scene["full_scene_prompt"]
    full_prompt = FULL_SCENE_PROMPT_PREFIX.format(scene_description=comp_text, art_style=ART_STYLE)
    if ref_parts:
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

    full_bytes = await generate_image(
        full_prompt, aspect_ratio="16:9", label=f"{story_id}/scene_{scene_num}_full",
        reference_images=ref_parts if ref_parts else None,
    )

    if full_bytes:
        full_hd_path = hd_dir / f"scene_{scene_num}_full.png"
        save_image(full_bytes, full_hd_path)
        result["full_hd"] = str(full_hd_path)

    result["elapsed"] = time.time() - t0
    result["has_images"] = True
    return result


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------

batch_jobs: Dict[str, Dict[str, Any]] = {}


def discover_batch_scenes(variant: str) -> List[Path]:
    """Find prolific scene JSONs matching the variant filter."""
    all_jsons = sorted(PROLIFIC_DIR.glob("study1_*.json"))
    if variant == "all":
        return all_jsons
    if variant == "all_new":
        return [f for f in all_jsons if f.stem.endswith(("_B", "_C", "_D"))]
    # Single variant: B, C, or D
    suffix = f"_{variant}"
    return [f for f in all_jsons if f.stem.endswith(suffix)]


def run_batch_in_thread(job_id: str, scene_files: List[Path], regenerate: bool):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_batch(job_id, scene_files, regenerate))
    finally:
        loop.close()


async def _run_batch(job_id: str, scene_files: List[Path], regenerate: bool):
    job = batch_jobs[job_id]
    job["total"] = len(scene_files)
    job["status"] = "running"
    sem = asyncio.Semaphore(BATCH_CONCURRENCY)

    async def process_one(scene_path: Path):
        async with sem:
            story_id = None
            try:
                with open(scene_path) as f:
                    data = json.load(f)
                story_id = data["story_id"]
                characters = data.get("entities", data.get("characters", {}))
                if "characters" not in data and "entities" in data:
                    data["characters"] = data["entities"]

                # Skip if already generated (unless regenerate)
                full_path = OUTPUT_BASE / story_id / "hd" / "scene_1_full.png"
                if full_path.exists() and not regenerate:
                    logger.info("[batch] Skipping %s — already generated", story_id)
                    job["skipped"] += 1
                    job["done"] += 1
                    job["results"].append({
                        "story_id": story_id,
                        "status": "skipped",
                        "full_hd": str(full_path),
                    })
                    return

                job["current"] = story_id

                # Find refs from sibling variants
                ref_images = find_sibling_refs(story_id, characters)

                # Generate each scene in the story
                for scene in data["scenes"]:
                    result = await generate_single_scene(
                        story_id, scene, characters, ref_images, mode="all")
                    job["results"].append({
                        "story_id": story_id,
                        "scene": scene["scene_number"],
                        "status": "done" if result.get("full_hd") else "error",
                        "full_hd": result.get("full_hd"),
                        "bg_hd": result.get("bg_hd"),
                        "elapsed": result.get("elapsed", 0),
                    })

                job["done"] += 1
                logger.info("[batch] Done %s (%d/%d)", story_id, job["done"], job["total"])

            except Exception as exc:
                logger.error("[batch] Error processing %s: %s", scene_path.stem, exc)
                job["errors"] += 1
                job["done"] += 1
                job["results"].append({
                    "story_id": story_id or scene_path.stem,
                    "status": "error",
                    "error": str(exc),
                })

    tasks = [process_one(sf) for sf in scene_files]
    await asyncio.gather(*tasks)

    job["status"] = "done"
    job["current"] = None
    logger.info("[batch] Batch %s complete: %d done, %d skipped, %d errors",
                job_id, job["done"], job["skipped"], job["errors"])


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

# In-memory state for single-scene mode
state: Dict[str, Any] = {
    "story_key": None,
    "story_data": None,
    "current_scene": 1,
    "ref_images": {},
    "generated": {},
}


def load_story(key: str) -> Dict[str, Any]:
    entry = STORY_FILES[key]
    if isinstance(entry, tuple):
        subdir, fname = entry
        if subdir == "training":
            path = TRAINING_DIR / fname
        elif subdir == "prolific":
            path = PROLIFIC_DIR / fname
        else:
            path = STORIES_DIR / fname
    else:
        path = STORIES_DIR / entry
    with open(path) as f:
        data = json.load(f)
    if "characters" not in data and "entities" in data:
        data["characters"] = data["entities"]
    return data


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Scene Generator & Extractor</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }
h1 { margin-bottom: 5px; color: #e94560; }
h2 { color: #0f3460; background: #e94560; display: inline-block; padding: 4px 12px; border-radius: 4px; margin: 10px 0; }
h3 { color: #aaa; margin: 8px 0; }
.tabs { display: flex; gap: 0; margin: 20px 0 0 0; }
.tab { padding: 10px 24px; background: #16213e; color: #aaa; border: none; border-radius: 8px 8px 0 0; font-size: 15px; font-weight: bold; cursor: pointer; }
.tab.active { background: #0f3460; color: #fff; }
.tab-content { display: none; background: #0f3460; border-radius: 0 8px 8px 8px; padding: 20px; }
.tab-content.active { display: block; }
.controls { margin: 15px 0; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
select, button { padding: 8px 16px; border: none; border-radius: 4px; font-size: 14px; cursor: pointer; }
select { background: #16213e; color: #eee; }
button { background: #e94560; color: white; font-weight: bold; }
button:hover { background: #c73652; }
button:disabled { background: #555; cursor: not-allowed; }
button.validate { background: #0f3460; }
button.validate:hover { background: #1a4a8a; }
button.regen { background: #e9a045; color: #1a1a2e; }
button.extract { background: #2e7d32; }
button.extract:hover { background: #388e3c; }
button.flag { background: transparent; border: 2px solid #e94560; color: #e94560; padding: 4px 10px; font-size: 12px; }
button.flag.flagged { background: #e94560; color: white; }
.status { margin: 10px 0; padding: 10px; background: #16213e; border-radius: 4px; min-height: 40px; }
.progress-bar { width: 100%; height: 24px; background: #16213e; border-radius: 12px; margin: 10px 0; overflow: hidden; }
.progress-fill { height: 100%; background: #2e7d32; border-radius: 12px; transition: width 0.3s; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: bold; min-width: 40px; }
.gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin: 15px 0; }
.gallery-card { background: #16213e; border-radius: 8px; padding: 10px; position: relative; }
.gallery-card h4 { margin-bottom: 6px; color: #e94560; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.gallery-card img { width: 100%; border-radius: 4px; cursor: pointer; }
.gallery-card .card-actions { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
.gallery-card .status-badge { position: absolute; top: 6px; right: 6px; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
.badge-done { background: #2e7d32; }
.badge-error { background: #c62828; }
.badge-skipped { background: #555; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 15px; margin: 15px 0; }
.card { background: #16213e; border-radius: 8px; padding: 12px; }
.card h4 { margin-bottom: 8px; color: #e94560; }
.card img { width: 100%; border-radius: 4px; }
.card .downloads { margin-top: 8px; display: flex; gap: 8px; }
.card .downloads a { color: #e94560; text-decoration: none; font-size: 13px; }
.refs-section { border: 1px solid #333; border-radius: 8px; padding: 15px; margin: 15px 0; }
.refs-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.scene-nav { display: flex; gap: 8px; margin: 10px 0; flex-wrap: wrap; }
.scene-nav button { padding: 6px 12px; font-size: 12px; }
.scene-nav button.active { background: #0f3460; }
.scene-nav button.done { background: #2e7d32; }
/* Extraction section */
.ext-scene-row { background: #16213e; border-radius: 8px; padding: 15px; margin: 10px 0; }
.ext-scene-row h4 { color: #e94560; margin-bottom: 10px; }
.ext-content { display: flex; gap: 15px; align-items: flex-start; flex-wrap: wrap; }
.ext-preview { flex: 0 0 350px; }
.ext-preview img { width: 100%; border-radius: 4px; }
.ext-panel { flex: 1; min-width: 250px; }
.ext-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-top: 10px; }
.ext-card { background: #1a1a2e; border-radius: 8px; padding: 8px; text-align: center; }
.ext-card h5 { margin-bottom: 4px; color: #e94560; font-size: 12px; }
.ext-card img { max-width: 100%; border-radius: 4px; background: white; }
.ext-card .actions { margin-top: 6px; display: flex; gap: 6px; justify-content: center; }
.ext-card .actions a, .ext-card .actions button { font-size: 11px; padding: 3px 8px; }
.ext-card .actions a { color: #e94560; text-decoration: none; }
#loading { display: none; }
#loading.show { display: block; }
.spinner { display: inline-block; width: 20px; height: 20px; border: 3px solid #555; border-top-color: #e94560; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 8px; }
@keyframes spin { to { transform: rotate(360deg); } }
.log-box { background: #111; color: #0f0; font-family: monospace; font-size: 12px; padding: 10px; border-radius: 4px; max-height: 200px; overflow-y: auto; margin: 10px 0; white-space: pre-wrap; }
</style>
</head>
<body>

<h1>Scene Generator & Extractor</h1>

<div class="tabs">
  <button class="tab active" onclick="switchTab('batch')">Phase 1: Batch Generation</button>
  <button class="tab" onclick="switchTab('single')">Single Scene</button>
  <button class="tab" onclick="switchTab('extract')">Phase 2: Extraction</button>
</div>

<!-- ==================== BATCH TAB ==================== -->
<div class="tab-content active" id="tab-batch">
  <h3>Batch Generation</h3>
  <div class="controls">
    <label>Variant:</label>
    <select id="batchVariant">
      <option value="B">_B scenes (25)</option>
      <option value="C">_C scenes (25)</option>
      <option value="D">_D scenes (25)</option>
      <option value="all_new" selected>All new (_B + _C + _D = 75)</option>
      <option value="all">All (100)</option>
    </select>
    <label><input type="checkbox" id="batchRegenerate"> Regenerate existing</label>
    <button id="btnBatchStart" onclick="startBatch()">Generate Batch</button>
    <button id="btnBatchStop" style="display:none;" disabled>Running...</button>
  </div>

  <div id="batchProgress" style="display:none;">
    <div class="progress-bar">
      <div class="progress-fill" id="batchProgressFill" style="width:0%">0%</div>
    </div>
    <div class="status" id="batchStatus">Starting...</div>
  </div>

  <div id="batchGallery" class="gallery"></div>

  <div id="batchActions" style="display:none; margin-top:15px;">
    <button class="regen" onclick="regenerateFlagged()">Regenerate Flagged</button>
    <span id="flagCount" style="margin-left:10px; color:#aaa;"></span>
  </div>
</div>

<!-- ==================== SINGLE SCENE TAB ==================== -->
<div class="tab-content" id="tab-single">
  <h3>Single Scene Generation</h3>
  <div class="controls">
    <label>Story:</label>
    <select id="storySelect">
      {% for key, title in stories %}
      <option value="{{ key }}" {% if key == current_story %}selected{% endif %}>{{ key }} — {{ title }}</option>
      {% endfor %}
    </select>
    <button onclick="loadStory()">Load Story</button>
  </div>

  <div id="storyInfo"></div>
  <div class="scene-nav" id="sceneNav"></div>

  <div class="controls" id="genControls" style="display:none;">
    <button id="btnGenScene" onclick="generateScene()">Generate Scene <span id="sceneNum">1</span></button>
    <button id="btnRegenBg" class="regen" onclick="regenerateBg()" style="display:none;">Regen Background</button>
    <button id="btnRegenFull" class="regen" onclick="regenerateFull()" style="display:none;">Regen Full Scene</button>
    <button id="btnRegenAll" class="regen" onclick="regenerateAll()" style="display:none;">Regen All</button>
    <button id="btnValidate" class="validate" onclick="validateScene()" style="display:none;">Validate & Next</button>
  </div>

  <div class="status" id="singleStatus"></div>
  <div id="loading"><span class="spinner"></span><span id="loadingText">Generating...</span></div>

  <div id="refsSection" style="display:none;">
    <div class="refs-section">
      <h3>Entity References</h3>
      <div class="refs-grid" id="refsGrid"></div>
    </div>
  </div>

  <div class="grid" id="imagesGrid"></div>
</div>

<!-- ==================== EXTRACTION TAB ==================== -->
<div class="tab-content" id="tab-extract">
  <h3>Entity Extraction</h3>
  <div class="controls">
    <label>Variant:</label>
    <select id="extractVariant">
      <option value="A">_A scenes</option>
      <option value="B">_B scenes</option>
      <option value="C">_C scenes</option>
      <option value="D">_D scenes</option>
      <option value="all">All</option>
    </select>
    <button onclick="loadExtractScenes()">Load Scenes</button>
  </div>

  <div class="status" id="extractStatus"></div>
  <div id="extractLoading" style="display:none;"><span class="spinner"></span><span id="extractLoadingText">Extracting...</span></div>
  <div id="extractContainer"></div>
</div>

<script>
// ==================== Tab switching ====================
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  document.querySelectorAll('.tab')[{batch:0, single:1, extract:2}[tab]].classList.add('active');
}

// ==================== BATCH ====================
let batchJobId = null;
let batchPollTimer = null;
let flaggedScenes = new Set();

async function startBatch() {
  const variant = document.getElementById('batchVariant').value;
  const regenerate = document.getElementById('batchRegenerate').checked;

  document.getElementById('btnBatchStart').disabled = true;
  document.getElementById('batchProgress').style.display = 'block';
  document.getElementById('batchGallery').innerHTML = '';
  document.getElementById('batchActions').style.display = 'none';
  flaggedScenes.clear();

  try {
    const res = await fetch('/api/generate_batch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({variant, regenerate})
    });
    const data = await res.json();
    if (data.error) {
      document.getElementById('batchStatus').textContent = 'Error: ' + data.error;
      document.getElementById('btnBatchStart').disabled = false;
      return;
    }
    batchJobId = data.job_id;
    document.getElementById('batchStatus').textContent = 'Started batch: ' + data.total + ' scenes...';
    batchPollTimer = setInterval(pollBatch, 2000);
  } catch(e) {
    document.getElementById('batchStatus').textContent = 'Error: ' + e.message;
    document.getElementById('btnBatchStart').disabled = false;
  }
}

async function pollBatch() {
  if (!batchJobId) return;
  try {
    const res = await fetch('/api/batch_progress?job_id=' + batchJobId);
    const data = await res.json();

    const pct = data.total > 0 ? Math.round((data.done / data.total) * 100) : 0;
    document.getElementById('batchProgressFill').style.width = pct + '%';
    document.getElementById('batchProgressFill').textContent = pct + '%';

    let statusText = data.done + '/' + data.total + ' done';
    if (data.skipped > 0) statusText += ', ' + data.skipped + ' skipped';
    if (data.errors > 0) statusText += ', ' + data.errors + ' errors';
    if (data.current) statusText += ' — generating ' + data.current;
    document.getElementById('batchStatus').textContent = statusText;

    // Update gallery with results
    renderBatchGallery(data.results);

    if (data.status === 'done') {
      clearInterval(batchPollTimer);
      batchPollTimer = null;
      document.getElementById('btnBatchStart').disabled = false;
      document.getElementById('batchActions').style.display = 'block';
      document.getElementById('batchStatus').textContent = 'Batch complete! ' + statusText;
    }
  } catch(e) {
    console.error('Poll error:', e);
  }
}

function renderBatchGallery(results) {
  if (!results || results.length === 0) return;
  const bust = '&t=' + Date.now();
  let html = '';
  for (const r of results) {
    const isFlagged = flaggedScenes.has(r.story_id);
    html += '<div class="gallery-card">';
    html += '<span class="status-badge badge-' + r.status + '">' + r.status + '</span>';
    html += '<h4>' + r.story_id + '</h4>';
    if (r.full_hd) {
      html += '<img src="/api/image?path=' + encodeURIComponent(r.full_hd) + bust + '" onclick="window.open(this.src)">';
    } else {
      html += '<div style="height:120px;display:flex;align-items:center;justify-content:center;background:#0f3460;border-radius:4px;color:#555;">No image</div>';
    }
    html += '<div class="card-actions">';
    if (r.full_hd) {
      html += '<a href="/api/image?path=' + encodeURIComponent(r.full_hd) + '&download=1" download style="color:#e94560;text-decoration:none;font-size:12px;">Download</a>';
    }
    html += '<button class="flag ' + (isFlagged ? 'flagged' : '') + '" data-sid="' + r.story_id + '" data-action="flag">' + (isFlagged ? 'Flagged' : 'Flag') + '</button>';
    html += '</div></div>';
  }
  document.getElementById('batchGallery').innerHTML = html;
  updateFlagCount();
}

function toggleFlag(storyId, btn) {
  if (flaggedScenes.has(storyId)) {
    flaggedScenes.delete(storyId);
    btn.classList.remove('flagged');
    btn.textContent = 'Flag';
  } else {
    flaggedScenes.add(storyId);
    btn.classList.add('flagged');
    btn.textContent = 'Flagged';
  }
  updateFlagCount();
}

function updateFlagCount() {
  const el = document.getElementById('flagCount');
  el.textContent = flaggedScenes.size > 0 ? flaggedScenes.size + ' scene(s) flagged' : '';
}

async function regenerateFlagged() {
  if (flaggedScenes.size === 0) return;
  const storyIds = Array.from(flaggedScenes);
  document.getElementById('btnBatchStart').disabled = true;
  document.getElementById('batchProgress').style.display = 'block';
  document.getElementById('batchGallery').innerHTML = '';

  try {
    const res = await fetch('/api/generate_batch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({story_ids: storyIds, regenerate: true})
    });
    const data = await res.json();
    batchJobId = data.job_id;
    flaggedScenes.clear();
    updateFlagCount();
    batchPollTimer = setInterval(pollBatch, 2000);
  } catch(e) {
    document.getElementById('batchStatus').textContent = 'Error: ' + e.message;
    document.getElementById('btnBatchStart').disabled = false;
  }
}

// ==================== SINGLE SCENE ====================
let currentScene = 1;
let totalScenes = 0;
let validatedScenes = new Set();

async function loadStory() {
  const key = document.getElementById('storySelect').value;
  const res = await fetch('/api/load_story?key=' + key);
  const data = await res.json();
  if (data.error) { setSingleStatus('Error: ' + data.error); return; }
  totalScenes = data.total_scenes;
  currentScene = 1;
  validatedScenes.clear();
  document.getElementById('storyInfo').innerHTML = '<h2>' + data.title + '</h2> <h3>' + data.total_scenes + ' scenes, ' + data.num_characters + ' entities</h3>';
  document.getElementById('genControls').style.display = 'flex';
  updateSceneNav();
  updateSingleUI();
  let refsMsg = '';
  if (data.loaded_refs && data.loaded_refs.length > 0) {
    refsMsg = ' Loaded ' + data.loaded_refs.length + ' existing refs: ' + data.loaded_refs.join(', ') + '.';
  }
  setSingleStatus('Story loaded.' + refsMsg + ' Ready to generate scene 1.');
}

function updateSceneNav() {
  let html = '';
  for (let i = 1; i <= totalScenes; i++) {
    const cls = i === currentScene ? 'active' : (validatedScenes.has(i) ? 'done' : '');
    html += '<button class="' + cls + '" onclick="goToScene(' + i + ')">' + i + '</button>';
  }
  document.getElementById('sceneNav').innerHTML = html;
}

function goToScene(n) { currentScene = n; updateSceneNav(); updateSingleUI(); }

function updateSingleUI() {
  document.getElementById('sceneNum').textContent = currentScene;
  document.getElementById('imagesGrid').innerHTML = '';
  document.getElementById('refsSection').style.display = 'none';
  document.getElementById('refsGrid').innerHTML = '';
  document.getElementById('btnRegenBg').style.display = 'none';
  document.getElementById('btnRegenFull').style.display = 'none';
  document.getElementById('btnRegenAll').style.display = 'none';
  document.getElementById('btnValidate').style.display = 'none';
  loadExisting();
}

async function loadExisting() {
  const res = await fetch('/api/scene_status?scene=' + currentScene);
  const data = await res.json();
  if (data.has_images) showSingleImages(data);
}

function setSingleStatus(msg) { document.getElementById('singleStatus').textContent = msg; }

function showLoading(show, text) {
  document.getElementById('loading').className = show ? 'show' : '';
  if (text) document.getElementById('loadingText').textContent = text;
}

async function generateScene() {
  document.getElementById('btnGenScene').disabled = true;
  showLoading(true, 'Generating scene ' + currentScene + '...');
  setSingleStatus('Generating scene ' + currentScene + '...');
  try {
    const res = await fetch('/api/generate_scene?scene=' + currentScene);
    const data = await res.json();
    showLoading(false);
    if (data.error) { setSingleStatus('Error: ' + data.error); }
    else { setSingleStatus('Scene ' + currentScene + ' generated in ' + data.elapsed.toFixed(1) + 's'); showSingleImages(data); }
  } catch (e) { showLoading(false); setSingleStatus('Error: ' + e.message); }
  document.getElementById('btnGenScene').disabled = false;
}

async function regenerateBg() { await doGenerate('bg_only'); }
async function regenerateFull() { await doGenerate('full_only'); }
async function regenerateAll() { await doGenerate('all'); }
async function doGenerate(mode) {
  document.getElementById('btnGenScene').disabled = true;
  showLoading(true, 'Generating (' + mode + ')...');
  setSingleStatus('Generating scene ' + currentScene + ' (' + mode + ')...');
  try {
    const res = await fetch('/api/generate_scene?scene=' + currentScene + '&mode=' + mode);
    const data = await res.json();
    showLoading(false);
    if (data.error) { setSingleStatus('Error: ' + data.error); }
    else { setSingleStatus('Scene ' + currentScene + ' generated in ' + data.elapsed.toFixed(1) + 's'); showSingleImages(data); }
  } catch (e) { showLoading(false); setSingleStatus('Error: ' + e.message); }
  document.getElementById('btnGenScene').disabled = false;
}

function showSingleImages(data) {
  let html = '';
  const bust = '&t=' + Date.now();
  if (data.bg_hd) html += imgCard('Background', '/api/image?path=' + encodeURIComponent(data.bg_hd) + bust, data.bg_hd);
  if (data.full_hd) html += imgCard('Full Scene', '/api/image?path=' + encodeURIComponent(data.full_hd) + bust, data.full_hd);
  document.getElementById('imagesGrid').innerHTML = html;
  if (data.refs && data.refs.length > 0) {
    let rhtml = '';
    for (const ref of data.refs) rhtml += imgCard(ref.name, '/api/image?path=' + encodeURIComponent(ref.hd) + bust, ref.hd);
    document.getElementById('refsGrid').innerHTML = rhtml;
    document.getElementById('refsSection').style.display = 'block';
  }
  document.getElementById('btnRegenBg').style.display = 'inline-block';
  document.getElementById('btnRegenFull').style.display = 'inline-block';
  document.getElementById('btnRegenAll').style.display = 'inline-block';
  document.getElementById('btnValidate').style.display = currentScene <= totalScenes ? 'inline-block' : 'none';
}

function imgCard(title, src, path) {
  return '<div class="card"><h4>' + title + '</h4><img src="' + src + '"><div class="downloads"><a href="' + src + '&download=1" download>Download</a></div></div>';
}

function validateScene() {
  validatedScenes.add(currentScene);
  if (currentScene < totalScenes) { currentScene++; updateSceneNav(); updateSingleUI(); setSingleStatus('Scene ' + (currentScene-1) + ' validated.'); }
  else { updateSceneNav(); setSingleStatus('All scenes validated!'); }
}

// ==================== EXTRACTION ====================
let extractData = [];

async function loadExtractScenes() {
  const variant = document.getElementById('extractVariant').value;
  document.getElementById('extractStatus').textContent = 'Loading scenes...';
  try {
    const res = await fetch('/api/extract_list?variant=' + variant);
    const data = await res.json();
    extractData = data.scenes;
    document.getElementById('extractStatus').textContent = data.scenes.length + ' scenes loaded.';
    renderExtractScenes();
  } catch(e) {
    document.getElementById('extractStatus').textContent = 'Error: ' + e.message;
  }
}

function renderExtractScenes() {
  let html = '';
  const bust = '&t=' + Date.now();
  for (const scene of extractData) {
    html += '<div class="ext-scene-row" id="ext-row-' + scene.story_id + '">';
    html += '<h4>' + scene.story_id + ' — Scene ' + scene.scene_number + '</h4>';
    html += '<div class="ext-content">';

    if (scene.full_hd) {
      html += '<div class="ext-preview"><img src="/api/image?path=' + encodeURIComponent(scene.full_hd) + bust + '"></div>';
    } else {
      html += '<div class="ext-preview" style="height:150px;display:flex;align-items:center;justify-content:center;background:#16213e;border-radius:4px;color:#555;">No composed scene</div>';
    }

    html += '<div class="ext-panel">';
    if (scene.full_hd) {
      html += '<button class="extract" data-sid="' + scene.story_id + '" data-scene="' + scene.scene_number + '" data-action="extract-all">Extract Entities</button>';
    }
    html += '<div class="ext-grid" id="ext-grid-' + scene.story_id + '-' + scene.scene_number + '">';
    if (scene.extractions) {
      for (const ext of scene.extractions) {
        html += extCard(ext.name, ext.path, scene.story_id, scene.scene_number);
      }
    }
    html += '</div></div></div></div>';
  }
  document.getElementById('extractContainer').innerHTML = html;
}

function extCard(name, path, storyId, sceneNum) {
  const bust = '&t=' + Date.now();
  const src = '/api/image?path=' + encodeURIComponent(path) + bust;
  return '<div class="ext-card"><h5>' + name + '</h5><img src="' + src + '"><div class="actions">' +
    '<a href="' + src + '&download=1" download>Download</a>' +
    '<button class="regen" data-sid="' + storyId + '" data-scene="' + sceneNum + '" data-char="' + name + '" data-action="extract-one">Redo</button>' +
    '</div></div>';
}

async function extractAll(storyId, sceneNum) {
  const el = document.getElementById('extractLoading');
  el.style.display = 'block';
  document.getElementById('extractLoadingText').textContent = 'Extracting entities from ' + storyId + '...';
  document.getElementById('extractStatus').textContent = 'Extracting...';

  try {
    const res = await fetch('/api/extract_all?story_id=' + encodeURIComponent(storyId) + '&scene=' + sceneNum);
    const data = await res.json();
    el.style.display = 'none';
    if (data.error) {
      document.getElementById('extractStatus').textContent = 'Error: ' + data.error;
      return;
    }
    // Update grid
    const grid = document.getElementById('ext-grid-' + storyId + '-' + sceneNum);
    let html = '';
    for (const ext of data.extractions) {
      if (ext.path) html += extCard(ext.name, ext.path, storyId, sceneNum);
    }
    grid.innerHTML = html;
    document.getElementById('extractStatus').textContent = 'Extracted ' + data.extractions.length + ' entities (' + data.elapsed.toFixed(1) + 's)';
  } catch(e) {
    el.style.display = 'none';
    document.getElementById('extractStatus').textContent = 'Error: ' + e.message;
  }
}

async function extractOne(storyId, sceneNum, charName) {
  const el = document.getElementById('extractLoading');
  el.style.display = 'block';
  document.getElementById('extractLoadingText').textContent = 'Re-extracting ' + charName + '...';

  try {
    const res = await fetch('/api/extract?story_id=' + encodeURIComponent(storyId) + '&scene=' + sceneNum + '&character=' + encodeURIComponent(charName));
    const data = await res.json();
    el.style.display = 'none';
    if (data.error) {
      document.getElementById('extractStatus').textContent = 'Error: ' + data.error;
      return;
    }
    // Refresh the grid
    const grid = document.getElementById('ext-grid-' + storyId + '-' + sceneNum);
    const cards = grid.querySelectorAll('.ext-card');
    let replaced = false;
    cards.forEach(card => {
      if (card.querySelector('h5').textContent === charName) {
        card.outerHTML = extCard(charName, data.path, storyId, sceneNum);
        replaced = true;
      }
    });
    if (!replaced) grid.innerHTML += extCard(charName, data.path, storyId, sceneNum);
    document.getElementById('extractStatus').textContent = 'Re-extracted ' + charName + ' (' + data.elapsed.toFixed(1) + 's)';
  } catch(e) {
    el.style.display = 'none';
    document.getElementById('extractStatus').textContent = 'Error: ' + e.message;
  }
}

// ==================== Event delegation for dynamic buttons ====================
document.addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === 'flag') {
    toggleFlag(btn.dataset.sid, btn);
  } else if (action === 'extract-all') {
    extractAll(btn.dataset.sid, parseInt(btn.dataset.scene));
  } else if (action === 'extract-one') {
    extractOne(btn.dataset.sid, parseInt(btn.dataset.scene), btn.dataset.char);
  }
});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    stories = []
    for key in sorted(STORY_FILES.keys()):
        try:
            d = load_story(key)
            stories.append((key, d["title"]))
        except Exception:
            stories.append((key, f"(not found)"))
    return render_template_string(HTML_TEMPLATE, stories=stories, current_story=state.get("story_key", "A"))


# --- Single scene endpoints ---

@app.route("/api/load_story")
def api_load_story():
    key = request.args.get("key", "A")
    if key not in STORY_FILES:
        return jsonify({"error": f"Unknown story: {key}"})

    data = load_story(key)
    state["story_key"] = key
    state["story_data"] = data
    state["current_scene"] = 1
    state["generated"] = {}

    characters = data["characters"]
    # Find refs from sibling variants
    state["ref_images"] = find_sibling_refs(data["story_id"], characters)

    loaded_refs = list(state["ref_images"].keys())

    return jsonify({
        "title": data["title"],
        "total_scenes": len(data["scenes"]),
        "num_characters": len(characters),
        "characters": list(characters.keys()),
        "loaded_refs": loaded_refs,
    })


@app.route("/api/generate_scene")
def api_generate_scene():
    scene_num = int(request.args.get("scene", 1))
    data = state["story_data"]
    if not data:
        return jsonify({"error": "No story loaded"})

    scene = None
    for s in data["scenes"]:
        if s["scene_number"] == scene_num:
            scene = s
            break
    if not scene:
        return jsonify({"error": f"Scene {scene_num} not found"})

    story_id = data["story_id"]
    characters = data["characters"]
    mode = request.args.get("mode", "all")

    result = asyncio.run(generate_single_scene(
        story_id, scene, characters, state["ref_images"], mode))
    state["generated"][scene_num] = result
    return jsonify(result)


@app.route("/api/scene_status")
def api_scene_status():
    scene_num = int(request.args.get("scene", 1))
    if scene_num in state.get("generated", {}):
        return jsonify(state["generated"][scene_num])

    # Check disk for existing images
    data = state.get("story_data")
    if data:
        story_id = data["story_id"]
        hd_dir = OUTPUT_BASE / story_id / "hd"
        bg_path = hd_dir / f"scene_{scene_num}_bg.png"
        full_path = hd_dir / f"scene_{scene_num}_full.png"
        if bg_path.exists() or full_path.exists():
            result = {"scene": scene_num, "has_images": True, "refs": []}
            if bg_path.exists():
                result["bg_hd"] = str(bg_path)
            if full_path.exists():
                result["full_hd"] = str(full_path)
            # Refs
            refs_dir = OUTPUT_BASE / story_id / "refs"
            for char_name in data["characters"]:
                ref_path = refs_dir / f"{char_name}.png"
                if ref_path.exists():
                    result["refs"].append({"name": char_name, "hd": str(ref_path)})
            return jsonify(result)

    return jsonify({"has_images": False})


# --- Batch endpoints ---

@app.route("/api/generate_batch", methods=["POST"])
def api_generate_batch():
    body = request.get_json() or {}
    variant = body.get("variant", "all_new")
    regenerate = body.get("regenerate", False)
    story_ids = body.get("story_ids")  # For regenerating specific flagged scenes

    if story_ids:
        # Find JSONs for specific story_ids
        scene_files = []
        for sid in story_ids:
            p = PROLIFIC_DIR / f"{sid}.json"
            if p.exists():
                scene_files.append(p)
    else:
        scene_files = discover_batch_scenes(variant)

    if not scene_files:
        return jsonify({"error": "No scenes found for this filter"})

    job_id = str(uuid.uuid4())[:8]
    batch_jobs[job_id] = {
        "status": "starting",
        "total": len(scene_files),
        "done": 0,
        "skipped": 0,
        "errors": 0,
        "current": None,
        "results": [],
    }

    thread = threading.Thread(target=run_batch_in_thread, args=(job_id, scene_files, regenerate), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "total": len(scene_files)})


@app.route("/api/batch_progress")
def api_batch_progress():
    job_id = request.args.get("job_id", "")
    if job_id not in batch_jobs:
        return jsonify({"error": "Unknown job"})
    job = batch_jobs[job_id]
    return jsonify({
        "status": job["status"],
        "total": job["total"],
        "done": job["done"],
        "skipped": job["skipped"],
        "errors": job["errors"],
        "current": job["current"],
        "results": job["results"],
    })


# --- Extraction endpoints ---

@app.route("/api/extract_list")
def api_extract_list():
    variant = request.args.get("variant", "all")

    if variant == "all":
        jsons = sorted(PROLIFIC_DIR.glob("study1_*.json"))
    else:
        jsons = sorted(PROLIFIC_DIR.glob(f"study1_*_{variant}.json"))

    scenes = []
    for jp in jsons:
        with open(jp) as f:
            data = json.load(f)
        story_id = data["story_id"]
        characters = data.get("entities", data.get("characters", {}))

        for scene in data["scenes"]:
            sn = scene["scene_number"]
            full_path = OUTPUT_BASE / story_id / "hd" / f"scene_{sn}_full.png"
            extract_dir = OUTPUT_BASE / story_id / "extractions"

            existing_ext = []
            for char_name in characters:
                ext_path = extract_dir / f"scene_{sn}_{char_name}.png"
                if ext_path.exists():
                    existing_ext.append({"name": char_name, "path": str(ext_path)})

            scenes.append({
                "story_id": story_id,
                "scene_number": sn,
                "title": scene.get("title", f"Scene {sn}"),
                "full_hd": str(full_path) if full_path.exists() else None,
                "characters": list(characters.keys()),
                "extractions": existing_ext,
            })

    return jsonify({"scenes": scenes})


@app.route("/api/extract_all")
def api_extract_all():
    story_id = request.args.get("story_id", "")
    scene_num = int(request.args.get("scene", 1))

    # Load scene data
    json_path = PROLIFIC_DIR / f"{story_id}.json"
    if not json_path.exists():
        return jsonify({"error": f"Scene JSON not found: {story_id}"})

    with open(json_path) as f:
        data = json.load(f)
    characters = data.get("entities", data.get("characters", {}))

    full_path = OUTPUT_BASE / story_id / "hd" / f"scene_{scene_num}_full.png"
    if not full_path.exists():
        return jsonify({"error": f"Composed scene not found: {full_path}"})

    scene_bytes = full_path.read_bytes()

    # Find which entities are in this scene
    scene_data = None
    for s in data["scenes"]:
        if s["scene_number"] == scene_num:
            scene_data = s
            break
    entities_in_scene = scene_data.get("entities_in_scene", list(characters.keys())) if scene_data else list(characters.keys())

    result = asyncio.run(_extract_all_async(story_id, scene_num, characters, entities_in_scene, scene_bytes))
    return jsonify(result)


async def _extract_all_async(
    story_id: str,
    scene_num: int,
    characters: Dict[str, str],
    entities_in_scene: List[str],
    scene_bytes: bytes,
) -> Dict[str, Any]:
    t0 = time.time()
    extract_dir = OUTPUT_BASE / story_id / "extractions"
    extractions = []

    for char_name in entities_in_scene:
        char_desc = characters.get(char_name, char_name)
        out_path = extract_dir / f"scene_{scene_num}_{char_name}.png"

        logger.info("[extract] Extracting %s from %s scene %d...", char_name, story_id, scene_num)
        result_bytes = await extract_character(scene_bytes, char_desc)

        if result_bytes:
            save_image(result_bytes, out_path)
            extractions.append({"name": char_name, "path": str(out_path)})
        else:
            extractions.append({"name": char_name, "path": None, "error": "extraction failed"})

        await asyncio.sleep(API_DELAY)

    return {"extractions": extractions, "elapsed": time.time() - t0}


@app.route("/api/extract")
def api_extract():
    story_id = request.args.get("story_id", "")
    scene_num = int(request.args.get("scene", 1))
    char_name = request.args.get("character", "")

    json_path = PROLIFIC_DIR / f"{story_id}.json"
    if not json_path.exists():
        return jsonify({"error": f"Scene JSON not found: {story_id}"})

    with open(json_path) as f:
        data = json.load(f)
    characters = data.get("entities", data.get("characters", {}))

    if char_name not in characters:
        return jsonify({"error": f"Unknown character: {char_name}"})

    full_path = OUTPUT_BASE / story_id / "hd" / f"scene_{scene_num}_full.png"
    if not full_path.exists():
        return jsonify({"error": f"Composed scene not found"})

    scene_bytes = full_path.read_bytes()
    char_desc = characters[char_name]

    result = asyncio.run(_extract_one_async(story_id, scene_num, char_name, char_desc, scene_bytes))
    return jsonify(result)


async def _extract_one_async(
    story_id: str, scene_num: int, char_name: str, char_desc: str, scene_bytes: bytes,
) -> Dict[str, Any]:
    t0 = time.time()
    extract_dir = OUTPUT_BASE / story_id / "extractions"
    out_path = extract_dir / f"scene_{scene_num}_{char_name}.png"

    result_bytes = await extract_character(scene_bytes, char_desc)
    if result_bytes:
        save_image(result_bytes, out_path)
        return {"path": str(out_path), "elapsed": time.time() - t0}
    return {"error": f"Extraction failed for {char_name}"}


@app.route("/api/image")
def api_image():
    path = request.args.get("path", "")
    download = request.args.get("download", "0") == "1"
    if not path or not Path(path).exists():
        return "Not found", 404
    return send_file(path, mimetype="image/png", as_attachment=download)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global client

    parser = argparse.ArgumentParser(description="Scene Generator & Extractor UI")
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.error("No API key. Set GEMINI_API_KEY or use --api-key.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    logger.info("Starting UI on http://%s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
