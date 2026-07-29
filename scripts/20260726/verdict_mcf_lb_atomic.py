"""Judge the mcf_lb atomic-gate-removal re-run against the run it replaces.

Run this **once, after the re-run finishes**. It does not poll or wait; if the
run is still in flight it says so and exits non-zero.

The question is not "did the numbers improve" but "is the bug gone, did the
change stay inside mcf_lb, and does the original conclusion still hold". So the
gates run in a fixed order and a failure early on suppresses the later reading
(``plans/experiment/20260726/mcf_lb_atomic_gate_removal.md`` §4):

    G1  every scenario registers an incumbent -- ``m1_k1_f01`` reaches 160/160
        and ``bestObj`` is never empty anywhere in the run.
    G2  arms ``a``/``b`` are bit-identical to the before-run. They are the
        negative controls: ``a`` never calls mcf_lb and ``b``'s budget was
        never binding, so any difference means the change leaked out of its
        intended scope. **G2 failing invalidates the rest** -- §4.1 says to
        stop and re-read the code rather than interpret the re-run, and this
        script enforces that by refusing to print the conclusion section.
    G3  arm ``c`` moved no further than the CP noise floor.

Only once those hold does it re-read the two axes the original analysis
concluded on (``plans/analysis/20260726/coarsening_short_budget_crossover.md``):
whether any (arm, f, k, mode) cell now shows an objective crossover, and whether
the f=1 % feasibility asymmetry disappeared.

Usage:
    uv run python scripts/20260726/verdict_mcf_lb_atomic.py
    uv run python scripts/20260726/verdict_mcf_lb_atomic.py --after <run_dir>
    uv run python scripts/20260726/verdict_mcf_lb_atomic.py --skip-artifacts

Exit code is 0 only when every gate passes.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd

# scripts/20260726/<this file> -- two levels of nesting below the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Both runs share an output base dir: the re-run reuses the config unchanged, so
# only the timestamp tells them apart. Before = the gated code (run setting
# a116e4c), after = round 1 made atomic.
BEFORE_RUN = "output/20260725_crossover_ladder/20260726T002619_971440"
AFTER_RUN = "output/20260725_crossover_ladder/20260726T173841_347539"

EXPECTED_SCENARIOS = 210
EXPECTED_INSTANCES = 160
EXPECTED_ROWS = EXPECTED_SCENARIOS * EXPECTED_INSTANCES

# Arms that must not move at all, and the arm judged against the noise floor.
CONTROL_ARMS = ("a", "b")
NOISE_ARM = "c"

# Wall-clock CP-SAT on 8 workers is nondeterministic, so arm c cannot be held to
# equality. This bound was established over the 1440-instance PRA2017 grid; the
# 160-instance slice here is noisier per-cell, so treat a result just under the
# line as "not distinguishable from noise", not as "proven unchanged".
NOISE_FLOOR_MEAN_OBJ = 350.0


def _load_module(name: str, relpath: str):
    """scripts/ is not an importable package; load a sibling module by path."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Reuse the ladder's scenario parser and pairing so this verdict cannot drift
# from the tables the analysis document quotes.
_ladder = _load_module(
    "analyze_crossover_ladder", "scripts/20260726/analyze_crossover_ladder.py"
)


# --------------------------------------------------------------------------- #
# Completion
# --------------------------------------------------------------------------- #
def resolve_comparison_csv(run_dir: Path, label: str) -> Path:
    """The run's ``*_rpdf_comparison.csv``, which only exists once it finished.

    post_run_pivot writes it during the final report pass, so its presence is
    the completion signal *and* the input every downstream table needs.
    """
    if not run_dir.is_dir():
        raise SystemExit(f"{label} run dir does not exist: {run_dir}")

    matches = sorted(run_dir.glob("*_rpdf_comparison.csv"))
    if matches:
        return matches[0]

    # <run>/<scenario>/<instance>/<instance>_instance_result.yaml
    done = sum(1 for _ in run_dir.glob("*/*/*_instance_result.yaml"))
    pct = 100.0 * done / EXPECTED_ROWS
    raise SystemExit(
        f"{label} run is not finished: no *_rpdf_comparison.csv under {run_dir}\n"
        f"  progress: {done} / {EXPECTED_ROWS} instance results ({pct:.1f}%)\n"
        f"  re-run this script once the run writes its report."
    )


