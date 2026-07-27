"""Four cross-run checks the usability sweep needs but `analyze_usability.py` does not emit.

`analyze_usability.py` answers Q2 (reconstruct 3-mode) and Q3 (all 139 vs
`m1_k1`) inside a single run. The write-up
(`plans/analysis/20260727/csr_usability_sweep.md`) additionally rests on four
numbers that either join *across* runs or pair two scenarios neither script
pairs:

    Q1              per-instance paired ``ceil - round`` at f=40, k in {2,4,8}
    dispatch gate   ``a_k1`` vs every ``m1_k1_f{FF}`` -- where the inner flow
                    starts to beat dispatch-only (plan section 4 (ii))
    repro gate      the 24 settings-identical scenarios shared with the
                    crossover ladder run (plan section 4)
    exp1 slice      the 20260724 3-way merge restricted to (T,R)=(0.6,0.2), the
                    only way to compare this run's reconstruct gaps with a
                    full-1440 analysis

`load_run` / `parse_scenario` come from the ladder script so the RPDf definition
cannot drift between documents.

Usage:
    uv run python scripts/20260727/analyze_usability_supplement.py \
        output/20260727_csr_usability_t06/20260727T224612_096605
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "20260726"))

from analyze_crossover_ladder import load_run  # noqa: E402

# The crossover ladder this run extends; 24 of its scenarios are settings-identical.
LADDER_RUN = REPO_ROOT / "output/20260725_crossover_ladder/20260726T173841_347539"
# The full-1440 3-way reconstruct merge (experiment 1).
EXP1_MERGE = REPO_ROOT / "output/20260724_merge_lastsemi_3way/20260724T203441_310017"

BUDGETS = ["01", "02", "03", "04", "05", "10", "20", "40"]
T06_R02 = {"T": 0.6, "R": 0.2}

# 18 byte-identical + 6 behaviourally identical (k=1 makes rounding an identity).
REPRO_SCENARIOS = (
    [f"a_k{k}_round" for k in (2, 4, 8)]
    + [f"b_k{k}_round" for k in (2, 4, 8)]
    + [f"m1_k{k}_round_f{f}" for k in (2, 4, 8) for f in ("01", "02", "03", "04")]
    + ["a_k1", "b_k1"]
    + [f"m1_k1_f{f}" for f in ("01", "02", "03", "04")]
)


def read_rpdf_csv(run_dir: Path) -> pd.DataFrame:
    """`load_run` without the scenario-name parsing.

    The 20260724 merge predates the ``{arm}_k{K}[_{mode}][_f{NN}]`` convention
    (``csr_k1_tl05_lastsemi``), so `load_run` rejects it. Only the RPDf
    definition matters here, and it is reproduced verbatim.
    """
    matches = sorted(run_dir.glob("*_rpdf_comparison.csv"))
    if not matches:
        raise SystemExit(f"no *_rpdf_comparison.csv under {run_dir}")
    df = pd.read_csv(matches[0])
    df["rpdf_pct"] = 100.0 * df["RPDf_BKS_data"]
    return df


def wide_rpdf(run_dir: Path) -> pd.DataFrame:
    """insIndex x scenarioName matrix of RPDf in percentage points."""
    df = load_run(run_dir)
    return df.pivot_table(index="insIndex", columns="scenarioName", values="rpdf_pct")


def paired(wide: pd.DataFrame, left: str, right: str) -> dict:
    """``left - right`` paired by insIndex; negative means `left` is better."""
    delta = (wide[left] - wide[right]).dropna()
    return {
        "left": left,
        "right": right,
        "n_paired": len(delta),
        "left_rpdf_pct": wide[left].mean(),
        "right_rpdf_pct": wide[right].mean(),
        "mean_d_pp": delta.mean(),
        "win": int((delta < 0).sum()),
        "tie": int((delta == 0).sum()),
        "loss": int((delta > 0).sum()),
    }


def q1_ceil_vs_round(wide: pd.DataFrame) -> pd.DataFrame:
    """Q1 -- the f=40 rounding probe, paired directly rather than via k=1."""
    return pd.DataFrame(
        paired(wide, f"m1_k{k}_ceil_f40", f"m1_k{k}_round_f40") for k in (2, 4, 8)
    )


def dispatch_gate(wide: pd.DataFrame) -> pd.DataFrame:
    """Where does the full inner flow start to beat dispatch-only?

    `a` has no f axis, so plan section 4 (ii) pairs it against every m1 budget
    and reads off the f where both the mean and the win count flip.
    """
    return pd.DataFrame(paired(wide, "a_k1", f"m1_k1_f{f}") for f in BUDGETS)


def repro_gate(wide: pd.DataFrame, ladder: pd.DataFrame) -> pd.DataFrame:
    """Same settings, two runs, one code boundary (2c7ef28) -- do they agree?"""
    rows = []
    for scenario in REPRO_SCENARIOS:
        both = pd.concat(
            {"new": wide[scenario], "old": ladder[scenario]}, axis=1
        ).dropna()
        rows.append(
            {
                "scenarioName": scenario,
                "n_paired": len(both),
                "new_rpdf_pct": both["new"].mean(),
                "old_rpdf_pct": both["old"].mean(),
                "d_pp": (both["new"] - both["old"]).mean(),
            }
        )
    return pd.DataFrame(rows)


def exp1_slice() -> pd.DataFrame:
    """Experiment 1 (full 1440, cumulative) restricted to this run's 160 cell.

    Only the k=1 rows are comparable: at k=1 rounding is an identity, so
    cumulative and round differ in name only, and the sole remaining difference
    against this run is the code version.
    """
    df = read_rpdf_csv(EXP1_MERGE)
    cell = df[(df["T"] == T06_R02["T"]) & (df["R"] == T06_R02["R"])]
    rows = []
    for scenario, group in df.groupby("scenarioName"):
        if "_k1_" not in scenario:
            continue
        sliced = cell[cell["scenarioName"] == scenario]
        rows.append(
            {
                "scenarioName": scenario,
                "full1440_rpdf_pct": group["rpdf_pct"].mean(),
                "n_full": len(group),
                "t06r02_rpdf_pct": sliced["rpdf_pct"].mean(),
                "n_slice": len(sliced),
            }
        )
    return pd.DataFrame(rows).sort_values("scenarioName")


def budget_realisation(run_dir: Path) -> pd.DataFrame:
    """Measured elapsed / outer timelimit, against the nominal f.

    Plan section 4 (grade 2, item 3): "40 %" must be shown to be measured, not
    nominal -- mcf_lb is non-interruptible so short budgets overshoot.
    """
    df = load_run(run_dir)
    k1 = df[(df["k"] == 1) & df["f"].notna()]
    out = (
        k1.groupby(["arm", "f"])["time%"]
        .mean()
        .mul(100.0)
        .reset_index()
        .rename(columns={"time%": "measured_pct"})
    )
    out["nominal_pct"] = out["f"].astype(float)
    return out.sort_values(["arm", "f"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--outdir", type=Path, default=None)
    args = parser.parse_args(argv)

    run_dir = args.run_dir.resolve()
    outdir = args.outdir or REPO_ROOT / "analysis" / f"{run_dir.name}_usability"
    outdir.mkdir(parents=True, exist_ok=True)

    wide = wide_rpdf(run_dir)
    tables = {
        "q1_ceil_vs_round_f40": q1_ceil_vs_round(wide),
        "dispatch_only_gate": dispatch_gate(wide),
        "repro_gate_vs_ladder": repro_gate(wide, wide_rpdf(LADDER_RUN)),
        "exp1_recon_t06_slice": exp1_slice(),
        "budget_realisation": budget_realisation(run_dir),
    }

    for name, table in tables.items():
        table.to_csv(outdir / f"{name}.csv", index=False)
        print(f"\n== {name}")
        print(table.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    print(f"\nwrote {len(tables)} CSVs to {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
