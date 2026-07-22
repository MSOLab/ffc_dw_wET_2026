"""SwCpModelBuilder time_factor tests (CSR W1).

Hand-checkable micro-cases proving the partial E/T objective terms
interpret a coarse completion ``C^c`` as ``time_factor * C^c`` against the
instance's (original-scale) due window — mirroring
``BaseModelBuilder._define_objective``.
"""

from __future__ import annotations

import pandas as pd
from ortools.sat.python import cp_model

from ffc_ddw_sum_et.algorithm.sw_cp import (
    OperationPartition,
    SwCpModelBuilder,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule


def _single_stage_instance(
    jobs: list[str],
    coarse_p: list[int],
    due_windows: dict[str, tuple[int, int]],
) -> FFcDDWParameters:
    return FFcDDWParameters(
        name="swcp_tf_model",
        job_id_list=jobs,
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="swcp_tf_model_p",
            df=pd.DataFrame([[p] for p in coarse_p]),
        ),
        job_2_due_window_map=due_windows,
        job_2_ewt_map={j: 1 for j in jobs},
        job_2_twt_map={j: 1 for j in jobs},
    )


def _solve(mdl: cp_model.CpModel) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    status = solver.solve(mdl)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    return solver


def test_partial_objective_optimum_differs_between_factors() -> None:
    """Hand-forced single-job case: with coarse ``C^c=1`` and window [2, 4],
    the CP optimum is on-time (obj 0) at ``time_factor=1`` but tardy
    (obj 1, from ``5*1 - 4``) at ``time_factor=5``. This can only happen if
    the objective terms multiply the completion by ``time_factor``."""
    instance = _single_stage_instance(["j0"], [1], {"j0": (2, 4)})
    rj = FFcSchedule(["j0"], ["i0"], {"i0": ["i0_0"]})
    rj.add_ops_times_2_mc("i0", "i0_0", "j0", 0, 1)
    partition = {
        "i0": OperationPartition(
            left_time_fixed=(),
            left_profile_fixed=(),
            unfixed=(("j0", "i0_0"),),
            right_profile_fixed=(),
            right_time_fixed=(),
        )
    }

    builder = SwCpModelBuilder()

    build_f5 = builder.build(
        instance, rj, partition, horizon=3, pf_method="PF1", time_factor=5
    )
    solver_f5 = _solve(build_f5.mdl)
    assert solver_f5.objective_value == 1  # 5*1 - 4
    assert int(solver_f5.value(build_f5.op_vars.op_end["j0", "i0"])) == 1

    build_f1 = builder.build(
        instance, rj, partition, horizon=3, pf_method="PF1", time_factor=1
    )
    solver_f1 = _solve(build_f1.mdl)
    assert solver_f1.objective_value == 0  # a coarse C in [2, 4] is on-time
    # at time_factor=1 the solver moves C into the window (>= 2)
    assert int(solver_f1.value(build_f1.op_vars.op_end["j0", "i0"])) >= 2


def test_partial_objective_et_recompute_matches_scaled_completion() -> None:
    """For every objective job, the solved ``E_j`` / ``T_j`` equal
    ``max(0, d_lo - F*C^c)`` / ``max(0, F*C^c - d_upper)``."""
    factor = 5
    instance = _single_stage_instance(
        ["j0", "j1"], [1, 1], {"j0": (2, 4), "j1": (10, 12)}
    )
    rj = FFcSchedule(["j0", "j1"], ["i0"], {"i0": ["i0_0"]})
    rj.add_ops_times_2_mc("i0", "i0_0", "j0", 0, 1)
    rj.add_ops_times_2_mc("i0", "i0_0", "j1", 1, 2)
    partition = {
        "i0": OperationPartition(
            left_time_fixed=(),
            left_profile_fixed=(),
            unfixed=(("j0", "i0_0"), ("j1", "i0_0")),
            right_profile_fixed=(),
            right_time_fixed=(),
        )
    }

    build = SwCpModelBuilder().build(
        instance, rj, partition, horizon=6, pf_method="PF1", time_factor=factor
    )
    solver = _solve(build.mdl)

    for j in build.objective_jobs:
        c = int(solver.value(build.op_vars.op_end[j, "i0"]))
        d_lo, d_hi = instance.job_2_due_window_map[j]
        scaled = factor * c
        assert int(solver.value(build.et_vars.E[j])) == max(0, d_lo - scaled)
        assert int(solver.value(build.et_vars.T[j])) == max(0, scaled - d_hi)


def test_partial_objective_offset_uses_time_factor() -> None:
    """A last-stage *time-fixed* job contributes a constant offset computed
    with ``time_factor``: coarse end 1, window [2, 4], factor 5 →
    ``T = 5*1 - 4 = 1`` (offset 1, not 0 as an un-scaled recompute would give)."""
    factor = 5
    # j0 is time-fixed (RTF); j1 is unfixed so the model still has a variable.
    instance = _single_stage_instance(
        ["j0", "j1"], [1, 1], {"j0": (2, 4), "j1": (10, 12)}
    )
    rj = FFcSchedule(["j0", "j1"], ["i0"], {"i0": ["i0_0"]})
    rj.add_ops_times_2_mc("i0", "i0_0", "j0", 0, 1)
    rj.add_ops_times_2_mc("i0", "i0_0", "j1", 1, 2)
    partition = {
        "i0": OperationPartition(
            left_time_fixed=(),
            left_profile_fixed=(),
            unfixed=(("j1", "i0_0"),),
            right_profile_fixed=(),
            right_time_fixed=(("j0", "i0_0"),),
        )
    }

    build = SwCpModelBuilder().build(
        instance, rj, partition, horizon=6, pf_method="PF1", time_factor=factor
    )
    # j0's last-stage op is time-fixed → contributes via et_offset_partial.
    assert build.objective_jobs == ("j1",)
    assert build.et_offset_partial == 1  # 5*1 - 4
