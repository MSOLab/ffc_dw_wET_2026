"""Single source of truth for job-contribution-based destruction selection.

``select_jd_jobs`` is a pure function callable from both the controller
(pre-check before dispatching) and the dispatcher (inside ``run``).
"""

from __future__ import annotations

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_job_2_obj_contrib_map

__all__ = ["select_jd_jobs"]


def select_jd_jobs(
    incumbent: FFcSchedule,
    instance: FFcDDWParameters,
    jd_count_target: int,
    *,
    time_factor: int = 1,
) -> list[str]:
    """Top ``jd_count_target`` jobs by contribution, ties broken by ``job_id``.

    Jobs with zero contribution are excluded from the candidate pool, so the
    returned list length is ``min(jd_count_target, #{j : f_j(C_j) > 0})``.
    An empty list means the incumbent has ``obj == 0``.
    """
    job_2_contrib = compute_job_2_obj_contrib_map(
        incumbent, instance, time_factor=time_factor
    )
    positive_jobs = [j for j, v in job_2_contrib.items() if v > 0]
    return sorted(positive_jobs, key=lambda j: (-job_2_contrib[j], j))[:jd_count_target]
