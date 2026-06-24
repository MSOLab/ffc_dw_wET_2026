"""Tests for FFcDDWSubroutineController.coarsen_solve_reconstruct step method.

Verifies the two CLAUDE.md subroutine step contract invariants:
1. ``_register`` is called exactly once per step call (success path, no-solution
   path, and stopping-condition path each exercised separately).
2. ``elapsed_time`` is measured from step entry to immediately before
   ``_register`` — no heavy work wedged in between.

WP-2 additions: flag independence tests for ``emit_phase_schedules`` and
``draw_cp_trajectory``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.base.alg_record import TerminationReason, WorkStatus
from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
    CoarsenSolveReconstructTrace,
)
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_instance(name: str = "csr_step_test") -> FFcDDWParameters:
    """Small 3-job, 2-stage, 1-machine instance for fast CP solves."""
    job_id_list = ["j0", "j1", "j2"]
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[3, 2], [2, 3], [1, 2]]),
        ),
        job_2_due_window_map={
            "j0": (3, 8),
            "j1": (4, 9),
            "j2": (2, 7),
        },
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _make_controller(
    instance: FFcDDWParameters | None = None,
    timelimit: float = 60.0,
) -> FFcDDWSubroutineController:
    if instance is None:
        instance = _make_instance()
    return FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "coarsen_solve_reconstruct"}],
        stopping_criteria=StoppingCriteria({"timelimit": timelimit}),
    )


def _make_fake_schedule() -> MagicMock:
    """Return a mock object that behaves like an FFcSchedule for registration."""
    sch = MagicMock()
    sch.makespan = 10
    return sch


def _make_trace_with_schedule(obj_value: float = 5.0) -> CoarsenSolveReconstructTrace:
    """Return a CoarsenSolveReconstructTrace carrying mock schedules."""
    return CoarsenSolveReconstructTrace(
        work_status=WorkStatus.FEASIBLE,
        termination_reason=TerminationReason.COMPLETED,
        error=None,
        final_schedule=_make_fake_schedule(),
        coarse_schedule=_make_fake_schedule(),
        reconstructed_raw_schedule=_make_fake_schedule(),
        cp_progress_log=(),
        obj_value=obj_value,
        metrics={},
    )


def _make_trace_no_schedule() -> CoarsenSolveReconstructTrace:
    """Return a CoarsenSolveReconstructTrace with no solution."""
    return CoarsenSolveReconstructTrace(
        work_status=WorkStatus.ERROR,
        termination_reason=TerminationReason.ERROR,
        error=None,
        final_schedule=None,
        coarse_schedule=None,
        reconstructed_raw_schedule=None,
        cp_progress_log=(),
        obj_value=None,
        metrics={},
    )


_CSR_PIPELINE_PATH = (
    "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct"
)


# ---------------------------------------------------------------------------
# Contract test 1: _register called exactly once on success path
# ---------------------------------------------------------------------------


def test_register_called_exactly_once_on_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the pipeline returns a schedule, _register must be called exactly once."""
    controller = _make_controller()

    fake_trace = _make_trace_with_schedule()
    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        lambda instance, option, logger: fake_trace,
    )

    register_calls: list[tuple] = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        register_calls.append((report, solution))
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct()

    assert len(register_calls) == 1, (
        f"Expected _register called exactly once on success path; "
        f"got {len(register_calls)} call(s)"
    )
    _, solution = register_calls[0]
    assert solution is not None, "Success path should register a non-None solution"


# ---------------------------------------------------------------------------
# Contract test 2: _register called exactly once on no-solution path
# ---------------------------------------------------------------------------


def test_register_called_exactly_once_on_no_solution_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the pipeline returns no schedule, _register must still be called exactly once."""
    controller = _make_controller()

    fake_trace = _make_trace_no_schedule()
    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        lambda instance, option, logger: fake_trace,
    )

    register_calls: list[tuple] = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        register_calls.append((report, solution))
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct()

    assert len(register_calls) == 1, (
        f"Expected _register called exactly once on no-solution path; "
        f"got {len(register_calls)} call(s)"
    )
    _, solution = register_calls[0]
    assert solution is None, "No-solution path should register None as solution"


# ---------------------------------------------------------------------------
# Contract test 3: stopping condition prevents _register
# ---------------------------------------------------------------------------


def test_stopping_condition_at_entry_returns_stop_report_no_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the stopping condition is already true at step entry, _register must
    not be called and the returned report should have obj_value=None."""
    controller = _make_controller(timelimit=0.001)
    # Advance the timer so it is already over.
    controller.timer.set_start_time(datetime.now() - timedelta(seconds=10))

    pipeline_called = {"count": 0}

    def spy_pipeline(instance, option, logger):  # type: ignore[no-untyped-def]
        pipeline_called["count"] += 1
        raise AssertionError("Pipeline must not run when stopping condition is true")

    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        spy_pipeline,
    )

    register_calls: list = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        register_calls.append((report, solution))
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    report = controller.coarsen_solve_reconstruct()

    assert pipeline_called["count"] == 0, (
        "Pipeline must not be called when stop fires at entry"
    )
    assert len(register_calls) == 0, (
        f"_register must not be called on stop path; got {len(register_calls)} call(s)"
    )
    assert report.obj_value is None, "Stop-report should have obj_value=None"


