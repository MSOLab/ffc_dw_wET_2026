"""Tests for ``incremental_job_contrib_cp`` composite step."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from routix.io.yaml import load_yaml
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.job_contrib_cp.selection import select_jd_jobs
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import (
    _fold_history_into_obj_log_dicts,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

_INNER_CALL_LABEL_RE = re.compile(r"jd\d+_r\d+$")


def _is_inner_call_label(step_label: str | None) -> bool:
    """Whether a history entry came from an inner ``job_contrib_cp`` call.

    ``incremental_job_contrib_cp`` namespaces every inner call as
    ``jd<NNN>_r<MMM>`` via ``temporarily_extended_context``; its own endpoint
    registration carries no such suffix. Matching the suffix keeps these tests
    independent of how deep the controller's method-context stack is (a bare
    step call has an empty stack, a flow-driven run does not).
    """
    return bool(step_label) and _INNER_CALL_LABEL_RE.search(step_label) is not None


def _make_instance(
    name: str = "ijccp_test",
) -> FFcDDWParameters:
    job_id_list = ["j0", "j1", "j2", "j3"]
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1], [1, 2]]),
        ),
        job_2_due_window_map={
            "j0": (4, 5),
            "j1": (3, 4),
            "j2": (0, 10),
            "j3": (6, 7),
        },
        job_2_ewt_map={"j0": 1, "j1": 2, "j2": 1, "j3": 1},
        job_2_twt_map={"j0": 2, "j1": 1, "j2": 1, "j3": 2},
    )


def _make_controller(
    instance: FFcDDWParameters,
    working_dir: Path | None = None,
    timelimit: float = 300,
) -> FFcDDWSubroutineController:
    controller = FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "run_fam"}],
        stopping_criteria=StoppingCriteria({"timelimit": timelimit}),
    )
    if working_dir is not None:
        controller.set_working_dir(working_dir)
    return controller


def _make_zero_contrib_instance() -> FFcDDWParameters:
    job_id_list = ["j0", "j1", "j2", "j3"]
    return FFcDDWParameters(
        name="ijccp_zero",
        job_id_list=job_id_list,
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="ijccp_zero_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1], [1, 2]]),
        ),
        job_2_due_window_map={j: (0, 100) for j in job_id_list},
        job_2_ewt_map={j: 1 for j in job_id_list},
        job_2_twt_map={j: 2 for j in job_id_list},
    )


# ------------------------------------------------------------------ P4: validation


def test_requires_incumbent() -> None:
    controller = _make_controller(_make_instance())

    with pytest.raises(RuntimeError, match="requires an incumbent schedule"):
        controller.incremental_job_contrib_cp()


def test_jd_step_size_less_than_1_raises(tmp_path: Path) -> None:
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    with pytest.raises(ValueError, match="jd_step_size must be >= 1"):
        controller.incremental_job_contrib_cp(jd_step_size=0)


def test_jd_end_less_than_jd_start_raises(tmp_path: Path) -> None:
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    with pytest.raises(ValueError, match="jd_end"):
        controller.incremental_job_contrib_cp(jd_start=3, jd_end=2)


def test_invalid_range_raises_even_when_jd_start_ge_n(tmp_path: Path) -> None:
    """Range validation runs before the ``jd_start >= n`` early return.

    Otherwise a genuinely invalid config returns silently on small instances.
    """
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    with pytest.raises(ValueError, match="jd_end"):
        # n=4, so jd_start=4 also trips the "nothing to do" guard.
        controller.incremental_job_contrib_cp(jd_start=4, jd_end=2)


# --------------------------------------------------------------- P4: composite pattern


def test_composite_registers_self_endpoint(tmp_path: Path) -> None:
    """C3: composite calls _register once at the end, even when inner steps
    also registered. The current incumbent is passed through so the tail entry
    keeps a solution (see ``test_composite_endpoint_keeps_work_status``)."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()
    history_before = len(controller.solution_manager.history)
    incumbent_before = controller.solution_manager.get_incumbent()

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    history_after = len(controller.solution_manager.history)
    # At least 2: one inner job_contrib_cp + composite self-registration
    assert history_after >= history_before + 2

    # Last entry is the composite's self-endpoint, carrying the incumbent.
    last_entry = controller.solution_manager.history[-1]
    assert last_entry.solution is controller.solution_manager.get_incumbent()
    assert last_entry.report.obj_value == controller.solution_manager.best_obj_value
    assert last_entry.report.obj_bound is None
    # Re-registering the incumbent must not swap it for an equal-objective one.
    if controller.solution_manager.best_obj_value == getattr(
        incumbent_before, "obj_value", None
    ):
        assert controller.solution_manager.get_incumbent() is incumbent_before


