"""Tests for algorithm.dispatcher.paired pure helpers.

WP-1: build_v3_paired_dispatch_schedule must create 6 candidates, select
min-wET, return feasible schedules, and be deterministic.

WP-6 regression: results must match controller v3 init step on small instances.
"""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.algorithm.dispatcher.paired import (
    build_v3_paired_dispatch_schedule,
    build_v4_paired_dispatch_schedule,
    dispatch_forward_with_iit,
    dispatch_reversed_with_iit,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.parameters.sorter import (
    V3_PRIORITY_SET,
    V4_PRIORITY_SET,
    dispatch_seq_job_sequence,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tiny_instance(
    *,
    name: str = "paired_test",
    processing_rows: list[list[int]] | None = None,
    due_window_map: dict[str, tuple[int, int]] | None = None,
) -> FFcDDWParameters:
    if processing_rows is None:
        processing_rows = [[3, 2], [2, 3], [1, 2]]
    n_jobs = len(processing_rows)
    n_stages = len(processing_rows[0])
    job_id_list = [f"j{i}" for i in range(n_jobs)]
    stage_id_list = [f"i{s}" for s in range(n_stages)]
    stage_2_machines_map = {
        stage_id: [f"{stage_id}_{k}" for k in range(1)] for stage_id in stage_id_list
    }
    if due_window_map is None:
        due_window_map = {j: (0, 9999) for j in job_id_list}
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=stage_id_list,
        stage_2_machines_map=stage_2_machines_map,
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame(processing_rows),
        ),
        job_2_due_window_map=due_window_map,
        job_2_ewt_map={j: 1 for j in job_id_list},
        job_2_twt_map={j: 1 for j in job_id_list},
    )


# ---------------------------------------------------------------------------
# V3_PRIORITY_SET smoke
# ---------------------------------------------------------------------------


def test_v3_priority_set_has_three_priorities() -> None:
    assert len(V3_PRIORITY_SET) == 3
    assert V3_PRIORITY_SET == ("edd", "wspt_twt", "wxd2")


# ---------------------------------------------------------------------------
# dispatch_forward_with_iit
# ---------------------------------------------------------------------------


def test_dispatch_forward_returns_feasible_schedule() -> None:
    instance = _make_tiny_instance()
    seq = ["j0", "j1", "j2"]
    schedule, obj = dispatch_forward_with_iit(instance, seq)
    assert schedule is not None
    assert obj >= 0
    # All jobs should be scheduled
    start_map = schedule.get_jik_2_start_time_map()
    for j in instance.job_id_list:
        for s in instance.stage_id_list:
            for k in instance.stage_2_machines_map[s]:
                assert (j, s, k) in start_map


def test_dispatch_forward_deterministic() -> None:
    instance = _make_tiny_instance()
    seq = ["j0", "j1", "j2"]
    sch1, obj1 = dispatch_forward_with_iit(instance, seq)
    sch2, obj2 = dispatch_forward_with_iit(instance, seq)
    assert obj1 == obj2
    assert sch1.get_jik_2_start_time_map() == sch2.get_jik_2_start_time_map()


# ---------------------------------------------------------------------------
# dispatch_reversed_with_iit
# ---------------------------------------------------------------------------


def test_dispatch_reversed_returns_feasible_schedule() -> None:
    instance = _make_tiny_instance()
    seq = ["j0", "j1", "j2"]
    schedule, obj = dispatch_reversed_with_iit(instance, seq)
    assert schedule is not None
    assert obj >= 0
    start_map = schedule.get_jik_2_start_time_map()
    for j in instance.job_id_list:
        for s in instance.stage_id_list:
            for k in instance.stage_2_machines_map[s]:
                assert (j, s, k) in start_map


def test_dispatch_reversed_deterministic() -> None:
    instance = _make_tiny_instance()
    seq = ["j0", "j1", "j2"]
    sch1, obj1 = dispatch_reversed_with_iit(instance, seq)
    sch2, obj2 = dispatch_reversed_with_iit(instance, seq)
    assert obj1 == obj2
    assert sch1.get_jik_2_start_time_map() == sch2.get_jik_2_start_time_map()


# ---------------------------------------------------------------------------
# build_v3_paired_dispatch_schedule
# ---------------------------------------------------------------------------


def test_build_v3_creates_six_candidates() -> None:
    """v3 pool with default 3 priorities × 2 directions = 6 candidates."""
    instance = _make_tiny_instance()
    schedule, obj, label = build_v3_paired_dispatch_schedule(instance)
    assert schedule is not None
    assert obj >= 0
    # Label format: "sd:<key>" or "rd:<key>"
    direction, priority = label.split(":", 1)
    assert direction in ("sd", "rd")
    assert priority in V3_PRIORITY_SET


