"""Tests for neh_cp_*_seq step methods that derive job sequence from incumbent."""

from __future__ import annotations

import logging
import re

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
# that lets all three modes disagree: with two stages first_stage and
# completion are still distinguishable but harder to separate from midpoint.
#
#   stage | operations (start, end)                              | idle
#   ------+------------------------------------------------------+-----
#   i0    | j0(0,2)   j1(4,6)   j2(8,10)  j3(12,14)               |  6
#   i1    | j3(14,16) j2(16,18) j1(18,20) j0(20,22)               |  0
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
        if "dist_to_midpoint=" in message and "dist_to_first_stage=" in message
    ]
    assert len(diag_lines) == 1


def test_diagnostic_line_still_matches_the_analysis_script_regex(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``scripts/20260801/analyze_neh_pass_chain.py`` parses this line.

    It requires ``dist_to_job_priority=`` and ``dist_to_prev_neh=`` to be
    adjacent and in that order, so reformatting the log silently breaks every
    pass-chain analysis. Kept in sync with that script's ``DIAG_RE``.
    """
    diag_re = re.compile(
        r"(?P<step>neh_cp_\w+?_seq): seq source=(?P<mode>\w+) .*?"
        r"dist_to_job_priority=(?P<job_priority>[\d.]+) "
        r"dist_to_prev_neh=(?P<prev_neh>[\d.]+|N/A)"
    )

    caplog.set_level(logging.INFO)
    _controller_with_incumbent("neh_cp_midpoint_seq").run()

    matches = [m for msg in caplog.messages if (m := diag_re.search(msg))]
    assert len(matches) == 1, "the pass-chain regex no longer matches the diag line"
    assert matches[0].group("step") == "neh_cp_midpoint_seq"
    assert matches[0].group("mode") == "midpoint"


def test_job_batch_cp_diagnostic_is_excluded_from_the_neh_regex(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The shared sequence helper logs the same format for job_batch_cp.

    That is deliberate, and so is the fact that ``neh_cp_\\w+?_seq`` does not
    match it — the pass-chain analysis must keep counting NEH passes only.
    """
    diag_re = re.compile(r"(?P<step>neh_cp_\w+?_seq): seq source=")

    caplog.set_level(logging.INFO)
    _controller_with_incumbent("job_batch_cp_midpoint_seq").run()

    diag_lines = [m for m in caplog.messages if "dist_to_job_priority=" in m]
    assert len(diag_lines) == 1, "job_batch_cp must log the same diagnostic"
    assert diag_lines[0].startswith("job_batch_cp_midpoint_seq: seq source=midpoint ")
    assert not any(diag_re.search(m) for m in caplog.messages)


# ── _step_log.yaml mapping format for new methods ────────────────────────────
def test_step_log_yaml_mapping_format(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller_with_incumbent("neh_cp_completion_seq")
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )

    controller.run()

    assert log_path.exists()
    data = load_yaml(log_path)
    assert data["job_sequence_source"] == "completion"
    assert data["job_sequence_fallback"] is False
    assert data["job_sequence"] == list(_EXPECTED_SEQUENCE["neh_cp_completion_seq"])
    assert isinstance(data["steps"], list)


def test_step_log_yaml_keeps_mapping_format_on_fallback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``*_seq`` step that fell back still writes the mapping.

    The plain-list format belongs to plain ``neh_cp`` only; branching on the
    *outcome* instead of the request would drop ``job_sequence_fallback``
    exactly on the runs where it is the thing worth reading.
    """
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "neh_cp_midpoint_seq"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )

    controller.run()

    data = load_yaml(log_path)
    assert isinstance(data, dict), "fallback must not degrade to the plain-list format"
    assert data["job_sequence_fallback"] is True
    assert data["job_sequence_source"] == "job_priority:weight-due-pos"
    assert data["job_sequence_tiebreak"] is None
    assert data["job_sequence_end_stage"] is None


def test_plain_neh_cp_step_log_stays_a_list(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller_with_incumbent("neh_cp")
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )

    controller.run()

    assert isinstance(load_yaml(log_path), list)


def test_new_methods_pass_routix_flow_validator() -> None:
    from routix.dynamic_data_object import DynamicDataObject
    from routix.subroutine_flow_validator import SubroutineFlowValidator

    for method in sorted(_EXPECTED_SEQUENCE):
        flow = DynamicDataObject.from_obj([{"method": method}])
        validator = SubroutineFlowValidator(
            controller_class=FFcDDWSubroutineController,
        )
        validator.validate(flow)


# ── seq_tiebreak parameter ────────────────────────────────────────────────────
def test_midpoint_seq_tiebreak_completion_passes_correct_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """neh_cp_midpoint_seq with seq_tiebreak='completion' reverses the m-tied
    j2/j3 pair relative to the default."""
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[
            {"method": "neh_cp_midpoint_seq", "seq_tiebreak": "completion"}
        ],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    schedule = _make_incumbent_schedule(controller.instance)
    controller.solution_manager.register(
        controller._wrap_report(
            SubroutineReport(elapsed_time=0.0, obj_value=100.0, obj_bound=None)
        ),
        FFcDDWSolution(schedule=schedule, obj_value=100.0),
    )
    option = _run_capturing_option(controller, monkeypatch)
    assert option.custom_job_sequence == ("j1", "j0", "j3", "j2")


def test_midpoint_seq_tiebreak_default_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """seq_tiebreak=None keeps the original order."""
    option = _run_capturing_option(
        _controller_with_incumbent("neh_cp_midpoint_seq"), monkeypatch
    )
    assert option.custom_job_sequence == _EXPECTED_SEQUENCE["neh_cp_midpoint_seq"]


def test_seq_tiebreak_flow_passes_validator() -> None:
    """subroutine_flow with seq_tiebreak passes routix validation."""
    from routix.dynamic_data_object import DynamicDataObject
    from routix.subroutine_flow_validator import SubroutineFlowValidator

    flow = DynamicDataObject.from_obj(
        [{"method": "neh_cp_midpoint_seq", "seq_tiebreak": "completion"}]
    )
    validator = SubroutineFlowValidator(
        controller_class=FFcDDWSubroutineController,
    )
    validator.validate(flow)


def test_falls_back_with_seq_tiebreak_when_no_incumbent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """seq_tiebreak is irrelevant when no incumbent — falls back to job_priority."""
    caplog.set_level(logging.WARNING)
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[
            {"method": "neh_cp_midpoint_seq", "seq_tiebreak": "completion"}
        ],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    option = _run_capturing_option(controller, monkeypatch)
    assert option.custom_job_sequence is None
    assert any("no incumbent schedule" in message for message in caplog.messages)


def test_step_log_yaml_includes_tiebreak(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_step_log.yaml includes job_sequence_tiebreak when seq_tiebreak is set."""
    from routix.io import load_yaml

    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[
            {"method": "neh_cp_midpoint_seq", "seq_tiebreak": "completion"}
        ],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    schedule = _make_incumbent_schedule(controller.instance)
    controller.solution_manager.register(
        controller._wrap_report(
            SubroutineReport(elapsed_time=0.0, obj_value=100.0, obj_bound=None)
        ),
        FFcDDWSolution(schedule=schedule, obj_value=100.0),
    )
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )
    controller.run()
    assert log_path.exists()
    data = load_yaml(log_path)
    assert data["job_sequence_tiebreak"] == "completion"
    assert data["job_sequence_fallback"] is False
    assert data["job_sequence"] == ["j1", "j0", "j3", "j2"]


