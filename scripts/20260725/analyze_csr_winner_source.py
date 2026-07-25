"""Tabulate *which inner solve_flow step won* for each CSR instance.

Answers "how deep into the inner ``solve_flow`` did the budget actually let the
search get?" -- the **algorithmic depth** diagnostic that objective means hide.

``coarsen_solve_reconstruct`` in ``solve_flow`` mode harvests one coarse
candidate per child registration, reconstructs each to the original scale, and
registers the argmin. It logs exactly one summary line per instance::

    coarsen_solve_reconstruct[solve_flow]: candidates=3 deduped=3 dropped=0 \
winner_source=2-run_flip_makespan_cp_from_incumbent winner_coarse_obj=... \
winner_original_obj=...

``winner_source`` is ``<step_idx>-<method>`` (plus a ``.<detail>`` suffix for
per-batch registrations, e.g. ``4-incremental_sw_cp.1-batch_002``), so the step
index *is* the depth the flow reached before the budget ran out. This script
scans those lines across a run and pivots them by scenario.

Why this is worth a standing script: at a short budget the same config degrades
to different algorithms depending on the coarsening factor. On the 2026-07-24
``lastsemi_fullgrid`` run at f=5 %, the (n=200, c=10) slice reads

    depth        mcf_lb  +flip  +neh_cp  +isw
    K=1              29    127       14     10
    K=8               1     10       50    119

i.e. K=1 never gets past ``run_flip_makespan_cp_from_incumbent`` while K=8
routinely reaches ``incremental_sw_cp``. Coarsening buys depth; whether that
depth pays for the lost resolution is a separate (objective) question. See
``plans/experiment/20260725/coarsening_short_budget_crossover.md``.

Instance metadata (n, c, T, R, RPDf, elapsedTime) is joined from the run's
``<ts>_rpdf_comparison.csv`` via ``pra2017_hybrid_match.csv``
(instance filename -> insIndex). The loader is imported from
``analyze_dispatch_sweep.py`` so the join cannot drift from the other analyses.

Usage:
    uv run python scripts/20260725/analyze_csr_winner_source.py <run_dir> \
        [--scenario PREFIX] [--n 200] [--c 10] [--t 0.6] [--r 0.2] \
        [--outdir analysis/<run_id>_winner_source]

Outputs (under --outdir):
    winner_source_long.csv         one row per (scenario, instance)
    winner_source_by_scenario.csv  scenario x depth counts (the pivot printed)
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import pandas as pd

# scripts/20260725/<this file> -- two levels of nesting below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

HYBRID_MATCH_CSV = REPO_ROOT / "benchmarks/PRA2017/pra2017_hybrid_match.csv"

# One summary line per instance; winner_source is None when nothing survived.
SUMMARY_RE = re.compile(
    r"coarsen_solve_reconstruct\[solve_flow\]: "
    r"candidates=(?P<candidates>\d+) "
    r"deduped=(?P<deduped>\d+) "
    r"dropped=(?P<dropped>\d+) "
    r"winner_source=(?P<winner_source>\S+)"
)
# "<idx>-<method>" with an optional ".<detail>" tail.
SOURCE_RE = re.compile(r"^(?P<idx>\d+)-(?P<method>[^.\s]+)(?:\.(?P<detail>\S+))?$")


def _load_module(name: str, relpath: str):
    """scripts/ is not an importable package; load a sibling module by path."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ads = _load_module("analyze_dispatch_sweep", "scripts/analyze_dispatch_sweep.py")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_winner_source(raw: str) -> tuple[int | None, str | None, str | None]:
    """``"4-incremental_sw_cp.1-batch_002"`` -> ``(4, "incremental_sw_cp", ...)``.

    Returns ``(None, None, None)`` for the literal ``"None"`` the controller
    logs when no candidate survived reconstruction.
    """
    if raw == "None":
        return None, None, None
    m = SOURCE_RE.match(raw)
    if not m:
        raise ValueError(f"unparseable winner_source: {raw!r}")
    return int(m["idx"]), m["method"], m["detail"]


def scan_run(run_dir: Path, scenario_prefix: str | None = None) -> pd.DataFrame:
    """Scan every ``*_SubroutineController.log`` under ``run_dir``.

    One row per (scenario, instance) that logged a solve_flow summary line.
    Scenarios whose CSR step ran on the legacy (non-``solve_flow``) path emit no
    such line and are simply absent -- reported by the caller, not silently
    treated as empty.
    """
    rows: list[dict] = []
    for scenario_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if scenario_prefix and not scenario_dir.name.startswith(scenario_prefix):
            continue
        for instance_dir in sorted(p for p in scenario_dir.iterdir() if p.is_dir()):
            for log_path in instance_dir.glob("*_SubroutineController.log"):
                text = log_path.read_text(errors="replace")
                for m in SUMMARY_RE.finditer(text):
                    idx, method, detail = parse_winner_source(m["winner_source"])
                    rows.append(
                        {
                            "scenarioName": scenario_dir.name,
                            "instanceName": instance_dir.name,
                            "candidates": int(m["candidates"]),
                            "deduped": int(m["deduped"]),
                            "dropped": int(m["dropped"]),
                            "winner_source": m["winner_source"],
                            "winner_depth": idx,
                            "winner_method": method,
                            "winner_detail": detail,
                        }
                    )
    return pd.DataFrame(rows)


