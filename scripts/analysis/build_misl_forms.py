#!/usr/bin/env python3
"""Prepare the content of the three MISL scoring forms (one Google Form per rater).

Input: a merged session CSV (merge_session_logs.py). Every study story that has
a condition and at least one recording becomes one story to score. Each story
is given to two of the three raters; the three pairs of raters get the same
number of stories (to within one), the same mix of conditions, and each
child's stories are spread over the pairs.

Output folder (default: "session transcripts/misl_forms"):
  stories.json   what the Apps Script (scripts/analysis/misl_forms.gs) reads to
                 build the forms: per rater, the stories in the order shown,
                 each with a neutral code and the recordings of its five scenes.
                 No participant id, no condition.
  key.csv        code -> participant, story, condition, source, raters.
                 Stays on this machine; needed to analyse the scores.
  images/        the 20 scene illustrations, reduced (A_1.jpg ... D_5.jpg).

To change the transcripts (another CSV): run this again with the new CSV,
replace stories.json in the Drive folder, run buildAll() in the Apps Script.
The forms keep their links.

Usage:
    python3 -m scripts.analysis.build_misl_forms "session transcripts/sessions_reelles_et_simulees_v6.csv"
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

PAIRS = [(1, 2), (1, 3), (2, 3)]
SCENES = ["1", "2", "3", "4", "5"]


def load_stories(csv_path: Path) -> List[Dict]:
    """Read the CSV and return one record per (participant, story) with its transcribed sentences grouped by scene; study-phase animation/control stories only."""
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if r["phase"] == "study" and r["story_condition"] in ("animation", "control")]
    by: Dict[tuple, Dict] = {}
    for r in rows:
        key = (r["participant_id"], r["story"])
        s = by.setdefault(key, {"participant": key[0], "story": key[1], "condition": r["story_condition"],
                                "source": r["source"], "scenes": defaultdict(list)})
        if r["event"] == "transcription" and r["text"].strip():
            s["scenes"][r["scene"]].append(r["text"].strip())
    return [s for s in by.values() if any(s["scenes"].values())]


def assign(stories: List[Dict], seed: int, trials: int = 4000) -> None:
    """Adds 'code' and 'raters' to each story.

    A child's stories go to different pairs of raters as far as possible (a
    child with four stories uses the three pairs, one of them twice), so that
    no rater scores more than three stories of one child. Among random draws
    that respect this, the one kept has the most even numbers of stories per
    pair, of conditions per rater and of story (A-D) per rater.
    """
    rng = random.Random(seed)
    codes = [f"S{i:02d}" for i in range(1, len(stories) + 1)]
    rng.shuffle(codes)
    order = lambda p: (not p.isdigit(), int(p) if p.isdigit() else 0, p)
    stories.sort(key=lambda s: (s["condition"], order(s["participant"]), s["story"]))
    for i, s in enumerate(stories):
        s["code"] = codes[i]

    children: Dict[str, List[Dict]] = defaultdict(list)
    for s in stories:
        children[s["participant"]].append(s)

    def spread(counter: Counter, keys) -> int:
        """Return the gap between the largest and smallest count over `keys`."""
        values = [counter[k] for k in keys]
        return max(values) - min(values)

    best, best_score = None, None
    draw = random.Random(seed + 1000)
    for _ in range(trials):
        plan = {}
        for mine in children.values():
            pairs = PAIRS[:]
            draw.shuffle(pairs)
            pairs = (pairs + [draw.choice(PAIRS) for _ in mine])[:len(mine)]
            draw.shuffle(pairs)
            for s, p in zip(mine, pairs):
                plan[s["code"]] = p
        per_pair = Counter(plan.values())
        cond, letter, load = Counter(), Counter(), Counter()
        for s in stories:
            for r in plan[s["code"]]:
                cond[r, s["condition"]] += 1
                letter[r, s["story"]] += 1
                load[r] += 1
        score = (spread(per_pair, PAIRS), spread(load, (1, 2, 3)),
                 sum(abs(cond[r, "animation"] - cond[r, "control"]) for r in (1, 2, 3)),
                 sum(spread(letter, [(r, x) for x in "ABCD"]) for r in (1, 2, 3)))
        if best_score is None or score < best_score:
            best, best_score = plan, score
    for s in stories:
        s["raters"] = best[s["code"]]


def arrange(mine: List[Dict], seed: int, trials: int = 20000) -> List[Dict]:
    """Random order in which the stories of one child are as far apart as possible, never back to back."""
    rng = random.Random(seed)
    best, best_gap = None, -1
    for _ in range(trials):
        order = mine[:]
        rng.shuffle(order)
        last: Dict[str, int] = {}
        gap = len(order)
        for i, s in enumerate(order):
            if s["participant"] in last:
                gap = min(gap, i - last[s["participant"]])
            last[s["participant"]] = i
        if gap > best_gap:
            best, best_gap = order, gap
    if best_gap < 2:
        raise SystemExit("could not separate the stories of one child")
    return best


def images(out: Path, source: Path, width: int) -> None:
    """Convert the scene pictures of stories A-D to JPEG of the given width into `out` (uses macOS `sips`); existing files are kept."""
    out.mkdir(parents=True, exist_ok=True)
    for story in "ABCD":
        for n in SCENES:
            src = source / story / "hd" / f"scene_{n}_full.png"
            dst = out / f"{story}_{n}.jpg"
            if not dst.exists():
                subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
                                "--resampleWidth", str(width), str(src), "--out", str(dst)],
                               check=True, capture_output=True)


def checksum(scenes: List[List[str]]) -> int:
    """Same value as check() in misl_forms.gs for the transcripts of one story."""
    h = 7
    for lines in scenes:
        text = ("\n".join(lines) if lines else "(nothing recorded for this scene)") + "|"
        data = text.encode("utf-16-le")
        for i in range(0, len(data), 2):
            h = (h * 31 + int.from_bytes(data[i:i + 2], "little")) % 1000000007
    return h


def print_check(path: Path) -> None:
    """One line per rater, to compare with the log of check() in the Apps Script."""
    raters = json.loads(path.read_text(encoding="utf-8"))["raters"]
    for rater, mine in raters.items():
        print(f"rater {rater} | {7 + 7 + 16 * len(mine)} items | "
              + " ".join(f"{m['code']}:{checksum(m['scenes'])}" for m in mine))


def main() -> None:
    """Command-line entry point: build the form data, or only print the balance check with --check."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--check", action="store_true",
                    help="only print the expected output of check() for the stories.json already in --out")
    ap.add_argument("--out", type=Path, default=Path("session transcripts/misl_forms"))
    ap.add_argument("--images", type=Path, default=Path("data/study_gen"))
    ap.add_argument("--width", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=2027)
    args = ap.parse_args()
    if args.check:
        print_check(args.out / "stories.json")
        return

    stories = load_stories(args.csv)
    assign(stories, args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    images(args.out / "images", args.images, args.width)

    raters: Dict[str, List[Dict]] = {}
    for rater in (1, 2, 3):
        mine = arrange([s for s in stories if rater in s["raters"]], args.seed + rater)
        raters[str(rater)] =[{"code": s["code"], "story": s["story"],
                               "scenes": [s["scenes"].get(n, []) for n in SCENES]} for s in mine]
    (args.out / "stories.json").write_text(json.dumps({"raters": raters}, ensure_ascii=False, indent=1), encoding="utf-8")

    with open(args.out / "key.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "participant_id", "story", "condition", "source", "rater_a", "rater_b", "recordings", "words"])
        for s in sorted(stories, key=lambda s: s["code"]):
            texts = [t for n in SCENES for t in s["scenes"].get(n, [])]
            w.writerow([s["code"], s["participant"], s["story"], s["condition"], s["source"], *s["raters"],
                        len(texts), sum(len(t.split()) for t in texts)])

    print(f"{len(stories)} stories -> {args.out}")
    for rater, mine in raters.items():
        keys = {s["code"]: s for s in stories}
        cond = Counter(keys[m["code"]]["condition"] for m in mine)
        print(f"rater {rater}: {len(mine)} stories | {dict(cond)} | by story {dict(Counter(m['story'] for m in mine))} "
              f"| children {len({keys[m['code']]['participant'] for m in mine})}")
        kids = [keys[m["code"]]["participant"] for m in mine]
        gaps = [j - i for i in range(len(kids)) for j in range(i + 1, len(kids)) if kids[i] == kids[j]]
        print(f"   stories of one child: max {max(Counter(kids).values())} | closest two of the same child: {min(gaps)} apart")
    print("pairs:", dict(Counter(s["raters"] for s in stories)))


if __name__ == "__main__":
    main()
