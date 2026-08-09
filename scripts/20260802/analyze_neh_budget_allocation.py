"""Analyze the NEH-CP budget-allocation run (full 1440 grid).

Config under test: ``metadata/20260801/neh_cp_budget_allocation.yaml``.
Predecessor: ``plans/analysis/20260801/neh_cp_pass_chain.md`` — the 160-instance
``(T, R) = (0.6, 0.2)`` slice that ranked seven pass chains but could not say
*why* a chain moved the number, because every chain also spent more NEH-CP time
than the single pass it beat.

This run removes that confound. Four of the five arms hold the NEH-CP budget
fixed at ``0.0324nc`` and differ only in **how it is split**:

======================  ===========================  =================
arm                     NEH block                    NEH budget
======================  ===========================  =================
``comp_x1_base``        completion x1, ln 15         0.0108nc
``comp_x1_long``        completion x1, ln 15         0.0324nc
``comp_x3_flat``        completion x3, ln 15         0.0324nc
``comp_mid_fs``         comp -> mid -> fs, ln 15     0.0324nc
``comp_then_comp_2x``   comp, then comp ln 30        0.0324nc
======================  ===========================  =================

so the named contrasts are each exactly identified:

- ``x1_long  - x1_base``  : more NEH time, one pass. The allocation control the
  predecessor analysis listed as its first limitation.
- ``x3_flat  - x1_long``  : same budget, split into three re-derived passes.
  Isolates **re-derivation** from **NEH time**.
- ``mid_fs   - x3_flat``  : same budget, same pass count, sort key *changes*
  between passes. Isolates **changing the sort key** from **re-deriving it**.
  Nothing in the repo could answer this before.
- ``2x       - x3_flat``  : same budget, two uneven passes with a doubled batch
  size against three flat ones.
- ``mid_fs   - x1_base``  : the bridge to the predecessor run's headline pair.

The scenario cap is ``0.09nc`` for every arm, and the tail
(``incremental_sw_cp``) keeps solving until it is hit, so this is also an
equal-*total*-budget comparison: an arm's extra NEH time is bought out of its
own tail.

Blocks:

1. Integrity, budget binding, and where the wall clock went.
2. Flow-level arm ranking (the practical verdict).
3. Named contrasts + all pairs, on flow RPDf and on the NEH chain's own output.
4. The NEH chain's own output and its per-pass trajectory.
5. Mechanism: did re-deriving the order actually move it (``dist_to_prev_neh``),
   and did pass k improve on pass k-1?
6. (T, R) and (n, c) cell decomposition — the full grid, unlike the predecessor.
7. Oracle portfolios over the five arms.

Usage:
    uv run python scripts/20260802/analyze_neh_budget_allocation.py <run_dir>
"""

from __future__ import annotations

import argparse
import sys
from itertools import combinations
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "20260731"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "20260801"))
from analyze_dispatch_sweep import oracle_value  # noqa: E402  -- shared oracle
from analyze_neh_cp_seq_pilot import load_rpdf  # noqa: E402  -- shared loader
from analyze_neh_pass_chain import (  # noqa: E402  -- shared, so metrics cannot drift
    TIE_TOL,
    assign_pass_columns,
    attach_bks,
    paired,
    parse_diag,
    parse_obj_log,
    verdicts,
)

# arm -> (ordered NEH-CP passes, NEH budget in nc units, short label)
ARMS: dict[str, tuple[tuple[str, ...], float, str]] = {
    "dv4_mcf_fmm_comp_x1_base": (("completion",), 0.0108, "x1_base"),
    "dv4_mcf_fmm_comp_x1_long": (("completion",), 0.0324, "x1_long"),
    "dv4_mcf_fmm_comp_x3_flat": (("completion",) * 3, 0.0324, "x3_flat"),
    "dv4_mcf_fmm_comp_mid_fs": (
        ("completion", "midpoint", "first_stage"),
        0.0324,
        "mid_fs",
    ),
    "dv4_mcf_fmm_comp_then_comp_2x": (
        ("completion", "completion"),
        0.0324,
        "2x",
    ),
}

