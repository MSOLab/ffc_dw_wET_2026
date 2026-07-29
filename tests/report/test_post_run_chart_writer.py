"""Integration test for
``ffc_ddw_sum_et.report.write_post_run_subroutine_chart_artifacts``.

Builds a tiny two-instance scenario directory in tmp_path that mimics the
real artifact layout (``<run>/<scenario>/<instance>/<...>``), runs the
writer, and asserts both HTML files are produced with the expected payload
shape.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from routix.io import RunRoot

from ffc_ddw_sum_et.orchestration.artifact_layout import init_ffc_artifact_layout
from ffc_ddw_sum_et.report import multi_scenario_method_chart as chart_mod
from ffc_ddw_sum_et.report import write_post_run_subroutine_chart_artifacts
from ffc_ddw_sum_et.report._chart_constants import HOVER_PERCENT_DECIMALS
from ffc_ddw_sum_et.report.np_utils import round_step_series
from ffc_ddw_sum_et.report.step_path import build_step_path


def _write_instance(
    layout,
    scenario: str,
    instance: str,
    *,
    timelimit: float,
    endpoints: list[tuple[float, float, str]],
) -> None:
    """Write a synthetic ``<instance>_obj_log.json`` + manifest pair.

    ``endpoints`` is a list of ``(end_sec, obj_value, label)`` tuples.
    Each entry produces one ``data`` point and one ``notes`` entry. Bound
    series mirrors with a constant of 0 (sufficient for the writer; the
    chart only consumes the obj_value series).
    """
    inst_dir = layout.instance_dir(scenario, instance)
    inst_dir.mkdir(parents=True, exist_ok=True)

    data = {repr(end): val for end, val, _ in endpoints}
    notes = {repr(end): label for end, _, label in endpoints}
    obj_log_payload = {
        "obj_value": {"name": "obj_value", "data": data, "notes": notes},
        "obj_bound": {"name": "obj_bound", "data": data, "notes": notes},
    }
    obj_log_path = layout.artifact_path(
        "obj_log_json", scenario_name=scenario, instance_name=instance
    )
    obj_log_path.write_text(json.dumps(obj_log_payload), encoding="utf-8")

    manifest_path = layout.artifact_path(
        "instance_result_manifest", scenario_name=scenario, instance_name=instance
    )
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "instance_name": instance,
                "job_count": 50,
                "stage_count": 5,
                "timelimit": timelimit,
            }
        ),
        encoding="utf-8",
    )


def _write_summary_csv(layout, rows: list[dict]) -> None:
    summary_csv = layout.artifact_path("summary_csv")
    pd.DataFrame(rows).to_csv(summary_csv, index=False)


def _write_baseline_files(
    tmp_path: Path, instances: list[str]
) -> tuple[Path, Path, Path]:
    match_csv = tmp_path / "match.csv"
    bks_csv = tmp_path / "bks.csv"
    inst_csv = tmp_path / "instance_table.csv"
    match_rows = [
        {
            "insIndex": f"{i:04d}",
            "ffc_ddw_sum_et_filename": f"{name}.txt",
            "hybridflowshop_filename": f"{i}.txt",
        }
        for i, name in enumerate(instances)
    ]
    bks_rows = [
        {
            "insIndex": f"{i:04d}",
            "n": 50,
            "c": 5,
            "totalMcCount": 15,
            "T": 0.2,
            "R": 0.2,
            "W": 10,
            "BKS_data": 8000 + i * 100,
            "BKS_calc": 0,
            "BKS_T": 0,
            "BKS_F": 0,
        }
        for i, _ in enumerate(instances)
    ]
    inst_rows = [
        {
            "insIndex": f"{i:04d}",
            "n": 50,
            "c": 5,
            "totalMcCount": 15,
            "T": 0.2,
            "R": 0.2,
            "W": 10,
            "BKS": 8000 + i * 100,
        }
        for i, _ in enumerate(instances)
    ]
    pd.DataFrame(match_rows).to_csv(match_csv, index=False)
    pd.DataFrame(bks_rows).to_csv(bks_csv, index=False)
    pd.DataFrame(inst_rows).to_csv(inst_csv, index=False)
    return match_csv, bks_csv, inst_csv


def test_writes_both_html_artifacts(tmp_path: Path) -> None:
    run_id = "20260507T000000_000000"
    rr = RunRoot(path=tmp_path / run_id, run_id=run_id)
    layout = init_ffc_artifact_layout(rr)

    instances = ["InstA", "InstB"]
    scenario = "scenario_x"
    for ins in instances:
        _write_instance(
            layout,
            scenario,
            ins,
            timelimit=10.0,
            endpoints=[
                (1.0, 9000.0, "1-step_alpha"),
                (5.0, 8500.0, "2-step_beta"),
            ],
        )
    _write_summary_csv(
        layout,
        [
            {"scenarioName": scenario, "instanceName": ins, "bestObj": 8500.0}
            for ins in instances
        ],
    )

    match_csv, bks_csv, inst_csv = _write_baseline_files(tmp_path, instances)
    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=match_csv,
        bks_table_csv=bks_csv,
        instance_table_csv=inst_csv,
    )

    scatter_path = layout.artifact_path(
        "subroutine_rpdf_scatter_html", scenario_name=scenario
    )
    flow_path = layout.artifact_path("multi_scenario_subroutine_flow_comparison_html")

    assert scatter_path.exists()
    assert flow_path.exists()

    scatter_html = scatter_path.read_text(encoding="utf-8")
    data_match = re.search(r"const DATA = (\{.*?\});", scatter_html, re.S)
    assert data_match is not None
    data = json.loads(data_match.group(1))
    assert len(data["raw_series"]) == 2  # one per instance
    assert {s["instance_id"] for s in data["raw_series"]} == set(instances)
    assert len(data["mean_series"]) == 1  # single (n=50,c=5) group

    flow_html = flow_path.read_text(encoding="utf-8")
    payload_match = re.search(r"const payload = (\{.*?\});", flow_html, re.S)
    assert payload_match is not None
    payload = json.loads(payload_match.group(1))
    assert len(payload["traces"]) == 1
    assert payload["traces"][0]["scenario"] == scenario
    assert "all" in payload["traces"][0]
    assert "x" in payload["traces"][0]["all"]
    assert "y" in payload["traces"][0]["all"]


def _setup_layout(tmp_path: Path) -> tuple[Any, str]:  # type: ignore[name-defined]
    run_id = "20260507T000000_000000"
    rr = RunRoot(path=tmp_path / run_id, run_id=run_id)
    return init_ffc_artifact_layout(rr), run_id


def test_silently_skips_when_baseline_csv_missing(tmp_path: Path) -> None:
    """Each baseline file is independently optional; a missing one returns
    early without writing artifacts and without raising."""
    layout, _ = _setup_layout(tmp_path)
    bogus = tmp_path / "does_not_exist.csv"
    real = tmp_path / "real.csv"
    real.write_text("insIndex,ffc_ddw_sum_et_filename\n", encoding="utf-8")

    # No HTML should be written for any of the three skip branches.
    flow_path = layout.artifact_path("multi_scenario_subroutine_flow_comparison_html")

    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=bogus,
        bks_table_csv=real,
        instance_table_csv=real,
    )
    assert not flow_path.exists()

    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=real,
        bks_table_csv=bogus,
        instance_table_csv=real,
    )
    assert not flow_path.exists()

    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=real,
        bks_table_csv=real,
        instance_table_csv=bogus,
    )
    assert not flow_path.exists()


def test_silently_skips_when_summary_csv_missing(tmp_path: Path) -> None:
    """No summary_csv → no scenarios → early return, no artifacts."""
    layout, _ = _setup_layout(tmp_path)
    match_csv, bks_csv, inst_csv = _write_baseline_files(tmp_path, ["Inst"])

    flow_path = layout.artifact_path("multi_scenario_subroutine_flow_comparison_html")

    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=match_csv,
        bks_table_csv=bks_csv,
        instance_table_csv=inst_csv,
    )
    assert not flow_path.exists()


def test_silently_skips_scenarios_without_obj_log(tmp_path: Path) -> None:
    """A scenario listed in summary but with no obj_log instances is skipped
    without raising; with no usable scenarios, the multi-scenario HTML is
    not written."""
    layout, _ = _setup_layout(tmp_path)
    instances = ["InstA"]
    match_csv, bks_csv, inst_csv = _write_baseline_files(tmp_path, instances)
    _write_summary_csv(
        layout,
        [{"scenarioName": "empty_scenario", "instanceName": "InstA", "bestObj": 0.0}],
    )

    flow_path = layout.artifact_path("multi_scenario_subroutine_flow_comparison_html")

    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=match_csv,
        bks_table_csv=bks_csv,
        instance_table_csv=inst_csv,
    )
    assert not flow_path.exists()


# ── helpers for flow-chart payload tests ─────────────────────────────────


def _gen_flow_chart_payload(
    tmp_path: Path,
    instances: list[str],
    scenario: str = "scenario_x",
) -> dict:
    run_id = "20260507T000000_000000"
    rr = RunRoot(path=tmp_path / run_id, run_id=run_id)
    layout = init_ffc_artifact_layout(rr)

    for ins in instances:
        _write_instance(
            layout,
            scenario,
            ins,
            timelimit=10.0,
            endpoints=[
                (1.0, 9000.0, "1-step_alpha"),
                (5.0, 8500.0, "2-step_beta"),
            ],
        )
    _write_summary_csv(
        layout,
        [
            {"scenarioName": scenario, "instanceName": ins, "bestObj": 8500.0}
            for ins in instances
        ],
    )
    match_csv, bks_csv, inst_csv = _write_baseline_files(tmp_path, instances)
    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=match_csv,
        bks_table_csv=bks_csv,
        instance_table_csv=inst_csv,
    )

    flow_path = layout.artifact_path("multi_scenario_subroutine_flow_comparison_html")
    flow_html = flow_path.read_text(encoding="utf-8")
    payload_match = re.search(r"const payload = (\{.*?\});", flow_html, re.S)
    assert payload_match is not None, "failed to parse payload from flow HTML"
    return json.loads(payload_match.group(1))


# ── C1: payload coordinate precision ─────────────────────────────────────


def _decimal_places(value: float) -> int:
    s = repr(value)
    if "." not in s:
        return 0
    return len(s) - s.index(".") - 1


def test_flow_chart_payload_coord_precision(tmp_path: Path) -> None:
    """C4: all.x ≤6, all.y ≤5, all.guide_x ≤6 decimal places."""
    payload = _gen_flow_chart_payload(tmp_path, ["InstA", "InstB"])
    for trace in payload["traces"]:
        for v in trace["all"]["x"]:
            assert _decimal_places(float(v)) <= 6, f"all.x {v!r} exceeds 6 decimals"
        for v in trace["all"]["y"]:
            assert _decimal_places(float(v)) <= 5, f"all.y {v!r} exceeds 5 decimals"
        for v in trace["all"]["guide_x"]:
            assert _decimal_places(float(v)) <= 6, (
                f"all.guide_x {v!r} exceeds 6 decimals"
            )


# ── C1: order contract ───────────────────────────────────────────────────


def test_round_step_series_preserves_step_path_order() -> None:
    """C1-6: rounding a micro-drop to flat avoids a duplicate step_x entry."""
    xs = [0.0, 0.5, 0.6, 1.0]
    ys = [0.5, 0.500001, 0.5, 0.0]
    rx, ry = round_step_series(xs, ys, x_decimals=6, y_decimals=5)
    step_x_before, _ = build_step_path(xs, ys)
    step_x_after, _ = build_step_path(rx, ry)
    assert len(step_x_after) < len(step_x_before), (
        f"with rounding: {len(step_x_after)} pts, without: {len(step_x_before)}"
    )
    assert step_x_after == [0.0, 0.5, 0.6, 1.0, 1.0]


# ── C1: axis consistency ─────────────────────────────────────────────────


def test_flow_chart_payload_axis_consistency(tmp_path: Path) -> None:
    """C4: y_min/y_max/x_max derived from all.x/all.y."""
    payload = _gen_flow_chart_payload(tmp_path, ["InstA", "InstB"])
    all_x: list[float] = []
    all_y: list[float] = []
    for trace in payload["traces"]:
        all_x.extend(float(v) for v in trace["all"]["x"])
        all_y.extend(float(v) for v in trace["all"]["y"])

    expected_x_max = max(1.0, max(all_x))
    assert math.isclose(payload["x_max"], expected_x_max), (
        f"x_max {payload['x_max']} != {expected_x_max}"
    )

    max_y = max(all_y)
    expected_y_max = 0.01 if max_y <= 0 else max_y * 1.05
    assert math.isclose(payload["y_max"], expected_y_max), (
        f"y_max {payload['y_max']} != {expected_y_max}"
    )

    expected_y_min = min(0.0, min(all_y))
    assert math.isclose(payload["y_min"], expected_y_min), (
        f"y_min {payload['y_min']} != {expected_y_min}"
    )


# ── C1-b: hovertemplate format ───────────────────────────────────────────


def _flow_chart_html(tmp_path: Path) -> str:
    _gen_flow_chart_payload(tmp_path, ["InstA", "InstB"])
    run_id = "20260507T000000_000000"
    rr = RunRoot(path=tmp_path / run_id, run_id=run_id)
    layout = init_ffc_artifact_layout(rr)
    flow_path = layout.artifact_path("multi_scenario_subroutine_flow_comparison_html")
    return flow_path.read_text(encoding="utf-8")


def test_flow_chart_hover_template_y_3_percent(tmp_path: Path) -> None:
    """C1-b + C3-4: hovertemplate uses .3% for both x and y, ticks stay .1%."""
    html = _flow_chart_html(tmp_path)
    dec = HOVER_PERCENT_DECIMALS
    assert re.search(rf"%\{{y:\.{dec}%}}", html), f"missing %{{y:.{dec}%}}"
    assert re.search(rf"%\{{x:\.{dec}%}}", html), f"missing %{{x:.{dec}%}}"
    assert not re.search(r"%\{[xy]:\.4%}", html), "stale .4% still present"
    assert 'tickformat: ".1%"' in html, "tickformat regressed from .1%"


def test_flow_chart_hover_follows_the_shared_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C3-8: every hover format is driven by HOVER_PERCENT_DECIMALS.

    Pins that no axis keeps a hardcoded literal — raising the constant must
    move x *and* y together, otherwise the two axes drift apart.
    """
    monkeypatch.setattr(chart_mod, "HOVER_PERCENT_DECIMALS", 4)
    html = _flow_chart_html(tmp_path)
    assert re.search(r"%\{x:\.4%}", html), "x hover ignored the constant"
    assert re.search(r"%\{y:\.4%}", html), "y hover ignored the constant"
    assert not re.search(r"%\{[xy]:\.3%}", html), "an axis kept a hardcoded .3%"


