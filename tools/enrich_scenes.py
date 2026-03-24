"""Scene enrichment tool — analyzes scene images with Gemini and enriches story JSONs.

Launches a local web UI that:
  1. Lists all study stories and their scenes
  2. Shows scene_x_full.png for each scene
  3. Calls Gemini 3 Flash (4096 thinking budget) to generate a rich scene_description
     and enriched misl_targets from the image
  4. Lets you review, edit, and save back to the JSON files

Usage:
    python tools/enrich_scenes.py
    → opens http://localhost:8501
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Scene Enrichment Tool")

DATA_DIR = PROJECT_ROOT / "data"
STUDY_SCENES_DIR = DATA_DIR / "study_scenes"
TRAINING_DIR = DATA_DIR / "training"
STUDY_GEN_DIR = DATA_DIR / "study_gen"

API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
MODEL_ID = "gemini-3-flash-preview"

# Serve images
app.mount("/images", StaticFiles(directory=str(STUDY_GEN_DIR)), name="images")


def _load_all_stories() -> list[dict]:
    """Load all story JSON files."""
    stories = []
    for path in sorted(STUDY_SCENES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = str(path)
        stories.append(data)
    for path in sorted(TRAINING_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = str(path)
        stories.append(data)
    return stories


def _image_path(story_id: str, scene_number: int) -> Path:
    return STUDY_GEN_DIR / story_id / "hd" / f"scene_{scene_number}_full.png"


ENRICHMENT_PROMPT = """\
You are analyzing a pixel-art scene image from a children's storytelling application.

# Current scene data

```json
{scene_json}
```

# Your task

Look at the image carefully and produce TWO things:

## 1. scene_description

Write an extremely detailed description of EVERYTHING visible in the scene. Include:
- Every character: their appearance, clothing, accessories, posture, body language, facial expression, exact position in the scene
- Every object: its color, size, shape, position, state (open/closed, moving/still)
- The background: environment type, weather, lighting, time of day, colors, atmosphere
- Spatial relations: who/what is next to, behind, in front of, above, below what
- Actions happening: what each character is doing, the dynamics of the scene
- Small details: textures, patterns, shadows, reflections, decorative elements
- Emotions conveyed: through faces, posture, color palette

Be exhaustive. The description should be so detailed that someone could reconstruct the image from it. Use simple English (the system is for children aged 7-11).

## 2. enriched misl_targets

Based on your scene_description (NOT the old data), produce enriched misl_targets. For each element, list ALL possible targets a child could describe from what is ACTUALLY VISIBLE in the image:

**Macro:**
- CH: All characters visible with distinguishing features
- S: All setting elements (place, time indicators, weather)
- IE: Any initiating events visible (something that starts action)
- IR: Any emotions/feelings visible on characters
- P: Any plans or intentions suggested by the scene
- A: ALL actions visible (every verb a child could use)
- CO: Any consequences/outcomes visible

**Micro:**
- ENP: ALL noun phrases that could be elaborated (adjective + noun combinations visible)
- ADV: ALL adverbs a child could naturally use to describe what they see
- SC: Subordinating conjunctions the scene naturally invites (because, when, while, after...)
- CC: Coordinating conjunctions the scene invites (and, but, so)
- M: Mental verbs the scene invites (thinks, wants, decides, knows, feels...)
- L: Linguistic verbs the scene invites (says, tells, asks, shouts, whispers...)

For misl_targets: use null for elements with NO viable target in the scene. Use [] only if the category exists but has no specific targets. Include targets that are VISIBLE, not imagined.

# Output

Return ONLY valid JSON:

```
{{
  "scene_description": "...",
  "misl_targets": {{
    "macro": {{
      "CH": [...] or null,
      "S": [...] or null,
      "IE": [...] or null,
      "IR": [...] or null,
      "P": [...] or null,
      "A": [...] or null,
      "CO": [...] or null
    }},
    "micro": {{
      "ENP": [...] or null,
      "ADV": [...] or null,
      "SC": [...] or null,
      "CC": [...] or null,
      "M": [...] or null,
      "L": [...] or null
    }}
  }}
}}
```
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


