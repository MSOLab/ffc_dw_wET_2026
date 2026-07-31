"""Tests for neh_cp_*_seq step methods that derive job sequence from incumbent."""

from __future__ import annotations

import logging

import pandas as pd
import pytest
from routix.io import load_yaml
from routix.report import SubroutineReport
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.neh_cp.dispatcher import NehCpDispatcher
from ffc_ddw_sum_et.algorithm.neh_cp.option import NehCpOption
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.orchestration.solution_manager import FFcDDWSolution
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule

# Staggered incumbent used by every test below. Three stages are the minimum
# that lets all four modes disagree: with two stages the bottleneck is either
# the first or the last stage, so its order necessarily coincides with
# `first_stage` or `completion`.
#
#   stage | operations (start, end)                              | idle
#   ------+------------------------------------------------------+-----
#   i0    | j0(0,2)   j1(4,6)   j2(8,10)  j3(12,14)               |  6
#   i1    | j3(14,16) j2(16,18) j1(18,20) j0(20,22)               |  0  ← bottleneck
#   i2    | j1(22,24) j3(25,27) j2(29,31) j0(33,35)               |  5
#
# midpoint  = (i0 start + i2 end)/2 → j1 14.0, j0 17.5, j2 19.5, j3 19.5
#             (the j2/j3 tie is broken by the secondary key, i0 start)
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

_EXPECTED_SEQUENCE: dict[str, tuple[str, ...]] = {
    "neh_cp_midpoint_seq": ("j1", "j0", "j2", "j3"),
    "neh_cp_first_stage_seq": ("j0", "j1", "j2", "j3"),
    "neh_cp_bottleneck_seq": ("j3", "j2", "j1", "j0"),
    "neh_cp_completion_seq": ("j1", "j3", "j2", "j0"),
}


def _make_instance(name: str = "neh_cp_seq_test") -> FFcDDWParameters:
    """4-job, 3-stage, m=1 instance with uniform processing times."""
    job_id_list = ["j0", "j1", "j2", "j3"]
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=["i0", "i1", "i2"],
        stage_2_machines_map={"i0": ["i0_0"], "i1": ["i1_0"], "i2": ["i2_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 2, 2]] * len(job_id_list)),
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


def _make_incumbent_schedule(
    instance: FFcDDWParameters, skipped_jobs: set[str] | None = None
) -> FFcSchedule:
    """Build the staggered schedule above.

    ``skipped_jobs`` leaves those jobs listed in ``schedule.jobs`` while
    carrying no operation — the partial-schedule shape produced by
    ``remove_jobs`` / ``deepcopy(job_subset=...)``.
    """
    skipped = skipped_jobs or set()
    sch = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    for stage_id, job_id, start, end in _INCUMBENT_OPS:
        if job_id in skipped:
            continue
        mc_id = instance.stage_2_machines_map[stage_id][0]
        sch.add_ops_times_2_mc(stage_id, mc_id, job_id, start, end)
    return sch


def _controller_with_incumbent(
    method: str, skipped_jobs: set[str] | None = None
) -> FFcDDWSubroutineController:
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": method}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    schedule = _make_incumbent_schedule(controller.instance, skipped_jobs)
    controller.solution_manager.register(
        controller._wrap_report(
            SubroutineReport(elapsed_time=0.0, obj_value=100.0, obj_bound=None)
        ),
        FFcDDWSolution(schedule=schedule, obj_value=100.0),
    )
    return controller


def _run_capturing_option(
    controller: FFcDDWSubroutineController, monkeypatch: pytest.MonkeyPatch
) -> NehCpOption:
    """Run the flow and return the option the dispatcher was called with."""
    captured: dict[str, NehCpOption] = {}
    original_run = NehCpDispatcher.run

    def capture(self_disp, spec):
        captured["option"] = spec.option
        return original_run(self_disp, spec)

    monkeypatch.setattr(NehCpDispatcher, "run", capture)
    controller.run()
    assert "option" in captured, "NehCpDispatcher.run was never called"
    return captured["option"]


# ── incumbent presence → mode-specific custom_job_sequence ───────────────────
@pytest.mark.parametrize("method", sorted(_EXPECTED_SEQUENCE))
def test_each_seq_method_passes_its_own_mode_order(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each step method must hand the dispatcher the order for *its* mode.

    The four expected sequences are pairwise distinct on this incumbent, so
    wiring a method to the wrong mode fails here.
    """
    option = _run_capturing_option(_controller_with_incumbent(method), monkeypatch)
    assert option.custom_job_sequence == _EXPECTED_SEQUENCE[method]


def test_expected_sequences_are_pairwise_distinct() -> None:
    """Guards the fixture itself: if the incumbent ever stops separating the
    modes, the parametrized test above would silently stop discriminating."""
    assert len(set(_EXPECTED_SEQUENCE.values())) == len(_EXPECTED_SEQUENCE)


# ── partial incumbent → sequence corrected to a full permutation ─────────────
def test_missing_job_is_appended_in_job_priority_order(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A job with no operations is skipped by the extractor, then appended by
    the controller so the dispatcher still receives a permutation."""
    caplog.set_level(logging.WARNING)
    controller = _controller_with_incumbent("neh_cp_midpoint_seq", skipped_jobs={"j2"})
    option = _run_capturing_option(controller, monkeypatch)

    assert option.custom_job_sequence == ("j1", "j0", "j3", "j2")
    assert any("correcting" in message for message in caplog.messages)


# ── no incumbent → fallback to job_priority ──────────────────────────────────
def test_falls_back_to_job_priority_when_no_incumbent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "neh_cp_midpoint_seq"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    option = _run_capturing_option(controller, monkeypatch)

    assert option.custom_job_sequence is None
    assert any("no incumbent schedule" in message for message in caplog.messages)


# ── diversity diagnostic logging ─────────────────────────────────────────────
def test_diversity_diagnostic_logged_when_incumbent_exists(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    _controller_with_incumbent("neh_cp_midpoint_seq").run()

    diag_lines = [
        message
        for message in caplog.messages
        if "dist_to_midpoint=" in message and "dist_to_bottleneck=" in message
    ]
    assert len(diag_lines) == 1


# ── _step_log.yaml mapping format for new methods ────────────────────────────
def test_step_log_yaml_mapping_format(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller_with_incumbent("neh_cp_bottleneck_seq")
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )

    controller.run()

    assert log_path.exists()
    data = load_yaml(log_path)
    assert data["job_sequence_source"] == "bottleneck"
    assert data["job_sequence_fallback"] is False
    assert data["job_sequence"] == list(_EXPECTED_SEQUENCE["neh_cp_bottleneck_seq"])
    assert isinstance(data["steps"], list)


def test_new_methods_pass_routix_flow_validator() -> None:
    from routix.dynamic_data_object import DynamicDataObject
    from routix.subroutine_flow_validator import SubroutineFlowValidator

    for method in sorted(_EXPECTED_SEQUENCE):
        flow = DynamicDataObject.from_obj([{"method": method}])
        validator = SubroutineFlowValidator(
            controller_class=FFcDDWSubroutineController,
        )
        validator.validate(flow)