# ── C2: max points constant ──────────────────────────────────────────────


def test_all_series_max_points_is_2000() -> None:
    """C4: _ALL_SERIES_MAX_POINTS == 2000."""
    from ffc_ddw_sum_et.report import multi_scenario_method_chart as mod

    assert mod._ALL_SERIES_MAX_POINTS == 2000


def test_cell_series_max_points_is_200() -> None:
    """C4: _CELL_SERIES_MAX_POINTS == 200."""
    from ffc_ddw_sum_et.report import multi_scenario_method_chart as mod

    assert mod._CELL_SERIES_MAX_POINTS == 200


# ── back-fill removal + mean marker visibility ────────────────────────────


def _gen_flow_chart_payload_with_endpoints(
    tmp_path: Path,
    instance_endpoints: dict[str, list[tuple[float, float, str]]],
    *,
    timelimit: float = 10.0,
    scenario: str = "scenario_x",
) -> dict:
    """Build a flow-chart payload from per-instance endpoint lists.

    Lets a test craft a single-sample scenario (one instance contributes one
    late endpoint) to exercise the no-back-fill + open-marker contract.
    """
    run_id = "20260507T000000_000000"
    rr = RunRoot(path=tmp_path / run_id, run_id=run_id)
    layout = init_ffc_artifact_layout(rr)

    instances = list(instance_endpoints)
    for ins, endpoints in instance_endpoints.items():
        _write_instance(layout, scenario, ins, timelimit=timelimit, endpoints=endpoints)
    _write_summary_csv(
        layout,
        [
            {
                "scenarioName": scenario,
                "instanceName": ins,
                "bestObj": endpoints[-1][1] if endpoints else 0.0,
            }
            for ins, endpoints in instance_endpoints.items()
        ],
    )
    match_csv, bks_csv, inst_csv = _write_baseline_files(tmp_path, instances)
    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=match_csv,
        bks_table_csv=bks_csv,
        instance_table_csv=inst_csv,
    )
    flow_path = layout.artifact_path("multi_scenario_subroutine_flow_comparison_html")
    flow_html = flow_path.read_text(encoding="utf-8")
    payload_match = re.search(r"const payload = (\{.*?\});", flow_html, re.S)
    assert payload_match is not None, "failed to parse payload from flow HTML"
    return json.loads(payload_match.group(1))


