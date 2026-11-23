#!/usr/bin/env python3
"""Merge the child-study session logs into one CSV (one row per logged event).

Inputs: Google Sheet exports (timestamp, participant_id, data_type, json_data)
as .csv, .xlsx or .numbers (.xlsx needs openpyxl, .numbers needs
numbers-parser). Both Sheet formats are read, through
fix_displayed_flag.load_logs:
  - wave 1 (March 2026): study_log / training_log rows holding a whole log
  - wave 2 (Sept. 2026): study_scene / training_scene rows, latest batch per
    (participant, scene_key)
Duplicate rows across files are harmless (latest snapshot wins).

animation_played.displayed is repaired with fix_displayed_flag.fix_log for
logs recorded before 2026-11-03 (later logs are left as they are):
`displayed_logged` keeps the raw value, `displayed` the repaired one,
`displayed_fix` the verdict.

The `source` column says "session" for a participant and "simulation" for a
simulated narrator (ids starting with SIM-, see scripts/simulation).

Story condition comes from the pipeline_cycle events of that story:
condition=animation -> "condition", condition=control -> "baseline".

--rename FILE:OLD=NEW renames a participant read from one file (e.g. a P2 in
Tellimations_Data_6participants.csv that is not the P2 of the other exports).

--participants 1,2,SIM-* keeps only those ids; --no-timestamps leaves the
timestamp column out (rows stay in recording order).

Usage:
    python3 -m scripts.analysis.merge_session_logs "session transcripts"/* \\
        --rename "Tellimations_Data_6participants.csv:2=PX" \\
        --out "session transcripts/tellimations_sessions.csv"
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List

from scripts.analysis.fix_displayed_flag import fix_log, load_logs

HEADER = ["timestamp", "participant_id", "data_type", "json_data"]

# Logs recorded from this day on have a reliable `displayed` (server fix d7190b8)
DISPLAYED_FIX_DATE = "2026-11-03"

COLUMNS = [
    "source", "wave", "participant_id", "phase", "story", "story_order", "story_role",
    "story_condition", "scene", "turn", "event_index", "event", "timestamp",
    "text", "animation_id", "targets", "rationale", "pass_type", "misl_element",
    "displayed", "displayed_logged", "displayed_fix", "resolved",
    "condition", "action", "selected", "suggestion", "items", "errors_found",
    "mention_counts", "deterministic_selection", "answers",
]
JSON_FIELDS = {"targets", "selected", "suggestion", "items", "errors_found",
               "mention_counts", "deterministic_selection", "answers"}


def _sheet_rows(path: Path) -> List[List[Any]]:
    """Read the first sheet of a .xlsx, .numbers or CSV file as a list of rows."""
    if path.suffix == ".xlsx":
        import openpyxl
        ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    if path.suffix == ".numbers":
        import numbers_parser
        return [list(r) for r in numbers_parser.Document(str(path)).sheets[0].tables[0].rows(values_only=True)]
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def _pid(v: Any) -> str:
    """Sheets/Excel turn '10' into 10.0."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def parse_renames(specs: List[str]) -> Dict[str, Dict[str, str]]:
    """["file.csv:2=PX,3=PY"] -> {"file.csv": {"2": "PX", "3": "PY"}}."""
    out: Dict[str, Dict[str, str]] = {}
    for spec in specs:
        name, _, pairs = spec.rpartition(":")
        for pair in pairs.split(","):
            old, _, new = pair.partition("=")
            out.setdefault(Path(name).name, {})[old.strip()] = new.strip()
    return out


