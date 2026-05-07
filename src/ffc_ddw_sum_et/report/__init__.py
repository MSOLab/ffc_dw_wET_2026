"""Per-instance trajectory reporting for ffc_ddw_sum_et.

Reads the on-disk ``<instance>_obj_log.json`` + sibling
``<instance>_instance_result.yaml`` produced by
``FFcDDWSingleInstanceRunner._save_obj_log`` / manifest writer, and renders
two interactive HTML charts adapted from the hybridflowshop project:

* per-scenario ``summary_method_rpdf_and_norm_time_scatter.html``
* run-level   ``<run_id>_multi_scenario_subroutine_flow_comparison.html``

Public entry points for callers (reporting pipeline + offline scripts):

* :func:`build_endpoint_df`
* :func:`build_raw_progression_df`
* :func:`load_baseline_df`
* :func:`export_method_rpdf_scatter_html`
* :func:`export_multi_scenario_method_rpdf_comparison_html`
* :func:`write_post_run_subroutine_chart_artifacts`
"""

from __future__ import annotations

from .multi_scenario_method_chart import (
    export_multi_scenario_method_rpdf_comparison_html,
)
from .obj_log_loader import (
    CallSegment,
    InstanceProgression,
    ProgPoint,
    build_endpoint_df,
    build_raw_progression_df,
    iter_scenario_instance_progressions,
    load_instance_progression,
)
from .post_run_chart_writer import (
    load_baseline_df,
    write_post_run_subroutine_chart_artifacts,
)
from .rpdf_scatter_chart import export_method_rpdf_scatter_html

__all__ = [
    "CallSegment",
    "InstanceProgression",
    "ProgPoint",
    "build_endpoint_df",
    "build_raw_progression_df",
    "export_method_rpdf_scatter_html",
    "export_multi_scenario_method_rpdf_comparison_html",
    "iter_scenario_instance_progressions",
    "load_baseline_df",
    "load_instance_progression",
    "write_post_run_subroutine_chart_artifacts",
]
