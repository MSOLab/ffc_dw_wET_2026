# Plan: Replace in-window outline with `x_jt = 1` cells, add `t < r_j` grey shading

## Context

The signed C-cost heatmap currently draws two visual overlays on top of the
heatmap cells:

- **Black hollow rectangles** — one per row, spanning the in-window placement
  band `[max(0, d⁺ − p), max(0, d⁺ − p) + p]`. This shows where the job
  *could* complete at its latest in-window time.

The user wants two changes:

1. **Replace** the per-row in-window outline with **per-cell highlights at
   every `(j, t)` where `x_jt = 1` in the MCF preemptive solution**. The
   meaningful overlay should reflect the actual placement chosen by the
   relaxation, not a static potential placement.
2. **Add** a grey-shaded region for cells with `t < r_j` (release-time
   blocked region), matching the convention already used in
   `benchmarks/PRA2017/visualize_wET_cost.py`.

## Critical Files

- `src/ffc_ddw_sum_et/io/parallel_mc_cost_heatmap.py` — builder + figure +
  YAML dump/load helpers. Most edits land here.
- `src/ffc_ddw_sum_et/orchestration/controller.py` — `apply_lb_by_mcf` Phase A
  needs to pass MCF flow values into the builder.
- `benchmarks/PRA2017/visualize_parallel_mc_cost.py` — CLI script needs to
  solve MCF (currently doesn't) so the same overlay renders offline.
- `src/ffc_ddw_sum_et/orchestration/reporting.py` — Phase B renderer; only
  trivial signature update for the new YAML keys.

## Existing Functions / Patterns Reused

- `ParallelMachinePreemptionMcf.get_variable_value_dict() -> dict[str, dict[int, int]]`
  in `algorithm/parallel_mc_pmtn.py:183` — returns sparse `{j: {t: flow}}`
  for each `(j, t)` arc with nonzero flow. We already solve the MCF in
  `apply_lb_by_mcf` via `solve_mcf_lb`, which returns the `mcf` handle on
  `McfLbResult.mcf`. No re-solve.
- `instance.get_job_2_p_sum_except_last_stage() -> dict[str, int]` for `r_j`.
- The grey-rect drawing pattern in
  `benchmarks/PRA2017/visualize_wET_cost.py:122-133`.

## Drawing Approach

Per-cell overlays are the visual goal, but `plotly.add_shape` does not scale
well to thousands of shapes (a 150-job instance has ≈ 150 × 𝔼[p_j] ≈
7,500 cells where `x_jt = 1`). We use:

- **Grey r_j region** (per row): `add_shape` rect from `t_axis[0]-0.5` to
  `r_j - 0.5`. ≤ n shapes total — cheap. Same style as wET visualizer:
  `fillcolor="rgba(127,127,127,1)"`, `line.width=0`.
- **`x_jt = 1` cell highlights**: a single `go.Scatter` trace with
  `mode="markers"`, `marker.symbol="square-open"`, sized to one cell. One
  trace covers all cells across all rows — far faster than thousands of
  shapes. Black outline, transparent fill, matching the prior aesthetic.

  - x = `t` (data coordinate), y = `y_labels[i]` (categorical, matches
    Heatmap's y axis), one point per `(i, t)` with `x_jt[j] = 1`.
  - Marker size: pick a fixed pixel size (e.g. 10 px) that visually matches
    a single heatmap cell at typical zoom. Plotly does not size markers in
    data coordinates, so this is a tradeoff — the marker will look correct
    at the default zoom and slightly off when the user zooms heavily. This
    is acceptable for a static export. (If size mismatch matters, we can
    fall back to per-cell rects with `add_shape`, paying the perf cost.)
  - `hovertemplate` shows job and t; `showlegend=False`.

## Data Flow Restructure

`build_signed_cost_matrix` currently returns
`(y_labels, t_axis, Z, rects)`. The new outputs are
`(y_labels, t_axis, Z, earliest_starts, x_cells)`. To keep call sites
readable and avoid further tuple-bloat, refactor to a frozen dataclass:

```python
@dataclass(frozen=True, slots=True)
class SignedCostHeatmapData:
    y_labels: list[str]
    t_axis: list[int]
    Z: np.ndarray
    earliest_starts: list[int]        # r_j per row, in display order
    x_cells: list[tuple[int, int]]    # (row_index, t) where x_jt = 1
    sort: HeatmapSort                 # echoed for YAML provenance


def build_signed_cost_matrix(
    instance: FFcDDWParameters,
    sort: HeatmapSort = "due-window",
    x_jt_map: Mapping[str, Mapping[int, int]] | None = None,
) -> SignedCostHeatmapData:
    ...
```

If `x_jt_map is None`, `x_cells` is `[]` and the figure draws no per-cell
overlay (the figure stays useful for "matrix-only" inspection). Both the
controller and the CLI will always pass a non-None map after this change,
but the optional parameter keeps the helper composable.

`make_figure` is updated to take the dataclass (or just the four arrays it
needs) plus `title`. The `rects` parameter is dropped.

## YAML Schema Change

`<ins>_C_heatmap.yaml` currently stores
`{instanceName, sort, yLabels, tAxis, z, rects}`. New schema:

```yaml
instanceName: ...
sort: due-window
yLabels: [...]
tAxis: [...]
z: [[...], ...]
earliestStarts: [r_0, r_1, ...]        # parallel to yLabels
xCells:                                # one entry per (j, t) with x_jt = 1
  - {i: 0, t: 5}
  - {i: 0, t: 6}
  - ...
```

`rects` is removed. This is a **breaking change** to the YAML schema, but:

- The YAMLs are run-scoped artifacts written by the current branch only;
  there is no other producer or consumer.
- The Phase B renderer (`_render_heatmap_from_yaml`) is updated in lockstep.
- We do not bother with backwards-compat shims (per project conventions).

`xCells` may be large (~thousands of entries on a 150-job instance), but
each entry is a small `{i, t}` pair — the YAML grows by tens of KiB at
most, dwarfed by the existing dense `z` matrix.

## Phase A — `apply_lb_by_mcf` (controller.py)

Inside the `if draw_heatmap:` block:

```python
from ..io import build_signed_cost_matrix, dump_signed_cost_heatmap_yaml

x_jt_map = mcf_result.mcf.get_variable_value_dict()  # {j: {t: 1}}
data = build_signed_cost_matrix(
    self.instance, sort=heatmap_sort, x_jt_map=x_jt_map
)
dump_signed_cost_heatmap_yaml(yaml_path, data, instance_name=self.instance.name)
```

`dump_signed_cost_heatmap_yaml` is simplified to take the dataclass directly
plus `instance_name`. (It already had a redundant `sort=` kwarg that
duplicated `data.sort`; the dataclass carries it.)

The `mcf` handle is already retained on `McfLbResult.mcf` from the earlier
refactor, so no changes are needed in `phase1_mcf.py`.

## Phase B — `_render_heatmap_from_yaml` (reporting.py)

Update the loader + renderer to read the new keys:

```python
y_labels = list(data["yLabels"])
t_axis = [int(t) for t in data["tAxis"]]
Z = np.asarray(data["z"], dtype=float)
earliest_starts = [int(r) for r in data["earliestStarts"]]
x_cells = [(int(c["i"]), int(c["t"])) for c in (data.get("xCells") or [])]
```

Then construct a `SignedCostHeatmapData` (or pass arrays directly) and call
`make_figure`. Drop the old `rects` handling.

## CLI — `benchmarks/PRA2017/visualize_parallel_mc_cost.py`

The CLI currently builds the matrix straight from the parsed instance.
After this change, the CLI must also solve the MCF to get `x_jt`. MCF is
cheap on a single instance; we always solve, no flag needed:

```python
mcf = ParallelMachinePreemptionMcf.from_instance(instance)
mcf.solve()
if not mcf.is_optimal():
    parser.error(f"MCF not optimal for {instance.name}")
data = build_signed_cost_matrix(instance, sort=args.sort,
                                x_jt_map=mcf.get_variable_value_dict())
fig = make_figure(data, title=heatmap_title(instance_path.stem))
```

No new CLI flags.

## Visual Spec

For the rendered HTML:

| Layer                | Style                                                  |
| -------------------- | ------------------------------------------------------ |
| Heatmap cells        | RdBu_r diverging, clipped at 25th percentile (no change) |
| `t < r_j` region     | Filled grey `rgba(127,127,127,1)`, no border, per row, `add_shape` |
| `x_jt = 1` cells     | Square markers, black outline, transparent fill, single Scatter trace |
| In-window outline    | **Removed**                                            |

Render z-order: heatmap → grey rects (`layer="above"`) → scatter trace
(plotted last, naturally on top).

## Verification

```bash
uv run ruff check
uv run ruff format
uv run pytest tests/ -q
```

End-to-end roundtrip on `Instance_150_5_3_0,6_1_20_Rep3.txt`:

```bash
uv run python -c "<smoke test>"
```

Asserts:

- `len(x_cells) == sum(p_j for j in jobs)` (each unit of MCF flow becomes
  one `x_jt = 1` cell since arc capacity is 1 and total source-to-job
  flow is `sum(p_j)`).
- `earliest_starts[i] == instance.get_job_2_p_sum_except_last_stage()[jobs[i]]`
  for each row in display order.
- YAML roundtrip preserves `xCells` and `earliestStarts` exactly.
- HTML renders without exception; file size stays within ~2× of current
  (Scatter trace adds little; removal of one shape per row offsets some).

CLI sanity: `uv run python benchmarks/PRA2017/visualize_parallel_mc_cost.py
--instance benchmarks/PRA2017/large/Instance_150_5_3_0,6_1_20_Rep3.txt`
should produce a renderable HTML with the new overlays.

## Out of Scope

- Per-cell `add_shape` rects instead of Scatter markers — only revisit if
  marker sizing at extreme zoom levels is a problem.
- Coloring `x_jt = 1` cells by which machine handles them. The MCF model
  is machine-agnostic (capacity m_c on the time→sink arc); machine
  identity is reconstructed only by `MCFPreemptiveSchedule.from_flow_dict`
  later. If we ever want to color-code by machine, that becomes a
  separate change requiring the preemptive schedule, not just `x_jt`.
- Backwards-compat for old YAML schema. Only the current branch produces
  these files.
