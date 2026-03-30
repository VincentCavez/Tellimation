#!/usr/bin/env python3
"""Video preview & download UI for Prolific study animations.

Composes background + entity assets into 4-second animated videos:
  500ms static → 3000ms animation → 500ms static

Usage:
    python -m scripts.study_gen.video_ui
    python -m scripts.study_gen.video_ui --port 5557
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import imageio_ffmpeg
import numpy as np
from flask import Flask, jsonify, request, send_file
from PIL import Image

FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROLIFIC_DIR = PROJECT_ROOT / "data" / "prolific_scenes"
PROLIFIC_GEN = PROJECT_ROOT / "data" / "prolific_gen"
HTML_TEMPLATE_PATH = Path(__file__).resolve().parent / "video_ui.html"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("video_ui")

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_bbox(img_path: Path) -> dict[str, int] | None:
    """Return bounding box {x, y, w, h} of non-transparent pixels in RGBA image."""
    try:
        img = Image.open(img_path).convert("RGBA")
        arr = np.array(img)
        alpha = arr[:, :, 3]
        rows = np.any(alpha > 10, axis=1)
        cols = np.any(alpha > 10, axis=0)
        if not rows.any() or not cols.any():
            return None
        y_min, y_max = np.where(rows)[0][[0, -1]]
        x_min, x_max = np.where(cols)[0][[0, -1]]
        return {"x": int(x_min), "y": int(y_min), "w": int(x_max - x_min + 1), "h": int(y_max - y_min + 1)}
    except Exception:
        return None


def discover_scenes() -> list[dict[str, Any]]:
    """Scan prolific_scenes + prolific_gen and return scene metadata."""
    scenes = []
    for json_path in sorted(PROLIFIC_DIR.glob("study1_*.json")):
        with open(json_path) as f:
            data = json.load(f)
        sid = data["story_id"]
        gen_dir = PROLIFIC_GEN / sid

        # Check assets exist
        bg_path = gen_dir / "hd" / "scene_1_bg.png"
        if not bg_path.exists():
            continue

        # Find entity assets
        assets_dir = gen_dir / "assets"
        entities_info = []
        seen = set()
        in_scene_list = data["scenes"][0].get("entities_in_scene", [])
        for ename, edesc in data["entities"].items():
            seen.add(ename)
            asset = None
            for pattern in [f"withoutbg-scene_1_{ename}.png", f"withoutbg-{ename}.png"]:
                p = assets_dir / pattern
                if p.exists():
                    asset = p
                    break
            entities_info.append({
                "name": ename,
                "description": edesc,
                "has_asset": asset is not None,
                "asset_filename": asset.name if asset else None,
                "in_scene": ename in in_scene_list,
            })
        # Also pick up entities referenced in entities_in_scene but missing from entities dict
        for ename in in_scene_list:
            if ename in seen:
                continue
            asset = None
            if assets_dir.exists():
                for pattern in [f"withoutbg-scene_1_{ename}.png", f"withoutbg-{ename}.png"]:
                    p = assets_dir / pattern
                    if p.exists():
                        asset = p
                        break
            if not asset:
                continue
            seen.add(ename)
            entities_info.append({
                "name": ename,
                "description": "",
                "has_asset": True,
                "asset_filename": asset.name,
                "in_scene": True,
            })

        # Extract animation type from story_id: study1_A1_A → A1
        parts = sid.split("_")
        anim_type = parts[1] if len(parts) >= 3 else "unknown"

        scenes.append({
            "story_id": sid,
            "title": data.get("title", sid),
            "anim_type": anim_type,
            "variant": parts[2] if len(parts) >= 3 else "A",
            "entities": entities_info,
            "has_bg": True,
        })

    return scenes


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_file(str(HTML_TEMPLATE_PATH), mimetype="text/html")


@app.route("/api/scenes")
def api_scenes():
    return jsonify(discover_scenes())


@app.route("/api/scene_data/<story_id>")
def api_scene_data(story_id: str):
    """Return full scene data including bounding boxes."""
    json_path = PROLIFIC_DIR / f"{story_id}.json"
    if not json_path.exists():
        return jsonify({"error": "not found"}), 404

    with open(json_path) as f:
        data = json.load(f)

    gen_dir = PROLIFIC_GEN / story_id
    assets_dir = gen_dir / "assets"

    entities = []
    seen_names: set[str] = set()
    in_scene_list = data["scenes"][0].get("entities_in_scene", [])

    # 1. Entities declared in the JSON
    for ename, edesc in data["entities"].items():
        seen_names.add(ename)
        asset = None
        for pattern in [f"withoutbg-scene_1_{ename}.png", f"withoutbg-{ename}.png"]:
            p = assets_dir / pattern
            if p.exists():
                asset = p
                break

        bbox = get_bbox(asset) if asset else None
        entities.append({
            "name": ename,
            "description": edesc,
            "asset_url": f"/image/{story_id}/assets/{asset.name}" if asset else None,
            "bbox": bbox,
            "in_scene": ename in in_scene_list,
        })

    # 2. Entities referenced in entities_in_scene but missing from entities dict
    #    (JSON mismatch — e.g. renamed between generation passes)
    if assets_dir.exists():
        for ename in in_scene_list:
            if ename in seen_names:
                continue
            asset = None
            for pattern in [f"withoutbg-scene_1_{ename}.png", f"withoutbg-{ename}.png"]:
                p = assets_dir / pattern
                if p.exists():
                    asset = p
                    break
            if not asset:
                continue
            seen_names.add(ename)
            bbox = get_bbox(asset)
            entities.append({
                "name": ename,
                "description": "",
                "asset_url": f"/image/{story_id}/assets/{asset.name}",
                "bbox": bbox,
                "in_scene": True,
            })

    parts = story_id.split("_")
    anim_type = parts[1] if len(parts) >= 3 else "unknown"

    result = {
        "story_id": story_id,
        "title": data.get("title", story_id),
        "anim_type": anim_type,
        "bg_url": f"/image/{story_id}/hd/scene_1_bg.png",
        "full_url": f"/image/{story_id}/hd/scene_1_full.png",
        "entities": entities,
    }
    # Pass through optional scene-level params (e.g. interjection_word for D4)
    if "interjection_word" in data:
        result["interjection_word"] = data["interjection_word"]

    return jsonify(result)


@app.route("/image/<story_id>/<path:subpath>")
def serve_image(story_id: str, subpath: str):
    img_path = PROLIFIC_GEN / story_id / subpath
    if not img_path.exists():
        return "Not found", 404
    return send_file(str(img_path), mimetype="image/png")


STATIC_JS_DIR = PROJECT_ROOT / "src" / "ui" / "static"


@app.route("/static_js/<path:filename>")
def serve_static_js(filename: str):
    """Serve JS files from src/ui/static/ for the pixel buffer animation system."""
    fpath = STATIC_JS_DIR / filename
    if not fpath.exists():
        return "Not found", 404
    return send_file(str(fpath), mimetype="application/javascript")


@app.route("/api/convert_mp4", methods=["POST"])
def api_convert_mp4():
    """Receive a webm blob, convert to MP4 (H.264), return MP4 file."""
    if "video" not in request.files:
        return jsonify({"error": "no video file"}), 400

    video = request.files["video"]
    filename = request.form.get("filename", "video")

    with tempfile.TemporaryDirectory() as tmp:
        webm_path = Path(tmp) / "input.webm"
        mp4_path = Path(tmp) / f"{filename}.mp4"

        video.save(str(webm_path))

        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", str(webm_path),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-an",  # no audio
            str(mp4_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.error("ffmpeg error: %s", result.stderr[-500:])
            return jsonify({"error": "ffmpeg conversion failed", "detail": result.stderr[-300:]}), 500

        return send_file(str(mp4_path), mimetype="video/mp4", as_attachment=True,
                         download_name=f"{filename}.mp4")



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Video preview & download UI")
    parser.add_argument("--port", type=int, default=5557)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    logger.info("Scanning scenes in %s", PROLIFIC_DIR)
    scenes = discover_scenes()
    logger.info("Found %d scenes with assets", len(scenes))

    app.run(host=args.host, port=args.port, debug=True)


if __name__ == "__main__":
    main()
