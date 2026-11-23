#!/usr/bin/env python3
"""Do simulated sessions give the pipeline the same kind of input as real ones?

Input: the CSV written by merge_session_logs.py (real sessions and simulated
narrators, column `source`). Study stories only. No model call: everything is
read from the logs.

For each child, two families of measures:
  - what the pipeline receives (the transcripts): how much is said, how it is
    cut, repeated, punctuated, how often a second speaker is heard;
  - what the pipeline does with it: errors flagged and their categories,
    correction or suggestion, animations interrupted, resolutions.

A simulated child's value is starred when it falls outside the range of the
real children (below the lowest or above the highest of them). With four real
children that range is wide, so a star means "no real child looks like this".

Usage:
    python3 -m scripts.analysis.compare_real_simulated "session transcripts/sessions_reelles_et_simulees_v4.csv" \\
        [--simulated SIM-N5,SIM-N6,SIM-N7,SIM-N8]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable, Dict, List

CATEGORIES = {"I": "Identity", "C": "Count", "P": "Property", "A": "Action",
              "S": "Space", "T": "Time", "R": "Relation", "D": "Discourse"}


def words(text: str) -> List[str]:
    """Lower-case word tokens of a text."""
    return re.findall(r"[a-z']+", text.lower())


def pct(n: float, d: float) -> float:
    """Percentage n/d, or NaN when d is zero."""
    return 100.0 * n / d if d else float("nan")


def mattr(tokens: List[str], window: int = 50) -> float:
    """Moving-average type/token ratio: does not depend on how much the child said."""
    if len(tokens) < window:
        return float("nan")
    return mean(len(set(tokens[i:i + window])) / window for i in range(len(tokens) - window + 1))


FUNCTION = set("""a an the this that these those there here it its he she they them him her his their we you i me my
is are was were be been being am do does did have has had will would can could should may might must
and or but so because if then than as while when of in on at by to from for with into onto up down out off over under
near not no yes okay oh um uh yeah just very really too also still now again what who where why how which one two""".split())


def stem(word: str) -> str:
    """Crude suffix stripper used to match words between transcripts and scene descriptions."""
    for end in ("'s", "ing", "ed", "es", "s"):
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[:-len(end)]
    return word


def load_descriptions(folder: Path) -> Dict[tuple, set]:
    """(story letter, scene number) -> stems of the written scene description and of the entity names."""
    out: Dict[tuple, set] = {}
    for path in sorted(folder.glob("story_*.json")):
        story = json.loads(path.read_text())
        letter = path.stem.split("_")[1].upper()
        for i, scene in enumerate(story.get("scenes", []), 1):
            text = " ".join([scene.get("scene_description", "")] + list(scene.get("entities_in_scene", [])))
            out[letter, str(i)] = {stem(w) for w in words(text)}
    return out


def measures(rows: List[Dict[str, str]], descriptions: Dict[tuple, set]) -> Dict[str, float]:
    """Compute the comparison measures for one participant's rows (study phase only), given the scene description words per scene."""
    rows = [r for r in rows if r["phase"] == "study"]
    scenes: Dict[tuple, List[str]] = defaultdict(list)      # (story, scene) -> transcripts in order
    cond: Dict[tuple, str] = {}
    cycles, anims, res = [], [], []
    for r in rows:
        key = (r["story"], r["scene"])
        cond[key] = r["story_condition"]
        if r["event"] == "transcription":
            scenes[key].append(r["text"])
        elif r["event"] == "pipeline_cycle":
            cycles.append(r)
        elif r["event"] == "animation_played":
            anims.append(r)
        elif r["event"] == "resolution" and r["turn"] and int(r["turn"]) >= 2:
            res.append(r)

    texts = [t for ts in scenes.values() for t in ts]
    lens = [len(words(t)) for t in texts]
    pairs = [(a, b) for ts in scenes.values() for a, b in zip(ts, ts[1:])]
    long_pairs = [(a, b) for a, b in pairs if len(words(a)) >= 3 and len(words(b)) >= 3]
    near = sum(SequenceMatcher(None, words(a), words(b)).ratio() >= 0.6 for a, b in long_pairs)
    n = len(texts)

    def per_scene(c: str) -> float:
        """Mean number of transcribed sentences per scene in condition `c`."""
        counts = [len(ts) for k, ts in scenes.items() if cond.get(k) == c]
        return mean(counts) if counts else float("nan")

    out: Dict[str, float] = {
        "recordings": n,
        "recordings / scene, animations on": per_scene("animation"),
        "recordings / scene, animations off": per_scene("control"),
        "words / recording (median)": median(lens),
        "% recordings of 1-2 words": pct(sum(x <= 2 for x in lens), n),
        "% recordings of 20+ words": pct(sum(x >= 20 for x in lens), n),
        "% with 2+ sentences": pct(sum(len(re.findall(r"[.!?]\s+\S", t)) >= 1 for t in texts), n),
        "% with a question (second speaker)": pct(sum("?" in t for t in texts), n),
        "% without final punctuation": pct(sum(not t.rstrip().endswith((".", "!", "?")) for t in texts), n),
        "% starting in lower case": pct(sum(t[:1].islower() for t in texts), n),
        "% with a word said twice in a row": pct(sum(bool(re.search(r"\b(\w+)\s+\1\b", t, re.I)) for t in texts), n),
        "% near-repeat of the previous recording": pct(near, len(long_pairs)),
        "% exact repeat of the previous recording": pct(sum(a.strip() == b.strip() for a, b in pairs), len(pairs)),
        "vocabulary variety (MATTR-50)": mattr([w for t in texts for w in words(t)]),
    }
    if descriptions:
        # Content words the child says about a picture: how many are in the written description of that picture?
        inside = total = 0
        for key, ts in scenes.items():
            known = descriptions.get(key)
            if not known:
                continue
            content = [stem(w) for t in ts for w in words(t) if w not in FUNCTION and len(w) > 2]
            inside += sum(w in known for w in content)
            total += len(content)
        out["% content words found in the scene description"] = pct(inside, total)

    errors = [json.loads(c["errors_found"]) if c["errors_found"] else [] for c in cycles]
    flat = [e for es in errors for e in es]
    out["% recordings with an error flagged"] = pct(sum(bool(es) for es in errors), len(cycles))
    out["errors flagged / recording"] = len(flat) / len(cycles) if cycles else float("nan")
    cats = Counter((e.get("animation_id") or "?")[:1] for e in flat)
    for k, name in CATEGORIES.items():
        out[f"% of errors: {name}"] = pct(cats[k], len(flat))
    sel = [json.loads(c["selected"]) if c["selected"] else {} for c in cycles]
    out["% cycles ending in a correction"] = pct(sum(s.get("source") == "error" for s in sel), len(sel))
    sugg = [s for s in sel if s.get("source") == "suggestion"]
    out["% of suggestions that are I2 (name)"] = pct(sum((s.get("animation_id") or "").startswith("I2") for s in sugg), len(sugg))
    out["% of suggestions that are I1 (setting)"] = pct(sum((s.get("animation_id") or "").startswith("I1") for s in sugg), len(sugg))
    out["% animations interrupted"] = pct(sum(a["displayed"] != "True" for a in anims), len(anims))
    for c, label in (("animation", "on"), ("control", "off")):
        rs = [r for r in res if r["story_condition"] == c]
        out[f"% resolved, animations {label}"] = pct(sum(r["resolved"] == "True" for r in rs), len(rs))
    stories: Dict[str, set] = defaultdict(set)
    for c in cycles:
        counts = json.loads(c["mention_counts"]) if c["mention_counts"] else {}
        stories[c["story"]] |= {k for k, v in counts.items() if v}
    out["MISL elements / story"] = mean(len(v) for v in stories.values()) if stories else float("nan")
    return out