def to_csv(paths: List[Path], tmp: Path, renames: Dict[str, Dict[str, str]]) -> List[Path]:
    """Normalise every input into a Sheet-format CSV for load_logs."""
    out = []
    for i, path in enumerate(paths):
        rename = renames.get(path.name, {})
        rows = _sheet_rows(path)
        if not rows or [str(c).strip() if c else "" for c in rows[0][:4]] != HEADER:
            print(f"skip {path.name}: not a Tellimations log export", file=sys.stderr)
            continue
        dst = tmp / f"{i}.csv"
        with open(dst, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            for r in rows[1:]:
                r = (r + [None] * 4)[:4]
                if r[1] is None or not r[3]:
                    continue
                pid = _pid(r[1])
                w.writerow([r[0], rename.get(pid, pid), r[2], r[3]])
        out.append(dst)
    return out


def questionnaires(paths: List[Path]) -> Dict[str, Dict[str, Any]]:
    """Map participant id to the questionnaire answers found in the given CSV exports."""
    q = {}
    for p in paths:
        with open(p, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row["data_type"] == "questionnaire":
                    q[row["participant_id"]] = json.loads(row["json_data"])
    return q


def _source(pid: str) -> str:
    """Simulated narrators (scripts/simulation) are never mixed up with sessions."""
    return "simulation" if pid.startswith("SIM-") else "session"


def _wave(timestamp: str, pid: str = "") -> str:
    """Wave 1 ran in March 2026, wave 2 from August 2026; simulations belong to none."""
    if not timestamp or _source(pid) == "simulation":
        return ""
    return "1" if timestamp < "2026-06" else "2"


def rows_for(pid: str, phase: str, log: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten one participant's study or training log into CSV rows, stories in chronological order."""
    scenes = log["scenes"]
    by_story: Dict[str, List] = {}
    for key, entries in scenes.items():
        story, _, num = key.rpartition("_scene_")
        by_story.setdefault(story, []).append((int(num), entries))

    def first_ts(story):
        """Earliest timestamp logged for a story ('' if none)."""
        return min((e["timestamp"] for _, es in by_story[story] for e in es if "timestamp" in e), default="")

    order = sorted(by_story, key=first_ts)
    wave = _wave(min((first_ts(s) for s in order if first_ts(s)), default=""), pid)
    out = []
    for rank, story in enumerate(order, 1):
        conds = {e.get("condition") for _, es in by_story[story] for e in es if e.get("event") == "pipeline_cycle"}
        conds.discard(None)
        cond = "/".join(sorted(conds))
        role = "practice" if phase == "training" else {"animation": "condition", "control": "baseline"}.get(cond, "")
        for num, entries in sorted(by_story[story]):
            turn = 0
            for idx, e in enumerate(entries):
                if e.get("event") == "transcription":
                    turn += 1
                row = {
                    "source": _source(pid), "wave": wave, "participant_id": pid, "phase": phase, "story": story,
                    "story_order": rank if phase == "study" else "", "story_role": role,
                    "story_condition": cond, "scene": num, "turn": turn or "",
                    "event_index": idx,
                }
                for k, v in e.items():
                    if k in COLUMNS and k not in row:
                        row[k] = json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and v is not None else v
                out.append(row)
    return out


def main(argv: List[str] | None = None) -> None:
    """Command-line entry point: merge the session logs into one CSV, with optional renaming and filtering of participants."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rename", action="append", default=[], metavar="FILE:OLD=NEW[,OLD=NEW]",
                    help="rename participant ids read from one input file (matched by file name)")
    ap.add_argument("--participants", default="", metavar="ID[,ID]",
                    help="keep only these participant ids, after renaming (wildcards allowed, e.g. 1,2,SIM-*)")
    ap.add_argument("--no-timestamps", action="store_true", help="leave the timestamp column out")
    args = ap.parse_args(argv)
    keep = [p.strip() for p in args.participants.split(",") if p.strip()]
    columns = [c for c in COLUMNS if not (args.no_timestamps and c == "timestamp")]

    with tempfile.TemporaryDirectory() as tmp:
        csvs = to_csv(args.inputs, Path(tmp), parse_renames(args.rename))
        logs = load_logs(csvs)
        quest = questionnaires(csvs)

    rows: List[Dict[str, Any]] = []
    key = lambda p: (not p.isdigit(), int(p) if p.isdigit() else 0, p)
    for pid in sorted(set(logs) | set(quest), key=key):
        if keep and not any(fnmatch(pid, pattern) for pattern in keep):
            continue
        for phase in ("training", "study"):
            if phase in logs.get(pid, {}):
                log = logs[pid][phase]
                stamps = [e["timestamp"] for es in log["scenes"].values() for e in es if "timestamp" in e]
                if stamps and min(stamps) < DISPLAYED_FIX_DATE:
                    fix_log(pid, phase, log)
                rows += rows_for(pid, phase, log)
        if pid in quest:
            rows.append({"source": _source(pid), "wave": _wave(quest[pid].get("timestamp") or "", pid), "participant_id": pid, "phase": "questionnaire",
                         "event": "questionnaire", "timestamp": quest[pid].get("timestamp"),
                         "answers": json.dumps(quest[pid].get("answers"), ensure_ascii=False)})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows, participants {sorted({r['participant_id'] for r in rows}, key=key)} -> {args.out}")


if __name__ == "__main__":
    main()
