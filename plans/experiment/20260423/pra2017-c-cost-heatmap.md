# PRA2017 — Visualize `parallel_mc_pmtn.py` cost matrix `self.C` as HTML heatmap

## Context

`ParallelMachinePreemptionMcf._define_parameters` builds a cost matrix
`self.C[j][t]` used as unit costs on job→time arcs in the MCF LP. The coefficient
encodes earliness/tardiness penalty relative to the due date window
`ddw[j] = (d_minus, d_plus)` and last-stage processing time `p[j]`:

- `t ≤ d_minus − p[j]` → `w_minus[j] · ceil((d_minus − p[j] − t + 1) / p[j])` (earliness)
- `d_minus − p[j] < t ≤ d_plus` → `0` (in-window)
- `t > d_plus` → `w_plus[j] · ceil((t − d_plus) / p[j])` (tardiness)

We need a quick visual sanity check of this coefficient landscape on individual
PRA2017 instances. A discrete 2D heatmap (jobs × time) with a diverging
color scale — white at the zero cells, blue shades on the earliness side, red
shades on the tardiness side — gives a one-glance read on (a) where each
window sits, (b) how steep each side's slope is under `w_minus`/`w_plus`, and
(c) how balanced the weights are across jobs.

Output target: self-contained HTML that opens in a browser and is easy to share
or archive alongside instance files.

## Approach (summary)

- New standalone script **`benchmarks/PRA2017/visualize_parallel_mc_cost.py`** following the existing
  `argparse` + `main()` convention in that folder (e.g. `add_lb_column.py:86`).
- Accepts one instance file path (absolute or relative, resolved via
  `Path.resolve()`), parses it through the existing loader, computes the signed
  C matrix directly from the formula (not via the algorithm object — see below),
  and writes an HTML heatmap.
- **New dependency:** `plotly` in `pyproject.toml` (confirmed with user). Used
  only for `plotly.graph_objects.Heatmap` + `fig.write_html()`.

### Signed C for diverging colorscale

The raw `self.C[j][t]` is always ≥ 0, so left/right of the window are
indistinguishable by value alone. For the heatmap we encode:

```text
if t <= d_minus - p[j]:  Z[j, t] = -w_minus[j] * ceil((d_minus - p[j] - t + 1) / p[j])
elif t <= d_plus:        Z[j, t] = 0
else:                    Z[j, t] = +w_plus[j] * ceil((t - d_plus) / p[j])
```

Render with `colorscale="RdBu_r"`, `zmid=0`, `zmin=-Z_abs_max`, `zmax=+Z_abs_max`
so zero maps to white, earliness → blue (deeper with magnitude), tardiness →
red (deeper with magnitude).

### Time horizon

Per user (confirmed):

```text
t_min = max(0, min(ddw[j][0] - p[j] for j in calJ) - max(p[j] for j in calJ))
t_max = max(ddw[j][1] for j in calJ) + max(p[j] for j in calJ)
```

(The spec's original `ddw[j][0]` upper bound was a typo for `ddw[j][1]`,
confirmed by user — otherwise the tardiness region would be clipped.)

x-axis: integer ticks over `range(t_min, t_max + 1)`.
y-axis: all jobs in `calJ` input order (confirmed with user).

### Why re-compute C locally rather than build the algorithm object

`ParallelMachinePreemptionMcf.from_instance()` also runs `_build_mcf()` (OR-Tools
graph construction) and fixes `self.calT` to the algorithm's internal horizon,
which may not match the user-specified visualization horizon. The penalty
formula itself is ~6 lines of arithmetic; reimplementing it in the viz script
keeps the script independent of algorithm internals and lets us scan a custom
t-range. A short comment will note the source of truth is
`src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py:113-125` so future changes
there are easy to mirror.

## Critical files

| File | Change |
| --- | --- |
| `benchmarks/PRA2017/visualize_parallel_mc_cost.py` | **new** — main script |
| `pyproject.toml` | add `plotly>=5.24` to `dependencies` |

Read-only reference files (no change):

- `src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py:99-125` — source of truth for the C formula and which `p` / weights to pull.
- `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py:173-310` — `from_pra_2017_data(name, stream)` loader.
- `benchmarks/PRA2017/add_lb_column.py:86-95` — argparse/`main()` convention to mirror.

## Script behavior

**CLI**

```sh
uv run python benchmarks/PRA2017/visualize_parallel_mc_cost.py \
    --instance benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt \
    [--output <path.html>]
```

