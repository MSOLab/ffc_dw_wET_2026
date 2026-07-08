"""Render MCF-LB process + flip-makespan Gantt charts as SVG.

Reads the phase-schedule JSONs emitted for a single run (both steps run with
``emit_phase_schedules: true``) and renders vector SVGs:

  * ``run_flip_makespan_cp_from_incumbent`` process series (5 steps) ->
      - ``<inst>_mcf_lb_init.svg``              <- (0) ``01_incumbent`` (seed)
      - ``<inst>_flip_p1_flipped.svg``          <- (1) ``03_flipped``
      - ``<inst>_flip_p2_cp_makespan_min.svg``  <- (2) ``05_cp_solved``
      - ``<inst>_flip_p3_reflipped.svg``        <- (3) ``06_unflipped_semi_active``
      - ``<inst>_flip_makespan.svg``            <- (4) ``07_unflipped_final``
  * ``calc_mcf_lb_and_derive_full_sch`` per-round phases ->
      - ``<inst>_mcflb_<round>_<index>_<label>.svg`` for every intermediate
        schedule (preemptive MCF LP + non-preemptive last-stage / full
        schedules across rounds r1/r2)

The flip ``operations[]`` phases reuse ``_render_gantt_from_solution_json``;
the MCF-LB phases mix preemptive (``segments[]``) and non-preemptive
(``operations[]``) shapes, so they reuse ``_render_phase_gantt_from_json``
(auto-detects the shape). Both helpers infer the image format from the output
path extension (``.svg`` -> vector).

When ``calc_mcf_lb_and_derive_full_sch`` runs with
``draw_pmtn_sch_heatmap: true``, it also emits one signed C-cost heatmap YAML
per round (``..._r1_C_heatmap.yaml`` / ``..._r2_C_heatmap.yaml``). These are
the same figures ``benchmarks/PRA2017/visualize_parallel_mc_cost.py`` draws
(shared ``make_figure``); we render each as a vector SVG via plotly + kaleido:

  * ``<inst>_mcflb_<round>_C_heatmap.svg``  (pairs with the ``1_mcf_preemptive``
    gantt of the same round)

Usage::

    uv run python scripts/render_mcf_lb_flip_makespan_svg.py \
        [--run-dir output/20260611/<timestamp>] \
        [--output-dir analysis/20260611]

When ``--run-dir`` is omitted, the most recent timestamped run directory under
``output/20260611`` is used.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ffc_ddw_sum_et.orchestration.reporting import (
    _render_gantt_from_solution_json,
    _render_phase_gantt_from_json,
)

logger = logging.getLogger(__name__)

DEFAULT_RUN_PARENT = Path("output/20260611")
DEFAULT_OUTPUT_DIR = Path("analysis/20260611")

# Flip-makespan process series, in execution order. (phase suffix in the
# emitted JSON filename, output svg label). The five steps:
#   (0) given schedule (the MCF-LB seed handed to the flip step)
#   (1) given schedule after time + stage flipping
#   (2) flipped schedule after CP makespan minimization
#   (3) re-flipped (unflipped) schedule
#   (4) final schedule (re-flipped + idle-time insertion)
# (0) keeps its standalone "mcf_lb_init" name (it is also the MCF-LB output);
# (4) keeps "flip_makespan" (the optimized result). (1)-(3) are the added
# intermediate process figures. Note (1)/(2) live in the flipped, stage-
# reversed coordinate space, so their Gantts read mirrored on purpose.
PHASES = [
    ("01_incumbent", "mcf_lb_init"),  # (0)
    ("03_flipped", "flip_p1_flipped"),  # (1) after time + stage flip
    ("05_cp_solved", "flip_p2_cp_makespan_min"),  # (2) after CP makespan min
    ("06_unflipped_semi_active", "flip_p3_reflipped"),  # (3) re-flipped
    ("07_unflipped_final", "flip_makespan"),  # (4) final
]


def _render_heatmap_svg(yaml_path: Path, svg_path: Path) -> None:
    """Render a signed C-cost heatmap YAML to a vector SVG.

    Reuses the same ``load_signed_cost_heatmap_yaml`` / ``make_figure`` path as
    ``visualize_parallel_mc_cost.py`` and the post-run reporter, but exports a
    static SVG (plotly + kaleido) instead of interactive HTML.
    """
    from ffc_ddw_sum_et.io.parallel_mc_cost_heatmap import (
        heatmap_title,
        load_signed_cost_heatmap_yaml,
        make_figure,
    )

    data = load_signed_cost_heatmap_yaml(yaml_path)
    if not data.y_labels or not data.t_axis or data.Z.size == 0:
        logger.warning("Empty heatmap data, skipping %s", yaml_path)
        return
    fig = make_figure(data, title=heatmap_title(data))
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(str(svg_path))


def _latest_run_dir(parent: Path) -> Path:
    candidates = [p for p in parent.iterdir() if p.is_dir()]
    if not candidates:
        raise FileNotFoundError(f"No run directories under {parent}")
    return max(candidates, key=lambda p: p.name)


def _find_phase_json(run_dir: Path, suffix: str) -> Path:
    matches = sorted(run_dir.glob(f"**/progress/*_{suffix}.json"))
    if not matches:
        raise FileNotFoundError(
            f"No phase schedule '*_{suffix}.json' under {run_dir}. "
            "Did the run use emit_phase_schedules: true on the flip step?"
        )
    if len(matches) > 1:
        logger.warning(
            "Multiple '*_%s.json' matches; using first: %s", suffix, matches[0]
        )
    return matches[0]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help=f"Run directory. Default: latest under {DEFAULT_RUN_PARENT}.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"SVG output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    args = parser.parse_args()

    run_dir = args.run_dir or _latest_run_dir(DEFAULT_RUN_PARENT)
    logger.info("Rendering from run dir: %s", run_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # --- flip-makespan step: incumbent (MCF-LB seed) + final output ---------
    for suffix, label in PHASES:
        json_path = _find_phase_json(run_dir, suffix)
        # phase filename: "<step>-<method>_<NN>_<phase>.json"; the leading
        # token up to "_<NN>_" carries the instance via its parent dirs, so we
        # derive the instance name from the progress dir's grandparent.
        instance_name = json_path.parent.parent.name
        out_path = args.output_dir / f"{instance_name}_{label}.svg"
        _render_gantt_from_solution_json(json_path, out_path)
        logger.info("Wrote %s (from %s)", out_path, json_path.name)

    # --- MCF-LB step: every per-round intermediate schedule -----------------
    # progress/<inst>/calc_mcf_lb_and_derive_full_sch/<round>/<index>_<label>.json
    mcflb_phases = sorted(run_dir.glob("**/calc_mcf_lb_and_derive_full_sch/*/*_*.json"))
    if not mcflb_phases:
        logger.warning(
            "No MCF-LB phase JSONs found; was calc_mcf_lb_and_derive_full_sch "
            "run with emit_phase_schedules: true?"
        )
    for json_path in mcflb_phases:
        round_part = json_path.parent.name  # e.g. "r1"
        # instance name is the dir three levels up: <inst>/progress/<step>/<round>
        instance_name = json_path.parent.parent.parent.parent.name
        out_path = (
            args.output_dir / f"{instance_name}_mcflb_{round_part}_{json_path.stem}.svg"
        )
        _render_phase_gantt_from_json(json_path, out_path)
        logger.info("Wrote %s (from %s/%s)", out_path, round_part, json_path.name)

    # --- MCF-LB step: per-round signed C-cost heatmaps (parallel_mc_cost) ----
    # progress/<inst>/<step>_<round>_C_heatmap.yaml
    heatmap_yamls = sorted(run_dir.glob("**/progress/*_C_heatmap.yaml"))
    if not heatmap_yamls:
        logger.warning(
            "No C-cost heatmap YAMLs found; was calc_mcf_lb_and_derive_full_sch "
            "run with draw_pmtn_sch_heatmap: true?"
        )
    for yaml_path in heatmap_yamls:
        instance_name = yaml_path.parent.parent.name
        # stem ".._r1_C_heatmap"; pull the "rN" round token for the label.
        stem = yaml_path.stem
        round_tag = next(
            (
                tok
                for tok in stem.split("_")
                if tok and tok[0] == "r" and tok[1:].isdigit()
            ),
            "r",
        )
        out_path = args.output_dir / f"{instance_name}_mcflb_{round_tag}_C_heatmap.svg"
        _render_heatmap_svg(yaml_path, out_path)
        logger.info("Wrote %s (from %s)", out_path, yaml_path.name)


if __name__ == "__main__":
    main()
