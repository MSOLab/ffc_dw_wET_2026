# Plan: Replace in-window outline with `x_jt = 1` cells, add `t < r_j` grey shading

> Status: shipped. This document records the final design as implemented;
> a few decisions were revised during iteration and the body below reflects
> the revisions, not the original Phase-1 sketch.

## Context

The signed C-cost heatmap originally drew **black hollow rectangles** —
one per row, spanning the in-window placement band
`[max(0, d⁺ − p), max(0, d⁺ − p) + p]`. That overlay showed where each
job *could* complete at its latest in-window time, but it had nothing to
do with the MCF preemptive solution that the heatmap is meant to
illustrate. Two changes:

1. **Replace** the per-row in-window outline with **per-cell highlights at
   every `(j, t)` where `x_jt = 1` in the MCF preemptive solution**, so the
   overlay shows the actual placement chosen by the relaxation. Contiguous
   `(t, t+1, …)` runs in a row are merged into a single rectangle.
2. **Add** a grey-shaded region for cells with `t < r_j` (release-time
   blocked region), matching the convention already used in
   `benchmarks/PRA2017/visualize_wET_cost.py:122-133`.

The chart title was also reformatted to surface the bound and the cutoff:

```
Last stage only preemptive schedule on C_jt heatmap - {instance_name}
(objValue: {mcf_lb} | C_jt cutoff: {clip_threshold})
```

## Critical Files

- `src/ffc_ddw_sum_et/io/parallel_mc_cost_heatmap.py` — builder + figure +
  YAML dump/load. Most edits land here.
- `src/ffc_ddw_sum_et/orchestration/controller.py` — `apply_lb_by_mcf`
  Phase A passes MCF flow + LB into the builder.
- `benchmarks/PRA2017/visualize_parallel_mc_cost.py` — CLI script also
  solves MCF so the same overlays render offline.
- `src/ffc_ddw_sum_et/orchestration/reporting.py` — Phase B renderer.

## Existing Functions / Patterns Reused

- `ParallelMachinePreemptionMcf.get_variable_value_dict() -> dict[str, dict[int, int]]`
  in `algorithm/parallel_mc_pmtn.py:183` — sparse `{j: {t: flow}}` for each
  `(j, t)` arc with nonzero flow. We already solve the MCF in
  `apply_lb_by_mcf` via `solve_mcf_lb`, which retains the `mcf` handle on
  `McfLbResult.mcf`. No re-solve.
- `ParallelMachinePreemptionMcf.get_obj_value()` for the title's
  `objValue` field.
- `instance.get_job_2_p_sum_except_last_stage() -> dict[str, int]` for `r_j`.
- The grey-rect drawing pattern in `visualize_wET_cost.py:122-133`.

## Drawing Approach

Per-cell overlays are the visual goal, but `plotly.add_shape` does not
scale well to thousands of shapes (a 150-job instance has ≈ 7,500 cells
where `x_jt = 1`). Three approaches were tried in order; the last one is
what shipped.

| Attempt | Outcome |
| --- | --- |
| 1. One `add_shape` rect per cell | Correct, but rendering took minutes — `add_shape` per-cell does not scale. |
| 2. Single `go.Scatter` trace (`mode="markers"`, `square-open`) | ~10 s, fast. Marker size is in pixels though, so the squares mis-aligned with the heatmap cell boundaries at non-default zoom. Also forced numeric y-axis with `tickvals`/`ticktext` (and `customdata` to keep job-name hover), which roughly doubled the HTML size. |
| 3. **Single `add_shape(type="path")` with multi-subpath SVG**, runs merged per row | What shipped. ~10 s render, 4.5 MiB HTML, data-coordinate accurate at any zoom, categorical y-axis preserved (so hover keeps `%{y}` showing the job label without `customdata` bloat). |

The shipped overlay logic in `make_figure`:

- **Grey r_j region** — `add_shape(type="rect")` per row, ≤ n shapes,
  `fillcolor="rgba(127,127,127,1)"`, `line.width=0`. Only emitted when
  `r_j > t_axis[0]`.
- **`x_jt = 1` cells** — group `x_cells` by row index, sort each row's
  `t` values, fold consecutive `t` into runs, emit each run as a closed
  SVG subpath `M{x0},{y0}L{x1},{y0}L{x1},{y1}L{x0},{y1}Z`. All subpaths
  are concatenated into a single string and passed to one
  `add_shape(type="path", line={"color": "black", "width": 1}, fillcolor="rgba(0,0,0,0)")`.

Run-merging is performed inside `make_figure`, not on the dataclass — the
canonical YAML payload stores cells, leaving room to render differently
later without regenerating data.

## Time Horizon Extension

The original horizon was
`[max(0, min(d⁻ − p) − p_max), max(d⁺) + p_max]`. Across the 50-instance
sweep this turned out to be too narrow on some instances — MCF placed
flow on cells outside the window. The horizon is now widened to also
contain every `x_jt = 1` cell:

```python
if x_jt_map is not None:
    x_ts = [t for jm in x_jt_map.values() for t, flow in jm.items() if flow > 0]
    if x_ts:
        t_min = min(t_min, min(x_ts))
        t_max = max(t_max, max(x_ts))
```

The matrix-only path (no `x_jt_map`) keeps the original horizon.

## Data Model

`SignedCostHeatmapData` is a frozen dataclass. Required fields first,
defaulted fields last (no `kw_only`).