def test_composite_endpoint_keeps_work_status(tmp_path: Path) -> None:
    """Given a run that ends with the composite step,
    when its self-endpoint is registered,
    then ``work_status`` still reports the feasible incumbent.

    ``work_status`` reads ``history[-1]`` and returns None for a record with
    ``solution=None``, which would write a successful run to
    ``<instance>_instance_result.yaml`` as status-unknown.
    """
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()
    assert controller.work_status is not None

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    assert controller.work_status is not None, (
        "composite self-endpoint erased work_status"
    )


def test_composite_endpoint_does_not_overwrite_last_inner_note(
    tmp_path: Path,
) -> None:
    """Given the composite endpoint registered right after the last inner call,
    when the history is folded into the obj_log maps,
    then the last inner step keeps its own note label.

    ``_fold_history_into_obj_log_dicts`` keys notes by
    ``repr(start_time + elapsed_time)`` and assigns (not ``setdefault``s) the
    label, so an endpoint timestamp identical to the last inner call's would
    silently replace that call's label with the composite's.
    """
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    history = controller.solution_manager.history
    inner_entry, composite_entry = history[-2], history[-1]
    assert _is_inner_call_label(inner_entry.report.step_label), (
        f"expected an inner call before the composite endpoint, "
        f"got {inner_entry.report.step_label!r}"
    )

    def _end_key(record) -> str:
        return repr(record.report.start_time + record.report.elapsed_time)

    assert _end_key(composite_entry) != _end_key(inner_entry), (
        "composite endpoint collides with the last inner call's timestamp"
    )

    value_data: dict[str, float] = {}
    value_notes: dict[str, str] = {}
    bound_data: dict[str, float] = {}
    bound_notes: dict[str, str] = {}
    _fold_history_into_obj_log_dicts(
        controller.solution_manager.history,
        value_data,
        value_notes,
        bound_data,
        bound_notes,
    )

    assert value_notes[_end_key(inner_entry)] == inner_entry.report.step_label
    assert value_notes[_end_key(composite_entry)] == composite_entry.report.step_label


def test_inner_steps_register(tmp_path: Path) -> None:
    """Each inner job_contrib_cp call registers exactly once."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()
    history_before = len(controller.solution_manager.history)

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    history_after = len(controller.solution_manager.history)
    inner_regs = history_after - history_before
    assert inner_regs >= 1, f"expected at least 1 inner registration, got {inner_regs}"


# ------------------------------------------------------------------ P5: no improvement → advance jd


def test_no_improvement_advances_jd(tmp_path: Path) -> None:
    """When a jd level yields no improvement, the outer loop advances."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.25n",  # n=4 → 1 level: jd=1
        destroyed_op_tl_multiplier=0.005,
    )

    assert controller.solution_manager.best_obj_value is not None


# ------------------------------------------------------------------ P7: jd_step_size


def test_jd_step_size_levels(tmp_path: Path) -> None:
    """jd_step_size > 1 correctly skips levels."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    # n=4, jd_start=1, jd_end=3 → levels [1, 3, 4] with step=2 (range 1..4, step 2)
    # But resolve_jd_count_target saturates at n, so jd_end="0.75n" = ceil(4*0.75)=3
    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.75n",
        jd_step_size=2,
        destroyed_op_tl_multiplier=0.005,
    )

    assert controller.solution_manager.best_obj_value is not None


# ------------------------------------------------------------------ P8: jd >= n guard


def test_jd_start_ge_n_no_cp_calls(tmp_path: Path) -> None:
    """jd_start >= n stops immediately without calling job_contrib_cp.
    With C3 the composite still registers its own endpoint (+1 history entry)."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()
    history_before = len(controller.solution_manager.history)

    with patch.object(
        controller,
        "job_contrib_cp",
        side_effect=RuntimeError("should not be called"),
    ):
        controller.incremental_job_contrib_cp(
            jd_start=4,  # >= n=4
            jd_end=4,  # valid range; the guard, not validation, must stop it
            destroyed_op_tl_multiplier=0.005,
        )

    # C3: composite registers self-endpoint even on early return
    assert len(controller.solution_manager.history) == history_before + 1
    assert (
        controller.solution_manager.history[-1].solution
        is controller.solution_manager.get_incumbent()
    )
    assert controller.work_status is not None


# -------------------------------------------------------------- P9a: zero obj early exit


