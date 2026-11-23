#!/usr/bin/env python3
"""Run simulated narrators through the REAL study pipeline, to see how it copes.

A headless client plays the browser's part against the app in process:
it fetches each scene from /api/study/scene, opens /ws/study exactly as
study_story.js does, and sends recordings. The only thing replaced is the
speech recogniser: the "audio" is the recogniser output of the simulated child
(scripts/simulation/child.py: what the child says, with mis-heard words, missed
starts and cut ends as in the real sessions). The child model never reads what
the pipeline wrote: it is only told which animation played, and on what.
Corrections, suggestions, animation choice,
resolution checks and study logs are produced by the unmodified server code.

Everything written is marked as simulated: participant ids start with SIM-,
each log carries "simulated": true with the profile used, and nothing is
pushed to the Google Sheet. Timestamps are those of the simulation run.

Output (default data/simulated_sessions/):
  users/SIM-N1/{study_log,training_log}.json   logs written by the server
  trace/SIM-N1.jsonl      one line per recording: plan, text, pipeline verdict
  server_logs/SIM-N1.log  warnings and errors raised by the server
  simulated_sessions_export.csv   Sheet-format export (reads with merge_session_logs)
  summary.json            run settings, API usage and cost

Usage:
    python3 -m scripts.simulation.simulate_sessions            # all profiles
    python3 -m scripts.simulation.simulate_sessions --only SIM-N1 --stories A --scenes 2
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
PROFILES = Path(__file__).with_name("profiles.json")
DEFAULT_OUT = REPO / "data" / "simulated_sessions"
PREFIX = "SIM-"


def load_profiles() -> List[Dict[str, Any]]:
    """Load the simulated child profiles."""
    return json.loads(PROFILES.read_text())["profiles"]


def scene_picture(scene: Dict[str, Any]) -> Optional[str]:
    """The whole picture the child looks at (hd/scene_N_full.png), or None for a legacy scene."""
    url = str(scene.get("background_url") or "")
    if not url.startswith("/study-assets/"):
        return None
    path = REPO / "data" / "study_gen" / url[len("/study-assets/"):].replace("_bg.png", "_full.png")
    return str(path) if path.exists() else None


class Session:
    """One simulated participant: owns the patched app, the child and the trace."""

    def __init__(self, profile: Dict[str, Any], out: Path, seed: int, child_model: Optional[str]) -> None:
        """Set up a simulated session: redirect logs to the output folder, disable the Sheet push and create the simulated child."""
        self.profile, self.out = profile, out
        self.pid = profile["id"]
        assert self.pid.startswith(PREFIX), "simulated ids must start with SIM-"
        os.chdir(REPO)

        import src.persistence as persistence
        persistence.DATA_ROOT = out / "users"   # logs go to the simulation folder
        persistence.GOOGLE_SHEET_WEBHOOK = ""   # never push simulations to the Sheet
        self.persistence = persistence

        import src.ui.app as appmod
        from config.models import FLASH_MODEL_ID, PRO_MODEL_ID
        from fastapi.testclient import TestClient

        async def text_as_audio(audio_bytes: bytes, known_names: Any = None) -> str:
            """Replacement for speech recognition: the simulated audio already holds the transcript as UTF-8 text."""
            return audio_bytes.decode("utf-8")
        appmod.transcribe_audio = text_as_audio  # the recording IS the text

        self._quiet_logging()
        self.pipeline_usage = self._meter_pipeline()
        self.pipeline_model = FLASH_MODEL_ID
        self.child_model = child_model or PRO_MODEL_ID
        self.client = TestClient(appmod.app)

        from scripts.simulation.child import ChildSimulator
        self.rng = random.Random(f"{seed}:{self.pid}")
        self.child = ChildSimulator(profile, os.environ["GEMINI_API_KEY"], self.child_model, self.rng)
        self.seed = seed
        self.turns = 0

    # ------------------------------------------------------------- plumbing

    def _quiet_logging(self) -> None:
        """Keep only the server's warnings and errors, in a file per participant."""
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        root.setLevel(logging.WARNING)
        log_dir = self.out / "server_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / f"{self.pid}.log", mode="w", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        root.addHandler(handler)

    @staticmethod
    def _meter_pipeline() -> Dict[str, int]:
        """Count the tokens of every Gemini call the server makes."""
        from google.genai import models as genai_models
        usage = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        original = genai_models.AsyncModels.generate_content

        async def generate_content(self, *args, **kwargs):
            """Wrapped generate_content that counts calls and tokens before returning the response."""
            response = await original(self, *args, **kwargs)
            meta = response.usage_metadata
            if meta is not None:
                usage["calls"] += 1
                usage["tokens_in"] += meta.prompt_token_count or 0
                usage["tokens_out"] += (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)
            return response

        genai_models.AsyncModels.generate_content = generate_content
        return usage

    def _reset_logs(self) -> None:
        """Start from empty logs that say what they are."""
        pdir = (self.out / "users" / self.pid).resolve()
        assert self.out.resolve() in pdir.parents and pdir.name.startswith(PREFIX)
        if pdir.exists():
            shutil.rmtree(pdir)
        header = {
            "simulated": True,
            "simulation": {
                "profile": {k: self.profile[k] for k in ("label", "diagnosis", "age", "order", "reference", "note", "traits")},
                "child_model": self.child_model, "pipeline_model": self.pipeline_model, "seed": self.seed,
                "generator": "scripts/simulation/simulate_sessions.py",
            },
        }
        for training in (True, False):
            path = self.persistence._study_log_path(self.pid, training)
            log = {"participant": self.pid, "type": "training" if training else "study", **header, "scenes": {}}
            path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        trace = self.out / "trace" / f"{self.pid}.jsonl"
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text("")
        self.trace_path = trace

    def _scene_entries(self, training: bool, scene_key: str) -> List[Dict[str, Any]]:
        """Log entries of a scene for this participant."""
        return self.persistence.load_study_log(self.pid, training)["scenes"].get(scene_key, [])

    # ---------------------------------------------------------------- story

    def run_story(self, story: str, animated: bool, scenes: List[int], silent: Optional[List[int]] = None) -> None:
        """One page of the study: a websocket per story, as the browser does."""
        training = story == "training"
        story_def = None
        said_in_story: List[str] = []
        sticky: List[str] = []
        named: List[str] = []
        seen_entities: List[str] = []
        url = f"/ws/study?participant={self.pid}&story={story}&animated={'1' if animated else '0'}"
        with self.client.websocket_connect(url) as ws:
            for scene_num in scenes:
                resp = self.client.get("/api/study/scene", params={"story": story, "scene": scene_num})
                resp.raise_for_status()
                scene = resp.json()
                ws.send_json({"type": "study_scene_loaded", "story": story, "scene_number": scene_num, "scene": scene})
                if silent and scene_num in silent:
                    continue  # tutorial page: animations are demonstrated, nothing is recorded
                manifest = scene.get("manifest") or scene
                in_scene = manifest.get("entities_in_scene", [])
                names = dict(scene.get("characters") or {})
                ctx: Dict[str, Any] = {
                    "story": story, "scene_number": 1 if training else scene_num,
                    "scene_count": 1 if training else len(scenes),
                    "scene_description": self.child.view(manifest.get("scene_description", "")),
                    "image": scene_picture(scene),
                    "noise": self.child.scene_noise(),
                    "entities_in_scene": in_scene, "entity_names": names,
                    "absent": [e for e in seen_entities if e not in in_scene],
                    "story_so_far": list(said_in_story), "said_in_scene": [],
                    "first_in_scene": True, "animation": None, "sticky": sticky, "named": named,
                    "last_mistakes": [], "last_spoken": None,
                }
                scene_key = f"{story}_scene_{scene.get('scene_number', scene_num)}"
                n_turns = self.child.turns_in_scene(animated)
                for turn in range(1, n_turns + 1):
                    self._turn(ws, ctx, training, story, scene_key, animated, turn, last=turn == n_turns)
                said_in_story += ctx["said_in_scene"]
                seen_entities += [e for e in in_scene if e not in seen_entities]
                story_def = story_def or scene

    def _turn(self, ws: Any, ctx: Dict[str, Any], training: bool, story: str, scene_key: str,
              animated: bool, turn: int, last: bool) -> None:
        """Play one turn: the child speaks, the real pipeline answers over the websocket, and the exchange is traced."""
        plan = self.child.plan(ctx)
        spoken = self.child.speak(plan, ctx)
        before = len(self._scene_entries(training, scene_key))
        started = time.time()
        ws.send_bytes(spoken["transcript"].encode("utf-8"))
        animation_msg = None
        while True:
            message = ws.receive()
            if message.get("text") is None:
                continue
            data = json.loads(message["text"])
            if data.get("type") == "animation":
                animation_msg = data
            elif data.get("type") == "error":
                raise RuntimeError(f"server error: {data.get('message')}")
            elif data.get("type") in ("study_turn_done", "transcription_empty"):
                break
        latency = round(time.time() - started, 2)

        new = self._scene_entries(training, scene_key)[before:]
        cycle = next((e for e in new if e["event"] == "pipeline_cycle"), None)
        resolution = next((e for e in new if e["event"] == "resolution"), None)
        selected = (cycle or {}).get("selected") or {}
        errors = (cycle or {}).get("errors_found") or []
        suggestion = (cycle or {}).get("suggestion") or {}

        # What the child will have in front of them at the next recording
        shown = None
        interrupted = False
        if animation_msg:
            if not last and self.rng.random() < self.child.traits["p_interrupt"]:
                ws.send_json({"type": "interrupt"})  # the child speaks before the animation shows
                interrupted = True
            else:
                short = str(selected.get("animation_id") or animation_msg["animation_id"]).split("_")[0]
                if selected.get("source") == "error":
                    rationale = next((e["desc"] for e in errors if e.get("animation_id") == short),
                                     errors[0]["desc"] if errors else "")
                else:
                    rationale = suggestion.get("rationale") or ""
                # `rationale` is kept for the trace only: the child model never reads it
                shown = {
                    "short": short, "targets": selected.get("targets") or [],
                    "pass_type": "correction" if selected.get("source") == "error" else "suggestion",
                    "rationale": rationale, "word": (animation_msg.get("params") or {}).get("word"),
                }

        # Wrong words stick until the child takes up an identity animation
        if plan["kind"] == "uptake" and (ctx.get("animation") or {}).get("short") in ("I1", "I2"):
            ctx["sticky"].clear()
        if "identity" in plan["slips"]:
            ctx["sticky"] += [m["detail"] for m in spoken["mistakes"] if m.get("type") == "identity" and m.get("detail")][:1]

        record = {
            "participant": self.pid, "story": story, "scene_key": scene_key, "turn": turn,
            "condition": "animation" if animated else "control",
            "plan": plan, "reacting_to": ctx.get("animation"),
            "transcript": spoken["transcript"], "child_said": spoken["child_said"], "mistakes": spoken["mistakes"],
            "misheard": spoken["misheard"], "asr": spoken["asr"],
            "pipeline": {
                "cycle_logged": cycle is not None,
                "errors": [e.get("animation_id") for e in errors],
                "suggestion": suggestion.get("animation_id"),
                "selected": selected, "action": (cycle or {}).get("action"),
                "resolution": None if resolution is None else resolution.get("resolved"),
            },
            "animation_sent": bool(animation_msg), "interrupted": interrupted, "latency_s": latency,
        }
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        if plan.get("name"):
            ctx["named"].append(f"{plan['name']} ({', '.join((ctx.get('animation') or {}).get('targets') or [])})")
        # The child remembers what they said, not what the recogniser printed
        ctx["said_in_scene"].append(spoken["child_said"] or spoken["transcript"])
        ctx["last_mistakes"] = [m for m in spoken["mistakes"] if m.get("type") != "grammar"]
        ctx["last_spoken"] = {k: spoken[k] for k in ("transcript", "child_said", "mistakes", "misheard")}
        ctx["first_in_scene"] = False
        ctx["animation"] = shown
        self.turns += 1

    # ------------------------------------------------------------------ run

    def run(self, stories: Optional[List[str]], max_scenes: Optional[int], practice: bool) -> Dict[str, Any]:
        """Run the whole session (optional practice page, then the stories in the profile's order) and return its summary."""
        self._reset_logs()
        started = time.time()
        if practice:
            # Practice page: tutorial (scene 1, nothing recorded) then one practice scene, animations on
            self.run_story("training", True, [1, 2], silent=[1])
        for entry in self.profile["order"]:
            story, animated = entry[:-1], entry.endswith("+")
            if stories and story not in stories:
                continue
            count = 5 if not max_scenes else min(5, max_scenes)
            self.run_story(story, animated, list(range(1, count + 1)))
        return {"participant": self.pid, "turns": self.turns, "seconds": round(time.time() - started),
                "pipeline_usage": self.pipeline_usage, "child_usage": self.child.usage,
                "child_model": self.child_model, "pipeline_model": self.pipeline_model}


