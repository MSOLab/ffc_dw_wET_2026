# Plan — MCFPreemptiveSchedule Gantt rendering

## Context
`run_mcf_lb` / `run_mcf_lb_4` already produce an `MCFPreemptiveSchedule` in Phase 1 (one stage; each `(job, machine)` may have multiple disjoint `[start, end)` segments) and carry it on `MCFLBResult`, but it is never dumped or plotted. Meanwhile the existing `GanttPlotter` ([src/ffc_ddw_sum_et/io/gantt.py](src/ffc_ddw_sum_et/io/gantt.py)) only consumes `(job, stage, machine) → start/end` maps — one segment per key — so preemptive data can't reach the existing reporter path. Add end-to-end rendering (domain → IO YAML → Gantt PNG) that mirrors the existing `FFcSchedule` pipeline without duplicating the plotter.

## Approach

Single public entry point on `GanttPlotter` that accepts segments; separate YAML suffix + separate reporter worker so schemas don't conflate.

### 1. Plotter classes — introduce `PlotterBase` and `PreemptiveGanttPlotter` ([src/ffc_ddw_sum_et/io/gantt.py](src/ffc_ddw_sum_et/io/gantt.py))

Refactor the existing single-class module into a small inheritance hierarchy. Do not change the public output of `GanttPlotter` for non-preemptive input.

- `PlotterBase` — abstract-ish base carrying everything that doesn't depend on schedule shape:
  - state: `fig`, `ax`
  - class constants: `cmap_name`, `machine_height`, `bar_height`, `bar_alpha`, `grid_alpha`, `figsize`
  - lifecycle helpers: `__init__`, `_ensure_figure`, `close`
  - drawing primitives: `draw_operation_bar`, `create_job_to_color_map`
  - lane layout: `create_machine_lanes` (static — already schedule-shape-independent)
  - axes finalization: a `_finalize_axes(machine_labels, machine_lane_count, title)` helper that sets yticks/ylim/xlabel/title/grid/invert
  - `set_x_horizon(earliest, latest, force_start, force_end)` accepting scalars (subclasses compute their own horizon)
  - `display` / `export` as template methods that delegate to `plot`, which each subclass overrides.
- `GanttPlotter(PlotterBase)` — keeps the exact current public signature (`plot`/`display`/`export` with `start_time_map` + `end_time_map`). Its own `compute_horizon` (over maps) stays here; `draw_operation_bars` stays here, consuming the maps. Output is byte-identical to today.
- `PreemptiveGanttPlotter(PlotterBase)` — new. Public surface mirrors `GanttPlotter` but keyed on segments:
  - `plot(segments, *, stage_id, machines, jobs=None, all_jobs=None, highlight_op_set=None, force_start=None, force_end=None)`
  - `display(...)` / `export(file_path, ...)` with matching kwargs.
  - Internal `compute_horizon(segments)` and `draw_segment_bars(segments, job_to_color, machine_to_y, job_list, highlight_op_set)` — iterates segments and calls inherited `draw_operation_bar` per bar. Multiple disjoint segments per `(job, machine)` produce multiple rectangles naturally.
  - Reuses `create_machine_lanes({stage_id: machines})` via the single-element stage list.

### 2. Domain converter ([src/ffc_ddw_sum_et/solution/mcf_preemptive_schedule.py](src/ffc_ddw_sum_et/solution/mcf_preemptive_schedule.py))

- Add `to_gantt_segments(self) -> list[tuple[str, str, str, int, int]]` returning `(job_id, stage_id, machine_id, start, end)` — symmetrical with the existing `from_flow_dict` constructor. Pure data, no io/matplotlib imports.

### 3. IO YAML helpers ([src/ffc_ddw_sum_et/io/schedule_yaml.py](src/ffc_ddw_sum_et/io/schedule_yaml.py))

