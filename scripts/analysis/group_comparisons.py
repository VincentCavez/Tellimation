#!/usr/bin/env python3
"""Diagnosed vs self-reported, age, and baseline vs condition, from a merged session CSV.

Per child and per condition (study stories only): recordings and words per
story, words per recording, MISL elements per story, errors flagged per
recording, and the share of assessed gaps judged resolved (paper section 6.3:
a gap is assessed when another recording follows in the same scene).

Real sessions and simulated narrators are ALWAYS reported apart. For simulated
narrators the group differences are largely the settings of their profiles
(scripts/simulation/profiles.json): length, number of recordings with and
without animations, how often an animation is taken up. They describe the
simulator, not children.

Ages and dyslexia status come from review_sessions.people(): profiles.json for
simulated narrators, participants.json next to the CSV for participants.

Usage:
    python3 -m scripts.analysis.group_comparisons "session transcripts/sessions_reelles_et_simulees_v6.csv"
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional

from scripts.analysis.review_sessions import people

CONDITIONS = {"animation": "condition", "control": "baseline"}
MEASURES = ["recordings/story", "words/story", "words/recording", "MISL/story", "errors/recording", "resolved %", "corrections resolved %"]


def words(text: str) -> int:
    """Number of words in a text."""
    return len(re.findall(r"[A-Za-z']+", text))


def child_measures(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    """condition ('condition' / 'baseline' / 'all') -> measures for one child."""
    out: Dict[str, Dict[str, Any]] = {}
    for label, keep in (("condition", {"animation"}), ("baseline", {"control"}), ("all", {"animation", "control"})):
        rs = [r for r in rows if r["phase"] == "study" and r["story_condition"] in keep]
        stories = {r["story"] for r in rs}
        if not stories:
            continue
        tx = [r for r in rs if r["event"] == "transcription"]
        cycles = [r for r in rs if r["event"] == "pipeline_cycle"]
        misl: Dict[str, set] = defaultdict(set)
        for c in cycles:
            counts = json.loads(c["mention_counts"]) if c["mention_counts"] else {}
            misl[c["story"]] |= {k for k, v in counts.items() if v}
        errors = sum(len(json.loads(c["errors_found"]) if c["errors_found"] else []) for c in cycles)
        res = [r for r in rs if r["event"] == "resolution" and r["turn"] and int(r["turn"]) >= 2]
        corr = [r for r in res if r["pass_type"] == "correction"]
        pct = lambda xs: 100 * sum(x["resolved"] == "True" for x in xs) / len(xs) if xs else float("nan")
        total = sum(words(r["text"]) for r in tx)
        out[label] = {
            "stories": len(stories),
            "recordings/story": len(tx) / len(stories),
            "words/story": total / len(stories),
            "words/recording": total / len(tx) if tx else float("nan"),
            "MISL/story": mean(len(misl[s]) for s in stories),
            "errors/recording": errors / len(cycles) if cycles else float("nan"),
            "resolved %": pct(res), "resolved n": len(res),
            "corrections resolved %": pct(corr), "corrections n": len(corr),
        }
    return out


def fmt(v: Any) -> str:
    """Format a number for the tables ('-' for missing or NaN)."""
    if v is None or v != v:
        return "-"
    return f"{v:.2f}" if abs(v) < 3 else f"{v:.0f}" if abs(v) >= 20 else f"{v:.1f}"


def avg(values: List[float]) -> float:
    """Mean ignoring NaN values; NaN when nothing is left."""
    values = [v for v in values if v == v]
    return mean(values) if values else float("nan")


def spearman(xs: List[float], ys: List[float]) -> Optional[str]:
    """Spearman correlation of paired values as a text, or None when there are too few or constant pairs."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x == x and y == y]
    if len(pairs) < 4 or len({x for x, _ in pairs}) < 2:
        return None
    from scipy.stats import spearmanr
    r = spearmanr([x for x, _ in pairs], [y for _, y in pairs])
    return f"rho = {r.statistic:+.2f}, p = {r.pvalue:.2f}, n = {len(pairs)}"


def wilcoxon(a: List[float], b: List[float]) -> Optional[str]:
    """Wilcoxon signed-rank test of paired values as a text, or None when there are too few or identical pairs."""
    pairs = [(x, y) for x, y in zip(a, b) if x == x and y == y]
    if len(pairs) < 3 or all(x == y for x, y in pairs):
        return None
    from scipy.stats import wilcoxon as test
    w = test([x for x, _ in pairs], [y for _, y in pairs])
    return f"W = {w.statistic:g}, p = {w.pvalue:.3f}, n = {len(pairs)}, higher with animations for {sum(x > y for x, y in pairs)}"


def main() -> None:
    """Command-line entry point: print the comparisons between children and between conditions for the CSV."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", type=Path)
    args = ap.parse_args()
    with open(args.csv, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    who = people(args.csv.resolve())
    by: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by[r["participant_id"]].append(r)
    order = lambda p: (not p.isdigit(), int(p) if p.isdigit() else 0, p)
    children = []
    for pid in sorted(by, key=order):
        m = child_measures(by[pid])
        if "all" in m:
            info = who.get(pid, {})
            children.append({"id": pid, "source": by[pid][0]["source"], "age": info.get("age"), "diagnosis": info.get("diagnosis"), "m": m})

    print("## Per child, all study stories")
    print("| child | source | age | dyslexia | " + " | ".join(MEASURES) + " |")
    print("|" + "---|" * (4 + len(MEASURES)))
    for c in children:
        a = c["m"]["all"]
        print(f"| {c['id']} | {c['source']} | {c['age']} | {c['diagnosis']} | " + " | ".join(
            fmt(a[k]) + (f" (n={a['resolved n']})" if k == "resolved %" else f" (n={a['corrections n']})" if k == "corrections resolved %" else "")
            for k in MEASURES) + " |")

    for source in ("session", "simulation"):
        group = [c for c in children if c["source"] == source]
        if not group:
            continue
        print(f"\n## {source}: diagnosed vs self-reported")
        for d in ("dis", "self"):
            g = [c for c in group if c["diagnosis"] == d]
            print(f"- {d}: n = {len(g)}" + ("" if not g else " | " + " | ".join(f"{k} {fmt(avg([c['m']['all'][k] for c in g]))}" for k in MEASURES)))
        print(f"\n## {source}: age")
        ages = [float(c["age"]) if c["age"] else float("nan") for c in group]
        for k in MEASURES:
            print(f"- {k}: {spearman(ages, [c['m']['all'][k] for c in group]) or 'too few children'}")
        print(f"\n## {source}: condition (animations) vs baseline, children with both")
        both = [c for c in group if "condition" in c["m"] and "baseline" in c["m"]]
        print(f"children with both conditions: {[c['id'] for c in both]}")
        print("| measure | condition | baseline | paired test |")
        print("|---|---|---|---|")
        for k in MEASURES:
            a = [c["m"]["condition"][k] for c in both]
            b = [c["m"]["baseline"][k] for c in both]
            print(f"| {k} | {fmt(avg(a))} | {fmt(avg(b))} | {wilcoxon(a, b) or '-'} |")
        print("per child (condition | baseline):")
        for c in both:
            print(f"  {c['id']}: " + "; ".join(f"{k} {fmt(c['m']['condition'][k])} | {fmt(c['m']['baseline'][k])}" for k in ("words/story", "recordings/story", "MISL/story", "resolved %")))


if __name__ == "__main__":
    main()