- `--instance` (required): path to a PRA2017 instance `.txt`. Absolute or
  relative (resolved via `Path(arg).expanduser().resolve()`).
- `--output` (optional): HTML output path. Default: same directory as the
  instance, filename `<instance_stem>_C_heatmap.html`.

**Load**

```python
path = Path(args.instance).expanduser().resolve()
with path.open() as fh:
    instance = FFcDDWParameters.from_pra_2017_data(path.stem, fh)
```

**Extract parameters** (mirrors `parallel_mc_pmtn.py:99-105`)

```python
calJ        = instance.job_id_list
last_stage  = instance.stage_id_list[-1]
p           = instance.get_job_2_p_map_for_stage(last_stage)
ddw         = instance.job_2_due_window_map
w_minus     = instance.job_2_ewt_map
w_plus      = instance.job_2_twt_map
```

**Horizon**

```python
t_min = min(ddw[j][0] - p[j] for j in calJ) - max(p[j] for j in calJ)
t_max = max(ddw[j][1] for j in calJ) + max(p[j] for j in calJ)
t_axis = list(range(t_min, t_max + 1))
```

**Signed cost matrix** (numpy, vectorized per row)

```python
Z = np.zeros((len(calJ), len(t_axis)), dtype=float)
for i, j in enumerate(calJ):
    pj, (dm, dp), wm, wp = p[j], ddw[j], w_minus[j], w_plus[j]
    for k, t in enumerate(t_axis):
        if t <= dm - pj:
            Z[i, k] = -wm * math.ceil((dm - pj - t + 1) / pj)
        elif t > dp:
            Z[i, k] = +wp * math.ceil((t - dp) / pj)
        # else 0
```

(A numpy-vectorized version per job is fine too; clarity first.)

**Plot**

```python
import plotly.graph_objects as go
z_abs = max(1, int(np.abs(Z).max()))
fig = go.Figure(go.Heatmap(
    z=Z, x=t_axis, y=calJ,
    colorscale="RdBu_r",
    zmid=0, zmin=-z_abs, zmax=+z_abs,
    xgap=0, ygap=0,                  # discrete cells, no interpolation
    colorbar=dict(title="signed C"),
    hovertemplate="job=%{y}<br>t=%{x}<br>signed C=%{z}<extra></extra>",
))
fig.update_layout(
    title=f"parallel_mc_pmtn C heatmap — {path.stem}",
    xaxis_title="time t",
    yaxis_title="job",
    yaxis=dict(autorange="reversed"),   # first job at top
    width=max(800, 12 * len(t_axis) // 10),
    height=max(400, 18 * len(calJ)),
)
fig.write_html(out_path, include_plotlyjs="cdn")
```

`include_plotlyjs="cdn"` keeps the HTML small; switch to `True` for fully
offline-portable files if that later turns out to matter.

## Verification

1. **Dependency install**: after editing `pyproject.toml`, run `uv sync` and
   confirm `plotly` is present.
2. **Smoke run** on the instance referenced in the request:

   ```sh
   uv run python benchmarks/PRA2017/visualize_parallel_mc_cost.py \
       --instance benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt
   ```

   Expected: an HTML file is written alongside the instance and opens in a
   browser showing 50 rows.
3. **Visual checks** (open the HTML):
   - There is a zero-cost (white) band per job that spans
     `[d_minus − p[j] + 1, d_plus]` — hover on the band's boundaries to confirm.
   - Cells to the **left** of the band are blue, darkening leftwards; cells to
     the **right** are red, darkening rightwards.
   - Color intensity scales with `w_minus` / `w_plus` (two jobs with the same
     distance-to-window but different weights should render at different
     shades).
4. **Relative path**: rerun from a different working directory with a relative
   `--instance` path and confirm resolution still works.
5. **Lint**: `uv run ruff check benchmarks/PRA2017/visualize_parallel_mc_cost.py`
   and `uv run ruff format benchmarks/PRA2017/visualize_parallel_mc_cost.py`.

## Out of scope

- Overlaying the exact `(d_minus, d_plus)` rectangle per job — the white band
  already makes the window position visually obvious. Can be added later if
  needed.
- Batching multiple instances / dashboards.
- Sorting jobs by `d_minus` (user chose input order).
- Integrating the visualizer into the algorithm's reporting pipeline — this is
  a standalone diagnostic script.