def test_step_log_yaml_tiebreak_null_when_not_set(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_step_log.yaml has job_sequence_tiebreak=null when seq_tiebreak is not set."""
    from routix.io import load_yaml

    controller = _controller_with_incumbent("neh_cp_midpoint_seq")
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )
    controller.run()
    data = load_yaml(log_path)
    assert data["job_sequence_tiebreak"] is None


# ── seq_end_stage parameter ──────────────────────────────────────────────────
# Fixture where midpoint3 differs from midpoint(-1) and first_stage.
# i1 is back-to-back (idle 0) so i1 order = bottleneck order;
# midpoint(-2) with tiebreak="completion" gives [j1,j0,j3,j2].
_MIDPOINT3_OPS: list[tuple[str, str, int, int]] = [
    ("i0", "j0", 0, 2),
    ("i0", "j1", 2, 4),
    ("i0", "j2", 6, 8),
    ("i0", "j3", 8, 10),
    ("i1", "j1", 4, 8),
    ("i1", "j0", 8, 10),
    ("i1", "j3", 10, 12),
    ("i1", "j2", 12, 14),
    ("i2", "j0", 10, 12),
    ("i2", "j1", 12, 14),
    ("i2", "j3", 14, 16),
    ("i2", "j2", 16, 18),
]

_MIDPOINT3_EXPECTED: tuple[str, ...] = ("j1", "j0", "j3", "j2")


def _build_midpoint3_incumbent(controller: FFcDDWSubroutineController) -> None:
    sch = FFcSchedule(
        jobs=controller.instance.job_id_list,
        stages=controller.instance.stage_id_list,
        machines_per_stage=controller.instance.stage_2_machines_map,
    )
    for stage_id, job_id, start, end in _MIDPOINT3_OPS:
        mc_id = controller.instance.stage_2_machines_map[stage_id][0]
        sch.add_ops_times_2_mc(stage_id, mc_id, job_id, start, end)
    controller.solution_manager.register(
        controller._wrap_report(
            SubroutineReport(elapsed_time=0.0, obj_value=100.0, obj_bound=None)
        ),
        FFcDDWSolution(schedule=sch, obj_value=100.0),
    )


def test_completion_seq_with_end_stage_minus2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """neh_cp_completion_seq with seq_end_stage=-2 sorts by (last-1) end."""
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "neh_cp_completion_seq", "seq_end_stage": -2}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    schedule = _make_incumbent_schedule(controller.instance)
    controller.solution_manager.register(
        controller._wrap_report(
            SubroutineReport(elapsed_time=0.0, obj_value=100.0, obj_bound=None)
        ),
        FFcDDWSolution(schedule=schedule, obj_value=100.0),
    )
    option = _run_capturing_option(controller, monkeypatch)
    assert option.custom_job_sequence == ("j3", "j2", "j1", "j0")


def test_midpoint3_seq_differs_from_midpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """midpoint with seq_end_stage=-2 and seq_tiebreak='completion' produces a
    different order from midpoint(-1)."""
    controller = FFcDDWSubroutineController(
        instance=_make_instance(name="midpoint3_test"),
        subroutine_flow=[
            {
                "method": "neh_cp_midpoint_seq",
                "seq_end_stage": -2,
                "seq_tiebreak": "completion",
            }
        ],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    _build_midpoint3_incumbent(controller)
    option = _run_capturing_option(controller, monkeypatch)
    assert option.custom_job_sequence == _MIDPOINT3_EXPECTED
    assert option.custom_job_sequence != _EXPECTED_SEQUENCE["neh_cp_midpoint_seq"]


def test_seq_end_stage_minus1_is_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """seq_end_stage=-1 produces the same order as omitting it."""
    option = _run_capturing_option(
        _controller_with_incumbent("neh_cp_completion_seq"), monkeypatch
    )
    assert option.custom_job_sequence == _EXPECTED_SEQUENCE["neh_cp_completion_seq"]


def test_seq_end_stage_clamp_and_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """abs(seq_end_stage) > c is clamped to -c with a warning."""
    caplog.set_level(logging.WARNING)
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "neh_cp_midpoint_seq", "seq_end_stage": -99}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    schedule = _make_incumbent_schedule(controller.instance)
    controller.solution_manager.register(
        controller._wrap_report(
            SubroutineReport(elapsed_time=0.0, obj_value=100.0, obj_bound=None)
        ),
        FFcDDWSolution(schedule=schedule, obj_value=100.0),
    )
    captured: dict[str, NehCpOption] = {}
    original_run = NehCpDispatcher.run

    def capture(self_disp, spec):
        captured["option"] = spec.option
        return original_run(self_disp, spec)

    monkeypatch.setattr(NehCpDispatcher, "run", capture)
    controller.run()
    assert any("clamping" in message for message in caplog.messages)


def test_seq_end_stage_fallback_when_no_incumbent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """seq_end_stage is ignored when no incumbent — falls back to job_priority."""
    caplog.set_level(logging.WARNING)
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "neh_cp_completion_seq", "seq_end_stage": -2}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    option = _run_capturing_option(controller, monkeypatch)
    assert option.custom_job_sequence is None
    assert any("no incumbent schedule" in message for message in caplog.messages)


def test_seq_end_stage_flow_passes_validator() -> None:
    """subroutine_flow with seq_end_stage passes routix validation."""
    from routix.dynamic_data_object import DynamicDataObject
    from routix.subroutine_flow_validator import SubroutineFlowValidator

    for method in ("neh_cp_midpoint_seq", "neh_cp_completion_seq"):
        flow = DynamicDataObject.from_obj([{"method": method, "seq_end_stage": -2}])
        validator = SubroutineFlowValidator(
            controller_class=FFcDDWSubroutineController,
        )
        validator.validate(flow)


def test_diag_log_format_still_matches_regex(
    caplog: pytest.LogCaptureFixture,
) -> None:
    r"""The diagnostic log line still matches DIAG_RE in analyze_neh_pass_chain.py.

    Regex (from scripts/20260801/analyze_neh_pass_chain.py:94):
        r"(?P<step>neh_cp_\w+?_seq): seq source=(?P<mode>\w+) .*?"
        r"dist_to_job_priority=(?P<job_priority>[\d.]+) "
        r"dist_to_prev_neh=(?P<prev_neh>[\d.]+|N/A)"
    """
    import re

    DIAG_RE = re.compile(
        r"(?P<step>neh_cp_\w+?_seq): seq source=(?P<mode>\w+) .*?"
        r"dist_to_job_priority=(?P<job_priority>[\d.]+) "
        r"dist_to_prev_neh=(?P<prev_neh>[\d.]+|N/A)"
    )
    caplog.set_level(logging.INFO)
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[
            {
                "method": "neh_cp_midpoint_seq",
                "seq_end_stage": -2,
                "seq_tiebreak": "completion",
            }
        ],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    schedule = _make_incumbent_schedule(controller.instance)
    controller.solution_manager.register(
        controller._wrap_report(
            SubroutineReport(elapsed_time=0.0, obj_value=100.0, obj_bound=None)
        ),
        FFcDDWSolution(schedule=schedule, obj_value=100.0),
    )
    controller.run()

    match = DIAG_RE.search("\n".join(caplog.messages))
    assert match is not None, (
        "DIAG_RE did not match any log line.\nMessages:\n" + "\n".join(caplog.messages)
    )
    assert match.group("step") == "neh_cp_midpoint_seq"
    assert match.group("mode") == "midpoint"


def test_step_log_yaml_includes_end_stage(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_step_log.yaml includes job_sequence_end_stage when set."""
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "neh_cp_completion_seq", "seq_end_stage": -2}],
        stopping_criteria=StoppingCriteria({"timelimit": 60.0}),
    )
    schedule = _make_incumbent_schedule(controller.instance)
    controller.solution_manager.register(
        controller._wrap_report(
            SubroutineReport(elapsed_time=0.0, obj_value=100.0, obj_bound=None)
        ),
        FFcDDWSolution(schedule=schedule, obj_value=100.0),
    )
    log_path = tmp_path / "_step_log.yaml"
    monkeypatch.setattr(
        controller, "try_get_file_path_for_subroutine", lambda *a, **k: log_path
    )
    controller.run()
    assert log_path.exists()
    data = load_yaml(log_path)
    assert data["job_sequence_end_stage"] == -2
    assert data["job_sequence_fallback"] is False
