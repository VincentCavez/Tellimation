#!/usr/bin/env python3
"""Batch-generate study scene assets from study_stories_source.json.

For each story (A-D) and each scene (1-5), this script:
  1. Builds a manifest dict from the source JSON descriptions
  2. Calls generate_scene_manifest() to produce the full LLM manifest
  3. Calls generate_scene_assets() to produce pixel-art sprites + background
  4. Saves the complete scene JSON to data/study_scenes/{story}/scene_{N}.json
  5. Maintains StoryState across scenes for sprite carry-over

Usage:
    python -m scripts.generate_study_scenes                    # Generate all
    python -m scripts.generate_study_scenes --story A          # Story A only
    python -m scripts.generate_study_scenes --story A --scene 3  # Single scene
    python -m scripts.generate_study_scenes --dry-run          # Validate only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.generation.scene_neg_generator import generate_scene_manifest
from src.generation.scene_generator import generate_scene_assets
from src.models.story_state import StoryState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("generate_study_scenes")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SOURCE_PATH = PROJECT_ROOT / "data" / "study_stories_source.json"
OUTPUT_BASE = PROJECT_ROOT / "data" / "study_scenes"
STORIES_CONFIG_PATH = PROJECT_ROOT / "config" / "study_stories.json"


def load_source() -> Dict[str, Any]:
    with open(SOURCE_PATH) as f:
        return json.load(f)


def save_scene(story_key: str, scene_number: int, scene_data: Dict[str, Any]) -> Path:
    """Save a scene JSON to data/study_scenes/{story}/scene_{N}.json."""
    out_dir = OUTPUT_BASE / story_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"scene_{scene_number}.json"
    with open(out_path, "w") as f:
        json.dump(scene_data, f, indent=2)
    logger.info("Saved %s (%d bytes)", out_path, out_path.stat().st_size)
    return out_path


# ---------------------------------------------------------------------------
# Manifest building from source descriptions
# ---------------------------------------------------------------------------


def build_theme_prompt(story: Dict[str, Any], scene: Dict[str, Any]) -> str:
    """Build a detailed theme prompt from source scene description.

    This is fed to generate_scene_manifest() as the 'theme' parameter.
    It includes the scene description, setting, characters, spatial relations,
    and the ground truth targets — giving the LLM maximum guidance to produce
    a manifest that matches our study design.
    """
    parts = []
    parts.append(f"Story: {story['title']}")
    parts.append(f"Scene {scene['scene_number']}: {scene['title']}")
    parts.append("")
    parts.append(f"SCENE DESCRIPTION (follow exactly):")
    parts.append(scene["scene_description"])
    parts.append("")
    parts.append(f"Setting: {scene['setting']}")
    parts.append("")

    # Characters with visual specs
    parts.append("Characters in this story:")
    char_map = {c["id"]: c for c in story.get("characters", [])}
    for eid in scene.get("entities_present", []):
        ch = char_map.get(eid)
        if ch:
            parts.append(f"  - {eid}: {ch['description']} (visual: {ch['visual_spec']})")
        else:
            parts.append(f"  - {eid}: (object/prop)")

    # Spatial relations
    rels = scene.get("spatial_relations", [])
    if rels:
        parts.append("")
        parts.append("Spatial relations (MUST be reflected in entity positions):")
        for r in rels:
            parts.append(f"  - {r['entity']} {r['relation']} {r['reference']}")

    # Ground truth targets (guide entity properties for MISL)
    gt = scene.get("ground_truth", {})
    micro = gt.get("micro", {})
    enp = micro.get("target_ENP", [])
    if enp:
        parts.append("")
        parts.append("Descriptive noun phrases to support (as entity properties):")
        for phrase in enp:
            parts.append(f"  - {phrase}")

    return "\n".join(parts)


def build_continuation_context(
    story: Dict[str, Any],
    scene: Dict[str, Any],
    scene_idx: int,
    previous_scenes: List[Dict[str, Any]],
) -> str:
    """Build context for continuation scenes (scene 2+)."""
    parts = []
    parts.append(f"Story: {story['title']}")
    parts.append(f"Scene {scene['scene_number']}: {scene['title']}")
    parts.append("")

    # Summary of previous scenes
    parts.append("PREVIOUS SCENES:")
    for prev in previous_scenes:
        prev_src = story["scenes"][prev["scene_index"]]
        parts.append(f"  Scene {prev_src['scene_number']}: {prev_src['title']}")
        parts.append(f"    {prev_src['scene_description'][:200]}")
    parts.append("")

    parts.append(f"CURRENT SCENE DESCRIPTION (follow exactly):")
    parts.append(scene["scene_description"])
    parts.append("")
    parts.append(f"Setting: {scene['setting']}")
    parts.append("")

    # Characters
    char_map = {c["id"]: c for c in story.get("characters", [])}
    parts.append("Characters present:")
    for eid in scene.get("entities_present", []):
        ch = char_map.get(eid)
        if ch:
            carried = " (recurring — carry over sprite)" if ch.get("recurring") else ""
            parts.append(f"  - {eid}: {ch['description']}{carried}")
        else:
            parts.append(f"  - {eid}: (object/prop)")

    # Spatial relations
    rels = scene.get("spatial_relations", [])
    if rels:
        parts.append("")
        parts.append("Spatial relations:")
        for r in rels:
            parts.append(f"  - {r['entity']} {r['relation']} {r['reference']}")

    # ENP targets
    gt = scene.get("ground_truth", {})
    micro = gt.get("micro", {})
    enp = micro.get("target_ENP", [])
    if enp:
        parts.append("")
        parts.append("Descriptive noun phrases to support:")
        for phrase in enp:
            parts.append(f"  - {phrase}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Scene generation pipeline
# ---------------------------------------------------------------------------


async def generate_story(
    api_key: str,
    story_key: str,
    story: Dict[str, Any],
    scene_filter: Optional[int] = None,
    dry_run: bool = False,
) -> None:
    """Generate all scenes for a single story."""
    logger.info("=" * 60)
    logger.info("STORY %s: %s (%d scenes)", story_key, story["title"], len(story["scenes"]))
    logger.info("=" * 60)

    story_state = StoryState(session_id=f"study_{story_key}")
    previous_manifest: Optional[Dict[str, Any]] = None
    previous_scenes: List[Dict[str, Any]] = []

    for scene_idx, scene in enumerate(story["scenes"]):
        scene_num = scene["scene_number"]

        if scene_filter is not None and scene_num != scene_filter:
            # Load existing scene to maintain story state continuity
            existing_path = OUTPUT_BASE / story_key / f"scene_{scene_num}.json"
            if existing_path.exists():
                logger.info("Scene %d: skipped (filter), loading existing for state", scene_num)
                with open(existing_path) as f:
                    existing = json.load(f)
                story_state.add_scene(
                    scene_id=f"scene_{scene_num:02d}",
                    narrative_text=scene["scene_description"],
                    manifest=existing.get("manifest", {}),
                    sprite_code=existing.get("sprite_code", {}),
                    accepted_utterances=[],
                )
                previous_manifest = existing
                previous_scenes.append({"scene_index": scene_idx})
            else:
                logger.warning("Scene %d: skipped (filter), no existing file", scene_num)
            continue

        logger.info("-" * 40)
        logger.info("Scene %d/%d: %s", scene_num, len(story["scenes"]), scene["title"])
        logger.info("-" * 40)

        if dry_run:
            theme = build_theme_prompt(story, scene)
            logger.info("[dry-run] Theme prompt (%d chars):", len(theme))
            logger.info(theme[:500] + ("..." if len(theme) > 500 else ""))
            previous_scenes.append({"scene_index": scene_idx})
            continue

        # Step 1: Generate manifest via LLM
        is_initial = scene_idx == 0
        if is_initial:
            theme = build_theme_prompt(story, scene)
            logger.info("Generating initial manifest (theme: %d chars)...", len(theme))
            manifest, manifest_data = await generate_scene_manifest(
                api_key=api_key,
                theme=theme,
            )
        else:
            context = build_continuation_context(story, scene, scene_idx, previous_scenes)
            logger.info("Generating continuation manifest (context: %d chars)...", len(context))

            # For continuations, we pass the story_state and previous manifest.
            # Use the context as the theme since the continuation prompt builder
            # also needs story context — but the scene description is the key guide.
            manifest, manifest_data = await generate_scene_manifest(
                api_key=api_key,
                story_state=story_state,
                previous_manifest=previous_manifest,
                theme=context,
                accepted_utterances=[],  # No child narration for pre-generated scenes
            )

        logger.info(
            "Manifest: %d entities, %d relations",
            len(manifest.entities),
            len(manifest.relations),
        )

        # Step 2: Generate assets (images → pixel art)
        logger.info("Generating assets...")
        t0 = time.time()
        assets = await generate_scene_assets(
            api_key=api_key,
            manifest_data=manifest_data,
            story_state=story_state if not is_initial else None,
        )
        elapsed = time.time() - t0
        logger.info("Assets generated in %.1fs: %d sprites", elapsed, len(assets["sprite_code"]))

        # Build scene dict (matches expected format)
        scene_dict = {
            "narrative_text": scene["scene_description"],
            "scene_description": manifest_data.get("scene_description", scene["scene_description"]),
            "manifest": manifest_data.get("manifest", {}),
            "sprite_code": assets["sprite_code"],
            "ground_truth": scene.get("ground_truth", {}),
            "story_key": story_key,
            "scene_number": scene_num,
            "title": scene["title"],
        }

        # Save to disk
        save_scene(story_key, scene_num, scene_dict)

        # Update story state for next scene
        story_state.add_scene(
            scene_id=f"scene_{scene_num:02d}",
            narrative_text=scene["scene_description"],
            manifest=manifest_data.get("manifest", {}),
            sprite_code=assets["sprite_code"],
            accepted_utterances=[],
        )
        previous_manifest = manifest_data
        previous_scenes.append({"scene_index": scene_idx})

        logger.info("Scene %d complete.", scene_num)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate study scene assets")
    parser.add_argument("--story", type=str, choices=["A", "B", "C", "D"],
                        help="Generate only this story (default: all)")
    parser.add_argument("--scene", type=int, choices=[1, 2, 3, 4, 5],
                        help="Generate only this scene number (requires --story)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate source data and show prompts without generating")
    parser.add_argument("--api-key", type=str, default=None,
                        help="Gemini API key (default: GEMINI_API_KEY env var)")
    args = parser.parse_args()

    if args.scene and not args.story:
        parser.error("--scene requires --story")

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        logger.error("No API key. Set GEMINI_API_KEY or use --api-key.")
        sys.exit(1)

    source = load_source()
    stories_to_generate = [args.story] if args.story else ["A", "B", "C", "D"]

    t_total = time.time()
    for story_key in stories_to_generate:
        story_data = source.get(story_key)
        if not story_data:
            logger.error("Story %s not found in source JSON", story_key)
            continue

        await generate_story(
            api_key=api_key,
            story_key=story_key,
            story=story_data,
            scene_filter=args.scene,
            dry_run=args.dry_run,
        )

    elapsed_total = time.time() - t_total
    logger.info("=" * 60)
    logger.info("ALL DONE in %.1fs", elapsed_total)
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
