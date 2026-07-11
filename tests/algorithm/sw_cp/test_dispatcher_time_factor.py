"""End-to-end SwCpDispatcher time_factor tests (CSR W1).

Angle (a): on a coarsened instance run with ``time_factor=factor`` the
returned record's ``obj_value`` must equal
``compute_weighted_earliness_tardiness(schedule, coarse, time_factor=factor)``.

Angle (b): ``time_factor=1`` reproduces the no-field behavior exactly.
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.neh_cp import NehCpDispatcher, NehCpOption
from ffc_ddw_sum_et.algorithm.sw_cp import SwCpDispatcher, SwCpOption
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

FACTOR = 10


def _make_base_instance() -> FFcDDWParameters:
    """Original-scale instance whose coarsen(factor=10) processing times are
    small distinct integers, with original-scale due windows."""
    return FFcDDWParameters(
        name="swcp_tf_base",
        job_id_list=["j0", "j1", "j2", "j3", "j4"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="swcp_tf_base_p",
            df=pd.DataFrame([[20, 30], [20, 20], [20, 10], [10, 20], [30, 10]]),
        ),
        job_2_due_window_map={
            "j0": (40, 50),
            "j1": (30, 40),
            "j2": (50, 80),
            "j3": (50, 60),
            "j4": (80, 100),
        },
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1, "j3": 1, "j4": 1},
        job_2_twt_map={"j0": 2, "j1": 2, "j2": 2, "j3": 2, "j4": 2},
    )


def _seed(coarse: FFcDDWParameters):
    """A feasible coarse incumbent. Seeded at time_factor=1 (default NEH-CP);
    seed quality is irrelevant — SW-CP refines it under the given factor."""
    rec = NehCpDispatcher().run(
        AlgSpec(instance=coarse, option=NehCpOption(cp_tl_seconds=1.0))
    )
    assert rec.result is not None and rec.result.schedule is not None
    return rec.result.schedule


def test_obj_value_matches_time_factor_recompute() -> None:
    base = _make_base_instance()
    coarse = FFcDDWParameters.coarsen_processing_times(base, FACTOR)
    seed = _seed(coarse)

    record = SwCpDispatcher().run(
        AlgSpec(
            instance=coarse,
            option=SwCpOption(
                cp_tl_seconds=1.0, unfixed_batch_count=2, time_factor=FACTOR
            ),
            ref_solution=seed,
        )
    )

    assert record.work_status == WorkStatus.FEASIBLE
    assert record.result is not None and record.result.schedule is not None
    sum_e, sum_t = compute_weighted_earliness_tardiness(
        record.result.schedule, coarse, time_factor=FACTOR
    )
    assert record.result.obj_value == float(sum_e + sum_t)


def test_result_not_worse_than_initial_incumbent_under_factor() -> None:
    """The refined incumbent must not be worse than the dispatcher's starting
    incumbent (seed after semi-active + factor-aware idle insertion), measured
    in the same (factor-scaled) objective space."""
    base = _make_base_instance()
    coarse = FFcDDWParameters.coarsen_processing_times(base, FACTOR)
    seed = _seed(coarse)

    start = seed.deepcopy()
    start.make_semi_active(coarse.stage_2_job_2_p_map)
    start.insert_idle_time(
        coarse.job_2_due_window_map,
        coarse.job_2_ewt_map,
        coarse.job_2_twt_map,
        time_factor=FACTOR,
    )
    se, st = compute_weighted_earliness_tardiness(start, coarse, time_factor=FACTOR)
    start_obj = float(se + st)

    record = SwCpDispatcher().run(
        AlgSpec(
            instance=coarse,
            option=SwCpOption(
                cp_tl_seconds=1.0, unfixed_batch_count=2, time_factor=FACTOR
            ),
            ref_solution=seed,
        )
    )
    assert record.result is not None and record.result.obj_value is not None
    assert record.result.obj_value <= start_obj


def test_time_factor_one_matches_default_field_absent() -> None:
    """Invariance: an option with time_factor=1 (explicit) yields byte-identical
    results to the default option (field untouched) on the same seed."""
    # Use the coarse instance purely as a small synthetic FFcDDW instance; at
    # time_factor=1 the coarse/original distinction is irrelevant.
    coarse = FFcDDWParameters.coarsen_processing_times(_make_base_instance(), FACTOR)
    seed = _seed(coarse)

    default_rec = SwCpDispatcher().run(
        AlgSpec(
            instance=coarse,
            option=SwCpOption(cp_tl_seconds=1.0, unfixed_batch_count=2),
            ref_solution=seed.deepcopy(),
        )
    )
    explicit_rec = SwCpDispatcher().run(
        AlgSpec(
            instance=coarse,
            option=SwCpOption(cp_tl_seconds=1.0, unfixed_batch_count=2, time_factor=1),
            ref_solution=seed.deepcopy(),
        )
    )

    assert default_rec.result is not None and explicit_rec.result is not None
    assert default_rec.result.obj_value == explicit_rec.result.obj_value

    d_sched = default_rec.result.schedule
    e_sched = explicit_rec.result.schedule
    assert d_sched is not None and e_sched is not None
    assert d_sched.get_jik_2_start_time_map() == e_sched.get_jik_2_start_time_map()
    assert d_sched.get_jik_2_end_time_map() == e_sched.get_jik_2_end_time_map()
