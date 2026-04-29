"""Tests for scenario name + output_subdir duplicate validation (§ 7.3).

The doc captures a prior incident where two scenarios silently shared an
output directory, overwriting each other's results. Two defensive layers:

1. `main._validate_scenario_uniqueness` (config-load time)
2. `ArtifactLayout.scenario_dir` (registration time)

Both must raise on duplicates so that one layer catching it cannot silently
hide a programming mistake.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from main import _validate_scenario_uniqueness
from ffc_ddw_sum_et.orchestration.artifact_layout import FFcArtifactLayout


def test_unique_names_pass() -> None:
    _validate_scenario_uniqueness(
        [
            {"name": "alpha", "output_subdir": "alpha_dir"},
            {"name": "beta", "output_subdir": "beta_dir"},
        ]
    )


def test_duplicate_name_raises() -> None:
    with pytest.raises(ValueError, match="duplicate scenario coordinates"):
        _validate_scenario_uniqueness(
            [
                {"name": "alpha", "output_subdir": "alpha_dir"},
                {"name": "alpha", "output_subdir": "other_dir"},
            ]
        )


def test_duplicate_output_subdir_raises() -> None:
    with pytest.raises(ValueError, match="duplicate scenario coordinates"):
        _validate_scenario_uniqueness(
            [
                {"name": "alpha", "output_subdir": "shared_dir"},
                {"name": "beta", "output_subdir": "shared_dir"},
            ]
        )


def test_subdir_defaults_to_name() -> None:
    """When ``output_subdir`` is omitted, it falls back to ``name``; two
    scenarios with the same name therefore collide on both axes."""
    with pytest.raises(ValueError, match="duplicate scenario coordinates"):
        _validate_scenario_uniqueness(
            [{"name": "x"}, {"name": "x"}]
        )


def test_layout_scenario_dir_blocks_duplicate(tmp_path: Path) -> None:
    """Stage-2 defense: even if the config check is bypassed (e.g., the
    scenarios are constructed in code), the layout itself refuses to register
    the same scenario twice."""
    layout = FFcArtifactLayout(run_root=tmp_path / "run", run_id="run")
    layout.scenario_dir("alpha")
    with pytest.raises(ValueError, match="already registered"):
        layout.scenario_dir("alpha")


def test_layout_scenario_dir_accepts_distinct_names(tmp_path: Path) -> None:
    layout = FFcArtifactLayout(run_root=tmp_path / "run", run_id="run")
    a = layout.scenario_dir("alpha")
    b = layout.scenario_dir("beta")
    assert a != b
    assert a.name == "alpha"
    assert b.name == "beta"
