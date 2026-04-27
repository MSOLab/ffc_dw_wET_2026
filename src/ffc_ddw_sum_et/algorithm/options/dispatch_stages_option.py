"""Concrete option type for dispatch-by-sequence algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ...parameters.ffc_params import FFcParameters
from ..base.alg_option import AlgOption

__all__ = ["DispatchStagesOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class DispatchStagesOption(AlgOption):
    """Options for stage dispatch based on a job sequence."""

    job_sequence: tuple[str, ...] | None = None
    job_2_release_t: Mapping[str, int] | None = None
    from_stage: str | None = None
    machine_then_job: bool = False

    def resolve_job_sequence(self, instance: FFcParameters) -> tuple[str, ...]:
        """Return the effective job sequence for the instance."""
        if self.job_sequence is not None:
            return self.job_sequence
        return tuple(instance.job_id_list)

    def resolve_job_2_release_t(self, instance: FFcParameters) -> dict[str, int]:
        """Return the effective release time for each job."""
        if self.job_2_release_t is not None:
            return dict(self.job_2_release_t)
        return {job_id: 0 for job_id in instance.job_id_list}
