"""Option payload for ``JobBatchCpDispatcher``."""

from __future__ import annotations

from dataclasses import dataclass

from ..base.alg_option import AlgOption
from ..cumulative import PFMethod
from ..step_tl_resolver import BatchTlMode

__all__ = ["JobBatchCpOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class JobBatchCpOption(AlgOption):
    """Algorithm-side option for job-batch CP-SAT sweep.

    ``job_sequence`` is the full permutation of job IDs defining batch
    membership — consecutive slices form each batch.
    """

    job_sequence: tuple[str, ...]
    batch_size: int = 1
    num_batches: int | None = None
    pf_method: PFMethod = "PF1"
    cp_tl_seconds: float | None = None
    total_timelimit_seconds: float | None = None
    batch_tl_mode: BatchTlMode = "constant"
    batch_tl_offset_seconds: float = 0.01
    destroyed_op_tl_multiplier: float | None = None
    horizon_multiplier: float = 1.25
    wall_clock_deadline_sec: float | None = None
    solver_thread_cnt: int = 1
    time_factor: int = 1
    error_if_infeasible: bool = False

    def __post_init__(self) -> None:
        if len(self.job_sequence) == 0:
            raise ValueError("job_sequence must not be empty")
        if len(set(self.job_sequence)) != len(self.job_sequence):
            raise ValueError("job_sequence contains duplicate job IDs")
        if self.batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {self.batch_size}")
        if self.num_batches is not None and self.num_batches < 1:
            raise ValueError(f"num_batches must be >= 1, got {self.num_batches}")
        if self.pf_method is None:
            raise ValueError(
                "pf_method cannot be None in JobBatchCpOption; "
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
            self.batch_tl_mode == "proportional"
            and self.destroyed_op_tl_multiplier is None
        ):
            # Without this the mode is a silent trap: resolve_per_step_tl
            # returns None for "proportional" (the dispatcher owns that mode),
            # so every batch would run with no CP limit at all and the first
            # one would consume the whole pass.
            raise ValueError(
                "destroyed_op_tl_multiplier is required when "
                "batch_tl_mode='proportional'"
            )
