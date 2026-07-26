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
from unittest.mock import MagicMock, patch

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
from ffc_ddw_sum_et.orchestration.controller import (
    FFcDDWSubroutineController,
    _best_valid_lb,
)
from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import (
    FFcDDWSingleInstanceRunner,
    _fold_history_into_obj_log_dicts,
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
# solve_flow reconstruct_mode routing (the b30_* batch runs this path)
# ---------------------------------------------------------------------------


_MINIMAL_FLOW = [
    {"method": "calc_mcf_lb_and_derive_full_sch"},
    {"method": "solve_base_model_cpsat", "timelimit": 1.0},
]


def test_solve_flow_active_mode_routes_through_active_reconstruction() -> None:
    """reconstruct_mode='active' reconstructs solve_flow candidates via the active
    rebuild, never the semi-active one."""
    import ffc_ddw_sum_et.orchestration.controller as ctrl_mod

    controller = _make_controller(timelimit=60.0)
    with (
        patch.object(
            ctrl_mod,
            "reconstruct_active_coarse_schedule",
            wraps=ctrl_mod.reconstruct_active_coarse_schedule,
        ) as active_spy,
        patch.object(
            ctrl_mod,
            "reconstruct_coarse_schedule",
            wraps=ctrl_mod.reconstruct_coarse_schedule,
        ) as semi_spy,
    ):
        controller.coarsen_solve_reconstruct(
            factor=2,
            timelimit=20.0,
            reconstruct_mode="active",
            solve_flow=_MINIMAL_FLOW,
        )

    assert active_spy.call_count >= 1
    assert semi_spy.call_count == 0
    assert controller.best_solution is not None


def test_solve_flow_default_mode_routes_through_semi_active() -> None:
    """Default (semi_active) reconstructs solve_flow candidates via the semi-active
    rebuild — the switch is opt-in."""
    import ffc_ddw_sum_et.orchestration.controller as ctrl_mod

    controller = _make_controller(timelimit=60.0)
    with (
        patch.object(
            ctrl_mod,
            "reconstruct_active_coarse_schedule",
            wraps=ctrl_mod.reconstruct_active_coarse_schedule,
        ) as active_spy,
        patch.object(
            ctrl_mod,
            "reconstruct_coarse_schedule",
            wraps=ctrl_mod.reconstruct_coarse_schedule,
        ) as semi_spy,
    ):
        controller.coarsen_solve_reconstruct(
            factor=2,
            timelimit=20.0,
            solve_flow=_MINIMAL_FLOW,
        )

    assert semi_spy.call_count >= 1
    assert active_spy.call_count == 0
    assert controller.best_solution is not None


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

    controller = SimpleNamespace(csr_phase_schedules=[])
    runner._emit_csr_artifacts(controller, layout, scope)

    csv_path = layout.artifact_path("csr_candidates_csv", **scope)
    assert not csv_path.exists()


# ---------------------------------------------------------------------------
# CSR inner-solve trajectory → progress_log (plan §4)
# ---------------------------------------------------------------------------


def test_solve_flow_progress_log_nonempty_and_non_decreasing() -> None:
    """TDD step 1: the registered report's progress_log is non-empty,
    has non-decreasing elapsed_sec, and its last obj_value equals the
    report's registered obj_value."""
    controller = _make_controller(timelimit=60.0)

    register_kwargs: list[dict] = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):
        register_kwargs.append(kwargs)
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    report = controller.coarsen_solve_reconstruct(
        factor=2,
        timelimit=30.0,
        solve_flow=_FULL_FIVE_STEP_FLOW,
    )

    assert len(register_kwargs) == 1
    progress_log = register_kwargs[0].get("progress_log", ())
    assert isinstance(progress_log, tuple)
    assert len(progress_log) > 0, "progress_log must be non-empty"

    secs = [e.elapsed_sec for e in progress_log]
    assert secs == sorted(secs), "elapsed_sec must be non-decreasing"

    assert progress_log[-1].obj_value == report.obj_value, (
        "last progress_log obj_value must equal the report's registered obj_value"
    )


