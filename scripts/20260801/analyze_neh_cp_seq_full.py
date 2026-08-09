"""Analyze the full-grid NEH-CP sequence-source run.

Doc: ``plans/analysis/20260801/neh_cp_seq_source_full.md``.
Config under test: ``metadata/20260731/neh_cp_seq_source_compare.yaml``.
Pilot predecessor: ``plans/analysis/20260731/neh_cp_seq_source_pilot.md``.

The 3-instance pilot could not rank the sequence modes — the mode effect
(2.60-5.13 pp) was smaller than the per-instance CP-SAT noise it measured with
the ``bottleneck``/``first_stage`` near-replicate pair (up to 7.80 pp). This
script answers the same questions on the 1440-instance grid, where 1440 paired
observations shrink the standard error of a mean difference by ~38x, so the
verdict rests on a paired CI rather than on a noise proxy (``bottleneck`` was
dropped from the config after the pilot).

Blocks:

1. Integrity and effort homogeneity — every scenario must cover the grid, and
   the NEH-CP budget must bind equally everywhere, else a mode difference is
   really an effort difference.
2. Scenario ranking — mean/median RPDf.
3. Mode effect inside each seeding prefix — the experiment's primary question.
   Paired mean difference with a 95 % CI and win/tie/loss.
4. Seeding-prefix effect at a fixed mode, plus every scenario against the
   baseline.
5. (T, R) and (n, c) cell decomposition — is a mode verdict global or local?
6. Rank stability of the modes across the 9 (T, R) cells.
7. Oracle mode portfolios inside each family — how complementary are the modes?
   Upper bounds at k x the budget, scored with ``analyze_dispatch_sweep``'s own
   ``oracle_value`` so they sit on the same footing as the other sweeps.

Usage:
    uv run python scripts/20260801/analyze_neh_cp_seq_full.py <run_dir>
"""

from __future__ import annotations

import argparse
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "20260731"))
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # analyze_dispatch_sweep
from analyze_dispatch_sweep import oracle_value  # noqa: E402  -- shared oracle
from analyze_neh_cp_seq_pilot import load_rpdf  # noqa: E402  -- shared loader

MODES = ("midpoint", "first_stage", "completion")
PREFIXES = {
    "neh_cp": "mcf_lb->fmm",
    "dv4_neh_cp": "dispatch_v4",
    "dv4_mcf_fmm_neh_cp": "dv4->mcf_lb->fmm",
}
BASELINE = "neh_cp_baseline"
GRID_SIZE = 1440
TIE_TOL = 1e-9
OUT_DIR = Path("analysis/20260801_neh_cp_seq_full")


def scenario_name(prefix: str, mode: str) -> str:
    return f"{prefix}_{mode}_seq"


def to_wide(df: pd.DataFrame, value: str = "rpdf") -> pd.DataFrame:
    return df.pivot(index="insIndex", columns="scenarioName", values=value)


def paired(wide: pd.DataFrame, a: str, b: str) -> dict[str, object]:
    """Paired ``a`` minus ``b``; negative mean_diff means ``a`` is better."""
    d = (wide[a] - wide[b]).dropna()
    n = len(d)
    sd = d.std(ddof=1)
    se = sd / np.sqrt(n) if n else np.nan
    return {
        "a": a,
        "b": b,
        "n_paired": n,
        "mean_diff": d.mean(),
        "ci95": 1.96 * se,
        "sigma": d.mean() / se if se else np.nan,
        "win": int((d < -TIE_TOL).sum()),
        "tie": int((d.abs() <= TIE_TOL).sum()),
        "loss": int((d > TIE_TOL).sum()),
    }


def fmt_paired(rows: list[dict[str, object]]) -> pd.DataFrame:
    out = pd.DataFrame(rows)
    out["verdict"] = np.where(
        out["ci95"].abs() >= out["mean_diff"].abs(),
        "indistinguishable",
        np.where(out["mean_diff"] < 0, "a better", "b better"),
    )
    return out


