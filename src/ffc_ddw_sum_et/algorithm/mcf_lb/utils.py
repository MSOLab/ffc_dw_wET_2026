"""Shared helpers for MCF-LB-driven job sequencing.

Both ``orchestration.controller._mcf_window_width_job_sequence`` and the
last-stage-only NEH-CP step want to sort jobs by ascending normalized
window width derived from the MCF flow. The shape of the input differs
(controller has the live ``ParallelMachinePreemptionMcf`` handle; the
NEH-CP step only has a stored ``MCFPreemptiveSchedule``), so this module
exposes both a window-map builder for the schedule path and a generic
sort that takes any pre-computed window map.
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule

__all__ = [
    "jobs_sorted_by_normalized_window_width",
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


def jobs_sorted_by_normalized_window_width(
    window_map: Mapping[str, tuple[int, int] | None],
    duration_map: Mapping[str, int],
    instance: FFcDDWParameters,
    *,
    logger: logging.Logger | None = None,
) -> list[str]:
    """Sort jobs by ascending ``(t_max - t_min) / p_{c,j}``.

    Tie-breakers, in order: total due-window weight ``-(w⁻+w⁺)`` (so
    higher-weighted jobs come first on ties), native
    ``instance.job_id_list`` position ASC. Jobs with ``window=None`` go
    last so the sort is total.

    When ``logger`` is provided, emits a rank-by-rank table at INFO level
    so a reader can verify the ordering.
    """
    job_id_list = instance.job_id_list
    job_2_pos = {j: i for i, j in enumerate(job_id_list)}
    ewt = instance.job_2_ewt_map or dict.fromkeys(job_id_list, 1)
    twt = instance.job_2_twt_map or dict.fromkeys(job_id_list, 1)

    def sort_key(j: str) -> tuple[int, float, int, int]:
        window = window_map[j]
        return (
            0 if window is not None else 1,
            ((window[1] - window[0]) / duration_map[j]) if window is not None else 0.0,
            -(ewt[j] + twt[j]),
            job_2_pos[j],
        )

    sorted_jobs = sorted(job_id_list, key=sort_key)

    if logger is not None:
        id_w = max(len(j) for j in job_id_list)
        logger.info(
            "MCF-induced job sequence "
            "(rank | %-*s | width | p_cj | width/p_cj | (w-+w+) | native_pos):",
            id_w,
            "job_id",
        )
        for rank, j in enumerate(sorted_jobs):
            window = window_map[j]
            width = (window[1] - window[0]) if window is not None else None
            ratio = (width / duration_map[j]) if width is not None else None
            logger.info(
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
