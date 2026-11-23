"""Analyse the blind resolution re-coding and print the numbers for Section 6.3.

Usage:
    python analyze_coding.py coding/coder_A.csv coding/coder_B.csv coding/key.csv

Coder sheets: resolved in {1, 0, y, n, yes, no}; condition_guess in
{animation, control, a, c}. A gap counts as resolved when both coders marked it
resolved (the rule stated in the paper).
"""

import sys

import pandas as pd
from scipy.stats import wilcoxon

YES = {"1", "y", "yes", "true", "resolved"}
NO = {"0", "n", "no", "false", "unresolved"}
ANIM = {"animation", "a", "anim"}
CTRL = {"control", "c", "ctrl"}


def to_bin(series, pos, neg, name):
    s = series.astype(str).str.strip().str.lower()
    bad = sorted(set(s) - pos - neg)
    if bad:
        raise SystemExit(f"{name}: unexpected values {bad}")
    return s.isin(pos).astype(int)


def kappa(a, b):
    a, b = pd.Series(a).astype(int), pd.Series(b).astype(int)
    po = (a == b).mean()
    pe = a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean())
    return po, (po - pe) / (1 - pe) if pe < 1 else float("nan")


def main(path_a, path_b, path_key):
    a = pd.read_csv(path_a, dtype=str)
    b = pd.read_csv(path_b, dtype=str)
    key = pd.read_csv(path_key)

    for name, d in (("coder A", a), ("coder B", b)):
        empty = d["resolved"].isna().sum() + d["condition_guess"].isna().sum()
        if empty:
            raise SystemExit(f"{name}: {empty} empty cells, finish coding first")

    a = a.assign(res_a=to_bin(a.resolved, YES, NO, "A.resolved"),
                 guess_a=to_bin(a.condition_guess, ANIM, CTRL, "A.condition_guess"))
    b = b.assign(res_b=to_bin(b.resolved, YES, NO, "B.resolved"),
                 guess_b=to_bin(b.condition_guess, ANIM, CTRL, "B.condition_guess"))
    d = key.merge(a[["item_id", "res_a", "guess_a"]], on="item_id") \
           .merge(b[["item_id", "res_b", "guess_b"]], on="item_id")
    if len(d) != len(key):
        raise SystemExit(f"{len(key) - len(d)} items missing from the coder sheets")

    d["anim"] = (d.condition == "animation").astype(int)
    d["human"] = d.res_a & d.res_b
    d["llm"] = d.llm_resolved.astype(int)

    po, k = kappa(d.res_a, d.res_b)
    print(f"Inter-coder agreement: {po:.1%} (Cohen's kappa = {k:.2f}), n = {len(d)}")

    # Blinding check: guesses against the actual condition.
    for c in ("a", "b"):
        po, k = kappa(d[f"guess_{c}"], d.anim)
        print(f"Coder {c.upper()} condition guesses: {po:.1%} correct "
              f"(kappa with actual condition = {k:.2f})")
    pooled = pd.concat([d.guess_a, d.guess_b]), pd.concat([d.anim, d.anim])
    po, k = kappa(*pooled)
    print(f"Pooled condition guesses: {po:.1%} correct (kappa = {k:.2f})")

    # Resolution by condition, human-coded.
    for cond in ("animation", "control"):
        s = d[d.condition == cond]
        print(f"{cond}: {s.human.sum()} of {len(s)} gaps resolved "
              f"({s.human.mean():.0%}) by the coders, "
              f"{s.llm.sum()} ({s.llm.mean():.0%}) by the pipeline")

    # Paired test on the children who completed both conditions.
    per = d.groupby(["child", "condition"]).human.mean().unstack()
    per = per.dropna()
    diff = per["animation"] - per["control"]
    stat, p = wilcoxon(per["animation"], per["control"])
    print(f"{(diff > 0).sum()} of {len(per)} children resolved a larger share with "
          f"animations, {(diff == 0).sum()} tie(s): Wilcoxon W = {stat:g}, p = {p:.3f}")

    # Coders (consensus) against the pipeline.
    po, k = kappa(d.human, d.llm)
    print(f"Coders vs. pipeline: {po:.1%} agreement (kappa = {k:.2f})")
    print("  pipeline resolved, coders not:", int(((d.llm == 1) & (d.human == 0)).sum()),
          "| coders resolved, pipeline not:", int(((d.llm == 0) & (d.human == 1)).sum()))

    # Displayed vs. interrupted animations.
    if "interrupted" in d.columns:
        s = d[d.condition == "animation"]
        for flag, label in ((0, "displayed"), (1, "interrupted")):
            t = s[s.interrupted.astype(int) == flag]
            if len(t):
                print(f"animation, {label}: {t.human.sum()} of {len(t)} ({t.human.mean():.0%})")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
