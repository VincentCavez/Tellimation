#!/usr/bin/env python3
"""Read the sessions of a merged CSV scene by scene, edit the sentences, save back to the CSV.

A small local page (127.0.0.1 only): one session and one story at a time, each
scene with its picture and the recordings made on it. A sentence can be edited
in place; "Enregistrer" writes the `text` column of the CSV.

What saving does:
  - only the `text` of `transcription` rows changes; `source`, the pipeline's
    events and every other column are left as they are (the pipeline's analysis
    was made on the original sentence and is not recomputed);
  - the first save of a run copies the CSV to <name>.backup-<date>.csv;
  - every change is appended to <name>.edits.jsonl (who, where, old, new, when).

Usage:
    python3 -m scripts.analysis.review_sessions \\
        [--csv "session transcripts/sessions_reelles_et_simulees_v6.csv"] [--port 8770]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import tempfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO / "session transcripts" / "sessions_reelles_et_simulees_v6.csv"
IMAGES = REPO / "data" / "study_gen"
SCENES = REPO / "data" / "study_scenes"
PROFILES = REPO / "scripts" / "simulation" / "profiles.json"


class Conflict(Exception):
    pass


def read_csv(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    """Read a CSV file: returns its rows and column names."""
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def titles() -> Dict[str, Dict[str, Any]]:
    """Story letter -> {title, scenes: [scene titles]} from the story definitions."""
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(SCENES.glob("story_*.json")):
        try:
            story = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        letter = path.stem.split("_")[1].upper()
        out[letter] = {"title": story.get("title", ""), "scenes": [s.get("title", "") for s in story.get("scenes", [])]}
    return out


def people(csv_path: Path) -> Dict[str, Dict[str, Any]]:
    """Age and dyslexia status per id: simulated narrators from profiles.json, participants from
    participants.json next to the CSV ({"1": {"age": 7, "diagnosis": "dis"}}; that folder is not in the repo)."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for prof in json.loads(PROFILES.read_text())["profiles"]:
            out[prof["id"]] = {"age": prof.get("age"), "diagnosis": prof.get("diagnosis")}
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    try:
        for pid, info in json.loads((csv_path.parent / "participants.json").read_text()).items():
            out[str(pid)] = {"age": info.get("age"), "diagnosis": info.get("diagnosis")}
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return out


def _json(value: str) -> Any:
    """Parse a JSON string; None when empty or invalid."""
    try:
        return json.loads(value) if value else None
    except json.JSONDecodeError:
        return None


