"""Persistence layer for Tellimations participant data.

Directory layout under DATA_ROOT/users/:

    <participant_id>/
        profile.json              # StudentProfile (cumulative across sessions)
        story_001/
            scene_01.json         # Full scene data (manifest, sprite_code, etc.)
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
import os
import urllib.request
import urllib.error
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
# Study session logging
# ---------------------------------------------------------------------------

def _study_log_path(participant_id: str, is_training: bool) -> Path:
    """Return the path to the study log JSON for a participant."""
    pdir = _participant_dir(participant_id)
    filename = "training_log.json" if is_training else "study_log.json"
    return pdir / filename


def load_study_log(participant_id: str, is_training: bool) -> Dict:
    """Load existing study log or return empty structure."""
    path = _study_log_path(participant_id, is_training)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"participant": participant_id, "type": "training" if is_training else "study", "scenes": {}}


def append_study_log_entry(
    participant_id: str,
    is_training: bool,
    story_key: str,
    scene_number: int,
    entry: Dict,
) -> None:
    """Append a timestamped entry to the study log for a given scene."""
    from datetime import datetime, timezone

    log = load_study_log(participant_id, is_training)
    scene_key = f"{story_key}_scene_{scene_number}"

    if scene_key not in log["scenes"]:
        log["scenes"][scene_key] = []

    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    log["scenes"][scene_key].append(entry)

    path = _study_log_path(participant_id, is_training)
    path.write_text(
        json.dumps(log, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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
    entity_images: Optional[Dict[str, bytes]] = None,
) -> None:
    """Save a scene (manifest + sprites + metadata) to a story folder.

    The scene_data dict is expected to have:
        - manifest (with scene_id)
        - sprite_code
        - narrative_text
        - branch_summary
        - scene_description
        - carried_over_entities

    Optionally saves:
        - reference_image: background PNG bytes
        - entity_images: dict mapping entity_id -> PNG bytes (for debugging)
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

    # Save entity images if provided (for debugging)
    if entity_images:
        for eid, img_bytes in entity_images.items():
            img_path = sdir / f"{scene_id}_{eid}.png"
            img_path.write_bytes(img_bytes)
            logger.debug("Saved entity image %s for scene %s", eid, scene_id)

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


# ---------------------------------------------------------------------------
# Google Sheet push (via Apps Script webhook)
# ---------------------------------------------------------------------------

GOOGLE_SHEET_WEBHOOK = os.environ.get("GOOGLE_SHEET_WEBHOOK", "")


def push_to_google_sheet(participant_id: str) -> bool:
    """Push participant data (profile + study_log + training_log) to Google Sheet.

    Returns True on success, False on failure. Never raises.
    """
    if not GOOGLE_SHEET_WEBHOOK:
        logger.debug("GOOGLE_SHEET_WEBHOOK not set — skipping push")
        return False

    pdir = DATA_ROOT / participant_id
    if not pdir.is_dir():
        logger.warning("No data directory for %s — skipping push", participant_id)
        return False

    rows = []
    for filename, data_type in [
        ("profile.json", "profile"),
        ("study_log.json", "study_log"),
        ("training_log.json", "training_log"),
    ]:
        fpath = pdir / filename
        if fpath.exists():
            try:
                content = fpath.read_text(encoding="utf-8")
                rows.append({
                    "participant_id": participant_id,
                    "data_type": data_type,
                    "json_data": content,
                })
            except Exception:
                logger.warning("Failed to read %s for %s", filename, participant_id)

    if not rows:
        logger.info("No data files to push for %s", participant_id)
        return False

    payload = json.dumps({"rows": rows}).encode("utf-8")
    req = urllib.request.Request(
        GOOGLE_SHEET_WEBHOOK,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            logger.info(
                "Pushed %d rows to Google Sheet for %s: %s",
                len(rows), participant_id, body,
            )
            return True
    except Exception:
        logger.exception("Failed to push data to Google Sheet for %s", participant_id)
        return False
