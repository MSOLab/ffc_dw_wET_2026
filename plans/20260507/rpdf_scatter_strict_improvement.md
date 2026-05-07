# Plan: Filter per-scenario scatter markers to strict global improvements + subroutine endpoints

## Context

`output/20260507_debug/20260507T184327_753817/mcf_lb_best_neh_cp_best_base_cpsat/summary_method_rpdf_and_norm_time_scatter.html` shows many `neh_cp` markers stacked at the **same RPDf** with **different Time%** values (e.g. 38 markers all at y=0.14447 for `Instance_50_5_3_0,2_0,2_10_Rep0`). Inside that `neh_cp` call the per-point obj_value is improving, but it never beats the global best already established by the prior `mcf_lb` step. Because the chart's marker y comes from `_lookup_rpdf_at_or_before(progression_points, marker_time)` (i.e. the **global** running best at the marker's time), every one of those points renders at the same y — a flat horizontal cluster.

The fix is to draw a marker only when it carries new information: the point either strictly improves the global running min, or it is a subroutine call's endpoint (always shown so the user can still see when each call ended).

## Scope

- Affects only the per-scenario chart `summary_method_rpdf_and_norm_time_scatter.html`, emitted by `export_method_rpdf_scatter_html` in `src/ffc_ddw_sum_et/report/rpdf_scatter_chart.py`.
- The run-level `multi_scenario_subroutine_flow_comparison_html` is **out of scope**: it only renders mean lines + one vertical-guide marker per subroutine, so it does not have the cluster issue.
- `obj_log_loader.py` (the upstream that builds `raw_progression_df`) is **not** modified — the filter belongs at the chart layer so other consumers of the loader are unaffected.

## Critical files

- `src/ffc_ddw_sum_et/report/rpdf_scatter_chart.py` — filter logic added here.
- `tests/report/test_rpdf_scatter_chart.py` — focused tests for the filter.

## Design

### Filter rule (strict global improvement OR call endpoint)

Sort the instance's full progression by `norm_time` (with `global_sec` as tiebreaker). Track a single running min of `rpd_f` across the entire trajectory (not per call). Keep a row iff:

- `rpd_f < running_min` (strict global improvement), updating running_min; OR
- it is the last row of its `call_index` group (endpoint, always kept).

A first-attempted variant that tracked the running min **per call** was insufficient: per-call obj_value can be strictly decreasing while every per-call point still sits above the global best, which is exactly the failure mode the bug report described. The global-min variant guarantees each non-endpoint marker sits at a distinct y.

### Where to apply

New private helper `_keep_strict_global_improvements_or_endpoints(progression_grp)` lives in `rpdf_scatter_chart.py` and is applied **once** in `_build_raw_instance_progression`, before computing both `raw_meta` and `points`.

Filtering once and feeding both:
- `raw_meta` drives the scatter markers — the user-visible target of this fix.
- `points` drives the best-so-far line via `_build_step_path`. Filtering here is a no-op for the line shape (non-improving intermediate rows contribute nothing to a step path, and endpoints sit at the running best so they don't draw a downward step). Sharing one filtered frame avoids the duplicate-time error from `_build_marker_meta_by_time` that would otherwise trip if the markers and the line consumed different frames.

### Endpoint semantics

A retained subroutine endpoint that did not improve the global best still appears as a marker. Its y-value is taken from `_lookup_rpdf_at_or_before(progression_points, m.time)`, which returns the global best-so-far at that time — not the call's actual ending obj_value. The marker therefore reads as "this subroutine ended at Time%=X; running best at that time is Y", matching the existing semantic.

## Verification

1. **Unit-level**: `tests/report/test_rpdf_scatter_chart.py` covers
   - Plateau-within-one-call `[0.10, 0.10, 0.10, 0.08]` → `[0.10, 0.08]`.
   - A whole call sitting above the global min → only its endpoint survives.
   - Mixed strict global improvements + plateaus across multiple calls.
   - End-to-end through `_build_html_payload`, including a regression that reproduces the user's `neh_cp` cluster (4 intra-call points above the global best collapse to just the call endpoint).

2. **End-to-end**: `uv run python scripts/build_subroutine_flow_charts.py output/20260507_debug/20260507T184327_753817` regenerates the HTML. Confirmed the `neh_cp` markers for `T=0.2, R=0.2` instances drop from 38–43 down to 1 (just the endpoint), and `solve_base_model_cpsat` markers all sit at distinct y-values (each one a strict global improvement, plus the endpoint).

3. **Lint + existing tests**: `uv run ruff check`, `uv run ruff format`, `uv run pytest tests/report` — all pass (10 tests).