def test_solve_flow_progress_log_bound_factor_dependent() -> None:
    """TDD step 3: factor>1 → every entry has obj_bound=None.
    factor=1 → carries the child LB bound (coarse_bound is valid at identity)."""
    instance = _make_instance()

    # --- factor=2: obj_bound must be None everywhere ---
    ctrl2 = FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "coarsen_solve_reconstruct"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    register_kwargs2: list[dict] = []
    orig2 = ctrl2._register

    def spy2(report, solution, **kwargs):
        register_kwargs2.append(kwargs)
        return orig2(report, solution, **kwargs)

    ctrl2._register = spy2  # type: ignore[method-assign]
    ctrl2.coarsen_solve_reconstruct(
        factor=2,
        timelimit=30.0,
        solve_flow=[
            {"method": "calc_mcf_lb_and_derive_full_sch"},
            {"method": "neh_cp", "job_priority": "weight-due-pos", "cp_tl": 1.0},
        ],
    )
    assert len(register_kwargs2) == 1
    pl2 = register_kwargs2[0].get("progress_log", ())
    assert len(pl2) > 0
    for e in pl2:
        assert e.obj_bound is None, (
            f"factor=2: obj_bound must be None, got {e.obj_bound}"
        )

    # --- factor=1: child LB steps carry a valid bound ---
    ctrl1 = FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "coarsen_solve_reconstruct"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    register_kwargs1: list[dict] = []
    orig1 = ctrl1._register

    def spy1(report, solution, **kwargs):
        register_kwargs1.append(kwargs)
        return orig1(report, solution, **kwargs)

    ctrl1._register = spy1  # type: ignore[method-assign]
    ctrl1.coarsen_solve_reconstruct(
        factor=1,
        timelimit=30.0,
        solve_flow=[
            {"method": "calc_mcf_lb_and_derive_full_sch"},
            {"method": "neh_cp", "job_priority": "weight-due-pos", "cp_tl": 1.0},
        ],
    )
    assert len(register_kwargs1) == 1
    pl1 = register_kwargs1[0].get("progress_log", ())
    assert len(pl1) > 0
    has_bound = any(e.obj_bound is not None for e in pl1)
    assert has_bound, "factor=1: at least one entry must carry a valid obj_bound"


def test_solve_flow_progress_log_non_increasing_obj_values() -> None:
    """TDD step 4: progress_log obj_values are non-increasing (running-min),
    ensuring trajectory consistency with the registered end point."""
    controller = _make_controller(timelimit=60.0)

    register_kwargs: list[dict] = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):
        register_kwargs.append(kwargs)
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct(
        factor=2,
        timelimit=30.0,
        solve_flow=_FULL_FIVE_STEP_FLOW,
    )

    assert len(register_kwargs) == 1
    progress_log = register_kwargs[0].get("progress_log", ())
    assert len(progress_log) > 0

    obj_values = [e.obj_value for e in progress_log if e.obj_value is not None]
    assert len(obj_values) >= 2, "need at least 2 obj_value entries"
    for i in range(1, len(obj_values)):
        assert obj_values[i] <= obj_values[i - 1], (
            f"obj_value must be non-increasing (running-min); "
            f"got {obj_values[i - 1]} → {obj_values[i]} at index {i}"
        )


# ---------------------------------------------------------------------------
# CSR coarse-scale inner obj_log (§8 TDD)
# ---------------------------------------------------------------------------


def test_inner_obj_log_has_more_points_than_child_steps() -> None:
    """TDD §8.5 step 1: the coarse aggregator yields a JSON payload whose
    obj_value.data has more points than there are child history records
    (proves intra-step folding of each registration's progress_log)."""
    controller = _make_controller(timelimit=60.0)

    controller.coarsen_solve_reconstruct(
        factor=2,
        timelimit=30.0,
        solve_flow=_FULL_FIVE_STEP_FLOW,
    )

    child_history = controller.csr_child_history
    assert child_history is not None
    n_steps = len(child_history)

    value_data: dict[str, float] = {}
    value_notes: dict[str, str] = {}
    bound_data: dict[str, float] = {}
    bound_notes: dict[str, str] = {}
    _fold_history_into_obj_log_dicts(
        child_history, value_data, value_notes, bound_data, bound_notes
    )

    assert len(value_data) > n_steps, (
        f"obj_value.data ({len(value_data)}) must have more points than "
        f"child history records ({n_steps}) — intra-step folding not working"
    )