Extend (don't split) — generic shapes only, no `MCFPreemptiveSchedule` import:
- `dump_preemptive_schedule_yaml(path, *, instance_name, stage_id, machines, jobs, segments, all_jobs=None, obj_value=None, obj_bound=None) -> None`
  - YAML top-level: `instanceName`, `objValue`, `objBound`, `stageId`, `jobs`, `machinesPerStage: {stage_id: [...]}`, `segments: [{job, stage, machine, start, end}, ...]`.
- `load_preemptive_schedule_yaml(path) -> dict[str, Any]` — thin mirror of `load_schedule_yaml`.
- Export both from [src/ffc_ddw_sum_et/io/__init__.py](src/ffc_ddw_sum_et/io/__init__.py).

### 4. Controller + runner wiring

- [src/ffc_ddw_sum_et/orchestration/controller.py](src/ffc_ddw_sum_et/orchestration/controller.py): add `self.mcf_preemptive_schedule: MCFPreemptiveSchedule | None = None`; set from `phase1.mcf_preemptive_schedule` inside both `run_mcf_lb` and `run_mcf_lb_4`, next to the existing `self.last_stage_cp_sat_solution` assignment.
- [src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py](src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py) (after the `last_stage_cp_sat` dump at ~line 190): `getattr(controller, "mcf_preemptive_schedule", None)`, and if present, dump `{ins_name}_mcf_preemptive_schedule.yaml` with `segments=sched.to_gantt_segments()`, `jobs` taken from the instance's `job_id_list` so colors are stable across artifacts.

### 5. Reporter ([src/ffc_ddw_sum_et/orchestration/reporting.py](src/ffc_ddw_sum_et/orchestration/reporting.py))

- In `_generate_gantt_charts` (line 528): exclude `*_mcf_preemptive_schedule.yaml` from the existing `*_schedule.yaml` glob (filter by `p.name.endswith("_mcf_preemptive_schedule.yaml")`), then add a second glob for that suffix.
- Add module-level `_render_preemptive_gantt_from_yaml(yaml_path: Path) -> None`: loads the YAML, reconstructs `segments`, calls `PreemptiveGanttPlotter().export(png_path, segments, stage_id=stage_id, machines=machines, jobs=jobs, all_jobs=jobs)`. PNG filename: replace `_mcf_preemptive_schedule` with `_mcf_preemptive_gantt`.
- Both workers share the same `ProcessPoolExecutor`.

## Files to modify

- [src/ffc_ddw_sum_et/io/gantt.py](src/ffc_ddw_sum_et/io/gantt.py) — introduce `PlotterBase` and `PreemptiveGanttPlotter`; keep `GanttPlotter` public API unchanged.
- [src/ffc_ddw_sum_et/io/schedule_yaml.py](src/ffc_ddw_sum_et/io/schedule_yaml.py) — preemptive YAML dump/load.
- [src/ffc_ddw_sum_et/io/__init__.py](src/ffc_ddw_sum_et/io/__init__.py) — re-export.
- [src/ffc_ddw_sum_et/solution/mcf_preemptive_schedule.py](src/ffc_ddw_sum_et/solution/mcf_preemptive_schedule.py) — `to_gantt_segments`.
- [src/ffc_ddw_sum_et/orchestration/controller.py](src/ffc_ddw_sum_et/orchestration/controller.py) — expose schedule.
- [src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py](src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py) — dump YAML.
- [src/ffc_ddw_sum_et/orchestration/reporting.py](src/ffc_ddw_sum_et/orchestration/reporting.py) — render PNG.

## Reused utilities (no new primitives)

- `PlotterBase.draw_operation_bar` / `create_machine_lanes` / `create_job_to_color_map` (shared by both subclasses)
- `routix.io.dump_yaml` / `load_yaml` (used by `schedule_yaml.py`)
- `ProcessPoolExecutor` gated by `draw_gantt` + `painter_thread_cnt`

## Verification

1. `uv run ruff check` and `uv run ruff format` — clean.
2. Run an existing MCF-LB experiment config end-to-end:
   - `uv run python main.py` with a `1_mcf_lb_*` config so `run_mcf_lb` fires.
   - Inspect the output directory: expect `{ins_name}_mcf_preemptive_schedule.yaml` alongside `{ins_name}_schedule.yaml` and `{ins_name}_last_stage_cp_sat_schedule.yaml`.
   - Expect `{ins_name}_mcf_preemptive_gantt.png` next to the existing Gantt PNGs.
3. Regression — open the legacy `{ins_name}_gantt.png` and confirm it is visually identical to a pre-change render (extracting shared helpers into `PlotterBase` must not change `GanttPlotter` output).
4. Preemptive PNG sanity — open one `{ins_name}_mcf_preemptive_gantt.png`:
   - same stage row count as the last stage of the non-preemptive chart;
   - a single job's color appears in multiple disjoint bars when the MCF flow is preemptive;
   - job colors match the full-schedule chart (verifies `all_job_list` plumbing).
5. `draw_gantt=False` path: PNGs skipped, YAML still written (parity with existing behavior).

## Risks / notes

- Each subclass owns its own `compute_horizon` (maps vs. segments); preserve the existing empty-input `ValueError` in `GanttPlotter` and add the parallel check in `PreemptiveGanttPlotter`.
- Preemptive YAML must be filtered out of the legacy glob — otherwise the existing worker reads zero `operations` and silently returns, hiding the new artifact.
- Plan file location: per `feedback_plan_location`, once approved this plan should be moved to `plans/experiment/20260420/mcf-preemptive-gantt.md` in-repo before implementation starts.
