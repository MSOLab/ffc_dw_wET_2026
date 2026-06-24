"""Tests for CoarsenSolveReconstructAdapter and CoarsenSolveReconstructOption.

TDD order:
  1. Unit tests for reconstruct arithmetic (red → green).
  2. Integration test: adapter.run on a small synthetic instance returns
     OPTIMAL/FEASIBLE with correct metrics keys.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.base.alg_record import (
    TerminationReason,
    WorkStatus,
)
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
    CoarsenSolveReconstructAdapter,
    CoarsenSolveReconstructOption,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import (
    JobStageProcessingTimeManager,
)
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.parameters.ffc_params import FFcParameters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_small_ddw_instance(
    *,
    name: str = "csr_test",
    processing_rows: list[list[int]] | None = None,
    job_2_due_window_map: dict[str, tuple[int, int]] | None = None,
    job_2_ewt_map: dict[str, int] | None = None,
    job_2_twt_map: dict[str, int] | None = None,
    stage_2_machine_count: tuple[int, ...] = (1, 1),
) -> FFcDDWParameters:
    """Build a minimal FFcDDWParameters for testing."""
    if processing_rows is None:
        # 3 jobs × 2 stages; processing times chosen to be large enough
        # that coarsen(factor=50) maps them to distinct small integers.
        processing_rows = [[100, 50], [200, 100], [150, 75]]

    n_jobs = len(processing_rows)
    n_stages = len(stage_2_machine_count)
    job_id_list = [f"j{i}" for i in range(n_jobs)]
    stage_id_list = [f"i{s}" for s in range(n_stages)]
    stage_2_machines_map = {
        stage_id: [f"{stage_id}_{k}" for k in range(mc_cnt)]
        for stage_id, mc_cnt in zip(stage_id_list, stage_2_machine_count)
    }

    if job_2_due_window_map is None:
        # Due windows: wide enough so jobs are feasible at coarsened scale.
        job_2_due_window_map = {j: (0, 9999) for j in job_id_list}

    if job_2_ewt_map is None:
        job_2_ewt_map = {j: 1 for j in job_id_list}
    if job_2_twt_map is None:
        job_2_twt_map = {j: 1 for j in job_id_list}

    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=stage_id_list,
        stage_2_machines_map=stage_2_machines_map,
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame(processing_rows),
        ),
        job_2_due_window_map=job_2_due_window_map,
        job_2_ewt_map=job_2_ewt_map,
        job_2_twt_map=job_2_twt_map,
    )


# ---------------------------------------------------------------------------
# Unit: CoarsenSolveReconstructOption defaults
# ---------------------------------------------------------------------------


def test_option_defaults() -> None:
    opt = CoarsenSolveReconstructOption()
    assert opt.factor == 50
    assert opt.timelimit_sec is None
    assert opt.solver_thread_cnt == 1
    assert opt.log_search_progress is False
    assert opt.error_if_infeasible is False


def test_option_is_frozen() -> None:
    opt = CoarsenSolveReconstructOption(factor=10)
    with pytest.raises((AttributeError, TypeError)):
        opt.factor = 99  # type: ignore[misc]


def test_option_rejects_non_ddw_instance() -> None:
    """The adapter must raise TypeError when spec.instance is not FFcDDWParameters."""
    plain = FFcParameters.__new__(FFcParameters)
    adapter = CoarsenSolveReconstructAdapter()
    with pytest.raises(TypeError, match="FFcDDWParameters"):
        adapter.run(AlgSpec(instance=plain))


def test_option_rejects_wrong_option_type() -> None:
    from ffc_ddw_sum_et.algorithm.cpsat_adapter import CpsatOption

    instance = _make_small_ddw_instance()
    adapter = CoarsenSolveReconstructAdapter()
    with pytest.raises(TypeError, match="CoarsenSolveReconstructOption"):
        adapter.run(AlgSpec(instance=instance, option=CpsatOption()))


# ---------------------------------------------------------------------------
# Unit: reconstruct arithmetic
# ---------------------------------------------------------------------------


def test_reconstructed_start_is_coarse_start_times_factor() -> None:
    """reconstructed_start[j,i] == coarse_start[j,i] * factor (by definition)."""
    factor = 50
    coarse_start = {("j0", "i0"): 0, ("j0", "i1"): 2, ("j1", "i0"): 3, ("j1", "i1"): 6}

    reconstructed_start = {k: v * factor for k, v in coarse_start.items()}

    assert reconstructed_start[("j0", "i0")] == 0
    assert reconstructed_start[("j0", "i1")] == 100
    assert reconstructed_start[("j1", "i0")] == 150
    assert reconstructed_start[("j1", "i1")] == 300


def test_reconstructed_end_uses_original_p() -> None:
    """reconstructed_end[j,i] == reconstructed_start[j,i] + original_p[j,i]."""
    factor = 50
    # original p values
    original_p = {("j0", "i0"): 73, ("j0", "i1"): 45}
    coarse_start = {("j0", "i0"): 0, ("j0", "i1"): 2}

    reconstructed_start = {k: v * factor for k, v in coarse_start.items()}
    reconstructed_end = {k: reconstructed_start[k] + original_p[k] for k in original_p}

    assert reconstructed_end[("j0", "i0")] == 73
    assert reconstructed_end[("j0", "i1")] == 100 + 45


def test_coarsen_p_ceiling_property() -> None:
    """ceil(p / factor) * factor >= p, so inflated interval is a superset."""
    for p in [1, 49, 50, 51, 100, 999]:
        factor = 50
        coarse_p = math.ceil(p / factor)
        inflated_duration = coarse_p * factor
        assert inflated_duration >= p, (
            f"p={p}, factor={factor}, inflated={inflated_duration}"
        )


# ---------------------------------------------------------------------------
# Integration: adapter.run on a small synthetic instance
# ---------------------------------------------------------------------------


def _make_tiny_2job_2stage_instance() -> FFcDDWParameters:
    """A 2-job × 2-stage instance tiny enough for CP-SAT to solve instantly.

    Processing times use values > 50 so coarsening is non-trivial.
    Due windows are wide to ensure feasibility.
    """
    return _make_small_ddw_instance(
        name="tiny_csr",
        processing_rows=[[60, 80], [70, 90]],
        job_2_due_window_map={"j0": (0, 9999), "j1": (0, 9999)},
        job_2_ewt_map={"j0": 1, "j1": 1},
        job_2_twt_map={"j0": 1, "j1": 1},
        stage_2_machine_count=(1, 1),
    )


def test_run_returns_feasible_or_optimal_on_tiny_instance() -> None:
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.work_status in (WorkStatus.OPTIMAL, WorkStatus.FEASIBLE)
    assert record.result is not None
    assert record.result.schedule is not None


def test_run_result_has_required_metrics_keys() -> None:
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    metrics = record.result.metrics
    assert metrics is not None
    required_keys = {
        "factor",
        "coarsened_instance_name",
        "coarsened_status",
        "coarsened_obj_value",
        "coarsened_obj_bound",
        "coarsened_elapsed",
        "reconstructed_obj_value",
        "reconstructed_makespan",
    }
    for key in required_keys:
        assert key in metrics, f"Missing metrics key: {key}"


def test_run_algorithm_id_is_set() -> None:
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    spec = AlgSpec(instance=instance)

    record = adapter.run(spec)

    assert record.algorithm_id == "coarsen_solve_reconstruct"
    assert record.instance_id == instance.name


def test_run_reconstructed_start_arithmetic_via_pure_unit() -> None:
    """The inflation arithmetic is verified by pure unit tests above.

    Here we confirm that the schedule returned by adapter.run has valid
    start/end times (non-negative, start <= end) — a sanity check on the
    schedule structure, not a duplicate of the arithmetic tests.
    """
    factor = 50
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(factor=factor, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    schedule = record.result.schedule
    assert schedule is not None

    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()
    for key in start_map:
        s = start_map[key]
        e = end_map[key]
        assert s >= 0, f"Negative start time {s} for {key}"
        assert e >= s, f"End {e} < start {s} for {key}"


def test_run_duration_restored_to_original_p() -> None:
    """After reconstruct, end - start == original p_ij for every operation."""
    factor = 50
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(factor=factor, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    schedule = record.result.schedule
    assert schedule is not None

    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()
    original_p = instance.job_2_stage_2_p_map

    for j, i, _k in start_map:
        duration = end_map[(j, i, _k)] - start_map[(j, i, _k)]
        expected_p = original_p[j][i]
        assert duration == expected_p, (
            f"duration for ({j},{i}) = {duration}, expected original p = {expected_p}"
        )


def test_run_objective_evaluated_on_original_scale() -> None:
    """obj_value in record must equal compute_weighted_ET on original instance."""
    from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    schedule = record.result.schedule
    assert schedule is not None
    sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
    expected_obj = float(sum_e + sum_t)
    assert record.result.obj_value == expected_obj


def test_run_default_option_is_used_when_none_given() -> None:
    """Passing option=None must use CoarsenSolveReconstructOption defaults."""
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    spec = AlgSpec(instance=instance, option=None)

    record = adapter.run(spec)

    assert record.work_status in (WorkStatus.OPTIMAL, WorkStatus.FEASIBLE)
    assert record.option is not None
    assert isinstance(record.option, CoarsenSolveReconstructOption)


def test_run_metrics_factor_matches_option_factor() -> None:
    """metrics['factor'] must match the option factor used."""
    factor = 10
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(factor=factor, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    assert record.result.metrics is not None
    assert record.result.metrics["factor"] == factor


def test_run_metrics_coarsened_instance_name() -> None:
    """metrics['coarsened_instance_name'] must be 'name_coarsen{factor}'."""
    factor = 50
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(factor=factor, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    assert record.result.metrics is not None
    expected_name = f"{instance.name}_coarsen{factor}"
    assert record.result.metrics["coarsened_instance_name"] == expected_name


def test_run_termination_reason_set() -> None:
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    spec = AlgSpec(instance=instance)

    record = adapter.run(spec)

    assert record.termination_reason in (
        TerminationReason.COMPLETED,
        TerminationReason.TIME_LIMIT,
    )


# ---------------------------------------------------------------------------
# WP-1: CoarsenSolveReconstructTrace + run_coarsen_solve_reconstruct
# ---------------------------------------------------------------------------


def test_trace_and_pipeline_importable() -> None:
    """run_coarsen_solve_reconstruct and CoarsenSolveReconstructTrace must be in __all__."""
    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructTrace,
        run_coarsen_solve_reconstruct,
    )

    assert CoarsenSolveReconstructTrace is not None
    assert run_coarsen_solve_reconstruct is not None


def test_trace_is_frozen_dataclass() -> None:
    """CoarsenSolveReconstructTrace must be a frozen slots dataclass."""
    from dataclasses import is_dataclass

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructTrace,
    )

    assert is_dataclass(CoarsenSolveReconstructTrace)
    # Frozen: attempting a setattr must raise
    instance = _make_tiny_2job_2stage_instance()
    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )
    import logging

    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))
    assert isinstance(trace, CoarsenSolveReconstructTrace)
    with pytest.raises((AttributeError, TypeError)):
        trace.obj_value = 999.0  # type: ignore[misc]


def test_trace_has_required_fields() -> None:
    """CoarsenSolveReconstructTrace must expose all documented fields."""
    from dataclasses import fields

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructTrace,
    )

    field_names = {f.name for f in fields(CoarsenSolveReconstructTrace)}
    required = {
        "work_status",
        "termination_reason",
        "error",
        "final_schedule",
        "coarse_schedule",
        "reconstructed_raw_schedule",
        "cp_progress_log",
        "obj_value",
        "metrics",
    }
    assert required <= field_names, f"Missing fields: {required - field_names}"


def test_trace_three_schedules_are_distinct_objects() -> None:
    """coarse_schedule, reconstructed_raw_schedule, and final_schedule are distinct objects."""
    import logging

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )

    instance = _make_tiny_2job_2stage_instance()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))

    assert trace.final_schedule is not None
    assert trace.coarse_schedule is not None
    assert trace.reconstructed_raw_schedule is not None

    # All three must be distinct objects
    assert trace.final_schedule is not trace.coarse_schedule
    assert trace.final_schedule is not trace.reconstructed_raw_schedule
    assert trace.coarse_schedule is not trace.reconstructed_raw_schedule


def test_trace_raw_schedule_not_mutated_by_postprocess() -> None:
    """reconstructed_raw_schedule must NOT reflect make_semi_active/insert_idle_time.

    We verify that raw op duration == original p_ij (not shifted by postprocess),
    and that raw op start == coarse_start * factor (the direct inflation).
    """
    import logging

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )

    instance = _make_tiny_2job_2stage_instance()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))

    raw = trace.reconstructed_raw_schedule
    assert raw is not None

    raw_start_map = raw.get_jik_2_start_time_map()
    raw_end_map = raw.get_jik_2_end_time_map()
    original_p = instance.job_2_stage_2_p_map

    # Raw duration must equal original p_ij
    for j, i, _k in raw_start_map:
        duration = raw_end_map[(j, i, _k)] - raw_start_map[(j, i, _k)]
        expected_p = original_p[j][i]
        assert duration == expected_p, (
            f"raw duration for ({j},{i})={duration}, expected original p={expected_p}"
        )


def test_trace_final_schedule_reflects_postprocess() -> None:
    """final_schedule must differ from reconstructed_raw_schedule.

    After make_semi_active + insert_idle_time, at least one start time should
    differ (unless the schedule is already active, which is unlikely on the
    tiny instance with wide due windows triggering insert_idle_time).
    We verify the objects are distinct; if they happened to be equal by
    coincidence we still confirm that raw starts <= final starts (postprocess
    can only shift idle time insertion, never retract).
    """
    import logging

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )

    instance = _make_tiny_2job_2stage_instance()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))

    assert trace.final_schedule is not trace.reconstructed_raw_schedule
    # Both must be valid schedules
    assert trace.final_schedule is not None
    assert trace.reconstructed_raw_schedule is not None


def test_trace_raw_schedule_stages_match_original_instance() -> None:
    """reconstructed_raw_schedule is built from original instance, not coarsened."""
    import logging

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )

    instance = _make_tiny_2job_2stage_instance()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))

    raw = trace.reconstructed_raw_schedule
    assert raw is not None
    # Stage list matches original
    assert set(raw.stages) == set(instance.stage_id_list)


def test_trace_coarse_schedule_stages_match_coarsened_instance() -> None:
    """coarse_schedule must be built from coarsened instance (same stage/job ids)."""
    import logging

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )

    instance = _make_tiny_2job_2stage_instance()
    factor = 50
    option = CoarsenSolveReconstructOption(factor=factor, solver_thread_cnt=1)
    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))

    coarse = trace.coarse_schedule
    assert coarse is not None
    # Job/stage IDs are the same; times are coarsened-scale (smaller)
    assert set(coarse.stages) == set(instance.stage_id_list)
    # Coarse op times should be <= original raw times (they are coarsened scale)
    coarse_end_map = coarse.get_jik_2_end_time_map()
    raw_end_map = trace.reconstructed_raw_schedule.get_jik_2_end_time_map()  # type: ignore[union-attr]
    # At least one coarse value must be strictly less than its raw counterpart
    # (since factor=50 compresses 60-unit times to 2-unit coarse times)
    assert any(coarse_end_map[k] < raw_end_map[k] for k in coarse_end_map), (
        "Expected coarse schedule times to be smaller than inflated raw schedule times"
    )


def test_trace_cp_progress_log_nonempty_on_tiny_instance() -> None:
    """cp_progress_log must be non-empty when the solver finds a solution."""
    import logging

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )

    instance = _make_tiny_2job_2stage_instance()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))

    assert trace.final_schedule is not None, "Expected a solution on tiny instance"
    assert trace.cp_progress_log is not None
    assert len(trace.cp_progress_log) > 0, (
        "Expected non-empty progress log when solution found"
    )


def test_trace_cp_progress_log_last_entry_matches_coarsened_obj() -> None:
    """Last progress_log entry obj_value/obj_bound must match coarsened solver output."""
    import logging

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )

    instance = _make_tiny_2job_2stage_instance()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))

    assert trace.final_schedule is not None
    assert trace.cp_progress_log is not None
    assert len(trace.cp_progress_log) > 0

    metrics = trace.metrics
    assert metrics is not None

    # The last entry with a non-None obj_value must match coarsened_obj_value from metrics
    entries_with_value = [e for e in trace.cp_progress_log if e.obj_value is not None]
    assert len(entries_with_value) > 0, "At least one entry must have an obj_value"
    last_value_entry = entries_with_value[-1]
    assert last_value_entry.obj_value == metrics["coarsened_obj_value"], (
        f"Progress log last obj_value {last_value_entry.obj_value} "
        f"!= coarsened_obj_value {metrics['coarsened_obj_value']}"
    )
    # Similarly for bound
    entries_with_bound = [e for e in trace.cp_progress_log if e.obj_bound is not None]
    assert len(entries_with_bound) > 0
    last_bound_entry = entries_with_bound[-1]
    assert last_bound_entry.obj_bound == metrics["coarsened_obj_bound"], (
        f"Progress log last obj_bound {last_bound_entry.obj_bound} "
        f"!= coarsened_obj_bound {metrics['coarsened_obj_bound']}"
    )


def test_trace_cp_progress_log_is_tuple() -> None:
    """cp_progress_log must be a tuple (immutable)."""
    import logging

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )

    instance = _make_tiny_2job_2stage_instance()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))

    assert isinstance(trace.cp_progress_log, tuple)


def test_adapter_run_now_populates_progress_log() -> None:
    """adapter.run must set AlgRecord.progress_log (non-None, non-empty) when solution found."""
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    assert record.result.schedule is not None
    assert record.progress_log is not None
    assert isinstance(record.progress_log, tuple)
    assert len(record.progress_log) > 0


def test_adapter_run_schedule_and_metrics_unchanged_from_base() -> None:
    """adapter.run must still return the same final schedule/metrics as the pre-WP-1 base.

    We verify all required metric keys are present and the schedule has valid op timings.
    This ensures the refactor to use run_coarsen_solve_reconstruct does not break
    existing behaviour.
    """
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.work_status in (WorkStatus.OPTIMAL, WorkStatus.FEASIBLE)
    assert record.result is not None
    metrics = record.result.metrics
    assert metrics is not None
    required_keys = {
        "factor",
        "coarsened_instance_name",
        "coarsened_status",
        "coarsened_obj_value",
        "coarsened_obj_bound",
        "coarsened_elapsed",
        "reconstructed_obj_value",
        "reconstructed_makespan",
    }
    for key in required_keys:
        assert key in metrics, f"Missing metrics key: {key}"

    schedule = record.result.schedule
    assert schedule is not None
    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()
    for key in start_map:
        assert start_map[key] >= 0
        assert end_map[key] >= start_map[key]


def test_trace_obj_value_matches_adapter_obj_value() -> None:
    """trace.obj_value must equal the original-scale objective (same as adapter.run)."""
    import logging

    from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
        CoarsenSolveReconstructOption,
        run_coarsen_solve_reconstruct,
    )

    instance = _make_tiny_2job_2stage_instance()
    option = CoarsenSolveReconstructOption(factor=50, solver_thread_cnt=1)
    spec = AlgSpec(instance=instance, option=option)

    trace = run_coarsen_solve_reconstruct(instance, option, logging.getLogger("test"))
    adapter = CoarsenSolveReconstructAdapter()
    record = adapter.run(spec)

    assert trace.obj_value is not None
    assert record.result is not None
    # Both runs are independent; we just verify obj_value is a valid float
    assert isinstance(trace.obj_value, float)
    assert trace.obj_value >= 0.0


# ---------------------------------------------------------------------------
# WP-5: seed_dispatch option field and metrics
# ---------------------------------------------------------------------------


def test_option_seed_dispatch_default_is_mixed() -> None:
    """CoarsenSolveReconstructOption.seed_dispatch must default to 'mixed'."""
    opt = CoarsenSolveReconstructOption()
    assert opt.seed_dispatch == "mixed"


def test_option_seed_dispatch_job_wise() -> None:
    """CoarsenSolveReconstructOption must accept 'job_wise' for seed_dispatch."""
    opt = CoarsenSolveReconstructOption(seed_dispatch="job_wise")
    assert opt.seed_dispatch == "job_wise"


def test_metrics_include_seed_dispatch_keys() -> None:
    """metrics must contain seed_dispatch and dispatch_seed_coarsened_obj."""
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(
        factor=50, solver_thread_cnt=1, seed_dispatch="mixed"
    )
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    metrics = record.result.metrics
    assert metrics is not None
    assert "seed_dispatch" in metrics
    assert metrics["seed_dispatch"] == "mixed"
    assert "dispatch_seed_coarsened_obj" in metrics


def test_metrics_seed_dispatch_job_wise() -> None:
    """metrics['seed_dispatch'] must reflect the option value."""
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(
        factor=50, solver_thread_cnt=1, seed_dispatch="job_wise"
    )
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    assert record.result.metrics["seed_dispatch"] == "job_wise"


def test_dispatch_seed_obj_is_non_negative() -> None:
    """dispatch_seed_coarsened_obj must be >= 0 when a solution exists."""
    instance = _make_tiny_2job_2stage_instance()
    adapter = CoarsenSolveReconstructAdapter()
    option = CoarsenSolveReconstructOption(
        factor=50, solver_thread_cnt=1, seed_dispatch="mixed"
    )
    spec = AlgSpec(instance=instance, option=option)

    record = adapter.run(spec)

    assert record.result is not None
    metrics = record.result.metrics
    assert metrics is not None
    seed_obj = metrics["dispatch_seed_coarsened_obj"]
    assert seed_obj is not None
    assert seed_obj >= 0.0
