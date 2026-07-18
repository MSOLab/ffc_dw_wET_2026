"""Merge the SW-CP TL capture-percentile scenarios into one p-axis sweep.

The seven p25..p75 scenarios were produced by two runs that share a base
incumbent and ``unfixed_batch_count_max=8``, with disjoint scenario names:

    output/20260709_sw_cp_tl_test/<ts>/            p50, p60, p70 (+ kappa_*)
    output/20260717_sw_cp_tl_p25_p75_u8/<ts>/      p25, p30, p40, p75

Concatenating their ``<ts>_summary.csv`` therefore yields all seven percentiles
on equal footing. A third run covers the same seven under
``unfixed_batch_count_max=12``:

    output/20260708_sw_cp_tl_test/<ts>/            p25..p75

Its scenario names collide with the u8 pair by design, so it is loaded
separately and joined per (scenario, instance) for the regime comparison.

Loading, the BKS join and the RPDf formula are imported from
``analyze_kappa_sweep`` rather than re-derived, so this script cannot drift from
the kappa sweep it is meant to sit alongside. Every instance is scored.

Usage:
    uv run python scripts/20260718/analyze_p_sweep.py \
        [--u8-run <dir> ...] [--u12-run <dir>] [--outdir <dir>]

Outputs (under --outdir):
    p_sweep_by_scenario.csv   one row per (slice, scenario), u8 p + kappa
                              mean RPDf and mean bestObj
    p_u8_vs_u12.csv           one row per (slice, p), both regimes + paired W/T/L
    p_sweep_u8_vs_u12.png     mean RPDf vs p, one panel per slice, both regimes
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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# scripts/ is not an importable package; load the sibling module by path.
_spec = importlib.util.spec_from_file_location(
    "analyze_kappa_sweep", REPO_ROOT / "scripts" / "20260706" / "analyze_kappa_sweep.py"
)
assert _spec and _spec.loader
_aks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_aks)

DEFAULT_U8_RUNS = (
    REPO_ROOT / "output/20260709_sw_cp_tl_test/20260710T003128_565779",
    REPO_ROOT / "output/20260717_sw_cp_tl_p25_p75_u8/20260717T012611_015148",
    REPO_ROOT / "output/20260710_sw_cp_tl_kappa_0.005/20260710T165804_500924",
)
# Named as a file, not a dir: this run also emits a per-step summary alongside
# the run-level one, which _summary_csv refuses to disambiguate.
DEFAULT_U12_RUN = REPO_ROOT / (
    "output/20260708_sw_cp_tl_test/20260708T215949_422005"
    "/20260708T215949_422005_summary.csv"
)

_P_RE = re.compile(r"^p([0-9]+)$")

# Okabe-Ito, matching analyze_kappa_sweep's validated pair; the two regimes are
# additionally separated by marker and dash so identity never rests on color.
U8_STYLE = ("#0072B2", "o", "solid")
U12_STYLE = ("#D55E00", "s", (0, (5, 2)))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="analyze_p_sweep")
    parser.add_argument(
        "--u8-run",
        dest="u8_runs",
        type=Path,
        action="append",
        help="run dir with unfixed_batch_count_max=8 (repeatable)",
    )
    parser.add_argument(
        "--u12-run",
        type=Path,
        default=None,
        help="run dir with unfixed_batch_count_max=12",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=REPO_ROOT / "analysis" / "20260718_sw_cp_tl_p_u8_merge",
        help="directory for the merged CSVs and the plot",
    )
    parser.add_argument(
        "--t", type=float, default=None, help="filter tardiness factor T"
    )
    parser.add_argument("--r", type=float, default=None, help="filter due-range R")
    return parser.parse_args()


def p_of(scenario: str) -> int | None:
    """The capture percentile a scenario name encodes, or None if it is not one."""
    match = _P_RE.match(scenario)
    return int(match.group(1)) if match else None


def by_scenario(df: pd.DataFrame, slices: list[tuple[str, dict]]) -> pd.DataFrame:
    """Mean RPDf per (slice, scenario), carrying the p / kappa axis coordinates.

    ``mean_bestObj`` is reported alongside, but rank on RPDf: the objective
    magnitude scales with instance size, so its mean is dominated by the large
    instances rather than weighting every instance equally.
    """
    rows = []
    for label, spec in slices:
        sliced = _aks.apply_slice(df, spec)
        for scenario, group in sliced.groupby("scenarioName"):
            rows.append(
                {
                    "slice": label,
                    "scenario": scenario,
                    "p": p_of(scenario),
                    "kappa": _aks.kappa_of(scenario),
                    "n_instances": len(group),
                    "mean_RPDf": group["RPDf_BKS_data"].mean(),
                    "mean_RPDf_pct": group["RPDf_BKS_data"].mean() * 100,
                    "mean_bestObj": group["bestObj"].mean(),
                }
            )
    return pd.DataFrame(rows)


def u8_vs_u12(
    u8: pd.DataFrame, u12: pd.DataFrame, slices: list[tuple[str, dict]]
) -> pd.DataFrame:
    """Per (slice, p): both regime means plus a paired per-instance win/tie/loss.

    The join is on (scenarioName, instanceName), so a p% present in only one
    regime drops out rather than being compared against a different percentile.
    """
    keys = ["scenarioName", "instanceName"]
    paired = u8[keys + ["RPDf_BKS_data", "T", "R"]].merge(
        u12[keys + ["RPDf_BKS_data"]], on=keys, suffixes=("_u8", "_u12")
    )
    rows = []
    for label, spec in slices:
        sliced = _aks.apply_slice(paired, spec)
        for scenario, g in sliced.groupby("scenarioName"):
            p = p_of(scenario)
            if p is None:
                continue
            delta = g["RPDf_BKS_data_u8"] - g["RPDf_BKS_data_u12"]
            rows.append(
                {
                    "slice": label,
                    "p": p,
                    "n_instances": len(g),
                    "mean_RPDf_pct_u8": g["RPDf_BKS_data_u8"].mean() * 100,
                    "mean_RPDf_pct_u12": g["RPDf_BKS_data_u12"].mean() * 100,
                    "delta_pct_u8_minus_u12": delta.mean() * 100,
                    "u8_wins": int((delta < 0).sum()),
                    "ties": int((delta == 0).sum()),
                    "u12_wins": int((delta > 0).sum()),
                }
            )
    return pd.DataFrame(rows).sort_values(["slice", "p"])


def plot_regimes(
    comparison: pd.DataFrame, slices: list[tuple[str, dict]], outdir: Path
) -> Path:
    """Mean RPDf vs p, one panel per slice, both regimes overlaid.

    Panels follow the caller's slice order (broadest first), not the
    alphabetical order a groupby would impose.
    """
    labels = [label for label, _ in slices]
    fig, axes = plt.subplots(
        1, len(labels), figsize=(4.2 * len(labels), 3.6), sharey=False
    )
    axes = [axes] if len(labels) == 1 else list(axes)
    for ax, label in zip(axes, labels):
        panel = comparison[comparison["slice"] == label].sort_values("p")
        for col, (color, marker, dash), name in (
            ("mean_RPDf_pct_u8", U8_STYLE, "max=8"),
            ("mean_RPDf_pct_u12", U12_STYLE, "max=12"),
        ):
            ax.plot(
                panel["p"],
                panel[col],
                color=color,
                marker=marker,
                linestyle=dash,
                label=name,
            )
        best = panel.loc[panel["mean_RPDf_pct_u8"].idxmin()]
        ax.annotate(
            f"best u8: p{int(best['p'])}",
            (best["p"], best["mean_RPDf_pct_u8"]),
            textcoords="offset points",
            xytext=(0, 26),
            ha="center",
            fontsize=8,
            color=U8_STYLE[0],
        )
        ax.set_title(f"slice: {label}  (n={int(panel['n_instances'].iloc[0])})")
        ax.set_xlabel("capture percentile p")
        ax.set_ylabel("mean RPDf (%)")
        ax.grid(alpha=0.3)
        ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    path = outdir / "p_sweep_u8_vs_u12.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> None:
    args = _parse_args()
    u8_runs = args.u8_runs or list(DEFAULT_U8_RUNS)
    u12_run = args.u12_run or DEFAULT_U12_RUN
    slices = _aks.resolve_slices(args.t, args.r)
    args.outdir.mkdir(parents=True, exist_ok=True)

    u8 = _aks.load_runs(u8_runs)
    u12 = _aks.load_runs([u12_run])

    scenario_table = by_scenario(u8, slices)
    scenario_table.to_csv(args.outdir / "p_sweep_by_scenario.csv", index=False)

    comparison = u8_vs_u12(u8, u12, slices)
    comparison.to_csv(args.outdir / "p_u8_vs_u12.csv", index=False)

    plot_path = plot_regimes(comparison, slices, args.outdir)

    for label, _ in slices:
        panel = scenario_table[scenario_table["slice"] == label]
        print(f"\n=== slice {label} (u8) ===")
        print(
            panel.sort_values("mean_RPDf_pct")[
                [
                    "scenario",
                    "p",
                    "kappa",
                    "n_instances",
                    "mean_RPDf_pct",
                    "mean_bestObj",
                ]
            ].to_string(index=False)
        )
    print("\n=== u8 vs u12 (paired) ===")
    print(comparison.to_string(index=False))
    print(f"\nwrote {args.outdir}/ (+ {plot_path.name})")


if __name__ == "__main__":
    main()
