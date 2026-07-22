"""Phase 1 of the CSR 3-phase analysis: rank the CSR init methods.

Plan: ``plans/analysis/20260719/csr_triple_analysis_plan.md`` §Phase 1.

Answers "which init flow + NEH priority gives the best mean RPDf on the full
PRA2017 1440-instance grid, per unit of *initialization* budget".

Scope caveat, load-bearing for every number this script prints: every scenario
here has a single-step outer flow (``subroutine_flow: [coarsen_solve_reconstruct]``)
and spends only ``0.0225nc`` of a ``0.09nc`` cap. So these RPDf values measure
**init quality**, not final solution quality -- no claim of the form "CSR init =>
better final solution" is supported. Phrase conclusions as "best init under a
fixed init budget".

Data source is the run's ``<ts>_rpdf_comparison.csv``, using its precomputed
``RPDf_BKS_data`` column verbatim -- it already carries the
``(n, c, totalMcCount, T, R, W, BKS_data, elapsedTime)`` join. All three phases
of this analysis read that same frame so they cannot drift apart. The loader and
the oracle/portfolio helpers are imported from ``analyze_dispatch_sweep.py``
rather than re-derived, for the same reason.

Two runs are read:

    20260713T195341_009592   1440 instances, 10 scenarios  (primary)
    20260713T091912_833529    160 instances, 11 scenarios  (secondary)

The 160-instance run is **not** a small sample of the 1440 grid: it is exactly
the ``(T=0.6, R=0.2)`` cell, i.e. one of the 9 cells of the (T, R) table. Its
~34 % RPDf level is not comparable to the ~15 % full-grid mean because it is the
hardest cell, not a different measurement. It is reported because it is the sole
run containing ``csr_fmm_base``.

Usage:
    uv run python scripts/20260719/analyze_csr_init_methods.py \
        [--full-run <run_dir>] [--subset-run <run_dir>] \
        [--outdir analysis/20260719_csr_init]

Outputs (under --outdir):
    csr_init_methods.csv            one row per (slice, scenario)
    csr_init_methods_tr_cells.csv   one row per ((T,R) cell, scenario)
    csr_init_methods_portfolio.csv  oracle 2-/3-subsets, overall + per cell
    csr_init_methods_secondary.csv  the 160-instance (=(0.6,0.2) cell) table
    csr_init_methods_scatter.png    mean elapsed vs mean RPDf
"""

from __future__ import annotations

import argparse
import importlib.util
from itertools import combinations
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

