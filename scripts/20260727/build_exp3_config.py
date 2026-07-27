"""Generate the 실험 3 config: CSR usability sweep over budget x inner-flow depth.

Base grid: arm {a, b, c, m1} x tau {1,2,4,8} x f {1,2,3,4,5,10,20,40}% with
`coarsen_mode: round` and `reconstruct_mode: active_but_last_semi`, on the
160-instance (T, R) = (0.6, 0.2) cell (the `ins_index` list is read from the
20260725 crossover ladder config so the two runs share an instance set exactly).

Two side blocks ride along:

* **ceil probe** -- `m1` x tau {2,4,8} at f=40 only. 실험 2 judged round > ceil,
  but measured f <= 15 only; this checks whether the ranking survives at the top
  of the budget axis. Names keep the ladder's convention so the existing
  analysis picks them up with no change.
* **reconstruct sweep** -- arms `msemi` / `mactive` are the `m1` inner flow with
  `reconstruct_mode` set to `semi_active` / `active`. They carry their own tau=1
  baselines because reconstruct_mode is NOT an identity at tau=1 (unlike
  coarsen_mode), so each mode needs its own comparator.

Three design points differ from the ladder (`metadata/20260725/coarsening_crossover.yaml`):

1. Arms `a` and `b` are NOT swept over f. Both carry the untouched outer budget
   (`0.09nc`) and neither can bind on it -- `a` never calls CP at all
   (`solve: False`) and `b` runs only the non-interruptible `mcf_lb`. Sweeping
   them would emit eight identical columns.
2. Arm `c`'s CSR `timelimit` is f-scaled like `m1`'s, where the ladder left it
   at `0.09nc` and scaled only the flip's `cp_tl`. With the ladder's setting
   `mcf_lb` sat *outside* the budget, so `c` and `m1` were not equal-budget and
   could not be compared horizontally. They now are.
3. Only `round` (plus the f=40 ceil probe) is run, where the ladder swept all
   four rounding rules.

Rounding IS the identity at factor=1, so tau=1 scenarios carry no mode in their
name (matching the ladder, whose `SCENARIO_RE` reads mode as None there) while
still setting `coarsen_mode` explicitly in the config.

Plan: plans/experiment/20260727/csr_usability_budget_arm_sweep.md
"""

from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import yaml

DEST_CONFIG = Path("metadata/20260727/csr_usability_sweep.yaml")
# Instance set: reuse the ladder's (T, R) = (0.6, 0.2) cell verbatim.
SRC_INS_INDEX_CONFIG = Path("metadata/20260725/coarsening_crossover.yaml")

TAU_VALUES = (1, 2, 4, 8)
F_VALUES = (1, 2, 3, 4, 5, 10, 20, 40)
COARSEN_MODE = "round"
RECONSTRUCT_MODE = "active_but_last_semi"
OUTER_TL = "0.09nc"

# Side block 1: does round > ceil survive at the top of the budget axis?
CEIL_PROBE_MODE = "ceil"
CEIL_PROBE_TAUS = (2, 4, 8)
CEIL_PROBE_F = 40

# Side block 2: `m1` inner flow under the other two reconstruct modes.
RECON_ARMS = {"msemi": "semi_active", "mactive": "active"}

# Per-percent coefficients: the step's budget is <coeff> * f * n * c.
CSR_TL_COEFF = Decimal("0.0009")  # CSR total (arms c, m1)
FLIP_TL_COEFF = Decimal("0.00009")  # m1's flip -- 10% of the CSR budget
NEH_TL_COEFF = Decimal("0.00027")  # m1's neh_cp -- 30% of the CSR budget
ISW_MULT_COEFF = Decimal("0.00005")  # m1's incremental_sw_cp multiplier


def _nc(coeff: Decimal, f: int) -> str:
    """``(0.0009, 40) -> "0.036nc"`` -- exact decimal, no float artifacts."""
    return f"{format((coeff * f).normalize(), 'f')}nc"


def _mcf_lb_step() -> dict:
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