def test_inner_obj_log_bound_at_factor_gt_one_and_child_origin() -> None:
    """TDD §8.5 step 3: at κ>1, obj_bound.data is populated (child coarse
    LB is carried here, unlike the parent obj_log which nulls it), and
    x-coordinates start at the child frame origin (t≈0), not the parent offset."""
    controller = _make_controller(timelimit=60.0)

    controller.coarsen_solve_reconstruct(
        factor=2,
        timelimit=30.0,
        solve_flow=_FULL_FIVE_STEP_FLOW,
    )

    child_history = controller.csr_child_history
    assert child_history is not None

    value_data: dict[str, float] = {}
    value_notes: dict[str, str] = {}
    bound_data: dict[str, float] = {}
    bound_notes: dict[str, str] = {}
    _fold_history_into_obj_log_dicts(
        child_history, value_data, value_notes, bound_data, bound_notes
    )

    # obj_bound must be populated (carried from child's LB steps).
    assert len(bound_data) > 0, (
        "obj_bound.data must be populated at κ>1 — child coarse LB "
        "is carried in the inner obj_log"
    )

    # x-coordinates (elapsed_sec keys) must start near the child frame origin.
    all_keys = sorted(float(k) for k in value_data)
    assert all_keys[0] >= 0.0
    assert all_keys[0] < 1.0, (
        f"earliest timestamp ({all_keys[0]}) should be near child frame "
        f"origin (t=0), not parent offset"
    )


def test_runner_emits_csr_inner_obj_log_json(tmp_path: Path) -> None:
    """TDD §8.5 step 4: the runner writes csr_inner_obj_log_json when
    csr_child_history is present, and does not when it is absent."""
    import json

    from ffc_ddw_sum_et.orchestration.subroutine_report import (
        FFcDDWSubroutineReport,
    )

    run_id = "20260721T000000_000000"
    layout = FFcArtifactLayout(run_root=tmp_path / run_id, run_id=run_id)
    scope = {"scenario_name": "sc", "instance_name": "test_ins"}

    runner = object.__new__(FFcDDWSingleInstanceRunner)
    runner.logger = MagicMock()
    runner.ins_name = "test_ins"
    runner._ins_name = "test_ins"
    runner.instance = MagicMock()

    # Build a minimal child history with one record carrying progress_log.
    report = FFcDDWSubroutineReport(
        elapsed_time=2.5,
        obj_value=100.0,
        obj_bound=50.0,
        start_time=0.1,
        step_label="1-calc_mcf_lb_and_derive_full_sch",
    )
    child_history = [
        SimpleNamespace(
            report=report,
            solution=MagicMock(),
        )
    ]
    controller = SimpleNamespace(
        csr_phase_schedules=[],
        csr_candidate_rows=[],
        csr_child_history=child_history,
    )

    runner._emit_csr_artifacts(controller, layout, scope)

    inner_path = layout.artifact_path("csr_inner_obj_log_json", **scope)
    assert inner_path.exists(), "csr_inner_obj_log_json must be written"

    with open(inner_path, encoding="utf-8") as f:
        payload = json.load(f)
    assert "obj_value" in payload
    assert "obj_bound" in payload
    assert len(payload["obj_value"]["data"]) > 0
    assert len(payload["obj_bound"]["data"]) > 0

    # --- csr_child_history absent: no file written ---
    run_id2 = "20260721T000000_000001"
    layout2 = FFcArtifactLayout(run_root=tmp_path / run_id2, run_id=run_id2)
    controller2 = SimpleNamespace(
        csr_phase_schedules=[],
        csr_candidate_rows=[],
        csr_child_history=None,
    )

    runner2 = object.__new__(FFcDDWSingleInstanceRunner)
    runner2.logger = MagicMock()
    runner2.ins_name = "test_ins"
    runner2._ins_name = "test_ins"
    runner2.instance = MagicMock()

    runner2._emit_csr_artifacts(controller2, layout2, scope)

    inner_path2 = layout2.artifact_path("csr_inner_obj_log_json", **scope)
    assert not inner_path2.exists(), (
        "csr_inner_obj_log_json must NOT be written when child_history is absent"
    )


# ---------------------------------------------------------------------------
# Candidate-drop path (reconstruction raises → dropped_count > 0)
# ---------------------------------------------------------------------------


def _RAISE_ON_FIRST(wrapped: object) -> object:  # type: ignore[no-untyped-def]
    """Side-effect factory: raise Exception on first call, pass through after."""
    _first = True

    def wrapper(*args, **kwargs):
        nonlocal _first
        if _first:
            _first = False
            raise RuntimeError("simulated reconstruction failure")
        return wrapped(*args, **kwargs)

    return wrapper


