"""Analyze the CSR time-budget scaling sweep (plans/20260714/csr_tl_scaling_sweep.md).

The sweep varies one axis — the CSR budget fraction ``f`` (share of the standard
reference budget given to the coarsen_solve_reconstruct step) — over
{5,10,15,20,30}% while proportionally scaling the four inner TL knobs, across
K (coarsening ``factor``) ∈ {1,2,4,8} and two init flows
(``csr_full_d2wp`` = mcf→flip→neh→sw_cp→base_cp, ``csr_neh_d2wp`` = neh→sw_cp→
base_cp). The f=25% column is supplied by the prior fixed-budget run
(``csr_full_grid_k248``), which covered only K∈{2,4,8}; K=1 therefore has no
25% point.

This script reads the run's ``<ts>_rpdf_comparison.csv`` (emitted by
``orchestration/post_run_pivot.py``) and uses its precomputed ``RPDf_BKS_data``
column verbatim — the symmetric RPD ``2(obj-ref)/(obj+ref)`` with ``ref =
BKS_data`` — so this analysis cannot drift from the report pipeline. All
percentages below are ``RPDf_BKS_data * 100``. optimality gap is not used
(coarse ``time_factor>1`` makes ``obj_bound`` loose for K≥2; K=1 is the
exception but is not scored here).

Scenario names decode as ``csr_{full,neh}_d2wp_k{K}[_tl{FF}]`` where FF is the
zero-padded percent and an absent ``_tl`` suffix marks the prior run's 25%.

A single invocation reproduces the whole ``## 결과 (실행 후)`` section of the
plan — seven blocks:
  1. f→RPDf curve per (flow, K): mean% (median%) at each f.
  2. best f per (flow, K) + marginal Δmean per +5%p budget (diminishing returns).
  3. T-level decomposition: mean RPDf% by (flow, K, f) × T ∈ {0.2, 0.4, 0.6}.
  4. csr_full vs csr_neh paired comparison (same instances at matched (K, f)):
     aggregate mean gap and per-instance win/tie/loss, sliced by K, f, and T.
  5. sanity gate re-check: file counts for the two invariant warnings and
     Traceback / AssertionError (regression detector; expect 0 / 0 / 0 / 0).
  6. budget starvation: ``no feasible`` warning counts tallied by scenario.
  7. K=1 optimality: obj_value==obj_bound proof counts per K=1 scenario (valid
     only at K=1, where ``time_factor==1`` keeps obj_bound tight).

Blocks 1–4 read the sweep + prior ``*_rpdf_comparison.csv``; blocks 5–7 scan the
sweep run dir's ``*.log`` (via grep) and per-instance ``*_instance_result.yaml``.

Usage:
    uv run python scripts/analyze_csr_tl_scaling_sweep.py <sweep_run_dir> \
        [<prior_25pct_run_dir>]

    # defaults to the 2026-07-14 sweep + its 25% baseline run:
    uv run python scripts/analyze_csr_tl_scaling_sweep.py
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SWEEP_DIR = (
    REPO_ROOT / "output/20260714_csr_tl_scaling_sweep/20260714T234921_531156"
)
DEFAULT_PRIOR_DIR = (
    REPO_ROOT / "output/20260714_csr_full_grid_k248/20260714T184236_642971"
)

FF_TO_F = {"05": 5, "10": 10, "15": 15, "20": 20, "30": 30}
FS = [5, 10, 15, 20, 25, 30]
KS = [1, 2, 4, 8]
FLOWS = ("full", "neh")
TS = (0.2, 0.4, 0.6)
_SCENARIO_RE = re.compile(r"^csr_(full|neh)_d2wp_k(\d+)(?:_tl(\d+))?$")


def parse_scenario(name: str) -> tuple[str, int, int] | None:
    """``csr_full_d2wp_k2_tl05`` -> ('full', 2, 5); ``..._k8`` -> ('neh', 8, 25)."""
    m = _SCENARIO_RE.match(name)
    if not m:
        return None
    flow, k, ff = m.group(1), int(m.group(2)), m.group(3)
    return flow, k, (25 if ff is None else FF_TO_F[ff])


def find_rpdf_csv(run_dir: Path) -> Path:
    matches = sorted(run_dir.glob("*_rpdf_comparison.csv"))
    if not matches:
        raise FileNotFoundError(f"no *_rpdf_comparison.csv under {run_dir}")
    return matches[0]


def load_rows(run_dirs: list[Path]) -> list[dict]:
    """Flatten every rpdf_comparison row with a decodable scenario name."""
    rows: list[dict] = []
    for run_dir in run_dirs:
        with open(find_rpdf_csv(run_dir)) as fh:
            for r in csv.DictReader(fh):
                parsed = parse_scenario(r["scenarioName"])
                if parsed is None:
                    continue
                flow, k, f = parsed
                try:
                    rpdf = float(r["RPDf_BKS_data"]) * 100
                    obj = float(r["bestObj"])
                    T = float(r["T"])
                except (ValueError, KeyError):
                    continue
                rows.append(
                    {
                        "flow": flow,
                        "K": k,
                        "f": f,
                        "T": T,
                        "insIndex": r["insIndex"],
                        "rpdf": rpdf,
                        "obj": obj,
                    }
                )
    return rows


def _agg(rows: list[dict], pred) -> tuple[float, float, int] | None:
    vals = [r["rpdf"] for r in rows if pred(r)]
    if not vals:
        return None
    return mean(vals), median(vals), len(vals)


def report_curves(rows: list[dict]) -> None:
    print("### f→RPDf curve per (flow, K) — mean% (median%)")
    for flow in FLOWS:
        for k in KS:
            cells = []
            for f in FS:
                a = _agg(rows, lambda r: r["flow"] == flow and r["K"] == k and r["f"] == f)
                cells.append(f"{a[0]:.2f}({a[1]:.2f})" if a else "--")
            print(f"  {flow}_k{k}: " + " | ".join(f"f{f}={c}" for f, c in zip(FS, cells)))


def report_best_f(rows: list[dict]) -> None:
    print("\n### best f per (flow, K) by mean + marginal Δmean per +5%p budget")
    for flow in FLOWS:
        for k in KS:
            means = [
                (_agg(rows, lambda r: r["flow"] == flow and r["K"] == k and r["f"] == f) or (None,))[0]
                for f in FS
            ]
            present = [m for m in means if m is not None]
            if not present:
                continue
            best_f = FS[means.index(min(present))]
            deltas = [
                f"{means[i] - means[i - 1]:+.1f}"
                if means[i] is not None and means[i - 1] is not None
                else "--"
                for i in range(1, len(means))
            ]
            print(f"  {flow}_k{k}: best f={best_f}% | Δ(5→10→15→20→25→30): " + " ".join(deltas))


def report_t_decomposition(rows: list[dict]) -> None:
    print("\n### T-level decomposition — mean RPDf% by (flow, K, f) × T")
    for T in TS:
        print(f"  -- T={T} --")
        for flow in FLOWS:
            for k in KS:
                cells = []
                for f in FS:
                    a = _agg(
                        rows,
                        lambda r: r["flow"] == flow and r["K"] == k and r["f"] == f and r["T"] == T,
                    )
                    cells.append(f"{a[0]:.1f}" if a else "--")
                print(f"    {flow}_k{k}: " + " ".join(f"f{f}={c}" for f, c in zip(FS, cells)))


def _paired(rows: list[dict], pred):
    """full vs neh on matched (K, f, insIndex). tie tol = obj relative 1e-6."""
    by_cell: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        by_cell[(r["K"], r["f"], r["insIndex"])][r["flow"]] = r
    full_vals, neh_vals, wf, tie, wn = [], [], 0, 0, 0
    for (k, f, _ins), d in by_cell.items():
        if "full" not in d or "neh" not in d:
            continue
        rf, rn = d["full"], d["neh"]
        if not pred(k, f, rf["T"]):
            continue
        full_vals.append(rf["rpdf"])
        neh_vals.append(rn["rpdf"])
        of, on = rf["obj"], rn["obj"]
        if abs(of - on) <= 1e-6 * max(1.0, abs(of), abs(on)):
            tie += 1
        elif of < on:
            wf += 1
        else:
            wn += 1
    n = len(full_vals)
    if not n:
        return None
    return mean(full_vals), mean(neh_vals), wf, tie, wn, n


def _paired_line(rows: list[dict], label: str, pred) -> None:
    r = _paired(rows, pred)
    if r is None:
        print(f"  {label}: (no data)")
        return
    mf, mn, wf, tie, wn, n = r
    gap = mf - mn
    winner = "full" if gap < 0 else "neh"
    print(
        f"  {label}: full {mf:6.2f} | neh {mn:6.2f} | gap {gap:+6.2f} → {winner} | "
        f"win full/tie/neh = {wf}/{tie}/{wn} "
        f"({100 * wf / n:.0f}/{100 * tie / n:.0f}/{100 * wn / n:.0f}%)  n={n}"
    )


def report_flow_comparison(rows: list[dict]) -> None:
    print("\n### csr_full vs csr_neh — paired (gap = full - neh mean RPDf%; <0 = full wins)")
    print(" by K (all f):")
    for k in KS:
        _paired_line(rows, f"K={k}", lambda kk, f, T, k=k: kk == k)
    print(" by f (all K):")
    for f in FS:
        _paired_line(rows, f"f={f}", lambda k, ff, T, f=f: ff == f)
    print(" by T (all K, all f):")
    for T in TS:
        _paired_line(rows, f"T={T}", lambda k, f, TT, T=T: abs(TT - T) < 1e-9)
    print(" K × T at best f=30:")
    for k in KS:
        for T in TS:
            _paired_line(
                rows,
                f"K={k} T={T} f30",
                lambda kk, ff, TT, k=k, T=T: kk == k and ff == 30 and abs(TT - T) < 1e-9,
            )


def _grep_files(pattern: str, run_dir: Path) -> list[str]:
    """Paths of ``*.log`` files under run_dir containing pattern (fixed string)."""
    proc = subprocess.run(
        ["grep", "-rlF", "--include=*.log", pattern, str(run_dir)],
        capture_output=True,
        text=True,
    )
    return [ln for ln in proc.stdout.splitlines() if ln]


def report_sanity(run_dir: Path) -> None:
    """Two invariant warnings + Traceback/AssertionError file counts (regression gate)."""
    print("\n### sanity (gate re-check) — # of *.log files carrying each signature")
    for label, pat in (
        ("insert_idle_time left E/T", "left E/T"),
        ("post-process objective >", "post-process objective >"),
        ("Traceback", "Traceback"),
        ("AssertionError", "AssertionError"),
    ):
        print(f"  {label}: {len(_grep_files(pat, run_dir))}")


def report_starvation(run_dir: Path) -> None:
    """Budget-starvation warnings (`no feasible`) tallied by scenario dir."""
    print("\n### budget starvation — # of *.log with 'no feasible' by scenario")
    files = _grep_files("no feasible", run_dir)
    tally: dict[str, int] = defaultdict(int)
    for path in files:
        rel = Path(path).relative_to(run_dir)
        tally[rel.parts[0]] += 1
    if not tally:
        print("  (none)")
        return
    for scenario, cnt in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {scenario}: {cnt}")
    print(f"  total: {len(files)}")


def _obj_pair(result_yaml: Path) -> tuple[float, float] | None:
    """Read top-level obj_value / obj_bound without a full YAML parse."""
    ov = ob = None
    with open(result_yaml) as fh:
        for line in fh:
            if line.startswith("obj_value:"):
                ov = float(line.split(":", 1)[1])
            elif line.startswith("obj_bound:"):
                ob = float(line.split(":", 1)[1])
            if ov is not None and ob is not None:
                break
    if ov is None or ob is None:
        return None
    return ov, ob


def report_k1_optimality(run_dir: Path) -> None:
    """obj_value==obj_bound proof counts — valid ONLY at K=1 (time_factor==1)."""
    print("\n### K=1 optimality (obj_value==obj_bound; valid only at K=1)")
    scenarios = sorted(
        d.name
        for d in run_dir.iterdir()
        if d.is_dir() and _SCENARIO_RE.match(d.name) and _SCENARIO_RE.match(d.name).group(2) == "1"
    )
    for scenario in scenarios:
        opt = tot = 0
        for res in (run_dir / scenario).glob("*/*_instance_result.yaml"):
            pair = _obj_pair(res)
            if pair is None:
                continue
            ov, ob = pair
            tot += 1
            if abs(ov - ob) <= 1e-6 * max(1.0, abs(ov)):
                opt += 1
        if tot:
            print(f"  {scenario}: {opt}/{tot} optimal ({100 * opt / tot:.1f}%)")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "sweep_run_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_SWEEP_DIR,
        help=f"CSR TL-scaling sweep run dir (default: {DEFAULT_SWEEP_DIR}).",
    )
    p.add_argument(
        "prior_run_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_PRIOR_DIR,
        help=f"Prior 25%% fixed-budget run dir supplying the f=25 column "
        f"(default: {DEFAULT_PRIOR_DIR}). Pass 'none' to omit.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    run_dirs = [args.sweep_run_dir]
    if args.prior_run_dir and str(args.prior_run_dir).lower() != "none":
        run_dirs.append(args.prior_run_dir)
    rows = load_rows(run_dirs)
    fs_present = sorted({r["f"] for r in rows})
    print(f"rows: {len(rows)}  f-points: {fs_present}  run_dirs: {[d.name for d in run_dirs]}\n")
    report_curves(rows)
    report_best_f(rows)
    report_t_decomposition(rows)
    report_flow_comparison(rows)
    report_sanity(args.sweep_run_dir)
    report_starvation(args.sweep_run_dir)
    report_k1_optimality(args.sweep_run_dir)


if __name__ == "__main__":
    main()
