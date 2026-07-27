"""Judge the W2 P1 gate on the tau=1 CSR-initializer budget curve.

The gate (plans/experiment/20260726/csr_init_tl_f35_f40.md) asks whether the
tau=1 CSR initializer beats the incumbent initializer ``best(MCF-LB -> FMM,
NEH-CP)`` -- measured in the same run as scenario ``c5_init_only`` -- in *all
nine* (T, R) cells while spending no more than 40 % of the 0.09nc budget::

    PASS(f)  <=>  for every (T, R) cell:  meanRPDf(csr_init_tau1_f) <= meanRPDf(c5_init_only)
    gate     <=>  some f <= 40 % satisfies PASS(f)

Both arms are initializer-only (no tail), so this is an initial-solution
quality comparison, not a final-objective one.

Cell means over 160 instances sit near the CSR batch CP noise floor, so the
gate uses the *sign* of the cell delta only; the emitted tables carry the
magnitudes and the paired win/tie/loss counts so a reader can see which cells
are decided and which are ties in all but name.

Usage:
    uv run python scripts/20260726/analyze_csr_init_tl_curve.py <run_dir> \
        [--outdir analysis/<run_id>_csr_init_tl_curve]

Outputs (under --outdir):
    gate_cells.csv      one row per (f, T, R) -- both arms' mean RPDf, delta, verdict
    f_curve.csv         one row per scenario -- pooled + per-T mean RPDf, elapsed
    win_tie_loss.csv    per-instance paired counts vs c5_init_only, pooled and per T
    inner_steps.csv     per (f, inner step) mean seconds and mean objective drop
    gate_verdict.txt    the human-readable verdict block printed to stdout

Exit code is 1 when no f <= 40 % passes the gate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

# scripts/20260726/<this file> -- two levels of nesting below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

BASELINE = "c5_init_only"
# "csr_init_tau1_f35" -> f = 35 (per cent of the 0.09nc outer budget)
CSR_RE = re.compile(r"^csr_init_tau1_f(?P<f>\d+)$")

# The gate's budget ceiling, in per cent of the 0.09nc outer time limit.
BUDGET_CEILING_PCT = 40

# Cell means over 160 instances are noisier than the pooled 1440-instance floor;
# deltas below this (in RPDf percentage points) are reported as "indistinct".
CELL_INDISTINCT_PP = 0.5


def load_run(run_dir: Path) -> pd.DataFrame:
    """Read the run's rpdf_comparison.csv (already carries T, R, n, c, BKS)."""
    matches = sorted(run_dir.glob("*_rpdf_comparison.csv"))
    if not matches:
        raise SystemExit(f"no *_rpdf_comparison.csv under {run_dir}")
    df = pd.read_csv(matches[0], dtype={"insIndex": str})
    df["rpdf_pp"] = df["RPDf_BKS_data"] * 100.0
    return df


def csr_scenarios(df: pd.DataFrame) -> list[tuple[int, str]]:
    """Return [(f, scenarioName), ...] sorted by f."""
    out = []
    for name in df["scenarioName"].unique():
        m = CSR_RE.match(name)
        if m:
            out.append((int(m.group("f")), name))
    return sorted(out)


def paired(df: pd.DataFrame, scenario: str) -> pd.DataFrame:
    """One row per instance: the CSR arm and the baseline side by side."""
    keep = ["insIndex", "T", "R", "n", "c", "rpdf_pp", "bestObj", "elapsedTime"]
    a = df[df["scenarioName"] == scenario][keep]
    b = df[df["scenarioName"] == BASELINE][
        ["insIndex", "rpdf_pp", "bestObj", "elapsedTime"]
    ]
    m = a.merge(b, on="insIndex", suffixes=("_csr", "_c5"), validate="one_to_one")
    m["d_rpdf_pp"] = m["rpdf_pp_csr"] - m["rpdf_pp_c5"]
    m["d_obj"] = m["bestObj_csr"] - m["bestObj_c5"]
    return m


