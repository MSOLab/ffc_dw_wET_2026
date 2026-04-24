"""Render per-job wET (weighted earliness + tardiness) penalty as a signed HTML heatmap.

Each cell (job j, time t) shows the penalty incurred when job j completes at time t:
  - t < d⁻:  earliness  =  w⁻ * (d⁻ - t)   (negative, blue)
  - d⁻ ≤ t ≤ d⁺:  0  (white)
  - t > d⁺:  tardiness  =  w⁺ * (t - d⁺)  (positive, red)

Source of truth for the formula:
src/ffc_ddw_sum_et/solution/objectives.py:compute_weighted_earliness_tardiness
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

import numpy as np
import plotly.graph_objects as go

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

CLIP_QUANTILE = 0.5


def _weights_or_default(raw: dict[str, int], jobs: list[str]) -> dict[str, int]:
    # Matches objectives.py: missing weights default to 1
    return dict.fromkeys(jobs, 1) if not raw else raw


def _sort_jobs(
    instance: FFcDDWParameters,
    sort: Literal["due-window", "neh-cp"] = "due-window",
) -> list[str]:
    if sort == "neh-cp":
        return instance.get_weight_due_pos_job_sequence()
    ddw = instance.job_2_due_window_map
    p = instance.get_job_2_p_map_for_stage(instance.stage_id_list[-1])
    return sorted(
        instance.job_id_list,
        key=lambda j: (max(0, ddw[j][1] - p[j]), ddw[j][1], ddw[j][0]),
    )


def build_wet_cost_matrix(
    instance: FFcDDWParameters,
    sort: Literal["due-window", "neh-cp"] = "due-window",
) -> tuple[list[str], list[int], np.ndarray, list[tuple[float, float, int]]]:
    calJ = _sort_jobs(instance, sort=sort)
    last_stage = instance.stage_id_list[-1]
    p = instance.get_job_2_p_map_for_stage(last_stage)
    ddw = instance.job_2_due_window_map
    w_minus = _weights_or_default(instance.job_2_ewt_map, calJ)
    w_plus = _weights_or_default(instance.job_2_twt_map, calJ)

    p_max = max(p[j] for j in calJ)
    t_min = max(0, min(ddw[j][0] - p[j] for j in calJ) - p_max)
    t_max = max(ddw[j][1] for j in calJ) + p_max
    t_axis = list(range(t_min, t_max + 1))

    Z = np.zeros((len(calJ), len(t_axis)), dtype=float)
    rects: list[tuple[float, float, int]] = []
    y_labels: list[str] = []
    for i, j in enumerate(calJ):
        pj = p[j]
        d_minus, d_plus = ddw[j]
        wm, wp = w_minus[j], w_plus[j]
        for k, t in enumerate(t_axis):
            if t < d_minus:
                Z[i, k] = -wm * (d_minus - t)
            elif t > d_plus:
                Z[i, k] = wp * (t - d_plus)
        x0 = max(0.0, d_plus - pj)
        rects.append((x0, x0 + pj, i))
        y_labels.append(f"{j}({pj:>3})")
    threshold = float(np.quantile(np.abs(Z), CLIP_QUANTILE))
    Z = np.clip(Z, -threshold, threshold)
    return y_labels, t_axis, Z, rects


def make_figure(
    y_labels: list[str],
    t_axis: list[int],
    Z: np.ndarray,
    rects: list[tuple[float, float, int]],
    title: str,
) -> go.Figure:
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
            colorbar={"title": "wET"},
            hovertemplate=("job=%{y}<br>t=%{x}<br>wET=%{z}<extra></extra>"),
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="completion time t",
        yaxis_title="job (p)",
        yaxis={
            "autorange": "reversed",
            "tickfont": {"family": "Courier New, monospace"},
        },
        width=max(900, len(t_axis) + 200),
        height=max(400, 18 * len(y_labels) + 120),
    )
    for x0, x1, i in rects:
        fig.add_shape(
            type="rect",
            x0=x0,
            x1=x1,
            y0=i - 0.5,
            y1=i + 0.5,
            line={"color": "black", "width": 1},
            fillcolor="rgba(0,0,0,0)",
            layer="above",
        )
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--instance",
        type=Path,
        required=True,
        help="Path (absolute or relative) to a PRA2017 instance .txt file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "HTML output path. Defaults to <instance_stem>_wET_heatmap.html "
            "next to the instance file."
        ),
    )
    parser.add_argument(
        "--sort",
        choices=["due-window", "neh-cp"],
        default="due-window",
        help=(
            "Job row ordering in the heatmap. "
            "'due-window' sorts by max(0, d⁺−p) asc, then d⁺ asc, then d⁻ asc. "
            "'neh-cp' sorts by (max(w⁻,w⁺) desc, w⁻+w⁺ desc, window width asc, position). "
            "Defaults to 'due-window'."
        ),
    )
    args = parser.parse_args()

    instance_path = args.instance.expanduser().resolve()
    if not instance_path.is_file():
        parser.error(f"Instance file not found: {instance_path}")

    with instance_path.open() as fh:
        instance = FFcDDWParameters.from_pra_2017_data(instance_path.stem, fh)

    y_labels, t_axis, Z, rects = build_wet_cost_matrix(instance, sort=args.sort)
    fig = make_figure(
        y_labels,
        t_axis,
        Z,
        rects,
        title=f"wET cost heatmap — {instance_path.stem}",
    )

    out_path = (
        args.output
        if args.output is not None
        else instance_path.with_name(f"{instance_path.stem}_wET_heatmap.html")
    )
    out_path = out_path.expanduser().resolve()
    fig.write_html(str(out_path), include_plotlyjs="cdn")

    print(
        f"Wrote {out_path} — jobs={len(y_labels)}, "
        f"t-range=[{t_axis[0]}..{t_axis[-1]}] ({len(t_axis)} cells/row)"
    )


if __name__ == "__main__":
    main()
