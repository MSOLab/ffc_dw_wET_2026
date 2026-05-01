"""Tests for the ffc_ddw_sum_et `ArtifactLayout` overlay.

Verifies that the overlay yaml at `metadata/artifact_layout/
ffc_ddw_sum_et_v1.yaml` is loaded on top of the routix default schema and
that the resulting layout resolves both routix-default kinds (e.g.
`solution_json`) and ffc-specific kinds (e.g. `mcf_lb_diagnostic`,
`gantt_png`, `report_xlsx`) to the expected zones.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ffc_ddw_sum_et.orchestration.artifact_layout import (
    DEFAULT_OVERLAY_PATH,
    FFcArtifactLayout,
)


def _layout(
    tmp_path: Path, run_id: str = "20260429T000000_000000"
) -> FFcArtifactLayout:
    return FFcArtifactLayout(run_root=tmp_path / run_id, run_id=run_id)


def test_overlay_yaml_exists() -> None:
    assert DEFAULT_OVERLAY_PATH.exists(), DEFAULT_OVERLAY_PATH


def test_routix_default_kinds_still_resolve(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    p = layout.artifact_path(
        "instance_result_manifest", scenario_name="sc", instance_name="ins"
    )
    assert p.name == "ins_instance_result.yaml"
    # final zone == instance dir bare
    assert p.parent.name == "ins"


def test_ffc_progress_kinds_routed_to_progress_zone(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    diag = layout.artifact_path(
        "mcf_lb_diagnostic", scenario_name="sc", instance_name="ins"
    )
    last_stage = layout.artifact_path(
        "last_stage_cp_sat_schedule", scenario_name="sc", instance_name="ins"
    )
    phase = layout.artifact_path(
        "mcf_lb_phase_schedule",
        scenario_name="sc",
        instance_name="ins",
        phase_name="1_mcf_preemptive_schedule",
    )
    for p in (diag, last_stage, phase):
        assert p.parent.name == "progress", p


def test_ffc_report_kinds_routed_to_report_zone(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    main_g = layout.artifact_path("gantt_png", scenario_name="sc", instance_name="ins")
    phase_g = layout.artifact_path(
        "phase_gantt_png",
        scenario_name="sc",
        instance_name="ins",
        phase_name="6_dispatched_schedule",
    )
    ls_cpsat_g = layout.artifact_path(
        "last_stage_cp_sat_gantt_png", scenario_name="sc", instance_name="ins"
    )
    for p in (main_g, phase_g, ls_cpsat_g):
        assert p.parent.name == "report", p
        assert p.suffix == ".png"


def test_run_scope_kinds_resolve_at_run_root(tmp_path: Path) -> None:
    layout = _layout(tmp_path, run_id="r1")
    summary = layout.artifact_path("summary_csv")
    xlsx = layout.artifact_path("report_xlsx")
    dashboard = layout.artifact_path("mcf_lb_dashboard")
    assert summary.name == "r1_summary.csv"
    assert xlsx.name == "r1_report.xlsx"
    assert dashboard.name == "r1_mcf_lb_dashboard.html"


def test_scenario_scope_kinds_resolve_under_scenario_dir(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    analysis = layout.artifact_path("mcf_lb_analysis", scenario_name="sc")
    stats = layout.artifact_path("scenario_statistics", scenario_name="sc")
    assert analysis.parent.name == "sc"
    assert stats.parent.name == "sc"


def test_phase_gantt_template_uses_phase_name(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    p = layout.artifact_path(
        "phase_gantt_png",
        scenario_name="sc",
        instance_name="ins",
        phase_name="6_dispatched_schedule",
    )
    assert p.name == "ins_6_dispatched_schedule_gantt.png"


def test_unknown_kind_raises(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    with pytest.raises(KeyError):
        layout.artifact_path("not_a_real_kind")


def test_stamp_round_trips_overlay_kinds(tmp_path: Path) -> None:
    """`stamp()` writes a yaml that contains every kind registered by overlay,
    so a downstream reader can resolve ffc kinds without re-applying the
    overlay."""
    layout = _layout(tmp_path)
    stamped = layout.stamp()
    text = stamped.read_text(encoding="utf-8")
    for kind in (
        "mcf_lb_diagnostic",
        "mcf_lb_phase_schedule",
        "gantt_png",
        "phase_gantt_png",
        "last_stage_cp_sat_gantt_png",
        "report_xlsx",
        "mcf_lb_dashboard",
        "rpdf_comparison_csv",
    ):
        assert kind in text, kind
