#!/usr/bin/env python3
"""Visual test for the anchor detection + resolution pipeline.

Usage:
    # Generate a fresh scene, then test the pipeline:
    python3 test_anchor_pipeline.py --generate --api-key KEY

    # Use existing files:
    python3 test_anchor_pipeline.py <hd_background.png> <scene.json> --api-key KEY

Loads (or generates) an HD background image and a scene JSON, runs the anchor
pipeline, and saves an annotated image showing:
  - Blue rectangles: detected structural element bounding boxes + name labels
  - Red crosses: original entity positions
  - Green crosses: resolved entity positions

Requires GEMINI_API_KEY env var or --api-key argument.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

from src.generation.anchor_detection import detect_anchors
from src.generation.anchor_resolver import resolve_positions


# ---------------------------------------------------------------------------
# HD image size (must match scene_generator.py constants)
# ---------------------------------------------------------------------------
HD_WIDTH = 1120
HD_HEIGHT = 720


def _normalize_pos(pos: dict) -> tuple[float, float]:
    """Convert position to normalized 0-1 coords, handling both formats.

    Old scenes use pixel coords (x=220, y=240 on 560x360 art grid).
    New scenes use normalized coords (x=0.39, y=0.67).
    """
    x = pos.get("x", 0.5)
    y = pos.get("y", 0.5)
    # If values > 1, assume pixel coords on art grid (280x180)
    if x > 1.0 or y > 1.0:
        x = x / 280.0
        y = y / 180.0
    return (min(1.0, max(0.0, x)), min(1.0, max(0.0, y)))


def _draw_cross(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: str, width: int = 2):
    """Draw a cross marker at (x, y)."""
    draw.line([(x - size, y - size), (x + size, y + size)], fill=color, width=width)
    draw.line([(x - size, y + size), (x + size, y - size)], fill=color, width=width)


def annotate(
    img: Image.Image,
    anchors: dict,
    scene_original: dict,
    scene_resolved: dict,
) -> Image.Image:
    """Draw bounding boxes, original positions, and resolved positions on image."""
    annotated = img.copy()
    draw = ImageDraw.Draw(annotated)
    w, h = annotated.size

    # Try to load a reasonable font; fall back to default
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except Exception:
        font = ImageFont.load_default()

    # --- Blue bounding boxes for detected structural elements ---
    for name, anchor_data in anchors.items():
        xmin, ymin, xmax, ymax = anchor_data["bbox"]
        px_xmin = int(xmin * w)
        px_ymin = int(ymin * h)
        px_xmax = int(xmax * w)
        px_ymax = int(ymax * h)

        draw.rectangle(
            [(px_xmin, px_ymin), (px_xmax, px_ymax)],
            outline="blue",
            width=2,
        )
        draw.text(
            (px_xmin, max(0, px_ymin - 16)),
            name,
            fill="blue",
            font=font,
        )

    # --- Entity positions ---
    original_entities = scene_original.get("manifest", {}).get("entities", [])
    resolved_entities = scene_resolved.get("manifest", {}).get("entities", [])

    resolved_by_id = {
        e["id"]: e for e in resolved_entities if "id" in e
    }

    cross_size = 8

    for entity in original_entities:
        eid = entity.get("id", "?")
        pos = entity.get("position", {})

        orig_x, orig_y = _normalize_pos(pos)
        px_orig_x = int(orig_x * w)
        px_orig_y = int(orig_y * h)

        # Red cross = original position
        _draw_cross(draw, px_orig_x, px_orig_y, cross_size, "red", width=2)
        draw.text(
            (px_orig_x + cross_size + 2, px_orig_y - 8),
            f"{eid} (orig)",
            fill="red",
            font=font,
        )

        # Green cross = resolved position (if changed)
        resolved_ent = resolved_by_id.get(eid)
        if resolved_ent:
            res_pos = resolved_ent.get("position", {})
            res_x, res_y = _normalize_pos(res_pos)

            if abs(res_x - orig_x) > 0.001 or abs(res_y - orig_y) > 0.001:
                px_res_x = int(res_x * w)
                px_res_y = int(res_y * h)

                _draw_cross(draw, px_res_x, px_res_y, cross_size, "lime", width=2)
                draw.text(
                    (px_res_x + cross_size + 2, px_res_y - 8),
                    f"{eid} (resolved)",
                    fill="lime",
                    font=font,
                )

                # Arrow from original to resolved
                draw.line(
                    [(px_orig_x, px_orig_y), (px_res_x, px_res_y)],
                    fill="yellow",
                    width=1,
                )

    return annotated


# ---------------------------------------------------------------------------
# Generate a test scene from scratch
# ---------------------------------------------------------------------------

async def generate_test_scene(api_key: str) -> tuple[Image.Image, dict]:
    """Generate a fresh scene with structural elements using the real pipeline.

    Returns (hd_background_image, scene_json).
    """
    from src.generation.scene_neg_generator import generate_scene_and_neg

    print("--- Generating scene manifest + NEG ---")
    _manifest, _neg, scene = await generate_scene_and_neg(api_key)

    # Print what we got
    entities = scene.get("manifest", {}).get("entities", [])
    bg = scene.get("manifest", {}).get("background", {})
    structural = bg.get("structural_elements", [])
    print(f"  Entities: {len(entities)}")
    print(f"  Structural elements: {len(structural)}")
    for se in structural:
        if isinstance(se, dict):
            print(f"    - {se.get('name')}: x={se.get('x')}, y={se.get('y')}, zone={se.get('zone')}")
        else:
            print(f"    - {se}")

    spatial_refs = [
        e.get("position", {}).get("spatial_ref")
        for e in entities
        if e.get("position", {}).get("spatial_ref")
    ]
    print(f"  Spatial refs: {spatial_refs}")

    # Generate background image
    print("\n--- Generating HD background image ---")
    from google import genai
    from src.generation.scene_generator import _generate_background
    client = genai.Client(api_key=api_key)
    bg_bytes = await _generate_background(client, scene)

    if not bg_bytes:
        print("ERROR: Background generation failed!", file=sys.stderr)
        sys.exit(1)

    bg_image = Image.open(__import__("io").BytesIO(bg_bytes)).convert("RGB")
    print(f"  Background size: {bg_image.size[0]}x{bg_image.size[1]}")

    return bg_image, scene


async def main():
    parser = argparse.ArgumentParser(description="Visual test for anchor pipeline")
    parser.add_argument("background", nargs="?", help="Path to HD background image (PNG/JPG)")
    parser.add_argument("scene_json", nargs="?", help="Path to scene manifest JSON file")
    parser.add_argument(
        "--generate", action="store_true",
        help="Generate a fresh scene instead of loading files",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GEMINI_API_KEY", ""),
        help="Gemini API key (or set GEMINI_API_KEY env var)",
    )
    parser.add_argument(
        "-o", "--output",
        default="test_anchor_result.png",
        help="Output path for annotated image (default: test_anchor_result.png)",
    )
    args = parser.parse_args()

    if not args.api_key:
        print("Error: provide --api-key or set GEMINI_API_KEY env var.", file=sys.stderr)
        sys.exit(1)

    if args.generate:
        # Generate fresh scene
        bg_image, scene = await generate_test_scene(args.api_key)
        # Save intermediates for reuse
        bg_image.save("test_anchor_bg.png")
        with open("test_anchor_scene.json", "w") as f:
            # Strip sprite_code (huge base64) for readability
            scene_save = {k: v for k, v in scene.items() if k != "sprite_code"}
            json.dump(scene_save, f, indent=2)
        print("  Saved: test_anchor_bg.png, test_anchor_scene.json")
    elif args.background and args.scene_json:
        bg_image = Image.open(args.background).convert("RGB")
        with open(args.scene_json, "r") as f:
            scene = json.load(f)
    else:
        print("Error: provide image + JSON paths, or use --generate.", file=sys.stderr)
        sys.exit(1)

    print(f"\nImage: {bg_image.size[0]}x{bg_image.size[1]}")
    entities = scene.get("manifest", {}).get("entities", [])
    structural = scene.get("manifest", {}).get("background", {}).get("structural_elements", [])
    print(f"Entities: {len(entities)}, Structural elements: {len(structural)}")

    # Step 1: Detect anchors
    print("\n--- Detecting anchors ---")
    anchors = await detect_anchors(args.api_key, bg_image, scene)
    print(f"Detected {len(anchors)} anchors:")
    for name, data in anchors.items():
        bbox = data["bbox"]
        print(f"  {name}: bbox=({bbox[0]:.3f}, {bbox[1]:.3f}, {bbox[2]:.3f}, {bbox[3]:.3f})")

    # Step 2: Resolve positions
    print("\n--- Resolving positions ---")
    scene_resolved = resolve_positions(scene, anchors)

    # Compare
    resolved_entities = scene_resolved.get("manifest", {}).get("entities", [])
    resolved_by_id = {e["id"]: e for e in resolved_entities if "id" in e}

    changes = 0
    for entity in entities:
        eid = entity.get("id", "?")
        pos = entity.get("position", {})
        spatial_ref = pos.get("spatial_ref")
        if not spatial_ref:
            continue

        orig_x, orig_y = _normalize_pos(pos)
        resolved_ent = resolved_by_id.get(eid, {})
        res_pos = resolved_ent.get("position", {})
        res_x, res_y = _normalize_pos(res_pos)

        moved = abs(res_x - orig_x) > 0.001 or abs(res_y - orig_y) > 0.001
        marker = " *MOVED*" if moved else ""
        if moved:
            changes += 1
        print(f"  {eid} [{spatial_ref}]: ({orig_x:.3f},{orig_y:.3f}) -> ({res_x:.3f},{res_y:.3f}){marker}")

    print(f"\n{changes} entities repositioned out of {len(entities)} total.")

    # Step 3: Annotate and save
    annotated = annotate(bg_image, anchors, scene, scene_resolved)
    annotated.save(args.output)
    print(f"\nAnnotated image saved to: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
