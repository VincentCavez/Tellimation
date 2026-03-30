#!/usr/bin/env python3
"""Remove white backgrounds from extraction PNGs using local withoutbg Docker API.

Writes to a SEPARATE output directory — NEVER overwrites originals.
Output: prolific_gen/{story_id}/extractions_nobg/

Requires: docker container withoutbg running on localhost:80
  docker run -d -p 80:80 withoutbg/app:latest

Usage:
    python -m scripts.study_gen.remove_backgrounds
    python -m scripts.study_gen.remove_backgrounds --dry-run
    python -m scripts.study_gen.remove_backgrounds --workers 8
"""

from __future__ import annotations

import argparse
import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROLIFIC_GEN = PROJECT_ROOT / "data" / "prolific_gen"
API_URL = "http://localhost:80/api/remove-background"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("remove_bg")


def find_extractions() -> list[tuple[Path, Path]]:
    """Return list of (input_path, output_path) pairs."""
    pairs = []
    for src in sorted(PROLIFIC_GEN.glob("*/extractions/*.png")):
        dst_dir = src.parent.parent / "extractions_nobg"
        dst = dst_dir / src.name
        if dst.exists():
            continue  # already processed
        pairs.append((src, dst))
    return pairs


def process_one(src: Path, dst: Path) -> bool:
    try:
        with open(src, "rb") as f:
            resp = requests.post(
                API_URL,
                files={"file": (src.name, f, "image/png")},
                data={"format": "png"},
                timeout=120,
            )

        if resp.status_code != 200:
            logger.error("ERR %s — HTTP %d: %s", src.name, resp.status_code, resp.text[:200])
            return False

        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        dst.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(dst), format="PNG")
        logger.info("OK  %s", src.relative_to(PROLIFIC_GEN))
        return True
    except Exception as exc:
        logger.error("ERR %s — %s", src.name, exc)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove backgrounds via withoutbg Docker API")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Health check
    if not args.dry_run:
        try:
            r = requests.get("http://localhost:80/api/health", timeout=5)
            r.raise_for_status()
            logger.info("withoutbg API is up")
        except Exception:
            logger.error("withoutbg not reachable on localhost:80. Run: docker run -d -p 80:80 withoutbg/app:latest")
            return

    pairs = find_extractions()
    logger.info("Found %d file(s) to process (output → extractions_nobg/)", len(pairs))

    if not pairs:
        logger.info("Nothing to do.")
        return

    if args.dry_run:
        for src, dst in pairs:
            print(f"{src}  →  {dst}")
        return

    done = errors = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_one, src, dst): src for src, dst in pairs}
        for future in as_completed(futures):
            if future.result():
                done += 1
            else:
                errors += 1
            logger.info("Progress: %d/%d (errors: %d)", done + errors, len(pairs), errors)

    logger.info("Done: %d OK, %d errors. Output in extractions_nobg/", done, errors)


if __name__ == "__main__":
    main()