@app.get("/api/stories")
async def api_stories():
    stories = _load_all_stories()
    result = []
    for story in stories:
        scenes = []
        for scene in story.get("scenes", []):
            sn = scene["scene_number"]
            img = _image_path(story["story_id"], sn)
            scenes.append({
                "scene_number": sn,
                "title": scene.get("title", ""),
                "has_image": img.exists(),
                "image_url": f"/images/{story['story_id']}/hd/scene_{sn}_full.png",
                "has_scene_description": bool(scene.get("scene_description")),
                "scene_description": scene.get("scene_description", ""),
                "current_data": scene,
            })
        result.append({
            "story_id": story["story_id"],
            "title": story["title"],
            "path": story["_path"],
            "scenes": scenes,
        })
    return JSONResponse(result)


@app.post("/api/enrich")
async def api_enrich(request: Request):
    body = await request.json()
    story_path = body["story_path"]
    scene_number = body["scene_number"]

    # Load story
    story = json.loads(Path(story_path).read_text(encoding="utf-8"))
    scene = next(s for s in story["scenes"] if s["scene_number"] == scene_number)
    story_id = story["story_id"]

    # Load image
    img_path = _image_path(story_id, scene_number)
    if not img_path.exists():
        return JSONResponse({"error": f"Image not found: {img_path}"}, status_code=404)

    img_bytes = img_path.read_bytes()
    img_b64 = base64.b64encode(img_bytes).decode()

    # Build prompt
    scene_json = json.dumps(scene, indent=2, ensure_ascii=False)
    user_prompt = ENRICHMENT_PROMPT.format(scene_json=scene_json)

    # Call Gemini
    client = genai.Client(api_key=API_KEY)
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                    types.Part.from_text(text=user_prompt),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=4096),
            temperature=1.0,
            response_mime_type="application/json",
        ),
    )

    # Extract text
    text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            text += part.text

    # Parse JSON
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown fences
        import re
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            result = json.loads(match.group(1))
        else:
            return JSONResponse({"error": "Failed to parse Gemini response", "raw": text}, status_code=500)

    return JSONResponse({
        "scene_description": result.get("scene_description", ""),
        "misl_targets": result.get("misl_targets", {}),
    })


