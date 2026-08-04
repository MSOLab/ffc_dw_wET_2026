"""Option payload for ``JobContribCpDispatcher``."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from typing import Callable, Literal

from ..base.alg_option import AlgOption
from ..cumulative import PFMethod

__all__ = ["JobContribCpOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class JobContribCpOption(AlgOption):
    """Inputs for one job-contribution CP-SAT run.

    Exactly one of ``jd_count_target`` and ``destroy_job_ids`` must be set:

    - ``jd_count_target`` (int ≥ 1): pre-resolved upper bound; the
      dispatcher selects that many top-contributing jobs.
    - ``destroy_job_ids`` (non-empty tuple of job IDs, no duplicates):
      the dispatcher destroys exactly those jobs regardless of
      contribution.
    """

    jd_count_target: int | None = None
    destroy_job_ids: tuple[str, ...] | None = None
    pf_method: PFMethod = "PF1"
    horizon_multiplier: float = 1.25
    cp_tl_seconds: float | None = None
    cp_tl_mode: Literal["constant", "proportional"] = "constant"
    destroyed_op_tl_multiplier: float | None = None
    wall_clock_deadline_sec: float | None = None
    solver_thread_cnt: int = 1
    time_factor: int = 1
    error_if_infeasible: bool = False
    log_search_progress: bool = False
    solver_log_path_getter: Callable[[str], PathLike[str] | str] | None = None

    def __post_init__(self) -> None:
        has_target = self.jd_count_target is not None
        has_explicit = self.destroy_job_ids is not None
        if not (has_target ^ has_explicit):
            raise ValueError(
                "Exactly one of jd_count_target or destroy_job_ids must be set; "
                f"jd_count_target={self.jd_count_target!r}, "
                f"destroy_job_ids={self.destroy_job_ids!r}"
            )
        if has_target:
            if self.jd_count_target < 1:
                raise ValueError(
                    f"jd_count_target must be >= 1, got {self.jd_count_target}"
                )
        if has_explicit:
            ids = self.destroy_job_ids
            if len(ids) == 0:
                raise ValueError("destroy_job_ids must not be empty")
            if len(set(ids)) != len(ids):
                raise ValueError(f"destroy_job_ids contains duplicates: {ids}")
        if self.pf_method is None:
            raise ValueError(
                "pf_method cannot be None in JobContribCpOption; "
                "profile-fix is the identity of this neighbourhood."
            )
        if self.time_factor < 1:
            raise ValueError(f"time_factor must be >= 1, got {self.time_factor}")
        if self.horizon_multiplier <= 0:
            raise ValueError(
                f"horizon_multiplier must be > 0, got {self.horizon_multiplier}"
            )
        if self.destroyed_op_tl_multiplier is not None:
            if self.destroyed_op_tl_multiplier <= 0:
                raise ValueError(
                    "destroyed_op_tl_multiplier must be > 0, "
                    f"got {self.destroyed_op_tl_multiplier}"
                )
        if (
            self.cp_tl_mode == "proportional"
            and self.destroyed_op_tl_multiplier is None
        ):
            raise ValueError(
                "destroyed_op_tl_multiplier is required when cp_tl_mode='proportional'"
            )
