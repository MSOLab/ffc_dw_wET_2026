"""Solution tracking for FFcDWwET experiment orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from routix.solution_manager import SolutionManager

from ..solution.ffc_schedule import FFcSchedule
from .subroutine_report import FFcDDWSubroutineReport


@dataclass(frozen=True, slots=True, kw_only=True)
class FFcDDWSolution:
    """Wrapper that pairs a schedule with its objective value."""

    schedule: FFcSchedule
    obj_value: float | None = None
    obj_bound: float | None = None


class FFcDDWSolutionManager(SolutionManager[FFcDDWSubroutineReport, FFcDDWSolution]):
    """Tracks the incumbent best solution across multiple FFcDWwET runs."""

    def _get_obj_value(self, solution: FFcDDWSolution) -> float:
        if solution.obj_value is None:
            raise ValueError(
                "Cannot extract objective from FFcDDWSolution without obj_value"
            )
        return float(solution.obj_value)

    def _a_is_better_obj_value(self, value_a: float, value_b: float | None) -> bool:
        if value_b is None:
            return True
        return value_a < value_b

    def _a_is_better_obj_bound(self, bound_a: float, bound_b: float | None) -> bool:
        # Soundness contract: `best_obj_bound` is consumed by
        # `controller_core.get_current_valid_lb` as a *valid* global LB.
        # This manager therefore trusts every register site to gate on
        # validity (i.e. only forward `obj_bound` for the original problem,
        # not augmented/sub-problem bounds). See
        # `controller_core.get_current_valid_lb` for the full invariant.
        if bound_b is None:
            return True
        return bound_a > bound_b