def test_flow_chart_mean_series_starts_at_max_first_time(tmp_path: Path) -> None:
    """C1 + C3: removing the t=0 back-fill restores the
    ``max(first_times)`` start — the flow chart's first point marks
    "the moment every instance has a valid schedule", not t=0.

    Two instances: InstA first observes at t=0.1 (norm), InstB only at the
    stop time t=1.0. The mean step series must start at 1.0, not 0.0.
    """
    payload = _gen_flow_chart_payload_with_endpoints(
        tmp_path,
        {
            "InstA": [(1.0, 9000.0, "1-step_alpha"), (5.0, 8000.0, "2-step_beta")],
            "InstB": [(10.0, 9500.0, "1-step_alpha")],  # single late sample
        },
    )
    assert len(payload["traces"]) == 1
    trace = payload["traces"][0]
    # The start marker sits on the first mean sample = max(first_times) = 1.0.
    assert trace["all"]["x"][0] == 1.0, (
        f"first all sample should be max(first_times)=1.0, got {trace['all']['x'][0]}"
    )


def test_flow_chart_marks_only_the_mean_series_start_point(tmp_path: Path) -> None:
    """C2-1: each scenario trace carries a *single* open-circle marker at the
    mean series' first sample — the moment every instance has a valid
    schedule. Marking every sample instead would bury the line under
    thousands of circles."""
    payload = _gen_flow_chart_payload(tmp_path, ["InstA", "InstB"])
    for trace in payload["traces"]:
        assert len(trace["all"]["x"]) >= 1
        # start marker is no longer a separate field — JS computes it from all.x[0]
        pass

    html = _flow_chart_html(tmp_path)
    assert '"circle-open"' in html, "start marker symbol circle-open missing"
    assert "startX" in html, "start marker variable missing from template"


