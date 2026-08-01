"""Analyze the NEH-CP pass-count / chaining run.

Config under test: ``metadata/20260801/neh_cp_seq_full_compare.yaml``.
Predecessors: ``plans/analysis/20260801/neh_cp_seq_source_full.md`` (which mode
is best, on the 1440 grid) and ``plans/analysis/20260801/neh_cp_seq_replicate.md``
(what run-to-run noise costs a scenario mean).

Those two ran NEH-CP **once** and stopped the flow right after it. This run asks
the next question on one fixed full flow
(``dispatch_v4 -> MCF-LB -> FMM -> NEH-CP(s) -> ISW-CP -> base CP``):
**does running NEH-CP more than once, re-deriving the insertion order from the
schedule the previous pass produced, pay for itself end to end?**

Seven scenarios: 3 single passes, 3 two-pass chains, 1 three-pass chain, on the
160-instance ``(T, R) = (0.6, 0.2)`` slice.

Two things this run measures that its predecessors could not, and one it cannot:

- The scenario cap ``0.09nc`` **binds** here (the tail keeps solving until it is
  hit), so every scenario spends the same wall clock. The comparison is therefore
  equal-*total*-budget: a chain buys its extra NEH passes out of the ISW-CP /
  base-CP tail. That makes the flow-level ranking a fair practical verdict.
- It is **not** equal-*allocation*, which is what the config header warns about:
  an n-pass chain also spends n x ``0.0108nc`` on NEH-CP, so a chain win
  confounds "re-deriving the order helps" with "more NEH-CP time helps".
  Block 5 separates what it can — whether the order actually moved between
  passes, and whether pass k improved on pass k-1 at all.

Blocks:

1. Integrity, budget binding, and where the wall clock went.
2. Flow-level scenario ranking (the practical verdict), by pass count.
3. Paired contrasts: every chain against each of its own component single
   passes, plus the all-pairs table.
4. The NEH chain's own output — RPDf at the end of the chain, and the per-pass
   trajectory.
5. Did chaining do anything? ``dist_to_prev_neh`` and pass-k-improved counts.
6. (n, c) cell decomposition (T and R are fixed by ``ins_filter``).
7. Oracle portfolios over the 7 scenarios.

Usage:
    uv run python scripts/20260801/analyze_neh_pass_chain.py <run_dir>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from ffc_ddw_sum_et._calc import rpd_f

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling helpers
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # analyze_dispatch_sweep
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "20260731"))
from analyze_dispatch_sweep import oracle_value  # noqa: E402  -- shared oracle
from analyze_neh_cp_seq_pilot import load_rpdf  # noqa: E402  -- shared loader

# scenario -> the ordered NEH-CP passes it runs
CHAINS: dict[str, tuple[str, ...]] = {
    "dv4_mcf_fmm_neh_cp_midpoint_seq_full": ("midpoint",),
    "dv4_mcf_fmm_neh_cp_first_stage_seq_full": ("first_stage",),
    "dv4_mcf_fmm_neh_cp_completion_seq_full": ("completion",),
    "dv4_mcf_fmm_neh_cp_completion_midpoint_seq_full": ("completion", "midpoint"),
    "dv4_mcf_fmm_neh_cp_completion_first_stage_seq_full": (
        "completion",
        "first_stage",
    ),
    "dv4_mcf_fmm_neh_cp_midpoint_first_stage_seq_full": ("midpoint", "first_stage"),
    "dv4_mcf_fmm_neh_cp_comp_mid_fs_seq_full": (
        "completion",
        "midpoint",
        "first_stage",
    ),
}
SINGLE = {passes[0]: name for name, passes in CHAINS.items() if len(passes) == 1}
GRID_SIZE = 160
TIE_TOL = 1e-9
MATCH_CSV = Path("benchmarks/PRA2017/pra2017_hybrid_match.csv")
BKS_CSV = Path("benchmarks/PRA2017/pra2017_bks_table.csv")
OUT_DIR = Path("analysis/20260801_neh_pass_chain")

DIAG_RE = re.compile(
    r"(?P<step>neh_cp_\w+?_seq): seq source=(?P<mode>\w+) .*?"
    r"dist_to_job_priority=(?P<job_priority>[\d.]+) "
    r"dist_to_prev_neh=(?P<prev_neh>[\d.]+|N/A)"
)


def n_passes(scenario: str) -> int:
    return len(CHAINS[scenario])


def label(scenario: str) -> str:
    return " -> ".join(CHAINS[scenario])


# --------------------------------------------------------------------------
# per-instance step trajectory
# --------------------------------------------------------------------------


def parse_obj_log(path: Path, passes: tuple[str, ...]) -> dict[str, float]:
    """Objective at each step boundary, split around the NEH-CP chain.

    ``obj_value.notes`` maps a timestamp to ``<step_idx>-<method>``. A step that
    never registered is simply absent (e.g. a base-CP step the scenario cap cut
    off).

    Passes are therefore numbered by the order they appear in the log, not by
    their position in ``passes``: if pass k registered nothing, pass k+1 is
    labelled ``pass{k}`` and the trajectory shifts by one. ``missing_passes``
    counts the shortfall so block 1 can report it — check that column before
    reading the per-pass trajectory. It is 0 across the run this script was
    written for.
    """
    payload = json.loads(path.read_text())
    data = {float(t): v for t, v in payload["obj_value"]["data"].items()}
    notes = sorted(
        (float(t), n) for t, n in payload["obj_value"].get("notes", {}).items()
    )
    out: dict[str, float] = {
        "flow_best": min(data.values()) if data else np.nan,
        "seed_obj": np.nan,
        "seed_t": np.nan,
        "neh_obj": np.nan,
        "neh_end_t": np.nan,
        "neh_seconds": np.nan,
        "missing_passes": 0,
    }
    neh_idx = [i for i, (_, lbl) in enumerate(notes) if "neh_cp" in lbl]
    for k in range(len(passes)):
        out[f"pass{k + 1}_obj"] = np.nan
        out[f"pass{k + 1}_seconds"] = np.nan
    if not neh_idx:
        out["missing_passes"] = len(passes)
        return out
    first = neh_idx[0]
    if first:
        out["seed_obj"] = data[notes[first - 1][0]]
        out["seed_t"] = notes[first - 1][0]
    # notes[first:] are the chain's passes in order; a pass that registered
    # nothing leaves the chain shorter than ``passes``.
    found = [notes[i] for i in neh_idx]
    out["missing_passes"] = len(passes) - len(found)
    prev_t = out["seed_t"] if first else 0.0
    for k, (t, _) in enumerate(found):
        out[f"pass{k + 1}_obj"] = data[t]
        out[f"pass{k + 1}_seconds"] = t - prev_t
        prev_t = t
    out["neh_obj"] = data[found[-1][0]]
    out["neh_end_t"] = found[-1][0]
    out["neh_seconds"] = found[-1][0] - (out["seed_t"] if first else 0.0)
    return out


def parse_diag(path: Path) -> list[dict[str, object]]:
    """One record per NEH-CP pass from the controller's diagnostic line."""
    rows = []
    for k, m in enumerate(DIAG_RE.finditer(path.read_text(errors="replace"))):
        prev = m.group("prev_neh")
        rows.append(
            {
                "pass_idx": k + 1,
                "step": m.group("step"),
                "mode": m.group("mode"),
                "dist_to_job_priority": float(m.group("job_priority")),
                "dist_to_prev_neh": np.nan if prev == "N/A" else float(prev),
            }
        )
    return rows


