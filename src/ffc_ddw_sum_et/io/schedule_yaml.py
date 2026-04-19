"""Schedule-YAML read/write helpers.

The YAML is the canonical text-only record of a schedule; the algorithm path
writes it during `_post_run_process_inner`, and the reporter reads it during
post-run-process to render Gantt charts. Nothing in this module imports
matplotlib.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from routix.io import dump_yaml, load_yaml

from ..solution.ffc_schedule import FFcSchedule


def dump_schedule_yaml(
    schedule: FFcSchedule,
    path: Path,
    *,
    instance_name: str,
    obj_value: float | None = None,
    obj_bound: float | None = None,
) -> None:
    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()
    operations: list[dict[str, Any]] = []
    for (job_id, stage_id, mc_id), start_time in sorted(
        start_map.items(), key=lambda item: (item[1], item[0])
    ):
        operations.append(
            {
                "job": job_id,
                "stage": stage_id,
                "machine": mc_id,
                "start": int(start_time),
                "end": int(end_map[(job_id, stage_id, mc_id)]),
            }
        )
    data = {
        "instanceName": instance_name,
        "objValue": None if obj_value is None else float(obj_value),
        "objBound": None if obj_bound is None else float(obj_bound),
        "jobs": list(schedule.jobs),
        "stages": list(schedule.stages),
        "machinesPerStage": {
            stage_id: list(mc_ids)
            for stage_id, mc_ids in schedule.machines_per_stage.items()
        },
        "operations": operations,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_yaml(data, path)


def load_schedule_yaml(path: Path) -> dict[str, Any]:
    return load_yaml(path)
