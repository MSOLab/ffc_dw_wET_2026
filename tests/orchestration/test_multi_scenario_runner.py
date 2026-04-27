from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import InstanceResult
from ffc_ddw_sum_et.orchestration.reporting import (
    FFcDDWMultiScenarioRunner,
)


def _bare_runner(tmp_path: Path) -> FFcDDWMultiScenarioRunner:
    """Construct a runner bypassing __init__ for narrow unit tests."""
    runner = FFcDDWMultiScenarioRunner.__new__(FFcDDWMultiScenarioRunner)
    runner.output_dir = tmp_path
    runner.results = []
    runner.draw_gantt = False
    runner.painter_thread_cnt = 1
    runner.ins_index_source = None
    runner.bks_table_csv_path = None
    return runner


def test_run_captures_scenario_exception_as_none(tmp_path: Path) -> None:
    ok_result = [
        InstanceResult(
            instance_name="a",
            elapsed_time=0.1,
            obj_value=1.0,
            obj_bound=None,
            work_status="FEASIBLE",
        )
    ]
    runner = _bare_runner(tmp_path)
    runner.runners = [
        Mock(run=Mock(side_effect=RuntimeError("boom"))),
        Mock(run=Mock(return_value=ok_result)),
    ]
    runner.scenario_configs = [{}, {}]
    runner.scenario_names = ["failing", "ok"]

    with patch.object(FFcDDWMultiScenarioRunner, "post_run_process", return_value=None):
        runner.run()

    assert runner.results == [None, ok_result]


def test_post_run_process_handles_none_result(tmp_path: Path) -> None:
    runner = _bare_runner(tmp_path)
    ok_result = [
        InstanceResult(
            instance_name="a",
            elapsed_time=0.1,
            obj_value=1.0,
            obj_bound=None,
            work_status="FEASIBLE",
        )
    ]
    failing_inner = Mock()
    failing_inner.output_dir = tmp_path / "failing"
    ok_inner = Mock()
    ok_inner.output_dir = tmp_path / "ok"
    runner.runners = [failing_inner, ok_inner]
    runner.results = [None, ok_result]
    runner.scenario_configs = [{}, {}]
    runner.scenario_names = ["failing", "ok"]

    final = runner.post_run_process()

    assert len(final.scenario_results) == 2
    assert final.scenario_results[0].name == "failing"
    assert final.scenario_results[0].instance_results == []
    assert final.scenario_results[1].name == "ok"
    assert final.scenario_results[1].instance_results == ok_result