def build_gate_cells(df: pd.DataFrame) -> pd.DataFrame:
    """Mean RPDf per (f, T, R) cell for both arms, with the per-cell verdict."""
    rows = []
    for f, scenario in csr_scenarios(df):
        m = paired(df, scenario)
        for (t, r), cell in m.groupby(["T", "R"]):
            d = cell["rpdf_pp_csr"].mean() - cell["rpdf_pp_c5"].mean()
            rows.append(
                {
                    "f": f,
                    "scenario": scenario,
                    "T": t,
                    "R": r,
                    "instances": len(cell),
                    "meanRPDf_csr_pp": cell["rpdf_pp_csr"].mean(),
                    "meanRPDf_c5_pp": cell["rpdf_pp_c5"].mean(),
                    "d_meanRPDf_pp": d,
                    # The gate reads the sign; the magnitude flag is for the reader.
                    "cell_pass": bool(d <= 0),
                    "indistinct": bool(abs(d) < CELL_INDISTINCT_PP),
                    "wins": int((cell["d_rpdf_pp"] < 0).sum()),
                    "ties": int((cell["d_rpdf_pp"] == 0).sum()),
                    "losses": int((cell["d_rpdf_pp"] > 0).sum()),
                }
            )
    return pd.DataFrame(rows).sort_values(["f", "T", "R"]).reset_index(drop=True)


def build_f_curve(df: pd.DataFrame) -> pd.DataFrame:
    """Pooled and per-T mean RPDf plus realised budget, one row per scenario."""
    rows = []
    for name in [BASELINE] + [s for _, s in csr_scenarios(df)]:
        sub = df[df["scenarioName"] == name]
        m = CSR_RE.match(name)
        row = {
            "scenario": name,
            "f_pct": int(m.group("f")) if m else pd.NA,
            "instances": len(sub),
            "meanRPDf_pp": sub["rpdf_pp"].mean(),
            "medianRPDf_pp": sub["rpdf_pp"].median(),
            "mean_elapsed_s": sub["elapsedTime"].mean(),
            "mean_timelimit_s": sub["timelimit"].mean(),
            # Realised share of the 0.09nc outer budget -- section 3 "budget compliance".
            "mean_elapsed_over_outer_pct": 100.0
            * (sub["elapsedTime"] / sub["timelimit"]).mean(),
        }
        for t, tsub in sub.groupby("T"):
            row[f"meanRPDf_pp_T{t}"] = tsub["rpdf_pp"].mean()
        rows.append(row)
    return pd.DataFrame(rows)


def build_win_tie_loss(df: pd.DataFrame) -> pd.DataFrame:
    """Per-instance paired win/tie/loss vs the baseline, pooled and per T."""
    rows = []
    for f, scenario in csr_scenarios(df):
        m = paired(df, scenario)
        groups = [("pooled", m)] + [(f"T={t}", g) for t, g in m.groupby("T")]
        for label, g in groups:
            rows.append(
                {
                    "f": f,
                    "slice": label,
                    "instances": len(g),
                    # Ties are counted on the objective, not on RPDf: equal obj is
                    # the only tie that survives the BKS normalisation exactly.
                    "wins": int((g["d_obj"] < 0).sum()),
                    "ties": int((g["d_obj"] == 0).sum()),
                    "losses": int((g["d_obj"] > 0).sum()),
                    "mean_d_rpdf_pp": g["d_rpdf_pp"].mean(),
                    "mean_d_obj": g["d_obj"].mean(),
                }
            )
    return pd.DataFrame(rows)


