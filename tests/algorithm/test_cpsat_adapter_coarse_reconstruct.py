"""RED-2 for plans/20260714/cpsat_reconstruct_coarse_et_gap.md.

Integration-level invariant for the CpsatAdapter coarse-grid (``time_factor > 1``)
reconstruction: CP-SAT proves a coarse optimum ``cp_obj`` on the coarse grid,
so the post-processed schedule (``make_semi_active -> insert_idle_time``) must
not report an E/T *above* that proven value.

    obj_value <= metrics["cpsat_obj_value"] + FP_TOL

Today this FAILS: the reconstruction left-compresses the CP-SAT placement and
``insert_idle_time`` cannot recover the in-window coarse cell (the ``K*(C+1)``
partition misclassifies the genuinely-early job as on-time), leaving residual
earliness — the same ``sum_e > 0, sum_t == 0`` signature as the production
``post-process objective 149.000 > CP-SAT objective 0.000`` warning.
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.cpsat_adapter import CpsatAdapter, CpsatOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import (
    JobStageProcessingTimeManager,
)
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.schedule_build import build_schedule_from_op_starts

FP_TOL = 1e-6


def _make_coarse_instance(
    processing_rows: list[list[int]],
    dw: dict[str, tuple[int, int]],
) -> FFcDDWParameters:
    jobs = [f"j{i}" for i in range(len(processing_rows))]
    stages = [f"i{s}" for s in range(len(processing_rows[0]))]
    machines = {s: [f"{s}_0"] for s in stages}
    return FFcDDWParameters(
        name="cpsat_coarse_red2",
        job_id_list=jobs,
        stage_id_list=stages,
        stage_2_machines_map=machines,
        p_manager=JobStageProcessingTimeManager(
            name="cpsat_coarse_red2_p", df=pd.DataFrame(processing_rows)
        ),
        job_2_due_window_map=dw,
        job_2_ewt_map={j: 1 for j in jobs},
        job_2_twt_map={j: 1 for j in jobs},
    )


def _run_coarse_reconstruct():
    """Single job, single stage, coarse p=2, K=50, window (110,170).

    Left-compressed completion C=2 (real 100, early by 10); the in-window
    coarse cell is C=3 (real 150). A ref_solution placed at C=3 gives CP-SAT
    horizon headroom, so CP-SAT proves obj 0 — which reconstruction loses.
    """
    K = 50
    inst = _make_coarse_instance([[2]], {"j0": (110, 170)})
    ref = build_schedule_from_op_starts(inst, {("j0", "i0"): 1}, {("j0", "i0"): 3})
    spec = AlgSpec(
        instance=inst,
        option=CpsatOption(time_factor=K, timelimit_sec=5.0, solver_thread_cnt=1),
        ref_solution=ref,
    )
    return CpsatAdapter().run(spec)


def test_cpsat_adapter_coarse_reconstruct_not_above_cp_obj() -> None:
    """obj_value must not exceed CP-SAT's proven coarse objective (RED until B)."""
    rec = _run_coarse_reconstruct()
    assert rec.work_status in (WorkStatus.OPTIMAL, WorkStatus.FEASIBLE)
    obj_value = rec.result.obj_value
    cp_obj = rec.result.metrics["cpsat_obj_value"]
    assert obj_value is not None
    assert obj_value <= cp_obj + FP_TOL, (
        f"reconstruction obj_value {obj_value} > CP-SAT obj {cp_obj}: "
        f"insert_idle_time did not recover the proven coarse placement"
    )


def test_cpsat_adapter_coarse_residual_is_earliness() -> None:
    """Diagnostic: the residual gap is entirely earliness (matches the
    production sum_e=149, sum_t=0 warning)."""
    rec = _run_coarse_reconstruct()
    metrics = rec.result.metrics
    gap = rec.result.obj_value - metrics["cpsat_obj_value"]
    if gap > FP_TOL:  # while RED, assert the failure shape; post-B, gap == 0
        assert metrics["sum_earliness"] > 0
        assert metrics["sum_tardiness"] == 0
