#!/usr/bin/env python3
"""Study 2 (Prolific revalidation of I2 / C3 / S1) counterbalancing lists.

Design (see ~/Downloads/study2_prolific_spec.md):
  * 3 target animations x 4 scenes (A-D) x 2 conditions = 24 target stimuli
  * 4 lists (list_id 1-4, k = list_id - 1), 10 participants each
  * block 1: 9 targets (3 animations x 3 scenes, scene k held out) + 3 fillers
  * block 2: the held-out scene k of each target animation (Likert)

Condition rule (replaces the spec's parity rule, which gave 8/4 per animation):
  * block 1, list k, animation i, scene j != k:
        pos = rank of k in sorted({0,1,2,3} - {j})
        condition = correction if (i + j + pos) even else suggestion
    -> per animation 6 correction / 6 suggestion across the 4 lists,
       each (animation, scene) seen in 3 lists with a 2/1 split
  * block 2, list k, animation i, scene k:
        condition = suggestion if (i + k) even else correction
    -> the minority block-1 condition, so every one of the 24 target stimuli
       is seen by exactly 2 lists (20 participants) across both blocks
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


def target_block1_condition(i: int, j: int, k: int) -> str:
    others = sorted(set(range(len(SCENES))) - {j})
    pos = others.index(k)
    return "correction" if (i + j + pos) % 2 == 0 else "suggestion"


def target_block2_condition(i: int, k: int) -> str:
    return "suggestion" if (i + k) % 2 == 0 else "correction"


def filler_condition(anim: str, k: int) -> str:
    even_is_correction = anim != "C2"
    if k % 2 == 0:
        return "correction" if even_is_correction else "suggestion"
    return "suggestion" if even_is_correction else "correction"


def build_list(k: int) -> dict:
    block1 = []
    block2 = []
    for i, anim in enumerate(TARGET_ANIMATIONS):
        for j, scene in enumerate(SCENES):
            if j == k:
                block2.append(_entry(TARGET_PREFIX, anim, scene, target_block2_condition(i, k), "target"))
            else:
                block1.append(_entry(TARGET_PREFIX, anim, scene, target_block1_condition(i, j, k), "target"))
    for anim in FILLER_ANIMATIONS:
        block1.append(_entry(FILLER_PREFIX, anim, SCENES[k], filler_condition(anim, k), "filler"))
    first_slot = k * PARTICIPANTS_PER_LIST + 1
    return {
        "list_id": k + 1,
        "k": k,
        "slots": f"{first_slot}-{first_slot + PARTICIPANTS_PER_LIST - 1}",
        "block2_scene": SCENES[k],
        "n_correction_block1": sum(e["condition"] == "correction" for e in block1),
        "n_suggestion_block1": sum(e["condition"] == "suggestion" for e in block1),
        "block1": block1,
        "block2": block2,
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
            "n_block1_per_participant": 12,
            "n_block2_per_participant": 3,
            "slot_to_list": "list_id = ceil(slot / 10)",
            "block1_order": "randomised per participant, no two consecutive items of the same animation",
            "condition_rule": (
                "block1: pos = rank of k in sorted({0..3} - {j}); correction iff (i+j+pos) even. "
                "block2: suggestion iff (i+k) even. "
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
    b2_stim = Counter()
    b1_anim_cond = Counter()
    b2_anim_cond = Counter()
    filler_scene = Counter()
    filler_cond = Counter()
    for lst in lists:
        if len(lst["block1"]) != 12:
            problems.append(f"list {lst['list_id']}: block1 has {len(lst['block1'])} items")
        if len(lst["block2"]) != 3:
            problems.append(f"list {lst['list_id']}: block2 has {len(lst['block2'])} items")
        ids = [e["stimulus_id"] for e in lst["block1"] + lst["block2"]]
        if len(set(ids)) != len(ids):
            problems.append(f"list {lst['list_id']}: duplicate stimulus")
        scenes_b1 = {(e["animation_id"], e["scene"]) for e in lst["block1"]}
        for e in lst["block2"]:
            if (e["animation_id"], e["scene"]) in scenes_b1:
                problems.append(f"list {lst['list_id']}: block2 scene {e['scene_id']} also in block1")
            b2_stim[e["stimulus_id"]] += 1
            b2_anim_cond[(e["animation_id"], e["condition"])] += 1
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
                if per_list[(anim, cond)] < 1:
                    problems.append(f"list {lst['list_id']}: {anim} never seen in {cond}")

    for anim in TARGET_ANIMATIONS:
        for cond in ("correction", "suggestion"):
            if b1_anim_cond[(anim, cond)] != 6:
                problems.append(f"block1 {anim}/{cond}: {b1_anim_cond[(anim, cond)]} views (expected 6)")
            if b2_anim_cond[(anim, cond)] != 2:
                problems.append(f"block2 {anim}/{cond}: {b2_anim_cond[(anim, cond)]} views (expected 2)")
        for scene in SCENES:
            for cond in ("correction", "suggestion"):
                sid = f"{TARGET_PREFIX}_{anim}_{scene}_{cond}"
                total = b1_stim[sid] + b2_stim[sid]
                if total != 2:
                    problems.append(f"{sid}: seen by {total} lists across blocks (expected 2)")
                if b1_stim[sid] not in (1, 2):
                    problems.append(f"{sid}: {b1_stim[sid]} block1 lists (expected 1 or 2)")
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
        print(f"list {lst['list_id']} (k={lst['k']}, slots {lst['slots']}, block2 scene {lst['block2_scene']}) "
              f"block1 {lst['n_correction_block1']}c/{lst['n_suggestion_block1']}s")
        for e in lst["block1"]:
            print(f"   b1 {e['stimulus_id']:28s} {e['role']}")
        for e in lst["block2"]:
            print(f"   b2 {e['stimulus_id']:28s}")
    views = Counter()
    for lst in data["lists"]:
        for e in lst["block1"] + lst["block2"]:
            if e["role"] == "target":
                views[e["stimulus_id"]] += 1
    print("target stimulus -> number of lists (both blocks):")
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
