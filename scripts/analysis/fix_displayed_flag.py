#!/usr/bin/env python3
"""Repair animation_played.displayed in study logs recorded before 2026-11-03.

The bug: study_story.js released its "awaiting assessment" gate only once it
had seen BOTH a MISTAKES and an OPTIONS study_log message (flags sticky until
both arrive), but the server sends MISTAKES only when there are corrections.
While the gate was stuck, the child's next Space press sent `interrupt`, and
the server logged the pending animation as displayed=false even though it had
already played. Fixed in commit d7190b8 (server `study_turn_done`).

The client gate is replayed from the logged events (one story page = one
client state; a reloaded page resets it). Each displayed=false is classified:

  genuine          gate was released after that turn, so `interrupt` can only
                   have been sent while the server was still processing: the
                   child really spoke before the animation appeared. Kept false.
  genuine_queued   gate stuck, but the next audio was handled < QUEUE_S after
                   the cycle ended: it was waiting in the socket queue, so the
                   press came during processing. Kept false.
  bug              gate stuck and the next audio arrived later: the flag is the
                   artifact. Set to true. A press during processing followed by
                   a recording that ended after the cycle also lands here; the
                   report gives the genuine rate seen on released turns as the
                   expected residual error.
  unknown          gate stuck, no following utterance in the scene (flush at
                   scene change or on an empty transcription). Kept false.

Corrected entries keep the logged value in `displayed_logged` and the verdict
in `displayed_fix`. Input files are never modified.

Inputs: Google Sheet CSV exports (timestamp, participant_id, data_type,
json_data) in either format:
  - study_log / training_log rows holding a whole log (wave 1, March 2026)
  - study_scene / training_scene rows holding one scene (wave 2): the latest
    batch per (participant, scene_key) is kept and its parts concatenated.

Usage:
    python3 -m scripts.analysis.fix_displayed_flag export.csv [more.csv ...] \\
        [--out data/displayed_fix] [--participants 9 10 ...]

Writes <out>/logs_fixed.json ({participant: {training|study: log}}),
<out>/logs_fixed.csv (one row per participant/log, wave-1 format) and
<out>/displayed_report.csv (one row per animation_played).
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

QUEUE_S = 0.5  # next audio handled within this delay after the cycle = it was queued

csv.field_size_limit(sys.maxsize)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_logs(paths: List[Path]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{participant: {"training"|"study": {"scenes": {scene_key: [entries]}}}}."""
    whole: Dict[Tuple[str, str], Tuple[str, Dict]] = {}
    scene_rows: Dict[Tuple[str, str, str], List[Tuple[str, Dict]]] = collections.defaultdict(list)

    for path in paths:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                pid, dtype, stamp = row["participant_id"].strip(), row["data_type"].strip(), row["timestamp"]
                try:
                    data = json.loads(row["json_data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if dtype in ("study_log", "training_log"):
                    kind = dtype.split("_")[0]
                    # Latest snapshot wins
                    if (pid, kind) not in whole or stamp >= whole[(pid, kind)][0]:
                        whole[(pid, kind)] = (stamp, data)
                elif dtype in ("study_scene", "training_scene"):
                    kind = dtype.split("_")[0]
                    scene_rows[(pid, kind, data.get("scene_key", ""))].append((stamp, data))

    logs: Dict[str, Dict[str, Dict[str, Any]]] = collections.defaultdict(dict)
    for (pid, kind), (_, data) in whole.items():
        logs[pid][kind] = {"participant": pid, "type": kind, "scenes": dict(data.get("scenes", {}))}

    for (pid, kind, scene_key), rows in scene_rows.items():
        latest = max(stamp for stamp, _ in rows)
        parts = sorted((d for stamp, d in rows if stamp == latest), key=lambda d: d.get("part", 0))
        entries = [e for d in parts for e in d.get("entries", [])]
        log = logs[pid].setdefault(kind, {"participant": pid, "type": kind, "scenes": {}})
        log["scenes"][scene_key] = entries
    return logs


# ---------------------------------------------------------------------------
# Gate replay
# ---------------------------------------------------------------------------

def _story_of(scene_key: str) -> str:
    return scene_key.rsplit("_scene_", 1)[0]


def fix_log(pid: str, kind: str, log: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Correct `log` in place; return one report row per animation_played."""
    items = sorted(
        ((key, idx, e) for key, entries in log["scenes"].items() for idx, e in enumerate(entries)),
        key=lambda x: (x[2].get("timestamp", ""), x[1]),
    )
    report = []
    # Per story page: sticky flags + gate, as in the pre-fix study_story.js
    state: Dict[str, Dict[str, Any]] = collections.defaultdict(
        lambda: {"m": False, "o": False, "stuck": False, "last_scene": 0, "done": None, "stuck_after": None})

    for pos, (key, _, e) in enumerate(items):
        story, ev = _story_of(key), e.get("event")
        s = state[story]
        if ev == "scene_loaded":
            n = int(e.get("scene") or 1)
            if n <= s["last_scene"]:  # same or earlier scene again: page reloaded
                s.update(m=False, o=False, stuck=False)
            s["last_scene"] = n
        elif ev == "transcription":
            s["stuck"] = True  # awaitingAssessment = true when the audio is sent
        elif ev == "corrections":
            s["m"] = True
        elif ev == "suggestions":
            s["o"] = True
        elif ev == "pipeline_cycle":
            if s["m"] and s["o"]:
                s.update(m=False, o=False, stuck=False)
            s["done"] = _ts(e["timestamp"])
            s["stuck_after"] = s["stuck"]
        elif ev == "animation_played":
            nxt = next((x for x in items[pos + 1:] if x[0] == key and x[2].get("event") in ("transcription", "scene_loaded")), None)
            next_utt = nxt[2] if nxt and nxt[2].get("event") == "transcription" else None
            gap = (_ts(e["timestamp"]) - s["done"]).total_seconds() if s["done"] else None
            logged = e.get("displayed_logged", e.get("displayed", True))

            if logged is not False:
                verdict = "kept_true"
            elif not s["stuck_after"]:
                verdict = "genuine"
            elif next_utt is None:
                verdict = "unknown"
            elif gap is not None and gap < QUEUE_S:
                verdict = "genuine_queued"
            else:
                verdict = "bug"

            if logged is False:
                e["displayed_logged"] = False
                e["displayed_fix"] = verdict
                e["displayed"] = verdict == "bug"
            report.append({
                "participant": pid, "log": kind, "scene_key": key,
                "animation_id": e.get("animation_id"), "timestamp": e.get("timestamp"),
                "gate_after_turn": "stuck" if s["stuck_after"] else "released",
                "gap_s": round(gap, 2) if gap is not None else "",
                "next_utterance_words": len(next_utt["text"].split()) if next_utt else "",
                "displayed_logged": logged, "verdict": verdict, "displayed_fixed": e.get("displayed"),
            })
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("data/displayed_fix"))
    ap.add_argument("--participants", nargs="*", help="only these participant ids")
    args = ap.parse_args(argv)

    logs = load_logs(args.inputs)
    if args.participants:
        logs = {p: v for p, v in logs.items() if p in set(args.participants)}

    report: List[Dict[str, Any]] = []
    for pid in sorted(logs, key=lambda p: (len(p), p)):
        for kind, log in logs[pid].items():
            report += fix_log(pid, kind, log)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "logs_fixed.json").write_text(json.dumps(logs, indent=1, ensure_ascii=False), encoding="utf-8")
    with open(args.out / "logs_fixed.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["participant_id", "data_type", "json_data"])
        for pid, kinds in logs.items():
            for kind, log in kinds.items():
                w.writerow([pid, f"{kind}_log", json.dumps(log, ensure_ascii=False)])
    if report:
        with open(args.out / "displayed_report.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(report[0]))
            w.writeheader()
            w.writerows(report)

    # Summary
    by_pid = collections.defaultdict(collections.Counter)
    for r in report:
        by_pid[r["participant"]][r["verdict"]] += 1
    cols = ["kept_true", "genuine", "genuine_queued", "bug", "unknown"]
    print(f"{'participant':<12}" + "".join(f"{c:>16}" for c in cols))
    for pid, c in by_pid.items():
        print(f"{pid:<12}" + "".join(f"{c[k]:>16}" for k in cols))
    released = [r for r in report if r["gate_after_turn"] == "released" and r["next_utterance_words"] != ""]
    genuine_late = [r for r in released if r["verdict"] == "genuine" and (r["gap_s"] or 0) >= QUEUE_S]
    if released:
        print(f"\nResidual error estimate: on released turns, {len(genuine_late)}/{len(released)} "
              f"({100 * len(genuine_late) / len(released):.0f}%) were genuine interruptions not caught "
              f"by the queue rule; the same share of 'bug' verdicts may really be interruptions.")
    total_false = sum(1 for r in report if r["displayed_logged"] is False)
    fixed = sum(1 for r in report if r["verdict"] == "bug")
    print(f"displayed=false: {total_false}, corrected to true: {fixed}. Output: {args.out}/")


if __name__ == "__main__":
    main()
