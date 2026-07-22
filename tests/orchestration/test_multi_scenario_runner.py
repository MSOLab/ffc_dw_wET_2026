from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import yaml
from routix.type_defs import RunMode

from ffc_ddw_sum_et.orchestration.artifact_layout import FFcArtifactLayout
from ffc_ddw_sum_et.orchestration.ffcddw_multi_instance_runner import (
    FFcDDWMultiInstanceRunner,
)
from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import (
    FFcDDWSingleInstanceRunner,
    InstanceResult,
)
from ffc_ddw_sum_et.orchestration.reporting import (
    FFcDDWMultiScenarioRunner,
)


def _bare_runner(tmp_path: Path) -> FFcDDWMultiScenarioRunner:
    """Construct a runner bypassing __init__ for narrow unit tests."""
    runner = FFcDDWMultiScenarioRunner.__new__(FFcDDWMultiScenarioRunner)
    runner.output_dir = tmp_path
    runner.results = []
    runner.mode = RunMode.FULL_RUN
    runner.draw_gantt = False
    runner.draw_progress_plot = False
    runner.painter_thread_cnt = 1
    runner.ins_index_source = None
    runner.bks_table_csv_path = None
    runner._setup_logging_args = None
    runner.layout = FFcArtifactLayout(run_root=tmp_path / "run", run_id="run")
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
        with patch("ffc_ddw_sum_et.orchestration.reporting.setup_logging"):
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

    with (
        patch("ffc_ddw_sum_et.orchestration.reporting.FFcDDWReporter") as reporter_cls,
        patch("ffc_ddw_sum_et.orchestration.reporting.setup_logging"),
    ):
        reporter_cls.return_value.generate.return_value = None
        final = runner.post_run_process()

    assert len(final.scenario_results) == 2
    assert final.scenario_results[0].name == "failing"
    assert final.scenario_results[0].instance_results == []
    assert final.scenario_results[1].name == "ok"
    assert final.scenario_results[1].instance_results == ok_result


# ---------------------------------------------------------------------------
# FFcDDWMultiInstanceRunner._load_resume_data
# ---------------------------------------------------------------------------


def test_load_resume_data_injects_solution(tmp_path: Path) -> None:
    """_load_resume_data reads solution JSON + manifest YAML from resume_root
    and injects resume_solution / resume_elapsed_time into each runner."""
    resume_root = tmp_path / "base"

    instances = [
        SimpleNamespace(name="ins_a"),
        SimpleNamespace(name="ins_b"),
    ]
    runners: list[FFcDDWSingleInstanceRunner] = []
    for ins in instances:
        sir = FFcDDWSingleInstanceRunner.__new__(FFcDDWSingleInstanceRunner)
        sir.resume_solution = None
        sir.resume_elapsed_time = None
        runners.append(sir)

        inst_dir = resume_root / ins.name
        inst_dir.mkdir(parents=True, exist_ok=True)

        sol_json = {
            "jobs": ["j0", "j1"],
            "stages": ["i0", "i1"],
            "machinesPerStage": {"i0": ["i0_0"], "i1": ["i1_0"]},
            "operations": [
                {"job": "j0", "stage": "i0", "machine": "i0_0", "start": 0, "end": 5},
                {"job": "j0", "stage": "i1", "machine": "i1_0", "start": 5, "end": 8},
                {"job": "j1", "stage": "i0", "machine": "i0_0", "start": 5, "end": 10},
                {"job": "j1", "stage": "i1", "machine": "i1_0", "start": 10, "end": 15},
            ],
            "objValue": 100.0,
            "objBound": 50.0,
        }
        with open(inst_dir / f"{ins.name}_solution.json", "w") as f:
            json.dump(sol_json, f)

        manifest = {"obj_value": 100.0, "obj_bound": 50.0, "elapsed_time": 42.5}
        with open(inst_dir / f"{ins.name}_instance_result.yaml", "w") as f:
            yaml.safe_dump(manifest, f)

    runner = FFcDDWMultiInstanceRunner.__new__(FFcDDWMultiInstanceRunner)
    runner.logger = MagicMock()
    runner.output_metadata = {"resume_root": str(resume_root)}
    runner.instances = instances
    runner.runners = runners

    runner._load_resume_data()

    for sir in runners:
        assert sir.resume_solution is not None
        assert sir.resume_solution.obj_value == 100.0
        assert sir.resume_solution.obj_bound == 50.0
        assert sir.resume_elapsed_time == 42.5


def test_load_resume_data_raises_on_missing_artifacts(tmp_path: Path) -> None:
    """When artifacts are missing, _load_resume_data raises RuntimeError."""
    resume_root = tmp_path / "base_empty"
    resume_root.mkdir()

    runner = FFcDDWMultiInstanceRunner.__new__(FFcDDWMultiInstanceRunner)
    runner.logger = MagicMock()
    runner.output_metadata = {"resume_root": str(resume_root)}
    runner.instances = [SimpleNamespace(name="ins_x")]
    runner.runners = []
    sir = FFcDDWSingleInstanceRunner.__new__(FFcDDWSingleInstanceRunner)
    sir.resume_solution = None
    sir.resume_elapsed_time = None
    runner.runners.append(sir)

    import pytest

    with pytest.raises(RuntimeError, match="missing base artifacts"):
        runner._load_resume_data()
