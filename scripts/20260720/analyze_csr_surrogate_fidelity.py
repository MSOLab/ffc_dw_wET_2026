"""Phase 0 of the CSR coarsening-rule plan: does the coarse objective *rank*
schedules the way the true objective does?

Implements the offline fidelity gate of
``plans/experiment/20260720/csr_coarsening_rounding_modes.md`` §5. No solver is
run: the merged budget sweep already holds 48 fine-scale schedules per instance
(48 scenarios x 1440 instances), and this script re-scores those same schedules
on coarse instances built by each candidate rounding rule.

The measurement
---------------
A coarse solution decides **(machine assignment, per-machine job order)** and
nothing else — its times are an artifact of the coarse grid. So each fine
schedule is *projected* by transplanting exactly that pair onto a target
instance and re-deriving times by forward sweep. The projection is
``reconstruct_raw_coarse_schedule``, reused verbatim: it reads assignment and
order from its first argument and processing times from its second, so passing
(fine schedule, coarse instance) performs the transplant with no new code.

Two scores per schedule, both after ``insert_idle_time``:

- **truth**  — projected onto the *original* instance, ``time_factor=1``
- **surrogate** — projected onto the *coarse* instance, ``time_factor=kappa``,
  scored exactly as production does (``_build_dispatch_seed_schedule``)

Kendall tau-b between the two rankings over the 48 schedules is the answer.
Both sides share the same projection, so the only difference is the processing
times — which isolates the rounding rule, the quantity under test.

``tau_raw`` additionally ranks the surrogate against the schedules' stored
``objValue`` (the un-projected solver output). It is a cross-check, not the
primary number: it also carries the projection loss, which is not the rule's
fault. ``proj_loss_pct`` reports that loss directly.

Reading tau
-----------
``P(a random pair ordered correctly) = (1 + tau) / 2``. tau = 0.6 -> 80 %,
tau = 0.3 -> 65 %, tau = 0 -> a coin flip. The §5 gate turns on this: a
surrogate that is right 65 % of the time on pairwise comparisons cannot steer a
search that makes thousands of them.

Two diagnostics the headline tau does not carry
-----------------------------------------------
- **tie_frac** — coarsening collapses distinct schedules onto identical coarse
  scores. That is the resolution-loss mechanism §2 blames, not incidental
  noise, so the tie rate separates "ranks them wrongly" from "cannot tell them
  apart at all". tau-b (scipy's default) is used because it corrects for ties;
  tau-a would drift with kappa.
- **tau_strat** — tau within quality terciles, averaged. The 48 schedules span
  a wide quality range (kappa=1,f=30 down to kappa=8,f=5), and ranking
  far-apart candidates is easy; the inner solver's real job is discriminating
  near-ties. A high headline tau with a low stratified tau means the gate would
  pass on a measurement that does not reflect the solver's actual task.

Self-checks (both fail loudly)
------------------------------
- the script's local ``ceil`` rule must reproduce
  ``FFcDDWParameters.coarsen_processing_times`` exactly, pinning the local
  reimplementation against drift;
- at ``kappa=1`` every rule is the identity (``ceil(p/1) == round(p/1) ==
  floor(p/1) == p``), so surrogate must equal truth exactly and tau must be 1.0.
  This validates the whole transplant-and-score pipeline end to end. Pass
  ``--kappas 1 ...`` to run it.

Usage::

    uv run python scripts/20260720/analyze_csr_surrogate_fidelity.py
    uv run python scripts/20260720/analyze_csr_surrogate_fidelity.py \
        --limit 160 --kappas 1 2 4 8 --workers 32
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Callable, Sequence
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.stats import kendalltau

from ffc_ddw_sum_et.io.schedule_json import load_schedule_json
from ffc_ddw_sum_et.orchestration.benchmark_loader import BenchmarkLoader
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness
from ffc_ddw_sum_et.solution.schedule_build import reconstruct_raw_coarse_schedule

DEFAULT_SWEEP_RUN = Path("output/20260720_merge_csr_k_f_sweep/20260720T171158_514111")
DEFAULT_BENCHMARK_DIR = Path("benchmarks/PRA2017/large")
DEFAULT_INS_INDEX_SOURCE = Path("benchmarks/PRA2017/pra2017_hybrid_match.csv")
DEFAULT_OUTDIR = Path("analysis/20260720_csr_surrogate_fidelity")

RULES: dict[str, Callable[[np.ndarray, int], np.ndarray]] = {
    # ceil is production today; round is the one real candidate; floor is the
    # sign control (plan §3) — if round helps and floor hurts symmetrically,
    # the bias explanation holds.
    "ceil": lambda df, k: np.ceil(df / k),
    "round": lambda df, k: np.maximum(np.round(df / k), 1),
    "floor": lambda df, k: np.maximum(np.floor(df / k), 1),
}

_N_STRATA = 3


def coarsen(instance: FFcDDWParameters, factor: int, rule: str) -> FFcDDWParameters:
    """Coarsen processing times by ``factor`` under ``rule``.

    Mirrors ``FFcDDWParameters.coarsen_processing_times`` (which hardcodes
    ceil); due windows stay at original scale and must be read with
    ``time_factor=factor``. ``_assert_ceil_matches_production`` pins this
    against drift.
    """
    new_df = RULES[rule](instance.p_manager.df.copy(), factor).astype(int)
    return FFcDDWParameters(
        f"{instance.name}_coarsen{rule}{factor}",
        list(instance.job_id_list),
        list(instance.stage_id_list),
        {s: list(instance.stage_2_machines_map[s]) for s in instance.stage_id_list},
        JobStageProcessingTimeManager(instance.p_manager.name, new_df),
        dict(instance.job_2_due_window_map),
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
        instance.generation_params,
    )


def _assert_ceil_matches_production(instance: FFcDDWParameters) -> None:
    """The local ceil rule must equal the production classmethod, or the whole
    comparison is measuring the reimplementation rather than the rule."""
    for factor in (2, 4, 8):
        mine = coarsen(instance, factor, "ceil").p_manager.df
        theirs = FFcDDWParameters.coarsen_processing_times(
            instance, factor
        ).p_manager.df
        if not mine.equals(theirs):
            raise AssertionError(
                f"local ceil != coarsen_processing_times at factor={factor} "
                f"on {instance.name}; the local rule has drifted."
            )


def project_and_score(
    fine_schedule,
    target: FFcDDWParameters,
    factor: int,
) -> float:
    """Transplant ``fine_schedule``'s (assignment, order) onto ``target`` and
    return the weighted E+T of the result.

    ``factor`` is the time scale of ``target``: 1 when it is the original
    instance, kappa when it is a coarse one. Mirrors production's coarse
    scoring path (``_build_dispatch_seed_schedule``): idle insertion and
    scoring both read ``target``'s maps with ``time_factor=factor``.
    """
    projected = reconstruct_raw_coarse_schedule(fine_schedule, target, factor)
    projected.insert_idle_time(
        target.job_2_due_window_map,
        target.job_2_ewt_map,
        target.job_2_twt_map,
        time_factor=factor,
    )
    sum_e, sum_t = compute_weighted_earliness_tardiness(
        projected, target, time_factor=factor
    )
    return float(sum_e + sum_t)


def _stratified_tau(surrogate: np.ndarray, truth: np.ndarray) -> float:
    """Mean tau within quality terciles of ``truth``.

    Ranking widely-separated candidates is easy; the inner solver discriminates
    near-ties. NaN when no stratum yields a defined tau.
    """
    order = np.argsort(truth)
    taus = []
    for chunk in np.array_split(order, _N_STRATA):
        if len(chunk) < 3:
            continue
        tau = kendalltau(surrogate[chunk], truth[chunk]).statistic
        if not np.isnan(tau):
            taus.append(tau)
    return float(np.mean(taus)) if taus else float("nan")


def _tie_frac(values: np.ndarray) -> float:
    """Fraction of pairs that are exact ties under the surrogate."""
    n = len(values)
    _, counts = np.unique(values, return_counts=True)
    tied_pairs = float(np.sum(counts * (counts - 1) / 2))
    return tied_pairs / (n * (n - 1) / 2)


def _rows_for_instance(job: tuple) -> list[dict]:
    """All (rule, kappa) rows for one instance. Runs in a worker process."""
    instance, schedule_paths, kappas, rules = job

    schedules, stored_objs = [], []
    for path in schedule_paths:
        sched, obj_value, _bound = load_schedule_json(path)
        schedules.append(sched)
        stored_objs.append(float("nan") if obj_value is None else obj_value)
    stored = np.array(stored_objs, dtype=float)

    truth = np.array(
        [project_and_score(s, instance, 1) for s in schedules],
        dtype=float,
    )
    # How much the projection itself costs, before any coarsening. Large values
    # mean the fine schedules carry quality in their *times* that no coarse
    # solution could have expressed.
    proj_loss = float(np.mean((truth - stored) / stored) * 100)

    rows = []
    for rule in rules:
        for kappa in kappas:
            coarse = coarsen(instance, kappa, rule)
            surrogate = np.array(
                [project_and_score(s, coarse, kappa) for s in schedules],
                dtype=float,
            )
            if kappa == 1 and not np.allclose(surrogate, truth):
                raise AssertionError(
                    f"kappa=1 must be the identity for rule={rule} on "
                    f"{instance.name}, but surrogate != truth; the "
                    f"transplant-and-score pipeline is wrong."
                )
            rows.append(
                {
                    "instanceName": instance.name,
                    "rule": rule,
                    "kappa": kappa,
                    "n_schedules": len(schedules),
                    "tau": kendalltau(surrogate, truth).statistic,
                    "tau_raw": kendalltau(surrogate, stored).statistic,
                    "tau_strat": _stratified_tau(surrogate, truth),
                    "tie_frac": _tie_frac(surrogate),
                    "proj_loss_pct": proj_loss,
                    "truth_spread_pct": float(
                        (truth.max() - truth.min()) / truth.mean() * 100
                    ),
                }
            )
    return rows


def _collect_schedule_paths(
    sweep_run: Path,
) -> tuple[list[str], dict[str, list[Path]]]:
    """Instance names present in *every* scenario, with their solution paths.

    An instance missing from any scenario would be ranked over a short pool and
    is dropped rather than silently compared on different footing.
    """
    scenario_dirs = sorted(d for d in sweep_run.iterdir() if d.is_dir())
    if not scenario_dirs:
        raise SystemExit(f"No scenario directories under {sweep_run}")

    per_scenario: list[dict[str, Path]] = []
    for scenario in scenario_dirs:
        found = {}
        for inst_dir in scenario.iterdir():
            if not inst_dir.is_dir():
                continue
            path = inst_dir / f"{inst_dir.name}_solution.json"
            if path.is_file():
                found[inst_dir.name] = path
        per_scenario.append(found)

    common = set(per_scenario[0])
    for found in per_scenario[1:]:
        common &= set(found)
    dropped = len(per_scenario[0]) - len(common)
    if dropped:
        print(f"  dropped {dropped} instance(s) not present in all scenarios")

    names = sorted(common)
    paths = {n: [found[n] for found in per_scenario] for n in names}
    return names, paths


def _print_table(rows: list[dict], rules: Sequence[str], kappas: Sequence[int]) -> None:
    print("\n=== Kendall tau-b: coarse surrogate vs true ranking ===")
    print("(tau: primary, isolates the rule | strat: within quality terciles)")
    print(
        f"{'rule':<8}{'kappa':>6}{'tau':>9}{'P(pair)':>9}{'strat':>9}"
        f"{'tie%':>8}{'tau_raw':>9}"
    )
    for rule in rules:
        for kappa in kappas:
            sel = [r for r in rows if r["rule"] == rule and r["kappa"] == kappa]
            tau = float(np.nanmean([r["tau"] for r in sel]))
            strat = float(np.nanmean([r["tau_strat"] for r in sel]))
            tie = float(np.nanmean([r["tie_frac"] for r in sel])) * 100
            raw = float(np.nanmean([r["tau_raw"] for r in sel]))
            print(
                f"{rule:<8}{kappa:>6}{tau:>9.3f}{(1 + tau) / 2:>9.1%}"
                f"{strat:>9.3f}{tie:>8.1f}{raw:>9.3f}"
            )

    loss = float(np.nanmean([r["proj_loss_pct"] for r in rows]))
    spread = float(np.nanmean([r["truth_spread_pct"] for r in rows]))
    print(f"\nprojection loss vs stored objValue: {loss:+.2f} % (mean)")
    print(f"candidate pool spread (max-min)/mean: {spread:.1f} % (mean)")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sweep-run", type=Path, default=DEFAULT_SWEEP_RUN)
    p.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK_DIR)
    p.add_argument("--ins-index-source", type=Path, default=DEFAULT_INS_INDEX_SOURCE)
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Instances to sample, evenly spaced over the sorted names "
        "(default 20, the §5 sizing run; 0 = all).",
    )
    p.add_argument("--kappas", type=int, nargs="+", default=[2, 4, 8])
    p.add_argument("--rules", nargs="+", default=list(RULES), choices=list(RULES))
    p.add_argument("--workers", type=int, default=1)
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    names, paths = _collect_schedule_paths(args.sweep_run)
    print(f"Found {len(names)} instances x {len(paths[names[0]])} schedules")
    if args.limit and args.limit < len(names):
        # Evenly spaced over the sorted names, so the sample spans the
        # generation grid instead of piling onto one corner of it.
        idx = np.linspace(0, len(names) - 1, args.limit).round().astype(int)
        names = [names[i] for i in sorted(set(idx))]
    print(f"Analyzing {len(names)} instances")

    loader = BenchmarkLoader(
        directory=args.benchmark_dir, ins_index_source=args.ins_index_source
    )
    instances = loader.load_all(instance_names=names)
    by_name = {ins.name: ins for ins in instances}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise SystemExit(f"Benchmark files not found for: {missing[:5]}")

    _assert_ceil_matches_production(by_name[names[0]])
    print("self-check: local ceil == coarsen_processing_times OK")

    jobs = [(by_name[n], paths[n], args.kappas, args.rules) for n in names]
    rows: list[dict] = []
    if args.workers <= 1:
        for k, job in enumerate(jobs, 1):
            rows.extend(_rows_for_instance(job))
            print(f"  {k}/{len(jobs)} instances done")
    else:
        with Pool(processes=args.workers) as pool:
            for k, inst_rows in enumerate(
                pool.imap_unordered(_rows_for_instance, jobs), 1
            ):
                rows.extend(inst_rows)
                print(f"  {k}/{len(jobs)} instances done")

    rows.sort(key=lambda r: (r["instanceName"], r["rule"], r["kappa"]))
    _print_table(rows, args.rules, args.kappas)

    args.outdir.mkdir(parents=True, exist_ok=True)
    out = args.outdir / "surrogate_fidelity.csv"
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
