"""Replicate check for the NEH-CP sequence-source full-grid analysis.

``20260801T102120_801587`` re-ran ``metadata/20260731/neh_cp_seq_source_compare.yaml``
byte-identically to ``20260801T012922_726471``, so the two runs are an
independent replicate pair over the whole 1440-instance grid. That is a
measurement the single-run analysis
(``plans/analysis/20260801/neh_cp_seq_source_full.md``) could not make: it had
to trust paired CIs computed *within* one run, which assume the only noise is
between instances. CP-SAT at 8 threads on wall-clock is nondeterministic, so a
scenario mean carries run-to-run noise too, and only a replicate exposes it.

Blocks:
  1  provenance - are the two runs really the same config?
  2  run-to-run noise floor on the NEH step's own output
  3  do the published scenario means reproduce?
  4  do the published mode contrasts reproduce, and what do they look like pooled?
  5  rank reproducibility across the two runs

Measurement surface is ``rpdf_neh`` - the NEH-CP step's own output, not the
flow's ``bestObj``. Result 0 of the analysis document established that the
flow-level number is the *seed's* objective whenever NEH-CP fails to beat it,
which mutes exactly the effect under test.

Usage:
    uv run python scripts/20260801/analyze_neh_seq_replicate.py
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

RUN_BASE = Path("output/20260731_neh_cp_seq_source_compare")
RUN_A = RUN_BASE / "20260801T012922_726471"
RUN_B = RUN_BASE / "20260801T102120_801587"
STEPS_A = Path("analysis/20260801_neh_cp_seq_full/step_objectives.csv")
STEPS_B = Path("analysis/20260801_neh_cp_seq_full_runB/step_objectives.csv")
OUT_DIR = Path("analysis/20260801_neh_cp_seq_replicate")

MODES = ("completion", "midpoint", "first_stage")
PREFIXES = {
    "neh_cp": "mcf_lb->fmm",
    "dv4_neh_cp": "dispatch_v4",
    "dv4_mcf_fmm_neh_cp": "dv4->mcf_lb->fmm",
}
BASELINE = "neh_cp_baseline"
TIE_TOL = 1e-9


def block1(run_a: Path, run_b: Path) -> None:
    print("=== Block 1 - provenance ===")
    digests = {}
    for label, run in (("A", run_a), ("B", run_b)):
        (cfg,) = run.glob("neh_cp_seq_source_compare.yaml")
        digests[label] = hashlib.sha256(cfg.read_bytes()).hexdigest()[:16]
        print(f"  run {label}  {run.name}  config sha256[:16] = {digests[label]}")
    verdict = "IDENTICAL" if digests["A"] == digests["B"] else "DIFFERENT"
    print(f"  config snapshots: {verdict}")
    if verdict != "IDENTICAL":
        raise SystemExit("runs do not share a config; not a replicate pair")


def load_steps(path: Path, label: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"insIndex": str})
    df["run"] = label
    return df[["run", "scenarioName", "instanceName", "n", "c", "T", "R", "rpdf_neh"]]


def block2(wide: pd.DataFrame) -> pd.DataFrame:
    """Run-to-run noise on the NEH step's own output."""
    print("\n=== Block 2 - run-to-run noise floor (A - B, same scenario+instance) ===")
    d = wide["A"] - wide["B"]
    per_scn = (
        d.groupby(level="scenarioName")
        .agg(
            n="size",
            mean_delta="mean",
            sd_delta="std",
            mean_abs=lambda s: s.abs().mean(),
            p95_abs=lambda s: s.abs().quantile(0.95),
            exact_tie=lambda s: int((s.abs() <= TIE_TOL).sum()),
        )
        .round(3)
    )
    print(per_scn.to_string())

    sd = d.std(ddof=1)
    se_scenario_mean = sd / np.sqrt(2) / np.sqrt(1440)
    print(
        f"\n  pooled over 10 scenarios x 1440 instances (n={len(d)}):"
        f"\n    sd of per-instance delta      {sd:8.3f} pp"
        f"\n    mean |delta|                  {d.abs().mean():8.3f} pp"
        f"\n    exact ties (deterministic)    {int((d.abs() <= TIE_TOL).sum()):8d}"
        f"\n  => run-to-run SE of a 1440-instance scenario mean  {se_scenario_mean:.4f} pp"
        f"\n  => 95% band on a single scenario mean              +/-{1.96 * se_scenario_mean:.3f} pp"
    )
    return per_scn


def block3(long: pd.DataFrame, per_scn_noise: pd.DataFrame) -> pd.DataFrame:
    """Do the published per-scenario means reproduce?"""
    print("\n=== Block 3 - scenario means, run A vs run B ===")
    means = long.pivot_table(
        index="scenarioName", columns="run", values="rpdf_neh", aggfunc="mean"
    )
    means["delta"] = means["A"] - means["B"]
    # paired SE of the A-B difference of means, per scenario
    means["se_delta"] = per_scn_noise["sd_delta"] / np.sqrt(per_scn_noise["n"])
    means["sigma"] = means["delta"] / means["se_delta"]
    means["reproduced"] = means["sigma"].abs() < 1.96
    means = means.sort_values("A")
    print(means.round(3).to_string())
    n_ok = int(means["reproduced"].sum())
    print(
        f"\n  {n_ok}/{len(means)} scenario means are within run-to-run noise;"
        f" max |shift| = {means['delta'].abs().max():.3f} pp"
    )
    return means


