#!/usr/bin/env python3
"""Build data/study2/study2_all_stimuli.json from the Study 1 stimuli.

Targets (24): study1_{I2,C3,S1}_{A-D}_{correction,suggestion} copied with
  * stimulus_id / scene_id renamed study2_..., source_scene_id = the Study 1
    scene (images and assets live under data/prolific_gen/<source_scene_id>/)
  * narrator_text, options, narrator_text_block2 kept verbatim (same scenes,
    same task; only the animation video changes)
  * pipeline_intent of the correction stimuli replaced from
    data/study2/study2_intents.json (describes the new animation)
  * a few option texts corrected from data/study2/study2_option_overrides.json
    (Study 1 wording that did not match the scene); the originals are kept
    in study1_options
Fillers (24): study1_{P2f,C2,T1}_{A-D}_* copied verbatim, ids unchanged.

Usage:
    python -m scripts.study2.build_stimuli
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STUDY1_STIMULI = PROJECT_ROOT / "data" / "study1_all_stimuli.json"
INTENTS_PATH = PROJECT_ROOT / "data" / "study2" / "study2_intents.json"
OVERRIDES_PATH = PROJECT_ROOT / "data" / "study2" / "study2_option_overrides.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "study2" / "study2_all_stimuli.json"

TARGET_ANIMATIONS = ["I2", "C3", "S1"]
FILLER_ANIMATIONS = ["P2f", "C2", "T1"]
OLD_ANIMATION_WORDS = re.compile(r"nametag|name tag|ghost|outline|reveal", re.IGNORECASE)


def animation_of(stim: dict) -> str:
    return stim.get("target_animation") or stim.get("pipeline_animation_id") or stim["scene_id"].split("_")[1]


def main() -> None:
    with open(STUDY1_STIMULI) as f:
        study1 = json.load(f)["stimuli"]
    with open(INTENTS_PATH) as f:
        intents = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    with open(OVERRIDES_PATH) as f:
        overrides = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

    targets, fillers = [], []
    for stim in study1:
        anim = animation_of(stim)
        if anim in TARGET_ANIMATIONS:
            new = copy.deepcopy(stim)
            new["stimulus_id"] = stim["stimulus_id"].replace("study1_", "study2_", 1)
            new["scene_id"] = stim["scene_id"].replace("study1_", "study2_", 1)
            new["source_scene_id"] = stim["scene_id"]
            new["animation_id"] = anim
            new["role"] = "target"
            new["study1_pipeline_intent"] = stim["pipeline_intent"]
            if stim["condition"] == "correction":
                new["pipeline_intent"] = intents.pop(new["stimulus_id"])
            for code, text in overrides.pop(new["stimulus_id"], {}).items():
                new.setdefault("study1_options", dict(stim["options"]))
                new["options"][code] = text
            if OLD_ANIMATION_WORDS.search(new["pipeline_intent"]):
                raise SystemExit(f"{new['stimulus_id']}: intent still describes the old animation: {new['pipeline_intent']}")
            targets.append(new)
        elif anim in FILLER_ANIMATIONS:
            new = copy.deepcopy(stim)
            new["source_scene_id"] = stim["scene_id"]
            new["animation_id"] = anim
            new["role"] = "filler"
            fillers.append(new)
    if intents:
        raise SystemExit(f"unused intents: {sorted(intents)}")
    if overrides:
        raise SystemExit(f"unused option overrides: {sorted(overrides)}")
    if len(targets) != 24 or len(fillers) != 24:
        raise SystemExit(f"expected 24 targets and 24 fillers, got {len(targets)} / {len(fillers)}")

    out = {
        "metadata": {
            "study": "study2",
            "n_targets": len(targets),
            "n_fillers": len(fillers),
            "target_animations": TARGET_ANIMATIONS,
            "filler_animations": FILLER_ANIMATIONS,
            "source": str(STUDY1_STIMULI.relative_to(PROJECT_ROOT)),
            "note": "Target narrator texts and options are the Study 1 ones; only the video and the correction pipeline_intent change.",
        },
        "stimuli": targets + fillers,
    }
    with open(OUTPUT_PATH, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {OUTPUT_PATH}: {len(targets)} targets + {len(fillers)} fillers")


if __name__ == "__main__":
    main()
