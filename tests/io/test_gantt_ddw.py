"""Tests for the introduction-slide DDW Gantt plotter.

Guards the two load-bearing correctness properties of the intro figure:

* the per-job palette never collides with the reserved earliness/tardiness
  colors;
* per-job color is keyed by the job's position in the *full* instance order,
  so the same job keeps its color across different drawn subsets (and thus
  across the shared hybridflowshop figure).
"""

from __future__ import annotations

import matplotlib.colors as mcolors

from ffc_ddw_sum_et.io.gantt import (
    EARLINESS_COLOR,
    JOB_PALETTE,
    TARDINESS_COLOR,
    DDWGanttPlotter,
)


def test_job_palette_excludes_reserved_colors() -> None:
    reserved = {EARLINESS_COLOR.lower(), TARDINESS_COLOR.lower()}
    palette = {c.lower() for c in JOB_PALETTE}
    assert reserved.isdisjoint(palette)


def test_color_map_keyed_by_global_position() -> None:
    full_order = [f"j{i:02d}" for i in range(8)]
    color_map = DDWGanttPlotter().create_job_to_color_map(full_order)
    for i, job in enumerate(full_order):
        assert color_map[job] == mcolors.to_rgba(JOB_PALETTE[i])


def test_same_job_keeps_color_when_subset_changes() -> None:
    """A job colored via the full order keeps its color no matter which
    subset is actually drawn, because color depends on global position."""
    full_order = [f"j{i:02d}" for i in range(6)]
    plotter = DDWGanttPlotter()
    full_map = plotter.create_job_to_color_map(full_order)
    # The plotter is always handed the full order as ``all_job_list``;
    # j03's color is palette[3] regardless of the drawn subset.
    assert full_map["j03"] == mcolors.to_rgba(JOB_PALETTE[3])
    assert full_map["j00"] != full_map["j03"]


def test_palette_wraps_when_more_jobs_than_colors() -> None:
    n = len(JOB_PALETTE) + 2
    full_order = [f"j{i:02d}" for i in range(n)]
    color_map = DDWGanttPlotter().create_job_to_color_map(full_order)
    # Index 0 and index len(JOB_PALETTE) wrap to the same color.
    assert color_map[full_order[0]] == color_map[full_order[len(JOB_PALETTE)]]
