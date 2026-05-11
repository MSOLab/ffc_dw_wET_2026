"""Option payload for PwCpDispatcher."""

from dataclasses import dataclass

from ..base.alg_option import AlgOption
from ..cumulative import PFMethod
from ..step_tl_resolver import BatchTlMode

__all__ = ["PwCpOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class PwCpOption(AlgOption):
    """Algorithm-side option for sliding-window CP refinement (PW-CP).

    All time-limit fields are pre-resolved scalars; the controller
    adapter is responsible for evaluating any ``"<n>nc"``-style
    expression strings before constructing this option.

    ``pf_method`` controls the precedence preserved in the left/right
    profile-fixed bands. With ``unfixed_batch_count == 1`` and
    ``pf_method == "PF1"`` the chain order is fully fixed and the CP
    solver can only retime; users seeking exploration should set
    ``unfixed_batch_count >= 2`` or ``pf_method == "PF0"``.
    """

    solver_thread_cnt: int = 1
    batch_size: int = 1
    step_size: int = 1
    unfixed_batch_count: int = 1
    left_profile_fixed_batch_count: int = 0
    right_profile_fixed_batch_count: int = 0
    enable_promotion_profile_fixed: bool = False
    pf_method: PFMethod = "PF1"

    cp_tl_seconds: float | None = None
    total_timelimit_seconds: float | None = None
    batch_tl_mode: BatchTlMode = "constant"
    batch_tl_offset_seconds: float = 0.01
    apply_cumulative_tl: bool = False
    wall_clock_deadline_sec: float | None = None
    """Optional ``time.monotonic()`` deadline used to clamp each batch's
    CP-SAT ``max_time_in_seconds`` by the remaining wall-clock budget.
    ``None`` means "no deadline" — preserves dispatcher-isolated behavior
    for tests.
    """

    error_if_infeasible: bool = False
    keep_step_schedules: bool = False

    horizon_makespan_multiplier: float = 1.25
    """Multiplier applied to the incumbent's makespan to size the CP-SAT
    horizon: ``horizon = ceil(incumbent.makespan * horizon_makespan_multiplier)``.
    ``1.0`` gives a tight horizon (= incumbent makespan); ``1.25`` (default)
    leaves 25% slack. Replaces the legacy ``sum(p)`` bound, which is far
    looser than necessary for a refinement step seeded by a feasible
    incumbent. Must be ``>= 1.0``."""

    log_search_progress: bool = False
    """When True, enable CP-SAT's per-step search log (`log_search_progress=True`,
    `log_to_response=True`). After each step's solve the dispatcher captures
    `solver.response_proto.solve_log` and writes it via the optional
    `solver_log_path_getter` (per-step file under the subroutine dir, named
    by the step index). When the getter is not provided the log is forwarded
    to the dispatcher's logger at INFO level instead. Used to verify hint
    validity (look for "load hints", "objective lower bound" lines)."""

    log_search_progress_max_steps: int | None = None
    """Cap the number of steps for which a search log is captured. ``None``
    (default) captures every step; set to a small int (e.g. 1) to verify
    hints once per run without bloating logs."""

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {self.batch_size}")
        if self.step_size < 1:
            raise ValueError(f"step_size must be >= 1, got {self.step_size}")
        if self.unfixed_batch_count < 1:
            raise ValueError(
                f"unfixed_batch_count must be >= 1, got {self.unfixed_batch_count}"
            )
        if self.left_profile_fixed_batch_count < 0:
            raise ValueError(
                "left_profile_fixed_batch_count must be >= 0, "
                f"got {self.left_profile_fixed_batch_count}"
            )
        if self.right_profile_fixed_batch_count < 0:
            raise ValueError(
                "right_profile_fixed_batch_count must be >= 0, "
                f"got {self.right_profile_fixed_batch_count}"
            )
        if self.horizon_makespan_multiplier < 1.0:
            raise ValueError(
                "horizon_makespan_multiplier must be >= 1.0 "
                "(values < 1 would clip the incumbent itself), got "
                f"{self.horizon_makespan_multiplier}"
            )
