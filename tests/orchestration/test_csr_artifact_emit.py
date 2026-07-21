"""Unit tests for _emit_csr_artifacts in FFcDDWSingleInstanceRunner.

Tests the helper directly via a minimal stand-in controller object so we
don't have to spin up a full runner + instance.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from ffc_ddw_sum_et.orchestration.artifact_layout import FFcArtifactLayout
from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import (
    FFcDDWSingleInstanceRunner,
)
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_layout(
    tmp_path: Path, run_id: str = "20260623T000000_000000"
) -> FFcArtifactLayout:
    return FFcArtifactLayout(run_root=tmp_path / run_id, run_id=run_id)


def _make_schedule() -> FFcSchedule:
    """Minimal FFcSchedule with no operations (enough for JSON emit)."""
    return FFcSchedule(
        jobs=["j0"],
        stages=["s0"],
        machines_per_stage={"s0": ["s0_m0"]},
    )


def _make_runner(tmp_path: Path) -> FFcDDWSingleInstanceRunner:
    """Construct a runner with enough state to call _emit_csr_artifacts.

    We bypass __init__ by building the object directly and patching only the
    attributes the method needs.
    """
    runner = object.__new__(FFcDDWSingleInstanceRunner)
    runner.logger = MagicMock()
    runner.ins_name = "test_ins"
    runner._ins_name = "test_ins"
    runner.instance = MagicMock()  # compute_phase_obj_value will be mocked out
    return runner


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmitCsrPhaseSchedules:
    def test_three_phase_schedule_jsons_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """3 csr_phase_schedule JSON files are created for 3 snapshots."""
        layout = _make_layout(tmp_path)
        scope = {"scenario_name": "sc", "instance_name": "test_ins"}
        runner = _make_runner(tmp_path)

        # Patch compute_phase_obj_value to return a dummy objective
        monkeypatch.setattr(
            "ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner.compute_phase_obj_value",
            lambda sched, instance: 42.0,
        )

        call_ctx = "run_csr"
        snapshots = [
            (f"{call_ctx}_1_coarse_solver_result", _make_schedule()),
            (f"{call_ctx}_2_reconstructed_raw", _make_schedule()),
            (f"{call_ctx}_3_final", _make_schedule()),
        ]
        controller = SimpleNamespace(
            csr_phase_schedules=snapshots,
        )

        runner._emit_csr_artifacts(controller, layout, scope)

        # Verify 3 JSON files in the progress zone
        for name, _ in snapshots:
            p = layout.artifact_path("csr_phase_schedule", phase_name=name, **scope)
            assert p.exists(), f"Expected {p} to exist"

    def test_coarse_solver_result_has_null_obj_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 1_coarse_solver_result snapshot must have obj_value=null."""
        layout = _make_layout(tmp_path)
        scope = {"scenario_name": "sc", "instance_name": "test_ins"}
        runner = _make_runner(tmp_path)

        monkeypatch.setattr(
            "ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner.compute_phase_obj_value",
            lambda sched, instance: 99.0,
        )

        call_ctx = "run_csr"
        snapshots = [
            (f"{call_ctx}_1_coarse_solver_result", _make_schedule()),
            (f"{call_ctx}_2_reconstructed_raw", _make_schedule()),
            (f"{call_ctx}_3_final", _make_schedule()),
        ]
        controller = SimpleNamespace(
            csr_phase_schedules=snapshots,
        )

        runner._emit_csr_artifacts(controller, layout, scope)

        coarse_path = layout.artifact_path(
            "csr_phase_schedule",
            phase_name=f"{call_ctx}_1_coarse_solver_result",
            **scope,
        )
        data = json.loads(coarse_path.read_text())
        assert data["objValue"] is None, (
            f"Expected objValue=null for coarse snapshot, got {data['objValue']!r}"
        )

    def test_reconstructed_raw_and_final_have_obj_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Snapshots 2 and 3 get a meaningful obj_value from compute_phase_obj_value."""
        layout = _make_layout(tmp_path)
        scope = {"scenario_name": "sc", "instance_name": "test_ins"}
        runner = _make_runner(tmp_path)

        expected_obj = 77.5
        monkeypatch.setattr(
            "ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner.compute_phase_obj_value",
            lambda sched, instance: expected_obj,
        )

        call_ctx = "run_csr"
        snapshots = [
            (f"{call_ctx}_1_coarse_solver_result", _make_schedule()),
            (f"{call_ctx}_2_reconstructed_raw", _make_schedule()),
            (f"{call_ctx}_3_final", _make_schedule()),
        ]
        controller = SimpleNamespace(
            csr_phase_schedules=snapshots,
        )

        runner._emit_csr_artifacts(controller, layout, scope)

        for suffix in ("2_reconstructed_raw", "3_final"):
            p = layout.artifact_path(
                "csr_phase_schedule",
                phase_name=f"{call_ctx}_{suffix}",
                **scope,
            )
            data = json.loads(p.read_text())
            assert data["objValue"] == pytest.approx(expected_obj), (
                f"Snapshot {suffix}: expected objValue={expected_obj!r}, got {data['objValue']!r}"
            )

    def test_none_sched_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A sched=None entry must be silently skipped."""
        layout = _make_layout(tmp_path)
        scope = {"scenario_name": "sc", "instance_name": "test_ins"}
        runner = _make_runner(tmp_path)

        monkeypatch.setattr(
            "ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner.compute_phase_obj_value",
            lambda sched, instance: 0.0,
        )

        call_ctx = "run_csr"
        snapshots: list[Any] = [
            (f"{call_ctx}_1_coarse_solver_result", None),
            (f"{call_ctx}_2_reconstructed_raw", _make_schedule()),
        ]
        controller = SimpleNamespace(
            csr_phase_schedules=snapshots,
        )

        runner._emit_csr_artifacts(controller, layout, scope)

        # Only snapshot 2 should exist
        p1 = layout.artifact_path(
            "csr_phase_schedule",
            phase_name=f"{call_ctx}_1_coarse_solver_result",
            **scope,
        )
        p2 = layout.artifact_path(
            "csr_phase_schedule",
            phase_name=f"{call_ctx}_2_reconstructed_raw",
            **scope,
        )
        assert not p1.exists()
        assert p2.exists()

    def test_empty_csr_phase_schedules_writes_nothing(self, tmp_path: Path) -> None:
        """When csr_phase_schedules is empty, no files are written."""
        layout = _make_layout(tmp_path)
        scope = {"scenario_name": "sc", "instance_name": "test_ins"}
        runner = _make_runner(tmp_path)
        controller = SimpleNamespace(csr_phase_schedules=[])

        runner._emit_csr_artifacts(controller, layout, scope)

        progress_dir = layout.zone_dir("progress", **scope)
        csr_files = list(progress_dir.glob("*csr*")) if progress_dir.exists() else []
        assert csr_files == []

    def test_missing_attribute_writes_nothing(self, tmp_path: Path) -> None:
        """When the controller lacks csr_phase_schedules entirely, nothing breaks."""
        layout = _make_layout(tmp_path)
        scope = {"scenario_name": "sc", "instance_name": "test_ins"}
        runner = _make_runner(tmp_path)
        controller = SimpleNamespace()  # no csr attributes

        # Must not raise
        runner._emit_csr_artifacts(controller, layout, scope)