def build_inner_steps(df: pd.DataFrame, run_dir: Path) -> pd.DataFrame:
    """Per inner step: mean seconds spent and mean objective drop bought.

    Reads the per-instance ``_obj_log.json`` trajectories, whose CSR inner
    points carry ``...inner-NN-<idx>-<step>`` notes since 2c7ef28 (W1). The
    first inner point is ``calc_mcf_lb_and_derive_full_sch``, which *sets* the
    incumbent rather than improving one, so its drop is reported as 0.

    Time is the gap to the previous inner point on the controller clock; the
    unlabelled closing point (the CSR step's own endpoint) is skipped so the
    shares sum over inner steps only.
    """
    inner_re = re.compile(r"inner-\d+-\d+-(?P<step>[a-z_]+)")
    rows = []
    for f, scenario in csr_scenarios(df):
        seconds: Counter[str] = Counter()
        drop: Counter[str] = Counter()
        logs = sorted((run_dir / scenario).glob("*/*_obj_log.json"))
        for path in logs:
            series = json.loads(path.read_text())["obj_value"]
            points = sorted(
                (float(t), v, series["notes"][t]) for t, v in series["data"].items()
            )
            prev_t, prev_v = 0.0, None
            for t, v, note in points:
                m = inner_re.search(note)
                if not m:
                    continue
                step = m.group("step")
                seconds[step] += t - prev_t
                if prev_v is not None:
                    drop[step] += prev_v - v
                prev_t, prev_v = t, v
        total = sum(seconds.values()) or 1.0
        for step, sec in seconds.items():
            rows.append(
                {
                    "f": f,
                    "inner_step": step,
                    "instances": len(logs),
                    "mean_sec": sec / max(len(logs), 1),
                    "share_pct": 100.0 * sec / total,
                    "mean_obj_drop": drop[step] / max(len(logs), 1),
                }
            )
    return pd.DataFrame(rows)


def render_verdict(cells: pd.DataFrame, curve: pd.DataFrame) -> tuple[str, bool]:
    """Format the gate verdict block; return (text, passed)."""
    lines: list[str] = []
    per_f = cells.groupby("f")["cell_pass"].all()
    eligible = [int(f) for f, ok in per_f.items() if ok and f <= BUDGET_CEILING_PCT]
    passed = bool(eligible)

    lines.append("=== W2 P1 gate: tau=1 CSR initializer vs c5_init_only ===")
    lines.append(f"budget ceiling: f <= {BUDGET_CEILING_PCT} %   cells: 9 (T x R)")
    lines.append("")
    lines.append("  f   cells_won  min_cell_d  max_cell_d  pooled_dRPDf_pp  PASS")
    base = float(curve.loc[curve["scenario"] == BASELINE, "meanRPDf_pp"].iloc[0])
    for f, g in cells.groupby("f"):
        pooled = float(curve.loc[curve["f_pct"] == f, "meanRPDf_pp"].iloc[0]) - base
        lines.append(
            f"{f:3d}   {int(g['cell_pass'].sum())}/9        "
            f"{g['d_meanRPDf_pp'].min():+8.2f}    {g['d_meanRPDf_pp'].max():+8.2f}    "
            f"{pooled:+11.2f}      {'YES' if g['cell_pass'].all() else 'no'}"
        )
    lines.append("")
    if passed:
        lines.append(f"GATE: PASS -- minimum passing f = {min(eligible)} %")
    else:
        lines.append("GATE: FAIL -- no f <= 40 % wins all nine cells")
        worst = cells[~cells["cell_pass"]].sort_values("d_meanRPDf_pp", ascending=False)
        lines.append("losing cells (worst first):")
        for _, r in worst.head(12).iterrows():
            lines.append(
                f"  f={int(r['f']):2d}  T={r['T']}  R={r['R']}  "
                f"d={r['d_meanRPDf_pp']:+.2f} pp  "
                f"w/t/l={int(r['wins'])}/{int(r['ties'])}/{int(r['losses'])}"
                + ("  (indistinct)" if r["indistinct"] else "")
            )
    return "\n".join(lines), passed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "run_dir", type=Path, help="output/<name>/<timestamp> run directory"
    )
    ap.add_argument("--outdir", type=Path, default=None)
    args = ap.parse_args()

    run_dir = args.run_dir if args.run_dir.is_absolute() else REPO_ROOT / args.run_dir
    outdir = args.outdir or REPO_ROOT / "analysis" / f"{run_dir.name}_csr_init_tl_curve"
    outdir.mkdir(parents=True, exist_ok=True)

    df = load_run(run_dir)
    cells = build_gate_cells(df)
    curve = build_f_curve(df)
    wtl = build_win_tie_loss(df)
    inner = build_inner_steps(df, run_dir)

    cells.to_csv(outdir / "gate_cells.csv", index=False)
    curve.to_csv(outdir / "f_curve.csv", index=False)
    wtl.to_csv(outdir / "win_tie_loss.csv", index=False)
    inner.to_csv(outdir / "inner_steps.csv", index=False)

    text, passed = render_verdict(cells, curve)
    (outdir / "gate_verdict.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nwrote {outdir}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