# scripts/20260719/<this file> -- two levels of nesting below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, relpath: str):
    """scripts/ is not an importable package; load a sibling module by path."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ads = _load_module("analyze_dispatch_sweep", "scripts/analyze_dispatch_sweep.py")

METRIC = "RPDf_BKS_data"
INSTANCE_COL = "insIndex"
METHOD_COL = "scenarioName"

DEFAULT_FULL_RUN = REPO_ROOT / "output/20260713_csr_init_methods/20260713T195341_009592"
DEFAULT_SUBSET_RUN = (
    REPO_ROOT / "output/20260713_csr_init_methods/20260713T091912_833529"
)

T_VALUES = (0.2, 0.4, 0.6)
R_VALUES = (0.2, 0.6, 1.0)

# Scenario -> (category, CSR budget as a fraction of the 0.09nc scenario cap).
# The five non-CSR scenarios are deliberately spread over four budget points, so
# CSR-vs-plain is answerable at f = 0/10/25/30, not only at the 25 % equal-budget
# point. csr_fmm_base is the one budget-asymmetric entry (see the a fortiori note
# in the plan) and exists only in the 160-instance run.
SCENARIO_META: dict[str, tuple[str, str]] = {
    "mcf_lb": ("free baseline", "~0"),
    "mcf_lb_fmm": ("natural baseline", "10%"),
    "neh": ("natural baseline", "30%"),
    "mcf_lb_fmm_25p": ("equal-budget control", "25%"),
    "neh_25p": ("equal-budget control", "25%"),
    "csr_base": ("CSR", "25%"),
    "csr_full_d2wp": ("CSR", "25%"),
    "csr_full_wdp": ("CSR", "25%"),
    "csr_neh_d2wp": ("CSR", "25%"),
    "csr_neh_wdp": ("CSR", "25%"),
    "csr_fmm_base": ("CSR (budget-asymmetric)", "35%"),
}

# The paired comparisons the plan calls out, as (label, A, B, why).
KEY_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    (
        "equal-budget A/B (THE critical test)",
        "csr_full_d2wp",
        "mcf_lb_fmm_25p",
        "same 0.0225nc, both full pipeline: CSR vs plain",
    ),
    (
        "NEH priority, full inner flow",
        "csr_full_d2wp",
        "csr_full_wdp",
        "due2-weight-pos vs weight-due-pos",
    ),
    (
        "NEH priority, neh-only inner flow",
        "csr_neh_d2wp",
        "csr_neh_wdp",
        "due2-weight-pos vs weight-due-pos",
    ),
    (
        "inner flow",
        "csr_full_d2wp",
        "csr_neh_d2wp",
        "with vs without mcf/flip in the inner flow",
    ),
    (
        "CSR base vs full",
        "csr_base",
        "csr_full_d2wp",
        "is the inner flow worth it over default CP-SAT?",
    ),
)

RULE = "=" * 78


def _pct(series: pd.Series) -> pd.Series:
    """RPDf as a percentage, the unit every table in this analysis reports."""
    return series * 100.0


def load_run(run_dir: Path) -> pd.DataFrame:
    """Load a run's rpdf_comparison frame and add the RPDf% column."""
    df = _ads.load_rpdf(run_dir)
    if df[METRIC].isna().any():
        raise ValueError(f"{run_dir}: null {METRIC}; refusing to average a partial set")
    df["RPDf_pct"] = _pct(df[METRIC])
    return df


def rank_scenarios(df: pd.DataFrame) -> pd.DataFrame:
    """Mean RPDf% per scenario, ascending (lower = better), with rank."""
    out = (
        df.groupby(METHOD_COL)
        .agg(
            instances=(INSTANCE_COL, "nunique"),
            mean_RPDf_pct=("RPDf_pct", "mean"),
            median_RPDf_pct=("RPDf_pct", "median"),
            mean_elapsed=("elapsedTime", "mean"),
        )
        .sort_values("mean_RPDf_pct")
        .reset_index()
    )
    out.insert(0, "rank", range(1, len(out) + 1))
    out["category"] = out[METHOD_COL].map(lambda s: SCENARIO_META.get(s, ("?", "?"))[0])
    out["budget_f"] = out[METHOD_COL].map(lambda s: SCENARIO_META.get(s, ("?", "?"))[1])
    return out


def paired(df: pd.DataFrame, a: str, b: str) -> dict[str, float]:
    """Per-instance paired comparison of two scenarios on the same instances.

    Pairing is on ``insIndex``, so this is a like-for-like comparison rather
    than a difference of two independently-taken means.
    """
    mat = df.pivot(index=INSTANCE_COL, columns=METHOD_COL, values="RPDf_pct")
    for name in (a, b):
        if name not in mat.columns:
            raise KeyError(f"scenario {name!r} not in this run")
    pair = mat[[a, b]].dropna()
    diff = pair[a] - pair[b]
    return {
        "instances": int(len(pair)),
        f"mean_{a}": float(pair[a].mean()),
        f"mean_{b}": float(pair[b].mean()),
        "delta_pp": float(diff.mean()),
        "a_wins": int((diff < 0).sum()),
        "ties": int((diff == 0).sum()),
        "b_wins": int((diff > 0).sum()),
    }


