#!/usr/bin/env python3
"""
Generate study1_utterances.pdf — list of all 200 utterances with scene descriptions
and expected system outputs (option T).

Usage: python3 data/gen_pdf_utterances.py
Output: data/study1_utterances.pdf
"""

import json
import os
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
STIMULI_FILE = BASE / "study1_all_stimuli.json"
SCENES_DIR = BASE / "prolific_scenes"
OUTPUT_FILE = BASE / "study1_utterances.pdf"

# ── Colors ─────────────────────────────────────────────────────────────────────
COL_SCENE_BG = colors.HexColor("#e8e8e8")
COL_CORR_BG  = colors.HexColor("#fff3f3")
COL_SUGG_BG  = colors.HexColor("#f0f4ff")
COL_DESC_FG  = colors.HexColor("#444444")
COL_LABEL    = colors.HexColor("#111111")
COL_HEADER   = colors.HexColor("#333333")
COL_PAGE_HDR = colors.HexColor("#888888")
COL_CORR_TAG = colors.HexColor("#c0392b")
COL_SUGG_TAG = colors.HexColor("#2980b9")

# ── Styles ─────────────────────────────────────────────────────────────────────
def make_styles():
    s = {}
    base = dict(fontName="Helvetica", alignment=TA_LEFT, spaceAfter=0, spaceBefore=0)

    s["scene_title"] = ParagraphStyle("scene_title",
        fontName="Helvetica-Bold", fontSize=9.5, textColor=COL_HEADER,
        alignment=TA_LEFT, spaceAfter=0, spaceBefore=0)

    s["desc"] = ParagraphStyle("desc",
        fontName="Helvetica-Oblique", fontSize=8, textColor=COL_DESC_FG,
        alignment=TA_LEFT, spaceAfter=0, spaceBefore=0, leading=11)

    s["label"] = ParagraphStyle("label",
        fontName="Helvetica-Bold", fontSize=8.5, textColor=COL_LABEL,
        alignment=TA_LEFT, spaceAfter=0, spaceBefore=0)

    s["text"] = ParagraphStyle("text",
        fontName="Helvetica", fontSize=8.5, textColor=COL_LABEL,
        alignment=TA_LEFT, spaceAfter=0, spaceBefore=0, leading=12)

    s["narrator"] = ParagraphStyle("narrator",
        fontName="Helvetica-Oblique", fontSize=8.5, textColor=colors.black,
        alignment=TA_LEFT, spaceAfter=0, spaceBefore=0, leading=12)

    s["expected"] = ParagraphStyle("expected",
        fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.HexColor("#1a1a1a"),
        alignment=TA_LEFT, spaceAfter=0, spaceBefore=0, leading=12)

    s["tag_corr"] = ParagraphStyle("tag_corr",
        fontName="Helvetica-Bold", fontSize=8.5, textColor=COL_CORR_TAG,
        alignment=TA_LEFT, spaceAfter=0, spaceBefore=0)

    s["tag_sugg"] = ParagraphStyle("tag_sugg",
        fontName="Helvetica-Bold", fontSize=8.5, textColor=COL_SUGG_TAG,
        alignment=TA_LEFT, spaceAfter=0, spaceBefore=0)

    return s


# ── Sort key for scene IDs ──────────────────────────────────────────────────────
ANIM_ORDER = ["I1", "I2", "C1", "C2", "C3", "C4", "P1", "P2a", "P2b",
              "P2c", "P2d", "P2e", "P2f", "T1", "T2", "T3", "T4", "T5"]

def scene_sort_key(scene_id: str):
    # scene_id = "study1_{anim}_{letter}"
    parts = scene_id.replace("study1_", "").rsplit("_", 1)
    anim = parts[0] if len(parts) > 0 else ""
    letter = parts[1] if len(parts) > 1 else ""
    try:
        anim_idx = ANIM_ORDER.index(anim)
    except ValueError:
        anim_idx = 999
    return (anim_idx, letter)


# ── Page header/footer callback ────────────────────────────────────────────────
def make_page_template(doc, styles):
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        id="main", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0
    )

    def on_page(canvas, doc):
        canvas.saveState()
        # Header line
        canvas.setStrokeColor(COL_PAGE_HDR)
        canvas.setLineWidth(0.5)
        y_hdr = A4[1] - 1.4 * cm
        canvas.line(doc.leftMargin, y_hdr, doc.leftMargin + doc.width, y_hdr)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(COL_PAGE_HDR)
        canvas.drawString(doc.leftMargin, y_hdr + 3, "Tellimations Study 1 — Utterances & Expected System Outputs")
        canvas.drawRightString(doc.leftMargin + doc.width, y_hdr + 3, f"Page {doc.page}")
        # Footer
        canvas.line(doc.leftMargin, 1.2 * cm, doc.leftMargin + doc.width, 1.2 * cm)
        canvas.drawCentredString(doc.leftMargin + doc.width / 2, 0.7 * cm,
                                 "Option T = expected system output   ·   Errors: all errors present (target highlighted)")
        canvas.restoreState()

    return PageTemplate(id="main", frames=[frame], onPage=on_page)


