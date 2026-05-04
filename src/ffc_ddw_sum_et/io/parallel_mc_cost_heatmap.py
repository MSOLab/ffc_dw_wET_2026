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
from typing import TYPE_CHECKING, Any, Mapping, Sequence, Union, get_args

import numpy as np
from routix.io import dump_yaml, load_yaml

from ..algorithm.pm_pmtn_sorter import (
    PmPrmpSortKey,
    pm_pmtn_sort_job_sequence_from_window_map,
)
from ..parameters.sorter import ParamSortKey, param_sort_job_sequence

if TYPE_CHECKING:
    from ..parameters.ffc_ddw_params import FFcDDWParameters

HeatmapSort = Union[ParamSortKey, PmPrmpSortKey]

_PARAM_SORT_VALUES: frozenset[str] = frozenset(get_args(ParamSortKey))
_PMTN_SORT_VALUES: frozenset[str] = frozenset(get_args(PmPrmpSortKey))

_HEATMAP_SORT_MIGRATION: dict[str, str] = {
    "neh-cp": "weight-due-pos",
    "due2-window": "due2-weight-pos",
}


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
    instance_name: str = ""
    """Name of the source instance — used as the rendered title suffix."""
    clip_threshold: float = 0.0
    """Symmetric ``|Z|`` clip threshold used to derive ``Z`` — surfaced in
    the rendered title as the ``C_jt cutoff`` value."""
    obj_value: float | None = None
    """MCF objective value (== preemptive lower bound) when an MCF solve
    was supplied to :func:`build_signed_cost_matrix`. ``None`` for the
    matrix-only case."""
    x_cells: list[tuple[int, int]] = field(default_factory=list)
    """``(row_index, t)`` for each MCF cell with ``x_jt = 1``. Empty when
    no MCF flow was supplied to :func:`build_signed_cost_matrix`."""
    sort: HeatmapSort = "due2-weight-pos"


def _weights_or_default(raw: dict[str, int], jobs: Sequence[str]) -> dict[str, int]:
    # Matches _resolve_weight_map in parallel_mc_pmtn.py: empty map -> all 1s.
    return dict.fromkeys(jobs, 1) if not raw else raw


def _window_map_from_x_jt(
    x_jt_map: Mapping[str, Mapping[int, int]],
    job_id_list: Sequence[str],
) -> dict[str, tuple[int, int] | None]:
    """Build a per-job window map from an MCF flow dict.

    Window is ``(min(t with flow>0) - 1, max(t with flow>0))`` so it matches
    the half-open ``[t-1, t)`` segment semantics used by
    ``MCFPreemptiveSchedule.from_flow_dict``. Jobs with no positive flow map
    to ``None``.
    """
    window_map: dict[str, tuple[int, int] | None] = {j: None for j in job_id_list}
    for j in job_id_list:
        ts = [t for t, flow in x_jt_map.get(j, {}).items() if flow > 0]
        if ts:
            window_map[j] = (min(ts) - 1, max(ts))
    return window_map


def _sort_jobs(
    instance: FFcDDWParameters,
    sort: HeatmapSort = "due2-weight-pos",
    x_jt_map: Mapping[str, Mapping[int, int]] | None = None,
) -> list[str]:
    if sort in _PARAM_SORT_VALUES:
        return param_sort_job_sequence(instance, sort)
    if sort in _PMTN_SORT_VALUES:
        if x_jt_map is None:
            raise ValueError(
                f'Heatmap sort "{sort}" requires x_jt_map to derive the '
                "per-job MCF preemptive time window."
            )
        last_stage = instance.stage_id_list[-1]
        return pm_pmtn_sort_job_sequence_from_window_map(
            _window_map_from_x_jt(x_jt_map, instance.job_id_list),
            instance.get_job_2_p_map_for_stage(last_stage),
            instance,
            sort,
        )
    raise ValueError(f"Unknown HeatmapSort: {sort!r}")


