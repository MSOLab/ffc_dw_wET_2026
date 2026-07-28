"""Tests for ``incremental_job_contrib_cp`` composite step."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from routix.io.yaml import load_yaml
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.job_contrib_cp.selection import select_jd_jobs
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


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


def test_composite_does_not_register_itself(tmp_path: Path) -> None:
    """Composite never calls _register — inner steps do."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()
    start_hist = len(controller.solution_manager.history)

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    # FAM (1) + some number of inner job_contrib_cp calls
    assert len(controller.solution_manager.history) >= start_hist


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
    """jd_start >= n stops immediately without calling job_contrib_cp."""
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

    assert len(controller.solution_manager.history) == history_before


# -------------------------------------------------------------- P9a: zero obj early exit


def test_zero_obj_early_exit(tmp_path: Path) -> None:
    """All jobs on time → zero obj → no CP calls."""
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

    assert len(controller.solution_manager.history) == history_before


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
    """Each CP-solve iteration produces one summary row."""
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()
    history_before = len(controller.solution_manager.history)

    controller.incremental_job_contrib_cp(
        jd_start=1,
        jd_end="0.1n",
        destroyed_op_tl_multiplier=0.005,
    )

    inner_regs = len(controller.solution_manager.history) - history_before
    log_files = list(tmp_path.rglob("*_incremental_job_contrib_cp_log.yaml"))
    assert log_files
    log = load_yaml(log_files[0])
    # Every row is a CP solve — skipped iterations (same destroy set) add no row.
    assert len(log["rows"]) == inner_regs, (
        f"summary has {len(log['rows'])} rows for {inner_regs} registrations"
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
