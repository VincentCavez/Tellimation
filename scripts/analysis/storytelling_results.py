#!/usr/bin/env python3
"""Storytelling-study measures (paper section 6.3) from a merged session CSV.

Input: the CSV written by merge_session_logs.py (timestamps not needed).
Measures, per condition (animation / control), study stories only:
  - cycles per story, split into corrections and suggestions
  - animations displayed and interrupted per story
  - distinct MISL elements per story (out of 15)
  - resolutions: a gap is "assessed" when another utterance follows in the
    same scene (a `resolution` event on turn >= 2 of the scene)
  - per-child resolution rates and a Wilcoxon signed-rank test
  - resolution by pass type, by category and by animation (animated stories)

The measures are printed for each `source` (session, simulation) and for all
rows together, so simulated narrators are never silently pooled with children.

Usage:
    python3 -m scripts.analysis.storytelling_results "session transcripts/sessions_reelles_et_simulees.csv"
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

CATEGORIES = {"I": "Identity", "C": "Count", "P": "Property", "A": "Action",
              "S": "Space", "T": "Time", "R": "Relation", "D": "Discourse"}
CONDITIONS = ("animation", "control")


def rate(n: int, d: int) -> str:
    """Format n out of d as a count with a percentage ('-' when d is zero)."""
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def base(animation_id: str) -> str:
    """I2_silhouette -> I2."""
    return (animation_id or "").split("_")[0]


def analyse(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """Compute the per-condition results (animation versus control) from the study-phase rows."""
    rows = [r for r in rows if r["phase"] == "study" and r["story_condition"] in CONDITIONS]
    stories = defaultdict(set)       # condition -> {(pid, story)}
    cycles: Counter = Counter()      # (condition, source of the selected gap)
    anims: Counter = Counter()       # (condition, displayed)
    anim_ids: Counter = Counter()
    by_child_anim: Counter = Counter()
    misl = defaultdict(set)          # (pid, story) -> elements mentioned
    res = []                         # assessed gaps
    for r in rows:
        cond, key = r["story_condition"], (r["participant_id"], r["story"])
        stories[cond].add(key)
        if r["event"] == "pipeline_cycle":
            selected = json.loads(r["selected"]) if r["selected"] else {}
            cycles[cond, {"error": "correction", "suggestion": "suggestion"}.get(selected.get("source"), "none")] += 1
            counts = json.loads(r["mention_counts"]) if r["mention_counts"] else {}
            misl[key] |= {k for k, v in counts.items() if v}
        elif r["event"] == "animation_played":
            shown = r["displayed"] == "True"
            anims[cond, shown] += 1
            anim_ids[base(r["animation_id"])] += 1
            by_child_anim[r["participant_id"], shown] += 1
        elif r["event"] == "resolution" and r["turn"] and int(r["turn"]) >= 2:
            res.append({"cond": cond, "pid": r["participant_id"], "story": r["story"], "pass": r["pass_type"],
                        "anim": base(r["animation_id"]), "ok": r["resolved"] == "True"})

    out: Dict[str, Any] = {"stories": {c: len(stories[c]) for c in CONDITIONS}}
    for c in CONDITIONS:
        n = len(stories[c]) or 1
        rs = [x for x in res if x["cond"] == c]
        out[c] = {
            "cycles/story": (cycles[c, "correction"] + cycles[c, "suggestion"] + cycles[c, "none"]) / n,
            "corrections/story": cycles[c, "correction"] / n,
            "suggestions/story": cycles[c, "suggestion"] / n,
            "cycles without gap": cycles[c, "none"],
            "displayed/story": anims[c, True] / n,
            "interrupted/story": anims[c, False] / n,
            "interrupted": rate(anims[c, False], anims[c, True] + anims[c, False]),
            "MISL/story": sum(len(misl[k]) for k in stories[c]) / n,
            "resolved": rate(sum(x["ok"] for x in rs), len(rs)),
            "resolved/story": sum(x["ok"] for x in rs) / n,
            "assessed/story": len(rs) / n,
            "corrections resolved": rate(sum(x["ok"] for x in rs if x["pass"] == "correction"), sum(x["pass"] == "correction" for x in rs)),
            "suggestions resolved": rate(sum(x["ok"] for x in rs if x["pass"] == "suggestion"), sum(x["pass"] == "suggestion" for x in rs)),
        }

    # Per-child rates, children with both conditions
    child = {}
    for pid in sorted({x["pid"] for x in res}, key=lambda p: (not p.isdigit(), int(p) if p.isdigit() else 0, p)):
        pair = []
        for c in CONDITIONS:
            rs = [x for x in res if x["pid"] == pid and x["cond"] == c]
            pair.append((sum(x["ok"] for x in rs), len(rs)))
        child[pid] = pair
    out["children"] = child
    both = {p: (a[0] / a[1], b[0] / b[1]) for p, (a, b) in child.items() if a[1] and b[1]}
    out["higher with animations"] = f"{sum(a > b for a, b in both.values())} of {len(both)}"
    if len(both) >= 2:
        from scipy.stats import wilcoxon
        w = wilcoxon([a for a, _ in both.values()], [b for _, b in both.values()])
        out["wilcoxon"] = f"W = {w.statistic:g}, p = {w.pvalue:.3f} (n = {len(both)})"

    animated = [x for x in res if x["cond"] == "animation"]
    out["by category"] = {name: rate(sum(x["ok"] for x in animated if x["anim"][:1] == k), sum(x["anim"][:1] == k for x in animated))
                          for k, name in CATEGORIES.items()}
    per_anim = Counter(x["anim"] for x in animated)
    out["by animation"] = {a: rate(sum(x["ok"] for x in animated if x["anim"] == a), n) for a, n in per_anim.most_common() if n >= 5}
    total = sum(anim_ids.values())
    out["most played"] = ", ".join(f"{a} {100 * n / total:.0f}%" for a, n in anim_ids.most_common(3)) if total else "-"
    out["interrupted by child"] = {p: rate(by_child_anim[p, False], by_child_anim[p, False] + by_child_anim[p, True])
                                   for p in child if by_child_anim[p, False] + by_child_anim[p, True]}
    return out


def show(title: str, a: Dict[str, Any]) -> None:
    """Print the results of `analyse` for one source of rows."""
    print(f"\n=== {title}: {a['stories']['animation']} animated, {a['stories']['control']} control stories ===")
    for k in a["animation"]:
        va, vc = a["animation"][k], a["control"][k]
        fmt = lambda v: f"{v:.1f}" if isinstance(v, float) else str(v)
        print(f"  {k:24s} animation {fmt(va):16s} control {fmt(vc)}")
    print("  per child (animation | control):", ", ".join(f"{p} {rate(*a_)} | {rate(*b_)}" for p, (a_, b_) in a["children"].items()))
    for k in ("higher with animations", "wilcoxon", "by category", "by animation", "most played", "interrupted by child"):
        if k in a:
            print(f"  {k}: {a[k]}")


def main() -> None:
    """Command-line entry point: print the results for each data source, then for all rows."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", type=Path)
    args = ap.parse_args()
    with open(args.csv, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for source in sorted({r["source"] for r in rows}):
        show(source, analyse([r for r in rows if r["source"] == source]))
    show("all rows", analyse(rows))


if __name__ == "__main__":
    main()