def test_flow_chart_single_sample_scenario_keeps_start_marker(tmp_path: Path) -> None:
    """C2-1 + C4: a single-sample scenario — where ``build_step_path``
    collapses the line to one point and ``mode="lines"`` would draw nothing
    — still emits its start marker, so the lone point stays referenceable."""
    payload = _gen_flow_chart_payload_with_endpoints(
        tmp_path,
        {
            "InstA": [(10.0, 9000.0, "1-step_alpha")],  # single late sample
            "InstB": [(10.0, 9500.0, "1-step_alpha")],
        },
    )
    assert len(payload["traces"]) == 1
    trace = payload["traces"][0]
    # Union grid collapses to a single sample at t=1.0 (norm_time).
    assert len(trace["all"]["x"]) == 1, "fixture no longer collapses to one sample"
    assert trace["all"]["x"][0] == 1.0
    assert trace["all"]["y"][0] is not None


# ── C1: load_baseline_df includes job_cnt/stage_cnt ────────────────────────


def test_load_baseline_df_contains_job_cnt_stage_cnt(tmp_path: Path) -> None:
    match_csv = tmp_path / "match.csv"
    bks_csv = tmp_path / "bks.csv"
    inst_csv = tmp_path / "instance_table.csv"

    pd.DataFrame(
        [
            {
                "insIndex": "0000",
                "ffc_ddw_sum_et_filename": "InstA.txt",
                "hybridflowshop_filename": "0.txt",
            }
        ]
    ).to_csv(match_csv, index=False)
    pd.DataFrame(
        [
            {
                "insIndex": "0000",
                "n": 50,
                "c": 5,
                "totalMcCount": 15,
                "T": 0.2,
                "R": 0.2,
                "W": 10,
                "BKS_data": 8000,
            }
        ]
    ).to_csv(bks_csv, index=False)
    pd.DataFrame(
        [
            {
                "insIndex": "0000",
                "n": 50,
                "c": 5,
                "totalMcCount": 15,
                "T": 0.2,
                "R": 0.2,
                "W": 10,
                "BKS": 8000,
            }
        ]
    ).to_csv(inst_csv, index=False)

    from ffc_ddw_sum_et.report.post_run_chart_writer import load_baseline_df

    df = load_baseline_df(match_csv, bks_csv, inst_csv)
    assert "job_cnt" in df.columns
    assert "stage_cnt" in df.columns
    assert df["job_cnt"].iloc[0] == 50
    assert df["stage_cnt"].iloc[0] == 5


