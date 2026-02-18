"""Persistence layer for Tellimations participant data.

Directory layout under DATA_ROOT/users/:

    <participant_id>/
        profile.json              # StudentProfile (cumulative across sessions)
        story_001/
            scene_01.json         # Full scene data (manifest, NEG, sprite_code, etc.)
            scene_01_ref.png      # Reference image (if generated)
            scene_02.json
            scene_02_ref.png
            ...
        story_002/
            scene_01.json
            ...
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.models.student_profile import StudentProfile

logger = logging.getLogger(__name__)

DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "users"


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def _participant_dir(participant_id: str) -> Path:
    """Return the directory for a given participant, creating it if needed."""
    d = DATA_ROOT / participant_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _story_dir(participant_id: str, story_index: int) -> Path:
    """Return the directory for a specific story, creating it if needed."""
    d = _participant_dir(participant_id) / f"story_{story_index:03d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Participant existence check
# ---------------------------------------------------------------------------

def participant_exists(participant_id: str) -> bool:
    """Check if a participant directory already exists."""
    return (DATA_ROOT / participant_id).is_dir()


def ensure_participant(participant_id: str) -> Path:
    """Create participant directory if it doesn't exist. Return its path."""
    return _participant_dir(participant_id)


# ---------------------------------------------------------------------------
# Student profile persistence
# ---------------------------------------------------------------------------

def load_student_profile(participant_id: str) -> StudentProfile:
    """Load the student profile from disk, or return a fresh one."""
    profile_path = _participant_dir(participant_id) / "profile.json"
    if profile_path.exists():
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            return StudentProfile.model_validate(data)
        except Exception:
            logger.warning(
                "Failed to load profile for %s, starting fresh", participant_id
            )
    return StudentProfile()


def save_student_profile(participant_id: str, profile: StudentProfile) -> None:
    """Persist the student profile to disk."""
    profile_path = _participant_dir(participant_id) / "profile.json"
    profile_path.write_text(
        json.dumps(profile.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.debug("Saved profile for %s", participant_id)


# ---------------------------------------------------------------------------
# Story management
# ---------------------------------------------------------------------------

def list_stories(participant_id: str) -> List[Path]:
    """Return sorted list of story directories for a participant."""
    pdir = DATA_ROOT / participant_id
    if not pdir.is_dir():
        return []
    stories = sorted(
        d for d in pdir.iterdir()
        if d.is_dir() and d.name.startswith("story_")
    )
    return stories


def story_count(participant_id: str) -> int:
    """Return the number of existing stories for a participant."""
    return len(list_stories(participant_id))


def next_story_index(participant_id: str) -> int:
    """Return the index for the next new story."""
    stories = list_stories(participant_id)
    if not stories:
        return 1
    # Parse last index and increment
    last = stories[-1].name  # e.g. "story_003"
    try:
        last_idx = int(last.split("_")[1])
    except (IndexError, ValueError):
        last_idx = len(stories)
    return last_idx + 1


def create_story(participant_id: str) -> tuple[int, Path]:
    """Create a new story directory. Returns (story_index, story_path)."""
    idx = next_story_index(participant_id)
    path = _story_dir(participant_id, idx)
    logger.info("Created story %d for participant %s", idx, participant_id)
    return idx, path


# ---------------------------------------------------------------------------
# Scene persistence within a story
# ---------------------------------------------------------------------------

def save_scene(
    participant_id: str,
    story_index: int,
    scene_data: Dict[str, Any],
    reference_image: Optional[bytes] = None,
) -> None:
    """Save a scene (manifest + NEG + sprites + metadata) to a story folder.

    The scene_data dict is expected to have:
        - manifest (with scene_id)
        - neg
        - sprite_code
        - narrative_text
        - branch_summary
        - scene_description
        - carried_over_entities
    """
    sdir = _story_dir(participant_id, story_index)
    scene_id = scene_data.get("manifest", {}).get("scene_id", "unknown")

    # Save the full scene JSON
    scene_path = sdir / f"{scene_id}.json"
    scene_path.write_text(
        json.dumps(scene_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Save reference image if provided
    if reference_image:
        img_path = sdir / f"{scene_id}_ref.png"
        img_path.write_bytes(reference_image)

    logger.debug(
        "Saved scene %s to story_%03d for %s",
        scene_id, story_index, participant_id,
    )


def load_scenes(participant_id: str, story_index: int) -> List[Dict[str, Any]]:
    """Load all scenes from a story folder, ordered by filename."""
    sdir = DATA_ROOT / participant_id / f"story_{story_index:03d}"
    if not sdir.is_dir():
        return []

    scenes = []
    for f in sorted(sdir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            scenes.append(data)
        except Exception:
            logger.warning("Failed to load scene from %s", f)
    return scenes


# ---------------------------------------------------------------------------
# Load existing stories as initial scene candidates (for selection page)
# ---------------------------------------------------------------------------

def load_story_first_scenes(participant_id: str) -> List[Dict[str, Any]]:
    """Load the first scene from each story to use as selection thumbnails.

    Returns a list of scene dicts (one per story), suitable for display
    on the selection page when the participant already has >= 3 stories.
    """
    stories = list_stories(participant_id)
    first_scenes: List[Dict[str, Any]] = []

    for story_dir in stories:
        # Find JSON files in the story dir (sorted = scene order)
        json_files = sorted(story_dir.glob("*.json"))
        if not json_files:
            continue
        try:
            data = json.loads(json_files[0].read_text(encoding="utf-8"))
            # Tag with the story index so the client can reference it
            story_name = story_dir.name  # e.g. "story_003"
            try:
                data["_story_index"] = int(story_name.split("_")[1])
            except (IndexError, ValueError):
                data["_story_index"] = 0
            first_scenes.append(data)
        except Exception:
            logger.warning("Failed to load first scene from %s", story_dir)

    return first_scenes
