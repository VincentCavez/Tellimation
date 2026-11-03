"""Readable PDF analysis of a participant's study logs (home page download).

Turns training_log.json / study_log.json into: an overview, how often each
MISL element was produced, and a per-scene transcript where each utterance is
followed by the feedback the system chose (correction, suggestion, animation)
and whether the next utterance addressed it. Deterministic, no LLM call.
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from config.misl import MACROSTRUCTURE, MICROSTRUCTURE
from src.persistence import load_study_log, participant_exists

_ROOT = Path(__file__).resolve().parent.parent.parent
_GRAMMAR_DIR = _ROOT / "animations" / "grammar"
_STORIES_PATH = _ROOT / "config" / "study_stories.json"

_RED = colors.HexColor("#DB0038")
_BLUE = colors.HexColor("#519DD9")
_GREEN = colors.HexColor("#27ae60")
_GREY = colors.HexColor("#666666")
_BEIGE = colors.HexColor("#f3ebe2")


# ---------------------------------------------------------------------------
# Label lookups
# ---------------------------------------------------------------------------

def _misl_labels() -> Dict[str, str]:
    """MISL code -> readable name, e.g. "ENP" -> "Elaborated Noun Phrases"."""
    labels: Dict[str, str] = {}
    for spec in list(MACROSTRUCTURE.values()) + list(MICROSTRUCTURE.values()):
        m = re.match(r"(.+?)\s*\((\w+)\)", spec.get("label", ""))
        if m:
            labels[m.group(2)] = m.group(1)
    return labels


def _macro_codes() -> List[str]:
    codes = []
    for spec in MACROSTRUCTURE.values():
        m = re.search(r"\((\w+)\)", spec.get("label", ""))
        if m:
            codes.append(m.group(1))
    return codes


def _animation_names() -> Dict[str, str]:
    """Grammar id -> display name, e.g. "A1" -> "Motion Lines"."""
    names: Dict[str, str] = {}
    for path in _GRAMMAR_DIR.glob("*.json"):
        try:
            spec = json.loads(path.read_text())
            names[spec["id"]] = spec.get("name", spec["id"])
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    return names


def _story_names() -> Dict[str, str]:
    """Story key -> title, e.g. "A" -> "The Balloon Seller", "training_1" -> "Tutorial"."""
    try:
        cfg = json.loads(_STORIES_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    names = {k: v.get("name", k) for k, v in cfg.items() if isinstance(v, dict)}
    for idx, entry in enumerate(cfg.get("training", []), start=1):
        names[f"training_{idx}"] = entry.get("name", f"Practice {idx}")
    return names


def _anim_label(anim_id: Optional[str], names: Dict[str, str]) -> str:
    if not anim_id:
        return "an animation"
    return names.get(anim_id.split("_", 1)[0], anim_id.replace("_", " "))


# ---------------------------------------------------------------------------
# Log -> structured scenes
# ---------------------------------------------------------------------------

def _split_scene_key(key: str) -> tuple:
    """"A_scene_3" -> ("A", 3); "training_scene_2" -> ("training", 2)."""
    m = re.match(r"(.+)_scene_(\d+)$", key)
    return (m.group(1), int(m.group(2))) if m else (key, 0)


def _build_turns(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group one scene's events into turns, one per transcribed utterance.

    Order written by the study handler for each utterance: animation_played
    (the previous turn's animation, flushed at the start of this cycle),
    transcription, resolution (did this utterance answer the previous turn's
    feedback?), corrections, suggestions, pipeline_cycle. A scene change also
    flushes the last turn's animation_played after its events.
    """
    turns: List[Dict[str, Any]] = []
    for e in entries:
        ev = e.get("event")
        if ev == "transcription":
            turns.append({"text": e.get("text", ""), "time": e.get("timestamp", "")})
        elif not turns:
            continue
        elif ev == "animation_played":
            turns[-1]["displayed"] = e.get("displayed", True)
        elif ev == "resolution":
            if len(turns) >= 2:
                turns[-2]["resolved"] = e.get("resolved")
        elif ev == "corrections":
            turns[-1]["corrections"] = e.get("items", [])
        elif ev == "suggestions":
            turns[-1]["suggestions"] = e.get("items", [])
        elif ev == "pipeline_cycle":
            turns[-1]["action"] = e.get("action")
            turns[-1]["selected"] = e.get("selected") or {}
            turns[-1]["mention_counts"] = e.get("mention_counts") or {}
    return turns