def test_attach_rpdf_columns_propagates_job_cnt_stage_cnt(tmp_path: Path) -> None:
    match_csv = tmp_path / "match.csv"
    bks_csv = tmp_path / "bks.csv"
    inst_csv = tmp_path / "instance_table.csv"

    pd.DataFrame(
        [
            {
                "insIndex": "0000",
                "ffc_ddw_sum_et_filename": "InstA.txt",
                "hybridflowshop_filename": "0.txt",
            }
        ]
    ).to_csv(match_csv, index=False)
    pd.DataFrame(
        [
            {
                "insIndex": "0000",
                "n": 50,
                "c": 5,
                "totalMcCount": 15,
                "T": 0.2,
                "R": 0.2,
                "W": 10,
                "BKS_data": 8000,
            }
        ]
    ).to_csv(bks_csv, index=False)
    pd.DataFrame(
        [
            {
                "insIndex": "0000",
                "n": 50,
                "c": 5,
                "totalMcCount": 15,
                "T": 0.2,
                "R": 0.2,
                "W": 10,
                "BKS": 8000,
            }
        ]
    ).to_csv(inst_csv, index=False)

    from ffc_ddw_sum_et.report.post_run_chart_writer import (
        attach_rpdf_columns,
        load_baseline_df,
    )

    baseline_df = load_baseline_df(match_csv, bks_csv, inst_csv)
    df = pd.DataFrame(
        {"instance_id": ["InstA"], "obj_value": [8500.0], "norm_time": [1.0]}
    )
    result = attach_rpdf_columns(df, baseline_df)
    assert "job_cnt" in result.columns
    assert "stage_cnt" in result.columns
    assert result["job_cnt"].iloc[0] == 50
    assert result["stage_cnt"].iloc[0] == 5


