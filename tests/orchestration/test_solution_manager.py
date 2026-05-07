from __future__ import annotations

import pytest
from routix.report import SubroutineReport

from ffc_ddw_sum_et.orchestration.solution_manager import (
    FFcDDWSolution,
    FFcDDWSolutionManager,
)
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule


def _make_schedule() -> FFcSchedule:
    return FFcSchedule(
        jobs=["j0"],
        stages=["i0"],
        machines_per_stage={"i0": ["i0_0"]},
    )


def test_tracks_incumbent() -> None:
    manager = FFcDDWSolutionManager()
    schedule = _make_schedule()

    manager.register(
        SubroutineReport(elapsed_time=0.1, obj_value=10.0, obj_bound=None),
        FFcDDWSolution(schedule=schedule, obj_value=10.0),
    )
    manager.register(
        SubroutineReport(elapsed_time=0.2, obj_value=5.0, obj_bound=None),
        FFcDDWSolution(schedule=schedule, obj_value=5.0),
    )
    manager.register(
        SubroutineReport(elapsed_time=0.3, obj_value=7.0, obj_bound=None),
        FFcDDWSolution(schedule=schedule, obj_value=7.0),
    )

    incumbent = manager.get_incumbent()
    assert incumbent is not None
    assert incumbent.obj_value == 5.0
    assert manager.best_obj_value == 5.0
    assert len(manager.history) == 3


def test_rejects_none_obj_value() -> None:
    manager = FFcDDWSolutionManager()
    schedule = _make_schedule()
    solution = FFcDDWSolution(schedule=schedule, obj_value=None)

    with pytest.raises(ValueError, match="obj_value"):
        manager._get_obj_value(solution)


def test_a_is_better_obj_value_minimization() -> None:
    manager = FFcDDWSolutionManager()

    assert manager._a_is_better_obj_value(3.0, 5.0) is True
    assert manager._a_is_better_obj_value(5.0, 3.0) is False
    assert manager._a_is_better_obj_value(3.0, 3.0) is False
    # None treated as no incumbent, so any value is better
    assert manager._a_is_better_obj_value(3.0, None) is True


def test_a_is_better_obj_bound_maximization() -> None:
    manager = FFcDDWSolutionManager()

    assert manager._a_is_better_obj_bound(5.0, 3.0) is True
    assert manager._a_is_better_obj_bound(3.0, 5.0) is False
    assert manager._a_is_better_obj_bound(3.0, 3.0) is False
    assert manager._a_is_better_obj_bound(3.0, None) is True
