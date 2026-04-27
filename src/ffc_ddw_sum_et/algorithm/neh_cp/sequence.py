"""Job priority rules for NEH-CP incremental construction."""

from __future__ import annotations

from typing import Literal

from ...parameters.ffc_ddw_params import FFcDDWParameters

__all__ = ["NehCpJobPriority", "neh_cp_job_sequence"]

NehCpJobPriority = Literal[
    "weight-due-pos", "due-weight-pos", "due*-weight-pos", "due2-weight-pos"
]


def neh_cp_job_sequence(
    instance: FFcDDWParameters, job_priority: NehCpJobPriority = "weight-due-pos"
) -> list[str]:
    if job_priority == "weight-due-pos":
        return instance.get_weight_due_pos_job_sequence()
    if job_priority == "due-weight-pos":
        return instance.get_due_weight_pos_job_sequence()
    if job_priority == "due*-weight-pos":
        return instance.get_due_star_weight_pos_job_sequence()
    if job_priority == "due2-weight-pos":
        return instance.due2_weight_pos_job_sequence()
    raise ValueError(f"Unknown job_priority: {job_priority!r}")
