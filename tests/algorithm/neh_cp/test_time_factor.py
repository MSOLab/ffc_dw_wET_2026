"""``time_factor`` (CSR coarse-grid) support for NEH-CP.

A coarsened instance (``coarsen_processing_times``) keeps due windows at the
ORIGINAL scale while processing times live on a coarse grid. Running NEH-CP on
it with ``time_factor=factor`` must interpret every coarse completion ``C^c`` as
original-scale ``factor * C^c`` when comparing against the due window — mirroring
``BaseModelBuilder`` / ``compute_weighted_earliness_tardiness(..., time_factor=)``.

``time_factor=1`` must reproduce the pre-existing behaviour exactly.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.neh_cp import NehCpDispatcher, NehCpOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness


def _make_original_instance() -> FFcDDWParameters:
    """A tiny 3-job / 2-stage instance with processing times big enough that
    ceil-coarsening genuinely changes them."""
    return FFcDDWParameters(
        name="neh_cp_tf",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="neh_cp_tf_p",
            df=pd.DataFrame([[20, 30], [20, 20], [20, 10]]),
        ),
        job_2_due_window_map={"j0": (40, 55), "j1": (30, 45), "j2": (0, 100)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _make_order_instance() -> FFcDDWParameters:
    """Two single-stage jobs whose optimal completion order flips depending on
    whether coarse completion is interpreted at ``time_factor``.

    Coarsened by 50, each ``p=50`` becomes coarse ``p=1``; horizon = 2 so the
    two jobs occupy coarse slots ``[0,1]`` and ``[1,2]`` (real 50 / 100 at
    ``time_factor=50``). ``jB`` (window 40-60) must take slot 1 and ``jA``
    (window 90-110) slot 2 for zero E/T. That ordering is only optimal when the
    completion is read as ``50 * C^c``.
    """
    return FFcDDWParameters(
        name="neh_cp_tf_order",
        job_id_list=["jA", "jB"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="neh_cp_tf_order_p",
            df=pd.DataFrame([[50], [50]]),
        ),
        job_2_due_window_map={"jA": (90, 110), "jB": (40, 60)},
        job_2_ewt_map={"jA": 1, "jB": 1},
        job_2_twt_map={"jA": 1, "jB": 1},
    )


# ---- option contract ----


def test_time_factor_default_is_one() -> None:
    assert NehCpOption().time_factor == 1


def test_time_factor_one_equals_default_option() -> None:
    assert NehCpOption(time_factor=1) == NehCpOption()


def test_time_factor_below_one_rejected() -> None:
    with pytest.raises(ValueError, match="time_factor"):
        NehCpOption(time_factor=0)
    with pytest.raises(ValueError, match="time_factor"):
        NehCpOption(time_factor=-5)


# ---- invariance at time_factor == 1 ----


def test_time_factor_one_reproduces_default_behaviour() -> None:
    instance = _make_original_instance()
    default = NehCpDispatcher().run(
        AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=1.0))
    )
    tf1 = NehCpDispatcher().run(
        AlgSpec(instance=instance, option=NehCpOption(cp_tl_seconds=1.0, time_factor=1))
    )
    assert default.result is not None and tf1.result is not None
    assert tf1.result.obj_value == default.result.obj_value
    sched_d = default.result.schedule
    sched_1 = tf1.result.schedule
    assert sched_d is not None and sched_1 is not None
    for stage_id in instance.stage_id_list:
        for job_id in instance.job_id_list:
            assert sched_1.get_job_end_time(
                stage_id, job_id
            ) == sched_d.get_job_end_time(stage_id, job_id)


# ---- coarse-instance objective correctness (self-consistency) ----


def test_obj_value_matches_scaled_recompute_on_coarse_instance() -> None:
    factor = 10
    original = _make_original_instance()
    coarse = FFcDDWParameters.coarsen_processing_times(original, factor)
    # coarsening preserves the original-scale due windows.
    assert coarse.job_2_due_window_map == original.job_2_due_window_map

    record = NehCpDispatcher().run(
        AlgSpec(
            instance=coarse,
            option=NehCpOption(cp_tl_seconds=2.0, time_factor=factor),
        )
    )
    assert record.work_status == WorkStatus.FEASIBLE
    assert record.result is not None
    schedule = record.result.schedule
    assert schedule is not None

    sum_e, sum_t = compute_weighted_earliness_tardiness(
        schedule, coarse, time_factor=factor
    )
    assert record.result.obj_value == float(sum_e + sum_t)
    # progress-log terminal point is the same scaled objective.
    assert record.progress_log is not None
    assert record.progress_log[-1].obj_value == record.result.obj_value


# ---- CP insertion decision provably depends on time_factor ----


def test_cp_order_decision_depends_on_time_factor() -> None:
    factor = 50
    original = _make_order_instance()
    coarse = FFcDDWParameters.coarsen_processing_times(original, factor)
    last_stage = coarse.stage_id_list[-1]
    # sanity: coarse processing time is 1 for both jobs.
    assert coarse.get_job_2_p_map_for_stage(last_stage) == {"jA": 1, "jB": 1}

    record = NehCpDispatcher().run(
        AlgSpec(
            instance=coarse,
            option=NehCpOption(cp_tl_seconds=2.0, time_factor=factor),
        )
    )
    assert record.result is not None
    schedule = record.result.schedule
    assert schedule is not None
    # Optimum at the true scale is zero, achieved only by jB-then-jA.
    assert record.result.obj_value == 0.0
    assert schedule.get_job_end_time(last_stage, "jB") < schedule.get_job_end_time(
        last_stage, "jA"
    )

    # Same coarse instance solved as if it were fine-scale (time_factor=1):
    # its schedule, scored at the true scale, is strictly worse than the
    # time_factor-aware optimum — the decision genuinely depends on time_factor.
    record_tf1 = NehCpDispatcher().run(
        AlgSpec(
            instance=coarse,
            option=NehCpOption(cp_tl_seconds=2.0, time_factor=1),
        )
    )
    assert record_tf1.result is not None
    schedule_tf1 = record_tf1.result.schedule
    assert schedule_tf1 is not None
    sum_e, sum_t = compute_weighted_earliness_tardiness(
        schedule_tf1, coarse, time_factor=factor
    )
    assert float(sum_e + sum_t) > 0.0
