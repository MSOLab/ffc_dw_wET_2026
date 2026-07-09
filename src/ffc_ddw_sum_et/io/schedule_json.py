"""Solution JSON read/write helpers."""

from __future__ import annotations

import json
import logging
from os import PathLike
from pathlib import Path
from typing import Sequence

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


def load_schedule_json(
    path: Path | PathLike[str] | str,
) -> tuple[FFcSchedule, float | None, float | None]:
    """Load a solution schedule from a JSON file.

    Rebuilds an :class:`FFcSchedule` from the ``operations`` list (exact
    machine + start/end times preserved) and returns it together with the
    stored ``objValue`` / ``objBound`` (each ``None`` when absent). Used by the
    resume-from-base path to restore a prior run's incumbent.

    Args:
        path (Path | PathLike[str] | str): Path to the JSON file.

    Returns:
        tuple[FFcSchedule, float | None, float | None]: The loaded schedule and
            its objective value and bound.
    """
    with open(path) as f:
        data = json.load(f)

    jobs = list(data[K.JOBS])
    stages = list(data[K.STAGES])
    machines_per_stage = {
        stage: list(mcs) for stage, mcs in data[K.MACHINES_PER_STAGE].items()
    }
    schedule = FFcSchedule(
        jobs=jobs, stages=stages, machines_per_stage=machines_per_stage
    )
    for op in data[K.OPERATIONS]:
        schedule.add_ops_times_2_mc(
            stage_id=op[K.OP_STAGE],
            mc_id=op[K.OP_MACHINE],
            job_id=op[K.OP_JOB],
            start_time=int(op[K.OP_START]),
            end_time=int(op[K.OP_END]),
        )

    obj_value = data.get(K.OBJ_VALUE)
    obj_bound = data.get(K.OBJ_BOUND)
    return (
        schedule,
        None if obj_value is None else float(obj_value),
        None if obj_bound is None else float(obj_bound),
    )


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
    path: Path | PathLike[str] | str,
    *,
    instance_name: str,
    obj_value: float | None = None,
    obj_bound: float | None = None,
    compact: bool = False,
) -> None:
    """Write a solution schedule as JSON.

    ``compact=True`` writes a single-line file with tight separators (suitable
    for high-volume per-phase emissions); the default keeps the human-readable
    indented form used for the canonical solution artifact.
    """
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
        if compact:
            json.dump(data, f, separators=(",", ":"), default=_json_default)
        else:
            json.dump(data, f, indent=2, default=_json_default)
    logger.info(
        "Solution JSON written: %s (jobs=%d, ops=%d, compact=%s)",
        path,
        len(data[K.JOBS]),
        len(operations),
        compact,
    )


def dump_preemptive_schedule_json(
    path: Path | PathLike[str] | str,
    *,
    instance_name: str,
    stage_id: str,
    machines: Sequence[str],
    jobs: Sequence[str],
    segments: Sequence[tuple[str, str, str, int, int]],
    all_jobs: Sequence[str] | None = None,
    obj_value: float | None = None,
    obj_bound: float | None = None,
    compact: bool = False,
) -> None:
    """Write a preemptive schedule as JSON (mirror of dump_preemptive_schedule_yaml).

    ``segments`` is a flat list of ``(job, stage, machine, start, end)``
    tuples; a single ``(job, stage, machine)`` triple may appear in
    multiple segments (preemption). ``compact=True`` writes a single-line
    file with tight separators.
    """
    segment_records: list[dict] = []
    for job_id, stage_i, mc_id, start_time, end_time in sorted(
        segments, key=lambda seg: (seg[1], seg[2], seg[3], seg[0])
    ):
        segment_records.append(
            {
                K.OP_JOB: job_id,
                K.OP_STAGE: stage_i,
                K.OP_MACHINE: mc_id,
                K.OP_START: int(start_time),
                K.OP_END: int(end_time),
            }
        )
    data = {
        K.INSTANCE_NAME: instance_name,
        K.OBJ_VALUE: None if obj_value is None else float(obj_value),
        K.OBJ_BOUND: None if obj_bound is None else float(obj_bound),
        K.STAGE_ID: stage_id,
        K.JOBS: list(jobs),
        K.ALL_JOBS: list(all_jobs) if all_jobs is not None else list(jobs),
        K.MACHINES_PER_STAGE: {stage_id: list(machines)},
        K.SEGMENTS: segment_records,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        if compact:
            json.dump(data, f, separators=(",", ":"), default=_json_default)
        else:
            json.dump(data, f, indent=2, default=_json_default)
    logger.info(
        "Preemptive schedule JSON written: %s (stage=%s, segments=%d, compact=%s)",
        path,
        stage_id,
        len(segment_records),
        compact,
    )