def _flip_step(cp_tl: str) -> dict:
    return {
        "method": "run_flip_makespan_cp_from_incumbent",
        "cp_tl": cp_tl,
        "solver_thread_cnt": 8,
        "log_search_progress": False,
        "emit_phase_schedules": False,
    }


def _m1_tail_steps(f: int) -> list[dict]:
    return [
        {
            "method": "neh_cp",
            "job_priority": "due2-weight-pos",
            "solver_thread_cnt": 8,
            "added_batch_size": 15,
            "total_timelimit": _nc(NEH_TL_COEFF, f),
            "batch_tl_mode": "linear",
            "apply_cumulative_tl": False,
            "pf_method": "PF1",
            "skip_pf_below_obj": "makespan",
            "make_semi_active_after_cp": True,
            "minimize_makespan_lex": False,
        },
        {
            "method": "incremental_sw_cp",
            "solver_thread_cnt": 8,
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
            "non_time_fixed_op_time_limit_multiplier": float(ISW_MULT_COEFF * f),
            "rj_right_justify_scope": "rtf_only",
        },
        {"method": "solve_base_model_cpsat", "solver_thread_cnt": 8},
    ]


def _csr_step(arm: str, tau: int, f: int | None, mode: str) -> dict:
    """The single `coarsen_solve_reconstruct` step for one (arm, tau, f, mode)."""
    step: dict = {
        "method": "coarsen_solve_reconstruct",
        "factor": tau,
        "coarsen_mode": mode,
    }

    if arm == "a":
        # Dispatch-only seed, no CP anywhere -- budget never binds.
        step["timelimit"] = OUTER_TL
        step["seed_dispatch"] = "v4"
        step["solve"] = False
    elif arm == "b":
        # Non-interruptible mcf_lb only -- budget never binds.
        step["timelimit"] = OUTER_TL
        step["solve_flow"] = [_mcf_lb_step()]
    elif arm == "c":
        assert f is not None
        # f-scaled CSR budget (differs from the ladder); the flip is the only
        # CP step, so it is handed the whole budget and the CSR timelimit binds.
        step["timelimit"] = _nc(CSR_TL_COEFF, f)
        step["solve_flow"] = [_mcf_lb_step(), _flip_step(_nc(CSR_TL_COEFF, f))]
    elif arm in ("m1", *RECON_ARMS):
        assert f is not None
        step["timelimit"] = _nc(CSR_TL_COEFF, f)
        step["solve_flow"] = [
            _mcf_lb_step(),
            _flip_step(_nc(FLIP_TL_COEFF, f)),
            *_m1_tail_steps(f),
        ]
    else:
        raise ValueError(f"unknown arm {arm!r}")

    step["reconstruct_mode"] = RECON_ARMS.get(arm, RECONSTRUCT_MODE)
    step["dump_csr_coarse"] = False
    return step


def _name(arm: str, tau: int, f: int | None, mode: str) -> str:
    """``{arm}_k{K}[_{mode}][_f{NN}]`` -- the ladder's convention.

    tau=1 carries no mode because rounding is the identity there, which is what
    ``analyze_crossover_ladder.SCENARIO_RE`` expects of a baseline scenario.
    """
    parts = [arm, f"k{tau}"]
    if tau > 1:
        parts.append(mode)
    if f is not None:
        parts.append(f"f{f:02d}")
    return "_".join(parts)


def _scenario(arm: str, tau: int, f: int | None, mode: str) -> dict:
    name = _name(arm, tau, f, mode)
    return {
        "name": name,
        "timelimit": OUTER_TL,
        "output_subdir": name,
        "subroutine_flow": [copy.deepcopy(_csr_step(arm, tau, f, mode))],
    }