def test_solve_flow_candidate_drop_path() -> None:
    """A simulated reconstruction failure on one candidate should produce
    ``dropped_count >= 1`` and at least one ``valid=False`` row."""
    controller = _make_controller(timelimit=60.0)
    two_step_flow = [
        {"method": "calc_mcf_lb_and_derive_full_sch"},
        {"method": "neh_cp", "job_priority": "weight-due-pos", "cp_tl": 1.0},
    ]

    from ffc_ddw_sum_et.solution.schedule_build import (
        reconstruct_coarse_schedule as real_reconstruct,
    )

    side_effect = _RAISE_ON_FIRST(real_reconstruct)
    with patch(
        "ffc_ddw_sum_et.orchestration.controller.reconstruct_coarse_schedule",
        side_effect=side_effect,
    ):
        controller.coarsen_solve_reconstruct(
            factor=2,
            timelimit=30.0,
            solve_flow=two_step_flow,
        )

    summary = controller.csr_solve_flow_summary
    assert summary is not None
    assert summary["dropped_count"] >= 1, (
        f"expected at least 1 dropped candidate, got {summary['dropped_count']}"
    )
    rows = controller.csr_candidate_rows
    assert any(r["valid"] is False for r in rows), (
        "expected at least one valid=False row"
    )


# ---------------------------------------------------------------------------
# No-winner fallback (all candidates dropped)
# ---------------------------------------------------------------------------


def _RAISE_ALWAYS(wrapped: object) -> object:  # type: ignore[no-untyped-def]
    """Side-effect factory: raise on every call, no candidate survives."""

    def wrapper(*args, **kwargs):
        raise RuntimeError("simulated reconstruction failure")

    return wrapper


def test_solve_flow_no_winner_registers_none() -> None:
    """When every candidate's reconstruction raises, the step registers with
    solution=None and the summary marks winner_source=None."""
    controller = _make_controller(timelimit=60.0)
    two_step_flow = [
        {"method": "calc_mcf_lb_and_derive_full_sch"},
        {"method": "neh_cp", "job_priority": "weight-due-pos", "cp_tl": 1.0},
    ]

    register_kwargs: list[dict] = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):
        register_kwargs.append({"report": report, "solution": solution})
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    with patch(
        "ffc_ddw_sum_et.orchestration.controller.reconstruct_coarse_schedule",
        side_effect=_RAISE_ALWAYS(None),
    ):
        controller.coarsen_solve_reconstruct(
            factor=2,
            timelimit=30.0,
            solve_flow=two_step_flow,
        )

    summary = controller.csr_solve_flow_summary
    assert summary is not None
    assert summary["dropped_count"] == summary["deduped_count"], (
        f"all deduped candidates should be dropped; "
        f"dropped={summary['dropped_count']}, deduped={summary['deduped_count']}"
    )
    assert summary["winner_source"] is None
    assert summary["winner_original_obj"] is None

    assert len(register_kwargs) == 1
    assert register_kwargs[0]["solution"] is None, (
        "no-winner path must register solution=None"
    )


# ---------------------------------------------------------------------------
# W1 C1 — CSR inner progress point notes
# ---------------------------------------------------------------------------


def test_solve_flow_progress_log_has_notes_at_factor_1() -> None:
    """C1 §5.1: τ=1 -> every progress_log entry has a note, and the format
    matches the ``<idx>-<subroutine_name>`` regex so the structured loader
    picks them up."""
    import re

    controller = _make_controller(timelimit=60.0)

    register_kwargs: list[dict] = []
    original_register = controller._register

    def spy_register(report, solution, **kwargs):
        register_kwargs.append(kwargs)
        return original_register(report, solution, **kwargs)

    controller._register = spy_register  # type: ignore[method-assign]

    controller.coarsen_solve_reconstruct(
        factor=1,
        timelimit=30.0,
        solve_flow=_FULL_FIVE_STEP_FLOW,
    )

    assert len(register_kwargs) == 1
    progress_log = register_kwargs[0].get("progress_log", ())
    assert len(progress_log) > 0

    notes_present = [e.note for e in progress_log if e.note is not None]
    assert len(notes_present) > 0, "at least one progress_log entry must have a note"

    # In a flow-run, call_context is e.g. "1-coarsen_solve_reconstruct";
    # direct calls yield "ROOT". Either way, the note must end with
    # ".inner-<k:02d>-<source>" and the loader regex `^\d+-(.+)$`
    # must parse it when the call_context has the right form.
    inner_pattern = re.compile(r"\.inner-\d{2}-")
    for entry in progress_log:
        if entry.note is not None:
            assert inner_pattern.search(entry.note) is not None, (
                f"note must contain '.inner-NN-': {entry.note!r}"
            )