def collect(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    steps, diags = [], []
    for scen_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if scen_dir.name not in CHAINS:
            continue
        passes = CHAINS[scen_dir.name]
        for ins_dir in sorted(p for p in scen_dir.iterdir() if p.is_dir()):
            log = next(ins_dir.glob("*_obj_log.json"), None)
            if log is None:
                continue
            row = parse_obj_log(log, passes)
            row["scenarioName"] = scen_dir.name
            row["instanceName"] = ins_dir.name
            steps.append(row)
            ctrl = next(ins_dir.glob("*_SubroutineController.log"), None)
            if ctrl is not None:
                for rec in parse_diag(ctrl):
                    diags.append(
                        {
                            **rec,
                            "scenarioName": scen_dir.name,
                            "instanceName": ins_dir.name,
                        }
                    )
    return pd.DataFrame(steps), pd.DataFrame(diags)


def attach_bks(df: pd.DataFrame) -> pd.DataFrame:
    match = pd.read_csv(MATCH_CSV, dtype={"insIndex": str})
    match["instanceName"] = match["ffc_ddw_sum_et_filename"].str.removesuffix(".txt")
    bks = pd.read_csv(BKS_CSV, dtype={"insIndex": str})
    out = df.merge(match[["insIndex", "instanceName"]], on="instanceName", how="left")
    out = out.merge(bks[["insIndex", "n", "c", "T", "R", "BKS_data"]], on="insIndex")
    obj_cols = [c for c in out.columns if c.endswith("_obj") or c == "flow_best"]
    for col in obj_cols:
        name = "flow" if col == "flow_best" else col.removesuffix("_obj")
        out[f"rpdf_{name}"] = [
            np.nan if pd.isna(obj) else 100.0 * rpd_f(obj, ref)
            for obj, ref in zip(out[col], out["BKS_data"], strict=True)
        ]
    return out


# --------------------------------------------------------------------------
# paired statistics
# --------------------------------------------------------------------------


def paired(wide: pd.DataFrame, a: str, b: str) -> dict[str, object]:
    """Paired ``a`` minus ``b``; negative ``mean_diff`` means ``a`` is better."""
    d = (wide[a] - wide[b]).dropna()
    n = len(d)
    se = d.std(ddof=1) / np.sqrt(n) if n else np.nan
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


def verdicts(rows: list[dict[str, object]]) -> pd.DataFrame:
    out = pd.DataFrame(rows)
    out["verdict"] = np.where(
        out["ci95"].abs() >= out["mean_diff"].abs(),
        "indistinguishable",
        np.where(out["mean_diff"] < 0, "a better", "b better"),
    )
    return out


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------


def block1(flow: pd.DataFrame, steps: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 1 - integrity, budget binding, and where the clock went ===")
    g = flow.groupby("scenarioName")
    eff = pd.DataFrame(
        {
            "passes": [n_passes(s) for s in g.size().index],
            "rows": g.size(),
            "missing_obj": g["bestObj"].apply(lambda s: int(s.isna().sum())),
            "mean_elapsed": g["elapsedTime"].mean(),
            "mean_time_pct": g["time%"].mean() * 100.0,
            "min_time_pct": g["time%"].min() * 100.0,
        }
    )
    s = steps.groupby("scenarioName")
    eff["mean_neh_sec"] = s["neh_seconds"].mean()
    eff["mean_neh_end_t"] = s["neh_end_t"].mean()
    eff["missing_pass_rows"] = s["missing_passes"].apply(lambda x: int((x > 0).sum()))
    eff["mean_tail_sec"] = eff["mean_elapsed"] - eff["mean_neh_end_t"]
    eff = eff.sort_values(["passes", "mean_elapsed"])
    print(eff.round(3).to_string())

    bad = eff.index[eff["rows"] != GRID_SIZE].tolist()
    print(f"\nscenarios not covering the {GRID_SIZE}-instance slice: {bad or 'none'}")
    print(
        "budget binding: the 0.09nc cap binds when time% ~ 100. "
        f"min over all scenarios = {eff['min_time_pct'].min():.1f} %, "
        f"mean of means = {eff['mean_time_pct'].mean():.1f} %"
    )
    print(
        "read mean_neh_sec against mean_tail_sec: the chain's extra NEH time is "
        "bought out of the ISW-CP / base-CP tail"
    )
    return eff


def block2(flow: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 2 - flow-level scenario ranking (mean RPDf %) ===")
    g = flow.groupby("scenarioName")["rpdf"]
    rank = pd.DataFrame(
        {
            "passes": [n_passes(s) for s in g.mean().index],
            "chain": [label(s) for s in g.mean().index],
            "mean": g.mean(),
            "median": g.median(),
            "std": g.std(),
            "n": g.size(),
        }
    ).sort_values("mean")
    print(rank.round(3).to_string())
    by_k = rank.groupby("passes")["mean"].agg(["mean", "min", "max"])
    print("\nby pass count:")
    print(by_k.round(3).to_string())
    return rank


def block3(wide: pd.DataFrame, metric: str) -> pd.DataFrame:
    print(f"\n=== Block 3 - paired contrasts on {metric} ===")
    print("\n3a - each chain against its own component single passes:")
    rows = []
    for scen, passes in CHAINS.items():
        if len(passes) == 1:
            continue
        for mode in passes:
            row = paired(wide, scen, SINGLE[mode])
            row["chain"] = label(scen)
            row["component"] = mode
            rows.append(row)
    comp = verdicts(rows)
    cols = [
        "chain",
        "component",
        "mean_diff",
        "ci95",
        "sigma",
        "win",
        "tie",
        "loss",
        "verdict",
    ]
    print(comp[cols].round(3).to_string(index=False))

    print("\n3b - all pairs:")
    rows = []
    for a, b in combinations(CHAINS, 2):
        row = paired(wide, a, b)
        row["pair"] = f"{label(a)}  vs  {label(b)}"
        rows.append(row)
    allp = verdicts(rows).sort_values("mean_diff")
    print(
        allp[["pair", "mean_diff", "ci95", "sigma", "win", "tie", "loss", "verdict"]]
        .round(3)
        .to_string(index=False)
    )
    return pd.concat([comp, allp], ignore_index=True)


def block4(steps: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 4 - the NEH chain's own output ===")
    g = steps.groupby("scenarioName")
    cols = {
        "passes": [n_passes(s) for s in g.size().index],
        "chain": [label(s) for s in g.size().index],
        "mean_seed_rpdf": g["rpdf_seed"].mean(),
        "mean_neh_end_rpdf": g["rpdf_neh"].mean(),
        "mean_flow_rpdf": g["rpdf_flow"].mean(),
    }
    for k in (1, 2, 3):
        col = f"rpdf_pass{k}"
        if col in steps.columns:
            cols[f"mean_pass{k}"] = g[col].mean()
    out = pd.DataFrame(cols).sort_values(["passes", "mean_neh_end_rpdf"])
    print(out.round(3).to_string())
    print(
        "\nmean_pass<k> is the objective that pass k itself ended with. A pass "
        "that lands worse than the incumbent is discarded by the solution "
        "manager, so the next pass re-derives its order from the better of the "
        "two - a rise from pass k to pass k+1 is not a regression of the flow."
    )
    return out


def block5(steps: pd.DataFrame, diags: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 5 - did chaining actually change anything? ===")
    if diags.empty:
        print("no diagnostic lines found")
        return pd.DataFrame()
    d = diags[diags["pass_idx"] > 1]
    if d.empty:
        print("no chained scenarios")
        return pd.DataFrame()
    mv = (
        d.groupby(["scenarioName", "pass_idx"])["dist_to_prev_neh"]
        .agg(["size", "mean", "median", "min", "max"])
        .round(4)
    )
    print("\n5a - dist_to_prev_neh (0 = pass k re-derived the same order):")
    print(mv.to_string())

    print("\n5b - did pass k beat pass k-1's own output?")
    rows = []
    for scen, passes in CHAINS.items():
        if len(passes) == 1:
            continue
        sub = steps[steps["scenarioName"] == scen]
        for k in range(2, len(passes) + 1):
            a, b = f"rpdf_pass{k}", f"rpdf_pass{k - 1}"
            dd = (sub[a] - sub[b]).dropna()
            rows.append(
                {
                    "chain": label(scen),
                    "pass": k,
                    "n": len(dd),
                    "mean_delta": dd.mean(),
                    "better": int((dd < -TIE_TOL).sum()),
                    "same": int((dd.abs() <= TIE_TOL).sum()),
                    "worse": int((dd > TIE_TOL).sum()),
                }
            )
    imp = pd.DataFrame(rows)
    print(imp.round(3).to_string(index=False))
    return imp


def block6(flow: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 6 - (n, c) cells: mean RPDf % per scenario ===")
    print(
        f"T / R are fixed by ins_filter: T={sorted(flow['T'].unique())} "
        f"R={sorted(flow['R'].unique())}"
    )
    nc = flow.pivot_table(
        index=["n", "c"], columns="scenarioName", values="rpdf", aggfunc="mean"
    )
    nc.columns = [label(s) for s in nc.columns]
    nc["winner"] = nc.idxmin(axis=1)
    print(nc.round(2).to_string())
    return nc


def block7(wide: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 7 - oracle portfolios over the 7 scenarios ===")
    mat = wide[list(CHAINS)].dropna()
    mat.columns = [label(s) for s in mat.columns]
    best_single = float(mat.mean().min())
    is_min = mat.eq(mat.min(axis=1), axis=0)
    unique = is_min.sum(axis=1) == 1
    strict = mat[unique].idxmin(axis=1).value_counts()
    rows = []
    for k in (1, 2, 3):
        for combo in combinations(mat.columns, k):
            value = oracle_value(mat, combo)
            rows.append(
                {
                    "k": k,
                    "combo": " | ".join(combo),
                    "oracle_mean": value,
                    "gain_vs_best_single": value - best_single,
                    "strict_wins": int(strict.get(combo[0], 0)) if k == 1 else None,
                }
            )
    out = pd.DataFrame(rows)
    top = out.sort_values(["k", "oracle_mean"]).groupby("k").head(5)
    print(top.round(3).to_string(index=False))
    print(
        f"\nall-tie instances: {int((is_min.sum(axis=1) == len(mat.columns)).sum())} "
        f"of {len(mat)}; an oracle over k scenarios costs k x the budget - these "
        "are upper bounds, not runnable configurations"
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    flow = load_rpdf(args.run_dir)
    raw_steps, diags = collect(args.run_dir)
    steps = attach_bks(raw_steps)
    steps.to_csv(args.out_dir / "step_objectives.csv", index=False)
    diags.to_csv(args.out_dir / "seq_diagnostics.csv", index=False)

    flow_wide = flow.pivot(index="insIndex", columns="scenarioName", values="rpdf")
    neh_wide = steps.pivot(
        index="instanceName", columns="scenarioName", values="rpdf_neh"
    )

    eff = block1(flow, steps)
    rank = block2(flow)
    contrasts_flow = block3(flow_wide, "flow RPDf (bestObj)")
    neh_out = block4(steps)
    contrasts_neh = block3(neh_wide, "NEH-chain RPDf (the chain's own output)")
    chained = block5(steps, diags)
    nc = block6(flow)
    oracle = block7(flow_wide)

    for name, frame, idx in [
        ("effort.csv", eff, True),
        ("scenario_ranking.csv", rank, True),
        ("contrasts_flow.csv", contrasts_flow, False),
        ("neh_chain_output.csv", neh_out, True),
        ("contrasts_neh.csv", contrasts_neh, False),
        ("chain_effect.csv", chained, False),
        ("nc_cells.csv", nc, True),
        ("oracle_portfolios.csv", oracle, False),
    ]:
        frame.to_csv(args.out_dir / name, index=idx)
    print(f"\nwrote 10 CSVs to {args.out_dir}")


if __name__ == "__main__":
    main()