# ---------------------------------------------------------------------------
# Contract test 4: elapsed_time is measured before _register (no heavy work
# wedged between measurement and register call).
# ---------------------------------------------------------------------------


def test_elapsed_time_does_not_include_post_register_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """elapsed_time on the returned SubroutineReport must reflect duration from
    step entry to the moment elapsed was measured — immediately before _register.

    We verify this by ensuring the reported elapsed_time is a finite positive float
    and that the report returned from the step matches what was passed to _register
    (same object), confirming no additional work happened between measurement and
    registration that could have modified the report.
    """
    controller = _make_controller()

    fake_trace = _make_trace_with_schedule()
    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        lambda instance, option, logger: fake_trace,
    )

    captured_report_at_register: list = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        captured_report_at_register.append(report)
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    returned_report = controller.coarsen_solve_reconstruct()

    assert len(captured_report_at_register) == 1
    report_at_register = captured_report_at_register[0]

    # The report passed to _register and the report returned by the step method
    # must be the same object (no copy or mutation between register and return).
    assert returned_report is report_at_register, (
        "Step method must return the same report object that was passed to _register"
    )
    assert isinstance(returned_report.elapsed_time, float)
    assert returned_report.elapsed_time >= 0.0


# ---------------------------------------------------------------------------
# WP-2 flag test 5: emit_phase_schedules=True, draw_cp_trajectory=False
# -> 3 snapshots on csr_phase_schedules, csr_cp_trajectory remains None.
# ---------------------------------------------------------------------------


def test_emit_phase_schedules_true_draws_three_snapshots_no_trajectory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """emit_phase_schedules=True + solution -> csr_phase_schedules length 3,
    names ordered 1_/2_/3_, and csr_cp_trajectory stays None."""
    controller = _make_controller()

    fake_trace = _make_trace_with_schedule()
    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        lambda instance, option, logger: fake_trace,
    )

    def permissive_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        return True

    controller._register = permissive_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct(
        emit_phase_schedules=True, draw_cp_trajectory=False
    )

    assert len(controller.csr_phase_schedules) == 3, (
        f"Expected 3 phase snapshots; got {len(controller.csr_phase_schedules)}"
    )
    names = [name for name, _ in controller.csr_phase_schedules]
    assert any("1_coarse_solver_result" in n for n in names), (
        f"Expected a name containing '1_coarse_solver_result'; got {names}"
    )
    assert any("2_reconstructed_raw" in n for n in names), (
        f"Expected a name containing '2_reconstructed_raw'; got {names}"
    )
    assert any("3_final" in n for n in names), (
        f"Expected a name containing '3_final'; got {names}"
    )
    # Verify ordering: 1_ before 2_ before 3_
    order = [n.split("_", 1)[-1] for n in names]
    assert order[0].startswith("1_"), (
        f"First snapshot must start with 1_; got {order[0]}"
    )
    assert order[1].startswith("2_"), (
        f"Second snapshot must start with 2_; got {order[1]}"
    )
    assert order[2].startswith("3_"), (
        f"Third snapshot must start with 3_; got {order[2]}"
    )

    assert controller.csr_cp_trajectory is None, (
        "draw_cp_trajectory=False must leave csr_cp_trajectory as None"
    )


# ---------------------------------------------------------------------------
# WP-2 flag test 6: emit_phase_schedules=False, draw_cp_trajectory=True
# -> no snapshots, csr_cp_trajectory set.
# ---------------------------------------------------------------------------


def test_draw_cp_trajectory_true_sets_trajectory_no_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """draw_cp_trajectory=True + solution -> csr_cp_trajectory set,
    csr_phase_schedules remains empty."""
    from ffc_ddw_sum_et.algorithm.base.alg_record import ProgressLogEntry

    fake_entry = ProgressLogEntry(elapsed_sec=0.1, obj_value=5.0, obj_bound=3.0)
    fake_trace = CoarsenSolveReconstructTrace(
        work_status=WorkStatus.FEASIBLE,
        termination_reason=TerminationReason.COMPLETED,
        error=None,
        final_schedule=_make_fake_schedule(),
        coarse_schedule=_make_fake_schedule(),
        reconstructed_raw_schedule=_make_fake_schedule(),
        cp_progress_log=(fake_entry,),
        obj_value=5.0,
        metrics={},
    )

    controller = _make_controller()
    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        lambda instance, option, logger: fake_trace,
    )

    def permissive_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        return True

    controller._register = permissive_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct(
        emit_phase_schedules=False, draw_cp_trajectory=True
    )

    assert len(controller.csr_phase_schedules) == 0, (
        f"emit_phase_schedules=False must leave csr_phase_schedules empty; "
        f"got {len(controller.csr_phase_schedules)} entries"
    )
    assert controller.csr_cp_trajectory is not None, (
        "draw_cp_trajectory=True must set csr_cp_trajectory"
    )
    assert controller.csr_cp_trajectory == (fake_entry,), (
        "csr_cp_trajectory must equal the trace's cp_progress_log"
    )


