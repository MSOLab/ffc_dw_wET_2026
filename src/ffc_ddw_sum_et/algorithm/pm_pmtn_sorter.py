"""Sort-key vocabulary for preemptive-schedule-derived job sequences.

Lightweight module: defines the :data:`PmPrmpSortKey` literal and its two
dispatchers. No runtime imports from the rest of the algorithm package,
so callers that need just the type (notably the IO heatmap module and
``mcf_lb.utils``) can import it without triggering the full
:mod:`algorithm` package init chain.

Two dispatchers:

* :func:`pm_pmtn_sort_job_sequence_from_window_map` — core logic; takes a
  pre-computed ``window_map`` and ``duration_map``. Used by callers that
  already have a stored preemptive schedule.
* :func:`pm_pmtn_sort_job_sequence` — convenience wrapper that derives the
  ``window_map`` and ``duration_map`` from a live
  :class:`~ffc_ddw_sum_et.algorithm.parallel_mc_pmtn.ParallelMachinePreemptionMcf`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Mapping

if TYPE_CHECKING:
    from ..parameters.ffc_ddw_params import FFcDDWParameters
    from .parallel_mc_pmtn import ParallelMachinePreemptionMcf

__all__ = [
    "PmPrmpSortKey",
    "pm_pmtn_sort_job_sequence",
    "pm_pmtn_sort_job_sequence_from_window_map",
]

PmPrmpSortKey = Literal[
    "1_rj_prmp_rel_dev",
    "1_rj_prmp_abs_dev",
    "start_time",
    "end_time",
    "start_time_maxw",
    "end_time_maxw",
]


def pm_pmtn_sort_job_sequence_from_window_map(
    window_map: Mapping[str, tuple[int, int] | None],
    duration_map: Mapping[str, int],
    instance: FFcDDWParameters,
    key: PmPrmpSortKey = "1_rj_prmp_rel_dev",
) -> list[str]:
    """Sort jobs by a preemptive-schedule key from a pre-computed window map.

    ``window_map[j] = (t_min, t_max)`` describes the preemptive activity
    window for job ``j``; ``None`` denotes no flow. Tie-breakers (in order):
    total weight ``-(w⁻+w⁺)`` desc, native ``instance.job_id_list`` position
    asc. Jobs with ``None`` windows are placed last.
    """
    job_id_list = instance.job_id_list
    job_2_pos = {j: i for i, j in enumerate(job_id_list)}
    ewt = instance.job_2_ewt_map or dict.fromkeys(job_id_list, 1)
    twt = instance.job_2_twt_map or dict.fromkeys(job_id_list, 1)

    def sort_key(j: str) -> tuple[int, float, int, int]:
        window = window_map[j]
        if key == "1_rj_prmp_rel_dev":
            return (
                0 if window is not None else 1,
                ((window[1] - window[0]) / duration_map[j])
                if window is not None
                else 0.0,
                -(ewt[j] + twt[j]),
                job_2_pos[j],
            )
        if key == "1_rj_prmp_abs_dev":
            return (
                0 if window is not None else 1,
                ((window[1] - window[0]) - duration_map[j])
                if window is not None
                else 0.0,
                -(ewt[j] + twt[j]),
                job_2_pos[j],
            )
        if key == "start_time":
            return (
                0 if window is not None else 1,
                float(window[0]) if window is not None else 0.0,
                -(ewt[j] + twt[j]),
                job_2_pos[j],
            )
        if key == "end_time":
            return (
                0 if window is not None else 1,
                float(window[1]) if window is not None else 0.0,
                -(ewt[j] + twt[j]),
                job_2_pos[j],
            )
        if key == "start_time_maxw":
            return (
                0 if window is not None else 1,
                float(window[0]) if window is not None else 0.0,
                -max(ewt[j], twt[j]),
                job_2_pos[j],
            )
        if key == "end_time_maxw":
            return (
                0 if window is not None else 1,
                float(window[1]) if window is not None else 0.0,
                -max(ewt[j], twt[j]),
                job_2_pos[j],
            )
        raise ValueError(f"Unknown PmPrmpSortKey: {key!r}")

    return sorted(job_id_list, key=sort_key)


def pm_pmtn_sort_job_sequence(
    mcf: ParallelMachinePreemptionMcf,
    instance: FFcDDWParameters,
    key: PmPrmpSortKey = "1_rj_prmp_rel_dev",
) -> list[str]:
    """Convenience: derive ``window_map`` and ``duration_map`` from a live MCF.

    Must be called after ``mcf.solve()`` succeeds. Uses the segment-aligned
    window convention ``(min(t with flow) - 1, max(t with flow))`` so the
    result matches the half-open ``[t-1, t)`` semantics used by
    ``MCFPreemptiveSchedule.from_flow_dict`` (and the IO heatmap sort).
    """
    x_val = mcf.get_variable_value_dict()
    window_map: dict[str, tuple[int, int] | None] = {}
    for j in instance.job_id_list:
        ts = list(x_val.get(j, {}).keys())
        window_map[j] = (min(ts) - 1, max(ts)) if ts else None
    return pm_pmtn_sort_job_sequence_from_window_map(window_map, mcf.p, instance, key)
