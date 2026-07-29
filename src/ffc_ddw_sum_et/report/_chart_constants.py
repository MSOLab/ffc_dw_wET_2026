"""Shared chart-template constants used by ``rpdf_scatter_chart``,
``multi_scenario_method_chart``, and ``method_mean_scatter``.

All three chart modules emit Plotly HTML. ``SERIES_COLORS``,
``SUBROUTINE_SYMBOL_MAP``, and ``HOVER_PERCENT_DECIMALS`` were previously
duplicated inline or in separate modules. Adding or renaming a subroutine
then required editing multiple files in lockstep.

This module owns the canonical Python-side values; the chart modules
inject them as JSON via their respective template substitution
(``.format`` for ``rpdf_scatter_chart``, ``string.Template`` for
``multi_scenario_method_chart`` and ``method_mean_scatter``).
"""

from __future__ import annotations

import json

SERIES_COLORS: tuple[str, ...] = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
)

SUBROUTINE_SYMBOL_MAP: dict[str, str] = {
    "calc_mcf_lb_and_derive_full_sch": "diamond",
    "solve_base_model_cpsat": "circle",
    "apply_lb_by_mcf": "square",
    "heuristic_last_stage_only_sch_from_mcf_lb": "triangle-up",
    "build_full_sch_from_last_stage_only_sch": "triangle-down",
    "coarsen_solve_reconstruct": "cross",
}

# Decimal places for percent values in hover readouts (RPDf and Time%
# alike) across every chart. Axis ticks deliberately stay at 1 decimal —
# ticks read the scale, hover reads the data, and a ".3%" tick label is
# both long and prone to collisions on the x axis. Raising this must move
# every axis of every chart together; do not hardcode a literal instead.
HOVER_PERCENT_DECIMALS = 3


def series_colors_json() -> str:
    """Return ``SERIES_COLORS`` as a compact JSON array literal."""
    return json.dumps(list(SERIES_COLORS), separators=(",", ":"))


def symbol_map_json() -> str:
    """Return ``SUBROUTINE_SYMBOL_MAP`` as a compact JSON object literal."""
    return json.dumps(SUBROUTINE_SYMBOL_MAP, separators=(",", ":"))
