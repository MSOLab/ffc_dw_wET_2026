"""Merge SW-CP TL-policy runs into one kappa sweep and report mean RPDf.

The kappa sweep was split across two runs that share a base incumbent, the same
1440-instance grid, and disjoint scenario names:

    output/20260709_sw_cp_tl_test/<ts>/      p50, p60, p70, kappa_0.002 .. 0.008
    output/20260710_sw_cp_tl_kappa_0.005/<ts>/   kappa_0.005

Concatenating their ``<ts>_summary.csv`` therefore yields a single 8-scenario
sweep. ``kappa_*`` scenarios vary ``non_time_fixed_op_time_limit_multiplier``;
``p50/p60/p70`` are percentile TL baselines that do not sit on the kappa axis,
so they are drawn as reference lines rather than sweep points.

BKS join and the RPDf formula are imported from ``build_results_index`` rather
than re-derived, so this script cannot drift from the weekly-review pipeline.

Every instance is scored, matching ``aggregate_results_index.py``. The symmetric
formula ``(bestObj - BKS) / ((bestObj + BKS) / 2)`` is bounded on [-2, +2], so
``BKS = 0`` needs no special handling: ``BKS = 0 < bestObj`` pins RPDf to +2, and
``bestObj = BKS = 0`` is defined as 0. On this grid all 58 ``BKS = 0`` instances
have ``bestObj == 0`` under every scenario, i.e. every policy solves them to
optimality; their RPDf is 0 everywhere, so they shift each mean toward 0 by the
same weight and leave the within-slice ranking intact.

Note on faceting: ``timelimit`` is *derived* (0.09 * n * c), so TL=45 mixes
(n=50, c=10) with (n=100, c=5). Facets are cut on (n, c), never on timelimit.

Every scenario is reported on three instance slices (see ``DEFAULT_SLICES``);
``--t`` / ``--r`` replace them with one custom slice, matching the flag names
in ``analyze_dispatch_sweep.py``. The slice matters: the full grid and the
T=0.6 subsets do not agree on which scenario wins.

Usage:
    uv run python scripts/analyze_kappa_sweep.py <run_dir> <run_dir> [...] \
        [--outdir analysis/kappa_sweep_20260710] [--t 0.6] [--r 0.2]

Outputs (under --outdir):
    kappa_sweep_long.csv            one row per (run, scenario, instance)
    kappa_sweep_by_scenario.csv     one row per (slice, scenario)
    kappa_sweep_by_scenario_nc.csv  one row per (slice, scenario, n, c)
    kappa_sweep_by_slice.png        mean RPDf vs kappa, one panel per slice
    kappa_sweep_rpdf_<slice>.png    mean RPDf vs kappa, small multiples by (n, c)
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

REPO_ROOT = Path(__file__).resolve().parent.parent

# scripts/ is not an importable package; load the sibling module by path.
_spec = importlib.util.spec_from_file_location(
    "build_results_index", REPO_ROOT / "scripts" / "build_results_index.py"
)
assert _spec and _spec.loader
_bri = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bri)

BASELINES = ("p50", "p60", "p70")
_KAPPA_RE = re.compile(r"^kappa_([0-9.]+)$")

# The three instance slices compared by default: the full grid, the hardest
# tardiness factor, and the tightest due-range within it. Sizes are 1440 / 480
# / 160 -- every instance is scored, none dropped.
DEFAULT_SLICES: tuple[tuple[str, dict[str, float]], ...] = (
    ("all", {}),
    ("T=0.6", {"T": 0.6}),
    ("T=0.6,R=0.2", {"T": 0.6, "R": 0.2}),
)

# Okabe-Ito subset; validated (light surface): lightness band, chroma floor,
# and CVD separation all pass. #CC79A7 warns on contrast, so every baseline
# carries a direct label -- identity never rests on color alone.
KAPPA_COLOR = "#0072B2"
BASELINE_STYLE = {
    "p50": ("#D55E00", (0, (6, 2))),
    "p60": ("#009E73", (0, (3, 2))),
    "p70": ("#CC79A7", (0, (1, 2))),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="analyze_kappa_sweep")
    parser.add_argument(
        "run_dirs",
        type=Path,
        nargs="+",
        help="timestamped run dirs, each holding <ts>_summary.csv",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=REPO_ROOT / "analysis" / "kappa_sweep_20260710",
        help="directory for the merged CSVs and the plot",
    )
    parser.add_argument(
        "--t", type=float, default=None, help="filter tardiness factor T"
    )
    parser.add_argument("--r", type=float, default=None, help="filter due-range R")
    return parser.parse_args()


def resolve_slices(t: float | None, r: float | None) -> list[tuple[str, dict]]:
    """A custom single slice when --t/--r is given, else the default three."""
    if t is None and r is None:
        return list(DEFAULT_SLICES)
    spec = {k: v for k, v in (("T", t), ("R", r)) if v is not None}
    label = ",".join(f"{k}={v:g}" for k, v in spec.items())
    return [(label, spec)]


def apply_slice(df: pd.DataFrame, spec: dict[str, float]) -> pd.DataFrame:
    for column, value in spec.items():
        df = df[df[column] == value]
    return df


def slugify(label: str) -> str:
    return label.replace("=", "").replace(",", "_").replace(".", "p")


def _summary_csv(run_dir: Path) -> Path:
    """The single <ts>_summary.csv a run dir holds."""
    matches = sorted(run_dir.glob("*_summary.csv"))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"expected exactly one *_summary.csv in {run_dir}, found {len(matches)}"
        )
    return matches[0]


def load_runs(run_dirs: list[Path]) -> pd.DataFrame:
    """Concatenate run summaries, joining BKS and computing RPDf as the index does."""
    bench = _bri.load_benchmark_tables()
    frames = []
    for run_dir in run_dirs:
        path = _summary_csv(run_dir)
        df = pd.read_csv(path).merge(bench, on="instanceName", how="left")
        if df["BKS_data"].isna().any():
            missing = int(df["BKS_data"].isna().sum())
            raise ValueError(f"{path.name}: {missing} instances missing BKS_data")
        raw = (df["bestObj"] - df["BKS_data"]) / ((df["bestObj"] + df["BKS_data"]) / 2)
        # best=0 and BKS=0 -> RPDf defined as 0 (same corner case as the index).
        df["RPDf_BKS_data"] = raw.where(
            ~((df["bestObj"] == 0) & (df["BKS_data"] == 0)), 0.0
        )
        df.insert(0, "runTimestamp", path.stem.removesuffix("_summary"))
        frames.append(df)

    merged = pd.concat(frames, ignore_index=True)
    overlap = merged.groupby("scenarioName")["runTimestamp"].nunique()
    if (overlap > 1).any():
        clashing = sorted(overlap[overlap > 1].index)
        raise ValueError(f"scenario name(s) appear in more than one run: {clashing}")
    return merged


def kappa_of(scenario: str) -> float | None:
    match = _KAPPA_RE.match(scenario)
    return float(match.group(1)) if match else None


def aggregate(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Group means at full precision.

    Deliberately unrounded: ``p60`` and ``kappa_0.005`` differ by 1e-05 mean RPDf
    on the T=0.6 slice, so rounding to 4 dp before ``idxmin()`` would tie them
    and let row order pick the winner. Round at display time only.
    """
    return df.groupby(by, as_index=False).agg(
        instances=("instanceName", "nunique"),
        mean_RPDf=("RPDf_BKS_data", "mean"),
        mean_bestObj=("bestObj", "mean"),
        mean_elapsed=("elapsedTime", "mean"),
    )


