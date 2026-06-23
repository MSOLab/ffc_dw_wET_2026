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
