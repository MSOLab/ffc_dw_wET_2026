"""Option payload for NehCpDispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..base.alg_option import AlgOption
from ..cumulative import PFMethod
from .sequence import NehCpJobPriority
from .tl_schedule import NehCpBatchTlMode

__all__ = ["NehCpOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class NehCpOption(AlgOption):
    """Algorithm-side option for incremental NEH-CP construction.

    All time-limit / batch-extra fields are pre-resolved scalars; the
    controller adapter is responsible for evaluating any ``"<n>nc"``-style
    expression strings before building this option.
    """

    job_priority: NehCpJobPriority = "weight-due-pos"
    solver_thread_cnt: int = 1
    added_batch_size: int = 1
    extra_batch_size_extra: int = 0
    cp_tl_seconds: float | None = None
    total_timelimit_seconds: float | None = None
    num_batches: int | None = None
    batch_tl_mode: NehCpBatchTlMode = "constant"
    batch_tl_offset_seconds: float = 0.01
    apply_cumulative_tl: bool = False
    pf_method: PFMethod = "PF1"
    skip_pf_below_obj: Literal["makespan"] | float | None = None
    make_semi_active_after_cp: bool = False
    minimize_makespan_lex: bool = False
    cp_tl_2nd_obj_seconds: float | None = None
    error_if_infeasible: bool = False

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
