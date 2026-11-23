#!/usr/bin/env python3
"""How did the pipeline cope with the simulated sessions?

Reads the traces written by simulate_sessions.py (what each simulated recording
contained, and what the pipeline answered) and writes:
  report.md   tables: profiles, mistakes caught or missed, false alarms,
              behaviour per kind of recording, resolution checks, failures
  turns.csv   one row per recording

"Mistakes present" are those the child model declared for what the child said:
an approximate ground truth, good for rates, not for single cases. Mis-heard
words and cut recordings are known exactly (they are added by the simulator).

Usage:
    python3 -m scripts.simulation.report [--out data/simulated_sessions]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Dict, List

from scripts.simulation.child import SLIPS
from scripts.simulation.simulate_sessions import DEFAULT_OUT, load_profiles

DISCONNECT = 'Cannot call "receive" once a disconnect message has been received'


def load_turns(out: Path) -> List[Dict[str, Any]]:
    """Load every simulated turn from the trace files and add the ground truth and pipeline flag fields."""
    turns = []
    for path in sorted((out / "trace").glob("SIM-*.jsonl")):
        turns += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for t in turns:
        errors = t["pipeline"]["errors"]
        types = {m["type"] for m in t["mistakes"]}
        t["gt_grammar"] = "grammar" in types
        t["gt_content"] = sorted(types & set(SLIPS))
        t["flag_grammar"] = "D4" in errors
        t["flag_content"] = [e for e in errors if e != "D4"]
        t["words"] = len(t["child_said"].split())
        # Adult speech as planned, or as actually written when it was not
        t["adult"] = bool(t["plan"].get("adult")) or len(t["transcript"].split()) - t["words"] >= 3
        # What the recogniser did to the recording (absent from the first run's traces)
        t["misheard"] = (t.get("misheard") or []) + [{"said": "", "heard": op} for op in t.get("asr") or [] if op in ("ending", "insert")]
        t["clipped"] = any(op.startswith(("head-", "tail-")) for op in t.get("asr") or [])
    return turns


def pct(n: int, d: int) -> str:
    """Format n out of d as a count with a percentage ('-' when d is zero)."""
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def table(header: List[str], rows: List[List[Any]]) -> str:
    """Render a Markdown table."""
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def build(out: Path) -> str:
    """Build the Markdown report on how the pipeline coped with the simulated sessions."""
    turns = load_turns(out)
    profiles = {p["id"]: p for p in load_profiles()}
    by_pid: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in turns:
        by_pid[t["participant"]].append(t)
    study = [t for t in turns if t["story"] != "training"]
    parts = ["# Simulated sessions: how the pipeline coped", "",
             "Simulated narrators, real pipeline. No child took part. "
             f"{len(turns)} recordings ({len(study)} in the four stories).", ""]

    # 1. Profiles as they came out
    rows = []
    for pid, ts in sorted(by_pid.items()):
        p = profiles.get(pid, {})
        tokens = re.findall(r"[a-z']+", " ".join(t["child_said"] for t in ts).lower())
        said = [t for t in ts if t["plan"]["kind"] not in ("fragment", "stuck")]
        rows.append([p.get("label", pid), p.get("diagnosis", ""), p.get("age", ""), " ".join(p.get("order", [])), len(ts),
                     f"{median(t['words'] for t in said):.0f}" if said else "-",
                     len(set(tokens)), f"{len(set(tokens)) / max(len(tokens), 1):.2f}",
                     pct(sum(t["gt_grammar"] for t in ts), len(ts)),
                     pct(sum(bool(t["gt_content"]) for t in ts), len(ts)),
                     pct(sum(t["adult"] for t in ts), len(ts)),
                     pct(sum(bool(t["misheard"]) for t in ts), len(ts)),
                     pct(sum(t["clipped"] for t in ts), len(ts))])
    parts += ["## 1. Simulated narrators", "",
              table(["child", "group", "age", "order", "recordings", "median words", "distinct words", "type/token",
                     "with grammar mistake", "with content mistake", "with adult speech",
                     "with a mis-heard word", "start or end cut"], rows), ""]

    # 2. Mistakes present -> caught?
    rows = []
    g = [t for t in turns if t["gt_grammar"]]
    rows.append(["grammar", len(g), pct(sum(t["flag_grammar"] for t in g), len(g)), "-",
                 pct(sum(not t["pipeline"]["errors"] for t in g), len(g))])
    for kind, spec in SLIPS.items():
        ts = [t for t in turns if kind in t["gt_content"]]
        right = sum(any(e[0] in spec["expect"] for e in t["flag_content"]) for t in ts)
        other = sum(bool(t["flag_content"]) and not any(e[0] in spec["expect"] for e in t["flag_content"]) for t in ts)
        rows.append([kind, len(ts), pct(right, len(ts)), pct(other, len(ts)),
                     pct(sum(not t["pipeline"]["errors"] for t in ts), len(ts))])
    parts += ["## 2. Mistakes present in the recording: were they caught?", "",
              table(["mistake", "recordings", "flagged in the expected category", "flagged in another category", "nothing flagged"], rows), ""]

    # 3. False alarms on clean narration
    clean = [t for t in turns if not t["mistakes"] and t["plan"]["kind"] in ("narrate", "uptake", "partial", "repeat")]
    rows = []
    alone = [t for t in clean if not t["adult"]]
    for label, ts in (("child alone, heard right and whole", [t for t in alone if not t["misheard"] and not t["clipped"]]),
                      ("child alone, a word mis-heard", [t for t in alone if t["misheard"]]),
                      ("child alone, start or end cut", [t for t in alone if t["clipped"] and not t["misheard"]]),
                      ("with adult speech", [t for t in clean if t["adult"]])):
        cats = Counter(e for t in ts for e in t["pipeline"]["errors"])
        rows.append([label, len(ts), pct(sum(bool(t["pipeline"]["errors"]) for t in ts), len(ts)),
                     ", ".join(f"{k}×{v}" for k, v in cats.most_common(6)) or "-"])
    parts += ["## 3. Recordings without any declared mistake: what was flagged anyway?", "",
              table(["recordings", "n", "flagged with an error", "errors flagged"], rows), ""]

    # 4. By kind of recording
    rows = []
    for kind in ("narrate", "uptake", "partial", "meta", "repeat", "stuck", "fragment"):
        ts = [t for t in turns if t["plan"]["kind"] == kind]
        if not ts:
            continue
        sel = Counter((t["pipeline"]["selected"] or {}).get("source") or "none" for t in ts)
        top = Counter(str((t["pipeline"]["selected"] or {}).get("animation_id") or "none").split("_")[0] for t in ts)
        rows.append([kind, len(ts), pct(sel["error"], len(ts)), pct(sel["suggestion"], len(ts)), pct(sel["no_action"] + sel["none"], len(ts)),
                     ", ".join(f"{k}×{v}" for k, v in top.most_common(4))])
    parts += ["## 4. What the pipeline chose, by kind of recording", "",
              table(["kind", "n", "correction", "suggestion", "nothing", "most chosen animations"], rows), ""]

    # 5. Resolution checks
    rows = []
    checked = [t for t in turns if t["pipeline"]["resolution"] is not None]
    for label, ts in (("animation seen, child takes it up", [t for t in checked if t["plan"]["kind"] == "uptake"]),
                      ("animation seen, child stays on the target but misses the point", [t for t in checked if t["plan"]["kind"] == "partial"]),
                      ("animation seen, child talks about the effect", [t for t in checked if t["plan"]["kind"] == "meta"]),
                      ("animation seen, child moves on", [t for t in checked if t["reacting_to"] and t["plan"]["kind"] == "narrate"]),
                      ("animated story, child spoke before seeing it", [t for t in checked if t["condition"] == "animation" and not t["reacting_to"]]),
                      ("baseline story (nothing shown)", [t for t in checked if t["condition"] == "control"])):
        rows.append([label, len(ts), pct(sum(bool(t["pipeline"]["resolution"]) for t in ts), len(ts))])
    parts += ["## 5. Resolution check on the following recording", "",
              "How often the child takes an animation up is set by the profile; the rows show whether the resolution check agrees.", "",
              table(["situation", "checks", "judged resolved"], rows), ""]

    # 6. Failures
    no_cycle = [t for t in turns if not t["pipeline"]["cycle_logged"]]
    no_anim = [t for t in turns if t["condition"] == "animation" and t["pipeline"]["action"] == "triggered" and not t["animation_sent"]]
    messages: Counter = Counter()
    for path in sorted((out / "server_logs").glob("SIM-*.log")):
        text = path.read_text(encoding="utf-8")
        for block in re.split(r"\n(?=\d{4}-\d\d-\d\d )", text):
            head = block.splitlines()[0] if block.strip() else ""
            if not re.search(r" (ERROR|WARNING) ", head) or DISCONNECT in block:
                continue
            last = block.strip().splitlines()[-1]
            messages[re.sub(r"^\S+ \S+ ", "", head)[:90] + (" → " + last[:110] if last != head else "")] += 1
    latency = sorted(t["latency_s"] for t in turns)
    parts += ["## 6. Failures and timing", "",
              f"- Recordings with no pipeline cycle logged: {len(no_cycle)} of {len(turns)}.",
              f"- Animated recordings where an animation was chosen but none reached the screen: {pct(len(no_anim), sum(t['condition'] == 'animation' for t in turns))}"
              + (" (targets: " + ", ".join(sorted({str((t['pipeline']['selected'] or {}).get('targets')) for t in no_anim})) + ")." if no_anim else "."),
              f"- Time from recording to end of turn: median {median(latency):.1f} s, 90th percentile {latency[int(0.9 * (len(latency) - 1))]:.1f} s, max {latency[-1]:.1f} s." if latency else "",
              "", "Server warnings and errors (page-close noise excluded):", ""]
    parts += [f"- {n}× {msg}" for msg, n in messages.most_common(12)] or ["- none"]

    # turns.csv
    with open(out / "turns.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["participant", "group", "age", "story", "scene_key", "turn", "condition", "kind", "planned_slips", "planned_grammar",
                    "adult_speech", "saw_animation", "transcript", "child_said", "child_words", "mistakes_present",
                    "errors_flagged", "suggestion", "selected_source", "selected_animation", "selected_targets", "action",
                    "resolution", "animation_sent", "spoke_before_animation", "latency_s", "recogniser", "misheard"])
        for t in turns:
            p, sel = profiles.get(t["participant"], {}), t["pipeline"]["selected"] or {}
            w.writerow([t["participant"], p.get("diagnosis", ""), p.get("age", ""), t["story"], t["scene_key"], t["turn"], t["condition"],
                        t["plan"]["kind"], " ".join(t["plan"]["slips"]), int(bool(t["plan"]["grammar"])), int(t["adult"]),
                        (t["reacting_to"] or {}).get("short", ""), t["transcript"], t["child_said"], t["words"],
                        " ".join(sorted({m["type"] for m in t["mistakes"]})), " ".join(t["pipeline"]["errors"]),
                        t["pipeline"]["suggestion"] or "", sel.get("source", ""), sel.get("animation_id") or "",
                        " ".join(sel.get("targets") or []), t["pipeline"]["action"] or "", t["pipeline"]["resolution"],
                        int(t["animation_sent"]), int(t["interrupted"]), t["latency_s"], " ".join(t.get("asr") or []),
                        "; ".join(f"{m.get('said', '')} -> {m.get('heard', '')}" for m in t["misheard"])])
    return "\n".join(parts) + "\n"


def main() -> None:
    """Command-line entry point: write and print the report."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    report = build(args.out)
    (args.out / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
