"""Solution JSON read/write helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..solution.ffc_schedule import FFcSchedule
from . import schedule_keys as K

logger = logging.getLogger(__name__)


def _json_default(obj):
    """Handle numpy/pandas types for JSON serialization."""
    import numpy as np

    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if hasattr(obj, "tolist"):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _extract_operations(schedule: FFcSchedule) -> list[dict]:
    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()
    operations = []
    for (job_id, stage_id, mc_id), start in sorted(start_map.items()):
        operations.append(
            {
                K.OP_STAGE: stage_id,
                K.OP_MACHINE: mc_id,
                K.OP_JOB: job_id,
                K.OP_START: start,
                K.OP_END: end_map.get((job_id, stage_id, mc_id)),
            }
        )
    return operations


def dump_solution_json(
    schedule: FFcSchedule,
    path: Path | str,
    *,
    instance_name: str,
    obj_value: float | None = None,
    obj_bound: float | None = None,
) -> None:
    """Write a solution schedule as JSON."""
    operations = _extract_operations(schedule)
    data = {
        K.INSTANCE_NAME: instance_name,
        K.OBJ_VALUE: None if obj_value is None else float(obj_value),
        K.OBJ_BOUND: None if obj_bound is None else float(obj_bound),
        K.JOBS: list(schedule.jobs),
        K.STAGES: list(schedule.stages),
        K.MACHINES_PER_STAGE: {
            stage: list(mcs) for stage, mcs in schedule.machines_per_stage.items()
        },
        K.OPERATIONS: operations,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=_json_default)
    logger.info(
        "Solution JSON written: %s (jobs=%d, ops=%d)",
        path,
        len(data[K.JOBS]),
        len(operations),
    )
