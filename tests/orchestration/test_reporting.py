from __future__ import annotations

import csv
import json
from pathlib import Path

from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import InstanceResult
from ffc_ddw_sum_et.orchestration.reporting import (
    FFcDDWReporter,
    ScenarioResult,
    _last_non_empty_line,
)


def _make_ir(
    instance_name: str = "ins",
    *,
    obj_value: float | None = 10.0,
    first_obj_value: float | None = 20.0,
    elapsed_time: float = 1.0,
    error: str | None = None,
    method_call_counts: dict[str, int] | None = None,
) -> InstanceResult:
    return InstanceResult(
        instance_name=instance_name,
        elapsed_time=elapsed_time,
        obj_value=obj_value,
        obj_bound=None,
        work_status="FEASIBLE" if obj_value is not None else None,
        has_incumbent=obj_value is not None,
        method_call_counts=method_call_counts or {},
        report_count=1 if obj_value is not None else 0,
        first_obj_value=first_obj_value,
        first_obj_bound=None,
        error=error,
    )


def test_last_non_empty_line_empty() -> None:
    assert _last_non_empty_line(None) is None
    assert _last_non_empty_line("") is None
    assert _last_non_empty_line("   \n\n  ") is None


def test_last_non_empty_line_single() -> None:
    assert _last_non_empty_line("only") == "only"


def test_last_non_empty_line_multi() -> None:
    assert _last_non_empty_line("first\nmiddle\nlast") == "last"
    assert _last_non_empty_line("first\nlast\n\n") == "last"


def test_aggregate_scenario_basic(tmp_path: Path) -> None:
    sc = ScenarioResult(
        name="s1",
        instance_results=[
            _make_ir("a", obj_value=10.0, first_obj_value=20.0),
            _make_ir("b", obj_value=6.0, first_obj_value=12.0),
            _make_ir("c", obj_value=8.0, first_obj_value=16.0),
        ],
    )
    reporter = FFcDDWReporter(tmp_path, [sc])

    stats = reporter._aggregate_scenario(sc)

    assert stats["scenarioName"] == "s1"
    assert stats["instanceCount"] == 3
    assert stats["completedCount"] == 3
    assert stats["erroredCount"] == 0
    assert stats["minObjValue"] == 6.0
    assert stats["maxObjValue"] == 10.0
    assert stats["meanObjValue"] == (10.0 + 6.0 + 8.0) / 3
    # All three: (20-10)/20, (12-6)/12, (16-8)/16 = 0.5, 0.5, 0.5
    assert stats["meanImprovementRatio"] == 0.5


def test_aggregate_scenario_with_errors(tmp_path: Path) -> None:
    sc = ScenarioResult(
        name="s1",
        instance_results=[
            _make_ir("a", obj_value=10.0, first_obj_value=20.0),
            _make_ir("b", obj_value=None, first_obj_value=None, error="boom"),
        ],
    )
    reporter = FFcDDWReporter(tmp_path, [sc])

    stats = reporter._aggregate_scenario(sc)

    assert stats["completedCount"] == 1
    assert stats["erroredCount"] == 1
    assert stats["minObjValue"] == 10.0


def test_aggregate_scenario_no_completed(tmp_path: Path) -> None:
    sc = ScenarioResult(
        name="s1",
        instance_results=[
            _make_ir("a", obj_value=None, first_obj_value=None, error="boom"),
        ],
    )
    reporter = FFcDDWReporter(tmp_path, [sc])

    stats = reporter._aggregate_scenario(sc)

    assert stats["completedCount"] == 0
    assert stats["meanObjValue"] is None
    assert stats["minObjValue"] is None
    assert stats["maxObjValue"] is None
    assert stats["meanImprovementRatio"] is None


def test_aggregate_scenario_improvement_ratio_skips_none_first(tmp_path: Path) -> None:
    sc = ScenarioResult(
        name="s1",
        instance_results=[
            _make_ir("a", obj_value=10.0, first_obj_value=20.0),
            _make_ir("b", obj_value=5.0, first_obj_value=None),
        ],
    )
    reporter = FFcDDWReporter(tmp_path, [sc])

    stats = reporter._aggregate_scenario(sc)

    # Only instance a contributes: (20 - 10) / 20 = 0.5
    assert stats["meanImprovementRatio"] == 0.5


def test_aggregate_scenario_improvement_ratio_skips_zero_first(tmp_path: Path) -> None:
    sc = ScenarioResult(
        name="s1",
        instance_results=[
            _make_ir("a", obj_value=10.0, first_obj_value=20.0),
            _make_ir("b", obj_value=0.0, first_obj_value=0.0),
        ],
    )
    reporter = FFcDDWReporter(tmp_path, [sc])

    stats = reporter._aggregate_scenario(sc)

    assert stats["meanImprovementRatio"] == 0.5


def test_write_summary_csv(tmp_path: Path) -> None:
    sc = ScenarioResult(
        name="s1",
        instance_results=[
            _make_ir("a"),
            _make_ir("b", method_call_counts={"run_fam": 2}),
        ],
    )
    out_dir = tmp_path / "run_42"
    out_dir.mkdir()
    reporter = FFcDDWReporter(out_dir, [sc])

    reporter._write_summary_csv()

    csv_path = out_dir / "run_42_summary.csv"
    assert csv_path.exists()
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    # hfs_summary-style header: scenarioName lives in the outputs block.
    assert rows[0]["scenarioName"] == "s1"
    assert rows[0]["instanceName"] == "a"
    assert rows[1]["methodCallCounts"] == json.dumps({"run_fam": 2})


def test_write_statistics_json(tmp_path: Path) -> None:
    sc = ScenarioResult(
        name="s1",
        instance_results=[_make_ir("a", obj_value=10.0, first_obj_value=20.0)],
    )
    reporter = FFcDDWReporter(tmp_path, [sc])

    reporter._write_statistics_json()

    stats_path = tmp_path / "s1_statistics.json"
    assert stats_path.exists()
    data = json.loads(stats_path.read_text())
    assert data == reporter._aggregate_scenario(sc)


def test_generate_summary_filename(tmp_path: Path) -> None:
    out_dir = tmp_path / "run_42"
    out_dir.mkdir()
    reporter = FFcDDWReporter(out_dir, [])

    assert reporter.generate_summary_filename("csv") == "run_42_summary.csv"