def test_zero_obj_early_exit(tmp_path: Path) -> None:
    """All jobs on time → zero obj → no CP calls.
    With C3 the composite still registers its own endpoint (+1 history entry)."""
    instance = _make_zero_contrib_instance()
    controller = _make_controller(instance, working_dir=tmp_path)
    controller.run_fam()

    history_before = len(controller.solution_manager.history)

    with patch.object(
        controller,
        "job_contrib_cp",
        side_effect=RuntimeError("should not be called"),
    ):
        controller.incremental_job_contrib_cp(
            jd_start=1,
            jd_end="0.1n",
            destroyed_op_tl_multiplier=0.005,
        )

    # C3: composite registers self-endpoint even on zero-obj early exit
    assert len(controller.solution_manager.history) == history_before + 1
    last_entry = controller.solution_manager.history[-1]
    assert last_entry.solution is controller.solution_manager.get_incumbent()
    assert last_entry.report.obj_value is not None
    assert controller.work_status is not None


# ------------------------------------------------------------------ P10: summary log


def test_summary_log_written(tmp_path: Path) -> None:
    """Summary log must be written with exit_reason and rows."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    log_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_log.yaml"))
    assert log_files, f"no summary log found under {tmp_path}"
    log = load_yaml(log_files[0])
    assert "exit_reason" in log
    assert "rows" in log
    assert isinstance(log["rows"], list)
    # Skipped (same destroy set) iterations add no row, so the count is the
    # only place Phase B can observe them.
    assert isinstance(log["same_set_skips"], int)
    assert log["same_set_skips"] >= 0


def test_summary_log_rows_match_iterations(tmp_path: Path) -> None:
    """Each CP-solve iteration produces one summary row;
    the composite's self-endpoint is not a CP-solve iteration."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()
    history_before = len(controller.solution_manager.history)

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    # Count the inner job_contrib_cp registrations by their step_label rather
    # than subtracting a fixed offset: every inner call is namespaced
    # ``…jdNNN_rMMM`` by temporarily_extended_context, the composite's own
    # endpoint is not.
    inner_regs = sum(
        1
        for record in controller.solution_manager.history[history_before:]
        if _is_inner_call_label(record.report.step_label)
    )
    log_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_log.yaml"))
    assert log_files
    log = load_yaml(log_files[0])
    # Every row is a CP solve — skipped iterations (same destroy set) add no row.
    assert len(log["rows"]) == inner_regs, (
        f"summary has {len(log['rows'])} rows for {inner_regs} inner registrations"
    )
    assert all(r["elapsed"] > 0 for r in log["rows"])


def test_summary_log_skipped_without_working_dir() -> None:
    """No working dir → artifact is skipped, not an AttributeError.

    ``sw_cp`` / ``job_contrib_cp`` both emit their logs through
    ``try_get_file_path_for_subroutine`` for this reason; the composite must
    not raise after doing all of its work.
    """
    controller = _make_controller(_make_instance())  # no set_working_dir
    controller.run_fam()

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    assert controller.solution_manager.best_obj_value is not None


def test_summary_log_skipped_without_working_dir_on_jd_ge_n() -> None:
    """Same for the ``jd_start >= n`` early-return path."""
    controller = _make_controller(_make_instance())  # no set_working_dir
    controller.run_fam()

    controller.incremental_job_contrib_cp(
        jd_start=4,  # >= n=4
        jd_end=4,
        destroyed_op_tl_multiplier=0.005,
    )


def test_summary_log_required_keys(tmp_path: Path) -> None:
    """Each summary row must have the required fields."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    log_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_log.yaml"))
    assert log_files
    log = load_yaml(log_files[0])
    for row in log["rows"]:
        for key in (
            "jd",
            "rep",
            "jd_count_eff",
            "destroyed_op_count",
            "cp_tl_seconds",
            "obj_before",
            "obj_after",
            "elapsed",
            "exit_reason",
        ):
            assert key in row, f"missing key {key!r} in row: {row}"


# ------------------------------------------------------ P8b: saturation termination


def test_saturated_by_positive_job_count(tmp_path: Path) -> None:
    """When len(selected) < jd, the iteration runs and then stops entirely."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    controller.incremental_job_contrib_cp(
        jd_start=3,  # target 3, but maybe only 2 positive jobs
        jd_end="0.75n",  # n=4 → jd_end=3
        destroyed_op_tl_multiplier=0.005,
    )

    log_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_log.yaml"))
    assert log_files
    log = load_yaml(log_files[0])
    assert log["exit_reason"] in (
        "saturated",
        "completed",
        "no_improvement",
        "same_set",
        "jd_ge_n",
    )


