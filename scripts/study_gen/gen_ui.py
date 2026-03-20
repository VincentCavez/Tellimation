#!/usr/bin/env python3
"""Simple Flask UI for step-by-step story image generation.

Generates scenes one at a time. For scene 1, also generates entity refs.
All images downloadable in HD and pixel art.

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
import time
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
OUTPUT_BASE = PROJECT_ROOT / "data" / "study_gen"
API_DELAY = 2.0

STORY_FILES = {
    "A": "story_a_balloon_seller.json",
    "B": "story_b_market_mixup.json",
    "C": "story_c_night_garden.json",
    "D": "story_d_runaway_train.json",
}

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
Create an illustration of the following character on a SOLID WHITE background. \
The white must be perfectly uniform — no gradients, no shading, no variation.

## Character
{character_description}

## Art Style — MUST FOLLOW EXACTLY
{art_style}

## Additional Rules
- **Side view** (like a 2D storybook): flat side profile.
- **The character should fill most of the image** — center it, leave only a small \
  margin of white around it.
- **Show the full body** of the character from head to feet.
- **Detailed**: clearly distinct body parts (head, body, limbs).
- **NO other elements**: no ground, no shadow, no text, no decorations. \
  ONLY the character on solid white.
- **NO text, labels, or writing of any kind.**
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


def save_image(image_bytes: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(image_bytes))
    img.save(str(path), format="PNG")


def downscale_to_pixel_art(image_bytes: bytes, target_w: int = ART_W, target_h: int = ART_H) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    scale = min(target_w / w, target_h / h)
    final_w = max(1, round(w * scale))
    final_h = max(1, round(h * scale))
    pixel_img = img.resize((final_w, final_h), Image.NEAREST)
    buf = io.BytesIO()
    pixel_img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

# In-memory state
state: Dict[str, Any] = {
    "story_key": None,
    "story_data": None,
    "current_scene": 1,
    "ref_images": {},       # char_name -> bytes (HD)
    "generated": {},        # scene_num -> { "bg_hd", "full_hd" }
}


def load_story(key: str) -> Dict[str, Any]:
    path = STORIES_DIR / STORY_FILES[key]
    with open(path) as f:
        return json.load(f)


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Story Image Generator</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }
h1 { margin-bottom: 10px; color: #e94560; }
h2 { color: #0f3460; background: #e94560; display: inline-block; padding: 4px 12px; border-radius: 4px; margin: 10px 0; }
h3 { color: #aaa; margin: 8px 0; }
.controls { margin: 15px 0; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
select, button { padding: 8px 16px; border: none; border-radius: 4px; font-size: 14px; cursor: pointer; }
select { background: #16213e; color: #eee; }
button { background: #e94560; color: white; font-weight: bold; }
button:hover { background: #c73652; }
button:disabled { background: #555; cursor: not-allowed; }
button.validate { background: #0f3460; }
button.validate:hover { background: #1a4a8a; }
button.regen { background: #e9a045; color: #1a1a2e; }
.status { margin: 10px 0; padding: 10px; background: #16213e; border-radius: 4px; min-height: 40px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 15px; margin: 15px 0; }
.card { background: #16213e; border-radius: 8px; padding: 12px; }
.card h4 { margin-bottom: 8px; color: #e94560; }
.card img { width: 100%; border-radius: 4px; }
.card .downloads { margin-top: 8px; display: flex; gap: 8px; }
.card .downloads a { color: #e94560; text-decoration: none; font-size: 13px; }
.card .downloads a:hover { text-decoration: underline; }
.refs-section { border: 1px solid #333; border-radius: 8px; padding: 15px; margin: 15px 0; }
.refs-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.scene-nav { display: flex; gap: 8px; margin: 10px 0; }
.scene-nav button { padding: 6px 12px; font-size: 12px; }
.scene-nav button.active { background: #0f3460; }
.scene-nav button.done { background: #2e7d32; }
#loading { display: none; }
#loading.show { display: block; }
.spinner { display: inline-block; width: 20px; height: 20px; border: 3px solid #555; border-top-color: #e94560; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 8px; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<h1>Story Image Generator</h1>

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
  <button id="btnValidate" class="validate" onclick="validateScene()" style="display:none;">Validate & Next →</button>
</div>

<div class="status" id="status"></div>
<div id="loading"><span class="spinner"></span><span id="loadingText">Generating...</span></div>

<div id="refsSection" style="display:none;">
  <div class="refs-section">
    <h3>Entity References</h3>
    <div class="refs-grid" id="refsGrid"></div>
  </div>
</div>

<div class="grid" id="imagesGrid"></div>

<script>
let currentScene = 1;
let totalScenes = 0;
let validatedScenes = new Set();

async function loadStory() {
  const key = document.getElementById('storySelect').value;
  const res = await fetch('/api/load_story?key=' + key);
  const data = await res.json();
  if (data.error) { setStatus('Error: ' + data.error); return; }
  totalScenes = data.total_scenes;
  currentScene = 1;
  validatedScenes.clear();
  document.getElementById('storyInfo').innerHTML = '<h2>' + data.title + '</h2> <h3>' + data.total_scenes + ' scenes, ' + data.num_characters + ' entities</h3>';
  document.getElementById('genControls').style.display = 'flex';
  updateSceneNav();
  updateUI();
  let refsMsg = '';
  if (data.loaded_refs && data.loaded_refs.length > 0) {
    refsMsg = ' Loaded ' + data.loaded_refs.length + ' existing refs: ' + data.loaded_refs.join(', ') + '.';
  }
  setStatus('Story loaded.' + refsMsg + ' Ready to generate scene 1.');
}

function updateSceneNav() {
  let html = '';
  for (let i = 1; i <= totalScenes; i++) {
    const cls = i === currentScene ? 'active' : (validatedScenes.has(i) ? 'done' : '');
    html += '<button class="' + cls + '" onclick="goToScene(' + i + ')">' + i + '</button>';
  }
  document.getElementById('sceneNav').innerHTML = html;
}

function goToScene(n) {
  currentScene = n;
  updateSceneNav();
  updateUI();
}

function updateUI() {
  document.getElementById('sceneNum').textContent = currentScene;
  document.getElementById('imagesGrid').innerHTML = '';
  document.getElementById('refsSection').style.display = 'none';
  document.getElementById('refsGrid').innerHTML = '';
  document.getElementById('btnRegenBg').style.display = 'none';
  document.getElementById('btnRegenFull').style.display = 'none';
  document.getElementById('btnRegenAll').style.display = 'none';
  document.getElementById('btnValidate').style.display = 'none';
  // Load existing images if any
  loadExisting();
}

async function loadExisting() {
  const res = await fetch('/api/scene_status?scene=' + currentScene);
  const data = await res.json();
  if (data.has_images) {
    showImages(data);
  }
}

function setStatus(msg) {
  document.getElementById('status').textContent = msg;
}

function showLoading(show, text) {
  const el = document.getElementById('loading');
  el.className = show ? 'show' : '';
  if (text) document.getElementById('loadingText').textContent = text;
}

async function generateScene() {
  document.getElementById('btnGenScene').disabled = true;
  showLoading(true, 'Generating scene ' + currentScene + '...');
  setStatus('Generating scene ' + currentScene + '...');

  try {
    const res = await fetch('/api/generate_scene?scene=' + currentScene);
    const data = await res.json();
    showLoading(false);
    if (data.error) {
      setStatus('Error: ' + data.error);
    } else {
      setStatus('Scene ' + currentScene + ' generated in ' + data.elapsed.toFixed(1) + 's');
      showImages(data);
    }
  } catch (e) {
    showLoading(false);
    setStatus('Error: ' + e.message);
  }
  document.getElementById('btnGenScene').disabled = false;
}

async function regenerateBg() {
  await doGenerate('bg_only');
}
async function regenerateFull() {
  await doGenerate('full_only');
}
async function regenerateAll() {
  await doGenerate('all');
}
async function doGenerate(mode) {
  document.getElementById('btnGenScene').disabled = true;
  showLoading(true, 'Generating scene ' + currentScene + ' (' + mode + ')...');
  setStatus('Generating scene ' + currentScene + ' (' + mode + ')...');
  try {
    const res = await fetch('/api/generate_scene?scene=' + currentScene + '&mode=' + mode);
    const data = await res.json();
    showLoading(false);
    if (data.error) {
      setStatus('Error: ' + data.error);
    } else {
      setStatus('Scene ' + currentScene + ' generated in ' + data.elapsed.toFixed(1) + 's');
      showImages(data);
    }
  } catch (e) {
    showLoading(false);
    setStatus('Error: ' + e.message);
  }
  document.getElementById('btnGenScene').disabled = false;
}

function showImages(data) {
  let html = '';
  const bust = '&t=' + Date.now();

  // Background
  if (data.bg_hd) {
    html += imgCard('Background', '/api/image?path=' + encodeURIComponent(data.bg_hd) + bust, data.bg_hd);
  }
  // Full scene
  if (data.full_hd) {
    html += imgCard('Full Scene', '/api/image?path=' + encodeURIComponent(data.full_hd) + bust, data.full_hd);
  }

  document.getElementById('imagesGrid').innerHTML = html;

  // Refs
  if (data.refs && data.refs.length > 0) {
    let refsHtml = '';
    for (const ref of data.refs) {
      refsHtml += imgCard(ref.name, '/api/image?path=' + encodeURIComponent(ref.hd) + bust, ref.hd);
    }
    document.getElementById('refsGrid').innerHTML = refsHtml;
    document.getElementById('refsSection').style.display = 'block';
  }

  document.getElementById('btnRegenBg').style.display = 'inline-block';
  document.getElementById('btnRegenFull').style.display = 'inline-block';
  document.getElementById('btnRegenAll').style.display = 'inline-block';
  document.getElementById('btnValidate').style.display = currentScene <= totalScenes ? 'inline-block' : 'none';
}

function imgCard(title, src, path) {
  return '<div class="card"><h4>' + title + '</h4>' +
    '<img src="' + src + '">' +
    '<div class="downloads"><a href="' + src + '&download=1" download>Download</a></div></div>';
}

function validateScene() {
  validatedScenes.add(currentScene);
  if (currentScene < totalScenes) {
    currentScene++;
    updateSceneNav();
    updateUI();
    setStatus('Scene ' + (currentScene - 1) + ' validated. Ready for scene ' + currentScene + '.');
  } else {
    updateSceneNav();
    setStatus('All scenes validated!');
  }
}

// Auto-load on page load
window.addEventListener('load', loadStory);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    stories = []
    for key, fname in sorted(STORY_FILES.items()):
        path = STORIES_DIR / fname
        if path.exists():
            with open(path) as f:
                d = json.load(f)
            stories.append((key, d["title"]))
        else:
            stories.append((key, f"({fname} not found)"))
    return render_template_string(HTML_TEMPLATE, stories=stories, current_story=state.get("story_key", "A"))


@app.route("/api/load_story")
def api_load_story():
    key = request.args.get("key", "A")
    if key not in STORY_FILES:
        return jsonify({"error": f"Unknown story: {key}"})

    data = load_story(key)
    state["story_key"] = key
    state["story_data"] = data
    state["current_scene"] = 1
    state["ref_images"] = {}
    state["generated"] = {}

    # Auto-load existing refs from disk
    refs_dir = OUTPUT_BASE / data["story_id"] / "refs"
    loaded_refs = []
    for char_name in data["characters"]:
        ref_path = refs_dir / f"{char_name}.png"
        if ref_path.exists():
            state["ref_images"][char_name] = ref_path.read_bytes()
            loaded_refs.append(char_name)

    return jsonify({
        "title": data["title"],
        "total_scenes": len(data["scenes"]),
        "num_characters": len(data["characters"]),
        "characters": list(data["characters"].keys()),
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
    mode = request.args.get("mode", "all")  # "all", "bg_only", "full_only"

    # Run generation in asyncio
    result = asyncio.run(_generate_scene_async(story_id, scene, characters, scene_num, mode))
    return jsonify(result)


async def _generate_scene_async(
    story_id: str,
    scene: Dict[str, Any],
    characters: Dict[str, str],
    scene_num: int,
    mode: str = "all",
) -> Dict[str, Any]:
    t0 = time.time()
    result: Dict[str, Any] = {"scene": scene_num, "refs": []}

    hd_dir = OUTPUT_BASE / story_id / "hd"
    refs_dir = OUTPUT_BASE / story_id / "refs"

    gen_refs = mode == "all" and scene_num == 1 and not state["ref_images"]
    gen_bg = mode in ("all", "bg_only")
    gen_full = mode in ("all", "full_only")

    # Scene 1: generate entity refs first
    if gen_refs:
        logger.info("Generating entity references...")
        for char_name, char_desc in characters.items():
            prompt = CHARACTER_REF_PROMPT.format(character_description=char_desc, art_style=ART_STYLE)
            ref_bytes = await generate_image(prompt, aspect_ratio="1:1", label=f"ref_{char_name}")

            if ref_bytes:
                state["ref_images"][char_name] = ref_bytes
                hd_path = refs_dir / f"{char_name}.png"
                save_image(ref_bytes, hd_path)

                result["refs"].append({
                    "name": char_name,
                    "hd": str(hd_path),
                })
            else:
                logger.error("Ref generation FAILED for %s", char_name)

            await asyncio.sleep(API_DELAY)

    # Also return existing refs if we have them
    if not result["refs"] and state["ref_images"]:
        for char_name in state["ref_images"]:
            hd_path = refs_dir / f"{char_name}.png"
            if hd_path.exists():
                result["refs"].append({"name": char_name, "hd": str(hd_path)})

    # Generate background
    bg_bytes: Optional[bytes] = None
    bg_hd_path = hd_dir / f"scene_{scene_num}_bg.png"

    if gen_bg:
        bg_prompt = BACKGROUND_PROMPT_PREFIX.format(scene_description=scene["background_prompt"], art_style=ART_STYLE)
        bg_bytes = await generate_image(bg_prompt, aspect_ratio="16:9", label=f"scene_{scene_num}_bg")

        if bg_bytes:
            save_image(bg_bytes, bg_hd_path)
            result["bg_hd"] = str(bg_hd_path)

        await asyncio.sleep(API_DELAY)
    elif bg_hd_path.exists():
        # Load existing background for full scene ref
        bg_bytes = bg_hd_path.read_bytes()
        result["bg_hd"] = str(bg_hd_path)

    if not gen_full:
        # Return existing full scene path if available
        full_hd_path = hd_dir / f"scene_{scene_num}_full.png"
        if full_hd_path.exists():
            result["full_hd"] = str(full_hd_path)
        result["elapsed"] = time.time() - t0
        result["has_images"] = True
        state["generated"][scene_num] = result
        return result

    # Generate full scene with refs
    ref_parts: List[bytes] = []
    present_chars: List[str] = []

    if bg_bytes:
        ref_parts.append(bg_bytes)

    present = scene.get("entities_in_scene", [])
    for c in present:
        if c in state["ref_images"]:
            ref_parts.append(state["ref_images"][c])
            present_chars.append(c)

    full_prompt = FULL_SCENE_PROMPT_PREFIX.format(scene_description=scene["full_scene_prompt"], art_style=ART_STYLE)
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
        full_prompt, aspect_ratio="16:9", label=f"scene_{scene_num}_full",
        reference_images=ref_parts if ref_parts else None,
    )

    if full_bytes:
        full_hd_path = hd_dir / f"scene_{scene_num}_full.png"
        save_image(full_bytes, full_hd_path)
        result["full_hd"] = str(full_hd_path)

    result["elapsed"] = time.time() - t0
    result["has_images"] = True
    state["generated"][scene_num] = result
    return result


@app.route("/api/scene_status")
def api_scene_status():
    scene_num = int(request.args.get("scene", 1))
    if scene_num in state.get("generated", {}):
        return jsonify(state["generated"][scene_num])
    return jsonify({"has_images": False})


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

    parser = argparse.ArgumentParser(description="Story Image Generator UI")
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
