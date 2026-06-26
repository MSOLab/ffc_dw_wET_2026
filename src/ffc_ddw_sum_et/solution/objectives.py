"""Objective-function helpers shared by FAM, LB init, and future algorithms."""

from __future__ import annotations

from typing import Any

from ..parameters.ffc_ddw_params import FFcDDWParameters
from .ffc_schedule import FFcSchedule
from .mcf_preemptive_schedule import MCFPreemptiveSchedule


def compute_weighted_earliness_tardiness(
    schedule: FFcSchedule, instance: FFcDDWParameters, *, time_factor: int = 1
) -> tuple[int, int]:
    """Return (sum_earliness, sum_tardiness) on the last stage of *schedule*.

    Uses the DDW weights ``w^{-}_j`` (earliness) and ``w^{+}_j`` (tardiness)
    against the job's due window ``[d^{-}_j, d^{+}_j]``. Missing weights default
    to 1 to match FAM's original inline calculation.

    ``time_factor`` scales each completion time before the E/T comparison: a
    job's completion ``C`` is interpreted as ``time_factor * C``. This is the
    CSR (coarsen-solve-reconstruct) seed-evaluation case, where *schedule*
    lives on a coarse grid but the penalty must be measured against the
    original-scale due window of *instance* (``factor * C^c`` vs the original
    window). The product stays integer, so the penalty is exact. The default
    ``time_factor=1`` is the ordinary same-scale evaluation.

    Invariant (caller's responsibility, not enforced): ``time_factor * C`` and
    *instance*'s due window must be in the **same time unit**. The CSR caller
    satisfies this by passing the **original** instance together with a
    coarse-grid *schedule* and ``time_factor=factor``. Passing the *coarsened*
    instance with ``time_factor=factor`` compares an original-scale completion
    against a coarse window and is a bug. (This is the same scale-consistency
    requirement the ``time_factor=1`` path already imposes between *schedule*
    and *instance*; ``time_factor`` only generalises it.)
    """
    last_stage_id = instance.stage_id_list[-1]
    ewt_map = instance.job_2_ewt_map
    twt_map = instance.job_2_twt_map
    due_window_map = instance.job_2_due_window_map

    sum_earliness = 0
    sum_tardiness = 0
    for job_id in instance.job_id_list:
        completion_time = time_factor * schedule.get_job_end_time(last_stage_id, job_id)
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


def compute_phase_obj_value(sched: Any, instance: FFcDDWParameters) -> float | None:
    """Return weighted ET on ``sched`` against ``instance``, or ``None`` when
    ``sched`` is on the reversed instance (its "last stage" is then the
    original *first* stage, which has no due window).

    :class:`MCFPreemptiveSchedule` always represents the (preemptive) last
    stage of the original instance, so a per-job completion-time scan
    suffices; the value is a lower bound on the original problem's ET
    (preemption is relaxed away in the upstream MCF).
    """
    if isinstance(sched, MCFPreemptiveSchedule):
        sum_e, sum_t = compute_weighted_et_from_preemptive(sched, instance)
        return float(sum_e + sum_t)
    if sched.stages and sched.stages[-1] == instance.stage_id_list[-1]:
        try:
            sum_e, sum_t = compute_weighted_earliness_tardiness(sched, instance)
        except (KeyError, AttributeError):
            return None
        return float(sum_e + sum_t)
    return None
