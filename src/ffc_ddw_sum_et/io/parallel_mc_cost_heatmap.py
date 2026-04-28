"""Signed C-cost heatmap helpers for the parallel-machine preemption model.

Two-phase emission, mirroring the Gantt pipeline:

* Phase A (during a subroutine run): build the matrix from an instance and
  write a self-contained YAML next to the other per-instance artifacts.
* Phase B (post-run reporter): scan the output dir for ``*_C_heatmap.yaml``
  and render an interactive plotly HTML next to each YAML.

Source of truth for the C formula: ``algorithm/parallel_mc_pmtn.py:113-125``.
Negative cells = earliness region, positive = tardiness, zero = in-window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping, Sequence

import numpy as np
from routix.io import dump_yaml, load_yaml

if TYPE_CHECKING:
    from ..parameters.ffc_ddw_params import FFcDDWParameters

CLIP_QUANTILE = 0.75

HeatmapSort = Literal["due2-window", "neh-cp"]


@dataclass(frozen=True, slots=True)
class SignedCostHeatmapData:
    """Self-contained payload for the signed C-cost heatmap.

    All collections are aligned to the same row ordering (``y_labels``,
    ``Z`` rows, ``earliest_starts``).
    """

    y_labels: list[str]
    t_axis: list[int]
    Z: np.ndarray
    earliest_starts: list[int]
    """``r_j`` per row in display order."""
    x_cells: list[tuple[int, int]] = field(default_factory=list)
    """``(row_index, t)`` for each MCF cell with ``x_jt = 1``. Empty when
    no MCF flow was supplied to :func:`build_signed_cost_matrix`."""
    sort: HeatmapSort = "due2-window"


def _weights_or_default(raw: dict[str, int], jobs: Sequence[str]) -> dict[str, int]:
    # Matches _resolve_weight_map in parallel_mc_pmtn.py: empty map -> all 1s.
    return dict.fromkeys(jobs, 1) if not raw else raw


def _sort_jobs(
    instance: "FFcDDWParameters",
    sort: HeatmapSort = "due2-window",
) -> list[str]:
    if sort == "neh-cp":
        return instance.get_weight_due_pos_job_sequence()
    return instance.get_due2_weight_pos_job_sequence()


def build_signed_cost_matrix(
    instance: "FFcDDWParameters",
    sort: HeatmapSort = "due2-window",
    x_jt_map: Mapping[str, Mapping[int, int]] | None = None,
) -> SignedCostHeatmapData:
    """Build the signed C-cost matrix and the row-aligned overlay payloads.

    ``Z`` is symmetrically clipped at the ``CLIP_QUANTILE``-th percentile of
    ``|Z|`` so the diverging colorscale isn't dominated by far tails.

    When ``x_jt_map`` is provided (typically
    ``ParallelMachinePreemptionMcf.get_variable_value_dict()``), every
    ``(j, t)`` entry with positive flow becomes one ``(row_index, t)``
    pair in :attr:`SignedCostHeatmapData.x_cells`. ``None`` leaves
    ``x_cells`` empty so callers without an MCF solution still get the
    matrix-only figure.
    """
    calJ = _sort_jobs(instance, sort=sort)
    last_stage = instance.stage_id_list[-1]
    p = instance.get_job_2_p_map_for_stage(last_stage)
    ddw = instance.job_2_due_window_map
    w_minus = _weights_or_default(instance.job_2_ewt_map, calJ)
    w_plus = _weights_or_default(instance.job_2_twt_map, calJ)
    r = instance.get_job_2_p_sum_except_last_stage()

    p_max = max(p[j] for j in calJ)
    t_min = max(0, min(ddw[j][0] - p[j] for j in calJ) - p_max)
    t_max = max(ddw[j][1] for j in calJ) + p_max
    if x_jt_map is not None:
        x_ts = [t for j_map in x_jt_map.values() for t, flow in j_map.items() if flow > 0]
        if x_ts:
            t_min = min(t_min, min(x_ts))
            t_max = max(t_max, max(x_ts))
    t_axis = list(range(t_min, t_max + 1))

    Z = np.zeros((len(calJ), len(t_axis)), dtype=float)
    earliest_starts: list[int] = []
    y_labels: list[str] = []
    x_cells: list[tuple[int, int]] = []
    for i, j in enumerate(calJ):
        pj = p[j]
        d_minus, d_plus = ddw[j]
        wm, wp = w_minus[j], w_plus[j]
        for k, t in enumerate(t_axis):
            if t <= d_minus - pj:
                Z[i, k] = -wm * math.ceil((d_minus - pj - t + 1) / pj)
            elif t > d_plus:
                Z[i, k] = wp * math.ceil((t - d_plus) / pj)
        earliest_starts.append(int(r[j]))
        y_labels.append(f"{j}({pj:>3})")
        if x_jt_map is not None:
            for t, flow in x_jt_map.get(j, {}).items():
                if flow > 0:
                    x_cells.append((i, int(t)))
    threshold = float(np.quantile(np.abs(Z), CLIP_QUANTILE))
    Z = np.clip(Z, -threshold, threshold)
    return SignedCostHeatmapData(
        y_labels=y_labels,
        t_axis=t_axis,
        Z=Z,
        earliest_starts=earliest_starts,
        x_cells=x_cells,
        sort=sort,
    )


def make_figure(data: SignedCostHeatmapData, *, title: str):
    """Build a plotly Figure for the signed C-cost heatmap.

    plotly is imported lazily so callers that only build the matrix (and
    write the YAML) do not pay for the import. Two overlay layers on
    top of the heatmap:

    * filled grey rect for ``t < r_j`` (release-time blocked region),
      one ``add_shape`` per row.
    * a single ``Scatter`` trace marking every ``(j, t)`` with ``x_jt = 1``
      using hollow black square markers. One trace covers all cells —
      far cheaper than per-cell ``add_shape`` rects.
    """
    import plotly.graph_objects as go

    y_labels = data.y_labels
    t_axis = data.t_axis
    Z = data.Z
    earliest_starts = data.earliest_starts
    x_cells = data.x_cells

    z_abs = max(1.0, float(np.abs(Z).max()))
    fig = go.Figure(
        go.Heatmap(
            z=Z,
            x=t_axis,
            y=y_labels,
            colorscale="RdBu_r",
            zmid=0,
            zmin=-z_abs,
            zmax=z_abs,
            xgap=0,
            ygap=0,
            colorbar={"title": "signed C"},
            hovertemplate=("job=%{y}<br>t=%{x}<br>signed C=%{z}<extra></extra>"),
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="time t",
        yaxis_title="job (p)",
        yaxis={
            "autorange": "reversed",
            "tickfont": {"family": "Courier New, monospace"},
        },
        width=max(900, len(t_axis) + 200),
        height=max(400, 18 * len(y_labels) + 120),
    )

    t_left = t_axis[0] - 0.5
    for i, r_j in enumerate(earliest_starts):
        if r_j > t_axis[0]:
            fig.add_shape(
                type="rect",
                x0=t_left,
                x1=r_j - 0.5,
                y0=i - 0.5,
                y1=i + 0.5,
                line={"width": 0},
                fillcolor="rgba(127,127,127,1)",
                layer="above",
            )

    if x_cells:
        fig.add_trace(
            go.Scatter(
                x=[t for _, t in x_cells],
                y=[y_labels[i] for i, _ in x_cells],
                mode="markers",
                marker={"symbol": "square-open", "size": 8, "color": "black"},
                hovertemplate="job=%{y}<br>t=%{x}<br>x_jt=1<extra></extra>",
                showlegend=False,
            )
        )
    return fig


def heatmap_title(instance_name: str) -> str:
    return f"parallel_mc_pmtn C heatmap — {instance_name}"


def dump_signed_cost_heatmap_yaml(
    path: Path,
    data: SignedCostHeatmapData,
    *,
    instance_name: str,
) -> None:
    """Write the heatmap input as a self-contained YAML.

    The post-run reporter reads this back with
    :func:`load_signed_cost_heatmap_yaml` and renders the HTML via
    :func:`make_figure` — no benchmark file needed at render time.
    """
    payload: dict[str, Any] = {
        "instanceName": instance_name,
        "sort": data.sort,
        "yLabels": list(data.y_labels),
        "tAxis": [int(t) for t in data.t_axis],
        "z": [[float(v) for v in row] for row in np.asarray(data.Z).tolist()],
        "earliestStarts": [int(r) for r in data.earliest_starts],
        "xCells": [{"i": int(i), "t": int(t)} for i, t in data.x_cells],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_yaml(payload, path)


def load_signed_cost_heatmap_yaml(path: Path) -> SignedCostHeatmapData:
    """Load a heatmap YAML written by :func:`dump_signed_cost_heatmap_yaml`."""
    raw = load_yaml(path)
    return SignedCostHeatmapData(
        y_labels=list(raw["yLabels"]),
        t_axis=[int(t) for t in raw["tAxis"]],
        Z=np.asarray(raw["z"], dtype=float),
        earliest_starts=[int(r) for r in raw["earliestStarts"]],
        x_cells=[(int(c["i"]), int(c["t"])) for c in (raw.get("xCells") or [])],
        sort=raw.get("sort", "due2-window"),
    )
