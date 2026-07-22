"""Phase 2 of the CSR 3-phase analysis: RPDf vs coarsening factor K.

Plan: ``plans/analysis/20260719/csr_triple_analysis_plan.md`` §Phase 2.

At a fixed f = 25 % init budget, how does RPDf vary with K in {1,2,4,8,16,32}
across the two init flows -- monotone, U-shaped, or a plateau?

Budget parity holds across every scenario here (all 0.0225nc), so K is the only
axis moving. As in Phase 1 the numbers measure **init quality under a fixed init
budget**: the outer flow is a single ``coarsen_solve_reconstruct`` step and ~75 %
of the 0.09nc scenario cap is deliberately left unspent.

Two views, kept in SEPARATE FRAMES on purpose:

    primary    K=1,2,4,8            1440 instances   gap-fill + k248 runs
    secondary  K=1,2,4,8,16,32       160 instances   higher_k run + gap-fill

``csr_{full,neh}_d2wp_k{2,4,8}`` exists in *both* the 1440 ``full_grid_k248`` run
and the 160 ``higher_k_validation`` run. Concatenating the two would double-count
K=2,4,8 on the 160 subset, so the frames are never merged. (This is also why
``analyze_kappa_sweep.load_runs`` cannot be used here -- it raises on a scenario
appearing in more than one run.)

The 160-instance subset is exactly the ``(T=0.6, R=0.2)`` cell -- the hardest one
-- so its RPDf level is not comparable to the full-grid mean. It has ~1/9 the
sample, so run-to-run variance cancels correspondingly less: read it as
directional and let the 1440-instance primary view settle any disagreement.

``20260715T175237_658738`` is NOT a substitute for the K=1 gap-fill: it holds
only 2 instances (a smoke test). This script rejects any K=1 source that does not
cover the expected grid.

Usage:
    uv run python scripts/20260719/analyze_csr_k_range.py \
        [--k1-run <run_dir>] [--k248-run <run_dir>] [--higher-k-run <run_dir>] \
        [--outdir analysis/20260719_csr_k]

Outputs (under --outdir):
    csr_k_range.csv        one row per (view, slice, flow, K)
    csr_k_range.png        RPDf% vs log2(K), 1440 panel + 160 panel
"""

from __future__ import annotations

import argparse
import importlib.util
import re
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

DEFAULT_K1_RUN = (
    REPO_ROOT / "output/20260714_csr_tl_scaling_sweep/20260715T183418_361919"
)
DEFAULT_K248_RUN = (
    REPO_ROOT / "output/20260714_csr_full_grid_k248/20260714T184236_642971"
)
DEFAULT_HIGHER_K_RUN = (
    REPO_ROOT / "output/20260714_csr_higher_k_validation/20260714T154426_711694"
)

# csr_full_d2wp_k1_tl25 / csr_neh_d2wp_k8 -> (flow, K). The optional _tl25 suffix
# marks the gap-fill run's explicit 25 % budget; every other scenario is already
# at 25 %, so the suffix carries no extra axis.
_SCENARIO_RE = re.compile(r"^csr_(?P<flow>full|neh)_d2wp_k(?P<k>\d+)(?:_tl25)?$")

DEFAULT_SLICES: tuple[tuple[str, dict[str, float]], ...] = (
    ("all", {}),
    ("T=0.6", {"T": 0.6}),
    ("T=0.6,R=0.2", {"T": 0.6, "R": 0.2}),
)

FLOW_STYLE = {
    # Okabe-Ito; every line also carries a direct label, so identity never
    # rests on color alone.
    "full": ("#0072B2", "o", "-"),
    "neh": ("#D55E00", "s", "--"),
}

RULE = "=" * 78


def load_run(run_dir: Path) -> pd.DataFrame:
    """Load a run's rpdf_comparison frame, parsing (flow, K) from the name."""
    df = _ads.load_rpdf(run_dir)
    if df[METRIC].isna().any():
        raise ValueError(f"{run_dir}: null {METRIC}; refusing to average a partial set")
    parsed = df[METHOD_COL].str.extract(_SCENARIO_RE)
    if parsed["flow"].isna().any():
        bad = sorted(df.loc[parsed["flow"].isna(), METHOD_COL].unique())
        raise ValueError(f"{run_dir}: unparseable scenario name(s): {bad}")
    df["flow"] = parsed["flow"]
    df["K"] = parsed["k"].astype(int)
    df["RPDf_pct"] = df[METRIC] * 100.0
    df["source_run"] = run_dir.name
    return df


