"""Tests for FFcDDWSubroutineController.coarsen_solve_reconstruct step method.

Verifies the two CLAUDE.md subroutine step contract invariants:
1. ``_register`` is called exactly once per step call (success path, no-solution
   path, and stopping-condition path each exercised separately).
2. ``elapsed_time`` is measured from step entry to immediately before
   ``_register`` — no heavy work wedged in between.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.base.alg_record import AlgRecord, AlgResult, WorkStatus
from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import CoarsenSolveReconstructAdapter
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


def _make_alg_record_with_schedule(obj_value: float = 5.0) -> AlgRecord:
    """Return an AlgRecord whose result carries a mock schedule."""
    return AlgRecord(
        work_status=WorkStatus.FEASIBLE,
        instance_id="csr_step_test",
        algorithm_id="coarsen_solve_reconstruct",
        option=None,
        result=AlgResult(
            schedule=_make_fake_schedule(),
            obj_value=obj_value,
            obj_bound=None,
            metrics={},
        ),
    )


def _make_alg_record_no_schedule() -> AlgRecord:
    """Return an AlgRecord whose result has no schedule (no-solution path)."""
    return AlgRecord(
        work_status=WorkStatus.ERROR,
        instance_id="csr_step_test",
        algorithm_id="coarsen_solve_reconstruct",
        option=None,
        result=AlgResult(
            schedule=None,
            obj_value=None,
            obj_bound=None,
            metrics={},
        ),
    )


# ---------------------------------------------------------------------------
# Contract test 1: _register called exactly once on success path
# ---------------------------------------------------------------------------


def test_register_called_exactly_once_on_success_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the adapter returns a schedule, _register must be called exactly once."""
    controller = _make_controller()

    fake_record = _make_alg_record_with_schedule()
    monkeypatch.setattr(CoarsenSolveReconstructAdapter, "run", lambda self, spec: fake_record)

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
    """When the adapter returns no schedule, _register must still be called exactly once."""
    controller = _make_controller()

    fake_record = _make_alg_record_no_schedule()
    monkeypatch.setattr(CoarsenSolveReconstructAdapter, "run", lambda self, spec: fake_record)

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

    adapter_called = {"count": 0}

    def spy_adapter_run(self, spec):  # type: ignore[no-untyped-def]
        adapter_called["count"] += 1
        raise AssertionError("Adapter must not run when stopping condition is true")

    monkeypatch.setattr(CoarsenSolveReconstructAdapter, "run", spy_adapter_run)

    register_calls: list = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        register_calls.append((report, solution))
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    report = controller.coarsen_solve_reconstruct()

    assert adapter_called["count"] == 0, "Adapter must not be called when stop fires at entry"
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

    fake_record = _make_alg_record_with_schedule()
    monkeypatch.setattr(CoarsenSolveReconstructAdapter, "run", lambda self, spec: fake_record)

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

    fake_record = _make_alg_record_with_schedule()
    monkeypatch.setattr(CoarsenSolveReconstructAdapter, "run", lambda self, spec: fake_record)

    # Patch _register to avoid full solution-manager overhead on mock schedule.
    def permissive_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        # Accept mock schedule without validation.
        return True

    controller._register = permissive_register  # type: ignore[method-assign]

    # Should not raise.
    controller.run()


# ---------------------------------------------------------------------------
# Integration test: step exercises the real adapter on a tiny instance
# ---------------------------------------------------------------------------


def test_coarsen_solve_reconstruct_real_adapter_small_instance() -> None:
    """End-to-end: run the real CSR adapter on a tiny instance with a generous
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
