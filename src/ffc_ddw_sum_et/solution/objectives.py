"""Objective-function helpers shared by FAM, LB init, and future algorithms."""

from __future__ import annotations

from ..parameters.ffc_ddw_params import FFcDDWParameters
from .ffc_schedule import FFcSchedule
from .mcf_preemptive_schedule import MCFPreemptiveSchedule


def compute_weighted_earliness_tardiness(
    schedule: FFcSchedule, instance: FFcDDWParameters
) -> tuple[int, int]:
    """Return (sum_earliness, sum_tardiness) on the last stage of *schedule*.

    Uses the DDW weights ``w^{-}_j`` (earliness) and ``w^{+}_j`` (tardiness)
    against the job's due window ``[d^{-}_j, d^{+}_j]``. Missing weights default
    to 1 to match FAM's original inline calculation.
    """
    last_stage_id = instance.stage_id_list[-1]
    ewt_map = instance.job_2_ewt_map
    twt_map = instance.job_2_twt_map
    due_window_map = instance.job_2_due_window_map

    sum_earliness = 0
    sum_tardiness = 0
    for job_id in instance.job_id_list:
        completion_time = schedule.get_job_end_time(last_stage_id, job_id)
        due_lower, due_upper = due_window_map[job_id]
        ewt = ewt_map.get(job_id, 1)
        twt = twt_map.get(job_id, 1)
        sum_earliness += ewt * max(due_lower - completion_time, 0)
        sum_tardiness += twt * max(completion_time - due_upper, 0)
    return sum_earliness, sum_tardiness


def compute_weighted_et_from_preemptive(
    schedule: MCFPreemptiveSchedule, instance: FFcDDWParameters
) -> tuple[int, int]:
    """Return (sum_earliness, sum_tardiness) for a preemptive last-stage
    schedule. Each job's completion time is the max segment-end across
    its own segments. The result is a *lower bound* on the original
    problem's ET because the upstream MCF relaxes preemption.
    """
    job_to_completion: dict[str, int] = {}
    for job_id, _stage_id, _mc_id, _start, end in schedule.to_gantt_segments():
        if end > job_to_completion.get(job_id, 0):
            job_to_completion[job_id] = end

    ewt_map = instance.job_2_ewt_map
    twt_map = instance.job_2_twt_map
    due_window_map = instance.job_2_due_window_map

    sum_earliness = 0
    sum_tardiness = 0
    for job_id in instance.job_id_list:
        completion_time = job_to_completion.get(job_id, 0)
        due_lower, due_upper = due_window_map[job_id]
        ewt = ewt_map.get(job_id, 1)
        twt = twt_map.get(job_id, 1)
        sum_earliness += ewt * max(due_lower - completion_time, 0)
        sum_tardiness += twt * max(completion_time - due_upper, 0)
    return sum_earliness, sum_tardiness