def tr_cell_table(df: pd.DataFrame) -> pd.DataFrame:
    """Mean RPDf% for every (T, R) cell x scenario -- 9 cells of 160 instances.

    A primary output, not a slice. The 1440-mean averages *over* these cells and
    therefore erases where a method wins, so a method must be ranked within each
    cell before any "method X is bad" conclusion is drawn.
    """
    out = (
        df.groupby(["T", "R", METHOD_COL])
        .agg(
            instances=(INSTANCE_COL, "nunique"),
            mean_RPDf_pct=("RPDf_pct", "mean"),
        )
        .reset_index()
    )
    out["cell"] = out.apply(lambda r: f"T={r['T']:g},R={r['R']:g}", axis=1)
    out["rank_in_cell"] = out.groupby("cell")["mean_RPDf_pct"].rank(method="min")
    return out


def unique_best_counts(df: pd.DataFrame, by_cell: bool) -> pd.DataFrame:
    """How often each scenario is the *unique* per-instance best.

    A method that is never uniquely best is genuinely redundant in a portfolio,
    whatever its standalone mean -- and conversely a method with a poor mean can
    still carry the only win on a set of instances.
    """
    mat = df.pivot(index=INSTANCE_COL, columns=METHOD_COL, values="RPDf_pct")
    row_min = mat.min(axis=1)
    is_min = mat.eq(row_min, axis=0)
    # Exact float equality is the right test: equal RPDf comes from an identical
    # bestObj through an identical formula, so ties are genuine, not rounding.
    unique = is_min[is_min.sum(axis=1) == 1]

    if not by_cell:
        counts = unique.sum().astype(int).sort_values(ascending=False)
        return counts.rename("unique_best").reset_index()

    cells = df[[INSTANCE_COL, "T", "R"]].drop_duplicates().set_index(INSTANCE_COL)
    joined = unique.join(cells, how="left")
    rows = []
    for (t, r), grp in joined.groupby(["T", "R"]):
        counts = grp[unique.columns].sum().astype(int)
        for scenario, n in counts.items():
            rows.append(
                {
                    "cell": f"T={t:g},R={r:g}",
                    METHOD_COL: scenario,
                    "unique_best": int(n),
                    "cell_instances": int(len(grp)),
                }
            )
    return pd.DataFrame(rows)


def portfolio_table(df: pd.DataFrame, label: str, top: int = 5) -> pd.DataFrame:
    """Oracle (virtual-best) means for every 1-, 2- and 3-subset of scenarios.

    ORACLE NUMBERS -- read the caveat printed with this table. A per-instance
    ``min`` assumes a perfect selector, and actually running k inits costs
    k x budget: two at 0.0225nc is 0.045nc (f = 50 %), beyond the f = 30 %
    ceiling of anything measured in this phase. So this is a complementarity
    diagnosis and an upper bound, not a runnable strategy at matched cost.
    """
    mat = _ads.metric_matrix(df, METRIC)
    mat = mat * 100.0
    rows = []
    for k in (1, 2, 3):
        for combo in combinations(sorted(mat.columns), k):
            rows.append(
                {
                    "slice": label,
                    "k": k,
                    "combo": " + ".join(combo),
                    "oracle_mean_RPDf_pct": float(mat[list(combo)].min(axis=1).mean()),
                }
            )
    out = pd.DataFrame(rows)
    out["rank_in_k"] = out.groupby("k")["oracle_mean_RPDf_pct"].rank(method="min")
    return out.sort_values(["k", "oracle_mean_RPDf_pct"]).reset_index(drop=True)


def marginal_contributions(df: pd.DataFrame, base: tuple[str, ...]) -> pd.DataFrame:
    """mean(min over S) - mean(min over S u {x}) for each candidate x.

    Positive = x improves the portfolio S. Zero = x is redundant given S.
    """
    mat = _ads.metric_matrix(df, METRIC) * 100.0
    base_val = float(mat[list(base)].min(axis=1).mean())
    rows = []
    for cand in mat.columns:
        if cand in base:
            continue
        val = float(mat[list(base) + [cand]].min(axis=1).mean())
        rows.append(
            {
                "base": " + ".join(base),
                "candidate": cand,
                "base_oracle_pct": base_val,
                "with_candidate_pct": val,
                "marginal_gain_pp": base_val - val,
            }
        )
    return pd.DataFrame(rows).sort_values("marginal_gain_pp", ascending=False)


