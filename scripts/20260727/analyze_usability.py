"""Analyze the CSR usability sweep: Q2 (3-mode reconstruct) & Q3 (vs m1_k1).

Plan: ``plans/experiment/20260727/csr_usability_budget_arm_sweep.md`` §4.

Q2 answers: at each (tau, f), what is the RPDf gap between msemi/mactive and m1
(last-semi)? Emits a per-k dRPDf-vs-f curve (the gap's budget dependence).

Q3 answers: is any of the 139 scenarios better than the uncoarsened full-inner-
flow ``m1_k1`` baseline at equal budget? Emits a paired table and a (mean elapsed,
mean RPDf) scatter with the ``m1_k1`` reference curve.

Imports ``load_run`` from ``analyze_crossover_ladder.py`` so the RPDf and pairing
definitions cannot drift from the 1급 게이트 script.

Usage:
    uv run python scripts/20260727/analyze_usability.py <run_dir> \
        [--outdir analysis/<run_id>_usability]

Outputs (under --outdir):
    q2_recon_mode_f_curve.csv    one row per (f, k, pair): dRPDf + win/tie/loss
    q2_recon_mode_f_curve.png    dRPDf vs f, one line per (pair, k)
    q3_all_vs_m1k1.csv           one row per scenario: dRPDf* vs m1_k1 baseline
    q3_scatter.png                (mean elapsed, mean RPDf) scatter + m1_k1 curve
    elapsed_by_scenario.csv       budget parity: mean elapsed per (arm, f, k)
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# scripts/20260727/<this file> -- two levels of nesting below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, relpath: str):
    """scripts/ is not an importable package; load a sibling module by path."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ladder = _load_module(
    "analyze_crossover_ladder", "scripts/20260726/analyze_crossover_ladder.py"
)

COARSEN_MODE = "round"  # the sweep's rounding rule; `ceil` appears only as a probe
# Pair name -> (arm to compare against m1, label)
RECON_PAIRS: dict[str, tuple[str, str]] = {
    "semi_vs_lastsemi": ("msemi", "semi"),
    "active_vs_lastsemi": ("mactive", "active"),
}

RULE = "=" * 78


# -- Q2: reconstruct 3-mode comparison -------------------------------------------------


def _q2_paired(df: pd.DataFrame, f: int, k: int, arm_a: str, arm_b: str) -> dict | None:
    """Per-instance paired dRPDf for ``arm_a - arm_b`` at a single (f, k) cell."""
    sub = df[df["f"] == f]
    sub = sub[sub["k"] == k]

    # One scenario per arm is required: pivot_table would silently average two.
    per_arm = sub.groupby("arm")["scenarioName"].nunique()
    dupes = per_arm[per_arm > 1]
    if not dupes.empty:
        raise SystemExit(
            f"f={f} k={k}: arms {list(dupes.index)} have >1 scenario "
            f"({sorted(sub['scenarioName'].unique())}) -- filter them first"
        )

    wide = sub.pivot_table(index="insIndex", columns="arm", values="rpdf_pct")
    for a in (arm_a, arm_b):
        if a not in wide.columns:
            return None
    pair = wide[[arm_a, arm_b]].dropna()
    delta = pair[arm_a] - pair[arm_b]
    a_nan = wide[arm_a].isna()
    b_nan = wide[arm_b].isna()
    return {
        "n_paired": len(pair),
        "a_only_feasible": int((~a_nan & b_nan).sum()),
        "b_only_feasible": int((a_nan & ~b_nan).sum()),
        "a_rpdf_pct": float(pair[arm_a].mean()),
        "b_rpdf_pct": float(pair[arm_b].mean()),
        "mean_drpdf_pp": float(delta.mean()),
        "win": int((delta < 0).sum()),  # arm_a wins (lower RPDf)
        "tie": int((delta == 0).sum()),
        "loss": int((delta > 0).sum()),
    }


