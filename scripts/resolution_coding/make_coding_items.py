"""Build blind coding sheets for the resolution re-coding (Tellimation, CHI 2027 revision).

Input: one CSV with one row per assessed gap (the 690 gaps of Section 6.3, plus the
gaps of the practice stories if you have them). Required columns:

    child            participant id (P1..P12)
    story            story id or name
    condition        "animation" or "control"
    scene_key        scene id within the story
    scene_image      path or URL of the scene illustration shown to the coder
    utterance        the child's utterance in which the pipeline found the gap
    gap              neutral description of the gap (the pipeline's rationale)
    next_utterance   the child's next utterance in the same scene
    llm_resolved     the pipeline's judgment (1 / 0)

Optional columns: animation_id, interrupted (1 / 0), is_practice (1 / 0).

Output (in --out):
    coder_A.csv, coder_B.csv   items in two different random orders, with empty
                               columns resolved, condition_guess, comment
    practice_items.csv         practice-story gaps for calibration (not analysed)
    key.csv                    item_id -> child, story, condition, llm_resolved, ...
                               (do NOT give this file to the coders)
    leak_report.csv            items whose text may reveal the condition

Usage:
    python make_coding_items.py gaps.csv --out coding/ --seed 2027
"""

import argparse
import re
from pathlib import Path

import pandas as pd

REQUIRED = ["child", "story", "condition", "scene_key", "scene_image",
            "utterance", "gap", "next_utterance", "llm_resolved"]

# Words that, in a child's utterance or in the gap text, may give the condition away.
ANIMATION_NAMES = [
    "spotlight", "nametag", "silhouette", "ghost", "outline", "missing piece",
    "reveal", "peek", "flip", "motion lines", "timelapse", "flashback",
    "interjection", "alert", "emanata", "heart", "speech bubble", "thought bubble",
]
LEAK_PATTERN = re.compile(
    r"\b(mov(e|es|ed|ing)|shak(e|es|ing)|glow(s|ing)?|flash(es|ing)?|bounc(e|es|ing)|"
    r"animat\w*|wiggl\w*|jump(s|ed|ing)? out|light(s)? up|"
    + "|".join(re.escape(n) for n in ANIMATION_NAMES) + r")\b",
    re.IGNORECASE,
)

CODER_COLUMNS = ["item_id", "scene_image", "utterance", "gap", "next_utterance"]
BLANK_COLUMNS = ["resolved", "condition_guess", "comment"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gaps_csv")
    ap.add_argument("--out", default="coding")
    ap.add_argument("--seed", type=int, default=2027)
    args = ap.parse_args()

    df = pd.read_csv(args.gaps_csv)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(f"Missing columns: {missing}")
    if "is_practice" not in df.columns:
        df["is_practice"] = 0
    df["is_practice"] = df["is_practice"].fillna(0).astype(int)
    df["condition"] = df["condition"].str.lower().str.strip()
    bad = set(df.loc[df.is_practice == 0, "condition"]) - {"animation", "control"}
    if bad:
        raise SystemExit(f"Unexpected condition values: {bad}")

    # A gap with no next utterance in the same scene (typically the last gap of a
    # scene, cut short by "Next Scene") gave the child no chance to respond: it is
    # not a coding item. Report how many are dropped, per condition.
    no_next = df["next_utterance"].isna() | (df["next_utterance"].astype(str).str.strip() == "")
    if no_next.any():
        dropped = df[no_next & (df.is_practice == 0)].groupby("condition").size().to_dict()
        print(f"Dropped {int(no_next.sum())} gaps with no next utterance in the scene: {dropped}")
    df = df[~no_next].copy()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    study = df[df.is_practice == 0].copy()
    practice = df[df.is_practice == 1].copy()
    if study.empty:
        raise SystemExit("No study items (all rows have is_practice = 1)")

    # Item ids carry no information: they are assigned after a shuffle.
    study = study.sample(frac=1, random_state=args.seed).reset_index(drop=True)
    study.insert(0, "item_id", [f"G{i + 1:03d}" for i in range(len(study))])

    # Key file, kept away from the coders.
    key_cols = ["item_id", "child", "story", "condition", "scene_key", "llm_resolved"]
    key_cols += [c for c in ("animation_id", "interrupted") if c in study.columns]
    study[key_cols].to_csv(out / "key.csv", index=False)

    # Two coders, two independent random orders, identical content.
    for coder, offset in (("A", 1), ("B", 2)):
        sheet = study[CODER_COLUMNS].sample(frac=1, random_state=args.seed + offset)
        for c in BLANK_COLUMNS:
            sheet[c] = ""
        sheet.to_csv(out / f"coder_{coder}.csv", index=False)

    if len(practice):
        practice = practice.sample(frac=1, random_state=args.seed).reset_index(drop=True)
        practice.insert(0, "item_id", [f"T{i + 1:02d}" for i in range(len(practice))])
        sheet = practice[CODER_COLUMNS].copy()
        sheet["resolved"] = ""
        sheet["comment"] = ""
        sheet.to_csv(out / "practice_items.csv", index=False)

    # Items whose text may reveal the condition: the coders keep them, but you
    # should know how many there are before reading the condition guesses.
    rows = []
    for _, r in study.iterrows():
        for field in ("utterance", "gap", "next_utterance"):
            m = LEAK_PATTERN.search(str(r[field]))
            if m:
                rows.append({"item_id": r.item_id, "field": field, "match": m.group(0),
                             "condition": r.condition, "text": r[field]})
    pd.DataFrame(rows, columns=["item_id", "field", "match", "condition", "text"]) \
        .to_csv(out / "leak_report.csv", index=False)

    n_anim = (study.condition == "animation").sum()
    n_ctrl = (study.condition == "control").sum()
    print(f"{len(study)} study items ({n_anim} animation, {n_ctrl} control), "
          f"{len(practice)} practice items, {len({r['item_id'] for r in rows})} "
          f"items flagged in leak_report.csv")


if __name__ == "__main__":
    main()
