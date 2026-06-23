"""Render the thesis introduction-slide DDW Gantt figure as an SVG.

Pipeline: read a YAML config -> load a PRA2017 instance -> subset jobs ->
build a schedule with NEH-CP -> compute each job's completion C_j ->
export a per-job-colored gantt (red/blue free) with a due-window strip
and blue earliness / red tardiness segments.

Usage::

    uv run python scripts/render_intro_ddw_gantt.py \
        [--config metadata/20260610/intro_ddw_figure.yaml]

See ``plans/20260610/intro-figure-ddw-gantt.md`` for the design.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import matplotlib
import yaml

matplotlib.use("Agg")

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.neh_cp import NehCpDispatcher, NehCpOption
from ffc_ddw_sum_et.io.gantt import (
    EARLINESS_COLOR,
    JOB_PALETTE,
    TARDINESS_COLOR,
    DDWGanttPlotter,
)
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("metadata/20260610/intro_ddw_figure.yaml")


def _resolve_job_subset(instance: FFcDDWParameters, cfg: dict) -> list[str]:
    """Resolve the ordered drawn-job subset from the config.

    ``job_subset`` (explicit ordered ids) takes precedence over
    ``job_count`` (first N in instance order). The returned order follows
    the instance ``job_id_list`` regardless of how the subset was given.
    """
    full_order = list(instance.job_id_list)
    explicit = cfg.get("job_subset")
    if explicit:
        unknown = [j for j in explicit if j not in full_order]
        if unknown:
            raise ValueError(f"job_subset contains unknown job ids: {unknown}")
        subset = set(explicit)
    else:
        count = int(cfg.get("job_count", 6))
        if not 0 < count <= len(full_order):
            raise ValueError(f"job_count {count} out of range 1..{len(full_order)}")
        subset = set(full_order[:count])
    return [j for j in full_order if j in subset]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Path to the figure config YAML (default: {DEFAULT_CONFIG}).",
    )
    args = parser.parse_args()

    with args.config.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    instance_path = Path(cfg["instance_path"])
    with instance_path.open(encoding="utf-8") as f:
        instance = FFcDDWParameters.from_pra_2017_data(instance_path.name, f)

    drawn_jobs = _resolve_job_subset(instance, cfg)
    logger.info("Drawing %d jobs: %s", len(drawn_jobs), drawn_jobs)

    subset = FFcDDWParameters.create_instance_of_job_subset(instance, set(drawn_jobs))

    record = NehCpDispatcher().run(
        AlgSpec(
            instance=subset,
            option=NehCpOption(cp_tl_seconds=float(cfg.get("cp_tl_seconds", 5.0))),
        )
    )
    if record.result is None or record.result.schedule is None:
        raise RuntimeError(
            f"NEH-CP produced no schedule (work_status={record.work_status})."
        )
    schedule = record.result.schedule

    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()

    last_stage_id = subset.stage_id_list[-1]
    job_2_completion = {
        j: schedule.get_job_end_time(last_stage_id, j) for j in drawn_jobs
    }
    job_2_dw_map = {j: instance.job_2_due_window_map[j] for j in drawn_jobs}

    # Guard: no per-job color may equal a reserved earliness/tardiness color.
    reserved = {EARLINESS_COLOR.lower(), TARDINESS_COLOR.lower()}
    clash = reserved & {c.lower() for c in JOB_PALETTE}
    if clash:
        raise AssertionError(f"JOB_PALETTE collides with reserved colors: {clash}")

    output_path = Path(cfg.get("output_path", "analysis/20260610/intro_ddw_gantt.svg"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    DDWGanttPlotter().export_ddw(
        file_path=output_path,
        start_time_map=start_map,
        end_time_map=end_map,
        job_2_dw_map=job_2_dw_map,
        job_2_completion=job_2_completion,
        drawn_job_list=drawn_jobs,
        all_job_list=list(instance.job_id_list),
        stage_list=list(subset.stage_id_list),
        machine_list_per_stage=subset.stage_2_machines_map,
        title=cfg.get("title"),
    )
    logger.info("Wrote %s", output_path)


if __name__ == "__main__":
    main()