def block1_integrity(df: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 1 - integrity and effort ===")
    g = df.groupby("scenarioName")
    eff = pd.DataFrame(
        {
            "rows": g.size(),
            "missing_obj": g["bestObj"].apply(lambda s: int(s.isna().sum())),
            "mean_elapsed": g["elapsedTime"].mean(),
            "mean_time_pct": g["time%"].mean() * 100.0,
            "max_time_pct": g["time%"].max() * 100.0,
        }
    ).sort_index()
    print(eff.round(3).to_string())
    bad = eff.index[eff["rows"] != GRID_SIZE].tolist()
    print(f"\nscenarios not covering the {GRID_SIZE}-instance grid: {bad or 'none'}")
    spread = eff["mean_elapsed"].max() - eff["mean_elapsed"].min()
    print(
        f"mean elapsed spread across scenarios: {spread:.2f} s "
        f"({eff['mean_elapsed'].min():.2f}-{eff['mean_elapsed'].max():.2f} s)"
    )
    return eff


def block2_ranking(df: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 2 - scenario ranking (mean RPDf %) ===")
    g = df.groupby("scenarioName")["rpdf"]
    rank = pd.DataFrame(
        {"mean": g.mean(), "median": g.median(), "std": g.std(), "n": g.size()}
    ).sort_values("mean")
    print(rank.round(3).to_string())
    return rank


def block3_mode_effect(wide: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 3 - mode effect inside each seeding prefix ===")
    rows = []
    for prefix, label in PREFIXES.items():
        for m_a, m_b in combinations(MODES, 2):
            row = paired(wide, scenario_name(prefix, m_a), scenario_name(prefix, m_b))
            row["prefix"] = label
            row["pair"] = f"{m_a} - {m_b}"
            rows.append(row)
    out = fmt_paired(rows)
    cols = [
        "prefix",
        "pair",
        "mean_diff",
        "ci95",
        "sigma",
        "win",
        "tie",
        "loss",
        "verdict",
    ]
    print(out[cols].round(3).to_string(index=False))
    return out


def block4_prefix_effect(wide: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 4a - seeding-prefix effect at a fixed mode ===")
    rows = []
    for mode in MODES:
        for p_a, p_b in combinations(PREFIXES, 2):
            row = paired(wide, scenario_name(p_a, mode), scenario_name(p_b, mode))
            row["mode"] = mode
            row["pair"] = f"{PREFIXES[p_a]} - {PREFIXES[p_b]}"
            rows.append(row)
    out = fmt_paired(rows)
    cols = [
        "mode",
        "pair",
        "mean_diff",
        "ci95",
        "sigma",
        "win",
        "tie",
        "loss",
        "verdict",
    ]
    print(out[cols].round(3).to_string(index=False))

    print("\n=== Block 4b - every scenario against the baseline ===")
    base_rows = [
        paired(wide, col, BASELINE) for col in sorted(wide.columns) if col != BASELINE
    ]
    base = fmt_paired(base_rows)
    print(
        base[["a", "mean_diff", "ci95", "sigma", "win", "tie", "loss", "verdict"]]
        .round(3)
        .to_string(index=False)
    )
    return pd.concat([out, base], ignore_index=True)


def block5_cells(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    print("\n=== Block 5a - (T, R) cells: mean RPDf % per scenario ===")
    tr = df.pivot_table(
        index=["T", "R"], columns="scenarioName", values="rpdf", aggfunc="mean"
    )
    tr["winner"] = tr.idxmin(axis=1)
    print(tr.round(2).to_string())

    print("\n=== Block 5b - (n, c) cells: mean RPDf % per scenario ===")
    nc = df.pivot_table(
        index=["n", "c"], columns="scenarioName", values="rpdf", aggfunc="mean"
    )
    nc["winner"] = nc.idxmin(axis=1)
    print(nc.round(2).to_string())
    return tr, nc


def block6_rank_stability(df: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 6 - mode rank stability across the 9 (T, R) cells ===")
    rows = []
    cells = df.pivot_table(
        index=["T", "R"], columns="scenarioName", values="rpdf", aggfunc="mean"
    )
    for prefix, label in PREFIXES.items():
        cols = [scenario_name(prefix, m) for m in MODES]
        ranks = cells[cols].rank(axis=1)
        ranks.columns = MODES
        for mode in MODES:
            rows.append(
                {
                    "prefix": label,
                    "mode": mode,
                    "mean_rank": ranks[mode].mean(),
                    "best_cells": int((ranks[mode] == 1).sum()),
                    "worst_cells": int((ranks[mode] == len(MODES)).sum()),
                }
            )
    out = pd.DataFrame(rows)
    print(out.round(2).to_string(index=False))
    return out


def oracle_table(mat: pd.DataFrame, family: str) -> pd.DataFrame:
    """Oracle mean of every mode subset of one family, plus winner counts.

    ``mat`` is an [instance x mode] matrix already restricted to one seeding
    family. The oracle mean of a subset is the mean over instances of the
    per-instance best, i.e. an **upper bound bought with k x the budget** — not
    a runnable configuration. Winner counts and the all-tie count say how much
    of the gain is real complementarity.
    """
    best_single = float(mat.mean().min())
    # idxmin credits the leftmost column on a tie, so strict wins (a unique
    # minimum) are counted instead — the tie mass here is large enough that the
    # naive count is really a column-order artifact.
    is_min = mat.eq(mat.min(axis=1), axis=0)
    unique = is_min.sum(axis=1) == 1
    strict = mat[unique].idxmin(axis=1).value_counts()
    all_tie = int((is_min.sum(axis=1) == len(mat.columns)).sum())
    rows = []
    for k in (1, 2, len(mat.columns)):
        for combo in combinations(mat.columns, k):
            value = oracle_value(mat, combo)
            rows.append(
                {
                    "family": family,
                    "k": k,
                    "combo": " + ".join(combo),
                    "oracle_mean": value,
                    "gain_vs_best_single": value - best_single,
                    "strict_wins": int(strict.get(combo[0], 0)) if k == 1 else None,
                    "tied_instances": int((~unique).sum()) if k == 1 else None,
                    "all_tie_instances": all_tie if k == 1 else None,
                }
            )
    return pd.DataFrame(rows).drop_duplicates(subset=["family", "combo"])


def block7_oracle(wide: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 7 - oracle mode portfolios inside each prefix family ===")
    frames = []
    for prefix, label in PREFIXES.items():
        mat = wide[[scenario_name(prefix, m) for m in MODES]].dropna()
        mat.columns = list(MODES)
        frames.append(oracle_table(mat, label))
    out = pd.concat(frames, ignore_index=True).sort_values(
        ["family", "oracle_mean"], ascending=[True, True]
    )
    print(out.round(3).to_string(index=False))
    print(
        "\nan oracle over k modes costs k x the NEH budget - read these as upper "
        "bounds, not as runnable configurations"
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    df = load_rpdf(args.run_dir)
    wide = to_wide(df)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    eff = block1_integrity(df)
    rank = block2_ranking(df)
    modes = block3_mode_effect(wide)
    prefixes = block4_prefix_effect(wide)
    tr, nc = block5_cells(df)
    stability = block6_rank_stability(df)
    oracle = block7_oracle(wide)

    for name, frame in [
        ("effort.csv", eff),
        ("scenario_ranking.csv", rank),
        ("mode_effect.csv", modes),
        ("prefix_effect.csv", prefixes),
        ("tr_cells.csv", tr),
        ("nc_cells.csv", nc),
        ("rank_stability.csv", stability),
        ("oracle_portfolios.csv", oracle),
    ]:
        frame.to_csv(args.out_dir / name)
    print(f"\nwrote 8 CSVs to {args.out_dir}")


if __name__ == "__main__":
    main()
