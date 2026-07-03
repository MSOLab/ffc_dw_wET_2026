"""Smoke tests for WP-5 CSR rendering additions in reporting.py.

Covers:
- ``_render_phase_gantt_from_json`` reuse with and without force_start/force_end
- ``_render_csr_cp_trajectory_line`` with mixed None values
- ``_render_csr_cp_trajectory_line`` with all-None / empty trajectory (no file)
- Shared-horizon helper logic: force_end = max(raw, final) makespan
"""

from __future__ import annotations

import json
from pathlib import Path

from ffc_ddw_sum_et.orchestration.reporting import (
    _phase_makespan_from_json,
    _render_csr_cp_trajectory_line,
    _render_phase_gantt_from_json,
)

# ---------------------------------------------------------------------------
# Minimal operations[] payload helpers
# ---------------------------------------------------------------------------

_JOBS = ["J0", "J1"]
_STAGES = ["S0"]
_MACHINES = {"S0": ["M0"]}


def _operations_json(path: Path, *, makespan: int = 10) -> None:
    """Write a tiny 2-job / 1-stage operations[] JSON to *path*."""
    data = {
        "instanceName": "TestIns",
        "objValue": makespan,
        "jobs": _JOBS,
        "stages": _STAGES,
        "machinesPerStage": _MACHINES,
        "operations": [
            {
                "job": "J0",
                "stage": "S0",
                "machine": "M0",
                "start": 0,
                "end": makespan // 2,
            },
            {
                "job": "J1",
                "stage": "S0",
                "machine": "M0",
                "start": makespan // 2,
                "end": makespan,
            },
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# _render_phase_gantt_from_json — reuse with / without force
# ---------------------------------------------------------------------------


def test_render_phase_gantt_no_force(tmp_path: Path) -> None:
    """Render a phase Gantt without forced x-axis — PNG produced and non-empty."""
    json_path = tmp_path / "phase.json"
    png_path = tmp_path / "out.png"
    _operations_json(json_path, makespan=10)

    _render_phase_gantt_from_json(json_path, png_path)

    assert png_path.exists(), "PNG should be created"
    assert png_path.stat().st_size > 0, "PNG should be non-empty"


def test_render_phase_gantt_with_force(tmp_path: Path) -> None:
    """Render a phase Gantt with forced x-axis — PNG produced and non-empty."""
    json_path = tmp_path / "phase.json"
    png_path = tmp_path / "out_forced.png"
    _operations_json(json_path, makespan=8)

    _render_phase_gantt_from_json(json_path, png_path, force_start=0, force_end=20)

    assert png_path.exists(), "PNG should be created even with padded horizon"
    assert png_path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Shared-horizon helper: _phase_makespan_from_json
# ---------------------------------------------------------------------------


def test_shared_horizon_is_max_of_raw_and_final(tmp_path: Path) -> None:
    """force_end for raw+final pair equals the max of their makespans."""
    raw_json = tmp_path / "2_reconstructed_raw.json"
    final_json = tmp_path / "3_final.json"
    _operations_json(raw_json, makespan=12)
    _operations_json(final_json, makespan=18)

    raw_ms = _phase_makespan_from_json(raw_json)
    final_ms = _phase_makespan_from_json(final_json)

    assert raw_ms == 12
    assert final_ms == 18
    assert max(raw_ms, final_ms) == 18  # shared horizon should be 18


def test_phase_makespan_from_json_missing_file(tmp_path: Path) -> None:
    """Missing file returns None (not an exception)."""
    result = _phase_makespan_from_json(tmp_path / "nonexistent.json")
    assert result is None


# ---------------------------------------------------------------------------
# _render_csr_cp_trajectory_line
# ---------------------------------------------------------------------------


def test_render_csr_cp_trajectory_with_partial_nones(tmp_path: Path) -> None:
    """Trajectory with some None entries still produces a non-empty PNG."""
    json_path = tmp_path / "MyInstance_csr_cp_trajectory.json"
    png_path = tmp_path / "MyInstance_csr_cp_trajectory.png"
    data = {
        "elapsed_sec": [0.0, 1.0, 2.0, 3.0],
        "obj_value": [None, 100, 90, None],  # UB appears at t=1 and t=2
        "obj_bound": [0, 0, None, 80],  # LB has a gap at t=2
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    _render_csr_cp_trajectory_line(json_path, png_path)

    assert png_path.exists(), "PNG should be produced with partial None values"
    assert png_path.stat().st_size > 0


def test_render_csr_cp_trajectory_all_none_no_file(tmp_path: Path) -> None:
    """All-None trajectory must NOT create a PNG file."""
    json_path = tmp_path / "MyInstance_csr_cp_trajectory.json"
    png_path = tmp_path / "MyInstance_csr_cp_trajectory.png"
    data = {
        "elapsed_sec": [0.0, 1.0, 2.0],
        "obj_value": [None, None, None],
        "obj_bound": [None, None, None],
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    _render_csr_cp_trajectory_line(json_path, png_path)

    assert not png_path.exists(), "No PNG should be created for an all-None trajectory"


def test_render_csr_cp_trajectory_empty_arrays_no_file(tmp_path: Path) -> None:
    """Empty arrays must NOT create a PNG file."""
    json_path = tmp_path / "Empty_csr_cp_trajectory.json"
    png_path = tmp_path / "Empty_csr_cp_trajectory.png"
    data = {
        "elapsed_sec": [],
        "obj_value": [],
        "obj_bound": [],
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    _render_csr_cp_trajectory_line(json_path, png_path)

    assert not png_path.exists(), "No PNG should be created for empty trajectory"


def test_render_csr_cp_trajectory_ub_only(tmp_path: Path) -> None:
    """Trajectory with only UB values (LB all None) still produces a PNG."""
    json_path = tmp_path / "UBOnly_csr_cp_trajectory.json"
    png_path = tmp_path / "UBOnly_csr_cp_trajectory.png"
    data = {
        "elapsed_sec": [0.5, 1.5, 2.5],
        "obj_value": [200, 180, 160],
        "obj_bound": [None, None, None],
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    _render_csr_cp_trajectory_line(json_path, png_path)

    assert png_path.exists(), "PNG should be produced when only UB is present"
    assert png_path.stat().st_size > 0
