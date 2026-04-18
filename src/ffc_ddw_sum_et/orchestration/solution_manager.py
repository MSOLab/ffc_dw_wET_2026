"""Solution tracking for FAM experiment orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from routix.report import SubroutineReport
from routix.solution_manager import SolutionManager

from ..solution.ffc_schedule import FFcSchedule


@dataclass(frozen=True, slots=True, kw_only=True)
class FFcDDWSolution:
    """Wrapper that pairs a schedule with its objective value."""

    schedule: FFcSchedule
    obj_value: float | None = None
    obj_bound: float | None = None


class FFcDDWSolutionManager(SolutionManager[SubroutineReport, FFcDDWSolution]):
    """Tracks the incumbent best solution across multiple FAM runs."""

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
        # FAM does not produce useful bounds
        return False