def run_participant(profile: Dict[str, Any], out: str, seed: int, child_model: Optional[str],
                    stories: Optional[List[str]], max_scenes: Optional[int], practice: bool) -> Dict[str, Any]:
    """Run one simulated participant; an aborted session is returned as an error record."""
    try:
        return Session(profile, Path(out), seed, child_model).run(stories, max_scenes, practice)
    except Exception as exc:
        logging.getLogger("simulation").exception("simulation aborted")
        return {"participant": profile["id"], "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export(out: Path, results: List[Dict[str, Any]], args: argparse.Namespace) -> None:
    """Sheet-format CSV of every simulated log, plus the run summary."""
    from config.pricing import GEMINI_FALLBACK_PRICE, GEMINI_PRICES

    def cost(model: str, usage: Dict[str, int]) -> float:
        """Cost in dollars of the token usage for a model."""
        price_in, price_out = GEMINI_PRICES.get(model, GEMINI_FALLBACK_PRICE)
        return (usage["tokens_in"] * price_in + usage["tokens_out"] * price_out) / 1e6

    stamp = datetime.now(timezone.utc).isoformat()
    with open(out / "simulated_sessions_export.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "participant_id", "data_type", "json_data"])
        for pdir in sorted((out / "users").glob(f"{PREFIX}*")):
            for name, dtype in (("training_log.json", "training_log"), ("study_log.json", "study_log")):
                path = pdir / name
                if path.exists() and json.loads(path.read_text())["scenes"]:
                    writer.writerow([stamp, pdir.name, dtype, path.read_text(encoding="utf-8")])

    for r in results:
        if "error" not in r:
            r["cost_usd"] = round(cost(r["pipeline_model"], r["pipeline_usage"]) + cost(r["child_model"], r["child_usage"]), 3)
    # A re-run of some profiles replaces their entries and keeps the others
    summary_path = out / "summary.json"
    merged = {r["participant"]: r for r in json.loads(summary_path.read_text())["participants"]} if summary_path.exists() else {}
    merged.update({r["participant"]: r for r in results})
    participants = [merged[k] for k in sorted(merged)]
    summary = {"simulated": True, "generated_at": stamp, "seed": args.seed,
               "note": "Simulated narrators run through the real study pipeline. Not participant data.",
               "total_cost_usd": round(sum(r.get("cost_usd", 0) for r in participants), 2),
               "participants": participants}
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "README.md").write_text(
        "# Simulated sessions\n\n"
        "Everything in this folder was produced by `scripts/simulation/simulate_sessions.py`: "
        "simulated narrators (profiles in `scripts/simulation/profiles.json`) run through the real study pipeline. "
        "**No child took part in these sessions.** Ids start with `SIM-`, every log carries `\"simulated\": true`, "
        "and the timestamps are those of the simulation run.\n\n"
        "How often a simulated child acts on an animation is set by its profile (`uptake_*`), "
        "so differences between animated and baseline stories here say nothing about the animations' effect. "
        "Mis-heard words, missed starts and cut ends are added on purpose (`asr` in the profile); "
        "`trace/` keeps what the child said next to what the recogniser printed.\n",
        encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> None:
    """Command-line entry point: simulate the selected profiles in parallel and write the outputs."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", nargs="*", help="profile ids to run (default: all)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--child-model", default=None, help="Gemini model writing the child's recordings (default: the Pro model)")
    ap.add_argument("--stories", nargs="*", help="restrict to these stories (A B C D), for a trial run")
    ap.add_argument("--scenes", type=int, default=None, help="scenes per story, for a trial run")
    ap.add_argument("--practice", action="store_true",
                    help="also simulate the practice page (off by default: only the four stories are simulated)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)

    profiles = [p for p in load_profiles() if not args.only or p["id"] in args.only]
    if not profiles:
        sys.exit("no matching profile")
    args.out.mkdir(parents=True, exist_ok=True)
    os.chdir(REPO)

    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=min(args.workers, len(profiles))) as pool:
        futures = [pool.submit(run_participant, p, str(args.out), args.seed, args.child_model,
                               args.stories, args.scenes, args.practice) for p in profiles]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps({k: v for k, v in result.items() if k in ("participant", "turns", "seconds", "error")}), flush=True)

    results.sort(key=lambda r: r["participant"])
    sys.path.insert(0, str(REPO))
    export(args.out, results, args)
    failed = [r["participant"] for r in results if "error" in r]
    print(f"done -> {args.out}" + (f" | FAILED: {failed}" if failed else ""))


if __name__ == "__main__":
    main()