def build(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Sessions -> stories -> scenes -> recordings, each recording with what the pipeline did next."""
    sessions: Dict[str, Dict[str, Any]] = {}
    current: Optional[Dict[str, Any]] = None
    where: Optional[tuple] = None
    for i, r in enumerate(rows):
        if r["phase"] not in ("training", "study"):
            continue
        pid = r["participant_id"]
        session = sessions.setdefault(pid, {"id": pid, "source": r["source"], "wave": r["wave"], "stories": {}})
        key = f'{r["phase"]}:{r["story"]}'
        story = session["stories"].setdefault(key, {
            "key": key, "phase": r["phase"], "story": r["story"],
            "order": int(r["story_order"]) if r["story_order"] else 0,
            "condition": r["story_condition"], "scenes": {}})
        scene = story["scenes"].setdefault(r["scene"], {"scene": r["scene"], "turns": []})
        here = (pid, key, r["scene"])
        if here != where:
            current, where = None, here
        event = r["event"]
        if event == "transcription":
            current = {"row": i, "turn": r["turn"], "text": r["text"]}
            scene["turns"].append(current)
        elif current is None:
            continue
        elif event == "resolution":
            current["resolution"] = {"animation": r["animation_id"], "pass": r["pass_type"], "resolved": r["resolved"] == "True"}
        elif event == "pipeline_cycle":
            selected = _json(r["selected"]) or {}
            errors = _json(r["errors_found"]) or []
            current["cycle"] = {"condition": r["condition"], "source": selected.get("source"),
                                "animation": selected.get("animation_id"), "targets": selected.get("targets") or [],
                                "errors": [{"animation": e.get("animation_id"), "targets": e.get("targets") or [],
                                            "desc": e.get("desc", "")} for e in errors]}
        elif event == "animation_played":
            current["animation"] = {"animation": r["animation_id"], "targets": _json(r["targets"]) or [],
                                    "rationale": r["rationale"], "pass": r["pass_type"], "displayed": r["displayed"] == "True"}
    out = []
    order = lambda p: (not p.isdigit(), int(p) if p.isdigit() else 0, p)
    for pid in sorted(sessions, key=order):
        s = sessions[pid]
        stories = sorted(s["stories"].values(), key=lambda x: (x["phase"] != "training", x["order"], x["story"]))
        for story in stories:
            story["scenes"] = sorted(story["scenes"].values(), key=lambda x: int(x["scene"]) if x["scene"].isdigit() else 0)
        out.append({**s, "stories": stories})
    return out


def save(path: Path, edits: List[Dict[str, Any]], state: Dict[str, Any]) -> int:
    """Apply the edited sentences to the CSV (after a backup on first save) and return how many changed; raises Conflict if the file changed on disk."""
    rows, columns = read_csv(path)
    changes = []
    for e in edits:
        i, new = int(e["row"]), " ".join(str(e["text"]).split())
        if not 0 <= i < len(rows) or rows[i]["event"] != "transcription":
            raise Conflict(f"la ligne {i} n'est pas une phrase")
        if rows[i]["text"] != e["old"]:
            raise Conflict("le CSV a changé sur le disque depuis l'ouverture de la page : rechargez-la")
        if new != rows[i]["text"]:
            changes.append((i, rows[i]["text"], new))
    if not changes:
        return 0
    if not state.get("backup"):
        backup = path.with_name(f"{path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}.csv")
        shutil.copy2(path, backup)
        state["backup"] = str(backup)
    stamp = datetime.now().isoformat(timespec="seconds")
    with open(path.with_name(path.stem + ".edits.jsonl"), "a", encoding="utf-8") as log:
        for i, old, new in changes:
            r = rows[i]
            log.write(json.dumps({"time": stamp, "participant_id": r["participant_id"], "source": r["source"],
                                  "phase": r["phase"], "story": r["story"], "scene": r["scene"], "turn": r["turn"],
                                  "old": old, "new": new}, ensure_ascii=False) + "\n")
            r["text"] = new
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)
    return len(changes)


def picture(story: str, scene: str) -> Optional[Path]:
    """Path of the HD picture of a scene, or None if missing or outside the images folder."""
    folder = IMAGES / ("training_1" if story == "training" else story)
    path = folder / "hd" / f"scene_{scene}_full.png"
    return path if path.is_file() and IMAGES in path.resolve().parents else None


PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relecture des sessions</title>
<style>
:root {
  --bg: #f6f5f2; --panel: #ffffff; --ink: #1d1c1a; --muted: #6b6860; --line: #e2dfd8;
  --accent: #2f5fd0; --accent-ink: #ffffff; --changed: #c77700; --changed-bg: #fff6e3;
  --real: #1f7a4d; --sim: #7a3fb0; --ok: #1f7a4d; --no: #a33b2a;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #171715; --panel: #201f1d; --ink: #ece9e2; --muted: #a09c92; --line: #34322e;
          --accent: #7ea2ff; --accent-ink: #10131c; --changed: #f0b24a; --changed-bg: #33290f;
          --real: #6fcf9b; --sim: #c79bf0; --ok: #6fcf9b; --no: #f08a78; }
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
.app { display: grid; grid-template-columns: 250px 1fr; height: 100vh; }
aside { border-right: 1px solid var(--line); background: var(--panel); overflow: auto; padding: 14px 10px; }
aside h1 { font-size: 15px; margin: 2px 8px 2px; }
aside .file { font-size: 12px; color: var(--muted); margin: 0 8px 12px; word-break: break-all; }
aside h2 { font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin: 14px 8px 6px; }
.session { display: flex; align-items: center; gap: 8px; width: 100%; border: 0; background: none; color: inherit;
           font: inherit; text-align: left; padding: 7px 8px; border-radius: 7px; cursor: pointer; }
.session:hover { background: var(--bg); }
.session[aria-current="true"] { background: var(--accent); color: var(--accent-ink); }
.session .who { display: flex; flex-direction: column; min-width: 0; }
.session .who small { font-size: 11.5px; opacity: .75; }
.session .n { margin-left: auto; white-space: nowrap; font-size: 12px; opacity: .7; font-variant-numeric: tabular-nums; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--changed); visibility: hidden; flex: none; }
.dot.on { visibility: visible; }
main { display: flex; flex-direction: column; min-width: 0; height: 100vh; }
header { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 18px; background: var(--panel);
         border-bottom: 1px solid var(--line); }
.title { font-weight: 600; }
.profile { font-size: 13px; color: var(--muted); margin-right: 6px; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 99px; border: 1px solid currentColor; }
.badge.session-real { color: var(--real); } .badge.session-sim { color: var(--sim); }
.tabs { display: flex; flex-wrap: wrap; gap: 6px; }
.tab { border: 1px solid var(--line); background: none; color: inherit; font: inherit; padding: 5px 11px; border-radius: 7px; cursor: pointer; }
.tab small { color: var(--muted); }
.tab[aria-current="true"] { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
.tab[aria-current="true"] small { color: inherit; }
.spacer { flex: 1; }
.pending { font-size: 13px; color: var(--changed); }
button.save { border: 0; background: var(--accent); color: var(--accent-ink); font: inherit; font-weight: 600;
              padding: 7px 14px; border-radius: 7px; cursor: pointer; }
button.save:disabled { opacity: .4; cursor: default; }
label.toggle { font-size: 13px; color: var(--muted); display: flex; align-items: center; gap: 6px; cursor: pointer; }
#scenes { overflow: auto; padding: 18px; flex: 1; }
.scene { display: grid; grid-template-columns: minmax(240px, 420px) 1fr; gap: 18px; background: var(--panel);
         border: 1px solid var(--line); border-radius: 12px; padding: 14px; margin: 0 auto 16px; max-width: 1180px; }
.scene figure { margin: 0; position: sticky; top: 0; align-self: start; }
.scene img { width: 100%; height: auto; border-radius: 8px; display: block; border: 1px solid var(--line); background: var(--bg); }
.scene figcaption { font-size: 13px; color: var(--muted); margin-top: 6px; }
.scene figcaption b { color: var(--ink); }
.turn { display: grid; grid-template-columns: 26px 1fr; gap: 8px; padding: 8px 0; border-top: 1px solid var(--line); }
.turn:first-child { border-top: 0; padding-top: 0; }
.turn .num { color: var(--muted); font-size: 13px; padding-top: 7px; text-align: right; font-variant-numeric: tabular-nums; }
textarea { width: 100%; resize: none; overflow: hidden; border: 1px solid transparent; border-left: 3px solid transparent;
           background: transparent; color: inherit; font: inherit; padding: 6px 8px; border-radius: 6px; display: block; }
textarea:hover { border-color: var(--line); border-left-color: var(--line); }
textarea:focus { outline: none; border-color: var(--accent); border-left-color: var(--accent); background: var(--bg); }
textarea.changed { border-left-color: var(--changed); background: var(--changed-bg); }
.was { font-size: 12px; color: var(--muted); margin: 3px 0 0 11px; }
.was button { border: 0; background: none; color: var(--accent); font: inherit; cursor: pointer; padding: 0 0 0 6px; }
.info { font-size: 12.5px; color: var(--muted); margin: 3px 0 0 11px; }
.info .ok { color: var(--ok); } .info .no { color: var(--no); }
.hide-info .info { display: none; }
.empty { color: var(--muted); font-size: 13px; padding: 6px 0; }
#toast { position: fixed; right: 18px; bottom: 18px; background: var(--ink); color: var(--bg); padding: 10px 14px;
         border-radius: 8px; font-size: 14px; opacity: 0; transition: opacity .2s; pointer-events: none; max-width: 420px; }
#toast.on { opacity: 1; }
@media (max-width: 860px) {
  .app { grid-template-columns: 1fr; height: auto; }
  aside { border-right: 0; border-bottom: 1px solid var(--line); max-height: 220px; }
  main { height: auto; } #scenes { overflow: visible; }
  .scene { grid-template-columns: 1fr; } .scene figure { position: static; }
}
</style>
</head>
<body>
<div class="app">
  <aside>
    <h1>Relecture des sessions</h1>
    <p class="file" id="file"></p>
    <div id="sessions"></div>
  </aside>
  <main>
    <header>
      <span class="title" id="title"></span>
      <span class="profile" id="profile"></span>
      <span class="badge" id="badge"></span>
      <div class="tabs" id="tabs"></div>
      <span class="spacer"></span>
      <label class="toggle"><input type="checkbox" id="showInfo" checked> pipeline</label>
      <span class="pending" id="pending"></span>
      <button class="save" id="save" disabled>Enregistrer</button>
    </header>
    <div id="scenes"></div>
  </main>
</div>
<div id="toast" role="status"></div>
<script>
const state = { data: null, session: null, story: null, edits: new Map(), original: new Map(), owner: new Map() };
// Shorthand for getElementById.
const $ = (id) => document.getElementById(id);
// Create an element with an optional class and text.
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

// Show a short notification message.
function toast(message) {
  const t = $("toast"); t.textContent = message; t.classList.add("on");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("on"), 3500);
}
// Display name of a session.
function sessionName(s) { return s.source === "simulation" ? s.id.replace("SIM-", "") : "P" + s.id; }
// Age and diagnosis of a child as text (short or long form).
function profileOf(s, short) {
  const parts = [];
  if (s.age) parts.push(s.age + " ans");
  if (s.diagnosis === "dis") parts.push(short ? "diagnostiquée" : "dyslexie diagnostiquée");
  else if (s.diagnosis === "self") parts.push(short ? "déclarée" : "dyslexie déclarée par la famille");
  return parts.join(" · ");
}
// Title and subtitle of a story for display.
function storyLabel(st) {
  if (st.phase === "training") return ["Pratique", ""];
  return ["Histoire " + st.story, st.condition === "animation" ? "avec animations" : st.condition === "control" ? "sans animations" : ""];
}
// Resize a textarea to fit its content.
function fit(t) { t.style.height = "auto"; t.style.height = t.scrollHeight + "px"; }

// Fetch the session data and render the page.
async function load() {
  const res = await fetch("/api/data"); state.data = await res.json();
  $("file").textContent = state.data.file;
  state.original.clear(); state.owner.clear();
  for (const s of state.data.sessions) for (const st of s.stories) for (const sc of st.scenes) for (const t of sc.turns) {
    state.original.set(t.row, t.text); state.owner.set(t.row, s.id);
  }
  const [sid, key] = decodeURIComponent(location.hash.slice(1)).split("/");
  const s = state.data.sessions.find((x) => x.id === sid) || state.data.sessions[0];
  select(s.id, key);
}

// Render the list of sessions, marking those with unsaved edits.
function renderSessions() {
  const box = $("sessions"); box.textContent = "";
  const dirty = new Set([...state.edits.keys()].map((row) => state.owner.get(row)));
  for (const [source, title] of [["session", "Sessions réelles"], ["simulation", "Sessions simulées"]]) {
    const list = state.data.sessions.filter((s) => s.source === source);
    if (!list.length) continue;
    box.append(el("h2", null, title));
    for (const s of list) {
      const b = el("button", "session"); b.type = "button";
      b.setAttribute("aria-current", s.id === state.session ? "true" : "false");
      const dot = el("span", "dot" + (dirty.has(s.id) ? " on" : "")); dot.title = "modifications non enregistrées";
      const n = s.stories.reduce((a, st) => a + st.scenes.reduce((b2, sc) => b2 + sc.turns.length, 0), 0);
      const who = el("span", "who"); who.append(el("span", null, sessionName(s)));
      const p = profileOf(s, true); if (p) who.append(el("small", null, p));
      b.title = sessionName(s) + (profileOf(s) ? " — " + profileOf(s) : "");
      b.append(dot, who, el("span", "n", n + " phrases"));
      b.onclick = () => select(s.id);
      box.append(b);
    }
  }
}

// Select a session and story, update the URL hash and render.
function select(sid, key) {
  const s = state.data.sessions.find((x) => x.id === sid);
  state.session = sid;
  state.story = (s.stories.find((st) => st.key === key) || s.stories.find((st) => st.phase === "study") || s.stories[0]).key;
  history.replaceState(null, "", "#" + encodeURIComponent(sid + "/" + state.story));
  render();
}

// Info lines under a sentence: how the previous animation was judged and what comes next.
function infoLines(t) {
  const lines = [];
  if (t.resolution) {
    const line = el("div", "info");
    line.append("Animation précédente (" + (t.resolution.animation || "?") + ") : ");
    line.append(el("span", t.resolution.resolved ? "ok" : "no", t.resolution.resolved ? "jugée résolue par cette phrase" : "jugée non résolue"));
    lines.push(line);
  }
  const c = t.cycle, a = t.animation;
  if (a) {
    const kind = a.pass === "correction" ? "Correction" : "Suggestion";
    const shown = a.displayed ? "" : " — animation interrompue, non vue";
    lines.push(el("div", "info", "Ensuite : " + kind + " " + a.animation + " sur " + (a.targets.join(", ") || "la scène") + shown + ". " + (a.rationale || "")));
  } else if (c && c.animation) {
    const kind = c.source === "error" ? "Correction" : "Suggestion";
    const why = c.condition === "control" ? " — histoire sans animations, rien n'est montré" : "";
    const desc = c.source === "error" && c.errors.length ? " " + c.errors[0].desc : "";
    lines.push(el("div", "info", "Ensuite : " + kind + " " + c.animation + " sur " + (c.targets.join(", ") || "la scène") + why + "." + desc));
  }
  return lines;
}

// Render the selected story with its scenes and editable sentences.
function render() {
  renderSessions();
  const s = state.data.sessions.find((x) => x.id === state.session);
  const st = s.stories.find((x) => x.key === state.story);
  $("title").textContent = sessionName(s);
  $("profile").textContent = profileOf(s);
  const badge = $("badge"); badge.textContent = s.source === "simulation" ? "simulation" : "session réelle";
  badge.className = "badge " + (s.source === "simulation" ? "session-sim" : "session-real");
  const tabs = $("tabs"); tabs.textContent = "";
  for (const x of s.stories) {
    const [name, cond] = storyLabel(x);
    const b = el("button", "tab"); b.type = "button"; b.setAttribute("aria-current", x.key === st.key ? "true" : "false");
    b.append(name); if (cond) { b.append(" "); b.append(el("small", null, cond)); }
    b.onclick = () => select(s.id, x.key);
    tabs.append(b);
  }
  const box = $("scenes"); box.textContent = ""; box.scrollTop = 0;
  const info = (state.data.titles[st.story] || {});
  for (const sc of st.scenes) {
    const card = el("section", "scene");
    const fig = el("figure");
    const img = el("img"); img.loading = "lazy"; img.alt = "Scène " + sc.scene;
    img.src = "/img/" + encodeURIComponent(st.story) + "/" + encodeURIComponent(sc.scene);
    img.onerror = () => { img.remove(); };
    const cap = el("figcaption"); cap.append(el("b", null, (st.phase === "training" ? "Pratique" : "Scène " + sc.scene)));
    const sceneTitle = (info.scenes || [])[Number(sc.scene) - 1];
    if (st.phase !== "training" && sceneTitle) cap.append(" — " + sceneTitle);
    fig.append(img, cap);
    const list = el("div");
    if (!sc.turns.length) list.append(el("div", "empty", "Aucun enregistrement sur cette scène."));
    for (const t of sc.turns) {
      const row = el("div", "turn");
      row.append(el("div", "num", t.turn));
      const body = el("div");
      const ta = el("textarea"); ta.rows = 1; ta.spellcheck = false; ta.dataset.row = t.row;
      ta.value = state.edits.has(t.row) ? state.edits.get(t.row) : state.original.get(t.row);
      ta.setAttribute("aria-label", "Phrase " + t.turn + ", scène " + sc.scene);
      const was = el("div", "was");
      // Mark the sentence as modified or not and refresh the edit state.
      const mark = () => {
        const changed = state.edits.has(t.row);
        ta.classList.toggle("changed", changed);
        was.textContent = "";
        if (changed) {
          was.append("Avant : " + (state.original.get(t.row) || "(vide)"));
          const undo = el("button", null, "annuler"); undo.type = "button";
          undo.onclick = () => { state.edits.delete(t.row); ta.value = state.original.get(t.row); fit(ta); mark(); status(); };
          was.append(undo);
        }
      };
      ta.addEventListener("input", () => {
        const value = ta.value.replace(/\s*\n\s*/g, " ");
        if (value !== ta.value) ta.value = value;
        if (value === state.original.get(t.row)) state.edits.delete(t.row); else state.edits.set(t.row, value);
        fit(ta); mark(); status();
      });
      body.append(ta, was, ...infoLines(t));
      row.append(body); list.append(row);
      mark();
    }
    card.append(fig, list); box.append(card);
  }
  box.querySelectorAll("textarea").forEach(fit);
  status();
}

// Update the pending-edits counter and the save button.
function status() {
  const n = state.edits.size;
  $("pending").textContent = n ? n + (n > 1 ? " phrases modifiées" : " phrase modifiée") : "";
  $("save").disabled = !n;
  renderSessions();
}

// Send the edited sentences to the server and refresh the state.
async function save() {
  if (!state.edits.size) return;
  const edits = [...state.edits].map(([row, text]) => ({ row, old: state.original.get(row), text }));
  $("save").disabled = true;
  try {
    const res = await fetch("/api/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ edits }) });
    const out = await res.json();
    if (!res.ok) throw new Error(out.error || "échec de l'enregistrement");
    for (const [row, text] of state.edits) state.original.set(row, text.replace(/\s+/g, " ").trim());
    state.edits.clear();
    toast(out.saved + (out.saved > 1 ? " phrases enregistrées" : " phrase enregistrée") + " dans le CSV." + (out.backup ? " Copie de l'ancien fichier : " + out.backup : ""));
    render();
  } catch (err) {
    toast("Non enregistré : " + err.message); status();
  }
}

$("save").onclick = save;
$("showInfo").onchange = (e) => $("scenes").classList.toggle("hide-info", !e.target.checked);
window.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(); } });
window.addEventListener("beforeunload", (e) => { if (state.edits.size) { e.preventDefault(); e.returnValue = ""; } });
window.addEventListener("resize", () => document.querySelectorAll("textarea").forEach(fit));
load().catch((err) => { $("scenes").textContent = "Impossible de charger le CSV : " + err.message; });
</script>
</body>
</html>
"""


def make_handler(csv_path: Path):
    """Build the HTTP request handler class serving the review page and API for `csv_path`."""
    state: Dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, kind: str, cache: bool = False) -> None:
            """Send a response with the given status, content type and body; images may be cached, other responses are not."""
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: Any) -> None:
            """Send a JSON response."""
            self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            """Serve the page, the session data, the scene pictures and the list of titles."""
            path = self.path.split("?")[0]
            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/data":
                rows, _ = read_csv(csv_path)
                who = people(csv_path)
                sessions = [{**s, **who.get(s["id"], {})} for s in build(rows)]
                self._json(200, {"file": csv_path.name, "sessions": sessions, "titles": titles()})
            elif path.startswith("/img/"):
                parts = path.split("/")
                image = picture(parts[2], parts[3]) if len(parts) == 4 and parts[3].isdigit() and parts[2].isalnum() else None
                if image is None:
                    self._send(404, b"", "text/plain")
                else:
                    self._send(200, image.read_bytes(), "image/png", cache=True)
            else:
                self._send(404, b"", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            """Handle /api/save: apply the posted edits and reply with the number saved (409 on conflict)."""
            if self.path != "/api/save":
                self._send(404, b"", "text/plain")
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                first = not state.get("backup")
                saved = save(csv_path, body.get("edits") or [], state)
                self._json(200, {"saved": saved, "backup": Path(state["backup"]).name if first and state.get("backup") else None})
            except Conflict as exc:
                self._json(409, {"error": str(exc)})
            except (ValueError, KeyError, OSError) as exc:
                self._json(400, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, *args: Any) -> None:  # keep the terminal quiet
            """Silence the default request logging."""
            pass

    return Handler


def main() -> None:
    """Command-line entry point: serve the review page on localhost until interrupted."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--port", type=int, default=8770)
    args = ap.parse_args()
    csv_path = args.csv.resolve()
    if not csv_path.is_file():
        raise SystemExit(f"CSV introuvable : {csv_path}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(csv_path))
    print(f"Relecture de {csv_path.name} sur http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