# name -> (a, b, what the contrast isolates). Negative mean_diff => a is better.
CONTRASTS: list[tuple[str, str, str]] = [
    ("dv4_mcf_fmm_comp_x1_long", "dv4_mcf_fmm_comp_x1_base", "3x NEH time, one pass"),
    (
        "dv4_mcf_fmm_comp_x3_flat",
        "dv4_mcf_fmm_comp_x1_long",
        "re-derivation, budget held",
    ),
    (
        "dv4_mcf_fmm_comp_mid_fs",
        "dv4_mcf_fmm_comp_x3_flat",
        "sort key changes vs repeats",
    ),
    (
        "dv4_mcf_fmm_comp_then_comp_2x",
        "dv4_mcf_fmm_comp_x3_flat",
        "2 uneven passes vs 3 flat",
    ),
    (
        "dv4_mcf_fmm_comp_then_comp_2x",
        "dv4_mcf_fmm_comp_x1_long",
        "split in 2 vs one long",
    ),
    (
        "dv4_mcf_fmm_comp_mid_fs",
        "dv4_mcf_fmm_comp_x1_base",
        "bridge to the 160-instance run",
    ),
]

GRID_SIZE = 1440
BIND_TOL = 0.99  # time% below this means the cap did not bind
OUT_DIR = Path("analysis/20260802_neh_budget_allocation")


def passes_of(arm: str) -> tuple[str, ...]:
    return ARMS[arm][0]


def short(arm: str) -> str:
    return ARMS[arm][2]