def q2_recon_comparison(df: pd.DataFrame, outdir: Path) -> pd.DataFrame:
    """Per (f, k): pair msemi / mactive against m1 (last-semi).

    dRPDf = msemi - m1  or  mactive - m1.  Positive → last-semi wins.
    """
    recon_arms = {"m1", "msemi", "mactive"}
    sub = df[df["arm"].isin(recon_arms)]
    # The ceil probe (m1_k{2,4,8}_ceil_f40) also lives in the m1 arm. Keeping it
    # would give the m1 arm two scenarios at (f=40, k>1) and the pivot would
    # average round and ceil into one "last-semi" column -- a silently wrong
    # reference. The reconstruct comparison is a round-only question (plan §1.1).
    sub = sub[sub["mode"].isna() | (sub["mode"] == COARSEN_MODE)]

    rows: list[dict] = []
    for f in sorted(sub["f"].dropna().unique()):
        f_i = int(f)
        for k in sorted(sub[sub["f"] == f_i]["k"].unique()):
            k_i = int(k)
            for pair_name, (arm_cmp, _label) in RECON_PAIRS.items():
                r = _q2_paired(sub, f_i, k_i, arm_cmp, "m1")
                if r is None:
                    continue
                rows.append({"f": f_i, "k": k_i, "pair": pair_name, **r})

    if not rows:
        # No msemi/mactive arms in this run (e.g. pointed at the ladder run).
        result = pd.DataFrame(
            columns=[
                "f",
                "k",
                "pair",
                "n_paired",
                "mean_drpdf_pp",
                "win",
                "tie",
                "loss",
            ]
        )
    else:
        result = pd.DataFrame(rows).sort_values(["pair", "f", "k"])
    result.to_csv(outdir / "q2_recon_mode_f_curve.csv", index=False)
    return result