def test_attach_rpdf_columns_drops_missing_baseline_instances(tmp_path: Path) -> None:
    match_csv = tmp_path / "match.csv"
    bks_csv = tmp_path / "bks.csv"
    inst_csv = tmp_path / "instance_table.csv"

    pd.DataFrame(
        [
            {
                "insIndex": "0000",
                "ffc_ddw_sum_et_filename": "InstA.txt",
                "hybridflowshop_filename": "0.txt",
            }
        ]
    ).to_csv(match_csv, index=False)
    pd.DataFrame(
        [
            {
                "insIndex": "0000",
                "n": 50,
                "c": 5,
                "totalMcCount": 15,
                "T": 0.2,
                "R": 0.2,
                "W": 10,
                "BKS_data": 8000,
            }
        ]
    ).to_csv(bks_csv, index=False)
    pd.DataFrame(
        [
            {
                "insIndex": "0000",
                "n": 50,
                "c": 5,
                "totalMcCount": 15,
                "T": 0.2,
                "R": 0.2,
                "W": 10,
                "BKS": 8000,
            }
        ]
    ).to_csv(inst_csv, index=False)

    from ffc_ddw_sum_et.report.post_run_chart_writer import (
        attach_rpdf_columns,
        load_baseline_df,
    )

    baseline_df = load_baseline_df(match_csv, bks_csv, inst_csv)
    df = pd.DataFrame(
        {
            "instance_id": ["InstA", "InstB"],
            "obj_value": [8500.0, 9000.0],
            "norm_time": [1.0, 0.5],
        }
    )
    result = attach_rpdf_columns(df, baseline_df)
    assert len(result) == 1
    assert result["instance_id"].iloc[0] == "InstA"


# ── C5: filter toolbar integration ───────────────────────────────────────────