def scatter_plot(ranked: pd.DataFrame, out_png: Path) -> None:
    """Mean elapsed vs mean RPDf% -- the time/quality frontier of the 10 inits."""
    fig, ax = plt.subplots(figsize=(8.5, 6.0))
    colors = {
        "free baseline": "#999999",
        "natural baseline": "#0072B2",
        "equal-budget control": "#D55E00",
        "CSR": "#009E73",
        "CSR (budget-asymmetric)": "#CC79A7",
    }
    for category, grp in ranked.groupby("category"):
        ax.scatter(
            grp["mean_elapsed"],
            grp["mean_RPDf_pct"],
            s=70,
            label=category,
            color=colors.get(category, "#000000"),
            zorder=3,
        )
    for _, row in ranked.iterrows():
        # Every point carries a direct label -- identity never rests on color.
        ax.annotate(
            row[METHOD_COL],
            (row["mean_elapsed"], row["mean_RPDf_pct"]),
            textcoords="offset points",
            xytext=(6, 4),
            fontsize=8,
        )
    ax.set_xlabel("mean elapsed time (s)")
    ax.set_ylabel("mean RPDf (%)  -- lower is better")
    ax.set_title(
        "CSR init methods: init quality vs init time (1440 instances)\n"
        "init-only scope: single-step flow, ~25% of the 0.09nc cap spent",
        fontsize=10,
    )
    ax.grid(alpha=0.3, zorder=0)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="analyze_csr_init_methods")
    p.add_argument("--full-run", type=Path, default=DEFAULT_FULL_RUN)
    p.add_argument("--subset-run", type=Path, default=DEFAULT_SUBSET_RUN)
    p.add_argument(
        "--outdir", type=Path, default=REPO_ROOT / "analysis" / "20260719_csr_init"
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    full = load_run(args.full_run)
    subset = load_run(args.subset_run)

    print(RULE)
    print("PHASE 1 -- CSR init methods comparison")
    print(RULE)
    print(f"primary run : {args.full_run}")
    print(
        f"              {full[INSTANCE_COL].nunique()} instances, "
        f"{full[METHOD_COL].nunique()} scenarios"
    )
    print(f"secondary   : {args.subset_run}")
    print(
        f"              {subset[INSTANCE_COL].nunique()} instances, "
        f"{subset[METHOD_COL].nunique()} scenarios"
    )
    print()
    print("SCOPE: every scenario is a single-step init flow spending 0.0225nc of a")
    print("0.09nc cap. These numbers measure INIT QUALITY under a fixed init")
    print("budget -- not final solution quality. No 'better init => better final")
    print("solution' claim is supported by this data.")

    # ---------------------------------------------------------------- (1)
    print()
    print(RULE)
    print("(1) Overall ranking -- mean RPDf%, 1440 instances (lower = better)")
    print(RULE)
    ranked = rank_scenarios(full)
    print(
        ranked[
            [
                "rank",
                METHOD_COL,
                "category",
                "budget_f",
                "instances",
                "mean_RPDf_pct",
                "median_RPDf_pct",
                "mean_elapsed",
            ]
        ]
        .round({"mean_RPDf_pct": 3, "median_RPDf_pct": 3, "mean_elapsed": 2})
        .to_string(index=False)
    )

    # ---------------------------------------------------------- (2)-(5)
    print()
    print(RULE)
    print("(2)-(5) Key paired comparisons -- per-instance paired on insIndex")
    print(RULE)
    pair_rows = []
    for label, a, b, why in KEY_PAIRS:
        res = paired(full, a, b)
        winner = a if res["delta_pp"] < 0 else (b if res["delta_pp"] > 0 else "tie")
        print(f"\n{label}")
        print(f"  {why}")
        print(f"  {a:>18} : {res[f'mean_{a}']:8.3f} %")
        print(f"  {b:>18} : {res[f'mean_{b}']:8.3f} %")
        print(
            f"  {'delta (A-B)':>18} : {res['delta_pp']:+8.3f} %p   -> better: {winner}"
        )
        print(
            f"  {'win/tie/loss':>18} : {res['a_wins']} / {res['ties']} / "
            f"{res['b_wins']}   (A wins / tie / B wins, n={res['instances']})"
        )
        pair_rows.append(
            {
                "comparison": label,
                "A": a,
                "B": b,
                "mean_A_pct": res[f"mean_{a}"],
                "mean_B_pct": res[f"mean_{b}"],
                "delta_pp": res["delta_pp"],
                "better": winner,
                "A_wins": res["a_wins"],
                "ties": res["ties"],
                "B_wins": res["b_wins"],
            }
        )

    # ---------------------------------------------------------------- (6)
    print()
    print(RULE)
    print("(6) (T, R) 3x3 decomposition -- 9 cells x 160 instances, mean RPDf%")
    print(RULE)
    print("A 1440-mean is NOT a sufficient basis for discarding a method: it")
    print("averages over these cells and erases where a method wins.")
    cells = tr_cell_table(full)
    pivot = cells.pivot(index=METHOD_COL, columns="cell", values="mean_RPDf_pct")
    order = [f"T={t:g},R={r:g}" for t in T_VALUES for r in R_VALUES]
    pivot = pivot[order]
    pivot = pivot.loc[ranked[METHOD_COL]]  # overall-rank order
    print()
    print(pivot.round(2).to_string())
    print()
    print("per-cell winner:")
    for cell in order:
        col = pivot[cell]
        print(f"  {cell:>14} : {col.idxmin():<18} ({col.min():.3f} %)")

    winners = {pivot[cell].idxmin() for cell in order}
    overall_winner = ranked.iloc[0][METHOD_COL]
    print()
    if winners == {overall_winner}:
        print(f"  -> the overall winner ({overall_winner}) wins ALL 9 cells.")
    else:
        print(
            f"  -> overall winner is {overall_winner}, but cell winners are: "
            f"{sorted(winners)}"
        )

    print()
    print("unique-best counts (how often a scenario is the SOLE per-instance best):")
    ub_all = unique_best_counts(full, by_cell=False)
    print(ub_all.to_string(index=False))
    never = ub_all[ub_all["unique_best"] == 0][METHOD_COL].tolist()
    if never:
        print(f"  -> never uniquely best (redundant in a portfolio): {never}")
    ub_cells = unique_best_counts(full, by_cell=True)

    # ---------------------------------------------------------------- (7)
    print()
    print(RULE)
    print("(7) Portfolio synergy -- ORACLE (virtual-best) numbers")
    print(RULE)
    print("CAVEAT: a per-instance min assumes a PERFECT selector, and running k")
    print("inits costs k x budget -- two at 0.0225nc is 0.045nc (f = 50%), beyond")
    print("the f = 30% ceiling of anything measured here. This is a")
    print("complementarity diagnosis and an UPPER BOUND, not a runnable strategy")
    print("compared at matched cost.")
    port_all = portfolio_table(full, "all")
    for k in (1, 2, 3):
        print(f"\n  best {k}-subsets (oracle mean RPDf%):")
        sub = port_all[port_all["k"] == k].head(5)
        for _, row in sub.iterrows():
            print(f"    {row['oracle_mean_RPDf_pct']:8.3f} %   {row['combo']}")

    best_single = (port_all[port_all["k"] == 1].iloc[0]["combo"],)
    best_pair = tuple(port_all[port_all["k"] == 2].iloc[0]["combo"].split(" + "))
    print()
    print(f"  marginal contribution over the best single ({best_single[0]}):")
    mc1 = marginal_contributions(full, best_single)
    print("    " + mc1.round(4).to_string(index=False).replace("\n", "\n    "))
    print()
    print(f"  marginal contribution over the best pair ({' + '.join(best_pair)}):")
    mc2 = marginal_contributions(full, best_pair)
    print("    " + mc2.round(4).to_string(index=False).replace("\n", "\n    "))

    # per-cell portfolios
    port_cells = []
    for t in T_VALUES:
        for r in R_VALUES:
            cell_df = full[(full["T"] == t) & (full["R"] == r)]
            port_cells.append(portfolio_table(cell_df, f"T={t:g},R={r:g}"))
    port = pd.concat([port_all] + port_cells, ignore_index=True)

    # ------------------------------------------------- secondary 160-inst
    print()
    print(RULE)
    print("(8) Secondary table -- 160 instances = EXACTLY the (T=0.6, R=0.2) cell")
    print(RULE)
    print("This is a CELL-LEVEL result, not a small-sample version of the overall")
    print("one: its RPDf level is high because it is the hardest cell. It is")
    print("reported because it is the only run containing csr_fmm_base.")
    print()
    print("csr_fmm_base is BUDGET-ASYMMETRIC: 0.0315nc vs 0.0225nc for every other")
    print("CSR scenario (~40% more). Read a fortiori -- if it loses DESPITE the")
    print("extra budget, 'outer FMM does not pay for itself' is STRONGER than an")
    print("equal-budget loss would be, and no budget-matched re-run is needed.")
    sub_ranked = rank_scenarios(subset)
    print()
    print(
        sub_ranked[
            [
                "rank",
                METHOD_COL,
                "category",
                "budget_f",
                "instances",
                "mean_RPDf_pct",
                "mean_elapsed",
            ]
        ]
        .round({"mean_RPDf_pct": 3, "mean_elapsed": 2})
        .to_string(index=False)
    )

    fmm = sub_ranked[sub_ranked[METHOD_COL] == "csr_fmm_base"]
    best_25p = sub_ranked[sub_ranked["budget_f"] == "25%"].iloc[0]
    if not fmm.empty:
        fmm_val = float(fmm.iloc[0]["mean_RPDf_pct"])
        print()
        print(f"  csr_fmm_base (0.0315nc) : {fmm_val:.3f} %")
        print(
            f"  best 0.0225nc scenario  : {float(best_25p['mean_RPDf_pct']):.3f} % "
            f"({best_25p[METHOD_COL]})"
        )
        if fmm_val >= float(best_25p["mean_RPDf_pct"]):
            print("  -> VERDICT: csr_fmm_base is worse/equal DESPITE ~40% more budget.")
            print("     The a fortiori argument HOLDS: outer FMM does not pay for")
            print("     itself, and no budget-matched re-run is required.")
        else:
            print("  -> VERDICT: csr_fmm_base BEATS the best 0.0225nc scenario.")
            print("     The a fortiori argument COLLAPSES -- a budget-matched re-run")
            print("     is now REQUIRED before any conclusion about outer FMM.")
        # Paired check on the same 160 instances, since means alone can mislead.
        res = paired(subset, "csr_fmm_base", str(best_25p[METHOD_COL]))
        print(
            f"     paired w/t/l vs {best_25p[METHOD_COL]}: "
            f"{res['a_wins']} / {res['ties']} / {res['b_wins']}"
        )

    # ------------------------------------------------------------- outputs
    ranked.to_csv(args.outdir / "csr_init_methods.csv", index=False)
    cells.to_csv(args.outdir / "csr_init_methods_tr_cells.csv", index=False)
    ub_cells.to_csv(args.outdir / "csr_init_methods_unique_best.csv", index=False)
    port.to_csv(args.outdir / "csr_init_methods_portfolio.csv", index=False)
    pd.concat([mc1, mc2], ignore_index=True).to_csv(
        args.outdir / "csr_init_methods_marginal.csv", index=False
    )
    pd.DataFrame(pair_rows).to_csv(
        args.outdir / "csr_init_methods_pairs.csv", index=False
    )
    sub_ranked.to_csv(args.outdir / "csr_init_methods_secondary.csv", index=False)
    scatter_plot(ranked, args.outdir / "csr_init_methods_scatter.png")

    print()
    print(RULE)
    print(f"wrote CSVs + scatter PNG to {args.outdir}")
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