@app.post("/api/save")
async def api_save(request: Request):
    body = await request.json()
    story_path = body["story_path"]
    scene_number = body["scene_number"]
    scene_description = body["scene_description"]
    misl_targets = body["misl_targets"]

    path = Path(story_path)
    story = json.loads(path.read_text(encoding="utf-8"))

    for scene in story["scenes"]:
        if scene["scene_number"] == scene_number:
            scene["scene_description"] = scene_description
            scene["misl_targets"] = misl_targets
            break

    path.write_text(
        json.dumps(story, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return JSONResponse({"ok": True})


HTML_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scene Enrichment Tool</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f0f0f; color: #e0e0e0; }
  .header { background: #1a1a2e; padding: 20px 32px; border-bottom: 1px solid #333; }
  .header h1 { font-size: 22px; color: #fff; }
  .header p { font-size: 13px; color: #888; margin-top: 4px; }
  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  .story-card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px; margin-bottom: 24px; overflow: hidden; }
  .story-header { padding: 16px 20px; background: #1a1a2e; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
  .story-header h2 { font-size: 18px; color: #fff; }
  .story-header .badge { font-size: 12px; background: #333; color: #aaa; padding: 3px 10px; border-radius: 12px; }
  .story-header .badge.done { background: #1a3a1a; color: #4caf50; }
  .scenes { display: none; padding: 16px; }
  .scenes.open { display: block; }
  .scene-row { display: flex; gap: 20px; padding: 16px; border-bottom: 1px solid #222; align-items: flex-start; }
  .scene-row:last-child { border-bottom: none; }
  .scene-img { width: 280px; min-width: 280px; border-radius: 8px; cursor: pointer; transition: transform 0.2s; }
  .scene-img:hover { transform: scale(1.02); }
  .scene-info { flex: 1; min-width: 0; }
  .scene-info h3 { font-size: 16px; margin-bottom: 8px; color: #fff; }
  .scene-info .status { font-size: 12px; margin-bottom: 12px; }
  .status .enriched { color: #4caf50; }
  .status .pending { color: #ff9800; }
  .btn { padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; transition: all 0.15s; }
  .btn-primary { background: #4a6cf7; color: #fff; }
  .btn-primary:hover { background: #5b7bf8; }
  .btn-primary:disabled { background: #333; color: #666; cursor: not-allowed; }
  .btn-save { background: #2e7d32; color: #fff; }
  .btn-save:hover { background: #388e3c; }
  .btn-row { display: flex; gap: 8px; margin-bottom: 12px; }
  .result-area { margin-top: 12px; }
  .field-label { font-size: 12px; font-weight: 600; color: #888; text-transform: uppercase; margin-bottom: 4px; }
  textarea { width: 100%; background: #111; color: #ddd; border: 1px solid #333; border-radius: 6px; padding: 10px; font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; line-height: 1.5; resize: vertical; }
  textarea.desc { min-height: 120px; }
  textarea.json { min-height: 300px; }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #555; border-top-color: #4a6cf7; border-radius: 50%; animation: spin 0.6s linear infinite; margin-right: 6px; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .toast { position: fixed; bottom: 24px; right: 24px; background: #2e7d32; color: #fff; padding: 12px 20px; border-radius: 8px; font-size: 14px; opacity: 0; transition: opacity 0.3s; z-index: 999; }
  .toast.show { opacity: 1; }
  .enrich-all-bar { padding: 16px 20px; background: #111; border-top: 1px solid #222; display: flex; align-items: center; gap: 12px; }
  .progress-text { font-size: 13px; color: #888; }
  /* Lightbox */
  .lightbox { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.9); z-index: 1000; justify-content: center; align-items: center; cursor: zoom-out; }
  .lightbox.open { display: flex; }
  .lightbox img { max-width: 95vw; max-height: 95vh; border-radius: 8px; }
</style>
</head>
<body>

<div class="header">
  <h1>Scene Enrichment Tool</h1>
  <p>Analyze scene images with Gemini to generate rich descriptions and MISL targets</p>
</div>

<div class="container" id="app">
  <p style="color:#666; padding: 40px; text-align:center;">Loading stories...</p>
</div>

<div class="lightbox" id="lightbox" onclick="this.classList.remove('open')">
  <img id="lightbox-img" src="" alt="">
</div>

<div class="toast" id="toast"></div>

<script>
let stories = [];

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2500);
}

function openLightbox(src) {
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('open');
}

function toggleStory(idx) {
  const el = document.getElementById('scenes-' + idx);
  el.classList.toggle('open');
}

async function enrichScene(storyIdx, sceneIdx) {
  const story = stories[storyIdx];
  const scene = story.scenes[sceneIdx];
  const btn = document.getElementById(`btn-enrich-${storyIdx}-${sceneIdx}`);
  const btnAll = document.getElementById(`btn-enrich-all-${storyIdx}`);

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Analyzing...';
  if (btnAll) btnAll.disabled = true;

  try {
    const resp = await fetch('/api/enrich', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        story_path: story.path,
        scene_number: scene.scene_number,
      }),
    });
    const data = await resp.json();
    if (data.error) {
      alert('Error: ' + data.error);
      return;
    }

    // Fill textareas
    document.getElementById(`desc-${storyIdx}-${sceneIdx}`).value = data.scene_description;
    document.getElementById(`misl-${storyIdx}-${sceneIdx}`).value = JSON.stringify(data.misl_targets, null, 2);
    document.getElementById(`result-${storyIdx}-${sceneIdx}`).style.display = 'block';

    showToast(`Scene ${scene.scene_number} analyzed`);
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = 'Enrich with Gemini';
    if (btnAll) btnAll.disabled = false;
  }
}

async function enrichAllScenes(storyIdx) {
  const story = stories[storyIdx];
  const btn = document.getElementById(`btn-enrich-all-${storyIdx}`);
  const progress = document.getElementById(`progress-${storyIdx}`);
  btn.disabled = true;

  for (let i = 0; i < story.scenes.length; i++) {
    progress.textContent = `Processing scene ${i + 1} / ${story.scenes.length}...`;
    await enrichScene(storyIdx, i);
  }

  progress.textContent = 'All scenes analyzed!';
  btn.disabled = false;
}

async function saveScene(storyIdx, sceneIdx) {
  const story = stories[storyIdx];
  const scene = story.scenes[sceneIdx];

  const desc = document.getElementById(`desc-${storyIdx}-${sceneIdx}`).value;
  let misl;
  try {
    misl = JSON.parse(document.getElementById(`misl-${storyIdx}-${sceneIdx}`).value);
  } catch (e) {
    alert('Invalid JSON in MISL targets: ' + e.message);
    return;
  }

  const resp = await fetch('/api/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      story_path: story.path,
      scene_number: scene.scene_number,
      scene_description: desc,
      misl_targets: misl,
    }),
  });
  const data = await resp.json();
  if (data.ok) {
    showToast(`Scene ${scene.scene_number} saved!`);
    // Update local state
    scene.has_scene_description = true;
    scene.scene_description = desc;
    const statusEl = document.getElementById(`status-${storyIdx}-${sceneIdx}`);
    if (statusEl) statusEl.innerHTML = '<span class="enriched">enriched</span>';
  }
}

async function saveAllScenes(storyIdx) {
  const story = stories[storyIdx];
  for (let i = 0; i < story.scenes.length; i++) {
    const resultEl = document.getElementById(`result-${storyIdx}-${i}`);
    if (resultEl && resultEl.style.display !== 'none') {
      await saveScene(storyIdx, i);
    }
  }
  showToast('All scenes saved!');
}

function render() {
  const container = document.getElementById('app');
  let html = '';

  stories.forEach((story, si) => {
    const enrichedCount = story.scenes.filter(s => s.has_scene_description).length;
    const total = story.scenes.length;
    const allDone = enrichedCount === total;

    html += `<div class="story-card">`;
    html += `<div class="story-header" onclick="toggleStory(${si})">`;
    html += `<h2>${story.title} <span style="color:#666; font-size:14px">(${story.story_id})</span></h2>`;
    html += `<span class="badge ${allDone ? 'done' : ''}">${enrichedCount}/${total} enriched</span>`;
    html += `</div>`;

    html += `<div class="scenes" id="scenes-${si}">`;

    story.scenes.forEach((scene, sci) => {
      html += `<div class="scene-row">`;

      // Image
      if (scene.has_image) {
        html += `<img class="scene-img" src="${scene.image_url}" alt="Scene ${scene.scene_number}" onclick="openLightbox('${scene.image_url}')" />`;
      } else {
        html += `<div class="scene-img" style="background:#222;display:flex;align-items:center;justify-content:center;height:180px;border-radius:8px;color:#666">No image</div>`;
      }

      // Info
      html += `<div class="scene-info">`;
      html += `<h3>Scene ${scene.scene_number}: ${scene.title}</h3>`;
      html += `<div class="status" id="status-${si}-${sci}">`;
      html += scene.has_scene_description
        ? '<span class="enriched">enriched</span>'
        : '<span class="pending">not enriched</span>';
      html += `</div>`;

      // Buttons
      html += `<div class="btn-row">`;
      html += `<button class="btn btn-primary" id="btn-enrich-${si}-${sci}" onclick="enrichScene(${si},${sci})">Enrich with Gemini</button>`;
      html += `</div>`;

      // Result area (editable)
      const showResult = scene.has_scene_description;
      html += `<div class="result-area" id="result-${si}-${sci}" style="display:${showResult ? 'block' : 'none'}">`;
      html += `<div class="field-label">scene_description</div>`;
      html += `<textarea class="desc" id="desc-${si}-${sci}">${scene.scene_description || ''}</textarea>`;
      html += `<div class="field-label" style="margin-top:12px">misl_targets</div>`;
      html += `<textarea class="json" id="misl-${si}-${sci}">${scene.current_data.misl_targets ? JSON.stringify(scene.current_data.misl_targets, null, 2) : '{}'}</textarea>`;
      html += `<div class="btn-row" style="margin-top:8px">`;
      html += `<button class="btn btn-save" onclick="saveScene(${si},${sci})">Save scene</button>`;
      html += `</div>`;
      html += `</div>`;

      html += `</div>`; // scene-info
      html += `</div>`; // scene-row
    });

    // Enrich all bar
    html += `<div class="enrich-all-bar">`;
    html += `<button class="btn btn-primary" id="btn-enrich-all-${si}" onclick="enrichAllScenes(${si})">Enrich all scenes</button>`;
    html += `<button class="btn btn-save" onclick="saveAllScenes(${si})">Save all</button>`;
    html += `<span class="progress-text" id="progress-${si}"></span>`;
    html += `</div>`;

    html += `</div>`; // scenes
    html += `</div>`; // story-card
  });

  container.innerHTML = html;
}

// Init
fetch('/api/stories')
  .then(r => r.json())
  .then(data => { stories = data; render(); });
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print("\n  Scene Enrichment Tool")
    print("  http://localhost:8501\n")
    uvicorn.run(app, host="0.0.0.0", port=8501, log_level="info")
