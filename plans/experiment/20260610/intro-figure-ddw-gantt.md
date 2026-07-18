# Plan: Introduction-slide DDW Gantt — per-job colors, auto due windows, E/T color coding

## Context

The thesis introduction slide (`Juntaek-PhD-Thesis/vault/introduction_3_fssp.pdf`)
illustrates the three studied problems side by side. The **rightmost** figure is
this repository's problem `FFc | d_j^-, d_j^+ | sum w_j^- E_j + w_j^+ T_j`.

The figure currently shown was produced by the **partition debug visualization**
`render_partition_gantt_svg()` in
`src/ffc_ddw_sum_et/algorithm/sw_cp/visual.py`, which colors each operation by its
*partition region* (`REGION_COLORS`: LTF / LPF / UNFIXED / RPF / RTF), **not by
job**. That viz is debug-only and must stay as-is; it is the wrong artifact for
the slide.

Shared instance (single source of truth for both this figure and the middle
`FFc||C_max` figure in `hybridflowshop`):

```
benchmarks/PRA2017/small/Instance_20_2_2_0,2_0,2_10_Rep0.txt
  20 jobs, 2 stages, 2 machines/stage (total_m = 4)
  processing times block (alternating stage_k p_k pairs)
  RELDUE block:  r_j(=-1)  d_j  w_j^-(earliness wt)  w_j^+(tardiness wt)
  DDW block:     d_j^-  d_j^+   (the 20 due windows)
```

Three problems to fix (user-stated), plus one cross-figure requirement:

1. **Per-job color coding is broken** — current figure is region-colored, not
   per-job.
2. **20 due windows must be drawn automatically** — `[d_j^-, d_j^+]` for every
   job; cannot be hand-drawn.
3. **Earliness/tardiness must be color-coded** — tardiness in **bright red**
   (matching the left `Fc|prmu|sumTj` figure), earliness in **blue**. Therefore
   **red and blue are reserved** and must NOT appear in the per-job palette.
4. **Job count must be user-configurable** — 20 jobs is too dense to read; the
   figure must be rendered for a user-chosen subset (e.g. 6 jobs).

## Design

Produce the slide figure from the **final/derived schedule** rendered through a
per-job plotter, NOT through the partition debug viz.

**Output format: SVG (vector).** The figure is exported as `.svg` via
matplotlib `savefig` (extension-inferred format); `dpi` is irrelevant for
vector output. This matches the partition viz, which is also SVG.

### 1. Per-job palette (red/blue reserved)

`PlotterBase.create_job_to_color_map()` (a method, not a free function) in
`src/ffc_ddw_sum_et/io/gantt.py` already colors per job, but via `tab20`, which
**contains blue (`#1f77b4`) and red (`#d62728`)**. Replace the per-job colormap
with a **curated qualitative palette that excludes the red and blue families**
(`JOB_PALETTE` module constant).

**Color must key off the global job index, not subset position.** The base
method assigns color by *position in the list it is handed*; if we hand it the
6-job subset, job `j03`'s color depends on its rank within the subset, so the
same job gets different colors in different subsets — breaking cross-figure
correspondence. Fix: assign `JOB_PALETTE[i]` where `i` is the job's position in
the **full instance `job_id_list`** (pass `all_job_list` = the full 20-job
order). Then job `j` keeps its color across any subset.

Cross-repo note: a true single source of truth is impossible across two repos.
`JOB_PALETTE` is a hardcoded list that must be **copied verbatim** into
`hybridflowshop`, and both figures must pick the **same job-id subset in the
same order**, for job `j` to share a color in both.

### 2. Auto-drawn due windows (per job, from data)

Pull `[d_j^-, d_j^+]` from the instance:
`FFcDDWParameters.job_2_due_window_map` (a.k.a. `job_2_dw_lb_map` /
`job_2_dw_ub_map`) in `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py`.

For each drawn job, render its due window automatically. Style: a per-job strip
beneath the gantt machine lanes, **one row per job** (NOT one column — a
column-per-job categorical x would conflict with the time x-axis). Each row
shares the gantt's **time x-axis** and shows a job-colored band spanning
`[d_j^-, d_j^+]` with `d_j^-`/`d_j^+` end markers. The number of rows follows
the job subset (no hand-drawing).

### 3. Earliness/tardiness color coding

