"""Render parallel_mc_pmtn.py C coefficients as a signed HTML heatmap.

Signed: earliness region (left of due date window) -> negative, tardiness
region (right of window) -> positive, in-window -> 0. Rendered with a RdBu
diverging colorscale centered at 0 so blue = earliness, red = tardiness,
white = zero cost.

Source of truth for the C formula:
src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py:113-125
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import plotly.graph_objects as go

from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


def _weights_or_default(raw: dict[str, int], jobs: list[str]) -> dict[str, int]:
    # Matches _resolve_weight_map in parallel_mc_pmtn.py: empty map -> all 1s.
    return dict.fromkeys(jobs, 1) if not raw else raw


def build_signed_cost_matrix(
    instance: FFcDDWParameters,
) -> tuple[list[str], list[int], np.ndarray]:
    calJ = instance.job_id_list
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
    for i, j in enumerate(calJ):
        pj = p[j]
        d_minus, d_plus = ddw[j]
        wm, wp = w_minus[j], w_plus[j]
        for k, t in enumerate(t_axis):
            if t <= d_minus - pj:
                Z[i, k] = -wm * math.ceil((d_minus - pj - t + 1) / pj)
            elif t > d_plus:
                Z[i, k] = wp * math.ceil((t - d_plus) / pj)
    return calJ, t_axis, Z


def make_figure(
    calJ: list[str], t_axis: list[int], Z: np.ndarray, title: str
) -> go.Figure:
    z_abs = max(1.0, float(np.abs(Z).max()))
    fig = go.Figure(
        go.Heatmap(
            z=Z,
            x=t_axis,
            y=calJ,
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
        yaxis_title="job",
        yaxis={"autorange": "reversed"},
        width=max(900, len(t_axis) + 200),
        height=max(400, 18 * len(calJ) + 120),
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
            "HTML output path. Defaults to <instance_stem>_C_heatmap.html "
            "next to the instance file."
        ),
    )
    args = parser.parse_args()

    instance_path = args.instance.expanduser().resolve()
    if not instance_path.is_file():
        parser.error(f"Instance file not found: {instance_path}")

    with instance_path.open() as fh:
        instance = FFcDDWParameters.from_pra_2017_data(instance_path.stem, fh)

    calJ, t_axis, Z = build_signed_cost_matrix(instance)
    fig = make_figure(
        calJ,
        t_axis,
        Z,
        title=f"parallel_mc_pmtn C heatmap — {instance_path.stem}",
    )

    out_path = (
        args.output
        if args.output is not None
        else instance_path.with_name(f"{instance_path.stem}_C_heatmap.html")
    )
    out_path = out_path.expanduser().resolve()
    fig.write_html(str(out_path), include_plotlyjs="cdn")

    print(
        f"Wrote {out_path} — jobs={len(calJ)}, "
        f"t-range=[{t_axis[0]}..{t_axis[-1]}] ({len(t_axis)} cells/row)"
    )


if __name__ == "__main__":
    main()
