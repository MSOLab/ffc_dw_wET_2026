"""``incremental_sw_cp(extra_batch_size_expr=...)`` — the ``m+2`` offset.

``resolve_value_expr`` has no arithmetic grammar, so ``batch_size="m+2"``
cannot parse.  ``incremental_sw_cp`` therefore mirrors ``neh_cp``'s existing
``extra_batch_size_expr`` parameter: ``batch_size="m"`` plus
``extra_batch_size_expr=2`` expresses ``m+2``.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.orchestration.value_resolver import resolve_value_expr
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

M = 2  # last-stage machine count of the fixture instance


def _make_instance(name: str = "isw_batch_instance") -> FFcDDWParameters:
    job_id_list = ["j0", "j1", "j2"]
    stage_id_list = ["i0", "i1"]
    return FFcDDWParameters(
        name=name,
        job_id_list=job_id_list,
        stage_id_list=stage_id_list,
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0", "i1_1"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[2, 3], [2, 2], [2, 1]]),
        ),
        # Unreachably tight windows keep the objective strictly positive, so
        # the fixture never trips the controller's optimality-proven stop.
        job_2_due_window_map={"j0": (1, 1), "j1": (1, 1), "j2": (1, 1)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _make_controller() -> FFcDDWSubroutineController:
    controller = FFcDDWSubroutineController(
        instance=_make_instance(),
        subroutine_flow=[{"method": "run_fam"}],
        stopping_criteria=StoppingCriteria({"timelimit": 60}),
    )
    controller.run_fam()  # incremental_sw_cp requires an incumbent
    return controller


def _captured_batch_sizes(
    controller: FFcDDWSubroutineController, monkeypatch: pytest.MonkeyPatch
) -> list[int]:
    """Stub out ``sw_cp`` and collect the ``batch_size`` it is handed."""
    seen: list[int] = []

    def _fake_sw_cp(*_args, **kwargs) -> None:
        seen.append(kwargs["batch_size"])

    monkeypatch.setattr(controller, "sw_cp", _fake_sw_cp)
    return seen


def test_m_plus_2_expression_does_not_parse() -> None:
    """The reason ``extra_batch_size_expr`` exists at all."""
    with pytest.raises(ValueError):
        resolve_value_expr("m+2", 3, 2, M)


def test_extra_batch_size_expr_adds_to_resolved_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller()
    seen = _captured_batch_sizes(controller, monkeypatch)

    controller.incremental_sw_cp(batch_size="m", extra_batch_size_expr=2)

    assert seen and all(size == M + 2 for size in seen)


def test_extra_batch_size_expr_none_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller()
    seen = _captured_batch_sizes(controller, monkeypatch)

    controller.incremental_sw_cp(batch_size="m")

    assert seen and all(size == M for size in seen)


def test_resolved_batch_size_is_floored_at_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _make_controller()
    seen = _captured_batch_sizes(controller, monkeypatch)

    controller.incremental_sw_cp(batch_size=0.1, extra_batch_size_expr=-5)

    assert seen and all(size == 1 for size in seen)


def test_log_reports_the_final_batch_size(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Otherwise every run log misattributes its own configuration."""
    controller = _make_controller()
    _captured_batch_sizes(controller, monkeypatch)

    with caplog.at_level(logging.INFO):
        controller.incremental_sw_cp(batch_size="m", extra_batch_size_expr=2)

    batch_size_lines = [
        record.getMessage()
        for record in caplog.records
        if "incremental_sw_cp: batch_size=" in record.getMessage()
    ]
    assert batch_size_lines
    assert all(f"-> {M + 2} " in line for line in batch_size_lines)
