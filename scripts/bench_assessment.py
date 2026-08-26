#!/usr/bin/env python3
"""Benchmark the assessment pipeline across Gemini models before switching.

Why: `gemini-3-flash-preview` is deprecated (no shutdown date announced) and
cheaper than its announced replacement `gemini-3.6-flash`. Assessment quality
drives both the pedagogy and the study data, so a model swap must be decided on
measured agreement, never on the vendor's release notes.

Corpus: the 200 stimuli in data/study1_all_stimuli.json, scored against
`target_animation` / `target_entities` (corrections) and
`pipeline_animation_id` / `pipeline_target_entities` (suggestions).

READ THE COLUMNS CORRECTLY. `target_animation` is the animation each stimulus
was *designed* to elicit (it is encoded in the stimulus id, e.g.
study1_P2a_C_correction -> P2a), and on all 100 corrections it is byte-identical
to `pipeline_animation_id`, i.e. to the March 2026 run recorded in
data/pipeline_results.json. That run scores 100/100 on a 25-way choice, which a
single unguided pass does not do -- it was curated stimulus by stimulus in
pipeline_ui until the pipeline produced the designed animation. So:

  - the `anim`/`entity`/`both` columns measure **single-pass agreement with the
    study's designed target**, not accuracy against an independent gold label;
  - measured at ~20-30% for corrections on every model tested, so this column
    does NOT discriminate between models;
  - the columns that DO discriminate are cross-model agreement (printed at the
    end) and median latency, which matters directly in the live child-facing
    loop.

The MISL code is chosen by `select_misl_candidates` (deterministic Python), so
it is identical across models and is deliberately not scored here.

Usage:
    python -m scripts.bench_assessment                    # 40 stimuli, 3 models
    python -m scripts.bench_assessment --all              # all 200
    python -m scripts.bench_assessment --models gemini-3-flash-preview gemini-3.1-flash-lite
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import os  # noqa: E402

from config.models import FLASH_MODEL_ID  # noqa: E402
from src.interaction import discrepancy_assessment  # noqa: E402
from src.interaction.discrepancy_assessment import (  # noqa: E402
    assess_corrections,
    assess_enrichment,
)
from src.interaction.misl_selector import select_misl_candidates  # noqa: E402
from scripts.study_gen.pipeline_ui import (  # noqa: E402
    _normalize_animation_id,
    _validate_correction,
    get_misl_targets_for_stimulus,
    load_scene,
    load_stimuli,
)

# pipeline_ui sets the root logger to INFO on import; the benchmark only
# wants its own table, so quiet everything back down.
logging.getLogger().setLevel(logging.WARNING)
for _noisy in ("google_genai", "src", "httpx"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

DEFAULT_MODELS = [
    FLASH_MODEL_ID,          # current
    "gemini-3.1-flash-lite",  # cheaper than current
    "gemini-3.6-flash",       # announced replacement, more expensive
]

OUT_PATH = PROJECT_ROOT / "data" / "bench_assessment.json"


# ---------------------------------------------------------------------------
# Single-stimulus runs (no pipeline_intent call — that is presentation, not
# assessment, and would double the cost of every benchmark run)
# ---------------------------------------------------------------------------

async def _run_correction(api_key: str, stimulus: Dict[str, Any]) -> Dict[str, Any]:
    scene = load_scene(stimulus["scene_id"])["scenes"][0]
    scene_desc = scene.get("scene_description") or scene["full_scene_prompt"]
    entities = scene.get("entities_in_scene", [])

    t0 = time.time()
    discrepancies, _names = await assess_corrections(
        api_key=api_key,
        utterance_text=stimulus["narrator_text"],
        story_so_far=[],
        scene_description=scene_desc,
        character_names=None,
        entities_in_scene=entities,
    )
    elapsed_ms = int((time.time() - t0) * 1000)

    if not discrepancies:
        return {
            "animation_id_short": None,
            "target_entities": [],
            "elapsed_ms": elapsed_ms,
            "validation": {"animation_match": False, "entity_match": False},
        }

    d = discrepancies[0]
    aid = _normalize_animation_id(d.animation_id or "")
    return {
        "animation_id_short": aid,
        "target_entities": d.target_entities,
        "description": d.description,
        "elapsed_ms": elapsed_ms,
        "validation": _validate_correction(stimulus, aid, d.target_entities),
    }


async def _run_suggestion(api_key: str, stimulus: Dict[str, Any]) -> Dict[str, Any]:
    scene = load_scene(stimulus["scene_id"])["scenes"][0]
    entities = scene.get("entities_in_scene", [])
    misl_targets = get_misl_targets_for_stimulus(
        load_scene(stimulus["scene_id"]), stimulus.get("target_misl", "")
    )
    macro_selected, micro_candidates, _trace = select_misl_candidates(
        misl_targets=misl_targets,
        mention_counts=stimulus.get("mention_counts", {}),
        study_log_entries=[],
    )

    t0 = time.time()
    discrepancies = await assess_enrichment(
        api_key=api_key,
        utterance_text=stimulus["narrator_text"],
        story_so_far=[],
        character_names=None,
        misl_targets=misl_targets,
        entities_in_scene=entities,
        macro_selected=macro_selected,
        micro_candidates=micro_candidates,
    )
    elapsed_ms = int((time.time() - t0) * 1000)

    ref_anim = _normalize_animation_id(stimulus.get("pipeline_animation_id", ""))
    ref_entities = set(stimulus.get("pipeline_target_entities", []))

    if not discrepancies:
        return {
            "animation_id_short": None,
            "target_entities": [],
            "elapsed_ms": elapsed_ms,
            "validation": {"animation_match": False, "entity_match": False},
        }

    d = discrepancies[0]
    aid = _normalize_animation_id(d.animation_id or "")
    return {
        "animation_id_short": aid,
        "target_entities": d.target_entities,
        "description": d.description,
        "elapsed_ms": elapsed_ms,
        "validation": {
            "animation_match": aid.upper() == ref_anim.upper() if ref_anim else True,
            "entity_match": bool(set(d.target_entities) & ref_entities) if ref_entities else True,
        },
    }


# ---------------------------------------------------------------------------
# Benchmark driver
# ---------------------------------------------------------------------------

def _sample(stimuli: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """Deterministic stratified sample: n/2 corrections, n/2 suggestions."""
    corrections = sorted(
        (s for s in stimuli if s["condition"] == "correction"),
        key=lambda s: s["stimulus_id"],
    )
    suggestions = sorted(
        (s for s in stimuli if s["condition"] == "suggestion"),
        key=lambda s: s["stimulus_id"],
    )
    half = max(1, n // 2)
    step_c = max(1, len(corrections) // half)
    step_s = max(1, len(suggestions) // half)
    return corrections[::step_c][:half] + suggestions[::step_s][:half]


async def bench_model(
    api_key: str,
    model_id: str,
    stimuli: List[Dict[str, Any]],
    concurrency: int,
) -> Dict[str, Any]:
    """Run the whole sample through one model. Patches the module-level MODEL_ID."""
    original = discrepancy_assessment.MODEL_ID
    discrepancy_assessment.MODEL_ID = model_id
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def one(stimulus: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal done
        async with sem:
            try:
                if stimulus["condition"] == "correction":
                    res = await _run_correction(api_key, stimulus)
                else:
                    res = await _run_suggestion(api_key, stimulus)
            except Exception as exc:  # a dead model / quota error must not abort the run
                res = {
                    "error": f"{type(exc).__name__}: {exc}",
                    "animation_id_short": None,
                    "target_entities": [],
                    "elapsed_ms": 0,
                    "validation": {"animation_match": False, "entity_match": False},
                }
            done += 1
            print(f"  {model_id}: {done}/{len(stimuli)}", end="\r", flush=True)
            return {
                "stimulus_id": stimulus["stimulus_id"],
                "condition": stimulus["condition"],
                **res,
            }

    try:
        results = await asyncio.gather(*(one(s) for s in stimuli))
    finally:
        discrepancy_assessment.MODEL_ID = original

    print(" " * 60, end="\r")
    return {"model": model_id, "results": list(results)}


def summarize(run: Dict[str, Any]) -> Dict[str, Any]:
    rows = run["results"]
    out: Dict[str, Any] = {"model": run["model"]}
    for cond in ("correction", "suggestion", "all"):
        subset = rows if cond == "all" else [r for r in rows if r["condition"] == cond]
        if not subset:
            continue
        n = len(subset)
        out[cond] = {
            "n": n,
            "animation_match": sum(r["validation"]["animation_match"] for r in subset) / n,
            "entity_match": sum(r["validation"]["entity_match"] for r in subset) / n,
            "both": sum(
                r["validation"]["animation_match"] and r["validation"]["entity_match"]
                for r in subset
            ) / n,
            "no_output": sum(r["animation_id_short"] is None for r in subset) / n,
            "errors": sum("error" in r for r in subset),
            "median_ms": sorted(r["elapsed_ms"] for r in subset)[n // 2],
        }
    return out


def agreement(run_a: Dict[str, Any], run_b: Dict[str, Any]) -> float:
    """Fraction of stimuli where two models picked the same animation."""
    by_id = {r["stimulus_id"]: r for r in run_b["results"]}
    shared = [r for r in run_a["results"] if r["stimulus_id"] in by_id]
    if not shared:
        return 0.0
    same = sum(
        r["animation_id_short"] == by_id[r["stimulus_id"]]["animation_id_short"]
        for r in shared
    )
    return same / len(shared)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--n", type=int, default=40, help="stimuli to sample (half of each condition)")
    ap.add_argument("--all", action="store_true", help="use all 200 stimuli")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        sys.exit("GEMINI_API_KEY not set")

    stimuli = load_stimuli()
    sample = stimuli if args.all else _sample(stimuli, args.n)
    print(f"Benchmarking {len(args.models)} models on {len(sample)} stimuli "
          f"({sum(s['condition'] == 'correction' for s in sample)} corrections / "
          f"{sum(s['condition'] == 'suggestion' for s in sample)} suggestions)\n")

    runs = []
    for model_id in args.models:
        t0 = time.time()
        run = await bench_model(api_key, model_id, sample, args.concurrency)
        run["wall_s"] = round(time.time() - t0, 1)
        runs.append(run)
        print(f"  {model_id}: done in {run['wall_s']}s")

    print("\n" + "=" * 92)
    print("anim/entity/both = single-pass agreement with the study's DESIGNED target "
          "(not an independent gold label -- see module docstring)")
    header = f"{'model':30s} {'cond':11s} {'n':>4s} {'anim':>7s} {'entity':>7s} {'both':>7s} {'empty':>7s} {'p50 ms':>8s}"
    print(header)
    print("-" * 92)
    summaries = []
    for run in runs:
        s = summarize(run)
        summaries.append(s)
        for cond in ("correction", "suggestion", "all"):
            if cond not in s:
                continue
            c = s[cond]
            print(f"{run['model']:30s} {cond:11s} {c['n']:4d} "
                  f"{c['animation_match']:6.1%} {c['entity_match']:6.1%} {c['both']:6.1%} "
                  f"{c['no_output']:6.1%} {c['median_ms']:8d}")
        print("-" * 92)

    if len(runs) > 1:
        print("\nCross-model animation agreement (vs. the first model listed):")
        for run in runs[1:]:
            print(f"  {runs[0]['model']} vs {run['model']}: {agreement(runs[0], run):.1%}")

    args.out.write_text(json.dumps(
        {"sample_size": len(sample), "summaries": summaries, "runs": runs},
        indent=1,
    ))
    print(f"\nFull results -> {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