def paired(wide_mode: pd.DataFrame, a: str, b: str) -> dict[str, float]:
    d = (wide_mode[a] - wide_mode[b]).dropna()
    se = d.std(ddof=1) / np.sqrt(len(d))
    return {
        "mean_diff": d.mean(),
        "ci95": 1.96 * se,
        "sigma": d.mean() / se,
        "n": len(d),
    }


def block4(long: pd.DataFrame) -> pd.DataFrame:
    """Mode contrasts per run, and pooled over both runs."""
    print("\n=== Block 4 - mode contrasts: per run and pooled ===")
    rows = []
    for prefix, label in PREFIXES.items():
        for m_a, m_b in itertools.combinations(MODES, 2):
            s_a, s_b = f"{prefix}_{m_a}_seq", f"{prefix}_{m_b}_seq"
            rec = {"prefix": label, "pair": f"{m_a} - {m_b}"}
            for run in ("A", "B"):
                sub = long[long.run == run]
                w = sub.pivot(
                    index="instanceName", columns="scenarioName", values="rpdf_neh"
                )
                r = paired(w, s_a, s_b)
                rec[f"{run}_diff"] = r["mean_diff"]
                rec[f"{run}_ci95"] = r["ci95"]
            # pooled: treat (run, instance) as the pairing unit -> 2880 pairs
            w = long.pivot_table(
                index=["run", "instanceName"],
                columns="scenarioName",
                values="rpdf_neh",
            )
            r = paired(w, s_a, s_b)
            rec["pooled_diff"] = r["mean_diff"]
            rec["pooled_ci95"] = r["ci95"]
            rec["pooled_sigma"] = r["sigma"]
            rec["n_pooled"] = r["n"]
            rec["sign_agrees"] = np.sign(rec["A_diff"]) == np.sign(rec["B_diff"])
            rows.append(rec)
    out = pd.DataFrame(rows)
    print(
        out[
            [
                "prefix",
                "pair",
                "A_diff",
                "A_ci95",
                "B_diff",
                "B_ci95",
                "sign_agrees",
                "pooled_diff",
                "pooled_ci95",
                "pooled_sigma",
            ]
        ]
        .round(3)
        .to_string(index=False)
    )
    agree = int(out["sign_agrees"].sum())
    print(f"\n  sign agreement across the replicate: {agree}/{len(out)} contrasts")
    return out


def block5(long: pd.DataFrame) -> pd.DataFrame:
    """Rank reproducibility of the three modes within each prefix."""
    print("\n=== Block 5 - mode ranking per prefix, per run ===")
    rows = []
    for prefix, label in PREFIXES.items():
        for run in ("A", "B"):
            sub = long[(long.run == run)]
            means = {
                m: sub[sub.scenarioName == f"{prefix}_{m}_seq"].rpdf_neh.mean()
                for m in MODES
            }
            order = " < ".join(sorted(means, key=means.get))
            rows.append(
                {
                    "prefix": label,
                    "run": run,
                    **{f"{m}": means[m] for m in MODES},
                    "ranking": order,
                }
            )
    out = pd.DataFrame(rows)
    print(out.round(3).to_string(index=False))
    same = out.groupby("prefix").ranking.nunique().eq(1).all()
    print(f"\n  ranking identical in both runs for every prefix: {bool(same)}")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-a", type=Path, default=RUN_A)
    p.add_argument("--run-b", type=Path, default=RUN_B)
    p.add_argument("--steps-a", type=Path, default=STEPS_A)
    p.add_argument("--steps-b", type=Path, default=STEPS_B)
    p.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    block1(args.run_a, args.run_b)
    long = pd.concat(
        [load_steps(args.steps_a, "A"), load_steps(args.steps_b, "B")],
        ignore_index=True,
    )
    wide = long.pivot_table(
        index=["scenarioName", "instanceName"], columns="run", values="rpdf_neh"
    ).dropna()

    noise = block2(wide)
    means = block3(long, noise)
    contrasts = block4(long)
    ranks = block5(long)

    noise.to_csv(args.out_dir / "run_to_run_noise.csv")
    means.to_csv(args.out_dir / "scenario_mean_reproduction.csv")
    contrasts.to_csv(args.out_dir / "mode_contrast_reproduction.csv", index=False)
    ranks.to_csv(args.out_dir / "mode_ranking_per_run.csv", index=False)
    print(f"\nwrote 4 CSVs to {args.out_dir}")


if __name__ == "__main__":
    main()
