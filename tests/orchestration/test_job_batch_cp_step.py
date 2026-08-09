"""Tests for the four ``job_batch_cp*`` controller step methods."""

from __future__ import annotations

import time

import pandas as pd
import pytest
from routix.io import load_yaml
from routix.report import SubroutineReport
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.job_batch_cp import (
    JobBatchCpDispatcher,
    JobBatchCpOption,
)
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.orchestration.solution_manager import FFcDDWSolution
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule

# Same staggered 3-stage incumbent as tests/orchestration/
# test_neh_cp_incumbent_sequence.py. Three stages are the minimum that keeps
# the three schedule-derived modes apart; with two they collapse and a
# mis-wired method would still pass.
#
#   stage | operations (start, end)
#   ------+-------------------------------------------------
#   i0    | j0(0,2)   j1(4,6)   j2(8,10)  j3(12,14)
#   i1    | j3(14,16) j2(16,18) j1(18,20) j0(20,22)
#   i2    | j1(22,24) j3(25,27) j2(29,31) j0(33,35)
_INCUMBENT_OPS: list[tuple[str, str, int, int]] = [
    ("i0", "j0", 0, 2),
    ("i0", "j1", 4, 6),
    ("i0", "j2", 8, 10),
    ("i0", "j3", 12, 14),
    ("i1", "j3", 14, 16),
    ("i1", "j2", 16, 18),
    ("i1", "j1", 18, 20),
    ("i1", "j0", 20, 22),
    ("i2", "j1", 22, 24),
    ("i2", "j3", 25, 27),
    ("i2", "j2", 29, 31),
    ("i2", "j0", 33, 35),
]

# ``job_batch_cp`` has no schedule-derived source, so it must land on the
# instance's job_priority order. Under "due2-weight-pos" that order is
# distinct from all three derived orders — see
# ``test_expected_sequences_are_pairwise_distinct``.
_JOB_PRIORITY = "due2-weight-pos"

_EXPECTED_SEQUENCE: dict[str, tuple[str, ...]] = {
    "job_batch_cp": ("j1", "j3", "j0", "j2"),
    "job_batch_cp_midpoint_seq": ("j1", "j0", "j2", "j3"),
    "job_batch_cp_first_stage_seq": ("j0", "j1", "j2", "j3"),
    "job_batch_cp_completion_seq": ("j1", "j3", "j2", "j0"),
}


def _make_instance(name: str = "job_batch_cp_test") -> FFcDDWParameters:
    job_id_list = ["j0", "j1", "j2", "j3"]
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=["i0", "i1", "i2"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"], "i2": ["i2_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p", df=pd.DataFrame([[2, 2, 2]] * len(job_id_list))
        ),
        job_2_due_window_map={
            "j0": (10, 12),
            "j1": (4, 6),
            "j2": (14, 16),
            "j3": (8, 10),
        },
        job_2_ewt_map={job_id: 1 for job_id in job_id_list},
        job_2_twt_map={job_id: 1 for job_id in job_id_list},
    )


def _make_incumbent_schedule(instance: FFcDDWParameters) -> FFcSchedule:
    sch = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    for stage_id, job_id, start, end in _INCUMBENT_OPS:
        sch.add_ops_times_2_mc(
            stage_id, instance.stage_2_machines_map[stage_id][0], job_id, start, end
        )
    return sch


def _make_controller(
    method: str, *, with_incumbent: bool = True, **step_kwargs
) -> FFcDDWSubroutineController:
    step: dict[str, object] = {"method": method, "job_priority": _JOB_PRIORITY}
    step.update(step_kwargs)
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[step],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    if with_incumbent:
        controller.solution_manager.register(
            controller._wrap_report(
                SubroutineReport(elapsed_time=0.0, obj_value=100.0, obj_bound=None)
            ),
            FFcDDWSolution(
                schedule=_make_incumbent_schedule(controller.instance),
                obj_value=100.0,
            ),
        )
    return controller


