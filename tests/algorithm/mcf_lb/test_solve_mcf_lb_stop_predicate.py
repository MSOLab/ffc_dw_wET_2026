"""Direct verification of solve_mcf_lb's stop_predicate path.

The catch site in ``controller.apply_lb_by_mcf`` is essentially
unreachable in normal subroutine_flow execution because routix's
``_run_flow`` checks ``is_stopping_condition()`` before each step (see
``routix/subroutine_controller.py:172``). These tests bypass that
pre-step guard so the raise → catch path can be deterministically
exercised.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
import pytest
from routix.report import SubroutineReport
from routix.stopping_criteria import StoppingCriteria

from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import (
    MCFLBStopRequested,
    solve_mcf_lb,
)
from ffc_ddw_sum_et.orchestration.controller import FFcDDWSubroutineController
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _make_instance(name: str = "mcflbstop_test") -> FFcDDWParameters:
    return FFcDDWParameters(
        name=name,
        job_id_list=["j0"],
        stage_id_list=["i0"],
        stage_2_machines_map={"i0": ["i0_0"]},
        p_manager=JobStageProcessingTimeManager(
            name=f"{name}_p",
            df=pd.DataFrame([[1]]),
        ),
        job_2_due_window_map={"j0": (0, 1)},
        job_2_ewt_map={"j0": 1},
        job_2_twt_map={"j0": 1},
    )


def test_solve_mcf_lb_raises_mcflbstoprequested_on_true_predicate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``stop_predicate`` returns True at entry, ``solve_mcf_lb``
    must raise ``MCFLBStopRequested`` *before* invoking ``mcf.solve()``
    and emit the new INFO log line on the supplied logger.
    """
    instance = _make_instance()
    test_logger = logging.getLogger("test_solve_mcf_lb_stop_predicate")

    with caplog.at_level(logging.INFO, logger=test_logger.name):
        with pytest.raises(MCFLBStopRequested):
            solve_mcf_lb(
                instance,
                stop_predicate=lambda: True,
                logger=test_logger,
            )

    # The INFO log fired (proves we hit the predicate-True branch,
    # not some downstream LP failure).
    assert any(
        "solve_mcf_lb: stop_predicate True before LP solve" in rec.message
        for rec in caplog.records
    ), f"expected raise log; got {[r.message for r in caplog.records]}"


def test_apply_lb_by_mcf_catches_mcflbstoprequested_with_stale_timer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``apply_lb_by_mcf`` is invoked with a timer already past the
    timelimit (bypassing routix's pre-step guard via direct call), the
    inner ``solve_mcf_lb`` must raise ``MCFLBStopRequested`` and the
    catch block must return a clean stop-report.

    Verifies both:
      - new INFO log: ``solve_mcf_lb: stop_predicate True before LP solve``
      - existing INFO log: ``apply_lb_by_mcf: stop predicate fired before MCF solve``
      - new INFO log: ``_make_stop_report: reason=timelimit``
    """
    instance = _make_instance()
    controller = FFcDDWSubroutineController(
        instance=instance,
        subroutine_flow=[{"method": "run_fam"}],
        stopping_criteria=StoppingCriteria({"timelimit": 1.0}),
    )
    # Push the timer far into the past so is_stopping_condition() returns
    # True the moment apply_lb_by_mcf calls solve_mcf_lb.
    controller.timer.set_start_time(datetime.now() - timedelta(seconds=1000))

    with caplog.at_level(logging.INFO, logger=controller.logger.name):
        report = controller.apply_lb_by_mcf()

    assert isinstance(report, SubroutineReport)
    assert report.obj_value is None  # stop-report shape

    messages = [rec.message for rec in caplog.records]
    assert any(
        "solve_mcf_lb: stop_predicate True before LP solve" in m for m in messages
    ), f"expected solve_mcf_lb raise log; got {messages}"
    assert any(
        "apply_lb_by_mcf: stop predicate fired before MCF solve" in m for m in messages
    ), f"expected apply_lb_by_mcf catch log; got {messages}"
    assert any("_make_stop_report: reason=timelimit" in m for m in messages), (
        f"expected _make_stop_report log; got {messages}"
    )