Compute each job's completion `C_j = schedule.get_job_end_time(last_stage, j)`
(direct accessor; in a flow shop the last-stage end *is* the completion). Then,
consistent with `solution/objectives.py:33-34`:

```
E_j = max(d_j^- - C_j, 0)   -> draw a BLUE segment from C_j to d_j^- in the job's strip row
T_j = max(C_j - d_j^+, 0)   -> draw a RED  segment from d_j^+ to C_j in the job's strip row
```

Reserved colors: `EARLINESS_COLOR = "#1f77b4"` (blue), `TARDINESS_COLOR =
"#d62728"` (red) — both excluded from `JOB_PALETTE` (see #1). The E/T segment is
drawn at the same time-axis position as the window, so it is geometrically
consistent with the gantt above.

### 4. Configurable job count

Subset the instance **before** scheduling and drawing using the existing
`FFcDDWParameters.create_instance_of_job_subset(instance, job_id_subset)`. Expose
the subset as a config knob (job count or explicit job-id list) so the figure is
legible.

## Files to modify

- `src/ffc_ddw_sum_et/io/gantt.py` — add `JOB_PALETTE`, `EARLINESS_COLOR`,
  `TARDINESS_COLOR` constants and a `DDWGanttPlotter(GanttPlotter)` subclass:
  curated red/blue-free palette keyed by global job index; an `export_ddw(...)`
  method that draws the machine-lane gantt plus a per-job strip with due
  windows + E/T segments, saved as `.svg`.
- `scripts/render_intro_ddw_gantt.py` — figure-generation entry: read config
  yaml -> load instance via `FFcDDWParameters.from_pra_2017_data` -> subset jobs
  -> derive a schedule with `NehCpDispatcher` -> compute `C_j` -> export the SVG.
  (There is **no** `io/reporting.py`/`draw_gantt`; the existing render path is
  `orchestration/reporting.py` calling `GanttPlotter().export()`. We reuse the
  `GanttPlotter.export` machinery, not a `draw_gantt` symbol.)
- `metadata/20260610/intro_ddw_figure.yaml` — config: benchmark instance, job
  subset (explicit ids or count), cp time limit, output path, title.

Do **NOT** modify `algorithm/sw_cp/visual.py` — the partition viz remains
debug-only and is not the slide artifact.

## Schedule source (was under-specified)

The 6-job subset schedule is built by `NehCpDispatcher().run(AlgSpec(instance=
subset, option=NehCpOption(cp_tl_seconds=...)))`; `record.result.schedule` is a
fully built `FFcSchedule`. Start/end maps come from
`schedule.get_jik_2_start_time_map()` / `get_jik_2_end_time_map()`.

## Existing utilities reused

- `io/gantt.py`: `GanttPlotter`, `PlotterBase.create_job_to_color_map`,
  `plot`/`export`.
- `parameters/ffc_ddw_params.py`: `job_2_due_window_map`, `job_2_dw_lb_map`,
  `job_2_dw_ub_map`, `job_2_ewt_map`, `job_2_twt_map`,
  `create_instance_of_job_subset`.
- `algorithm/neh_cp`: `NehCpDispatcher`, `NehCpOption`;
  `algorithm/base/alg_spec.AlgSpec`.
- `solution/ffc_schedule.py`: `get_jik_2_start_time_map`,
  `get_jik_2_end_time_map`, `get_job_end_time`.
- `solution/objectives.py`: E/T sign conventions (reuse, do not re-derive).

## Verification

Render with a small subset (e.g. 6 jobs) and confirm:
- every job has a distinct color, none red or blue;
- one due window per drawn job, values matching the `DDW` block;
- jobs finishing before `d_j^-` show a blue earliness mark; jobs finishing after
  `d_j^+` show a red tardiness mark; signs match `objectives.py`;
- the same job-id maps to the same color that `hybridflowshop` uses for the
  middle figure.

## Open decisions (resolved)

- Due-window rendering: **per-job strip, one row per job**, sharing the gantt
  time x-axis (resolved — column-per-job rejected as geometrically
  inconsistent).
- Job subset: **explicit job-id list** in the config (default `j00..j05`, 6
  jobs) — pinned so `hybridflowshop` can use the identical subset.
- Palette: fixed `JOB_PALETTE` qualitative list excluding red/blue, indexed by
  global job position, copied verbatim into `hybridflowshop`.

## Output

`.svg` written to the config's `output_path` (default
`analysis/20260610/intro_ddw_gantt.svg`).
