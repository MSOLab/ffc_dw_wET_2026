"""Generate the CSR init TL curve config for W2 P1 gate.

Plan: ``plans/experiment/20260726/csr_init_tl_f35_f40.md``.

Emits ``metadata/20260726/csr_init_tl_curve.yaml`` — 9 scenarios over the
full 1440-instance grid. 8 CSR τ=1 scenarios with budget f in
{5,10,15,20,25,30,35,40} % + 1 C5 init-only baseline (mcf_lb → flip → neh_cp,
no tail). All share the same outer cap of ``0.09nc``.

Inner TLs scale strictly proportional to f from the 20260714 baseline
(plan §2 table): CSR timelimit ``0.0009*f*nc``, flip cp_tl ``0.00009*f*nc``,
neh total_timelimit ``0.00027*f*nc``, isw multiplier ``0.00005*f``.

Usage:
    uv run python scripts/20260726/build_csr_init_tl_config.py \
        [--out metadata/20260726/csr_init_tl_curve.yaml]

Every emitted step dict is validated with ``routix``'s ``parse_step`` and
against the real ``FFcDDWSubroutineController`` method names.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from routix.constants import SubroutineFlowKeys

from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "metadata/20260726/csr_init_tl_curve.yaml"

# Per-1 % coefficients, back-derived from the f=5 % block of
# metadata/20260714/csr_tl_scaling_sweep.yaml.
CSR_TL_PER_PCT = 0.0009
FLIP_TL_PER_PCT = 0.00009
NEH_TL_PER_PCT = 0.00027
ISW_MULT_PER_PCT = 0.00005

F_PERCENTS = [5, 10, 15, 20, 25, 30, 35, 40]
TOP_TIMELIMIT = "0.09nc"
RECONSTRUCT_MODE = "active_but_last_semi"
SOLVER_THREAD_CNT = 8

# C5 init-only arm budgets (plan §1.1).
C5_FLIP_CP_TL = "0.009nc"
C5_NEH_TOTAL_TL = "0.027nc"


def fmt_nc(value: float) -> str:
    """``0.0009`` -> ``"0.0009nc"``, without float-repr noise."""
    return f"{value:.8f}".rstrip("0").rstrip(".") + "nc"


# --------------------------------------------------------------------------- #
# Shared inner solve_flow step builders
# --------------------------------------------------------------------------- #
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
def csr_scenario(f: int) -> dict:
    """Build a single CSR τ=1 init scenario with budget f %.

    No ``idle_mode`` key anywhere — ``d442ac0`` rejects it. ``seed_dispatch=v4``
    is set but ignored in solve_flow mode (controller logs a warning).
    """
    solve_flow = [
        mcf_lb_step(),
        flip_step(fmt_nc(FLIP_TL_PER_PCT * f)),
        neh_step(fmt_nc(NEH_TL_PER_PCT * f)),
        isw_step(round(ISW_MULT_PER_PCT * f, 8)),
        base_cp_step(),
    ]
    name = f"csr_init_tau1_f{f:02d}"
    return {
        "name": name,
        "timelimit": TOP_TIMELIMIT,
        "output_subdir": name,
        "subroutine_flow": [
            {
                "method": "coarsen_solve_reconstruct",
                "factor": 1,
                "timelimit": fmt_nc(CSR_TL_PER_PCT * f),
                "reconstruct_mode": RECONSTRUCT_MODE,
                "dump_csr_coarse": False,
                "solve_flow": solve_flow,
            }
        ],
    }


def c5_init_scenario() -> dict:
    """C5 init-only baseline: mcf_lb → flip → neh_cp, no tail (plan §2)."""
    name = "c5_init_only"
    return {
        "name": name,
        "timelimit": TOP_TIMELIMIT,
        "output_subdir": name,
        "subroutine_flow": [
            mcf_lb_step(),
            flip_step(C5_FLIP_CP_TL),
            neh_step(C5_NEH_TOTAL_TL),
        ],
    }


def build_scenarios() -> list[dict]:
    scenarios = [csr_scenario(f) for f in F_PERCENTS]
    scenarios.append(c5_init_scenario())
    return scenarios


# --------------------------------------------------------------------------- #
# Config assembly
# --------------------------------------------------------------------------- #
def build_config(scenarios: list[dict]) -> dict:
    return {
        "run_mode": "FULL_RUN",
        "benchmark_dir": "benchmarks/PRA2017/large",
        "ins_index_source": "benchmarks/PRA2017/pra2017_hybrid_match.csv",
        "bks_table_csv_path": "benchmarks/PRA2017/pra2017_bks_table.csv",
        "output_dir": "output/20260726_csr_init_tl_curve",
        "instance_worker_cnt": 12,
        "draw_gantt": False,
        "draw_progress_plot": False,
        "painter_thread_cnt": 1,
        "scenarios": scenarios,
    }


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate(config: dict) -> None:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    config = build_config(build_scenarios())
    validate(config)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as fh:
        yaml.safe_dump(config, fh, sort_keys=False, default_flow_style=False)
    print(f"wrote {args.out}: {len(config['scenarios'])} scenarios x full 1440-grid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
