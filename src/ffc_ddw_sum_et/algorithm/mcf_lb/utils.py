"""Helpers for MCF-LB-driven job sequencing.

The actual sort logic lives in
:mod:`ffc_ddw_sum_et.algorithm.sort_keys.pm_pmtn_sort_job_sequence_from_window_map`
(SSOT). This module wraps it with two MCF-LB-specific concerns:

* a window-map builder for ``MCFPreemptiveSchedule`` (the stored, post-
  controller-discard form), and
* a logging-enabled wrapper that emits the rank-by-rank table consumed by
  the NEH-CP last-stage diagnostics.
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ..pm_pmtn_sorter import PmPrmpSortKey, pm_pmtn_sort_job_sequence_from_window_map

__all__ = [
    "pm_pmtn_sort_job_sequence_with_log",
    "window_map_from_preemptive_schedule",
]


def window_map_from_preemptive_schedule(
    schedule: MCFPreemptiveSchedule,
    job_id_list: Sequence[str],
) -> dict[str, tuple[int, int] | None]:
    """For each job, return ``(min start, max end)`` across its segments.

    Jobs with no segments map to ``None``. Mirrors the contract of
    ``ParallelMachinePreemptionMcf.get_job_2_time_window_map`` but works
    off the stored preemptive schedule rather than the live MCF handle.
    """
    window_map: dict[str, tuple[int, int] | None] = {j: None for j in job_id_list}
    for _mc, job_id, start, end in schedule.segments:
        cur = window_map.get(job_id)
        if cur is None:
            window_map[job_id] = (start, end)
        else:
            window_map[job_id] = (min(cur[0], start), max(cur[1], end))
    return window_map


def pm_pmtn_sort_job_sequence_with_log(
    window_map: Mapping[str, tuple[int, int] | None],
    duration_map: Mapping[str, int],
    instance: FFcDDWParameters,
    *,
    logger: logging.Logger | None = None,
    log_level: int = logging.DEBUG,
    job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
) -> list[str]:
    """Sort jobs by a :data:`PmPrmpSortKey`, with optional rank-by-rank logging.

    Delegates the sort to
    :func:`pm_pmtn_sort_job_sequence_from_window_map`. When ``logger`` is
    provided, emits a rank-by-rank table at INFO level so a reader can
    verify the ordering.
    """
    sorted_jobs = pm_pmtn_sort_job_sequence_from_window_map(
        window_map, duration_map, instance, job_priority
    )

    if logger is not None:
        job_id_list = instance.job_id_list
        job_2_pos = {j: i for i, j in enumerate(job_id_list)}
        ewt = instance.job_2_ewt_map or dict.fromkeys(job_id_list, 1)
        twt = instance.job_2_twt_map or dict.fromkeys(job_id_list, 1)
        id_w: int = max(len(j) for j in job_id_list)
        logger.log(
            log_level,
            "MCF-induced job sequence "
            "(rank | %-*s | width | p_cj | width/p_cj | (w-+w+) | native_pos):",
            id_w,
            "job_id",
        )
        for rank, j in enumerate(sorted_jobs):
            window = window_map[j]
            width = (window[1] - window[0]) if window is not None else None
            ratio = (width / duration_map[j]) if width is not None else None
            logger.log(
                log_level,
                "  %4d | %-*s | %s | %4d | %s | %4d | %4d",
                rank,
                id_w,
                j,
                f"{width:>5}" if width is not None else f"{'None':>5}",
                duration_map[j],
                f"{ratio:>10.4f}" if ratio is not None else f"{'None':>10}",
                ewt[j] + twt[j],
                job_2_pos[j],
            )

    return sorted_jobs