def build_signed_cost_matrix(
    instance: "FFcDDWParameters",
    sort: HeatmapSort = "due2-weight-pos",
    x_jt_map: Mapping[str, Mapping[int, int]] | None = None,
    obj_value: float | None = None,
    c_jt_clip_abs_value: float | None = None,
    c_jt_clip_quantile: float = 0.5,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
) -> SignedCostHeatmapData:
    """Build the signed C-cost matrix and the row-aligned overlay payloads.

    Args:
        instance: Instance whose ``C_jt`` cost matrix is being visualised.
        sort: Row ordering (see :data:`HeatmapSort`).
        x_jt_map: ``ParallelMachinePreemptionMcf.get_variable_value_dict()``
            output. When provided, every ``(j, t)`` entry with positive flow
            becomes one ``(row_index, t)`` pair in
            :attr:`SignedCostHeatmapData.x_cells`, and the resolved time
            horizon is widened to contain all such cells. ``None`` leaves
            ``x_cells`` empty so callers without an MCF solution still get
            the matrix-only figure.
        obj_value: MCF objective value (== preemptive lower bound). Stored
            on the result and surfaced in the rendered title. ``None``
            for the matrix-only case.
        c_jt_clip_abs_value: Hard symmetric ``|Z|`` clip threshold. When
            non-``None`` this overrides ``c_jt_clip_quantile``.
        c_jt_clip_quantile: Quantile of ``|Z|`` used as the symmetric clip
            threshold when ``c_jt_clip_abs_value`` is ``None``. Default
            ``0.5`` keeps the diverging colorscale from being dominated by
            far-tail cells.
        r_multiplier: Scales the per-job release dates ``r_j`` (sum of
            upstream processing times) used for the grey release-blocked
            overlay; each value becomes ``ceil(r_j * r_multiplier)``.
            Must match the multiplier passed to the MCF solve so the
            overlay aligns with the actual ``x_jt`` flow region. ``1.0``
            (default) preserves the unscaled view.
        r_increment: Integer ``>= 0`` added to each ``r_j`` *after* the
            ``r_multiplier`` scaling, so the effective release used for
            the overlay becomes ``ceil(r_j * r_multiplier) + r_increment``.
            Must match the increment passed to the MCF solve. ``0``
            (default) preserves the unscaled view.
    """
    if r_multiplier < 0:
        raise ValueError(f"r_multiplier must be >= 0; got {r_multiplier}.")
    if r_increment < 0:
        raise ValueError(
            f"r_increment must be 0 or a positive integer; got {r_increment}."
        )
    calJ = _sort_jobs(instance, sort=sort, x_jt_map=x_jt_map)
    last_stage = instance.stage_id_list[-1]
    p = instance.get_job_2_p_map_for_stage(last_stage)
    ddw = instance.job_2_due_window_map
    w_minus = _weights_or_default(instance.job_2_ewt_map, calJ)
    w_plus = _weights_or_default(instance.job_2_twt_map, calJ)
    r = instance.get_job_2_p_sum_except_last_stage()
    if r_multiplier != 1.0:
        r = {j: math.ceil(v * r_multiplier) for j, v in r.items()}
    if r_increment != 0:
        r = {j: v + r_increment for j, v in r.items()}

    p_max = max(p[j] for j in calJ)
    t_min = max(0, min(ddw[j][0] - p[j] for j in calJ) - p_max)
    t_max = max(ddw[j][1] for j in calJ) + p_max
    if x_jt_map is not None:
        x_ts = [
            t for j_map in x_jt_map.values() for t, flow in j_map.items() if flow > 0
        ]
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
    if c_jt_clip_abs_value is not None:
        threshold = c_jt_clip_abs_value
    else:
        threshold = float(np.quantile(np.abs(Z), c_jt_clip_quantile))
    Z = np.clip(Z, -threshold, threshold)
    return SignedCostHeatmapData(
        y_labels=y_labels,
        t_axis=t_axis,
        Z=Z,
        earliest_starts=earliest_starts,
        instance_name=instance.name,
        clip_threshold=float(threshold),
        obj_value=obj_value,
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
    * a single ``add_shape(type="path")`` whose SVG path bundles all
      contiguous ``x_jt = 1`` runs as ``M..L..L..L..Z`` subpaths. One
      shape covers every row — far cheaper than per-cell rects, the
      rectangles align with cell boundaries at any zoom (data
      coordinates), and the categorical y-axis is preserved so hover
      shows the job label directly via ``%{y}``.
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
        runs_by_row: dict[int, list[int]] = {}
        for i, t in x_cells:
            runs_by_row.setdefault(i, []).append(t)
        parts: list[str] = []
        for i, ts in runs_by_row.items():
            ts.sort()
            run_start = run_end = ts[0]
            for t in ts[1:]:
                if t == run_end + 1:
                    run_end = t
                else:
                    parts.append(
                        f"M{run_start - 0.5},{i - 0.5}"
                        f"L{run_end + 0.5},{i - 0.5}"
                        f"L{run_end + 0.5},{i + 0.5}"
                        f"L{run_start - 0.5},{i + 0.5}Z"
                    )
                    run_start = run_end = t
            parts.append(
                f"M{run_start - 0.5},{i - 0.5}"
                f"L{run_end + 0.5},{i - 0.5}"
                f"L{run_end + 0.5},{i + 0.5}"
                f"L{run_start - 0.5},{i + 0.5}Z"
            )
        fig.add_shape(
            type="path",
            path="".join(parts),
            line={"color": "black", "width": 1},
            fillcolor="rgba(0,0,0,0)",
            layer="above",
        )
    return fig


def heatmap_title(data: SignedCostHeatmapData) -> str:
    obj_str = "None" if data.obj_value is None else f"{data.obj_value:g}"
    # plotly renders title as HTML; use <br> for line break (raw \n is ignored).
    return (
        f"Last stage only preemptive schedule on C_jt heatmap - "
        f"{data.instance_name}<br>"
        f"(objValue: {obj_str} | C_jt cutoff: {data.clip_threshold:g})"
    )


def dump_signed_cost_heatmap_yaml(
    path: Path,
    data: SignedCostHeatmapData,
) -> None:
    """Write the heatmap input as a self-contained YAML.

    The post-run reporter reads this back with
    :func:`load_signed_cost_heatmap_yaml` and renders the HTML via
    :func:`make_figure` — no benchmark file needed at render time.
    """
    payload: dict[str, Any] = {
        "instanceName": data.instance_name,
        "sort": data.sort,
        "clipThreshold": float(data.clip_threshold),
        "objValue": None if data.obj_value is None else float(data.obj_value),
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
    obj_raw = raw.get("objValue")
    return SignedCostHeatmapData(
        y_labels=list(raw["yLabels"]),
        t_axis=[int(t) for t in raw["tAxis"]],
        Z=np.asarray(raw["z"], dtype=float),
        earliest_starts=[int(r) for r in raw["earliestStarts"]],
        instance_name=raw.get("instanceName", ""),
        clip_threshold=float(raw.get("clipThreshold", 0.0)),
        obj_value=None if obj_raw is None else float(obj_raw),
        x_cells=[(int(c["i"]), int(c["t"])) for c in (raw.get("xCells") or [])],
        sort=_HEATMAP_SORT_MIGRATION.get(
            raw.get("sort", "due2-weight-pos"), raw.get("sort", "due2-weight-pos")
        ),
    )
