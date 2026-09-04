#!/usr/bin/env python3
"""Study 2 (Prolific revalidation of I2 / C3 / S1) counterbalancing lists.

Design (spec ~/Downloads/study2_prolific_spec.md, block 2 dropped on 2026-09-04:
it rates the pipeline's decision text, which did not change):
  * 3 target animations x 4 scenes (A-D) x 2 conditions = 24 target stimuli
  * 4 lists (list_id 1-4, k = list_id - 1), 10 participants each
  * block 1 only: 12 targets (every animation on all 4 scenes, one condition
    each) + 3 fillers = 15 forced-choice items

Condition rule:
  * list k, animation i, scene j: correction if (i + j + k) even else suggestion
    -> per list and animation 2 correction / 2 suggestion; each of the 24
       target stimuli is seen by exactly 2 lists (20 participants)
  * fillers, list k: P2f_k, C2_k, T1_k; P2f/T1 correction if k even,
    C2 suggestion if k even -> 2/2 per filler animation, each scene once

Usage:
    python -m scripts.study2.build_lists            # write data/study2/study2_lists.json
    python -m scripts.study2.build_lists --verify   # write + check counts
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "study2" / "study2_lists.json"

TARGET_ANIMATIONS = ["I2", "C3", "S1"]
FILLER_ANIMATIONS = ["P2f", "C2", "T1"]
SCENES = ["A", "B", "C", "D"]
N_LISTS = 4
PARTICIPANTS_PER_LIST = 10
TARGET_PREFIX = "study2"
FILLER_PREFIX = "study1"


def _entry(prefix: str, anim: str, scene: str, condition: str, role: str) -> dict:
    scene_id = f"{prefix}_{anim}_{scene}"
    return {
        "stimulus_id": f"{scene_id}_{condition}",
        "scene_id": scene_id,
        "animation_id": anim,
        "scene": scene,
        "condition": condition,
        "role": role,
    }


def target_condition(i: int, j: int, k: int) -> str:
    return "correction" if (i + j + k) % 2 == 0 else "suggestion"


def filler_condition(anim: str, k: int) -> str:
    even_is_correction = anim != "C2"
    if k % 2 == 0:
        return "correction" if even_is_correction else "suggestion"
    return "suggestion" if even_is_correction else "correction"


def build_list(k: int) -> dict:
    block1 = []
    for i, anim in enumerate(TARGET_ANIMATIONS):
        for j, scene in enumerate(SCENES):
            block1.append(_entry(TARGET_PREFIX, anim, scene, target_condition(i, j, k), "target"))
    for anim in FILLER_ANIMATIONS:
        block1.append(_entry(FILLER_PREFIX, anim, SCENES[k], filler_condition(anim, k), "filler"))
    first_slot = k * PARTICIPANTS_PER_LIST + 1
    return {
        "list_id": k + 1,
        "k": k,
        "slots": f"{first_slot}-{first_slot + PARTICIPANTS_PER_LIST - 1}",
        "n_correction_block1": sum(e["condition"] == "correction" for e in block1),
        "n_suggestion_block1": sum(e["condition"] == "suggestion" for e in block1),
        "block1": block1,
        "block2": [],
    }


def build() -> dict:
    lists = [build_list(k) for k in range(N_LISTS)]
    return {
        "design": {
            "study": "study2",
            "n_lists": N_LISTS,
            "n_participants_per_list": PARTICIPANTS_PER_LIST,
            "n_participants_total": N_LISTS * PARTICIPANTS_PER_LIST,
            "target_animations": TARGET_ANIMATIONS,
            "filler_animations": FILLER_ANIMATIONS,
            "n_scenes_per_animation": len(SCENES),
            "n_block1_per_participant": 15,
            "n_block2_per_participant": 0,
            "slot_to_list": "list_id = ceil(slot / 10)",
            "block1_order": "randomised per participant, no two consecutive items of the same animation",
            "condition_rule": (
                "targets: correction iff (i_animation + i_scene + k) even. "
                "fillers: scene k; P2f/T1 correction iff k even, C2 suggestion iff k even."
            ),
        },
        "lists": lists,
    }


def verify(data: dict) -> list[str]:
    """Return a list of human-readable violations (empty = design OK)."""
    problems: list[str] = []
    lists = data["lists"]
    if len(lists) != N_LISTS:
        problems.append(f"expected {N_LISTS} lists, got {len(lists)}")

    b1_stim = Counter()
    b1_anim_cond = Counter()
    filler_scene = Counter()
    filler_cond = Counter()
    for lst in lists:
        if len(lst["block1"]) != 15:
            problems.append(f"list {lst['list_id']}: block1 has {len(lst['block1'])} items")
        if lst["block2"]:
            problems.append(f"list {lst['list_id']}: block2 should be empty")
        ids = [e["stimulus_id"] for e in lst["block1"]]
        if len(set(ids)) != len(ids):
            problems.append(f"list {lst['list_id']}: duplicate stimulus")
        scenes = Counter((e["animation_id"], e["scene"]) for e in lst["block1"] if e["role"] == "target")
        for anim in TARGET_ANIMATIONS:
            for scene in SCENES:
                if scenes[(anim, scene)] != 1:
                    problems.append(f"list {lst['list_id']}: {anim}_{scene} seen {scenes[(anim, scene)]} times")
        per_list = Counter()
        for e in lst["block1"]:
            if e["role"] == "target":
                b1_stim[e["stimulus_id"]] += 1
                b1_anim_cond[(e["animation_id"], e["condition"])] += 1
                per_list[(e["animation_id"], e["condition"])] += 1
            else:
                filler_scene[(e["animation_id"], e["scene"])] += 1
                filler_cond[(e["animation_id"], e["condition"])] += 1
        for anim in TARGET_ANIMATIONS:
            for cond in ("correction", "suggestion"):
                if per_list[(anim, cond)] != 2:
                    problems.append(f"list {lst['list_id']}: {anim}/{cond} seen {per_list[(anim, cond)]} times (expected 2)")

    for anim in TARGET_ANIMATIONS:
        for cond in ("correction", "suggestion"):
            if b1_anim_cond[(anim, cond)] != 8:
                problems.append(f"{anim}/{cond}: {b1_anim_cond[(anim, cond)]} list views (expected 8)")
        for scene in SCENES:
            for cond in ("correction", "suggestion"):
                sid = f"{TARGET_PREFIX}_{anim}_{scene}_{cond}"
                if b1_stim[sid] != 2:
                    problems.append(f"{sid}: seen by {b1_stim[sid]} lists (expected 2)")
    for anim in FILLER_ANIMATIONS:
        for scene in SCENES:
            if filler_scene[(anim, scene)] != 1:
                problems.append(f"filler {anim}_{scene}: {filler_scene[(anim, scene)]} lists (expected 1)")
        for cond in ("correction", "suggestion"):
            if filler_cond[(anim, cond)] != 2:
                problems.append(f"filler {anim}/{cond}: {filler_cond[(anim, cond)]} lists (expected 2)")
    return problems


def print_summary(data: dict) -> None:
    for lst in data["lists"]:
        print(f"list {lst['list_id']} (k={lst['k']}, slots {lst['slots']}) "
              f"block1 {lst['n_correction_block1']}c/{lst['n_suggestion_block1']}s")
        for e in lst["block1"]:
            print(f"   {e['stimulus_id']:28s} {e['role']}")
    views = Counter(e["stimulus_id"] for lst in data["lists"] for e in lst["block1"] if e["role"] == "target")
    print("target stimulus -> number of lists:")
    for sid in sorted(views):
        print(f"   {sid:28s} {views[sid]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="check the design and print counts")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    data = build()
    problems = verify(data)
    if problems:
        for p in problems:
            print("VIOLATION:", p)
        raise SystemExit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"wrote {args.output}")
    if args.verify:
        print_summary(data)
        print("design OK")


if __name__ == "__main__":
    main()
