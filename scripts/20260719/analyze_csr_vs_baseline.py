"""Best CSR setting vs the EXISTING initialization methods, at matched budget.

Plan: ``plans/analysis/20260719/csr_triple_analysis_plan.md``
§"The baselines already span part of the budget axis".

Phase 1 compared the baselines against CSR at **K=4**, which Phase 2/3 showed is
far from the best K. This script redoes that comparison against **K=1**, the
setting that actually wins every equal-budget column, so the question "is CSR
better than what we already had?" is answered at CSR's best rather than at an
arbitrary K.

The existing methods are:

    mcf_lb           ~0        MCF LB only, no CP        (reference, no comparator)
    mcf_lb_fmm       0.009nc   MCF + flip-makespan CP    -> f = 10 %
    mcf_lb_fmm_25p   0.0225nc  same, budget-matched      -> f = 25 %
    neh_25p          0.0225nc  NEH CP, budget-matched    -> f = 25 %
    neh              0.027nc   NEH CP                    -> f = 30 %

so the baseline family already spans f ≈ 0 / 10 / 25 / 30 and **no new baseline
sweep is needed** -- CSR can be met head-to-head at three of its six budget
points. f = 5/15/20 have no baseline comparator and are omitted.

Budget parity is verified empirically, not assumed: the script prints mean
``elapsedTime`` for both sides of every pair. (They agree closely -- e.g. `neh`
22.35 s vs `F_k1@30` 23.46 s -- so these are matched-wall-clock comparisons, not
merely matched-nominal-budget ones.)

CROSS-RUN CAVEAT. The baselines live in the 07-13 run and the K=1 CSR scenarios
in the 07-14/15 runs, i.e. either side of commit ``9b7ad2a``. Plan Appendix A
measured that boundary directly: on the seed path it changed nothing for
``lookahead`` (the mode these scenarios use), and end-to-end it moved CSR quality
by −0.098 / −0.176 %p. The baselines do not coarsen, so the CSR-specific change
does not reach them at all. Both runs cover the identical 1440-instance grid
(asserted below). The join is therefore sound, and the effect size here is two
orders of magnitude larger than the boundary effect.

Scope, as everywhere in this analysis: single-step init flows, so this compares
**initialization quality under a fixed initialization budget**, not final
solution quality.

Usage:
    uv run python scripts/20260719/analyze_csr_vs_baseline.py \
        [--outdir analysis/20260719_csr_init]

Outputs (under --outdir):
    csr_vs_baseline.csv         one row per (slice, f, baseline, csr): means + paired w/t/l
    csr_vs_baseline_cells.csv   the same comparison per (T, R) cell
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pandas as pd

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

BASELINE_RUN = REPO_ROOT / "output/20260713_csr_init_methods/20260713T195341_009592"
SWEEP_RUN = REPO_ROOT / "output/20260714_csr_tl_scaling_sweep/20260714T234921_531156"
K1_RUN = REPO_ROOT / "output/20260714_csr_tl_scaling_sweep/20260715T183418_361919"

# The reference point: free, and the thing every other method must beat to
# justify its budget at all.
FREE_BASELINE = "mcf_lb"

# (f, baseline scenario, CSR scenario at the same budget). Only budgets where a
# baseline exists are comparable; f = 5/15/20 have no comparator.
MATCHED: tuple[tuple[int, str, str], ...] = (
    (10, "mcf_lb_fmm", "csr_full_d2wp_k1_tl10"),
    (25, "mcf_lb_fmm_25p", "csr_full_d2wp_k1_tl25"),
    (25, "neh_25p", "csr_full_d2wp_k1_tl25"),
    (30, "neh", "csr_full_d2wp_k1_tl30"),
)

# The *diagonal* read: CSR at its cheapest budget against baselines that were
# given 2-5x more. A win here is a budget-efficiency claim, not an equal-budget
# one -- and it does NOT hold uniformly, which is the point of reporting it per
# cell rather than as a single mean.
DIAGONAL_CSR = "csr_full_d2wp_k1_tl05"
DIAGONAL_BASELINES = ("mcf_lb_fmm", "mcf_lb_fmm_25p", "neh_25p", "neh")

DEFAULT_SLICES: tuple[tuple[str, dict[str, float]], ...] = (
    ("overall", {}),
    ("T=0.6", {"T": 0.6}),
    ("(T,R)=(0.6,0.2)", {"T": 0.6, "R": 0.2}),
)

T_VALUES = (0.2, 0.4, 0.6)
R_VALUES = (0.2, 0.6, 1.0)

RULE = "=" * 78


def load_all() -> pd.DataFrame:
    """Concatenate the baseline run and the two K=1 CSR runs onto one grid."""
    frames = [_ads.load_rpdf(p) for p in (BASELINE_RUN, SWEEP_RUN, K1_RUN)]
    grids = [frozenset(f[INSTANCE_COL].unique()) for f in frames]
    if len(set(grids)) != 1:
        sizes = [len(g) for g in grids]
        raise ValueError(f"runs do not share one instance grid (sizes {sizes})")
    df = pd.concat(frames, ignore_index=True)
    if df[METRIC].isna().any():
        raise ValueError(f"null {METRIC}; refusing to average a partial set")
    df["RPDf_pct"] = df[METRIC] * 100.0
    return df


def _slice(df: pd.DataFrame, spec: dict[str, float]) -> pd.DataFrame:
    for col, val in spec.items():
        df = df[df[col] == val]
    return df


def compare(df: pd.DataFrame, base: str, csr: str) -> dict:
    """Paired per-instance comparison of a baseline against a CSR scenario."""
    mat = df.pivot(index=INSTANCE_COL, columns=METHOD_COL, values="RPDf_pct")
    for name in (base, csr):
        if name not in mat.columns:
            raise KeyError(f"scenario {name!r} missing from the joined frame")
    pair = mat[[base, csr]].dropna()
    diff = pair[csr] - pair[base]
    elapsed = df.groupby(METHOD_COL)["elapsedTime"].mean()
    return {
        "n": int(len(pair)),
        "baseline_RPDf_pct": float(pair[base].mean()),
        "csr_RPDf_pct": float(pair[csr].mean()),
        "delta_pp": float(diff.mean()),
        "csr_wins": int((diff < 0).sum()),
        "ties": int((diff == 0).sum()),
        "baseline_wins": int((diff > 0).sum()),
        "baseline_elapsed": float(elapsed[base]),
        "csr_elapsed": float(elapsed[csr]),
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="analyze_csr_vs_baseline")
    p.add_argument(
        "--outdir", type=Path, default=REPO_ROOT / "analysis" / "20260719_csr_init"
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    df = load_all()

    print(RULE)
    print("BEST CSR SETTING (K=1) vs EXISTING INIT METHODS, at matched budget")
    print(RULE)
    print("Phase 1 compared the baselines against CSR at K=4. This redoes it at")
    print("K=1 -- the setting that wins every equal-budget column -- so CSR is")
    print("judged at its best, not at an arbitrary K.")
    print()
    print(f"grid: {df[INSTANCE_COL].nunique()} instances, shared by all three runs")
    free = df[df[METHOD_COL] == FREE_BASELINE]["RPDf_pct"].mean()
    print(f"free reference: {FREE_BASELINE} = {free:.2f} % (no CP, ~0 budget)")

    rows = []
    for label, spec in DEFAULT_SLICES:
        sliced = _slice(df, spec)
        print()
        print(RULE)
        print(f"slice: {label}")
        print(RULE)
        for f, base, csr in MATCHED:
            res = compare(sliced, base, csr)
            better = "CSR" if res["delta_pp"] < 0 else "baseline"
            print(f"\n  f={f}%  {base}  vs  {csr}")
            print(
                f"    {base:>16} : {res['baseline_RPDf_pct']:8.2f} %"
                f"   ({res['baseline_elapsed']:.1f} s)"
            )
            print(
                f"    {'CSR K=1':>16} : {res['csr_RPDf_pct']:8.2f} %"
                f"   ({res['csr_elapsed']:.1f} s)"
            )
            print(
                f"    {'delta':>16} : {res['delta_pp']:+8.2f} %p   -> {better} better"
            )
            print(
                f"    {'win/tie/loss':>16} : {res['csr_wins']} / {res['ties']} / "
                f"{res['baseline_wins']}   (CSR / tie / baseline, n={res['n']})"
            )
            rows.append({"slice": label, "f": f, "baseline": base, "csr": csr, **res})

    # --------------------------------------------------- (T, R) cell view
    # Phase 1 found mcf_lb_fmm_25p beats K=4 CSR outright in 3 of the 9 cells.
    # The question that matters for adoption is whether that survives at K=1.
    print()
    print(RULE)
    print("(T, R) cell view -- does the baseline still win any cell at K=1?")
    print(RULE)
    print("Phase 1 (K=4): mcf_lb_fmm_25p was the OUTRIGHT WINNER in 3 of 9 cells.")
    cell_rows = []
    for f, base, csr in MATCHED:
        print(f"\n  f={f}%  {csr}  minus  {base}   (negative = CSR better)")
        header = "    cell        " + "".join(f"{r:>10}" for r in R_VALUES)
        print(
            header.replace("0.2", "R=0.2")
            .replace("0.6", "R=0.6")
            .replace("1.0", "R=1.0")
        )
        for t in T_VALUES:
            line = f"    T={t:<10g}"
            for r in R_VALUES:
                cell = _slice(df, {"T": t, "R": r})
                res = compare(cell, base, csr)
                line += f"{res['delta_pp']:>10.2f}"
                cell_rows.append(
                    {
                        "f": f,
                        "baseline": base,
                        "csr": csr,
                        "T": t,
                        "R": r,
                        **res,
                    }
                )
            print(line)
        sub = [c for c in cell_rows if c["f"] == f and c["baseline"] == base]
        lost = [c for c in sub if c["delta_pp"] > 0]
        if lost:
            cells = ", ".join(f"(T={c['T']:g},R={c['R']:g})" for c in lost)
            print(f"    -> baseline still wins {len(lost)}/9 cells: {cells}")
        else:
            print("    -> CSR K=1 wins ALL 9 cells.")

    # ------------------------------------------------ diagonal (cheap CSR)
    print()
    print(RULE)
    print(f"DIAGONAL -- {DIAGONAL_CSR} (f=5 %) vs baselines given 2-5x more budget")
    print(RULE)
    print("A budget-efficiency read, NOT an equal-budget one. Reported per cell")
    print("because the aggregate mean hides a sign flip across R.")
    for base in DIAGONAL_BASELINES:
        res = compare(df, base, DIAGONAL_CSR)
        ratio = res["baseline_elapsed"] / res["csr_elapsed"]
        print(
            f"\n  vs {base}  ({res['baseline_elapsed']:.1f} s, {ratio:.1f}x CSR's budget)"
        )
        print(
            f"    mean delta {res['delta_pp']:+.2f} %p   "
            f"w/t/l {res['csr_wins']}/{res['ties']}/{res['baseline_wins']}"
        )
        lost = []
        for t in T_VALUES:
            line = f"    T={t:<4g}"
            for r in R_VALUES:
                cres = compare(_slice(df, {"T": t, "R": r}), base, DIAGONAL_CSR)
                line += f"{cres['delta_pp']:>10.2f}"
                cell_rows.append(
                    {
                        "f": 5,
                        "baseline": base,
                        "csr": DIAGONAL_CSR,
                        "T": t,
                        "R": r,
                        **cres,
                    }
                )
                if cres["delta_pp"] > 0:
                    lost.append(f"(T={t:g},R={r:g})")
            print(line)
        if lost:
            print(f"    -> baseline still wins {len(lost)}/9 cells: {', '.join(lost)}")
        else:
            print("    -> CSR wins ALL 9 cells despite the budget handicap.")

    out = pd.DataFrame(rows)
    out.to_csv(args.outdir / "csr_vs_baseline.csv", index=False)
    pd.DataFrame(cell_rows).to_csv(
        args.outdir / "csr_vs_baseline_cells.csv", index=False
    )
    print()
    print(RULE)
    print(f"wrote csr_vs_baseline{{,_cells}}.csv to {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
