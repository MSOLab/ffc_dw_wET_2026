"""MixedDispatcher adapted from hybridflowshop/dispatcher/mixed.py.

Only the sequence-driven entry point is ported (the CDS/Gupta/Palmer variants
are not needed for the MCF LB-init path). Candidate schedules are scored by
weighted earliness+tardiness instead of makespan, matching the FFcDDW
objective.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_window_et
from .base import BaseDispatcher
from .utils import from_job_sequence_get_schedule_mixed


class MixedDispatcher(BaseDispatcher):
    """Mixed dispatch with head/tail concept, scored by weighted ET."""

    def _get_np_candidates(self) -> list[int]:
        np = self.job_count
        np_list = [np]
        while np > 1:
            np = math.ceil(np / 2)
            np_list.append(np)
        np_list.append(0)
        return np_list

    def get_best_mixed_schedule_by_sequence(
        self,
        job_sequence: Sequence[str],
        schedule: FFcSchedule | None = None,
        from_stage: str | None = None,
        job_2_release_t: dict[str, int] | None = None,
        machine_then_job: bool = False,
        head_for_all_stages: bool = False,
        use_palmer_index: bool = False,
    ) -> FFcSchedule | None:
        best_obj: float | None = None
        best_sch: FFcSchedule | None = None

        np_list = self._get_np_candidates()
        np_2_stage_2_head: dict[int, dict[str, int]] = {}

        for np in np_list:
            if head_for_all_stages:
                np_2_stage_2_head[np] = {
                    stage_id: np for stage_id in self.stage_id_list
                }
            elif from_stage is not None:
                if from_stage not in self.stage_id_list:
                    raise ValueError(f"from_stage {from_stage} not in stage list")
                np_2_stage_2_head[np] = {from_stage: np}
            else:
                np_2_stage_2_head[np] = {self.stage_id_list[0]: np}

        for np in np_list:
            _schedule = (
                schedule.deepcopy()
                if schedule is not None
                else self._create_empty_schedule()
            )
            try:
                from_job_sequence_get_schedule_mixed(
                    _schedule,
                    job_sequence,
                    self.stage_2_job_2_p,
                    np_2_stage_2_head[np],
                    from_stage=from_stage,
                    job_2_release=job_2_release_t,
                    machine_then_job=machine_then_job,
                    use_palmer_index=use_palmer_index,
                )
            except ValueError:
                continue

            sum_e, sum_t = compute_window_et(_schedule, self.instance)
            obj = sum_e + sum_t
            if best_obj is None or obj < best_obj:
                best_obj = obj
                best_sch = _schedule

        return best_sch
