"""P6: controller wiring for the ``job_contrib_cp`` step.

The dispatcher-level tests never touch ``FFcDDWSubroutineController``, so the
post-register artifact path (``_metrics.yaml`` via ``dump_yaml``) is only
exercised here. That path is type-sensitive: ``dump_yaml`` cannot represent
numpy scalars, and ``FFcSchedule`` time values are numpy-backed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance(name: str = "jc_step") -> FFcDDWParameters:
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
    instance: FFcDDWParameters, working_dir: Path | None = None
) -> FFcDDWSubroutineController:
    controller = FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "run_fam"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60}),
    )
    if working_dir is not None:
        controller.set_working_dir(working_dir)
    return controller


def test_requires_incumbent() -> None:
    controller = _make_controller(_make_instance())

    with pytest.raises(RuntimeError, match="requires an incumbent schedule"):
        controller.job_contrib_cp(jd_target=1)


def test_invalid_jd_target_rejected_before_solve() -> None:
    controller = _make_controller(_make_instance())
    controller.run_fam()

    with pytest.raises(ValueError, match="must be > 0|must be ≥ 1"):
        controller.job_contrib_cp(jd_target=0)


def test_registers_once_and_does_not_worsen(tmp_path: Path) -> None:
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    seed_report = controller.run_fam()
    assert seed_report.obj_value is not None
    history_len_before = len(controller.solution_manager.history)

    report = controller.job_contrib_cp(jd_target=2, cp_tl=5.0)

    assert len(controller.solution_manager.history) == history_len_before + 1
    assert report.obj_value is not None
    assert report.obj_value <= seed_report.obj_value + 1e-6
    # Profile-fixed bounds are not global; only the all-on-time early exit
    # reports a bound, and this fixture has positive contributions.
    assert report.obj_bound is None


def test_metrics_yaml_is_written_and_loadable(tmp_path: Path) -> None:
    """Regression: ``metrics["makespan"]`` used to be a numpy scalar, which made
    ``dump_yaml`` raise ``RepresenterError`` and fail the whole step."""
    from routix.io.yaml import load_yaml

    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()
    controller.job_contrib_cp(jd_target=2, cp_tl=5.0)

    metrics_files = list(tmp_path.rglob("*_metrics.yaml"))
    assert metrics_files, f"no _metrics.yaml written under {tmp_path}"

    metrics = load_yaml(metrics_files[0])
    for key in (
        "jd_count_target",
        "jd_count_eff",
        "positive_contrib_job_count",
        "incumbent_obj",
        "selected_jobs",
        "cp_progress",
    ):
        assert key in metrics, f"missing metrics key {key!r}: {metrics}"
    assert metrics["jd_count_target"] == 2
    assert 0 <= metrics["jd_count_eff"] <= metrics["jd_count_target"]
    assert isinstance(metrics["selected_jobs"], list)
    assert isinstance(metrics["makespan"], int)


def test_jd_target_ratio_expression(tmp_path: Path) -> None:
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    controller.job_contrib_cp(jd_target="0.5n", cp_tl=5.0)

    metrics_files = list(tmp_path.rglob("*_metrics.yaml"))
    assert metrics_files
    from routix.io.yaml import load_yaml

    metrics = load_yaml(metrics_files[0])
    assert metrics["jd_count_target"] == 2  # ceil(4 * 0.5)


def test_logs_solver_status(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Controller must log work_status and cpsat_status after solve."""
    caplog.set_level(logging.INFO)
    controller = _make_controller(_make_instance(), working_dir=tmp_path)
    controller.run_fam()

    controller.job_contrib_cp(jd_target=2, cp_tl=5.0)

    combined = "\n".join(caplog.messages)
    assert "work_status=" in combined, (
        f"work_status not found in log messages: {caplog.messages}"
    )
    assert "cpsat_status=" in combined, (
        f"cpsat_status not found in log messages: {caplog.messages}"
    )