def load_insindex_map() -> pd.DataFrame:
    """``instanceName`` (no ``.txt``) -> ``insIndex`` (int)."""
    df = pd.read_csv(HYBRID_MATCH_CSV, dtype={"insIndex": str})
    df["instanceName"] = df["ffc_ddw_sum_et_filename"].str.removesuffix(".txt")
    df["insIndex"] = df["insIndex"].astype(int)
    return df[["instanceName", "insIndex"]]


def join_metadata(scan: pd.DataFrame, run_dir: Path) -> pd.DataFrame:
    """Attach ``n, c, T, R, RPDf_BKS_data, elapsedTime`` from the run's CSV.

    Returns ``scan`` unchanged (plus an ``insIndex`` column) when the run has no
    ``*_rpdf_comparison.csv`` -- e.g. a run whose reporting stage never ran.
    """
    scan = scan.merge(load_insindex_map(), on="instanceName", how="left")
    missing = scan["insIndex"].isna().sum()
    if missing:
        raise ValueError(f"{missing} instance dirs are absent from {HYBRID_MATCH_CSV}")
    scan["insIndex"] = scan["insIndex"].astype(int)

    try:
        rpdf = _ads.load_rpdf(run_dir)
    except FileNotFoundError:
        print(
            f"[warn] no *_rpdf_comparison.csv under {run_dir}; "
            "slice filters and RPDf columns unavailable",
            file=sys.stderr,
        )
        return scan

    keep = [
        c
        for c in (
            "insIndex",
            "scenarioName",
            "n",
            "c",
            "totalMcCount",
            "T",
            "R",
            "W",
            "bestObj",
            "RPDf_BKS_data",
            "elapsedTime",
        )
        if c in rpdf.columns
    ]
    return scan.merge(rpdf[keep], on=["insIndex", "scenarioName"], how="left")


def filter_slice(
    df: pd.DataFrame,
    n: int | None,
    c: int | None,
    t: float | None,
    r: float | None,
) -> pd.DataFrame:
    """Restrict to an instance-parameter slice; ``--t/--r`` match the other scripts."""
    for col, val in (("n", n), ("c", c), ("T", t), ("R", r)):
        if val is None:
            continue
        if col not in df.columns:
            raise KeyError(f"cannot filter on {col!r}: metadata join unavailable")
        df = df[df[col] == val]
    if df.empty:
        raise ValueError(f"no rows for slice n={n}, c={c}, T={t}, R={r}")
    return df


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def depth_label(row: pd.Series) -> str:
    """``2`` / ``run_flip_makespan_cp_from_incumbent`` -> ``"2-run_flip_..."``."""
    if pd.isna(row["winner_depth"]):
        return "none (no candidate)"
    return f"{int(row['winner_depth'])}-{row['winner_method']}"


def build_pivot(df: pd.DataFrame) -> pd.DataFrame:
    """scenario x depth-label counts, columns ordered by step index."""
    labels = df.apply(depth_label, axis=1)
    pivot = pd.crosstab(df["scenarioName"], labels)
    ordered = sorted(
        pivot.columns,
        key=lambda s: (999, s) if s.startswith("none") else (int(s.split("-")[0]), s),
    )
    return pivot[ordered]


def report(df: pd.DataFrame, pivot: pd.DataFrame) -> None:
    print("\n=== winner_source counts by scenario ===")
    print(pivot.to_string())

    print("\n=== candidate count by scenario (mean / min / max) ===")
    print(df.groupby("scenarioName")["candidates"].agg(["mean", "min", "max"]).round(2))

    if "RPDf_BKS_data" in df.columns and df["RPDf_BKS_data"].notna().any():
        print("\n=== mean RPDf (pp) by scenario x winning depth ===")
        tmp = df.assign(depth=df.apply(depth_label, axis=1))
        table = (
            tmp.pivot_table(
                index="scenarioName",
                columns="depth",
                values="RPDf_BKS_data",
                aggfunc="mean",
            )
            * 100
        )
        print(table.round(2).to_string())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path, help="run directory (timestamp folder)")
    ap.add_argument("--scenario", help="keep only scenarios with this name prefix")
    ap.add_argument("--n", type=int, help="job-count slice")
    ap.add_argument("--c", type=int, help="stage-count slice")
    ap.add_argument("--t", type=float, help="tardiness-factor slice")
    ap.add_argument("--r", type=float, help="due-range slice")
    ap.add_argument(
        "--outdir", type=Path, help="default: analysis/<run_id>_winner_source"
    )
    args = ap.parse_args()

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        raise SystemExit(f"not a directory: {run_dir}")

    scan = scan_run(run_dir, args.scenario)
    if scan.empty:
        raise SystemExit(
            f"no coarsen_solve_reconstruct[solve_flow] summary lines under {run_dir} "
            "(scenarios using the legacy non-solve_flow CSR path log none)"
        )

    df = join_metadata(scan, run_dir)
    df = filter_slice(df, args.n, args.c, args.t, args.r)
    pivot = build_pivot(df)
    report(df, pivot)

    outdir = args.outdir or REPO_ROOT / "analysis" / f"{run_dir.name}_winner_source"
    outdir.mkdir(parents=True, exist_ok=True)
    df.to_csv(outdir / "winner_source_long.csv", index=False)
    pivot.to_csv(outdir / "winner_source_by_scenario.csv")
    print(f"\nwrote {outdir}/winner_source_long.csv ({len(df)} rows)")
    print(f"wrote {outdir}/winner_source_by_scenario.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
