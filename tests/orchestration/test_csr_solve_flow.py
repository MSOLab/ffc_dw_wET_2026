"""Tests for the ``coarsen_solve_reconstruct`` ``solve_flow`` mode (W5).

Covers plan §6 validation:
  (a) child produces >= 2 candidates across the 5-step flow,
  (b) structural dedup collapses identical schedules,
  (c) every surviving reconstructed candidate is feasible on the original
      instance,
  (d) the registered incumbent equals the original-scale argmin over
      reconstructed candidates,
  (e) the parent record carries ``obj_bound`` None,
  (f) the legacy no-solve_flow path is unchanged (matches the pure pipeline).

Plus unit tests for the structural-signature / dedup pure helpers.

Tiny synthetic instances + tiny time limits keep the whole file well under 60s.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
    CoarsenSolveReconstructOption,
    CsrCandidate,
    dedup_candidates,
    run_coarsen_solve_reconstruct,
    schedule_sequence_signature,
)
from ffc_ddw_sum_et.orchestration.artifact_layout import FFcArtifactLayout
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import (
    FFcDDWSingleInstanceRunner,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_instance(name: str = "csr_flow_test") -> FFcDDWParameters:
    """4-job, 2-stage instance; stage i1 has 2 machines (non-trivial layout)."""
    return FFcDDWParameters(
        name=name,
        job_id_list=["j0", "j1", "j2", "j3"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0", "i1_1"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[6, 4], [4, 6], [2, 5], [5, 3]]),
        ),
        job_2_due_window_map={
            "j0": (6, 16),
            "j1": (8, 18),
            "j2": (4, 14),
            "j3": (10, 20),
        },
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1, "j3": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1, "j3": 1},
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


_FULL_FIVE_STEP_FLOW = [
    {"method": "calc_mcf_lb_and_derive_full_sch"},
    {
        "method": "run_flip_makespan_cp_from_incumbent",
        "cp_tl": 1.0,
        "solver_thread_cnt": 1,
    },
    {
        "method": "neh_cp",
        "job_priority": "weight-due-pos",
        "cp_tl": 1.0,
        "solver_thread_cnt": 1,
    },
    {
        "method": "incremental_sw_cp",
        "cp_tl": 1.0,
        "solver_thread_cnt": 1,
        "unfixed_batch_count_min": 1,
        "unfixed_batch_count_max": 2,
    },
    {"method": "solve_base_model_cpsat", "timelimit": 1.0, "solver_thread_cnt": 1},
]


# ---------------------------------------------------------------------------
# Unit tests: schedule_sequence_signature + dedup_candidates
# ---------------------------------------------------------------------------


def _tiny_schedule(order: list[str]) -> FFcSchedule:
    """Single-stage single-machine schedule placing jobs back-to-back in *order*."""
    sch = FFcSchedule(
        jobs=["a", "b", "c"], stages=["s0"], machines_per_stage={"s0": ["m0"]}
    )
    t = 0
    for job in order:
        sch.add_ops_times_2_mc("s0", "m0", job, t, t + 1)
        t += 1
    return sch


def test_signature_identical_schedules_match() -> None:
    sig1 = schedule_sequence_signature(_tiny_schedule(["a", "b", "c"]))
    sig2 = schedule_sequence_signature(_tiny_schedule(["a", "b", "c"]))
    assert sig1 == sig2


def test_signature_differs_on_reorder() -> None:
    sig1 = schedule_sequence_signature(_tiny_schedule(["a", "b", "c"]))
    sig2 = schedule_sequence_signature(_tiny_schedule(["c", "b", "a"]))
    assert sig1 != sig2


def test_dedup_collapses_identical_and_earlier_wins() -> None:
    c_first = CsrCandidate(
        source="first",
        coarse_schedule=_tiny_schedule(["a", "b", "c"]),
        coarse_obj=5.0,
        coarse_bound=None,
    )
    c_dup = CsrCandidate(
        source="second",
        coarse_schedule=_tiny_schedule(["a", "b", "c"]),
        coarse_obj=5.0,
        coarse_bound=None,
    )
    c_other = CsrCandidate(
        source="third",
        coarse_schedule=_tiny_schedule(["c", "b", "a"]),
        coarse_obj=7.0,
        coarse_bound=None,
    )
    deduped = dedup_candidates([c_first, c_dup, c_other])
    assert len(deduped) == 2
    # Earlier candidate wins for the collapsed signature.
    assert deduped[0].source == "first"
    assert deduped[1].source == "third"


# ---------------------------------------------------------------------------
# Integration: full 5-step solve_flow (plan §6 a-e in one run)
# ---------------------------------------------------------------------------


def test_solve_flow_full_five_steps() -> None:
    """One test exercises all five step methods in a single solve_flow and
    asserts plan §6 (a)-(e) plus original-scale feasibility of the winner."""
    controller = _make_controller(timelimit=60.0)

    report = controller.coarsen_solve_reconstruct(
        factor=2,
        timelimit=30.0,
        solve_flow=_FULL_FIVE_STEP_FLOW,
    )

    summary = controller.csr_solve_flow_summary
    assert summary is not None

    # (a) child produced >= 2 candidates across the 5-step flow.
    assert summary["candidate_count"] >= 2, summary

    # (b) dedup collapses identical schedules (never grows).
    assert summary["deduped_count"] <= summary["candidate_count"]
    assert len(controller.csr_candidate_rows) == summary["deduped_count"]

    # (c) every surviving reconstructed candidate is feasible on the original
    #     instance (the step drops infeasible ones; on a feasible coarse
    #     schedule reconstruction never yields infeasibility).
    assert summary["dropped_count"] == 0
    assert all(row["valid"] for row in controller.csr_candidate_rows)

    # (d) the registered incumbent equals the original-scale argmin.
    valid_objs = [
        row["restored_obj"]
        for row in controller.csr_candidate_rows
        if row["valid"] and row["restored_obj"] is not None
    ]
    assert valid_objs, "expected at least one valid reconstructed candidate"
    assert report.obj_value == min(valid_objs)
    assert controller.best_obj_value == min(valid_objs)
    assert summary["winner_original_obj"] == min(valid_objs)

    # (d') every row carries a CSR-only elapsed timestamp, harvested from the
    #      child report (start_time + elapsed_time), non-decreasing in row order.
    step_secs = [row["sec_elapsed_step"] for row in controller.csr_candidate_rows]
    assert all(s is not None for s in step_secs), step_secs
    assert all(s >= 0.0 for s in step_secs), step_secs
    assert step_secs == sorted(step_secs), step_secs

    # (e) parent record carries obj_bound None (coarse solve is not a global LB).
    assert report.obj_bound is None
    assert controller.solution_manager.best_obj_bound is None
    assert controller.get_current_valid_lb() == 0

    # Winner incumbent is structurally feasible on the ORIGINAL instance.
    incumbent = controller.best_solution
    assert incumbent is not None
    controller.check_feasibility(incumbent.schedule.get_jik_2_start_time_map())


def test_child_controller_executes_all_five_steps_headless() -> None:
    """The headless child controller (time_factor=factor, no layout/working dir)
    invokes all five step methods without crashing, and each registers at least
    once — the structural guarantee behind the full-flow harvest."""
    instance = _make_instance()
    coarse = FFcDDWParameters.coarsen_processing_times(instance, 2)
    child = FFcDDWSubroutineController(
        instance=coarse,
        subroutine_flow=_FULL_FIVE_STEP_FLOW,
        stopping_criteria={"timelimit": 30.0},
        time_factor=2,
    )
    child.run()  # headless: must not raise despite no artifact sink

    for method in (
        "calc_mcf_lb_and_derive_full_sch",
        "run_flip_makespan_cp_from_incumbent",
        "neh_cp",
        "incremental_sw_cp",
        "solve_base_model_cpsat",
    ):
        assert child.method_call_counts.get(method, 0) >= 1, method
    # Multiple candidate schedules land on the child history.
    schedules = [r for r in child.solution_manager.history if r.solution is not None]
    assert len(schedules) >= 2


def test_solve_flow_registers_exactly_once() -> None:
    """The step must call _register exactly once in solve_flow mode."""
    controller = _make_controller(timelimit=60.0)

    register_calls: list = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):  # type: ignore[no-untyped-def]
        register_calls.append((report, solution))
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct(
        factor=2,
        timelimit=30.0,
        solve_flow=[
            {"method": "calc_mcf_lb_and_derive_full_sch"},
            {"method": "solve_base_model_cpsat", "timelimit": 1.0},
        ],
    )
    assert len(register_calls) == 1


def test_solve_flow_via_run_dispatch() -> None:
    """solve_flow reaches the step through the routix subroutine-flow dispatch
    (a reduced 2-step flow keeps this fast)."""
    instance = _make_instance()
    controller = FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[
            {
                "method": "coarsen_solve_reconstruct",
                "factor": 2,
                "timelimit": 20.0,
                "solve_flow": [
                    {"method": "calc_mcf_lb_and_derive_full_sch"},
                    {"method": "solve_base_model_cpsat", "timelimit": 1.0},
                ],
            }
        ],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    controller.run()  # must not raise
    assert controller.best_solution is not None
    assert controller.solution_manager.best_obj_bound is None


# ---------------------------------------------------------------------------
# solve_flow validation / warnings
# ---------------------------------------------------------------------------


def test_solve_flow_empty_list_raises() -> None:
    controller = _make_controller()
    with pytest.raises(ValueError, match="non-empty"):
        controller.coarsen_solve_reconstruct(solve_flow=[])


def test_solve_flow_warns_on_explicit_seed_dispatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    controller = _make_controller(timelimit=60.0)
    with caplog.at_level(logging.WARNING):
        controller.coarsen_solve_reconstruct(
            factor=2,
            timelimit=20.0,
            seed_dispatch="v4",  # non-default -> should warn and be ignored
            solve_flow=[
                {"method": "calc_mcf_lb_and_derive_full_sch"},
                {"method": "solve_base_model_cpsat", "timelimit": 1.0},
            ],
        )
    assert any("ignored in solve_flow mode" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# (f) legacy no-solve_flow path is byte-identical to the pure pipeline
# ---------------------------------------------------------------------------


def test_legacy_no_solve_flow_matches_pure_pipeline() -> None:
    """A no-solve_flow CSR call registers the same original-scale objective the
    pure ``run_coarsen_solve_reconstruct`` pipeline produces.

    Uses ``solve=False`` (deterministic seed-only mode) so the comparison is
    exact and does not depend on CP-SAT nondeterminism under a time limit.
    """
    instance = _make_instance()
    factor = 2

    controller = _make_controller(instance=instance, timelimit=60.0)
    report = controller.coarsen_solve_reconstruct(
        factor=factor, timelimit=30.0, solve=False
    )

    option = CoarsenSolveReconstructOption(
        factor=factor, timelimit_sec=30.0, solve=False, seed_dispatch="mixed"
    )
    trace = run_coarsen_solve_reconstruct(
        instance, option, logging.getLogger("test_csr_legacy")
    )

    assert report.obj_value == trace.obj_value
    assert report.obj_bound is None
    # solve_flow-only state stays empty on the legacy path.
    assert controller.csr_candidate_rows == []
    assert controller.csr_solve_flow_summary is None


# ---------------------------------------------------------------------------
# Runner emission: <instance>_csr_candidates.csv
# ---------------------------------------------------------------------------


def test_runner_emits_candidates_csv(tmp_path: Path) -> None:
    """The single-instance runner emits a csr_candidates.csv with one row per
    candidate when the controller carries ``csr_candidate_rows``."""
    run_id = "20260711T000000_000000"
    layout = FFcArtifactLayout(run_root=tmp_path / run_id, run_id=run_id)
    scope = {"scenario_name": "sc", "instance_name": "test_ins"}

    runner = object.__new__(FFcDDWSingleInstanceRunner)
    runner.logger = MagicMock()
    runner.ins_name = "test_ins"
    runner._ins_name = "test_ins"
    runner.instance = MagicMock()

    rows = [
        {
            "source": "1-calc_mcf_lb_and_derive_full_sch",
            "coarse_obj": 2.0,
            "coarse_bound": None,
            "restored_obj": 1.0,
            "valid": True,
            "sec_elapsed_step": 1.17,
            "sec_elapsed_recon": 0.001,
        },
        {
            "source": "5-solve_base_model_cpsat",
            "coarse_obj": 3.0,
            "coarse_bound": 3.0,
            "restored_obj": None,
            "valid": False,
            "sec_elapsed_step": 3.48,
            "sec_elapsed_recon": 0.002,
        },
    ]
    controller = SimpleNamespace(
        csr_phase_schedules=[],
        csr_cp_trajectory=None,
        csr_candidate_rows=rows,
    )

    runner._emit_csr_artifacts(controller, layout, scope)

    csv_path = layout.artifact_path("csr_candidates_csv", **scope)
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        parsed = list(csv.DictReader(f))
    assert [r["source"] for r in parsed] == [
        "1-calc_mcf_lb_and_derive_full_sch",
        "5-solve_base_model_cpsat",
    ]
    assert parsed[0]["restored_obj"] == "1.0"
    assert parsed[0]["valid"] == "True"
    # Dropped candidate: restored_obj cell is blank.
    assert parsed[1]["restored_obj"] == ""
    assert parsed[1]["valid"] == "False"


def test_runner_no_candidates_writes_no_csv(tmp_path: Path) -> None:
    """When csr_candidate_rows is empty/absent, no candidates CSV is written."""
    run_id = "20260711T000000_000001"
    layout = FFcArtifactLayout(run_root=tmp_path / run_id, run_id=run_id)
    scope = {"scenario_name": "sc", "instance_name": "test_ins"}

    runner = object.__new__(FFcDDWSingleInstanceRunner)
    runner.logger = MagicMock()
    runner.ins_name = "test_ins"
    runner._ins_name = "test_ins"
    runner.instance = MagicMock()

    controller = SimpleNamespace(csr_phase_schedules=[], csr_cp_trajectory=None)
    runner._emit_csr_artifacts(controller, layout, scope)

    csv_path = layout.artifact_path("csr_candidates_csv", **scope)
    assert not csv_path.exists()