def plot_q2(q2: pd.DataFrame, outdir: Path) -> None:
    """dRPDf vs f, one subplot per k, two lines (semi / active vs last-semi)."""
    if q2.empty:
        return
    ks = sorted(q2["k"].unique())
    ncols = min(4, len(ks))
    nrows = int(np.ceil(len(ks) / ncols))
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(4.2 * ncols, 3.8 * nrows), squeeze=False
    )
    colors = {"semi_vs_lastsemi": "#0072B2", "active_vs_lastsemi": "#D55E00"}
    markers = {"semi_vs_lastsemi": "o", "active_vs_lastsemi": "s"}

    for ax_i, k in enumerate(ks):
        ax = axes[ax_i // ncols][ax_i % ncols]
        sub = q2[q2["k"] == k]
        for pair_name, color in colors.items():
            grp = sub[sub["pair"] == pair_name].sort_values("f")
            if grp.empty:
                continue
            ax.plot(
                grp["f"],
                grp["mean_drpdf_pp"],
                marker=markers[pair_name],
                markersize=5,
                color=color,
                label=RECON_PAIRS[pair_name][1],
                linewidth=1.4,
            )
        ax.set_xticks(sorted(sub["f"].unique()))
        ax.set_title(f"k={k}", fontsize=10)
        ax.set_xlabel("f (%)")
        ax.set_ylabel("dRPDf (pp) vs last-semi")
        ax.axhline(0, color="#666666", linewidth=0.8)
        ax.grid(alpha=0.3)

    for i in range(len(ks), nrows * ncols):
        axes[i // ncols][i % ncols].set_visible(False)

    axes[0][0].legend(fontsize=8, frameon=False)
    fig.suptitle(
        "Reconstruct 3-mode gap vs last-semi (positive = last-semi wins)",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(outdir / "q2_recon_mode_f_curve.png", dpi=150)
    plt.close(fig)


# -- Q3: all scenarios vs the m1_k1 baseline -----------------------------------------


def scenario_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Mean elapsed + mean RPDf per scenario, plus arm/f/k/mode metadata."""
    g = df.groupby("scenarioName", sort=False)
    out = g["elapsedTime"].mean().to_frame("mean_elapsed_s")
    out["mean_rpdf_pct"] = g["rpdf_pct"].mean()
    meta = (
        df[["scenarioName", "arm", "f", "k", "mode"]]
        .drop_duplicates("scenarioName")
        .set_index("scenarioName")
    )
    return out.join(meta).reset_index()


def _match_a_b_baselines(
    a_b_summary: pd.DataFrame, m1k1_summary: pd.DataFrame
) -> dict[str, str]:
    """For each a/b scenario, pick the closest m1_k1 by mean elapsed."""
    mapping: dict[str, str] = {}
    for _, row in a_b_summary.iterrows():
        diff = (m1k1_summary["mean_elapsed_s"] - row["mean_elapsed_s"]).abs()
        best = m1k1_summary.loc[diff.idxmin(), "scenarioName"]
        mapping[row["scenarioName"]] = best
    return mapping


def q3_all_vs_m1k1(
    df: pd.DataFrame, summary: pd.DataFrame, outdir: Path
) -> pd.DataFrame:
    """Every scenario compared against the uncoarsened ``m1_k1`` baseline.

    f-scaled arms (c / m1 / msemi / mactive): paired against ``m1_k1_f{FF}``
    at the same f. Arms a/b (no f): paired against the closest ``m1_k1_f{FF}``
    by mean ``elapsedTime``.
    """
    m1k1_df = df[df["arm"] == "m1"]
    m1k1_df = m1k1_df[m1k1_df["k"] == 1]
    m1k1_summary = summary[
        summary["scenarioName"].isin(m1k1_df["scenarioName"].unique())
    ]

    a_b_mask = summary["arm"].isin(["a", "b"]) & summary["f"].isna()
    a_b_mapping = _match_a_b_baselines(summary[a_b_mask], m1k1_summary)

    wide = df.pivot_table(index="insIndex", columns="scenarioName", values="rpdf_pct")
    mean_elapsed = summary.set_index("scenarioName")["mean_elapsed_s"]

    rows: list[dict] = []
    for scenario in wide.columns:
        meta = summary.set_index("scenarioName").loc[scenario]
        if meta["arm"] == "m1" and meta["k"] == 1:
            continue  # m1_k1 is the baseline; no self-comparison
        if pd.notna(meta["f"]):
            f_i = int(meta["f"])
            baseline = f"m1_k1_f{f_i:02d}"
        else:
            baseline = a_b_mapping.get(scenario)
            if baseline is None:
                continue

        if baseline not in wide.columns:
            raise SystemExit(f"missing baseline {baseline!r} for {scenario!r}")

        pair = wide[[scenario, baseline]].dropna()
        delta = pair[scenario] - pair[baseline]
        sc_nan = wide[scenario].isna()
        bl_nan = wide[baseline].isna()

        # "Same nominal f" only means "same budget" when the CSR timelimit is
        # f-scaled in BOTH arms. That holds for this sweep's config but NOT for
        # the 20260725 ladder, whose `c` arm kept timelimit=0.09nc. Carry the
        # measured ratio so a win can never be read as equal-budget on faith.
        elapsed_ratio = float(
            mean_elapsed.get(scenario, np.nan) / mean_elapsed.get(baseline, np.nan)
        )

        rows.append(
            {
                "scenarioName": scenario,
                "arm": meta["arm"],
                "f": meta["f"] if pd.notna(meta["f"]) else None,
                "k": int(meta["k"]),
                "mode": meta["mode"] if isinstance(meta["mode"], str) else None,
                "baseline": baseline,
                "elapsed_ratio": elapsed_ratio,
                "n_paired": len(pair),
                # baseline solved but this scenario did not, and vice versa.
                "k1_only_feasible": int((sc_nan & ~bl_nan).sum()),
                "scenario_only_feasible": int((~sc_nan & bl_nan).sum()),
                "scenario_rpdf_pct": float(pair[scenario].mean()),
                "baseline_rpdf_pct": float(pair[baseline].mean()),
                "mean_drpdf_star_pp": float(delta.mean()),
                "win": int((delta < 0).sum()),  # scenario wins
                "tie": int((delta == 0).sum()),
                "loss": int((delta > 0).sum()),
            }
        )

    q3 = pd.DataFrame(rows).sort_values(["arm", "f", "k"])
    q3.to_csv(outdir / "q3_all_vs_m1k1.csv", index=False)
    return q3


def plot_q3(summary: pd.DataFrame, q3: pd.DataFrame, outdir: Path) -> None:
    """(mean elapsed, mean RPDf) scatter with m1_k1 reference curve.

    m1_k1 is the practical standard -- 8 points connected as a line.
    All other 131 scenarios are scattered, colored by arm.
    """
    m1k1_mask = (summary["arm"] == "m1") & (summary["k"] == 1)
    m1k1_pts = summary[m1k1_mask].sort_values("f").reset_index(drop=True)
    other_pts = summary[~m1k1_mask]

    # Join dRPDf* so outliers can be labelled.
    mapping = q3.set_index("scenarioName")["mean_drpdf_star_pp"]
    other_pts = other_pts.copy()
    other_pts["dRPDf*"] = other_pts["scenarioName"].map(mapping)

    arm_colors = {
        "a": "#009E73",
        "b": "#56B4E9",
        "c": "#E69F00",
        "m1": "#0072B2",
        "msemi": "#CC79A7",
        "mactive": "#D55E00",
    }
    arm_markers = {
        "a": "o",
        "b": "s",
        "c": "^",
        "m1": "D",
        "msemi": "v",
        "mactive": "X",
    }

    fig, ax = plt.subplots(figsize=(8, 6))

    # m1_k1 reference curve.
    ax.plot(
        m1k1_pts["mean_elapsed_s"],
        m1k1_pts["mean_rpdf_pct"],
        "k-o",
        linewidth=1.8,
        markersize=6,
        label="m1_k1 (baseline)",
        zorder=3,
    )
    for _, pt in m1k1_pts.iterrows():
        ax.annotate(
            f"f={int(pt['f'])}%",
            (pt["mean_elapsed_s"], pt["mean_rpdf_pct"]),
            textcoords="offset points",
            xytext=(6, 4),
            fontsize=7,
            color="black",
        )

    # Scatter all other scenarios.
    for arm, grp in other_pts.groupby("arm"):
        ax.scatter(
            grp["mean_elapsed_s"],
            grp["mean_rpdf_pct"],
            c=arm_colors.get(arm, "#999999"),
            marker=arm_markers.get(arm, "o"),
            label=arm,
            s=28,
            alpha=0.75,
            edgecolors="none",
        )

    # Label the best (lowest-RPDf) non-m1k1 point in each arm.
    for arm, grp in other_pts.groupby("arm"):
        best = grp.loc[grp["mean_rpdf_pct"].idxmin()]
        ax.annotate(
            best["scenarioName"],
            (best["mean_elapsed_s"], best["mean_rpdf_pct"]),
            textcoords="offset points",
            xytext=(6, 2),
            fontsize=6,
            alpha=0.85,
            color=arm_colors.get(arm, "#666666"),
        )

    ax.set_xlabel("Mean elapsed time (s)")
    ax.set_ylabel("Mean RPDf (%)")
    ax.set_title(
        "All scenarios vs m1_k1 baseline\n"
        "(point left-of/below the curve = beats m1_k1 at equal or less time)",
        fontsize=10,
    )
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, frameon=False, ncol=2)
    fig.tight_layout()
    fig.savefig(outdir / "q3_scatter.png", dpi=150)
    plt.close(fig)


# -- budget parity -------------------------------------------------------------------


def budget_parity(df: pd.DataFrame, outdir: Path) -> pd.DataFrame:
    """Mean elapsed per (arm, f, k, mode).

    Plan §4 2급 2: elapsed should not vary with k inside an (arm, f) cell.
    """
    elapsed = (
        df.groupby(["arm", "f", "k", "mode"], dropna=False)["elapsedTime"]
        .agg(["mean", "std", "count"])
        .reset_index()
        .rename(columns={"mean": "elapsed_s", "std": "elapsed_std_s", "count": "n"})
    )
    elapsed["cv"] = elapsed["elapsed_std_s"] / elapsed["elapsed_s"].replace(0, np.nan)
    elapsed = elapsed.sort_values(["arm", "f", "k"]).reset_index(drop=True)
    elapsed.to_csv(outdir / "elapsed_by_scenario.csv", index=False)
    return elapsed


# -- console report ------------------------------------------------------------------


def _fmt_f(f_val: float | None) -> str:
    if pd.isna(f_val) or f_val is None:
        return "-"
    return f"f={int(f_val):02d}%"


def print_report(
    q2: pd.DataFrame, q3: pd.DataFrame, elapsed: pd.DataFrame, df: pd.DataFrame
) -> None:
    print(RULE)
    print("Q2: RECONSTRUCT 3-MODE COMPARISON (msemi/mactive vs m1 = last-semi)")
    print("     dRPDf > 0 = last-semi wins")
    print(RULE)
    if q2.empty:
        print("  (no msemi/mactive arms in this run -- Q2 not applicable)")
    for k in sorted(q2["k"].unique()):
        print(f"\n  k={k}:")
        for pair in sorted(q2["pair"].unique()):
            sub = q2[(q2["k"] == k) & (q2["pair"] == pair)].sort_values("f")
            line = "    " + "  ".join(
                f"f={int(r['f']):02d}:{r['mean_drpdf_pp']:+6.2f}"
                for _, r in sub.iterrows()
            )
            print(f"    {pair:20s} {line}")
        sub = q2[q2["k"] == k]
        for pair in sorted(sub["pair"].unique()):
            pr = sub[sub["pair"] == pair]
            n_neg = int((pr["mean_drpdf_pp"] < 0).sum())
            n_pos = int((pr["mean_drpdf_pp"] > 0).sum())
            print(
                f"    {pair}: {n_neg} f-cells where {RECON_PAIRS[pair][1]} wins, "
                f"{n_pos} where last-semi wins"
            )
    print()

    # Q3
    print(RULE)
    print("Q3: ALL SCENARIOS vs m1_k1 BASELINE")
    print("     dRPDf* > 0 = m1_k1 wins")
    print(RULE)
    has_crossover = (q3["mean_drpdf_star_pp"] < 0) & (q3["win"] > q3["loss"])
    if not has_crossover.any():
        print("  No scenario beats m1_k1 at equal budget (dRPDf* < 0 AND win > loss).")
    else:
        print("  Scenarios beating m1_k1 on both mean and count:")
        print("    (t= measured elapsed ratio vs baseline; >1.10 is NOT equal budget)")
        for _, r in q3[has_crossover].iterrows():
            ratio = r["elapsed_ratio"]
            flag = "  <-- OVER BUDGET" if pd.notna(ratio) and ratio > 1.10 else ""
            print(
                f"    {r['scenarioName']:30s} vs {r['baseline']:15s}  "
                f"dRPDf*={r['mean_drpdf_star_pp']:+7.2f} pp  "
                f"w/t/l={int(r['win'])}/{int(r['tie'])}/{int(r['loss'])}  "
                f"t={ratio:5.2f}x{flag}"
            )
        n_over = int((q3.loc[has_crossover, "elapsed_ratio"] > 1.10).sum())
        if n_over:
            print(
                f"\n  WARNING: {n_over} of the above spent >110% of their baseline's "
                "wall time -- those are not equal-budget wins and must not be "
                "reported as Q3 crossovers."
            )

    # Best per arm
    print("\n  Best scenario per arm (lowest dRPDf* vs m1_k1):")
    for arm in sorted(q3["arm"].unique()):
        arm_q3 = q3[q3["arm"] == arm]
        best = arm_q3.loc[arm_q3["mean_drpdf_star_pp"].idxmin()]
        f_str = _fmt_f(best["f"])
        print(
            f"    {arm:8s}  {best['scenarioName']:28s}  {f_str:7s}  "
            f"dRPDf*={best['mean_drpdf_star_pp']:+7.2f} pp  "
            f"w/t/l={int(best['win'])}/{int(best['tie'])}/{int(best['loss'])}"
        )

    # Feasibility asymmetry (the f=1 % gate).
    print(RULE)
    print("FEASIBILITY ASYMMETRY (NaN RPDf — only scenarios with issues)")
    print(RULE)
    nan_counts = (
        df.assign(is_nan=df["rpdf_pct"].isna())
        .groupby("scenarioName")
        .agg(nan=("is_nan", "sum"), total=("is_nan", "size"))
        .reset_index()
    )
    bad = nan_counts[nan_counts["nan"] > 0]
    if bad.empty:
        print("  none — every scenario registered an incumbent on every instance")
    else:
        for _, r in bad.iterrows():
            print(
                f"  {r['scenarioName']:30s}  {int(r['nan']):3d}/{int(r['total'])} NaN"
            )

    # Budget parity: CV of elapsed within each (arm, f) cell.
    print(RULE)
    print("BUDGET PARITY: CV( elapsed ) within (arm, f) cells")
    print("  (high CV = elapsed varies with k — equal-budget assumption broken)")
    print(RULE)
    # NB: `f` is NaN for arms a/b, and a NaN key cannot be looked up by label --
    # so collect (arm, f, cv) as plain rows rather than indexing the grouped
    # Series.
    cv_rows: list[tuple[str, float | None, float]] = []
    for (arm, f_val), grp in elapsed.groupby(["arm", "f"], dropna=False):
        mean = grp["elapsed_s"].mean()
        if not mean or len(grp) < 2:
            continue
        cv_rows.append(
            (arm, None if pd.isna(f_val) else f_val, grp["elapsed_s"].std() / mean)
        )

    if not cv_rows:
        print("  (no (arm, f) cell has >1 scenario -- nothing to compare)")
        return

    worst = max(cv_rows, key=lambda r: r[2])
    cvs = sorted(r[2] for r in cv_rows)
    print(f"  max CV = {worst[2]:.3f} at (arm={worst[0]}, {_fmt_f(worst[1])})")
    print(f"  median CV = {cvs[len(cvs) // 2]:.3f}")
    for arm in sorted({r[0] for r in cv_rows}):
        vals = sorted(
            ((f, cv) for a, f, cv in cv_rows if a == arm),
            key=lambda x: (x[0] is None, x[0]),
        )
        line = "  ".join(f"{_fmt_f(f)}:{cv:.3f}" for f, cv in vals)
        print(f"  {arm:8s} {line}")


# -- main ----------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="analyze_usability")
    p.add_argument("run_dir", type=Path, help="timestamp directory under output/...")
    p.add_argument("--outdir", type=Path, default=None)
    return p.parse_args()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args()
    run_dir = args.run_dir.resolve()
    outdir = args.outdir or REPO_ROOT / "analysis" / f"{run_dir.name}_usability"
    outdir.mkdir(parents=True, exist_ok=True)

    df = _ladder.load_run(run_dir)
    n_ins = df["insIndex"].nunique()
    n_scn = df["scenarioName"].nunique()
    print(f"{n_scn} scenarios × {n_ins} instances")
    print(f"output -> {outdir}")
    print()

    summary = scenario_summary(df)
    elapsed = budget_parity(df, outdir)

    # Q2
    q2 = q2_recon_comparison(df, outdir)
    plot_q2(q2, outdir)

    # Q3
    q3 = q3_all_vs_m1k1(df, summary, outdir)
    plot_q3(summary, q3, outdir)

    print_report(q2, q3, elapsed, df)
    print()
    print(f"wrote CSVs + PNGs to {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