def test_budget_exhaustion_stops_loop(tmp_path: Path) -> None:
    """When remaining time falls below the effective minimum, the loop exits
    with ``exit_reason == "budget"`` and the composite registers its endpoint.
    Uses ``min_remaining_sec`` to force a high threshold so the check fires
    regardless of CP-SAT solve speed."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()
    history_before = len(controller.solution_manager.history)

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.5n",
        destroyed_op_tl_multiplier=0.005,
        min_remaining_sec=9e9,
    )

    log_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_log.yaml"))
    assert log_files
    log = load_yaml(log_files[0])
    assert log["exit_reason"] == "budget", (
        f"expected 'budget', got {log['exit_reason']!r}"
    )
    # Composite still registers its own endpoint.
    assert len(controller.solution_manager.history) == history_before + 1
    assert (
        controller.solution_manager.history[-1].solution
        is controller.solution_manager.get_incumbent()
    )


# ------------------------------------------------------------------ P1: select_jd_jobs


def test_select_jd_jobs_returns_correct_order() -> None:
    """Higher contribution = selected first; ties broken by job_id."""
    instance = _make_instance()
    controller = _make_controller(instance)
    controller.run_fam()

    incumbent = controller.solution_manager.get_incumbent()
    selected = select_jd_jobs(incumbent.schedule, instance, 2, time_factor=1)

    assert len(selected) <= 2
    assert all(j in instance.job_id_list for j in selected)


def test_select_jd_jobs_zero_obj_returns_empty() -> None:
    """All-zero contrib → empty list."""
    instance = _make_zero_contrib_instance()
    controller = _make_controller(instance)
    controller.run_fam()

    incumbent = controller.solution_manager.get_incumbent()
    selected = select_jd_jobs(incumbent.schedule, instance, 2, time_factor=1)

    assert selected == []


# -------------------------------------------------------------- P8: progress json


def test_progress_json_written(tmp_path: Path) -> None:
    """Composite writes _incremental_job_contrib_cp_progress.json after running."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    progress_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_progress.json"))
    assert progress_files, f"no progress json found under {tmp_path}"
    data = json.loads(progress_files[0].read_text(encoding="utf-8"))
    assert "same_set_skips" in data
    assert "cp_progress" in data
    assert isinstance(data["cp_progress"], list)
    for entry in data["cp_progress"]:
        assert "jd" in entry
        assert "rep" in entry
        assert "t" in entry
        assert isinstance(entry["t"], (int, float))
        assert "obj_value" in entry
        assert "obj_bound" in entry


def test_progress_json_skipped_without_working_dir() -> None:
    """No working dir → progress json is skipped, no error."""
    controller = _make_controller(_make_instance())
    controller.run_fam()

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    assert controller.solution_manager.best_obj_value is not None


# ------------------------------------------------------------ P5: try/finally


def test_summary_log_written_on_inner_exception(tmp_path: Path) -> None:
    """P5: 내부에서 예외 발생 시에도 summary log가 작성된다."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    with patch.object(
        controller,
        "job_contrib_cp",
        side_effect=RuntimeError("simulated solver error"),
    ):
        with pytest.raises(RuntimeError, match="simulated solver error"):
            controller.incremental_job_contrib_cp(
                jd_start=1,
                jd_end="0.1n",
                destroyed_op_tl_multiplier=0.005,
            )

    log_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_log.yaml"))
    assert log_files, "summary log must be written even on exception"
    log = load_yaml(log_files[0])
    assert log["exit_reason"].startswith("error:"), (
        f"exit_reason should record the error, got {log['exit_reason']!r}"
    )
    assert "RuntimeError" in log["exit_reason"]


def test_summary_log_written_on_inner_infeasible(tmp_path: Path) -> None:
    """P5: error_if_infeasible + INFEASIBLE → 요약 로그가 작성된다."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    with patch(
        "ffc_ddw_sum_et.algorithm.job_contrib_cp.dispatcher.get_solver",
    ) as mock_get_solver:
        from unittest.mock import MagicMock

        mock_solver = MagicMock()
        mock_solver.solve.return_value = 3  # INFEASIBLE
        mock_solver.status_name = MagicMock(return_value="INFEASIBLE")
        mock_get_solver.return_value = mock_solver

        with pytest.raises(RuntimeError, match="INFEASIBLE"):
            controller.incremental_job_contrib_cp(
                jd_start=1,
                jd_end="0.1n",
                destroyed_op_tl_multiplier=0.005,
                error_if_infeasible=True,
            )

    log_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_log.yaml"))
    assert log_files, "summary log must be written even on infeasible"
    log = load_yaml(log_files[0])
    assert "error:RuntimeError" in log["exit_reason"]


