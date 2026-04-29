"""Round-trip tests for the per-instance ``*_instance_result.yaml`` manifest.

Verifies the contract used by POST_PROCESS_ONLY:
- atomic write (.tmp -> os.replace)
- forward-compat projection (drop unknown keys, default missing keys)
- multi-line ``error`` traceback survives YAML block-scalar round-trip
- nested ``mcf_lb_diagnostic`` dict round-trips
- ``.tmp`` siblings are not picked up by the loader
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from routix.io import dump_yaml

from ffc_ddw_sum_et.orchestration.artifact_layout import FFcArtifactLayout
from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import (
    FFcDDWSingleInstanceRunner,
    InstanceResult,
    _MANIFEST_SCHEMA_VERSION,
)


_SCENARIO = "sc"


def _make_runner(
    tmp_path: Path, ins_name: str
) -> tuple[FFcDDWSingleInstanceRunner, Path]:
    """Construct a runner stub bypassing __init__ — only fields used by
    manifest read/write are populated. Returns (runner, instance_dir)."""
    layout = FFcArtifactLayout(run_root=tmp_path / "run", run_id="run")
    instance_dir = layout.instance_dir(_SCENARIO, ins_name)
    runner: Any = FFcDDWSingleInstanceRunner.__new__(FFcDDWSingleInstanceRunner)
    runner.working_dir = instance_dir
    runner.ins_name = ins_name
    runner.instance = SimpleNamespace(name=ins_name)
    runner.layout = layout
    runner._scenario_name = _SCENARIO
    return runner, instance_dir


def _make_result(**overrides: Any) -> InstanceResult:
    base = dict(
        instance_name="inst1",
        elapsed_time=1.25,
        obj_value=42.0,
        obj_bound=40.0,
        work_status="FEASIBLE",
        solution_path="/tmp/inst1_solution.json",
        has_incumbent=True,
        method_call_counts={"run_fam": 3, "run_neh_cp": 1},
        report_count=4,
        first_obj_value=50.0,
        first_obj_bound=39.0,
        error=None,
        job_count=20,
        stage_count=4,
        machines_per_stage=2,
        timelimit=60.0,
        mcf_lb_diagnostic=None,
        makespan=42,
    )
    base.update(overrides)
    return InstanceResult(**base)


def test_round_trip_basic(tmp_path: Path) -> None:
    runner, _ = _make_runner(tmp_path, "inst1")
    result = _make_result()
    runner._write_instance_result_manifest(result)
    loaded = runner._load_instance_result()
    assert loaded == result


def test_round_trip_multiline_error(tmp_path: Path) -> None:
    traceback_text = textwrap.dedent(
        """\
        Traceback (most recent call last):
          File "x.py", line 12, in run
            raise RuntimeError("boom: ' \" tab\there")
        RuntimeError: boom
        """
    )
    runner, _ = _make_runner(tmp_path, "inst_err")
    result = _make_result(
        instance_name="inst_err",
        obj_value=None,
        obj_bound=None,
        work_status=None,
        has_incumbent=False,
        error=traceback_text,
        makespan=None,
    )
    runner._write_instance_result_manifest(result)
    loaded = runner._load_instance_result()
    assert loaded.error == traceback_text


def test_round_trip_mcf_lb_diagnostic(tmp_path: Path) -> None:
    diag = {
        "mcf_lb": 12.5,
        "profile_fix_bound": 11.0,
        "nested": {"a": 1, "b": [1, 2, 3]},
    }
    runner, _ = _make_runner(tmp_path, "inst_diag")
    result = _make_result(instance_name="inst_diag", mcf_lb_diagnostic=diag)
    runner._write_instance_result_manifest(result)
    loaded = runner._load_instance_result()
    assert loaded.mcf_lb_diagnostic == diag


def test_atomic_write_replaces_existing(tmp_path: Path) -> None:
    runner, ins_dir = _make_runner(tmp_path, "inst_replace")
    runner._write_instance_result_manifest(_make_result(instance_name="inst_replace"))
    final = ins_dir / "inst_replace_instance_result.yaml"
    tmp = ins_dir / "inst_replace_instance_result.yaml.tmp"
    assert final.exists()
    assert not tmp.exists()


def test_loader_drops_unknown_keys(tmp_path: Path) -> None:
    runner, ins_dir = _make_runner(tmp_path, "inst_extra")
    payload = {
        "_schema_version": _MANIFEST_SCHEMA_VERSION,
        "instance_name": "inst_extra",
        "elapsed_time": 1.0,
        "obj_value": 1.0,
        "obj_bound": None,
        "work_status": "FEASIBLE",
        "future_field_we_do_not_know_yet": "ignored",
    }
    dump_yaml(payload, ins_dir / "inst_extra_instance_result.yaml")
    loaded = runner._load_instance_result()
    assert loaded.instance_name == "inst_extra"
    assert loaded.obj_value == 1.0


def test_loader_fills_missing_with_defaults(tmp_path: Path) -> None:
    runner, ins_dir = _make_runner(tmp_path, "inst_min")
    payload = {
        "_schema_version": _MANIFEST_SCHEMA_VERSION,
        "instance_name": "inst_min",
        "elapsed_time": 0.5,
        "obj_value": None,
        "obj_bound": None,
        "work_status": None,
    }
    dump_yaml(payload, ins_dir / "inst_min_instance_result.yaml")
    loaded = runner._load_instance_result()
    assert loaded.instance_name == "inst_min"
    assert loaded.has_incumbent is False
    assert loaded.method_call_counts == {}
    assert loaded.makespan is None


def test_loader_ignores_tmp_sibling(tmp_path: Path) -> None:
    runner, ins_dir = _make_runner(tmp_path, "inst_tmp")
    runner._write_instance_result_manifest(_make_result(instance_name="inst_tmp"))
    final = ins_dir / "inst_tmp_instance_result.yaml"
    tmp = ins_dir / "inst_tmp_instance_result.yaml.tmp"
    tmp.write_text("partial: yaml: !!! invalid", encoding="utf-8")
    assert final.exists()
    loaded = runner._load_instance_result()
    assert loaded.instance_name == "inst_tmp"


def test_loader_raises_when_manifest_missing(tmp_path: Path) -> None:
    runner, _ = _make_runner(tmp_path, "inst_missing")
    with pytest.raises(FileNotFoundError):
        runner._load_instance_result()


def test_atomic_write_does_not_leave_tmp(tmp_path: Path) -> None:
    """No ``.yaml.tmp`` sibling after a successful write."""
    runner, ins_dir = _make_runner(tmp_path, "inst_clean")
    runner._write_instance_result_manifest(_make_result(instance_name="inst_clean"))
    leftovers = list(ins_dir.glob("*.yaml.tmp"))
    assert leftovers == []


def test_overwrite_existing_manifest(tmp_path: Path) -> None:
    runner, _ = _make_runner(tmp_path, "inst_over")
    runner._write_instance_result_manifest(
        _make_result(instance_name="inst_over", obj_value=1.0)
    )
    runner._write_instance_result_manifest(
        _make_result(instance_name="inst_over", obj_value=2.0)
    )
    loaded = runner._load_instance_result()
    assert loaded.obj_value == 2.0


def test_manifest_persists_to_disk(tmp_path: Path) -> None:
    """Manifest YAML lives at the expected filename and includes schema version."""
    runner, ins_dir = _make_runner(tmp_path, "inst_disk")
    runner._write_instance_result_manifest(_make_result(instance_name="inst_disk"))
    final = ins_dir / "inst_disk_instance_result.yaml"
    assert final.exists()
    assert os.path.getsize(final) > 0
    text = final.read_text(encoding="utf-8")
    assert "_schema_version" in text
    assert "inst_disk" in text