def _collect_scenes(participant_id: str) -> List[Dict[str, Any]]:
    """All scenes with at least one utterance, practices first."""
    scenes: List[Dict[str, Any]] = []
    if not participant_exists(participant_id):
        return scenes  # load_study_log would create the directory
    for is_training in (True, False):
        log = load_study_log(participant_id, is_training)
        for key, entries in log.get("scenes", {}).items():
            story, num = _split_scene_key(key)
            turns = _build_turns(entries)
            loads = [e for e in entries if e.get("event") == "scene_loaded"]
            scenes.append({
                "story": story,
                "scene": num,
                "training": is_training,
                "turns": turns,
                "started": loads[0].get("timestamp", "") if loads else "",
            })
    scenes = [s for s in scenes if s["turns"]]
    scenes.sort(key=lambda s: (not s["training"], s["started"]))
    return scenes


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

def _styles() -> Dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], textColor=_RED, fontSize=22, spaceAfter=4),
        "subtitle": ParagraphStyle("st", parent=base["Normal"], textColor=_GREY, alignment=1, spaceAfter=14),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=15, textColor=_BLUE, spaceBefore=12, spaceAfter=6, keepWithNext=1),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4, keepWithNext=1),
        "body": ParagraphStyle("b", parent=base["Normal"], fontSize=10, leading=14),
        "said": ParagraphStyle("s", parent=base["Normal"], fontSize=10.5, leading=14, leftIndent=8),
        "fb": ParagraphStyle("f", parent=base["Normal"], fontSize=9, leading=12, leftIndent=22, textColor=colors.HexColor("#333333")),
        "note": ParagraphStyle("n", parent=base["Normal"], fontSize=8.5, textColor=_GREY, leading=11),
    }


def _table(rows: List[List[Any]], widths: List[float]) -> Table:
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _BEIGE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _feedback_lines(turn: Dict[str, Any], anim_names: Dict[str, str]) -> List[str]:
    """Plain-language description of what the system did after an utterance.

    At most one animation per sentence: a correction always wins over an idea,
    so when both exist the idea is listed but marked as not animated.
    """
    action = turn.get("action")
    sel = turn.get("selected") or {}
    source = sel.get("source")
    animated = action in ("triggered", "control_suppressed")

    lines = []
    for c in turn.get("corrections", []):
        lines.append(f"<font color='#DB0038'><b>Correction:</b></font> {escape(c.get('rationale') or '')}")
    for s in turn.get("suggestions", []):
        line = f"<font color='#519DD9'><b>Idea to enrich the story:</b></font> {escape(s.get('rationale') or '')}"
        if animated and source == "error":
            line += " <font color='#666666'>(not animated: the correction came first)</font>"
        lines.append(line)

    targets = ", ".join(sel.get("targets") or []) or "the scene"
    name = _anim_label(sel.get("animation_id"), anim_names)
    reason = "the correction" if source == "error" else "the idea"
    if action == "triggered":
        line = f"<b>Animation shown for {reason}:</b> {escape(name)} on {escape(targets)}"
        if turn.get("displayed") is False:
            line += " <font color='#666666'>(you spoke again before it appeared)</font>"
        resolved = turn.get("resolved")
        if resolved is True:
            line += " &nbsp;<font color='#27ae60'><b>-> addressed in the next sentence</b></font>"
        elif resolved is False:
            line += " &nbsp;<font color='#666666'>-> not addressed in the next sentence</font>"
        lines.append(line)
    elif action == "control_suppressed":
        lines.append(f"<font color='#666666'>Animations off: {escape(name)} on {escape(targets)} "
                     f"would have been shown for {reason}.</font>")
        if turn.get("resolved") is True:
            lines.append("<font color='#27ae60'>-> addressed anyway in the next sentence</font>")
    elif action == "no_action" and not lines:
        lines.append("<font color='#27ae60'>No feedback needed.</font>")
    return lines