def build_scenarios() -> list[dict]:
    scenarios: list[dict] = []

    # Base grid + the two reconstruct-sweep arms.
    for arm in ("a", "b", "c", "m1", *RECON_ARMS):
        f_axis: tuple[int | None, ...] = (
            (None,) if arm in ("a", "b") else tuple(F_VALUES)
        )
        for f in f_axis:
            for tau in TAU_VALUES:
                scenarios.append(_scenario(arm, tau, f, COARSEN_MODE))

    # ceil probe at the top of the budget axis (tau=1 is rounding-invariant, so
    # the existing m1_k1_f40 serves as its baseline -- no extra scenario).
    for tau in CEIL_PROBE_TAUS:
        scenarios.append(_scenario("m1", tau, CEIL_PROBE_F, CEIL_PROBE_MODE))

    return scenarios


HEADER = f"""\
# 실험 3 -- CSR usability screen: budget x inner-flow depth x reconstruct mode
#
# SCREENING RUN on the 160-instance (T, R) = (0.6, 0.2) cell. The full-1440
# follow-up runs only the cells this screen shows to be worth it.
#
# Base grid: arm {{a, b, c, m1}} x tau {{1,2,4,8}} x f {{1,2,3,4,5,10,20,40}}%
#   coarsen_mode     = {COARSEN_MODE!r}  (identity at tau=1 -> tau=1 names carry no mode)
#   reconstruct_mode = {RECONSTRUCT_MODE!r}
#
# Arms isolate the two channels coarsening acts through:
#   a  dispatch-only (solve: False)   -- resolution loss only
#   b  mcf_lb only                    -- resolution loss only
#   c  mcf_lb -> flip CP              -- + one equal-budget CP stage
#   m1 full 5-step solve_flow         -- + algorithmic depth
#
# Side blocks:
#   m1_k{{2,4,8}}_ceil_f40   ceil probe -- 실험 2 判 round > ceil but measured
#                          f <= 15 only; does it survive at the top of the axis?
#   msemi_* / mactive_*    the m1 flow under reconstruct_mode semi_active /
#                          active. These carry their OWN tau=1 baselines because
#                          reconstruct_mode is not an identity at tau=1.
#
# a and b are NOT swept over f: both keep timelimit=0.09nc and neither can bind
# on it, so an f sweep would emit eight identical columns.
#
# c's CSR timelimit is f-scaled here, unlike the 20260725 ladder which left it
# at 0.09nc and scaled only the flip cp_tl. mcf_lb is now inside the budget, so
# c and m1 are equal-budget and can be compared horizontally -- but c's numbers
# no longer line up with the ladder's c arm.
#
# ins_index is the ladder's cell verbatim, so the 24 settings-identical
# scenarios (m1/a/b at f<=4, round) double as a reproduction gate against
# output/20260725_crossover_ladder/20260726T173841_347539.
#
# Derived from metadata/20260725/coarsening_crossover.yaml.
# Plan:   plans/experiment/20260727/csr_usability_budget_arm_sweep.md
# Config generated by: scripts/20260727/build_exp3_config.py
"""


def main() -> None:
    scenarios = build_scenarios()
    names = [s["name"] for s in scenarios]
    assert len(names) == len(set(names)), "duplicate scenario names"

    ins_index = yaml.safe_load(SRC_INS_INDEX_CONFIG.read_text())["ins_index"]

    config: dict = {
        "run_mode": "FULL_RUN",
        "benchmark_dir": "benchmarks/PRA2017/large",
        "ins_index_source": "benchmarks/PRA2017/pra2017_hybrid_match.csv",
        "bks_table_csv_path": "benchmarks/PRA2017/pra2017_bks_table.csv",
        "output_dir": "output/20260727_csr_usability_t06",
        "ins_index": ins_index,
        "instance_worker_cnt": 12,
        "draw_gantt": False,
        "draw_progress_plot": False,
        "painter_thread_cnt": 96,
        "scenarios": scenarios,
    }

    DEST_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(DEST_CONFIG, "w", encoding="utf-8") as fh:
        fh.write(HEADER)
        yaml.dump(config, fh, default_flow_style=False, indent=2, sort_keys=False)

    print(f"Wrote {len(scenarios)} scenarios to {DEST_CONFIG}")


if __name__ == "__main__":
    main()