def _fmt(df: pd.DataFrame, columns: list[str]) -> str:
    """Render selected columns at review precision, leaving the data unrounded."""
    shown = df[columns].round({"mean_RPDf": 4, "mean_bestObj": 1, "mean_elapsed": 2})
    return shown.to_string(index=False)


def plot_sweep(by_nc: pd.DataFrame, out_png: Path) -> None:
    """Mean RPDf vs kappa, one panel per (n, c); baselines as reference lines."""
    combos = sorted(by_nc[["n", "c"]].drop_duplicates().itertuples(index=False))
    ncols = 4
    nrows = -(-len(combos) // ncols)
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(4.2 * ncols, 3.4 * nrows), sharex=True, squeeze=False
    )

    for ax, (n, c) in zip(axes.flat, combos):
        panel = by_nc[(by_nc.n == n) & (by_nc.c == c)]
        sweep = panel[panel.kappa.notna()].sort_values("kappa")

        ax.plot(
            sweep.kappa,
            sweep.mean_RPDf,
            marker="o",
            markersize=5,
            linewidth=2,
            color=KAPPA_COLOR,
            label="kappa sweep",
            zorder=3,
        )
        # Mark the newly added point so the reader sees what this run contributed.
        new = sweep[sweep.kappa == 0.005]
        if not new.empty:
            ax.plot(
                new.kappa,
                new.mean_RPDf,
                marker="o",
                markersize=10,
                markerfacecolor="none",
                markeredgewidth=2,
                color=KAPPA_COLOR,
                zorder=4,
            )

        # Reserve room on the right so the baseline labels sit inside the axes.
        xmin = sweep.kappa.min() if not sweep.empty else 0.002
        xmax = sweep.kappa.max() if not sweep.empty else 0.008
        span = xmax - xmin
        ax.set_xlim(xmin - 0.04 * span, xmax + 0.18 * span)

        for name in BASELINES:
            row = panel[panel.scenarioName == name]
            if row.empty:
                continue
            value = float(row.mean_RPDf.iloc[0])
            color, dash = BASELINE_STYLE[name]
            ax.axhline(value, color=color, linestyle=dash, linewidth=1.5, zorder=2)
            # Direct label: the contrast WARN on p70 forbids color-alone identity.
            ax.annotate(
                name,
                xy=(xmax, value),
                xytext=(3, 0),
                textcoords="offset points",
                va="center",
                fontsize=8,
                color=color,
            )

        ax.set_title(f"n={n}, c={c}   (TL={0.09 * n * c:g}s)", fontsize=10)
        ax.grid(alpha=0.25, linewidth=0.6)
        ax.spines[["top", "right"]].set_visible(False)

    for ax in axes.flat[len(combos) :]:
        ax.set_visible(False)
    for ax in axes[-1]:
        ax.set_xlabel("kappa  (non_time_fixed_op_time_limit_multiplier)")
    for row in axes:
        row[0].set_ylabel("mean RPDf  (lower = better)")

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper right", frameon=False)
    fig.suptitle(
        "SW-CP TL policy: mean RPDf vs kappa, with percentile baselines",
        fontsize=13,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def plot_slices(by_slice: pd.DataFrame, labels: list[str], out_png: Path) -> None:
    """Mean RPDf vs kappa, one panel per instance slice; baselines as reference lines.

    Each panel keeps its own y-scale: a harder slice sits at a different RPDf
    level, and forcing a shared scale would flatten the within-slice curvature
    that the comparison is about.
    """
    fig, axes = plt.subplots(
        1, len(labels), figsize=(4.6 * len(labels), 3.8), squeeze=False
    )

    for ax, label in zip(axes.flat, labels):
        panel = by_slice[by_slice["slice"] == label]
        sweep = panel[panel.kappa.notna()].sort_values("kappa")

        ax.plot(
            sweep.kappa,
            sweep.mean_RPDf,
            marker="o",
            markersize=5,
            linewidth=2,
            color=KAPPA_COLOR,
            label="kappa sweep",
            zorder=3,
        )
        new = sweep[sweep.kappa == 0.005]
        if not new.empty:
            ax.plot(
                new.kappa,
                new.mean_RPDf,
                marker="o",
                markersize=10,
                markerfacecolor="none",
                markeredgewidth=2,
                color=KAPPA_COLOR,
                zorder=4,
            )

        xmin, xmax = sweep.kappa.min(), sweep.kappa.max()
        span = xmax - xmin
        ax.set_xlim(xmin - 0.04 * span, xmax + 0.18 * span)

        for name in BASELINES:
            row = panel[panel.scenarioName == name]
            if row.empty:
                continue
            value = float(row.mean_RPDf.iloc[0])
            color, dash = BASELINE_STYLE[name]
            ax.axhline(value, color=color, linestyle=dash, linewidth=1.5, zorder=2)
            ax.annotate(
                name,
                xy=(xmax, value),
                xytext=(3, 0),
                textcoords="offset points",
                va="center",
                fontsize=8,
                color=color,
            )

        instances = int(panel.instances.iloc[0])
        ax.set_title(f"{label}   ({instances} instances)", fontsize=11)
        ax.set_xlabel("kappa")
        ax.grid(alpha=0.25, linewidth=0.6)
        ax.spines[["top", "right"]].set_visible(False)

    axes[0][0].set_ylabel("mean RPDf  (lower = better)")
    handles, plot_labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, plot_labels, loc="upper right", frameon=False)
    fig.suptitle("SW-CP TL policy: kappa sweep across instance slices", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def main() -> int:
    args = _parse_args()
    merged = load_runs(args.run_dirs)
    slices = resolve_slices(args.t, args.r)

    print(f"runs merged : {merged.runTimestamp.nunique()}")
    print(f"scenarios   : {', '.join(sorted(merged.scenarioName.unique()))}")
    print(f"instances   : {merged.instanceName.nunique()}")

    args.outdir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.outdir / "kappa_sweep_long.csv", index=False)

    scenario_frames, nc_frames = [], []
    for label, spec in slices:
        scored = apply_slice(merged, spec)
        # RPDf is bounded on [-2, +2], so nothing is dropped. Report the two
        # boundary cases instead: solved-to-zero rows, and rows pinned at +2.
        solved_zero = int(((scored.bestObj == 0) & (scored.BKS_data == 0)).sum())
        pinned = int((scored.RPDf_BKS_data == 2.0).sum())
        print(
            f"\nslice {label!r}: {scored.instanceName.nunique()} instances scored "
            f"({solved_zero} rows bestObj=BKS=0 -> RPDf 0"
            + (f", {pinned} rows pinned at RPDf +2" if pinned else "")
            + ")"
        )

        by_scenario = aggregate(scored, ["scenarioName"])
        by_scenario["kappa"] = by_scenario.scenarioName.map(kappa_of)
        by_scenario.insert(0, "slice", label)
        by_scenario = by_scenario.sort_values(
            ["kappa", "scenarioName"], na_position="first"
        )
        scenario_frames.append(by_scenario)

        by_nc = aggregate(scored, ["scenarioName", "n", "c"])
        by_nc["kappa"] = by_nc.scenarioName.map(kappa_of)
        by_nc.insert(0, "slice", label)
        nc_frames.append(by_nc)

        plot_sweep(by_nc, args.outdir / f"kappa_sweep_rpdf_{slugify(label)}.png")

        print(_fmt(by_scenario, ["scenarioName", "mean_RPDf", "mean_elapsed"]))

        ranked = by_scenario.sort_values("mean_RPDf")
        best, runner_up = ranked.iloc[0], ranked.iloc[1]
        margin = runner_up.mean_RPDf - best.mean_RPDf
        # Show the margin: a "win" of 1e-05 is a tie the display rounding hides.
        print(
            f"  best: {best.scenarioName} ({best.mean_RPDf:.4f}), "
            f"{margin:.2e} ahead of {runner_up.scenarioName}"
        )

    all_scenario = pd.concat(scenario_frames, ignore_index=True)
    all_scenario.to_csv(args.outdir / "kappa_sweep_by_scenario.csv", index=False)
    pd.concat(nc_frames, ignore_index=True).to_csv(
        args.outdir / "kappa_sweep_by_scenario_nc.csv", index=False
    )

    labels = [label for label, _ in slices]
    plot_slices(all_scenario, labels, args.outdir / "kappa_sweep_by_slice.png")

    print("\n--- mean RPDf, scenario x slice ---")
    pivot = all_scenario.pivot(
        index="scenarioName", columns="slice", values="mean_RPDf"
    ).reindex(columns=labels)
    print(pivot.round(4).to_string())
    print(f"\nwrote CSVs + {len(labels) + 1} plots to {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