# ── Build scene block ──────────────────────────────────────────────────────────
def build_scene_block(scene_id, scene_data, correction, suggestion, styles):
    """Returns a KeepTogether (if short) or list of flowables for one scene."""
    elements = []
    W = 16.7 * cm  # inner table width

    title = scene_data.get("title", scene_id)
    desc = scene_data.get("scenes", [{}])[0].get("full_scene_prompt", "")

    # ── Scene header ──────────────────────────────────────────────────────────
    scene_header_data = [[
        Paragraph(f"{scene_id}  ·  {title}", styles["scene_title"])
    ]]
    scene_header = Table(scene_header_data, colWidths=[W])
    scene_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COL_SCENE_BG),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    elements.append(scene_header)

    # ── Scene description ─────────────────────────────────────────────────────
    if desc:
        desc_data = [[Paragraph(desc, styles["desc"])]]
        desc_table = Table(desc_data, colWidths=[W])
        desc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8f8f8")),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ]))
        elements.append(desc_table)

    # ── Correction block ──────────────────────────────────────────────────────
    if correction:
        cat = correction.get("target_category", "")
        err = correction.get("errors_description", "")
        narrator = correction["narrator_text"]
        expected = correction["options"]["T"]

        rows = []
        rows.append([Paragraph("CORRECTION", styles["tag_corr"]),
                     Paragraph(f"Category: {cat}", styles["label"])])
        rows.append([Paragraph("Narrator:", styles["label"]),
                     Paragraph(f"\u201c{narrator}\u201d", styles["narrator"])])
        if err:
            rows.append([Paragraph("Errors:", styles["label"]),
                         Paragraph(err, styles["text"])])
        rows.append([Paragraph("Expected:", styles["label"]),
                     Paragraph(expected, styles["expected"])])

        col_w = [2.0 * cm, W - 2.0 * cm]
        corr_table = Table(rows, colWidths=col_w)
        corr_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), COL_CORR_BG),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, -1), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ]))
        elements.append(corr_table)

    # ── Suggestion block ──────────────────────────────────────────────────────
    if suggestion:
        narrator = suggestion["narrator_text"]
        expected = suggestion["options"]["T"]

        rows = []
        rows.append([Paragraph("SUGGESTION", styles["tag_sugg"]), Paragraph("", styles["label"])])
        rows.append([Paragraph("Narrator:", styles["label"]),
                     Paragraph(f"\u201c{narrator}\u201d", styles["narrator"])])
        rows.append([Paragraph("Expected:", styles["label"]),
                     Paragraph(expected, styles["expected"])])

        col_w = [2.0 * cm, W - 2.0 * cm]
        sugg_table = Table(rows, colWidths=col_w)
        sugg_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), COL_SUGG_BG),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(sugg_table)

    elements.append(Spacer(1, 10))
    return elements


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    # Load stimuli
    with open(STIMULI_FILE) as f:
        data = json.load(f)

    stimuli = data["stimuli"]
    print(f"Loaded {len(stimuli)} stimuli")

    # Index by scene_id + condition
    by_scene = {}
    for s in stimuli:
        sid = s["scene_id"]
        cond = s["condition"]
        if sid not in by_scene:
            by_scene[sid] = {}
        by_scene[sid][cond] = s

    print(f"Found {len(by_scene)} unique scenes")

    # Load prolific_scenes
    scene_meta = {}
    for scene_file in SCENES_DIR.glob("study1_*.json"):
        with open(scene_file) as f:
            d = json.load(f)
        scene_meta[d["story_id"]] = d

    print(f"Loaded {len(scene_meta)} scene descriptions")

    # Sort scenes
    sorted_scenes = sorted(by_scene.keys(), key=scene_sort_key)

    # Build PDF
    doc = BaseDocTemplate(
        str(OUTPUT_FILE),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2.2 * cm,
        bottomMargin=1.8 * cm,
    )

    styles = make_styles()
    doc.addPageTemplates([make_page_template(doc, styles)])

    story = []

    # Title page content (inline, no separate page)
    title_style = ParagraphStyle("title_main",
        fontName="Helvetica-Bold", fontSize=14, textColor=COL_HEADER,
        alignment=TA_CENTER, spaceAfter=4, spaceBefore=0)
    sub_style = ParagraphStyle("title_sub",
        fontName="Helvetica", fontSize=9, textColor=COL_DESC_FG,
        alignment=TA_CENTER, spaceAfter=0, spaceBefore=0)

    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("Tellimations Study 1", title_style))
    story.append(Paragraph("Complete list of 200 utterances with scene descriptions and expected system outputs", sub_style))
    story.append(Paragraph(f"100 scenes  ·  100 corrections  ·  100 suggestions", sub_style))
    story.append(Spacer(1, 0.5 * cm))

    # Legend
    W = 16.7 * cm
    legend_rows = [[
        Paragraph("CORRECTION  target error to fix (T option)", ParagraphStyle("lc",
            fontName="Helvetica", fontSize=7.5, textColor=COL_CORR_TAG)),
        Paragraph("SUGGESTION  missing information to add (T option)", ParagraphStyle("ls",
            fontName="Helvetica", fontSize=7.5, textColor=COL_SUGG_TAG)),
    ]]
    legend = Table(legend_rows, colWidths=[W/2, W/2])
    legend.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f5")),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ]))
    story.append(legend)
    story.append(Spacer(1, 0.5 * cm))

    # All scenes
    n_corr = 0
    n_sugg = 0
    for scene_id in sorted_scenes:
        stimuli_for_scene = by_scene[scene_id]
        correction = stimuli_for_scene.get("correction")
        suggestion = stimuli_for_scene.get("suggestion")
        meta = scene_meta.get(scene_id, {})

        block = build_scene_block(scene_id, meta, correction, suggestion, styles)
        story.extend(block)

        if correction: n_corr += 1
        if suggestion: n_sugg += 1

    print(f"Built {n_corr} correction blocks + {n_sugg} suggestion blocks")

    doc.build(story)
    print(f"PDF saved to: {OUTPUT_FILE}")
    print(f"File size: {OUTPUT_FILE.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
