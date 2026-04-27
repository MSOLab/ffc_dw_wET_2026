"""MixedDispatcher adapted from hybridflowshop/dispatcher/mixed.py.

Candidate schedules are scored by weighted earliness+tardiness by default
(matching the FFcDDW objective), and can optionally be scored by makespan
via ``criteria="makespan"`` to preserve upstream semantics when used inside
BN2D / Johnson / CDS / Gupta / Palmer variants.
"""

from __future__ import annotations

import math
from typing import Literal, Sequence

from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from .base import BaseDispatcher
from .utils import from_job_sequence_get_schedule_mixed


class MixedDispatcher(BaseDispatcher):
    """Mixed dispatch with head/tail concept, scored by weighted ET (default)
    or makespan (when ``criteria="makespan"``)."""

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
        criteria: Literal["weighted_et", "makespan"] = "weighted_et",
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

            if criteria == "makespan":
                obj = _schedule.makespan
            else:
                sum_e, sum_t = compute_weighted_earliness_tardiness(
                    _schedule, self.instance
                )
                obj = sum_e + sum_t
            if best_obj is None or obj < best_obj:
                best_obj = obj
                best_sch = _schedule

        return best_sch

    def get_schedule_by_cds(
        self,
        schedule: FFcSchedule | None = None,
        from_stage: str | None = None,
        job_2_release_t: dict[str, int] | None = None,
        machine_then_job: bool = False,
        head_for_all_stages: bool = False,
        use_palmer_index: bool = False,
    ) -> FFcSchedule | None:
        """Run the mixed decoder on every CDS k-cut (1 <= k < stage_count) and
        return the best candidate by ``criteria``."""
        best_obj: float | None = None
        best_sch: FFcSchedule | None = None
        for k in range(1, self.stage_count):
            base_schedule = schedule.deepcopy() if schedule is not None else None
            job_sequence = self.get_cds_sequence(k)
            dispatched = self.get_best_mixed_schedule_by_sequence(
                job_sequence,
                schedule=base_schedule,
                from_stage=from_stage,
                job_2_release_t=job_2_release_t,
                machine_then_job=machine_then_job,
                head_for_all_stages=head_for_all_stages,
                use_palmer_index=use_palmer_index,
                criteria="makespan",
            )
            if dispatched is None:
                continue
            obj = dispatched.makespan
            if best_obj is None or obj < best_obj:
                best_obj = obj
                best_sch = dispatched
        return best_sch

    def get_schedule_by_gupta(
        self,
        schedule: FFcSchedule | None = None,
        from_stage: str | None = None,
        job_2_release_t: dict[str, int] | None = None,
        machine_then_job: bool = False,
        head_for_all_stages: bool = False,
        use_palmer_index: bool = False,
    ) -> FFcSchedule | None:
        """Run the mixed decoder on the Gupta sequence."""
        base_schedule = schedule.deepcopy() if schedule is not None else None
        return self.get_best_mixed_schedule_by_sequence(
            self.get_gupta_sequence(),
            schedule=base_schedule,
            from_stage=from_stage,
            job_2_release_t=job_2_release_t,
            machine_then_job=machine_then_job,
            head_for_all_stages=head_for_all_stages,
            use_palmer_index=use_palmer_index,
            criteria="makespan",
        )

    def get_schedule_by_palmer(
        self,
        schedule: FFcSchedule | None = None,
        from_stage: str | None = None,
        job_2_release_t: dict[str, int] | None = None,
        machine_then_job: bool = False,
        head_for_all_stages: bool = False,
        use_palmer_index: bool = False,
    ) -> FFcSchedule | None:
        """Run the mixed decoder on the Palmer slope-index sequence."""
        base_schedule = schedule.deepcopy() if schedule is not None else None
        return self.get_best_mixed_schedule_by_sequence(
            self.get_palmer_sequence(),
            schedule=base_schedule,
            from_stage=from_stage,
            job_2_release_t=job_2_release_t,
            machine_then_job=machine_then_job,
            head_for_all_stages=head_for_all_stages,
            use_palmer_index=use_palmer_index,
            criteria="makespan",
        )