```python
@dataclass(frozen=True, slots=True)
class SignedCostHeatmapData:
    y_labels: list[str]
    t_axis: list[int]
    Z: np.ndarray
    earliest_starts: list[int]                          # r_j per row
    instance_name: str = ""                             # title suffix
    clip_threshold: float = 0.0                         # title cutoff value
    obj_value: float | None = None                      # MCF LB; in title
    x_cells: list[tuple[int, int]] = field(default_factory=list)
    sort: HeatmapSort = "due2-window"
```

`build_signed_cost_matrix` populates every field and exposes the clip
policy via kwargs (replacing the previous module-level constants):

```python
def build_signed_cost_matrix(
    instance: FFcDDWParameters,
    sort: HeatmapSort = "due2-window",
    x_jt_map: Mapping[str, Mapping[int, int]] | None = None,
    obj_value: float | None = None,
    c_jt_clip_abs_value: float | None = None,
    c_jt_clip_quantile: float = 0.5,
) -> SignedCostHeatmapData: ...
```

`c_jt_clip_abs_value`, when not `None`, overrides
`c_jt_clip_quantile`. The resolved threshold is stored on
`data.clip_threshold` and surfaced in the title.

`heatmap_title(data)` takes the dataclass and emits a two-line plotly
title (using `<br>` because plotly ignores raw `\n`):

```
Last stage only preemptive schedule on C_jt heatmap - {instance_name}<br>
(objValue: {obj_value} | C_jt cutoff: {clip_threshold})
```

## YAML Schema

Self-contained — Phase B never re-reads the benchmark file. `dump_*`
takes only `(path, data)`; `load_*` returns a fully-populated
`SignedCostHeatmapData`.

```yaml
instanceName: ...
sort: due2-window
clipThreshold: 100.0
objValue: 37618.0          # null when obj_value is None
yLabels: [...]
tAxis: [...]
z: [[...], ...]
earliestStarts: [r_0, r_1, ...]
xCells:
  - {i: 0, t: 5}
  - {i: 0, t: 6}
  - ...
```

## Phase A — `apply_lb_by_mcf` (controller.py)

Inside the `if draw_heatmap:` block:

```python
yaml_path = self.try_get_file_path_for_subroutine("_C_heatmap.yaml")
if yaml_path is not None:
    heatmap_data = build_signed_cost_matrix(
        self.instance,
        sort=heatmap_sort,
        x_jt_map=mcf_result.mcf.get_variable_value_dict(),
        obj_value=obj_bound_by_mcf,
    )
    dump_signed_cost_heatmap_yaml(yaml_path, heatmap_data)
```

The `mcf` handle was already retained on `McfLbResult.mcf`; no changes
needed in `phase1_mcf.py`.

## Phase B — `_render_heatmap_from_yaml` (reporting.py)

```python
data = load_signed_cost_heatmap_yaml(yaml_path)
if not data.y_labels or not data.t_axis or data.Z.size == 0:
    return
fig = make_figure(data, title=heatmap_title(data))
fig.write_html(yaml_path.with_suffix(".html"), include_plotlyjs="cdn")
```

The earlier filename-stem fallback for the title is gone — the YAML
carries `instance_name` directly, so subroutine-prefixed filenames
(`<idx>_<method>_C_heatmap.yaml`) no longer leak into the title.

## CLI — `benchmarks/PRA2017/visualize_parallel_mc_cost.py`

CLI also solves MCF (cheap per-instance) and passes both `x_jt_map` and
`obj_value`. No new flags.

```python
mcf = ParallelMachinePreemptionMcf.from_instance(instance)
mcf.solve()
if not mcf.is_optimal():
    parser.error(f"MCF not optimal for instance {instance.name}")
data = build_signed_cost_matrix(
    instance,
    sort=args.sort,
    x_jt_map=mcf.get_variable_value_dict(),
    obj_value=float(mcf.get_obj_value()),
)
fig = make_figure(data, title=heatmap_title(data))
```

`--sort` choices and default were aligned with `HeatmapSort`
(`due2-window`, `neh-cp`).

## Visual Spec

| Layer | Style |
| --- | --- |
| Heatmap cells | RdBu_r diverging, symmetric clip at `data.clip_threshold` |
| `t < r_j` region | Filled grey `rgba(127,127,127,1)`, no border, per row, `add_shape(type="rect", layer="above")` |
| `x_jt = 1` runs | Single `add_shape(type="path")` with `M..L..L..L..Z` subpaths; black border, transparent fill |
| In-window outline | **Removed** |
| Title | Two lines via `<br>`; instance name + objValue + cutoff |

Render z-order: heatmap → grey rects (`layer="above"`) → black outline
path (added last; on top).

## Verification

```bash
uv run ruff check
uv run ruff format
uv run pytest tests/ -q
```

End-to-end roundtrip (used while iterating on
`Instance_150_5_3_0,6_1_20_Rep3.txt`):

- `len(data.x_cells) == sum(p_j on last stage)` — each unit of MCF flow
  becomes one `x_jt = 1` cell since the job→time arc capacity is 1 and
  total source-to-job flow is `sum(p_j)`.
- `data.earliest_starts[i] == instance.get_job_2_p_sum_except_last_stage()[jobs[i]]`
  in display order.
- YAML roundtrip preserves `xCells`, `earliestStarts`,
  `clipThreshold`, `objValue`, `instanceName` exactly.
- HTML renders in ~10 s for the 150-job case, ~4.5 MiB.

## Out of Scope

- Coloring `x_jt = 1` cells by which machine handles them. The MCF model
  is machine-agnostic (capacity m_c on the time→sink arc); machine
  identity is reconstructed only by `MCFPreemptiveSchedule.from_flow_dict`
  later. Color-coding by machine would require the preemptive schedule,
  not just `x_jt`.
- Backwards-compat for old YAML schema. Only the current branch produces
  these files.