def test_solve_flow_progress_log_notes_in_obj_log() -> None:
    """C1 §5.1 (loader): notes from progress_log survive into the obj_log
    value_notes / bound_notes dicts after _fold_history_into_obj_log_dicts
    on the parent's history."""
    controller = _make_controller(timelimit=60.0)

    controller.coarsen_solve_reconstruct(
        factor=1,
        timelimit=30.0,
        solve_flow=_FULL_FIVE_STEP_FLOW,
    )

    parent_history = controller.solution_manager.history
    assert len(parent_history) > 0

    value_data: dict[str, float] = {}
    value_notes: dict[str, str] = {}
    bound_data: dict[str, float] = {}
    bound_notes: dict[str, str] = {}
    _fold_history_into_obj_log_dicts(
        parent_history, value_data, value_notes, bound_data, bound_notes
    )

    inner_notes = [v for v in value_notes.values() if ".inner-" in v]
    assert len(inner_notes) > 0, (
        f"parent obj_log value_notes must contain .inner- notes; "
        f"got {list(value_notes.values())}"
    )
    # bound_notes also carry inner- notes when entries have obj_bound.
    inner_bound_notes = [v for v in bound_notes.values() if ".inner-" in v]
    assert len(inner_bound_notes) > 0, (
        "parent obj_log bound_notes must contain .inner- notes for MCF-LB entry"
    )


# ---------------------------------------------------------------------------
# W1 C3 — τ=1 obj_bound validity
# ---------------------------------------------------------------------------


def test_solve_flow_report_obj_bound_at_factor_1() -> None:
    """C3 §5.5: factor=1 -> SubroutineReport.obj_bound is not None
    (child best LB propagated); factor>1 -> obj_bound is None."""
    instance = _make_instance()

    # factor=1 with MCF-LB in flow -> should get a valid bound
    ctrl1 = FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "coarsen_solve_reconstruct"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    register_kwargs1: list[dict] = []
    orig1 = ctrl1._register

    def spy1(report, solution, **kwargs):
        register_kwargs1.append({"report": report, "solution": solution, **kwargs})
        return orig1(report, solution, **kwargs)

    ctrl1._register = spy1  # type: ignore[method-assign]
    ctrl1.coarsen_solve_reconstruct(
        factor=1,
        timelimit=30.0,
        solve_flow=[
            {"method": "calc_mcf_lb_and_derive_full_sch"},
            {"method": "neh_cp", "job_priority": "weight-due-pos", "cp_tl": 1.0},
        ],
    )
    assert len(register_kwargs1) == 1
    rpt1 = register_kwargs1[0]["report"]
    assert rpt1.obj_bound is not None, "factor=1: report.obj_bound must be populated"

    # The propagated bound is the TIGHTEST (max) child LB, not the loosest.
    child_bounds = [
        float(rec.report.obj_bound)
        for rec in ctrl1.csr_child_history
        if rec.report is not None and rec.report.obj_bound is not None
    ]
    assert child_bounds, "flow must produce at least one child bound"
    assert rpt1.obj_bound == max(child_bounds), (
        f"factor=1: obj_bound must be max(child LBs)={max(child_bounds)}, "
        f"got {rpt1.obj_bound}"
    )

    # factor=2 -> bound must be None
    ctrl2 = FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "coarsen_solve_reconstruct"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    register_kwargs2: list[dict] = []
    orig2 = ctrl2._register

    def spy2(report, solution, **kwargs):
        register_kwargs2.append({"report": report, "solution": solution, **kwargs})
        return orig2(report, solution, **kwargs)

    ctrl2._register = spy2  # type: ignore[method-assign]
    ctrl2.coarsen_solve_reconstruct(
        factor=2,
        timelimit=30.0,
        solve_flow=[
            {"method": "calc_mcf_lb_and_derive_full_sch"},
            {"method": "neh_cp", "job_priority": "weight-due-pos", "cp_tl": 1.0},
        ],
    )
    assert len(register_kwargs2) == 1
    rpt2 = register_kwargs2[0]["report"]
    assert rpt2.obj_bound is None, "factor=2: report.obj_bound must be None"


def test_best_valid_lb_picks_the_largest_bound() -> None:
    """A lower bound is tighter the larger it is, so ``_best_valid_lb`` must
    reduce with ``max`` — matching ``_a_is_better_obj_bound`` (``a > b``) and
    the runner's ``bestBound = max(...)``. ``None`` entries are skipped."""
    assert _best_valid_lb([7261.0, 10174.0, 0.0]) == 10174.0
    assert _best_valid_lb([None, 7261.0, None]) == 7261.0
    assert _best_valid_lb([]) is None
    assert _best_valid_lb([None, None]) is None
    # Ints are normalised to float so callers can compare without surprises.
    assert _best_valid_lb([3, 9]) == 9.0
    assert isinstance(_best_valid_lb([3, 9]), float)
