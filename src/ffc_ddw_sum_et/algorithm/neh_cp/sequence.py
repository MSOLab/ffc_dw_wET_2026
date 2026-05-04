"""Job priority rules for NEH-CP incremental construction."""

from __future__ import annotations

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...parameters.sorter import ParamSortKey, param_sort_job_sequence

__all__ = ["NehCpJobPriority", "neh_cp_job_sequence"]

NehCpJobPriority = ParamSortKey


def neh_cp_job_sequence(
    instance: FFcDDWParameters, job_priority: NehCpJobPriority = "weight-due-pos"
) -> list[str]:
    return param_sort_job_sequence(instance, job_priority)