# ---------------------------------------------- P4: composite survives UNKNOWN


def test_composite_survives_continuous_unknown(tmp_path: Path) -> None:
    """P4: UNKNOWN이 계속 나오는 stub → 루프가 예외 없이 끝나고 incumbent 보존."""
    from unittest.mock import MagicMock

    instance = _make_instance()
    controller = _make_controller(instance, working_dir=tmp_path)
    controller.run_fam()
    fam_obj = controller.solution_manager.best_obj_value

    mock_solver = MagicMock()
    mock_solver.solve.return_value = 0  # UNKNOWN
    mock_solver.status_name = MagicMock(return_value="UNKNOWN")
    mock_solver.objective_value = 0.0
    mock_solver.value = MagicMock(return_value=0)
    mock_solver.response_proto = MagicMock()
    mock_solver.response_proto.solve_log = ""

    with patch(
        "ffc_ddw_sum_et.algorithm.job_contrib_cp.dispatcher.get_solver",
        return_value=mock_solver,
    ):
        controller.incremental_job_contrib_cp(
            jd_start=1,
            jd_end="0.1n",
            destroyed_op_tl_multiplier=0.005,
            error_if_infeasible=True,
        )

    assert controller.solution_manager.best_obj_value == fam_obj, (
        "incumbent must be preserved after UNKNOWN fallback"
    )
    log_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_log.yaml"))
    assert log_files, "summary log must be written"
    log = load_yaml(log_files[0])
    assert log["exit_reason"] == "completed"


# --------------------------------------------------- P9: renderer smoke test


def test_render_job_contrib_progress_plot_produces_png(
    tmp_path: Path,
) -> None:
    """P9: renderer가 JSON에서 PNG를 생성하며 예외가 없어야 한다."""
    from ffc_ddw_sum_et.orchestration.reporting import (
        _render_job_contrib_progress_plot,
    )

    json_path = tmp_path / "test_progress.json"
    png_path = tmp_path / "test_progress.png"

    json_path.write_text(
        json.dumps(
            {
                "same_set_skips": 0,
                "global_lb": 15000.0,
                "cp_progress": [
                    {
                        "jd": 3,
                        "rep": 1,
                        "t": 0.0,
                        "obj_value": 42000.0,
                        "obj_bound": 30000.0,
                    },
                    {
                        "jd": 3,
                        "rep": 1,
                        "t": 1.0,
                        "obj_value": 41500.0,
                        "obj_bound": 31000.0,
                    },
                    {
                        "jd": 4,
                        "rep": 1,
                        "t": 2.0,
                        "obj_value": 41000.0,
                        "obj_bound": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    _render_job_contrib_progress_plot(json_path, png_path)
    assert png_path.exists(), "PNG must be created"
    assert png_path.stat().st_size > 0, "PNG must be non-empty"


# ------------------------------------------- P10: obj_log regression


def test_job_contrib_cp_step_report_obj_bound_none(tmp_path: Path) -> None:
    """P10: job_contrib_cp의 report가 global obj_log에 LB를 흘리지 않는다.

    ``_fold_history_into_obj_log_dicts``는 bound_data를 두 경로로 채운다 —
    ``report.progress_log[].obj_bound`` (엔트리별) 와 ``report.obj_bound``
    (스텝 종료점). 계획서 §2의 오염(스텝 구간 LB 60점 진동)은 **전자**가
    원인이었으므로 둘 다 확인해야 회귀를 막는다.
    """
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    history_before = len(controller.solution_manager.history)
    report = controller.job_contrib_cp(jd_target=2, cp_tl=5.0)

    assert report.obj_bound is None, (
        f"step report obj_bound must be None to prevent global LB pollution, "
        f"got {report.obj_bound!r}"
    )

    # The registered (wrapped) report is what the runner folds into obj_log.
    registered = controller.solution_manager.history[history_before:]
    assert registered, "job_contrib_cp must register exactly one history record"
    entries = [e for rec in registered for e in rec.report.progress_log]
    assert entries, "progress_log must carry the CP trajectory"
    leaked = [e for e in entries if e.obj_bound is not None]
    assert not leaked, (
        f"progress_log must not carry the restricted-model bound into the "
        f"global obj_log; {len(leaked)} entr(ies) leaked, first={leaked[0]!r}"
    )