def _require_grid(df: pd.DataFrame, expected: int, what: str) -> None:
    """Reject a source that does not cover the instance grid it claims to.

    Guards against silently averaging the 2-instance smoke-test run
    (``20260715T175237_658738``) as if it were the real K=1 gap-fill.
    """
    got = df[INSTANCE_COL].nunique()
    if got != expected:
        raise ValueError(
            f"{what}: expected {expected} instances, found {got}. "
            "Refusing to aggregate over an incomplete grid."
        )


def aggregate(df: pd.DataFrame, view: str) -> pd.DataFrame:
    """Mean RPDf% per (slice, flow, K) over the three default slices."""
    rows = []
    for label, spec in DEFAULT_SLICES:
        sliced = df
        for col, val in spec.items():
            sliced = sliced[sliced[col] == val]
        if spec and sliced.empty:
            # A slice that matches nothing would aggregate to NaN and read like
            # a real result -- fail loudly instead.
            raise ValueError(f"{view}: slice {spec} matched no instances")
        grouped = (
            sliced.groupby(["flow", "K"])
            .agg(
                instances=(INSTANCE_COL, "nunique"),
                mean_RPDf_pct=("RPDf_pct", "mean"),
                mean_elapsed=("elapsedTime", "mean"),
            )
            .reset_index()
        )
        grouped.insert(0, "slice", label)
        grouped.insert(0, "view", view)
        rows.append(grouped)
    return pd.concat(rows, ignore_index=True)


def _shape_of(curve: pd.DataFrame) -> str:
    """Classify a K-curve as monotone / U-shaped / plateau, for the console read."""
    vals = curve.sort_values("K")["mean_RPDf_pct"].to_numpy()
    if len(vals) < 3:
        return "too few points"
    best = int(vals.argmin())
    spread = float(vals.max() - vals.min())
    if spread < 0.5:
        return "plateau (spread < 0.5 %p)"
    if best == 0:
        return "monotone worsening in K (K=1 best)"
    if best == len(vals) - 1:
        return "monotone improving in K (largest K best)"
    return f"U-shaped (interior minimum at K={int(curve.sort_values('K')['K'].iloc[best])})"


