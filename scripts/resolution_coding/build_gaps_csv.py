#!/usr/bin/env python3
"""Adapter for the blind re-coding of resolutions: session logs -> gaps.csv.

Input: the merged session CSV written by scripts/analysis/merge_session_logs.py
(one row per logged event; the same file the MISL forms are built from). The
session files themselves are not read again and are never modified.

Output: gaps.csv, one row per gap the pipeline identified (see
RESOLUTION_CODING.md for the columns), read by make_coding_items.py. Two more
columns are written for the reports: `source` (session / simulation) and
`pass_type` (correction / suggestion).

How a gap is read from the log (src/ui/app.py, _handle_study_audio):
  - one turn = transcription, [resolution], pipeline_cycle, [animation_played]
  - the turn has a gap when pipeline_cycle.selected.source is "error" or
    "suggestion". Its text is the rationale of the chosen discrepancy:
    animation_played.rationale when the animation was logged, otherwise the
    matching entry of pipeline_cycle.errors_found, or pipeline_cycle.suggestion
  - the pipeline's judgment on that gap is the `resolution` event of the NEXT
    turn of the same scene. A `resolution` event in the first turn of a scene
    judges the last gap of the previous scene: it is counted and never used.
  - a `resolution` event names the discrepancy it judged. When the next turn
    judged another discrepancy (in wave 1, a C3 gap without target was neither
    shown nor stored, and the turn after it judged the gap before it), the gap
    was never assessed by the pipeline: it is reported and left out of
    gaps.csv, which holds the assessed gaps and the last gap of each scene.
  - a recording whose text is empty in the CSV (emptied in the review page)
    is treated as removed: its gap is left out, and the gap before it has no
    next utterance.
  - interrupted = animation_played.displayed is false (the child pressed
    push-to-talk before the animation appeared). An animation still pending
    when the child clicked "Next Scene" is logged at that moment with
    resolved=false; it has no next utterance, so it is never a coding item.

Usage:
    python3 build_gaps_csv.py "../../session transcripts/sessions_reelles_et_simulees_v6.csv" --out gaps.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

csv.field_size_limit(sys.maxsize)

REPO = Path(__file__).resolve().parents[2]
COLUMNS = ["child", "story", "condition", "scene_key", "scene_image", "utterance", "gap",
           "next_utterance", "llm_resolved", "animation_id", "interrupted", "is_practice",
           "source", "pass_type"]
PAPER = {"animation": {"stories": 24, "cycles": 20.6, "kept": 425, "resolved": 197},
         "control": {"stories": 22, "cycles": 14.7, "kept": 265, "resolved": 34}}
# Rationales that talk about what happens on screen rather than about the narration.
ON_SCREEN = re.compile(r"\b(animation|spotlight|highlight\w*|shak(e|es|ing)|wiggl\w*|flash(es|ing)?|"
                       r"on (the )?screen|nametag|name tag|this suggestion|I can|I will|I'll)\b", re.I)


def base(animation_id: str) -> str:
    """'P2c_emanation_joy' -> 'P2c' (the id used in errors_found and in resolution events)."""
    return (animation_id or "").split("_")[0]


def loads(value: str):
    """Parse a JSON cell; empty cell -> None."""
    return json.loads(value) if value else None


def image_dirs() -> Dict[str, str]:
    """Story key -> image folder, from config/study_stories.json."""
    cfg = json.loads((REPO / "config" / "study_stories.json").read_text(encoding="utf-8"))
    return {k: v.get("image_dir", "") for k, v in cfg.items() if isinstance(v, dict)}


def scene_image(dirs: Dict[str, str], story: str, scene: str) -> str:
    """Path of the scene illustration (relative to the repository), '' if not found."""
    for folder in (dirs.get(story, ""), f"data/study_gen/{story}"):
        for sub in ("hd", ""):
            p = Path(folder) / sub / f"scene_{scene}_full.png"
            if folder and (REPO / p).exists():
                return str(p)
    return ""


def gap_of(cycle: Dict[str, str], played: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """The gap chosen in one turn, or None when the pipeline chose nothing."""
    selected = loads(cycle["selected"]) or {}
    source = selected.get("source")
    if source not in ("error", "suggestion") or not selected.get("animation_id"):
        return None
    animation_id = selected["animation_id"]
    pass_type = "correction" if source == "error" else "suggestion"
    logged = played["rationale"].strip() if played else ""
    if source == "error":
        # The animation shown can belong to another family than the discrepancy
        # (C1 -> C2_disintegration): match on the text first, then on the id.
        errors = loads(cycle["errors_found"]) or []
        exact = [e for e in errors if logged and (e.get("desc") or "").strip() == logged]
        same = [e for e in errors if e.get("animation_id") == base(animation_id)]
        both = [e for e in same if (e.get("targets") or []) == (selected.get("targets") or [])]
        chosen = (exact or both or same or errors or [{}])[0]
    else:
        suggestion = loads(cycle["suggestion"]) or {}
        chosen = {"desc": suggestion.get("rationale"), "animation_id": suggestion.get("animation_id")}
    return {"gap": logged or (chosen.get("desc") or "").strip(), "animation_id": animation_id,
            "discrepancy_id": chosen.get("animation_id") or base(animation_id), "pass_type": pass_type}


def build(csv_path: Path) -> tuple[List[Dict], Dict]:
    """Return (gap rows, report counters)."""
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["phase"] in ("study", "training") and r["scene"]]
    scenes: Dict[tuple, List[Dict]] = defaultdict(list)
    for r in rows:
        scenes[r["source"], r["participant_id"], r["phase"], r["story"], int(r["scene"])].append(r)

    dirs = image_dirs()
    out: List[Dict] = []
    rep = {"cross_scene": Counter(), "not_assessed": [], "emptied": [], "no_text": 0,
           "cycles": Counter(), "stories": defaultdict(set), "flushed_next_scene": Counter(),
           "interrupted_all": Counter(), "played_all": Counter(), "mixed_condition": set()}

    for (source, pid, phase, story, scene), events in sorted(scenes.items()):
        events.sort(key=lambda r: int(r["event_index"]))
        turns: Dict[int, Dict[str, Dict]] = defaultdict(dict)
        for e in events:
            if e["turn"]:
                turns[int(e["turn"])].setdefault(e["event"], e)
        practice = phase == "training"
        for t in sorted(turns):
            turn = turns[t]
            cycle, played = turn.get("pipeline_cycle"), turn.get("animation_played")
            if not cycle or "transcription" not in turn:
                continue
            condition = cycle["condition"]
            if not practice and condition != cycle["story_condition"]:
                rep["mixed_condition"].add((pid, story))
            if not practice:
                rep["cycles"][condition] += 1
                rep["stories"][condition].add((pid, story))
            if t == 1 and "resolution" in turn:
                rep["cross_scene"][condition] += 1
            if played:
                rep["played_all"][practice] += 1
                if played["displayed"] == "False":
                    rep["interrupted_all"][practice] += 1
                if played["resolved"]:
                    rep["flushed_next_scene"][practice] += 1
            gap = gap_of(cycle, played)
            if not gap:
                continue
            if not turn["transcription"]["text"].strip():
                rep["emptied"].append(f"{pid} {story} scene {scene} turn {t}")
                continue
            if not gap["gap"]:
                rep["no_text"] += 1
            nxt = turns.get(t + 1, {})
            next_text = nxt["transcription"]["text"].strip() if "transcription" in nxt else ""
            judgment = nxt.get("resolution")
            if judgment and judgment["animation_id"] and judgment["animation_id"] != gap["discrepancy_id"]:
                judgment = None
            if next_text and not judgment:
                rep["not_assessed"].append(f"{pid} {story} scene {scene} turn {t} ({gap['animation_id']}"
                                           f"{', practice' if practice else ''})")
                if not practice:
                    continue
            out.append({
                "child": pid if pid.startswith("SIM-") else f"P{pid}",
                "story": story, "condition": condition, "scene_key": f"{story}_scene_{scene}",
                "scene_image": scene_image(dirs, story, str(scene)),
                "utterance": turn["transcription"]["text"].strip(), "gap": gap["gap"],
                "next_utterance": next_text,
                "llm_resolved": "" if not judgment else int(judgment["resolved"] == "True"),
                "animation_id": gap["animation_id"],
                "interrupted": int(bool(played) and played["displayed"] == "False"),
                "is_practice": int(practice), "source": source, "pass_type": gap["pass_type"],
            })
    return out, rep


def report(gaps: List[Dict], rep: Dict) -> None:
    """Print the checks asked for in RESOLUTION_CODING.md."""
    study = [g for g in gaps if not g["is_practice"]]
    kept = [g for g in study if g["next_utterance"]]
    print(f"{len(gaps)} gaps ({len(study)} study, {len(gaps) - len(study)} practice); "
          f"{len(kept)} study gaps followed by another utterance in the same scene")
    print("children:", ", ".join(sorted({g["child"] for g in study})))
    by_source = Counter((g["source"], g["condition"]) for g in kept)
    print("kept study gaps by source and condition:", dict(by_source))

    print("\nSanity check against Section 6.3 (paper value in brackets)")
    for cond in ("animation", "control"):
        n = len(rep["stories"][cond])
        mine = [g for g in kept if g["condition"] == cond]
        judged = [g for g in mine if g["llm_resolved"] != ""]
        res = sum(g["llm_resolved"] for g in judged)
        p = PAPER[cond]
        print(f"  {cond}: {n} stories [{p['stories']}] | {rep['cycles'][cond] / max(n, 1):.1f} cycles per story "
              f"[{p['cycles']}] | {len(mine)} kept gaps [{p['kept']}] | {res} resolved by the pipeline "
              f"({100 * res / max(len(judged), 1):.0f}%) [{p['resolved']}, {100 * p['resolved'] / p['kept']:.0f}%]")
    anim_all = [g for g in study if g["condition"] == "animation"]
    n = max(len(rep["stories"]["animation"]), 1)
    interrupted = sum(g["interrupted"] for g in anim_all)
    print(f"  animations per story: {(len(anim_all) - interrupted) / n:.1f} displayed [11.9], "
          f"{interrupted / n:.1f} interrupted [8.7]")

    print("\nPairing of gaps and judgments")
    print(f"  judgments made in the first turn of a scene (on the last gap of the previous scene, "
          f"never used): {dict(rep['cross_scene'])}")
    print(f"  gaps followed by an utterance that the pipeline did not assess (left out of gaps.csv "
          f"unless practice): {rep['not_assessed']}")
    print(f"  gaps without text: {rep['no_text']}")
    print(f"  gaps left out because their utterance is empty in the CSV (emptied in the review page): {rep['emptied']}")
    if rep["mixed_condition"]:
        print(f"  stories with cycles in both conditions: {sorted(rep['mixed_condition'])}")

    print("\nInterruptions (animation condition, study stories)")
    kept_anim = [g for g in kept if g["condition"] == "animation"]
    ptt = sum(g["interrupted"] for g in kept_anim)
    print(f"  kept gaps: {ptt} of {len(kept_anim)} interrupted by a new push-to-talk press "
          f"({100 * ptt / max(len(kept_anim), 1):.0f}%), 0 by Next Scene (those have no next utterance)")
    dropped = [g for g in anim_all if not g["next_utterance"]]
    print(f"  gaps not kept (last of their scene): {len(dropped)}, of which "
          f"{sum(g['interrupted'] for g in dropped)} logged as not displayed; "
          f"{rep['flushed_next_scene'][False]} were still pending when Next Scene was clicked")

    on_screen = [g for g in kept if ON_SCREEN.search(g["gap"])]
    print(f"\nRationales that mention what happens on screen: {len(on_screen)} of {len(kept)} kept gaps "
          f"({dict(Counter(g['condition'] for g in on_screen))})")
    for g in on_screen:
        print(f"   [{g['condition']}] {g['gap'][:150]}")


def main() -> None:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", type=Path, help="merged session CSV (merge_session_logs.py)")
    ap.add_argument("--out", type=Path, default=Path("gaps.csv"))
    args = ap.parse_args()
    gaps, rep = build(args.csv)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(gaps)
    report(gaps, rep)
    print(f"\n-> {args.out}")


if __name__ == "__main__":
    main()