def _run_capturing_option(
    controller: FFcDDWSubroutineController,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sleep_sec: float = 0.0,
) -> JobBatchCpOption:
    """Run the flow and return the option the dispatcher was called with."""
    captured: dict[str, JobBatchCpOption] = {}
    original_run = JobBatchCpDispatcher.run

    def capture(self_disp, spec):
        captured["option"] = spec.option
        if sleep_sec:
            time.sleep(sleep_sec)
        return original_run(self_disp, spec)

    monkeypatch.setattr(JobBatchCpDispatcher, "run", capture)
    controller.run()
    assert "option" in captured, "JobBatchCpDispatcher.run was never called"
    return captured["option"]


# ── job sequence wiring ──────────────────────────────────────────────────────
@pytest.mark.parametrize("method", sorted(_EXPECTED_SEQUENCE))
def test_each_method_passes_its_own_mode_order(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    option = _run_capturing_option(_make_controller(method), monkeypatch)
    assert option.job_sequence == _EXPECTED_SEQUENCE[method]


def test_expected_sequences_are_pairwise_distinct() -> None:
    """Guards the fixture: if these ever coincide, the test above stops
    discriminating between the four methods."""
    assert len(set(_EXPECTED_SEQUENCE.values())) == len(_EXPECTED_SEQUENCE)


def test_midpoint_seq_honours_the_tiebreak_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """j2 and j3 share a midpoint of 19.5; the tiebreak decides their order.

    The default secondary key (i0 start) puts j2 first, completion order puts
    j3 first — so this separates "wired" from "accepted and ignored".
    """
    option = _run_capturing_option(
        _make_controller("job_batch_cp_midpoint_seq", seq_tiebreak="completion"),
        monkeypatch,
    )
    assert option.job_sequence == ("j1", "j0", "j3", "j2")
    assert option.job_sequence != _EXPECTED_SEQUENCE["job_batch_cp_midpoint_seq"]


def test_completion_seq_honours_the_end_stage_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``seq_end_stage=-2`` sorts by the i1 completion times, which order the
    jobs differently from the default last-stage sort."""
    option = _run_capturing_option(
        _make_controller("job_batch_cp_completion_seq", seq_end_stage=-2), monkeypatch
    )
    assert option.job_sequence == ("j3", "j2", "j1", "j0")
    assert option.job_sequence != _EXPECTED_SEQUENCE["job_batch_cp_completion_seq"]


# ── incumbent is required, never faked ───────────────────────────────────────
@pytest.mark.parametrize("method", sorted(_EXPECTED_SEQUENCE))
def test_missing_incumbent_raises(method: str) -> None:
    """Unlike ``neh_cp_*_seq``, this step has nothing to do without an
    incumbent — it repairs a schedule rather than building one."""
    controller = _make_controller(method, with_incumbent=False)
    with pytest.raises(RuntimeError, match="requires an incumbent schedule"):
        controller.run()


# ── batch_size expression resolution ─────────────────────────────────────────
@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (2, 2),
        ("2", 2),
        ("0.5n", 2),
        ("0.05n", 1),  # ceil, then floored at 1
    ],
)
def test_batch_size_expression_is_resolved(
    expr: object, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    option = _run_capturing_option(
        _make_controller("job_batch_cp_midpoint_seq", batch_size=expr), monkeypatch
    )
    assert option.batch_size == expected


# ── step contract ────────────────────────────────────────────────────────────
def test_registers_exactly_once_and_times_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller("job_batch_cp_midpoint_seq")
    calls: list[float] = []
    original_register = controller._register

    def counting_register(report, solution=None, **kwargs):
        calls.append(report.elapsed_time)
        return original_register(report, solution, **kwargs)

    monkeypatch.setattr(controller, "_register", counting_register)
    _run_capturing_option(controller, monkeypatch, sleep_sec=0.05)

    assert len(calls) == 1, f"expected exactly one _register, got {len(calls)}"
    assert calls[0] >= 0.05, "elapsed_time must cover the dispatcher call"


def test_methods_pass_routix_flow_validator() -> None:
    from routix.dynamic_data_object import DynamicDataObject
    from routix.subroutine_flow_validator import SubroutineFlowValidator

    flow = DynamicDataObject.from_obj(
        [
            {"method": "job_batch_cp", "batch_size": 2},
            {"method": "job_batch_cp_midpoint_seq", "seq_tiebreak": "completion"},
            {"method": "job_batch_cp_first_stage_seq", "pf_method": "PF1"},
            {"method": "job_batch_cp_completion_seq", "seq_end_stage": -2},
        ]
    )
    SubroutineFlowValidator(controller_class=FFcDDWSubroutineController).validate(flow)


# ── _step_log.yaml ───────────────────────────────────────────────────────────
def test_step_log_yaml_shape(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    controller = _make_controller(
        "job_batch_cp_midpoint_seq", batch_size=2, seq_tiebreak="completion"
    )
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )

    controller.run()

    data = load_yaml(log_path)
    assert data["job_sequence_source"] == "midpoint"
    assert data["job_sequence_tiebreak"] == "completion"
    assert data["job_sequence_end_stage"] == -1
    assert data["job_sequence_fallback"] is False
    assert data["job_sequence"] == ["j1", "j0", "j3", "j2"], (
        "the logged sequence must be the one actually used, tiebreak included"
    )
    assert data["batch_size"] == 2
    assert data["batch_count"] == 2
    assert len(data["steps"]) == 2
    assert [s["batch_head"] for s in data["steps"]] == ["j1", "j3"]


def test_step_log_batch_size_is_the_one_the_dispatcher_used(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``num_batches`` overrides ``batch_size`` inside the dispatcher, so the
    log must read the realized size back from it rather than report the
    controller's pre-resolved value."""
    controller = _make_controller(
        "job_batch_cp_midpoint_seq", batch_size=1, num_batches=3
    )
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )

    controller.run()

    data = load_yaml(log_path)
    # 4 jobs / 3 batches -> ceil(4/3) = 2 per batch, so 2 batches, not 3.
    assert data["batch_size"] == 2
    assert data["batch_count"] == 2
    assert [s["batch_size"] for s in data["steps"]] == [2, 2]