def plot_k_range(primary: pd.DataFrame, secondary: pd.DataFrame, out_png: Path) -> None:
    """RPDf% vs log2(K): the 1440-instance and 160-instance views side by side.

    The two panels do NOT share a y-axis: the 160-instance view is the hardest
    (T,R) cell, so its RPDf level is structurally higher and a shared axis would
    imply a comparison that is not being made.
    """
    slices = [label for label, _ in DEFAULT_SLICES]
    fig, axes = plt.subplots(
        len(slices), 2, figsize=(11.0, 3.3 * len(slices)), squeeze=False
    )
    views = [
        ("primary (1440 inst)", primary),
        ("secondary (160 inst = the (0.6,0.2) cell)", secondary),
    ]
    for row, slice_label in enumerate(slices):
        for col, (view_label, frame) in enumerate(views):
            ax = axes[row][col]
            sub = frame[frame["slice"] == slice_label]
            for flow, grp in sub.groupby("flow"):
                grp = grp.sort_values("K")
                color, marker, ls = FLOW_STYLE[flow]
                ax.plot(
                    grp["K"],
                    grp["mean_RPDf_pct"],
                    marker=marker,
                    color=color,
                    linestyle=ls,
                    label=f"csr_{flow}_d2wp",
                )
                best = grp.loc[grp["mean_RPDf_pct"].idxmin()]
                ax.annotate(
                    f"K={int(best['K'])}",
                    (best["K"], best["mean_RPDf_pct"]),
                    textcoords="offset points",
                    xytext=(4, -12),
                    fontsize=7,
                    color=color,
                )
            ax.set_xscale("log", base=2)
            ax.set_xticks(sorted(sub["K"].unique()))
            ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
            ax.set_xlabel("coarsening factor K (log2)")
            ax.set_ylabel("mean RPDf (%)")
            ax.set_title(f"{view_label} -- {slice_label}", fontsize=9)
            ax.grid(alpha=0.3)
            if row == 0 and col == 0:
                ax.legend(fontsize=8, frameon=False)
    fig.suptitle(
        "CSR: init quality vs coarsening factor K at f = 25 % budget\n"
        "panels do not share a y-axis (the 160-inst view is the hardest cell)",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="analyze_csr_k_range")
    p.add_argument("--k1-run", type=Path, default=DEFAULT_K1_RUN)
    p.add_argument("--k248-run", type=Path, default=DEFAULT_K248_RUN)
    p.add_argument("--higher-k-run", type=Path, default=DEFAULT_HIGHER_K_RUN)
    p.add_argument(
        "--outdir", type=Path, default=REPO_ROOT / "analysis" / "20260719_csr_k"
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    k1 = load_run(args.k1_run)
    k248 = load_run(args.k248_run)
    higher = load_run(args.higher_k_run)

    _require_grid(k1, 1440, f"K=1 gap-fill ({args.k1_run.name})")
    _require_grid(k248, 1440, f"K=2,4,8 grid ({args.k248_run.name})")
    _require_grid(higher, 160, f"higher-K validation ({args.higher_k_run.name})")

    print(RULE)
    print("PHASE 2 -- CSR K range at fixed f = 25 % budget")
    print(RULE)
    print(f"K=1  (1440) : {args.k1_run}")
    print(f"K=2,4,8 (1440): {args.k248_run}")
    print(f"K=2..32 (160) : {args.higher_k_run}")
    print()
    print("Budget parity: every scenario is 0.0225nc, so K is the only axis")
    print("moving. As in Phase 1 these are INIT-quality numbers, not final")
    print("solution quality (~75 % of the 0.09nc cap is left unspent by design).")

    # ------------------------------------------------------------- primary
    primary_df = pd.concat([k1, k248], ignore_index=True)
    # The two runs must not both supply the same (flow, K) or the mean would be
    # taken over a doubled frame.
    dupes = primary_df.groupby(["flow", "K"])["source_run"].nunique()
    if (dupes > 1).any():
        raise ValueError(
            f"(flow,K) supplied by >1 run: {dupes[dupes > 1].index.tolist()}"
        )
    primary = aggregate(primary_df, "primary_1440")

    # ----------------------------------------------------------- secondary
    subset_ids = set(higher[INSTANCE_COL].unique())
    if not subset_ids.issubset(set(k1[INSTANCE_COL].unique())):
        raise ValueError("the 160-instance subset is not contained in the K=1 grid")
    k1_subset = k1[k1[INSTANCE_COL].isin(subset_ids)]
    secondary_df = pd.concat([k1_subset, higher], ignore_index=True)
    secondary = aggregate(secondary_df, "secondary_160")

    cells = higher[["T", "R"]].drop_duplicates()
    print()
    print(
        f"secondary subset spans (T,R) = "
        f"{sorted(map(tuple, cells.to_numpy().tolist()))} "
        f"-- {len(subset_ids)} instances"
    )

    # -------------------------------------------------------------- report
    for view, frame in (
        ("PRIMARY (1440 inst)", primary),
        ("SECONDARY (160 inst)", secondary),
    ):
        print()
        print(RULE)
        print(f"{view} -- mean RPDf% by (flow, K)")
        print(RULE)
        for slice_label, _ in DEFAULT_SLICES:
            sub = frame[frame["slice"] == slice_label]
            pivot = sub.pivot(index="K", columns="flow", values="mean_RPDf_pct")
            print(f"\n  slice = {slice_label}  (n={int(sub['instances'].max())})")
            print("    " + pivot.round(3).to_string().replace("\n", "\n    "))
            for flow in sorted(sub["flow"].unique()):
                curve = sub[sub["flow"] == flow]
                best = curve.loc[curve["mean_RPDf_pct"].idxmin()]
                print(
                    f"    csr_{flow}_d2wp: best K={int(best['K'])} "
                    f"({best['mean_RPDf_pct']:.3f} %)  -- {_shape_of(curve)}"
                )

    print()
    print(RULE)
    print("Cross-view read")
    print(RULE)
    print("The primary view has 9x the sample and settles any disagreement; the")
    print("secondary view is directional and exists to extend the axis to K=32.")

    out = pd.concat([primary, secondary], ignore_index=True)
    out.to_csv(args.outdir / "csr_k_range.csv", index=False)
    plot_k_range(primary, secondary, args.outdir / "csr_k_range.png")
    print()
    print(f"wrote csr_k_range.csv + csr_k_range.png to {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
