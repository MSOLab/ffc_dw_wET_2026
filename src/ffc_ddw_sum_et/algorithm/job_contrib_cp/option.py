"""Option payload for ``JobContribCpDispatcher``."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from typing import Callable

from ..base.alg_option import AlgOption
from ..cumulative import PFMethod

__all__ = ["JobContribCpOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class JobContribCpOption(AlgOption):
    """Inputs for one job-contribution CP-SAT run.

    ``jd_count_target`` is the pre-resolved upper bound on jobs to destroy
    (≥ 1, controller-resolved from the raw ``jd_target`` config value).

    ``pf_method`` must be a concrete ``PFMethod`` (``None`` is rejected)
    because this neighbourhood's identity is profile-fix-based.
    """

    jd_count_target: int
    pf_method: PFMethod = "PF1"
    horizon_multiplier: float = 1.25
    cp_tl_seconds: float | None = None
    wall_clock_deadline_sec: float | None = None
    solver_thread_cnt: int = 1
    time_factor: int = 1
    error_if_infeasible: bool = False
    log_search_progress: bool = False
    solver_log_path_getter: Callable[[str], PathLike[str] | str] | None = None

    def __post_init__(self) -> None:
        if self.jd_count_target < 1:
            raise ValueError(
                f"jd_count_target must be >= 1, got {self.jd_count_target}"
            )
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