def load(run_dir: Path, label: str) -> pd.DataFrame:
    resolve_comparison_csv(run_dir, label)
    df = _ladder.load_run(run_dir)
    scn, ins = df["scenarioName"].nunique(), df["insIndex"].nunique()
    if (scn, ins) != (EXPECTED_SCENARIOS, EXPECTED_INSTANCES):
        print(
            f"  ! {label}: {scn} scenarios x {ins} instances "
            f"(expected {EXPECTED_SCENARIOS} x {EXPECTED_INSTANCES})"
        )
    return df


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
def _solved(df: pd.DataFrame, scenario: str) -> tuple[int, int]:
    rows = df[df["scenarioName"] == scenario]
    return int(rows["bestObj"].notna().sum()), len(rows)


def gate_g1(before: pd.DataFrame, after: pd.DataFrame) -> bool:
    """Every (scenario, instance) registered an incumbent."""
    print("=== G1: no missing incumbents ===")
    missing = after[after["bestObj"].isna()]

    # The headline case: the scenario §4.4 found empty on 20 of 160 instances.
    b_ok, b_n = _solved(before, "m1_k1_f01")
    a_ok, a_n = _solved(after, "m1_k1_f01")
    print(f"  m1_k1_f01 solved: before {b_ok}/{b_n} -> after {a_ok}/{a_n}")

    if missing.empty:
        print(f"  obj_value null across the whole run: 0 / {len(after)}  PASS")
        return True

    print(f"  obj_value null across the whole run: {len(missing)} / {len(after)}  FAIL")
    for scenario, grp in missing.groupby("scenarioName"):
        sizes = sorted({f"(n={r.n},c={r.c})" for r in grp.itertuples()})
        print(f"    {scenario:<24s} {len(grp):3d} missing  {' '.join(sizes)}")
    print("  -> suspect the round-1 entry gate (plan §5) before anything else.")
    return False


def gate_g2(before: pd.DataFrame, after: pd.DataFrame) -> bool:
    """Arms a and b must be bit-identical: they never exercised the gates."""
    print("\n=== G2: arms a/b bit-identical to the before-run ===")
    ok = True
    for arm in CONTROL_ARMS:
        merged = before[before["arm"] == arm].merge(
            after[after["arm"] == arm],
            on=["insIndex", "scenarioName"],
            suffixes=("_before", "_after"),
            how="outer",
            indicator=True,
        )
        unmatched = merged[merged["_merge"] != "both"]
        b, a = merged["bestObj_before"], merged["bestObj_after"]
        # NaN on both sides means "infeasible in both", which counts as equal.
        differs = merged[~((b == a) | (b.isna() & a.isna()))]

        if unmatched.empty and differs.empty:
            print(f"  arm {arm}: {len(merged)} rows, all identical  PASS")
            continue

        ok = False
        print(f"  arm {arm}: {len(differs)} differing / {len(merged)} rows  FAIL")
        if not unmatched.empty:
            print(f"    {len(unmatched)} rows present in only one run")
        for r in differs.head(10).itertuples():
            print(
                f"    {r.scenarioName:<24s} ins {r.insIndex}  "
                f"{r.bestObj_before} -> {r.bestObj_after}"
            )
        if len(differs) > 10:
            print(f"    ... and {len(differs) - 10} more")
    if not ok:
        print("  -> the change leaked outside mcf_lb. Re-read the diff; do NOT")
        print("     interpret the re-run's numbers (plan §4.1).")
    return ok


def gate_g3(before: pd.DataFrame, after: pd.DataFrame) -> bool:
    """Arm c carries CP nondeterminism, so it is judged against the noise floor."""
    print("\n=== G3: arm c within the CP noise floor ===")
    merged = before[before["arm"] == NOISE_ARM].merge(
        after[after["arm"] == NOISE_ARM],
        on=["insIndex", "scenarioName"],
        suffixes=("_before", "_after"),
    )
    d_obj = (merged["bestObj_after"] - merged["bestObj_before"]).mean()
    d_rpdf = (merged["rpdf_pct_after"] - merged["rpdf_pct_before"]).mean()
    passed = abs(d_obj) <= NOISE_FLOOR_MEAN_OBJ

    print(f"  {len(merged)} paired rows")
    print(
        f"  mean bestObj delta:  {d_obj:+10.1f}  (floor +/-{NOISE_FLOOR_MEAN_OBJ:.0f})"
    )
    print(f"  mean RPDf delta:     {d_rpdf:+10.3f} pp")
    print("  " + ("PASS" if passed else "FAIL -- larger than CP noise explains"))
    return passed


# --------------------------------------------------------------------------- #
# Conclusion re-read (only reached when the gates hold)
# --------------------------------------------------------------------------- #
def _violations(drpdf: pd.DataFrame) -> pd.DataFrame:
    """Cells contradicting "no crossover" = dRPDf > 0 AND win < loss."""
    return drpdf[~((drpdf["mean_drpdf_pp"] > 0) & (drpdf["win"] < drpdf["loss"]))]