def test_plain_step_records_the_job_priority_source(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``job_batch_cp`` has no derived source, but the log must still name the
    rule that produced the batching order."""
    controller = _make_controller("job_batch_cp", batch_size=4)
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )

    controller.run()

    data = load_yaml(log_path)
    assert data["job_sequence_source"] == f"job_priority:{_JOB_PRIORITY}"
    assert data["job_sequence_tiebreak"] is None
    assert data["job_sequence"] == list(_EXPECTED_SEQUENCE["job_batch_cp"])


# ── end-to-end objective behaviour ───────────────────────────────────────────
def test_sweep_never_worsens_the_registered_incumbent() -> None:
    """§1.3 monotonicity, at the controller boundary."""
    controller = _make_controller("job_batch_cp_midpoint_seq", batch_size=2)
    before = controller.solution_manager.best_obj_value

    controller.run()

    assert controller.solution_manager.best_obj_value <= before
    assert controller.solution_manager.get_incumbent() is not None


# ── proportional per-batch TL ────────────────────────────────────────────────
@pytest.mark.parametrize("method", sorted(_EXPECTED_SEQUENCE))
def test_destroyed_op_tl_multiplier_reaches_the_option(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All four surfaces expose the ISW-CP-style proportional limit."""
    option = _run_capturing_option(
        _make_controller(
            method,
            batch_size=2,
            batch_tl_mode="proportional",
            destroyed_op_tl_multiplier=0.005,
        ),
        monkeypatch,
    )
    assert option.batch_tl_mode == "proportional"
    assert option.destroyed_op_tl_multiplier == 0.005


def test_proportional_without_multiplier_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller(
        "job_batch_cp_midpoint_seq", batch_size=2, batch_tl_mode="proportional"
    )
    with pytest.raises(ValueError, match="destroyed_op_tl_multiplier"):
        controller.run()


def test_proportional_flow_passes_routix_flow_validator() -> None:
    from routix.dynamic_data_object import DynamicDataObject
    from routix.subroutine_flow_validator import SubroutineFlowValidator

    flow = DynamicDataObject.from_obj(
        [
            {
                "method": "job_batch_cp_midpoint_seq",
                "batch_size": 15,
                "batch_tl_mode": "proportional",
                "destroyed_op_tl_multiplier": 0.005,
            }
        ]
    )
    SubroutineFlowValidator(controller_class=FFcDDWSubroutineController).validate(flow)
