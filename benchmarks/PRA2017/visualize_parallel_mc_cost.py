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
from pathlib import Path

from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf
from ffc_ddw_sum_et.io.parallel_mc_cost_heatmap import (
    build_signed_cost_matrix,
    heatmap_title,
    make_figure,
)
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters


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
    parser.add_argument(
        "--sort",
        choices=[
            "due2-weight-pos",
            "weight-due-pos",
            "due-weight-pos",
            "due*-weight-pos",
            "wxd1",
            "wxd2",
            "1_rj_prmp_rel_dev",
            "1_rj_prmp_abs_dev",
            "start_time",
        ],
        default="due2-weight-pos",
        help="Job row ordering in the heatmap. Defaults to 'due2-weight-pos'.",
    )
    args = parser.parse_args()

    instance_path = args.instance.expanduser().resolve()
    if not instance_path.is_file():
        parser.error(f"Instance file not found: {instance_path}")

    with instance_path.open() as fh:
        instance = FFcDDWParameters.from_pra_2017_data(instance_path.stem, fh)

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

    out_path = (
        args.output
        if args.output is not None
        else instance_path.with_name(f"{instance_path.stem}_C_heatmap.html")
    )
    out_path = out_path.expanduser().resolve()
    fig.write_html(str(out_path), include_plotlyjs="cdn")

    print(
        f"Wrote {out_path} — jobs={len(data.y_labels)}, "
        f"t-range=[{data.t_axis[0]}..{data.t_axis[-1]}] "
        f"({len(data.t_axis)} cells/row), x_jt cells={len(data.x_cells)}"
    )


if __name__ == "__main__":
    main()