def conclusion(before: pd.DataFrame, after: pd.DataFrame) -> None:
    b_drpdf, a_drpdf = _ladder.paired_drpdf(before), _ladder.paired_drpdf(after)

    print("\n=== Axis 1: is there now an objective crossover? ===")
    b_bad, a_bad = _violations(b_drpdf), _violations(a_drpdf)
    print(f"  before: {len(b_bad)} / {len(b_drpdf)} cells contradict 'no crossover'")
    print(f"  after:  {len(a_bad)} / {len(a_drpdf)} cells contradict 'no crossover'")
    if a_bad.empty:
        print("  -> conclusion HOLDS: K=1 still best in every cell.")
    else:
        print("  -> conclusion CHANGED. Cells that flipped:")
        for r in a_bad.itertuples():
            f = "-" if pd.isna(r.f) else f"f={int(r.f)}%"
            print(
                f"    {r.arm:3s} {f:6s} k={r.k:<3d} {str(r.mode):<10s} "
                f"dRPDf={r.mean_drpdf_pp:+7.2f}pp  W/L={r.win}/{r.loss}"
            )

    print("\n=== Axis 2: did the f=1% feasibility asymmetry disappear? ===")
    print("  'coarse_only' = instances the coarse arm solved and K=1 did not.")
    for label, drpdf in (("before", b_drpdf), ("after", a_drpdf)):
        cells = drpdf[(drpdf["arm"] == "m1") & (drpdf["f"] == 1)]
        total = int(cells["coarse_only_feasible"].sum())
        worst = int(cells["coarse_only_feasible"].max()) if len(cells) else 0
        print(
            f"  {label:6s} m1 f=1%: coarse_only total={total:3d}  max per cell={worst}"
        )
    a_total = int(
        a_drpdf[(a_drpdf["arm"] == "m1") & (a_drpdf["f"] == 1)][
            "coarse_only_feasible"
        ].sum()
    )
    print(
        "  -> asymmetry GONE; §4.4 was a defect record, as read."
        if a_total == 0
        else "  -> asymmetry PERSISTS; the gate was not the only cause (plan §5)."
    )

    print("\n=== m1 dRPDf ladder: before -> after (k=2, pp) ===")
    for mode in _ladder.MODES:
        cells = []
        for f in (1, 2, 3, 4):
            sel = lambda d: d[  # noqa: E731
                (d["arm"] == "m1") & (d["k"] == 2) & (d["mode"] == mode) & (d["f"] == f)
            ]["mean_drpdf_pp"]
            bv, av = sel(b_drpdf), sel(a_drpdf)
            cells.append(
                f"f={f}%: {bv.iloc[0]:+6.2f}->{av.iloc[0]:+6.2f}"
                if len(bv) and len(av)
                else f"f={f}%: {'n/a':>14s}"
            )
        print(f"  {mode:<11s}" + "  ".join(cells))


def run_artifact_scripts(after_dir: Path) -> None:
    """Emit the standard CSVs the analysis document cites."""
    for relpath, extra in (
        ("scripts/20260726/analyze_crossover_ladder.py", []),
        ("scripts/20260725/analyze_csr_winner_source.py", ["--n", "200", "--c", "10"]),
    ):
        cmd = [sys.executable, str(REPO_ROOT / relpath), str(after_dir), *extra]
        print(f"\n$ {' '.join(cmd[1:])}")
        subprocess.run(cmd, cwd=REPO_ROOT, check=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--after", type=Path, default=REPO_ROOT / AFTER_RUN)
    parser.add_argument("--before", type=Path, default=REPO_ROOT / BEFORE_RUN)
    parser.add_argument(
        "--skip-artifacts",
        action="store_true",
        help="Only print the verdict; do not run the ladder/depth scripts.",
    )
    args = parser.parse_args(argv)

    after_dir, before_dir = args.after.resolve(), args.before.resolve()
    print(f"before: {before_dir}\nafter:  {after_dir}\n")

    before = load(before_dir, "before")
    after = load(after_dir, "after")

    g1 = gate_g1(before, after)
    g2 = gate_g2(before, after)
    g3 = gate_g3(before, after)

    print("\n" + "=" * 70)
    print(f"G1 incumbents   {'PASS' if g1 else 'FAIL'}")
    print(f"G2 a/b control  {'PASS' if g2 else 'FAIL'}")
    print(f"G3 c noise      {'PASS' if g3 else 'FAIL'}")
    print("=" * 70)

    if not g2:
        print("\nG2 failed -- conclusion re-read suppressed on purpose (plan §4.1).")
        return 1

    conclusion(before, after)
    if not args.skip_artifacts:
        run_artifact_scripts(after_dir)

    return 0 if (g1 and g2 and g3) else 1


if __name__ == "__main__":
    sys.exit(main())
