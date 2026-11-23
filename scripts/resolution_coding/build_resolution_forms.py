#!/usr/bin/env python3
"""Google Forms for the blind re-coding of resolutions: content and answers.

Two steps around the two forms (one per coder) built by resolution_forms.gs:

1. items (default): reads coding/coder_A.csv and coding/coder_B.csv, written by
   make_coding_items.py, and writes coding/resolution_items.json, the file the
   Apps Script reads: per coder, a list of pages, each with the name of one
   scene image and up to --per-page items about that scene (id and the three
   texts). Nothing else: no child, no story, no condition, no pipeline
   judgment. key.csv stays on this machine.

   A Google Form cannot hold more than MAX_ELEMENTS elements, so the items
   cannot each have their own page and image. The items of one scene are
   shuffled, cut into pages that show the illustration once, and the pages of
   all scenes are shuffled; both shuffles differ between the two coders. The
   order of the coder sheets is therefore not the order of the forms; the
   analysis goes by item id and does not depend on it.

2. --answers A responses.csv: reads the answers downloaded from the form of
   coder A (Google Forms > Responses > Download responses (.csv)) and writes
   coding/coder_A_filled.csv, in the format analyze_coding.py expects.

--check prints, per coder, the number of form items and each item as
id:checksum; resolutionCheck() in the Apps Script prints the same line from
the form itself.

To change the data: build_gaps_csv.py, make_coding_items.py, this script, then
replace resolution_items.json in the Drive folder and run rebuildCoderA and
rebuildCoderB in the Apps Script. The forms keep their links.

Usage (from scripts/resolution_coding):
    python3 build_resolution_forms.py
    python3 build_resolution_forms.py --check
    python3 build_resolution_forms.py --answers A "Resolution coding (coder A).csv"
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

CODERS = ("A", "B")
INTRO_ELEMENTS = 5   # section headers of the first page (resolution_forms.gs, resIntro_)
PAGE_ELEMENTS = 3    # page break, image, comment box; plus one grid per item
MAX_ELEMENTS = 1000  # limit of Google Forms ("vous avez atteint le nombre maximal d'éléments")
ROW_RESOLVED = "The child addressed the gap in what they said next"
ROW_GUESS = "My guess: this story was told with animations"


def image_name(path: str) -> str:
    """'data/study_gen/C/hd/scene_3_full.png' -> 'C_3.jpg' (the names used in the Drive folder)."""
    m = re.search(r"/([^/]+)/(?:hd/)?scene_(\d+)_full\.png$", path)
    if not m:
        raise SystemExit(f"unexpected scene image path: {path}")
    return f"{m.group(1)}_{m.group(2)}.jpg"


def texts(item: Dict[str, str]) -> str:
    """The text shown under the image; the same string is built in resolution_forms.gs (resTexts_)."""
    return ("WHAT THE CHILD SAID\n" + item["utterance"] + "\n\nTHE GAP FOUND BY THE TOOL\n" + item["gap"]
            + "\n\nWHAT THE CHILD SAID NEXT\n" + item["next"])


def checksum(text: str) -> int:
    """Same value as resolutionCheck() in resolution_forms.gs."""
    h = 7
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h = (h * 31 + int.from_bytes(data[i:i + 2], "little")) % 1000000007
    return h


def read_sheet(path: Path) -> List[Dict[str, str]]:
    """Rows of a coder sheet."""
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def pages_of(rows: List[Dict[str, str]], per_page: int, seed: int) -> List[Dict]:
    """Pages of one coder: items of one scene shuffled and cut into pages of equal size, pages shuffled."""
    rng = random.Random(seed)
    by_image: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        item = {"id": r["item_id"], "utterance": " ".join(r["utterance"].split()),
                "gap": " ".join(r["gap"].split()), "next": " ".join(r["next_utterance"].split())}
        if not (item["utterance"] and item["gap"] and item["next"]):
            raise SystemExit(f"{item['id']}: empty text")
        by_image[image_name(r["scene_image"])].append(item)
    pages = []
    for image in sorted(by_image):
        items = by_image[image]
        rng.shuffle(items)
        k = -(-len(items) // per_page)
        for j in range(k):
            pages.append({"image": image, "items": items[j * len(items) // k:(j + 1) * len(items) // k]})
    rng.shuffle(pages)
    return pages


def elements(pages: List[Dict]) -> int:
    """Number of form elements these pages need."""
    return INTRO_ELEMENTS + sum(PAGE_ELEMENTS + len(p["items"]) for p in pages)


def write_items(folder: Path, per_page: int, seed: int) -> None:
    """coding/coder_X.csv -> coding/resolution_items.json."""
    coders = {c: pages_of(read_sheet(folder / f"coder_{c}.csv"), per_page, seed + k) for k, c in enumerate(CODERS)}
    ids = {c: sorted(i["id"] for p in coders[c] for i in p["items"]) for c in CODERS}
    if ids["A"] != ids["B"]:
        raise SystemExit("the two coders do not have the same items")
    for c in CODERS:
        if elements(coders[c]) > MAX_ELEMENTS:
            raise SystemExit(f"coder {c}: {elements(coders[c])} form elements, more than the {MAX_ELEMENTS} a Google "
                             f"Form accepts. Raise --per-page.")
    out = folder / "resolution_items.json"
    out.write_text(json.dumps({"coders": coders}, ensure_ascii=False, indent=1), encoding="utf-8")
    for c in CODERS:
        sizes = [len(p["items"]) for p in coders[c]]
        print(f"coder {c}: {len(ids[c])} items on {len(sizes)} pages ({min(sizes)} to {max(sizes)} items per page), "
              f"{elements(coders[c])} form elements")
    print(f"-> {out}")


def print_check(folder: Path) -> None:
    """One line per coder, to compare with the log of resolutionCheck()."""
    coders = json.loads((folder / "resolution_items.json").read_text(encoding="utf-8"))["coders"]
    for c, pages in coders.items():
        print(f"coder {c} | {elements(pages)} elements | "
              + " ".join(f"{i['id']}:{checksum(texts(i))}" for p in pages for i in p["items"]))


def read_answers(folder: Path, coder: str, responses: Path) -> None:
    """Form answers of one coder -> coding/coder_X_filled.csv (resolved 1/0, condition_guess animation/control)."""
    rows = read_sheet(responses)
    if len(rows) != 1:
        raise SystemExit(f"{responses}: {len(rows)} responses, expected exactly one (delete the test responses first)")
    answer: Dict[str, Dict[str, str]] = {}
    comments = []
    for title, value in rows[0].items():
        m = re.match(r"Item (G\d+) \[(.+)\]$", title or "")
        if m:
            answer.setdefault(m.group(1), {})[m.group(2)] = (value or "").strip().lower()
        elif re.match(r"Page \d+ · Comments", title or "") and (value or "").strip():
            comments.append(f"{title}: {value.strip()}")
    sheet = read_sheet(folder / f"coder_{coder}.csv")
    missing = [r["item_id"] for r in sheet if answer.get(r["item_id"], {}).get(ROW_RESOLVED) not in ("yes", "no")
               or answer[r["item_id"]].get(ROW_GUESS) not in ("yes", "no")]
    if missing:
        raise SystemExit(f"{len(missing)} items without an answer, e.g. {missing[:5]}")
    for r in sheet:
        a = answer[r["item_id"]]
        r["resolved"] = "1" if a[ROW_RESOLVED] == "yes" else "0"
        r["condition_guess"] = "animation" if a[ROW_GUESS] == "yes" else "control"
        r["comment"] = ""
    if comments:
        (folder / f"coder_{coder}_comments.txt").write_text("\n\n".join(comments) + "\n", encoding="utf-8")
        print(f"coder {coder}: {len(comments)} page comments -> {folder / f'coder_{coder}_comments.txt'}")
    out = folder / f"coder_{coder}_filled.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sheet[0].keys()))
        w.writeheader()
        w.writerows(sheet)
    print(f"coder {coder}: {len(sheet)} items, {sum(r['resolved'] == '1' for r in sheet)} resolved, "
          f"{sum(r['condition_guess'] == 'animation' for r in sheet)} guessed as animation -> {out}")


def main() -> None:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--coding", type=Path, default=Path("coding"), help="folder written by make_coding_items.py")
    ap.add_argument("--per-page", type=int, default=8, help="largest number of items on one page")
    ap.add_argument("--seed", type=int, default=2027)
    ap.add_argument("--check", action="store_true", help="print the expected output of resolutionCheck()")
    ap.add_argument("--answers", nargs=2, metavar=("CODER", "RESPONSES_CSV"),
                    help="convert the answers downloaded from one form")
    args = ap.parse_args()
    if args.check:
        print_check(args.coding)
    elif args.answers:
        if args.answers[0] not in CODERS:
            raise SystemExit("coder must be A or B")
        read_answers(args.coding, args.answers[0], Path(args.answers[1]))
    else:
        write_items(args.coding, args.per_page, args.seed)


if __name__ == "__main__":
    main()