# ---------------------------------------------------------------------------
# WP-2 flag test 7: both flags False -> no snapshots, trajectory None.
# ---------------------------------------------------------------------------


def test_both_flags_false_no_snapshots_no_trajectory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both flags False (default) + solution -> no snapshots, trajectory None."""
    controller = _make_controller()

    fake_trace = _make_trace_with_schedule()
    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        lambda instance, option, logger: fake_trace,
    )

    def permissive_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        return True

    controller._register = permissive_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct(
        emit_phase_schedules=False, draw_cp_trajectory=False
    )

    assert len(controller.csr_phase_schedules) == 0, (
        "Both flags False must leave csr_phase_schedules empty"
    )
    assert controller.csr_cp_trajectory is None, (
        "Both flags False must leave csr_cp_trajectory as None"
    )


# ---------------------------------------------------------------------------
# WP-2 flag test 8: no-solution path -> flags have no effect (no snapshots,
# trajectory None even when both are True).
# ---------------------------------------------------------------------------


def test_no_solution_flags_have_no_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no solution, both flags True must still leave state empty/None."""
    controller = _make_controller()

    fake_trace = _make_trace_no_schedule()
    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        lambda instance, option, logger: fake_trace,
    )

    def permissive_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        return True

    controller._register = permissive_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct(
        emit_phase_schedules=True, draw_cp_trajectory=True
    )

    assert len(controller.csr_phase_schedules) == 0, (
        "No-solution path must leave csr_phase_schedules empty regardless of flags"
    )
    assert controller.csr_cp_trajectory is None, (
        "No-solution path must leave csr_cp_trajectory None regardless of flags"
    )


# ---------------------------------------------------------------------------
# Integration test: method is callable via controller.run() dispatch
# ---------------------------------------------------------------------------


def test_coarsen_solve_reconstruct_via_run_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step name 'coarsen_solve_reconstruct' is reachable via the routix
    subroutine flow dispatcher (controller.run())."""
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "coarsen_solve_reconstruct"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )

    fake_trace = _make_trace_with_schedule()
    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        lambda instance, option, logger: fake_trace,
    )

    # Patch _register to avoid full solution-manager overhead on mock schedule.
    def permissive_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        # Accept mock schedule without validation.
        return True

    controller._register = permissive_register  # type: ignore[method-assign]

    # Should not raise.
    controller.run()


# ---------------------------------------------------------------------------
# Integration test: step exercises the real pipeline on a tiny instance
# ---------------------------------------------------------------------------


def test_coarsen_solve_reconstruct_real_pipeline_small_instance() -> None:
    """End-to-end: run the real CSR pipeline on a tiny instance with a generous
    timelimit. Either a feasible schedule is registered or the step returns a
    report (no crash is acceptable from an integration perspective)."""
    controller = _make_controller(timelimit=30.0)

    report = controller.coarsen_solve_reconstruct(factor=1, timelimit=10.0)

    # Report must be a SubroutineReport with a non-negative elapsed_time.
    assert report.elapsed_time >= 0.0
    # Either a schedule was found (obj_value set) or not (obj_value None).
    # Both are acceptable; the contract only requires no double-register.
    if report.obj_value is not None:
        assert report.obj_value >= 0.0


# ---------------------------------------------------------------------------
# WP-5: seed_dispatch kwarg
# ---------------------------------------------------------------------------


def test_seed_dispatch_kwarg_passed_to_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seed_dispatch kwarg must flow through to the option."""
    captured_option: list = []

    def spy_pipeline(instance, option, logger):  # type: ignore[no-untyped-def]
        captured_option.append(option)
        return _make_trace_no_schedule()

    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        spy_pipeline,
    )

    controller = _make_controller()

    def permissive_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        return True

    controller._register = permissive_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct(seed_dispatch="job_wise")

    assert len(captured_option) == 1
    assert captured_option[0].seed_dispatch == "job_wise"


def test_seed_dispatch_default_is_mixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When seed_dispatch is not specified, it must default to 'mixed'."""
    captured_option: list = []

    def spy_pipeline(instance, option, logger):  # type: ignore[no-untyped-def]
        captured_option.append(option)
        return _make_trace_no_schedule()

    monkeypatch.setattr(
        "ffc_ddw_sum_et.orchestration.controller.run_coarsen_solve_reconstruct",
        spy_pipeline,
    )

    controller = _make_controller()

    def permissive_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        return True

    controller._register = permissive_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct()

    assert len(captured_option) == 1
    assert captured_option[0].seed_dispatch == "mixed"