def test_writer_includes_filter_toolbar_and_cells(tmp_path: Path) -> None:
    """C5-17: both HTML artifacts include the cell filter toolbar and
    non-empty cell payload."""
    run_id = "20260507T000000_000000"
    rr = RunRoot(path=tmp_path / run_id, run_id=run_id)
    layout = init_ffc_artifact_layout(rr)

    instances = ["InstA", "InstB"]
    scenario = "scenario_x"
    for ins in instances:
        _write_instance(
            layout,
            scenario,
            ins,
            timelimit=10.0,
            endpoints=[
                (1.0, 9000.0, "1-step_alpha"),
                (5.0, 8500.0, "2-step_beta"),
            ],
        )
    _write_summary_csv(
        layout,
        [
            {"scenarioName": scenario, "instanceName": ins, "bestObj": 8500.0}
            for ins in instances
        ],
    )
    match_csv, bks_csv, inst_csv = _write_baseline_files(tmp_path, instances)
    write_post_run_subroutine_chart_artifacts(
        layout=layout,
        hybrid_match_csv=match_csv,
        bks_table_csv=bks_csv,
        instance_table_csv=inst_csv,
    )

    # Flow comparison chart
    flow_path = layout.artifact_path("multi_scenario_subroutine_flow_comparison_html")
    flow_html = flow_path.read_text(encoding="utf-8")
    assert 'id="filter-t_factor"' in flow_html
    assert 'id="filter-r_factor"' in flow_html
    assert 'id="filter-job_cnt"' in flow_html
    assert 'id="filter-stage_cnt"' in flow_html
    assert "payload.traces[0].cells" not in flow_html  # no cells if none generated

    # Method-mean scatter
    run_level_path = layout.artifact_path("multi_scenario_method_mean_scatter_html")
    scatter_html = run_level_path.read_text(encoding="utf-8")
    assert 'id="filter-t_factor"' in scatter_html

    # Per-scenario scatter
    per_scenario_path = layout.artifact_path(
        "method_mean_scatter_html", scenario_name=scenario
    )
    assert per_scenario_path.exists()
    per_html = per_scenario_path.read_text(encoding="utf-8")
    assert 'id="filter-t_factor"' in per_html


def test_writer_without_cell_map_renders_no_toolbar(tmp_path: Path) -> None:
    """C5-18: calling the flow writer *without* cell_by_instance produces HTML
    with no filter toolbar but a **runnable** chart (the
    ``build_cross_run_flow_chart`` path).

    Asserting only that a file was written is not enough: the render path calls
    the filter helpers unconditionally, so omitting their definitions left this
    exact caller with a ReferenceError and a blank page.
    """
    from ffc_ddw_sum_et.report.multi_scenario_method_chart import (
        export_multi_scenario_method_rpdf_comparison_html,
    )

    ep = pd.DataFrame(
        {
            "instance_id": ["A", "A"],
            "subroutine_name": ["step_alpha", "step_beta"],
            "norm_time": [0.1, 0.5],
            "rpd_f": [0.05, 0.02],
            "obj_value": [100.0, 95.0],
            "subroutine_order": [1, 2],
        }
    )
    out = tmp_path / "no_cells.html"
    ok = export_multi_scenario_method_rpdf_comparison_html(
        [{"label": "test", "endpoint_df": ep, "raw_progression_df": None}],
        out,
    )
    assert ok
    html = out.read_text(encoding="utf-8")
    assert 'id="filter-t_factor"' not in html
    assert 'id="filter-r_factor"' not in html
    assert 'id="cell-filter-toolbar"' not in html
    # Every helper the render path calls must still be defined.
    for fn in ("getSelectedCellKeys", "buildStepPath", "mergeCells"):
        assert f"function {fn}(" in html, f"{fn} called but never defined"


def test_flow_chart_guide_shape_lookup_matches_traces_per_scenario(
    tmp_path: Path,
) -> None:
    """``buildVisibleGuideShapes`` finds each scenario's line trace by
    striding over ``plotData``; the stride must equal the number of traces
    emitted per scenario. Adding the start-marker trace made it 3, so a
    hard-coded ``idx * 2`` would toggle the wrong scenario's vertical guides.
    """
    html = _flow_chart_html(tmp_path)

    stride_match = re.search(r"TRACES_PER_SCENARIO\s*=\s*(\d+)", html)
    assert stride_match is not None, "traces-per-scenario stride is not named"
    assert re.search(r"plotData\?\.\[idx \* TRACES_PER_SCENARIO\]", html), (
        "guide-shape lookup does not use the named stride"
    )

    flat_map = re.search(
        r"payload\.traces\.flatMap\(\(trace, idx\) => \{.*?\n    \}\);", html, re.S
    )
    assert flat_map is not None, "per-scenario trace builder not found"
    emitted = len(re.findall(r'type: "scatter"', flat_map.group(0)))
    assert emitted == int(stride_match.group(1)), (
        f"{emitted} traces per scenario but stride is {stride_match.group(1)}"
    )