def collect(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-instance step trajectory and per-pass sequence diagnostics."""
    steps, diags = [], []
    for scen_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if scen_dir.name not in ARMS:
            continue
        passes = passes_of(scen_dir.name)
        scen_rows: list[dict[str, object]] = []
        for ins_dir in sorted(p for p in scen_dir.iterdir() if p.is_dir()):
            log = next(ins_dir.glob("*_obj_log.json"), None)
            if log is None:
                continue
            row = parse_obj_log(log, passes)
            row["scenarioName"] = scen_dir.name
            row["instanceName"] = ins_dir.name
            scen_rows.append(row)
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
        assign_pass_columns(scen_rows, len(passes))
        for row in scen_rows:
            del row["neh_steps"]
        steps.extend(scen_rows)
    return pd.DataFrame(steps), pd.DataFrame(diags)


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------


def block1(flow: pd.DataFrame, steps: pd.DataFrame, grid_size: int) -> pd.DataFrame:
    print("\n=== Block 1 - integrity, budget binding, and where the clock went ===")
    g = flow.groupby("scenarioName")
    eff = pd.DataFrame(
        {
            "arm": [short(s) for s in g.size().index],
            "passes": [len(passes_of(s)) for s in g.size().index],
            "rows": g.size(),
            "missing_obj": g["bestObj"].apply(lambda s: int(s.isna().sum())),
            "mean_elapsed": g["elapsedTime"].mean(),
            "mean_time_pct": g["time%"].mean() * 100.0,
            "unbound": g["time%"].apply(lambda s: int((s < BIND_TOL).sum())),
        }
    )
    s = steps.groupby("scenarioName")
    eff["mean_neh_sec"] = s["neh_seconds"].mean()
    eff["mean_neh_end_t"] = s["neh_end_t"].mean()
    eff["unrun_pass_rows"] = s["missing_passes"].apply(lambda x: int((x > 0).sum()))
    eff["mean_tail_sec"] = eff["mean_elapsed"] - eff["mean_neh_end_t"]
    # ARMS carries the arm's TOTAL NEH budget already, so no pass multiplier
    mean_nc = flow.groupby("scenarioName").apply(
        lambda d: (d["n"] * d["c"]).mean(), include_groups=False
    )
    eff["nominal_neh_sec"] = [ARMS[s][1] * mean_nc[s] for s in eff.index]
    eff["neh_sec_over_nominal"] = eff["mean_neh_sec"] / eff["nominal_neh_sec"]
    eff = eff.sort_values(["passes", "mean_neh_sec"])
    print(eff.round(3).to_string())

    bad = eff.index[eff["rows"] != grid_size].tolist()
    print(f"\narms not covering the {grid_size}-instance slice: {bad or 'none'}")
    long_arms = [a for a in eff.index if ARMS[a][1] == 0.0324]
    spread = eff.loc[long_arms, "mean_neh_sec"]
    print(
        "budget parity: the four 0.0324nc arms should agree on mean_neh_sec; "
        f"spread = {spread.max() - spread.min():.2f} s over "
        f"{spread.mean():.2f} s mean"
    )
    print(
        "neh_sec_over_nominal < 1 means the NEH block returned before spending "
        "its budget - batches that prove optimality early exit before their "
        "per-batch time limit"
    )
    print(
        f"cap binding: instances with time% < {BIND_TOL:.0%} per arm are in "
        "'unbound' - those are the early-stop instances that finish before the "
        "0.09nc cap, where an arm cannot spend its whole budget"
    )
    return eff


def block2(flow: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 2 - flow-level arm ranking (mean RPDf %) ===")
    g = flow.groupby("scenarioName")["rpdf"]
    rank = pd.DataFrame(
        {
            "arm": [short(s) for s in g.mean().index],
            "passes": [len(passes_of(s)) for s in g.mean().index],
            "neh_budget_nc": [ARMS[s][1] for s in g.mean().index],
            "mean": g.mean(),
            "median": g.median(),
            "std": g.std(),
            "n": g.size(),
        }
    ).sort_values("mean")
    print(rank.round(4).to_string())
    return rank


def block3(wide: pd.DataFrame, metric: str) -> pd.DataFrame:
    print(f"\n=== Block 3 - paired contrasts on {metric} ===")
    print("\n3a - named contrasts (negative mean_diff = the first arm is better):")
    rows = []
    for a, b, isolates in CONTRASTS:
        row = paired(wide, a, b)
        row["contrast"] = f"{short(a)} - {short(b)}"
        row["isolates"] = isolates
        rows.append(row)
    named = verdicts(rows)
    cols = [
        "contrast",
        "isolates",
        "mean_diff",
        "ci95",
        "sigma",
        "win",
        "tie",
        "loss",
        "verdict",
    ]
    print(named[cols].round(3).to_string(index=False))

    print("\n3b - all pairs:")
    rows = []
    for a, b in combinations(ARMS, 2):
        row = paired(wide, a, b)
        row["contrast"] = f"{short(a)} - {short(b)}"
        rows.append(row)
    allp = verdicts(rows).sort_values("mean_diff")
    print(
        allp[
            ["contrast", "mean_diff", "ci95", "sigma", "win", "tie", "loss", "verdict"]
        ]
        .round(3)
        .to_string(index=False)
    )
    return pd.concat([named, allp], ignore_index=True)


def block4(steps: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 4 - the NEH chain's own output ===")
    g = steps.groupby("scenarioName")
    cols = {
        "arm": [short(s) for s in g.size().index],
        "passes": [len(passes_of(s)) for s in g.size().index],
        "mean_seed_rpdf": g["rpdf_seed_best"].mean(),
        "mean_neh_inc_rpdf": g["rpdf_neh_best"].mean(),
        "mean_neh_last_rpdf": g["rpdf_neh"].mean(),
        "mean_flow_rpdf": g["rpdf_flow"].mean(),
        "mean_registrations": g["neh_registrations"].mean(),
        "unrun_pass_rows": g["missing_passes"].apply(lambda x: int((x > 0).sum())),
    }
    for k in (1, 2, 3):
        col = f"rpdf_pass{k}"
        if col in steps.columns:
            cols[f"n_pass{k}"] = g[col].apply(lambda s: int(s.notna().sum()))
            cols[f"mean_pass{k}"] = g[col].mean()
    out = pd.DataFrame(cols).sort_values("mean_neh_inc_rpdf")
    print(out.round(3).to_string())
    print(
        "\nmean_neh_inc_rpdf is the incumbent leaving the NEH block - what the "
        "ISW-CP tail inherits, and the metric to rank chains by. "
        "mean_neh_last_rpdf is only the LAST pass's own output, which penalises "
        "a chain whose final pass landed worse than a value the flow had "
        "already kept.\n"
        "seed -> neh_inc is what the NEH block bought; neh_inc -> flow is what "
        "the tail did afterwards.\n"
        "unrun_pass_rows are instances where the flow ENDED before the later "
        "passes (early-stop cells, tail included) - not passes that ran and "
        "found nothing. mean_pass<k> is therefore a mean over n_pass<k> "
        "instances only and the columns are NOT comparable across k; use the "
        "paired deltas in block 5b."
    )
    return out


def block5(steps: pd.DataFrame, diags: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 5 - mechanism: did re-deriving the order move it? ===")
    if diags.empty:
        print("no diagnostic lines found")
        return pd.DataFrame()
    d = diags[diags["pass_idx"] > 1]
    if d.empty:
        print("no chained arms")
        return pd.DataFrame()
    mv = d.groupby(["scenarioName", "pass_idx"])["dist_to_prev_neh"].agg(
        n="size",
        mean="mean",
        median="median",
        max="max",
        frac_zero=lambda s: float((s.abs() <= TIE_TOL).mean()),
    )
    print("\n5a - dist_to_prev_neh (0 = pass k re-derived the identical order):")
    print(mv.round(4).to_string())
    print(
        "\nfrac_zero is the decisive number for x3_flat: where it is 1.0 the "
        "extra passes re-ran NEH-CP on the *same* order, so any difference they "
        "made came from re-solving, not from a new insertion order."
    )

    print("\n5b - did pass k beat pass k-1's own output?")
    rows = []
    for arm in ARMS:
        passes = passes_of(arm)
        if len(passes) == 1:
            continue
        sub = steps[steps["scenarioName"] == arm]
        for k in range(2, len(passes) + 1):
            a, b = f"rpdf_pass{k}", f"rpdf_pass{k - 1}"
            has_a, has_b = sub[a].notna(), sub[b].notna()
            dd = (sub[a] - sub[b]).dropna()
            rows.append(
                {
                    "arm": short(arm),
                    "pass": k,
                    "n_both": len(dd),
                    "mean_delta": dd.mean(),
                    "better": int((dd < -TIE_TOL).sum()),
                    "same": int((dd.abs() <= TIE_TOL).sum()),
                    "worse": int((dd > TIE_TOL).sum()),
                    "silent_k": int((~has_a).sum()),
                    "silent_prev": int((~has_b).sum()),
                }
            )
    imp = pd.DataFrame(rows)
    print(imp.round(3).to_string(index=False))
    print(
        "\nmean_delta compares each pass's OWN output, so a positive value means "
        "pass k produced a worse schedule than pass k-1 did - the flow keeps the "
        "better of the two, so this is not a regression, it is wasted budget.\n"
        "silent_k counts instances where pass k never ran because the flow had "
        "already ended; they are excluded from n_both."
    )
    return imp


def block6(flow: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    print("\n=== Block 6 - cell decomposition (full grid) ===")
    tr = flow.pivot_table(
        index=["T", "R"], columns="scenarioName", values="rpdf", aggfunc="mean"
    )
    tr.columns = [short(s) for s in tr.columns]
    tr["winner"] = tr.idxmin(axis=1)
    print("\n6a - (T, R) cells, mean RPDf %:")
    print(tr.round(3).to_string())

    nc = flow.pivot_table(
        index=["n", "c"], columns="scenarioName", values="rpdf", aggfunc="mean"
    )
    nc.columns = [short(s) for s in nc.columns]
    nc["winner"] = nc.idxmin(axis=1)
    print("\n6b - (n, c) cells, mean RPDf %:")
    print(nc.round(3).to_string())
    print(
        "\n(n, c) is where comp_then_comp_2x's ln 30 second pass has to be read: "
        "a 30-job batch is a different fraction of the instance at n=50 than at "
        "n=200."
    )
    return tr, nc


def block7(wide: pd.DataFrame) -> pd.DataFrame:
    print("\n=== Block 7 - oracle portfolios over the five arms ===")
    mat = wide[list(ARMS)].dropna()
    mat.columns = [short(s) for s in mat.columns]
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
        f"of {len(mat)}; an oracle over k arms costs k x the budget - these are "
        "upper bounds, not runnable configurations"
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--t", type=float, default=None, help="restrict to this T (e.g. 0.6)"
    )
    parser.add_argument(
        "--r", type=float, default=None, help="restrict to this R (e.g. 0.2)"
    )
    args = parser.parse_args()

    slice_tag = "".join(
        p
        for p in (f"_t{args.t:g}" if args.t else "", f"_r{args.r:g}" if args.r else "")
    )
    out_dir = args.out_dir or Path(f"{OUT_DIR}{slice_tag}")
    out_dir.mkdir(parents=True, exist_ok=True)

    flow = load_rpdf(args.run_dir)
    raw_steps, diags = collect(args.run_dir)
    steps = attach_bks(raw_steps)

    # The pooled mean is dominated by the wide-spread T=0.2 cells, so every
    # block below is slice-aware; T / R are joined onto both frames already.
    for col, val in (("T", args.t), ("R", args.r)):
        if val is None:
            continue
        keep = set(steps.loc[steps[col] == val, "instanceName"])
        flow = flow[flow[col] == val]
        steps = steps[steps[col] == val]
        diags = diags[diags["instanceName"].isin(keep)] if not diags.empty else diags
    grid_size = len(flow) // len(ARMS)
    print(f"slice: T={args.t or 'all'} R={args.r or 'all'} -> {grid_size} instances")

    steps.to_csv(out_dir / "step_objectives.csv", index=False)
    diags.to_csv(out_dir / "seq_diagnostics.csv", index=False)

    flow_wide = flow.pivot(index="insIndex", columns="scenarioName", values="rpdf")
    neh_wide = steps.pivot(
        index="instanceName", columns="scenarioName", values="rpdf_neh_best"
    )

    eff = block1(flow, steps, grid_size)
    rank = block2(flow)
    contrasts_flow = block3(flow_wide, "flow RPDf (bestObj)")
    neh_out = block4(steps)
    contrasts_neh = block3(
        neh_wide, "NEH-block incumbent RPDf (what the ISW-CP tail inherits)"
    )
    chained = block5(steps, diags)
    tr, nc = block6(flow)
    oracle = block7(flow_wide)

    for name, frame, idx in [
        ("effort.csv", eff, True),
        ("arm_ranking.csv", rank, True),
        ("contrasts_flow.csv", contrasts_flow, False),
        ("neh_chain_output.csv", neh_out, True),
        ("contrasts_neh.csv", contrasts_neh, False),
        ("chain_effect.csv", chained, False),
        ("tr_cells.csv", tr, True),
        ("nc_cells.csv", nc, True),
        ("oracle_portfolios.csv", oracle, False),
    ]:
        frame.to_csv(out_dir / name, index=idx)
    print(f"\nwrote 11 CSVs to {out_dir}")


if __name__ == "__main__":
    main()
