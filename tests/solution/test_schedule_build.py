"""Unit tests for the coarse-scale reconstruct helpers in ``schedule_build``.

These exercise ``reconstruct_raw_coarse_schedule`` (scaling only, no
postprocess) and ``reconstruct_coarse_schedule`` (the ET-aligned wrapper)
directly, independent of the coarsen-solve-reconstruct pipeline. The pipeline
itself is covered at the trace level in
``tests/algorithm/test_coarsen_solve_reconstruct.py``; these pin the contract of
the extracted, reusable functions.
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.schedule_build import (
    build_schedule_from_op_starts,
    reconstruct_coarse_schedule,
    reconstruct_raw_coarse_schedule,
)

FACTOR = 10


def _instance() -> FFcDDWParameters:
    """3 jobs × 2 stages; i0 has 2 machines, i1 has 1. Original p = 10 every op."""
    return FFcDDWParameters(
        name="recon_test",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="recon_p", df=pd.DataFrame([[10, 10], [10, 10], [10, 10]])
        ),
        job_2_due_window_map={"j0": (10, 20), "j1": (20, 30), "j2": (30, 40)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _coarse_schedule(
    instance: FFcDDWParameters,
) -> tuple[object, dict[tuple[str, str], int]]:
    """A feasible coarse-scale schedule (coarse p == 1 at FACTOR=10).

    The last stage (i1) is deliberately *gapped* (starts 5/6/7) so that the
    reconstruction's ``make_semi_active`` left-shift is non-trivial, letting
    tests distinguish the raw schedule from the post-processed one.
    """
    coarsened = FFcDDWParameters.coarsen_processing_times(instance, FACTOR)
    coarse_start = {
        ("j0", "i0"): 0,
        ("j1", "i0"): 0,
        ("j2", "i0"): 1,
        ("j0", "i1"): 5,
        ("j1", "i1"): 6,
        ("j2", "i1"): 7,
    }
    coarse_end = {k: v + 1 for k, v in coarse_start.items()}
    coarse_sched = build_schedule_from_op_starts(coarsened, coarse_start, coarse_end)
    return coarse_sched, coarse_start


def _ji_starts(schedule) -> dict[tuple[str, str], int]:
    return {(j, i): s for (j, i, _mc), s in schedule.get_jik_2_start_time_map().items()}


def test_reconstruct_raw_scales_starts_by_factor() -> None:
    inst = _instance()
    coarse_sched, coarse_start = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)

    raw_start = _ji_starts(raw)
    for (j, i), cs in coarse_start.items():
        assert raw_start[j, i] == cs * FACTOR


def test_reconstruct_raw_reapplies_original_p() -> None:
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)

    starts = raw.get_jik_2_start_time_map()
    ends = raw.get_jik_2_end_time_map()
    original_p = inst.job_2_stage_2_p_map
    for (j, i, mc), s in starts.items():
        assert ends[j, i, mc] - s == original_p[j][i]


def test_reconstruct_raw_is_pre_postprocess() -> None:
    """Raw must NOT have make_semi_active/insert_idle_time applied: its last-stage
    starts equal coarse*factor (gapped), unlike the post-processed schedule."""
    inst = _instance()
    coarse_sched, coarse_start = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)
    final = reconstruct_coarse_schedule(coarse_sched, inst, FACTOR)

    raw_start = _ji_starts(raw)
    # Last stage is gapped in coarse scale -> raw keeps coarse*factor positions.
    assert raw_start["j0", "i1"] == coarse_start["j0", "i1"] * FACTOR
    # make_semi_active left-shifts the gapped last stage, so raw != final.
    assert _ji_starts(raw) != _ji_starts(final)


def test_reconstruct_coarse_equals_raw_plus_postprocess() -> None:
    """The wrapper is exactly raw + make_semi_active + insert_idle_time."""
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    final = reconstruct_coarse_schedule(coarse_sched, inst, FACTOR)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)
    raw.make_semi_active(inst.stage_2_job_2_p_map)
    raw.insert_idle_time(
        inst.job_2_due_window_map,
        inst.job_2_ewt_map,
        inst.job_2_twt_map,
    )

    assert raw.get_jik_2_start_time_map() == final.get_jik_2_start_time_map()
    assert raw.get_jik_2_end_time_map() == final.get_jik_2_end_time_map()


def test_reconstruct_raw_and_coarse_are_distinct_objects() -> None:
    """Separate calls return independent schedules (CSR keeps both as snapshots)."""
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)
    final = reconstruct_coarse_schedule(coarse_sched, inst, FACTOR)

    assert raw is not final


def test_reconstruct_raw_not_mutated_by_building_wrapper() -> None:
    """Building the wrapper must not mutate a separately-obtained raw schedule."""
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)
    before = _ji_starts(raw)

    _ = reconstruct_coarse_schedule(coarse_sched, inst, FACTOR)

    assert _ji_starts(raw) == before


def test_reconstruct_raw_uses_original_instance_layout() -> None:
    """Reconstruction is on the original instance: stage ids and durations match."""
    inst = _instance()
    coarse_sched, _ = _coarse_schedule(inst)

    raw = reconstruct_raw_coarse_schedule(coarse_sched, inst, FACTOR)

    assert raw.stages == inst.stage_id_list
    assert set(raw.jobs) == set(inst.job_id_list)