def build_analysis_pdf(participant_id: str) -> bytes:
    """Render the participant's analysis; works (with a short note) on empty logs."""
    st = _styles()
    misl = _misl_labels()
    macro = _macro_codes()
    anim_names = _animation_names()
    story_names = _story_names()
    scenes = _collect_scenes(participant_id)

    turns = [t for s in scenes for t in s["turns"]]
    words = sum(len(t["text"].split()) for t in turns)
    shown = [t for t in turns if t.get("action") == "triggered"]
    resolved = [t for t in shown if t.get("resolved") is True]
    judged = [t for t in shown if t.get("resolved") is not None]
    n_corr = sum(len(t.get("corrections", [])) for t in turns)
    n_sugg = sum(len(t.get("suggestions", [])) for t in turns)
    corr_turns = sum(1 for t in turns if t.get("corrections"))
    sugg_turns = sum(1 for t in turns if t.get("suggestions"))
    shown_corr = sum(1 for t in shown if (t.get("selected") or {}).get("source") == "error")

    story = [
        Paragraph("My SHIELD analysis", st["title"]),
        Paragraph(f"Generated on {datetime.now().strftime('%d %B %Y, %H:%M')}", st["subtitle"]),
    ]

    if not turns:
        story.append(Paragraph(
            "Nothing to analyse yet: play a practice or a story from the home page and "
            "tell it out loud (hold Space to talk), then download the analysis again.", st["body"]))
        return _render(story)

    # --- Overview ---
    played = []
    for s in scenes:
        key = f"training_{s['scene']}" if s["story"] == "training" else s["story"]
        name = story_names.get(key, s["story"])
        if name not in played:
            played.append(name)
    story.append(Paragraph("Overview", st["h1"]))
    # The last animation of a scene has no next sentence to judge it by
    rate = f"{len(resolved)} of {len(judged)}" if judged else "-"
    story.append(_table([
        ["Stories played", ", ".join(played)],
        ["Scenes told", str(len(scenes))],
        ["Sentences spoken", f"{len(turns)}  ({words} words, {words / len(turns):.1f} per sentence)"],
        ["Corrections (wrong or unclear)", f"{n_corr}, in {corr_turns} sentences"],
        ["Ideas to enrich the story", f"{n_sugg}, in {sugg_turns} sentences"],
        ["Animations shown", f"{len(shown)}: {shown_corr} for a correction, {len(shown) - shown_corr} for an idea"],
        ["Animations addressed right after", rate],
    ], [6 * cm, 11 * cm]))

    # --- MISL coverage: last count of each scene (counts are per scene), summed ---
    totals: Dict[str, int] = {code: 0 for code in misl}
    for s in scenes:
        counts = next((t["mention_counts"] for t in reversed(s["turns"]) if t.get("mention_counts")), {})
        for code, n in counts.items():
            totals[code] = totals.get(code, 0) + int(n or 0)
    story.append(Paragraph("What the stories contained", st["h1"]))
    story.append(Paragraph(
        "How many times each narrative element (MISL rubric) was detected in what was said. "
        "<i>Story grammar</i> elements build the plot; <i>language</i> elements make sentences richer.",
        st["body"]))
    story.append(Spacer(1, 6))
    rows = [["Element", "Type", "Times"]]
    for code in sorted(totals, key=lambda c: (c not in macro, -totals[c], misl.get(c, c))):
        kind = "Story grammar" if code in macro else "Language"
        rows.append([f"{misl.get(code, code)} ({code})", kind, str(totals[code])])
    t = _table(rows, [8 * cm, 4 * cm, 2.5 * cm])
    for i, row in enumerate(rows[1:], start=1):
        if row[2] == "0":
            t.setStyle(TableStyle([("TEXTCOLOR", (0, i), (-1, i), _GREY)]))
    story.append(t)
    missing = [misl.get(c, c) for c in macro if totals.get(c, 0) == 0]
    if missing:
        story.append(Spacer(1, 4))
        story.append(Paragraph("Never mentioned: " + escape(", ".join(missing)) + ".", st["note"]))

    # --- Scene by scene ---
    story.append(Paragraph("Scene by scene", st["h1"]))
    for s in scenes:
        if s["story"] == "training":
            title = story_names.get(f"training_{s['scene']}", f"Practice {s['scene']}")
        else:
            title = f"{story_names.get(s['story'], s['story'])} - scene {s['scene']}"
        block = [Paragraph(escape(title), st["h2"])]
        for i, turn in enumerate(s["turns"], start=1):
            said = [Paragraph(f"<b>{i}.</b> “{escape(turn['text'])}”", st["said"])]
            said += [Paragraph(line, st["fb"]) for line in _feedback_lines(turn, anim_names)]
            said.append(Spacer(1, 3))
            block.append(KeepTogether(said))
        story += block

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "At most one animation follows each sentence; when there is both a correction and an idea, "
        "the correction is animated. “Addressed” means the sentence that followed an animation "
        "was judged to answer it (the last animation of a scene has no next sentence). "
        "Corrections and ideas are the system's automatic reading of each sentence and can be wrong.",
        st["note"]))
    return _render(story)


def _render(flowables: List[Any]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, title="My SHIELD analysis",
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    )
    doc.build(flowables)
    return buf.getvalue()
