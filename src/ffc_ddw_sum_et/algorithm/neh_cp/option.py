"""Option payload for NehCpDispatcher."""

from dataclasses import dataclass
from typing import Literal

from ..base.alg_option import AlgOption
from ..cumulative import PFMethod
from ..step_tl_resolver import BatchTlMode
from .sequence import NehCpJobPriority

__all__ = ["NehCpOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class NehCpOption(AlgOption):
    """Algorithm-side option for incremental NEH-CP construction.

    All time-limit / batch-extra fields are pre-resolved scalars; the
    controller adapter is responsible for evaluating any ``"<n>nc"``-style
    expression strings before building this option.

    ``custom_job_sequence`` overrides the priority-rule-derived sequence
    when provided. ``NehCpDispatcher`` validates it is a permutation of
    the instance's ``job_id_list``; ``job_priority`` is then ignored.

    ``make_semi_active_after_cp_obj_threshold`` overrides the boolean
    ``make_semi_active_after_cp`` flag when set to a non-negative value:
    semi-active rebuild is applied iff the per-step CP-SAT weighted E+T
    is at or above the threshold. ``-1`` (default) keeps the boolean
    flag in effect.

    ``keep_step_schedules`` toggles per-step schedule capture: when
    ``True``, the dispatcher attaches a list of ``(step,
    dispatched_schedule, cp_raw_schedule, semi_active_schedule)``
    tuples to ``result.metrics["step_schedules"]`` for downstream
    diagnostic emission. Cloning every step's schedule is O(n*c) per
    step, so leave off for production runs.
    """

    job_priority: NehCpJobPriority = "weight-due-pos"
    custom_job_sequence: tuple[str, ...] | None = None
    solver_thread_cnt: int = 1
    added_batch_size: int = 1
    extra_batch_size_extra: int = 0
    cp_tl_seconds: float | None = None
    total_timelimit_seconds: float | None = None
    num_batches: int | None = None
    batch_tl_mode: BatchTlMode = "constant"
    batch_tl_offset_seconds: float = 0.01
    apply_cumulative_tl: bool = False
    pf_method: PFMethod = "PF1"
    skip_pf_below_obj: Literal["makespan"] | float | None = None
    make_semi_active_after_cp: bool = False
    make_semi_active_after_cp_obj_threshold: int = -1
    minimize_makespan_lex: bool = False
    cp_tl_2nd_obj_seconds: float | None = None
    error_if_infeasible: bool = False
    keep_step_schedules: bool = False

    wall_clock_deadline_sec: float | None = None
    """Optional ``time.monotonic()`` deadline used to clamp each batch's
    CP-SAT ``max_time_in_seconds`` by the remaining wall-clock budget.
    ``None`` means "no deadline" — preserves today's behavior for
    isolated dispatcher tests / scripts.
    """

    objective_lower_bound: float | None = None
    """Optional valid global lower bound on the full-instance weighted
    E+T objective. Passed to ``BaseModelBuilder.build`` as ``obj_lb``
    only at the **last** NEH-CP batch (which by construction covers
    every job, so the global LB is a valid LB on that batch's CP-SAT
    objective). Lets CP-SAT prove optimality early when the bound is
    tight. ``None`` (default) preserves today's behavior.
    """

    @classmethod
    def coerce_skip_pf_below_obj(
        cls, value: str | float | None
    ) -> Literal["makespan"] | float | None:
        """Normalize a raw ``skip_pf_below_obj`` value.

        Accepts the controller-facing input grammar (``"makespan"`` /
        float / numeric string / None) and returns the typed form stored
        on the option.
        """
        if value is None or value == "makespan":
            return value
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(
                f"Invalid skip_pf_below_obj value: {value!r}; "
                "expected 'makespan', a float, or None."
            ) from exc