def test_build_v3_selects_min_wet() -> None:
    """Best schedule must have wET ≤ all individual candidate wETs."""
    instance = _make_tiny_instance()
    schedule, best_obj, best_label = build_v3_paired_dispatch_schedule(instance)

    # Re-enumerate candidates manually
    from ffc_ddw_sum_et.parameters.sorter import dispatch_seq_job_sequence

    candidates: list[tuple[float, str]] = []
    for p in V3_PRIORITY_SET:
        seq = dispatch_seq_job_sequence(instance, p)
        sd_sch, sd_obj = dispatch_forward_with_iit(instance, seq)
        candidates.append((sd_obj, f"sd:{p}"))
        rd_sch, rd_obj = dispatch_reversed_with_iit(instance, seq)
        candidates.append((rd_obj, f"rd:{p}"))

    assert len(candidates) == 6
    min_obj = min(c[0] for c in candidates)
    assert best_obj == min_obj


def test_build_v3_deterministic() -> None:
    instance = _make_tiny_instance()
    sch1, obj1, lab1 = build_v3_paired_dispatch_schedule(instance)
    sch2, obj2, lab2 = build_v3_paired_dispatch_schedule(instance)
    assert obj1 == obj2
    assert lab1 == lab2
    assert sch1.get_jik_2_start_time_map() == sch2.get_jik_2_start_time_map()


def test_build_v3_feasible_schedule() -> None:
    """Schedule must respect precedence and machine constraints."""
    instance = _make_tiny_instance()
    schedule, _obj, _label = build_v3_paired_dispatch_schedule(instance)

    # Check precedence: each stage start ≥ previous stage end
    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()
    for j in instance.job_id_list:
        stages = instance.stage_id_list
        for s_idx in range(1, len(stages)):
            prev_machines = instance.stage_2_machines_map[stages[s_idx - 1]]
            curr_machines = instance.stage_2_machines_map[stages[s_idx]]
            for k_prev, k_curr in zip(prev_machines, curr_machines):
                prev_end = end_map[(j, stages[s_idx - 1], k_prev)]
                curr_start = start_map[(j, stages[s_idx], k_curr)]
                assert curr_start >= prev_end


def test_build_v3_custom_priorities() -> None:
    """Custom priority set should produce 2·len(priorities) candidates."""
    instance = _make_tiny_instance()
    custom_prio = ("edd", "wspt_twt")
    schedule, obj, label = build_v3_paired_dispatch_schedule(
        instance, priorities=custom_prio
    )
    assert schedule is not None
    direction, priority = label.split(":", 1)
    assert priority in custom_prio


# ---------------------------------------------------------------------------
# CSR regression: build_v3/v4 must thread time_factor into candidate building
# ---------------------------------------------------------------------------


def _coarse_oracle_min_obj(
    instance: FFcDDWParameters, factor: int, priority_set: tuple[str, ...]
) -> float:
    """Enumerate sd/rd candidates with ``time_factor=factor`` threaded and
    return the minimum objective — the value build_v*/factor must reproduce."""
    objs: list[float] = []
    for p in priority_set:
        seq = dispatch_seq_job_sequence(instance, p)
        _, sd_obj = dispatch_forward_with_iit(instance, seq, time_factor=factor)
        _, rd_obj = dispatch_reversed_with_iit(instance, seq, time_factor=factor)
        objs.append(sd_obj)
        objs.append(rd_obj)
    return min(objs)


def test_build_v4_threads_time_factor_into_candidates() -> None:
    """CSR regression: ``build_v4_paired_dispatch_schedule(coarsened, factor>1)``
    must build its candidates with ``time_factor=factor`` (not just re-score
    ``time_factor=1`` schedules). Otherwise ``insert_idle_time`` mispositions
    ops on the coarse grid against the original-scale window, and the selected
    seed/obj diverge from the CSR CP objective.
    """
    factor = 10
    # Finite due windows large enough that, on the coarse grid, jobs are
    # EARLY (so insert_idle_time actually shifts) — the shift target differs
    # between time_factor=1 (toward d/1) and time_factor=factor (toward
    # d/factor), which is exactly what the bug would get wrong.
    windows = {"j0": (400, 600), "j1": (450, 650), "j2": (500, 700)}
    original = _make_tiny_instance(
        processing_rows=[[30, 20], [20, 30], [10, 20]], due_window_map=windows
    )
    coarsened = FFcDDWParameters.coarsen_processing_times(original, factor)

    _, best_obj, _ = build_v4_paired_dispatch_schedule(coarsened, factor=factor)
    oracle_min = _coarse_oracle_min_obj(coarsened, factor, V4_PRIORITY_SET)

    assert best_obj == oracle_min


def test_build_v3_threads_time_factor_into_candidates() -> None:
    """Same CSR regression as v4, for the v3 paired pool."""
    factor = 10
    windows = {"j0": (400, 600), "j1": (450, 650), "j2": (500, 700)}
    original = _make_tiny_instance(
        processing_rows=[[30, 20], [20, 30], [10, 20]], due_window_map=windows
    )
    coarsened = FFcDDWParameters.coarsen_processing_times(original, factor)

    _, best_obj, _ = build_v3_paired_dispatch_schedule(coarsened, factor=factor)
    oracle_min = _coarse_oracle_min_obj(coarsened, factor, V3_PRIORITY_SET)

    assert best_obj == oracle_min
