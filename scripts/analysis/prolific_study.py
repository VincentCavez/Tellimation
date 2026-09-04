#!/usr/bin/env python3
"""Analysis of the Prolific interpretability studies (Study 1 and Study 2).

Inputs are the Google Sheets exports (`responses_block1`, `responses_block2`);
the delimiter is sniffed so `;` exports load too.

Study 2 analyses (spec, "Plan d'analyse"):
  1. accuracy (share of T) per target animation, overall and per condition,
     Wilson 95% CI, vs. the Study 1 baseline of the replaced animation
     (two-proportion z test + Fisher exact)
  2. partial credit in the correction condition (T / E1-E2 / D1-D2)
  3. item analysis per scene (A-D)
  4. fillers P2f / C2 / T1: Study 2 vs Study 1
  5. Likert (block 2): mean and distribution per animation, vs Study 1
  6. response_time_ms and video_play_count (medians)

Usage:
    python3 -m scripts.analysis.prolific_study \\
        --s1-block1 s1_b1.csv --s1-block2 s1_b2.csv \\
        [--s2-block1 s2_b1.csv --s2-block2 s2_b2.csv] [--out data/study2/analysis]

With only the Study 1 files, prints the Study 1 baselines. Duplicate rows
(same participant, same stimulus) are dropped, first occurrence kept.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from scipy.stats import fisher_exact, norm

TARGET_ANIMATIONS = ["I2", "C3", "S1"]
FILLER_ANIMATIONS = ["P2f", "C2", "T1"]
SCENES = ["A", "B", "C", "D"]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(f, dialect=dialect))
    # The survey re-sends queued responses after a network hiccup: keep the
    # first row per (participant, stimulus).
    seen: set[tuple[str, str]] = set()
    deduped = []
    for r in rows:
        key = (r.get("prolific_id", ""), r.get("stimulus_id", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    if len(deduped) != len(rows):
        print(f"{path.name}: dropped {len(rows) - len(deduped)} duplicate rows")
    rows = deduped
    for r in rows:
        for k in ("response_time_ms", "video_play_count", "likert_rating", "slot", "block"):
            if k in r and r[k] not in (None, ""):
                try:
                    r[k] = int(float(r[k]))
                except ValueError:
                    pass
    return rows


def scene_letter(row: dict) -> str:
    return row["scene_id"].rsplit("_", 1)[-1]


def study_of(row: dict) -> str:
    return row["stimulus_id"].split("_", 1)[0]  # study1 / study2


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float, float]:
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, centre - half, centre + half


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    if min(n1, n2) == 0:
        return float("nan"), float("nan")
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (k1 / n1 - k2 / n2) / se
    return z, 2 * (1 - norm.cdf(abs(z)))


def fisher(k1: int, n1: int, k2: int, n2: int) -> float:
    if min(n1, n2) == 0:
        return float("nan")
    return float(fisher_exact([[k1, n1 - k1], [k2, n2 - k2]])[1])


def fmt(x: float, nd: int = 2) -> str:
    return "nan" if x != x else f"{x:.{nd}f}"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def accuracy(rows: list[dict]) -> tuple[int, int]:
    return sum(r["selected_option_code"] == "T" for r in rows), len(rows)


def by(rows: list[dict], key) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def acc_row(label: str, rows: list[dict]) -> dict:
    k, n = accuracy(rows)
    p, lo, hi = wilson(k, n)
    return {"label": label, "k": k, "n": n, "acc": p, "ci_lo": lo, "ci_hi": hi}


def compare(label: str, rows_new: list[dict], rows_base: list[dict]) -> dict:
    k1, n1 = accuracy(rows_new)
    k2, n2 = accuracy(rows_base)
    z, pz = two_proportion_z(k1, n1, k2, n2)
    return {
        "label": label,
        "k_s2": k1, "n_s2": n1, "acc_s2": k1 / n1 if n1 else float("nan"),
        "k_s1": k2, "n_s1": n2, "acc_s1": k2 / n2 if n2 else float("nan"),
        "z": z, "p_z": pz, "p_fisher": fisher(k1, n1, k2, n2),
    }


def print_table(title: str, rows: list[dict], cols: list[str]) -> None:
    print(f"\n### {title}\n")
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c, "")
            cells.append(fmt(v, 3) if isinstance(v, float) else str(v))
        print("| " + " | ".join(cells) + " |")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------

def analyse_block1(s1: list[dict], s2: list[dict] | None, out: Path | None) -> None:
    s1_by_anim = by(s1, lambda r: r["animation_id"])

    # Baselines (Study 1)
    base = [acc_row(a, s1_by_anim.get(a, [])) for a in TARGET_ANIMATIONS + FILLER_ANIMATIONS]
    for a in TARGET_ANIMATIONS + FILLER_ANIMATIONS:
        for cond in ("correction", "suggestion"):
            base.append(acc_row(f"{a}/{cond}", [r for r in s1_by_anim.get(a, []) if r["condition"] == cond]))
    print_table("Study 1 baselines (block 1 accuracy, Wilson 95% CI)", base, ["label", "k", "n", "acc", "ci_lo", "ci_hi"])
    if out:
        write_csv(out / "study1_baselines.csv", base)
    if s2 is None:
        return

    s2_targets = [r for r in s2 if study_of(r) == "study2"]
    s2_fillers = [r for r in s2 if study_of(r) == "study1"]
    s2_by_anim = by(s2_targets, lambda r: r["animation_id"])

    # 1. accuracy per target animation, overall and per condition, vs Study 1
    acc = []
    cmp_rows = []
    for a in TARGET_ANIMATIONS:
        rows = s2_by_anim.get(a, [])
        acc.append(acc_row(a, rows))
        cmp_rows.append(compare(a, rows, s1_by_anim.get(a, [])))
        for cond in ("correction", "suggestion"):
            sub = [r for r in rows if r["condition"] == cond]
            acc.append(acc_row(f"{a}/{cond}", sub))
            cmp_rows.append(compare(f"{a}/{cond}", sub, [r for r in s1_by_anim.get(a, []) if r["condition"] == cond]))
    print_table("1. Study 2 accuracy per target animation (Wilson 95% CI)", acc, ["label", "k", "n", "acc", "ci_lo", "ci_hi"])
    print_table("1. Study 2 vs Study 1 (two-proportion z, Fisher exact)", cmp_rows,
                ["label", "acc_s2", "n_s2", "acc_s1", "n_s1", "z", "p_z", "p_fisher"])

    # 2. partial credit, correction condition
    credit = []
    for study_name, rows in (("study2", s2_targets), ("study1", [r for r in s1 if r["animation_id"] in TARGET_ANIMATIONS])):
        for a in TARGET_ANIMATIONS:
            sub = [r for r in rows if r["animation_id"] == a and r["condition"] == "correction"]
            c = Counter(r["selected_option_code"] for r in sub)
            n = len(sub)
            credit.append({
                "study": study_name, "animation": a, "n": n,
                "T": c["T"], "E": c["E1"] + c["E2"], "D": c["D1"] + c["D2"],
                "share_T": c["T"] / n if n else float("nan"),
                "share_E": (c["E1"] + c["E2"]) / n if n else float("nan"),
                "share_D": (c["D1"] + c["D2"]) / n if n else float("nan"),
            })
    print_table("2. Partial credit, correction condition (T / E1+E2 / D1+D2)", credit,
                ["study", "animation", "n", "T", "E", "D", "share_T", "share_E", "share_D"])

    # 3. item analysis per scene
    items = []
    for a in TARGET_ANIMATIONS:
        for sc in SCENES:
            sub = [r for r in s2_by_anim.get(a, []) if scene_letter(r) == sc]
            row = acc_row(f"{a}_{sc}", sub)
            k1, n1 = accuracy([r for r in s1_by_anim.get(a, []) if scene_letter(r) == sc])
            row["acc_s1"] = k1 / n1 if n1 else float("nan")
            row["n_s1"] = n1
            items.append(row)
    print_table("3. Item analysis per scene (Study 2, with Study 1 scene accuracy)", items,
                ["label", "k", "n", "acc", "ci_lo", "ci_hi", "acc_s1", "n_s1"])

    # 4. fillers
    fill = []
    s2f_by_anim = by(s2_fillers, lambda r: r["animation_id"])
    for a in FILLER_ANIMATIONS:
        fill.append(compare(a, s2f_by_anim.get(a, []), s1_by_anim.get(a, [])))
        # same stimuli only (the exact stimulus ids used in Study 2)
        used = {r["stimulus_id"] for r in s2f_by_anim.get(a, [])}
        fill.append(compare(f"{a} (same stimuli)", s2f_by_anim.get(a, []),
                            [r for r in s1_by_anim.get(a, []) if r["stimulus_id"] in used]))
    print_table("4. Fillers: Study 2 vs Study 1", fill,
                ["label", "acc_s2", "n_s2", "acc_s1", "n_s1", "z", "p_z", "p_fisher"])

    # 6. secondary variables
    sec = []
    for study_name, rows in (("study2 targets", s2_targets), ("study2 fillers", s2_fillers), ("study1 all", s1)):
        rt = [r["response_time_ms"] for r in rows if isinstance(r.get("response_time_ms"), int)]
        vp = [r["video_play_count"] for r in rows if isinstance(r.get("video_play_count"), int)]
        sec.append({"rows": study_name, "n": len(rows),
                    "rt_median_ms": median(rt) if rt else float("nan"),
                    "rt_mean_ms": mean(rt) if rt else float("nan"),
                    "plays_median": median(vp) if vp else float("nan"),
                    "plays_mean": mean(vp) if vp else float("nan")})
    for a in TARGET_ANIMATIONS:
        rows = s2_by_anim.get(a, [])
        rt = [r["response_time_ms"] for r in rows if isinstance(r.get("response_time_ms"), int)]
        vp = [r["video_play_count"] for r in rows if isinstance(r.get("video_play_count"), int)]
        sec.append({"rows": f"study2 {a}", "n": len(rows),
                    "rt_median_ms": median(rt) if rt else float("nan"),
                    "rt_mean_ms": mean(rt) if rt else float("nan"),
                    "plays_median": median(vp) if vp else float("nan"),
                    "plays_mean": mean(vp) if vp else float("nan")})
    print_table("6. Response time and video plays", sec, ["rows", "n", "rt_median_ms", "rt_mean_ms", "plays_median", "plays_mean"])

    # participants and completeness
    per_pid = Counter(r["prolific_id"] for r in s2)
    print(f"\nStudy 2 block 1: {len(per_pid)} participants, "
          f"{sum(1 for v in per_pid.values() if v == 12)} with 12 responses, "
          f"{len(s2)} rows.")

    if out:
        write_csv(out / "study2_accuracy.csv", acc)
        write_csv(out / "study2_vs_study1.csv", cmp_rows)
        write_csv(out / "study2_partial_credit.csv", credit)
        write_csv(out / "study2_items.csv", items)
        write_csv(out / "study2_fillers.csv", fill)
        write_csv(out / "study2_secondary.csv", sec)


def likert_summary(label: str, rows: list[dict]) -> dict:
    ratings = [r["likert_rating"] for r in rows if isinstance(r.get("likert_rating"), int)]
    dist = Counter(ratings)
    return {"label": label, "n": len(ratings),
            "mean": mean(ratings) if ratings else float("nan"),
            "median": median(ratings) if ratings else float("nan"),
            **{f"r{v}": dist[v] for v in range(1, 6)}}


def analyse_block2(s1: list[dict], s2: list[dict] | None, out: Path | None) -> None:
    rows = []
    s1_by_anim = by(s1, lambda r: r["animation_id"])
    for a in TARGET_ANIMATIONS:
        rows.append(likert_summary(f"study1 {a}", s1_by_anim.get(a, [])))
        for cond in ("correction", "suggestion"):
            rows.append(likert_summary(f"study1 {a}/{cond}", [r for r in s1_by_anim.get(a, []) if r["condition"] == cond]))
    if s2 is not None:
        s2_by_anim = by(s2, lambda r: r["animation_id"])
        for a in TARGET_ANIMATIONS:
            rows.append(likert_summary(f"study2 {a}", s2_by_anim.get(a, [])))
            for cond in ("correction", "suggestion"):
                rows.append(likert_summary(f"study2 {a}/{cond}", [r for r in s2_by_anim.get(a, []) if r["condition"] == cond]))
        per_pid = Counter(r["prolific_id"] for r in s2)
        print(f"\nStudy 2 block 2: {len(per_pid)} participants, {len(s2)} rows.")
    print_table("5. Likert (block 2): mean, median, distribution 1-5", rows,
                ["label", "n", "mean", "median", "r1", "r2", "r3", "r4", "r5"])
    if out:
        write_csv(out / "likert.csv", rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--s1-block1", type=Path, required=True)
    parser.add_argument("--s1-block2", type=Path, required=True)
    parser.add_argument("--s2-block1", type=Path)
    parser.add_argument("--s2-block2", type=Path)
    parser.add_argument("--out", type=Path, default=None, help="directory for CSV outputs")
    args = parser.parse_args()

    s1_b1 = load_csv(args.s1_block1.expanduser())
    s1_b2 = load_csv(args.s1_block2.expanduser())
    s2_b1 = load_csv(args.s2_block1.expanduser()) if args.s2_block1 else None
    s2_b2 = load_csv(args.s2_block2.expanduser()) if args.s2_block2 else None
    print(f"Study 1: {len(s1_b1)} block-1 rows, {len(s1_b2)} block-2 rows"
          + (f"; Study 2: {len(s2_b1)} block-1 rows, {len(s2_b2) if s2_b2 else 0} block-2 rows" if s2_b1 else ""))

    analyse_block1(s1_b1, s2_b1, args.out)
    analyse_block2(s1_b2, s2_b2, args.out)


if __name__ == "__main__":
    main()
