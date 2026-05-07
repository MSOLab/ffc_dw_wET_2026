"""Integration test for
``ffc_ddw_sum_et.report.write_post_run_subroutine_chart_artifacts``.

Builds a tiny two-instance scenario directory in tmp_path that mimics the
real artifact layout (``<run>/<scenario>/<instance>/<...>``), runs the
writer, and asserts both HTML files are produced with the expected payload
shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from routix.io import RunRoot

from ffc_ddw_sum_et.orchestration.artifact_layout import init_ffc_artifact_layout
from ffc_ddw_sum_et.report import write_post_run_subroutine_chart_artifacts


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