def fmt(v: float) -> str:
    """Format a number for the comparison table ('-' for NaN)."""
    if v != v:
        return "-"
    return f"{v:.2f}" if abs(v) < 2 and v != int(v) else f"{v:.0f}" if abs(v) >= 10 or v == int(v) else f"{v:.1f}"


def main() -> None:
    """Command-line entry point: print real versus simulated measures side by side."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--simulated", default="", help="simulated ids to show (default: all)")
    ap.add_argument("--scenes", type=Path, default=Path("data/study_scenes"),
                    help="folder of the story definitions (written scene descriptions)")
    args = ap.parse_args()
    with open(args.csv, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    by: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by[r["participant_id"]].append(r)
    order: Callable[[str], Any] = lambda p: (not p.isdigit(), int(p) if p.isdigit() else 0, p)
    real = sorted((p for p in by if by[p][0]["source"] == "session"), key=order)
    keep = [p.strip() for p in args.simulated.split(",") if p.strip()]
    sims = sorted(p for p in by if by[p][0]["source"] == "simulation" and (not keep or p in keep))
    descriptions = load_descriptions(args.scenes) if args.scenes.is_dir() else {}
    m = {p: measures(by[p], descriptions) for p in real + sims}

    head = ["measure"] + [f"P{p}" for p in real] + ["real range"] + [p.replace("SIM-", "") for p in sims] + ["outside"]
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * len(head))
    off: Counter = Counter()
    for name in m[real[0]]:
        vals = [m[p][name] for p in real if m[p][name] == m[p][name]]
        lo, hi = (min(vals), max(vals)) if vals else (float("nan"), float("nan"))
        cells, stars = [], 0
        for p in sims:
            v = m[p][name]
            out = v == v and vals and (v < lo - 1e-9 or v > hi + 1e-9)
            stars += bool(out)
            off[p] += bool(out)
            cells.append(fmt(v) + ("*" if out else ""))
        print(f"| {name} | " + " | ".join(fmt(m[p][name]) for p in real) + f" | {fmt(lo)}-{fmt(hi)} | "
              + " | ".join(cells) + f" | {stars}/{len(sims)} |")
    print("\nmeasures outside the real range, per simulated child:", dict(off), f"(of {len(m[real[0]])})")


if __name__ == "__main__":
    main()
