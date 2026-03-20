#!/usr/bin/env python3
"""UI to extract characters from full scene images onto white backgrounds.

Takes scene_x_full.png images, identifies characters present,
and uses Gemini to extract each character onto a solid white background.

Usage:
    python -m scripts.study_gen.extract_ui --api-key YOUR_KEY
    python -m scripts.study_gen.extract_ui  # uses GEMINI_API_KEY env var
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
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("extract_ui")

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
# Art style (same as gen_ui)
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
# Image generation
# ---------------------------------------------------------------------------

client: Optional[Any] = None


async def extract_character(
    scene_image_bytes: bytes,
    character_desc: str,
) -> Optional[bytes]:
    """Extract a character from a scene image onto white background.

    Keeps the same aspect ratio as the source image so the character
    stays at its exact original position.
    """
    prompt = EXTRACT_PROMPT.format(character_description=character_desc)

    contents = [
        types.Part.from_bytes(data=scene_image_bytes, mime_type="image/png"),
        prompt,
    ]

    # Detect source aspect ratio
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
        aspect = "16:9"  # default

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
                        logger.info("Attempt %d/%d: got %d bytes in %.1fs",
                                    attempt, IMAGE_MAX_RETRIES, len(part.inline_data.data), elapsed)
                        return part.inline_data.data

            logger.warning("Attempt %d/%d: no image data (%.1fs)", attempt, IMAGE_MAX_RETRIES, elapsed)
        except Exception as exc:
            logger.warning("Attempt %d/%d failed (%.1fs): %s",
                           attempt, IMAGE_MAX_RETRIES, time.time() - t0, exc)

        if attempt < IMAGE_MAX_RETRIES:
            await asyncio.sleep(API_DELAY)

    return None


def save_image(image_bytes: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(image_bytes))
    img.save(str(path), format="PNG")


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

state: Dict[str, Any] = {
    "story_key": None,
    "story_data": None,
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
<title>Character Extractor</title>
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
button.extract { background: #2e7d32; }
button.extract:hover { background: #388e3c; }
button.regen { background: #e9a045; color: #1a1a2e; }
.status { margin: 10px 0; padding: 10px; background: #16213e; border-radius: 4px; min-height: 40px; }
.scene-row { background: #16213e; border-radius: 8px; padding: 15px; margin: 15px 0; }
.scene-row h3 { color: #e94560; margin-bottom: 10px; }
.scene-content { display: flex; gap: 15px; align-items: flex-start; flex-wrap: wrap; }
.scene-preview { flex: 0 0 400px; }
.scene-preview img { width: 100%; border-radius: 4px; }
.chars-panel { flex: 1; min-width: 300px; }
.char-check { display: flex; align-items: center; gap: 8px; margin: 6px 0; padding: 8px; background: #0f3460; border-radius: 4px; }
.char-check input[type="checkbox"] { width: 18px; height: 18px; }
.char-check label { flex: 1; cursor: pointer; }
.char-check .char-desc { font-size: 12px; color: #999; margin-top: 2px; }
.extractions { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin-top: 15px; }
.ext-card { background: #0f3460; border-radius: 8px; padding: 10px; text-align: center; }
.ext-card h4 { margin-bottom: 6px; color: #e94560; font-size: 13px; }
.ext-card img { max-width: 100%; border-radius: 4px; background: white; }
.ext-card .actions { margin-top: 6px; display: flex; gap: 6px; justify-content: center; }
.ext-card .actions a, .ext-card .actions button { font-size: 12px; padding: 4px 8px; }
.ext-card .actions a { color: #e94560; text-decoration: none; }
#loading { display: none; }
#loading.show { display: block; }
.spinner { display: inline-block; width: 20px; height: 20px; border: 3px solid #555; border-top-color: #e94560; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 8px; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<h1>Character Extractor</h1>
<p style="color:#aaa; margin-bottom:15px;">Extract characters from full scene images onto white backgrounds.</p>

<div class="controls">
  <label>Story:</label>
  <select id="storySelect">
    {% for key, title in stories %}
    <option value="{{ key }}" {% if key == current_story %}selected{% endif %}>{{ key }} — {{ title }}</option>
    {% endfor %}
  </select>
  <button onclick="loadStory()">Load Story</button>
</div>

<div class="status" id="status"></div>
<div id="loading"><span class="spinner"></span><span id="loadingText">Extracting...</span></div>

<div id="scenesContainer"></div>

<script>
let storyData = null;

async function loadStory() {
  const key = document.getElementById('storySelect').value;
  const res = await fetch('/api/load_story?key=' + key);
  const data = await res.json();
  if (data.error) { setStatus('Error: ' + data.error); return; }
  storyData = data;
  renderScenes();
  setStatus('Story "' + data.title + '" loaded. ' + data.scenes.length + ' scenes found.');
}

function setStatus(msg) { document.getElementById('status').textContent = msg; }
function showLoading(show, text) {
  document.getElementById('loading').className = show ? 'show' : '';
  if (text) document.getElementById('loadingText').textContent = text;
}

function renderScenes() {
  let html = '';
  for (const scene of storyData.scenes) {
    const bust = '&t=' + Date.now();
    html += '<div class="scene-row" id="scene-row-' + scene.scene_number + '">';
    html += '<h3>Scene ' + scene.scene_number + ': ' + scene.title + '</h3>';
    html += '<div class="scene-content">';

    // Preview
    if (scene.full_hd_path) {
      html += '<div class="scene-preview">';
      html += '<img src="/api/image?path=' + encodeURIComponent(scene.full_hd_path) + bust + '">';
      html += '</div>';
    } else {
      html += '<div class="scene-preview" style="display:flex;align-items:center;justify-content:center;height:200px;background:#0f3460;border-radius:4px;"><span style="color:#555;">No full scene image found</span></div>';
    }

    // Characters panel
    html += '<div class="chars-panel">';
    html += '<div style="margin-bottom:8px;"><strong>Characters in scene:</strong></div>';
    for (const char of scene.characters) {
      const checkId = 'check-' + scene.scene_number + '-' + char.name;
      html += '<div class="char-check">';
      html += '<input type="checkbox" id="' + checkId + '" ' + (char.in_scene ? 'checked' : '') + '>';
      html += '<div><label for="' + checkId + '">' + char.name + '</label>';
      html += '<div class="char-desc">' + char.description + '</div></div>';
      html += '</div>';
    }
    if (scene.full_hd_path) {
      html += '<div style="margin-top:10px;">';
      html += '<button class="extract" onclick="extractAll(' + scene.scene_number + ')">Extract Selected Characters</button>';
      html += '</div>';
    }

    // Extractions area
    html += '<div class="extractions" id="extractions-' + scene.scene_number + '">';
    // Load existing extractions
    if (scene.existing_extractions) {
      for (const ext of scene.existing_extractions) {
        html += extCard(ext.name, ext.path, scene.scene_number);
      }
    }
    html += '</div>';

    html += '</div>'; // chars-panel
    html += '</div>'; // scene-content
    html += '</div>'; // scene-row
  }
  document.getElementById('scenesContainer').innerHTML = html;
}

function extCard(name, path, sceneNum) {
  const bust = '&t=' + Date.now();
  const src = '/api/image?path=' + encodeURIComponent(path) + bust;
  return '<div class="ext-card"><h4>' + name + '</h4>' +
    '<img src="' + src + '">' +
    '<div class="actions">' +
    '<a href="' + src + '&download=1" download>Download</a>' +
    '<button class="regen" onclick="extractOne(' + sceneNum + ',&quot;' + name + '&quot;)">Redo</button>' +
    '</div></div>';
}

async function extractAll(sceneNum) {
  const scene = storyData.scenes.find(s => s.scene_number === sceneNum);
  if (!scene) return;

  const selected = [];
  for (const char of scene.characters) {
    const checkId = 'check-' + sceneNum + '-' + char.name;
    const cb = document.getElementById(checkId);
    if (cb && cb.checked) selected.push(char.name);
  }

  if (selected.length === 0) { setStatus('No characters selected.'); return; }

  showLoading(true, 'Extracting ' + selected.length + ' characters from scene ' + sceneNum + '...');
  setStatus('Extracting ' + selected.join(', ') + ' from scene ' + sceneNum + '...');

  const extDiv = document.getElementById('extractions-' + sceneNum);
  extDiv.innerHTML = '';

  for (const charName of selected) {
    showLoading(true, 'Extracting ' + charName + ' from scene ' + sceneNum + '...');
    try {
      const res = await fetch('/api/extract?scene=' + sceneNum + '&character=' + encodeURIComponent(charName));
      const data = await res.json();
      if (data.error) {
        setStatus('Error extracting ' + charName + ': ' + data.error);
      } else {
        extDiv.innerHTML += extCard(charName, data.path, sceneNum);
        setStatus('Extracted ' + charName + ' (' + data.elapsed.toFixed(1) + 's)');
      }
    } catch (e) {
      setStatus('Error: ' + e.message);
    }
  }
  showLoading(false);
  setStatus('All extractions done for scene ' + sceneNum + '.');
}

async function extractOne(sceneNum, charName) {
  showLoading(true, 'Re-extracting ' + charName + ' from scene ' + sceneNum + '...');
  try {
    const res = await fetch('/api/extract?scene=' + sceneNum + '&character=' + encodeURIComponent(charName));
    const data = await res.json();
    showLoading(false);
    if (data.error) {
      setStatus('Error: ' + data.error);
    } else {
      // Refresh just that card
      const extDiv = document.getElementById('extractions-' + sceneNum);
      // Replace existing card or append
      const cards = extDiv.querySelectorAll('.ext-card');
      let replaced = false;
      cards.forEach(card => {
        if (card.querySelector('h4').textContent === charName) {
          card.outerHTML = extCard(charName, data.path, sceneNum);
          replaced = true;
        }
      });
      if (!replaced) extDiv.innerHTML += extCard(charName, data.path, sceneNum);
      setStatus('Re-extracted ' + charName + ' (' + data.elapsed.toFixed(1) + 's)');
    }
  } catch (e) {
    showLoading(false);
    setStatus('Error: ' + e.message);
  }
}

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
    story_id = data["story_id"]

    hd_dir = OUTPUT_BASE / story_id / "hd"
    extract_dir = OUTPUT_BASE / story_id / "extractions"

    scenes_info = []
    for scene in data["scenes"]:
        sn = scene["scene_number"]
        full_path = hd_dir / f"scene_{sn}_full.png"
        entities = scene.get("entities_in_scene", [])

        # Build character list with checkbox state
        chars_info = []
        for char_name, char_desc in data["characters"].items():
            chars_info.append({
                "name": char_name,
                "description": char_desc,
                "in_scene": char_name in entities,
            })

        # Check for existing extractions
        existing = []
        for char_name in data["characters"]:
            ext_path = extract_dir / f"scene_{sn}_{char_name}.png"
            if ext_path.exists():
                existing.append({"name": char_name, "path": str(ext_path)})

        scenes_info.append({
            "scene_number": sn,
            "title": scene.get("title", f"Scene {sn}"),
            "full_hd_path": str(full_path) if full_path.exists() else None,
            "characters": chars_info,
            "existing_extractions": existing,
        })

    return jsonify({
        "title": data["title"],
        "story_id": story_id,
        "scenes": scenes_info,
    })


@app.route("/api/extract")
def api_extract():
    scene_num = int(request.args.get("scene", 1))
    char_name = request.args.get("character", "")

    data = state["story_data"]
    if not data:
        return jsonify({"error": "No story loaded"})

    story_id = data["story_id"]
    characters = data["characters"]

    if char_name not in characters:
        return jsonify({"error": f"Unknown character: {char_name}"})

    # Load the full scene image
    full_path = OUTPUT_BASE / story_id / "hd" / f"scene_{scene_num}_full.png"
    if not full_path.exists():
        return jsonify({"error": f"Full scene image not found: {full_path}"})

    scene_bytes = full_path.read_bytes()
    char_desc = characters[char_name]

    result = asyncio.run(_extract_async(story_id, scene_num, char_name, char_desc, scene_bytes))
    return jsonify(result)


async def _extract_async(
    story_id: str,
    scene_num: int,
    char_name: str,
    char_desc: str,
    scene_bytes: bytes,
) -> Dict[str, Any]:
    t0 = time.time()

    extract_dir = OUTPUT_BASE / story_id / "extractions"
    out_path = extract_dir / f"scene_{scene_num}_{char_name}.png"

    logger.info("Extracting %s from scene %d...", char_name, scene_num)

    result_bytes = await extract_character(scene_bytes, char_desc)

    if result_bytes:
        save_image(result_bytes, out_path)
        elapsed = time.time() - t0
        logger.info("Saved extraction: %s (%.1fs)", out_path, elapsed)
        return {"path": str(out_path), "elapsed": elapsed}
    else:
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

    parser = argparse.ArgumentParser(description="Character Extractor UI")
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--port", type=int, default=5556)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.error("No API key. Set GEMINI_API_KEY or use --api-key.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    logger.info("Starting Character Extractor UI on http://%s:%d", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
