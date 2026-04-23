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

MAX_Z_ABS = 100


def _weights_or_default(raw: dict[str, int], jobs: list[str]) -> dict[str, int]:
    # Matches objectives.py: missing weights default to 1
    return dict.fromkeys(jobs, 1) if not raw else raw


def _sort_jobs(
    instance: FFcDDWParameters,
    sort: Literal["due-window", "neh-cp"] = "due-window",
) -> list[str]:
    if sort == "neh-cp":
        return instance.get_neh_cp_job_sequence()
    return sorted(instance.job_id_list, key=lambda j: instance.job_2_due_window_map[j])


def build_wet_cost_matrix(
    instance: FFcDDWParameters,
    sort: Literal["due-window", "neh-cp"] = "due-window",
) -> tuple[list[str], list[int], np.ndarray]:
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
    for i, j in enumerate(calJ):
        d_minus, d_plus = ddw[j]
        wm, wp = w_minus[j], w_plus[j]
        for k, t in enumerate(t_axis):
            if t < d_minus:
                Z[i, k] = -wm * (d_minus - t)
            elif t > d_plus:
                Z[i, k] = wp * (t - d_plus)
    Z = np.clip(Z, -MAX_Z_ABS, MAX_Z_ABS)
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
            colorbar={"title": "wET"},
            hovertemplate=("job=%{y}<br>t=%{x}<br>wET=%{z}<extra></extra>"),
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="completion time t",
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
            "'due-window' sorts by (d⁻, d⁺) tuples. "
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

    calJ, t_axis, Z = build_wet_cost_matrix(instance, sort=args.sort)
    fig = make_figure(
        calJ,
        t_axis,
        Z,
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
        f"Wrote {out_path} — jobs={len(calJ)}, "
        f"t-range=[{t_axis[0]}..{t_axis[-1]}] ({len(t_axis)} cells/row)"
    )


if __name__ == "__main__":
    main()
