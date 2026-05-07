"""Shared chart-template constants used by ``rpdf_scatter_chart`` and
``multi_scenario_method_chart``.

Both chart modules emit Plotly HTML and previously duplicated identical
``SERIES_COLORS`` and ``SUBROUTINE_SYMBOL_MAP`` JS literals inline. Adding
or renaming a subroutine then required editing both files in lockstep.

This module owns the canonical Python-side values; the chart modules
inject them as JSON via their respective template substitution
(``.format`` for ``rpdf_scatter_chart``, ``string.Template`` for
``multi_scenario_method_chart``).
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
    "neh_cp_full_sch_from_mcf_lb": "square",
    "single_pass_full_sch_from_mcf_lb": "triangle-up",
    "neh_cp_last_stage_only_sch_from_mcf_lb": "square-open",
    "single_pass_last_stage_only_sch_from_mcf_lb": "triangle-down",
    "run_last_stage_cp_sat_lb": "x",
    "run_mcf_lb_4": "star",
}


def series_colors_json() -> str:
    """Return ``SERIES_COLORS`` as a compact JSON array literal."""
    return json.dumps(list(SERIES_COLORS), separators=(",", ":"))


def symbol_map_json() -> str:
    """Return ``SUBROUTINE_SYMBOL_MAP`` as a compact JSON object literal."""
    return json.dumps(SUBROUTINE_SYMBOL_MAP, separators=(",", ":"))
