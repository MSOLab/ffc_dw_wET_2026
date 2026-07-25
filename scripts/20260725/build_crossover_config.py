"""Generate the sub-5 % budget coarsening-crossover experiment config.

Plan: ``plans/experiment/20260725/coarsening_short_budget_crossover.md``.

Emits ``metadata/20260725/coarsening_crossover.yaml`` — 210 scenarios over the
160-instance ``(T, R) = (0.6, 0.2)`` cell. Four arms, all sharing the same
``reconstruct_mode: active_but_last_semi`` and the same K grid
(``K=1`` plus ``{2,4,8,16,32} x {cumulative, ceil, floor, round}`` = 21 settings;
``factor=1`` makes the four rounding modes identical, so K=1 is a single
mode-free scenario):

    m1  full inner solve_flow, budget f in {1,2,3,4}%      84 scenarios
    a   dispatch-only (seed_dispatch=v4, solve=False)      21
    b   calc_mcf_lb_and_derive_full_sch only               21
    c   b + run_flip_makespan_cp_from_incumbent, f-swept   84

``m1`` extends the 2026-07-24 f in {5,10,15}% curve downward: every inner time
limit is scaled strictly proportionally to f from the f=5 % block of
``metadata/20260724/lastsemi_fullgrid.yaml`` (csr ``0.0009f``, flip ``0.00009f``,
neh ``0.00027f``, isw multiplier ``0.00005f`` — all reproduce that file exactly
at f=5,10,15).

``a`` / ``b`` / ``c`` are the **seed ladder**: they hold algorithmic depth fixed
and vary only resolution, decomposing the full-flow penalty into a resolution
channel and a depth channel. Their CSR step is deliberately left at the global
``0.09nc`` cap (no per-call limit) so the fixed-cost constructive steps are never
truncated mid-way — their true cost is then read off ``elapsedTime`` post hoc.
This makes them **equal-algorithm, not equal-wall-clock** comparisons; see the
plan's §3 caveats before putting their numbers next to the f-swept arms.

Usage:
    uv run python scripts/20260725/build_crossover_config.py \
        [--out metadata/20260725/coarsening_crossover.yaml]

Every emitted step dict is validated with ``routix``'s ``parse_step`` and against
the real ``FFcDDWSubroutineController`` method names, so a typo fails here rather
than hours into the run.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import yaml
from routix.constants import SubroutineFlowKeys

from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController

REPO_ROOT = Path(__file__).resolve().parents[2]
BKS_TABLE_CSV = REPO_ROOT / "benchmarks/PRA2017/pra2017_bks_table.csv"
DEFAULT_OUT = REPO_ROOT / "metadata/20260725/coarsening_crossover.yaml"

SLICE_T = 0.6
SLICE_R = 0.2

MODES = ["cumulative", "ceil", "floor", "round"]
FACTORS = [2, 4, 8, 16, 32]
F_PERCENTS = [1, 2, 3, 4]

TOP_TIMELIMIT = "0.09nc"
RECONSTRUCT_MODE = "active_but_last_semi"
SOLVER_THREAD_CNT = 8

# Per-1 % coefficients, back-derived from the f=5 % block of
# metadata/20260724/lastsemi_fullgrid.yaml (csr 0.0045nc, flip 0.00045nc,
# neh 0.00135nc, isw 0.00025) so this file reproduces it exactly at f=5.
CSR_TL_PER_PCT = 0.0009
FLIP_TL_PER_PCT = 0.00009
NEH_TL_PER_PCT = 0.00027
ISW_MULT_PER_PCT = 0.00005


# --------------------------------------------------------------------------- #
# Instance slice
# --------------------------------------------------------------------------- #
def load_slice_indices(t: float = SLICE_T, r: float = SLICE_R) -> list[int]:
    """The ``insIndex`` list of one (T, R) cell — 160 of the 1440 instances."""
    with BKS_TABLE_CSV.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    sel = sorted(
        int(row["insIndex"])
        for row in rows
        if float(row["T"]) == t and float(row["R"]) == r
    )
    if len(sel) != 160:
        raise ValueError(f"expected 160 instances for (T,R)=({t},{r}), got {len(sel)}")
    return sel


# --------------------------------------------------------------------------- #
# Step builders (parameters copied verbatim from lastsemi_fullgrid.yaml)
# --------------------------------------------------------------------------- #
def fmt_nc(value: float) -> str:
    """``0.00027`` -> ``"0.00027nc"``, without float-repr noise."""
    return f"{value:.8f}".rstrip("0").rstrip(".") + "nc"


def mcf_lb_step() -> dict:
    return {
        "method": "calc_mcf_lb_and_derive_full_sch",
        "draw_pmtn_sch_heatmap": False,
        "job_placement_priority": "end_time",
        "last_stage_only_placement_criteria": "dist",
        "makespan_delta_ref": "lastStageOnlyMakespan",
        "adjust_p": True,
        "adjust_r": True,
        "r_adjust_coeff": 0.5,
        "proceed_r2_when_nonpositive_cmax": True,
    }


def flip_step(cp_tl: str) -> dict:
    return {
        "method": "run_flip_makespan_cp_from_incumbent",
        "cp_tl": cp_tl,
        "solver_thread_cnt": SOLVER_THREAD_CNT,
        "log_search_progress": False,
        "emit_phase_schedules": False,
    }


def neh_step(total_timelimit: str) -> dict:
    return {
        "method": "neh_cp",
        "job_priority": "due2-weight-pos",
        "solver_thread_cnt": SOLVER_THREAD_CNT,
        "added_batch_size": 15,
        "total_timelimit": total_timelimit,
        "batch_tl_mode": "linear",
        "apply_cumulative_tl": False,
        "pf_method": "PF1",
        "skip_pf_below_obj": "makespan",
        "make_semi_active_after_cp": True,
        "minimize_makespan_lex": False,
    }


def isw_step(multiplier: float) -> dict:
    return {
        "method": "incremental_sw_cp",
        "solver_thread_cnt": SOLVER_THREAD_CNT,
        "batch_size": "m",
        "step_size": 1,
        "unfixed_batch_count_min": 2,
        "unfixed_batch_count_max": 8,
        "increment_unfixed_batch_count_flag": "always",
        "left_profile_fixed_batch_count": 2,
        "right_profile_fixed_batch_count": 2,
        "enable_promotion_profile_fixed": True,
        "pf_method": "PF1",
        "batch_tl_mode": "proportional",
        "non_time_fixed_op_time_limit_multiplier": multiplier,
        "rj_right_justify_scope": "rtf_only",
    }


def base_cp_step() -> dict:
    return {"method": "solve_base_model_cpsat", "solver_thread_cnt": SOLVER_THREAD_CNT}


# --------------------------------------------------------------------------- #
# Scenario builders
# --------------------------------------------------------------------------- #
def csr_step(
    *,
    factor: int,
    coarsen_mode: str,
    timelimit: str,
    solve_flow: list[dict] | None = None,
    seed_dispatch: str | None = None,
    solve: bool | None = None,
) -> dict:
    step = {
        "method": "coarsen_solve_reconstruct",
        "factor": factor,
        "coarsen_mode": coarsen_mode,
        "timelimit": timelimit,
    }
    if seed_dispatch is not None:
        step["seed_dispatch"] = seed_dispatch
    if solve is not None:
        step["solve"] = solve
    if solve_flow is not None:
        step["solve_flow"] = solve_flow
    step["reconstruct_mode"] = RECONSTRUCT_MODE
    step["dump_csr_coarse"] = False
    return step


def scenario(name: str, csr: dict) -> dict:
    return {
        "name": name,
        "timelimit": TOP_TIMELIMIT,
        "output_subdir": name,
        "subroutine_flow": [csr],
    }


def k_settings() -> list[tuple[int, str, str]]:
    """``(factor, coarsen_mode, name_suffix)`` — K=1 once, K>1 per rounding mode.

    ``FFcDDWParameters.coarsen_processing_times`` is the identity at ``factor=1``,
    so a per-mode K=1 scenario would be four copies of one run.
    """
    settings = [(1, "cumulative", "k1")]
    settings += [(k, mode, f"k{k}_{mode}") for k in FACTORS for mode in MODES]
    return settings


def build_scenarios() -> list[dict]:
    scenarios: list[dict] = []

    # --- m1: full inner flow, budget swept below the 2026-07-24 floor ---------
    for f in F_PERCENTS:
        for factor, mode, suffix in k_settings():
            flow = [
                mcf_lb_step(),
                flip_step(fmt_nc(FLIP_TL_PER_PCT * f)),
                neh_step(fmt_nc(NEH_TL_PER_PCT * f)),
                isw_step(round(ISW_MULT_PER_PCT * f, 8)),
                base_cp_step(),
            ]
            scenarios.append(
                scenario(
                    f"m1_{suffix}_f{f:02d}",
                    csr_step(
                        factor=factor,
                        coarsen_mode=mode,
                        timelimit=fmt_nc(CSR_TL_PER_PCT * f),
                        solve_flow=flow,
                    ),
                )
            )

    # --- a: dispatch-only (legacy non-solve_flow path, deterministic) ---------
    for factor, mode, suffix in k_settings():
        scenarios.append(
            scenario(
                f"a_{suffix}",
                csr_step(
                    factor=factor,
                    coarsen_mode=mode,
                    timelimit=TOP_TIMELIMIT,
                    seed_dispatch="v4",
                    solve=False,
                ),
            )
        )

    # --- b: MCF-LB constructive only (fixed cost, no time knob) --------------
    for factor, mode, suffix in k_settings():
        scenarios.append(
            scenario(
                f"b_{suffix}",
                csr_step(
                    factor=factor,
                    coarsen_mode=mode,
                    timelimit=TOP_TIMELIMIT,
                    solve_flow=[mcf_lb_step()],
                ),
            )
        )

    # --- c: b + flip CP; f caps the CP only, not the MCF-LB fixed cost -------
    # The flip CP gets the WHOLE f% budget (CSR_TL_PER_PCT), not m1's 10 % share
    # (FLIP_TL_PER_PCT). With the 10 % share the CP is starved to UNKNOWN and arm
    # c reproduces arm b bit-for-bit -- measured on the 2026-07-26 smoke.
    for f in F_PERCENTS:
        for factor, mode, suffix in k_settings():
            scenarios.append(
                scenario(
                    f"c_{suffix}_f{f:02d}",
                    csr_step(
                        factor=factor,
                        coarsen_mode=mode,
                        timelimit=TOP_TIMELIMIT,
                        solve_flow=[
                            mcf_lb_step(),
                            flip_step(fmt_nc(CSR_TL_PER_PCT * f)),
                        ],
                    ),
                )
            )

    return scenarios


def build_config(scenarios: list[dict]) -> dict:
    return {
        "run_mode": "FULL_RUN",
        "benchmark_dir": "benchmarks/PRA2017/large",
        "ins_index_source": "benchmarks/PRA2017/pra2017_hybrid_match.csv",
        "ins_index": load_slice_indices(),
        "bks_table_csv_path": "benchmarks/PRA2017/pra2017_bks_table.csv",
        "output_dir": "output/20260725_crossover_ladder",
        "instance_worker_cnt": 12,
        "draw_gantt": False,
        "draw_progress_plot": False,
        "painter_thread_cnt": 96,
        "scenarios": scenarios,
    }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate(config: dict) -> None:
    """Fail here, not hours into the run: names unique, every step parseable."""
    names = [s["name"] for s in config["scenarios"]]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"duplicate scenario names: {dupes}")

    def check_flow(flow: list[dict], where: str) -> None:
        for step in flow:
            SubroutineFlowKeys.parse_step(step)
            method = step["method"]
            if not callable(getattr(FFcDDWSubroutineController, method, None)):
                raise ValueError(f"{where}: no controller method {method!r}")
            nested = step.get("solve_flow")
            if nested:
                check_flow(nested, f"{where} > {method}.solve_flow")

    for sc in config["scenarios"]:
        check_flow(sc["subroutine_flow"], sc["name"])


def summarize(config: dict) -> str:
    counts: dict[str, int] = {}
    for sc in config["scenarios"]:
        counts[sc["name"].split("_")[0]] = counts.get(sc["name"].split("_")[0], 0) + 1
    parts = ", ".join(f"{arm}={n}" for arm, n in sorted(counts.items()))
    return (
        f"{len(config['scenarios'])} scenarios ({parts}) "
        f"x {len(config['ins_index'])} instances"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    config = build_config(build_scenarios())
    validate(config)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as fh:
        yaml.safe_dump(config, fh, sort_keys=False, default_flow_style=False)
    print(f"wrote {args.out}: {summarize(config)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
