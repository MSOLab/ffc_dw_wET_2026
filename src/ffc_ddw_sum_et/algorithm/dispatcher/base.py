"""BaseDispatcher extracted from hybridflowshop/dispatcher/base.py.

Trimmed to the fields and helpers that `MixedDispatcher` and `BN2DDispatcher`
actually need, plus the classical permutation-flowshop sequence generators
(Johnson / CDS / Gupta / Palmer) used by ``MixedDispatcher``'s
``get_schedule_by_{cds,gupta,palmer}`` wrappers.
"""

from __future__ import annotations

import logging
from typing import Mapping

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule


class BaseDispatcher:
    def __init__(
        self,
        instance: FFcDDWParameters,
        logger: logging.Logger | None = None,
        job_tiebreak_rank: Mapping[str, int] | None = None,
    ) -> None:
        self.instance = instance
        self.stage_2_job_2_p: dict[str, dict[str, int]] = instance.stage_2_job_2_p_map
        self.job_2_stage_2_p: dict[str, dict[str, int]] = instance.job_2_stage_2_p_map
        self.stage_id_list: list[str] = list(instance.stage_id_list)
        self.job_id_list: list[str] = list(instance.job_id_list)
        self.machines_per_stage: dict[str, list[str]] = {
            stage_id: list(mc_ids)
            for stage_id, mc_ids in instance.stage_2_machines_map.items()
        }
        self.stage_count: int = len(self.stage_id_list)
        self.job_count: int = len(self.job_id_list)
        self.job_id_2_original_index: dict[str, int] = {
            job_id: idx for idx, job_id in enumerate(self.job_id_list)
        }
        self.job_tiebreak_rank: dict[str, int] = dict(job_tiebreak_rank or {})
        self.logger: logging.Logger = logger or logging.getLogger(__name__)

    def _get_rank_tiebreak_key(self, job_id: str) -> int:
        """Return rank-based tie-break key, falling back to original order."""
        if self.job_tiebreak_rank:
            return self.job_tiebreak_rank.get(
                job_id,
                self.job_id_2_original_index.get(job_id, len(self.job_tiebreak_rank)),
            )
        return self.job_id_2_original_index.get(job_id, 0)

    def get_johnsons_rule_sequence(
        self,
        job_2_p1_map: Mapping[str, int],
        job_2_p2_map: Mapping[str, int],
    ) -> list[str]:
        """Classical two-machine Johnson rule job sequence."""
        jobs = list(job_2_p1_map.keys())
        l1: list[str] = []
        l2: list[str] = []
        for job_id in jobs:
            if job_2_p1_map[job_id] <= job_2_p2_map[job_id]:
                l1.append(job_id)
            else:
                l2.append(job_id)

        l1.sort(key=lambda j: (job_2_p1_map[j], self._get_rank_tiebreak_key(j)))
        if self.job_tiebreak_rank:
            l2.sort(
                key=lambda j: (
                    -job_2_p2_map[j],
                    self._get_rank_tiebreak_key(j),
                )
            )
        else:
            l2.sort(key=lambda j: (job_2_p2_map[j], j), reverse=True)
        return l1 + l2

    def get_cds_sequence(self, k: int) -> list[str]:
        """Campbell-Dudek-Smith k-cut aggregation over stages."""
        jobs = self.job_id_list
        stages = self.stage_id_list
        m = self.stage_count
        p_dict = self.job_2_stage_2_p

        p1_stages = stages[:k]
        p2_stages = stages[m - k :]
        p1 = {j: sum(p_dict[j][i] for i in p1_stages) for j in jobs}
        p2 = {j: sum(p_dict[j][i] for i in p2_stages) for j in jobs}
        return self.get_johnsons_rule_sequence(p1, p2)

    def get_gupta_sequence(self) -> list[str]:
        """Gupta's functional heuristic sequence (sign / min adjacent sum)."""
        jobs = self.job_id_list
        stages = self.stage_id_list
        m = len(stages)

        gupta_score: dict[str, float] = {}
        total_p: dict[str, int] = {}
        for job_id in jobs:
            stage_2_p = self.job_2_stage_2_p[job_id]
            min_sum = min(
                stage_2_p[stages[idx]] + stage_2_p[stages[idx + 1]]
                for idx in range(m - 1)
            )
            sign = 1 if stage_2_p[stages[-1]] <= stage_2_p[stages[0]] else -1
            gupta_score[job_id] = float("inf") if min_sum == 0 else sign / min_sum
            total_p[job_id] = sum(stage_2_p[s] for s in stages)

        return sorted(
            jobs,
            key=lambda j: (
                gupta_score[j],
                total_p[j],
                self._get_rank_tiebreak_key(j),
            ),
        )

    def get_palmer_sequence(self) -> list[str]:
        """Palmer's slope-index sequence."""
        jobs = self.job_id_list
        stages = self.stage_id_list
        m = len(stages)

        palmer_score: dict[str, int] = {}
        for job_id in jobs:
            stage_2_p = self.job_2_stage_2_p[job_id]
            palmer_score[job_id] = sum(
                (m - 2 * (stage_idx + 1) + 1) * stage_2_p[stages[stage_idx]]
                for stage_idx in range(m)
            )
        return sorted(
            jobs,
            key=lambda j: (palmer_score[j], self._get_rank_tiebreak_key(j)),
        )

    def _create_empty_schedule(
        self, instance: FFcDDWParameters | None = None
    ) -> FFcSchedule:
        if instance is None:
            jobs = list(self.job_id_list)
            stages = list(self.stage_id_list)
            machines_per_stage = {
                stage_id: list(mc_ids)
                for stage_id, mc_ids in self.machines_per_stage.items()
            }
        else:
            jobs = list(instance.job_id_list)
            stages = list(instance.stage_id_list)
            machines_per_stage = {
                stage_id: list(mc_ids)
                for stage_id, mc_ids in instance.stage_2_machines_map.items()
            }
        return FFcSchedule(
            jobs=jobs,
            stages=stages,
            machines_per_stage=machines_per_stage,
        )

    def _prepare_schedule_for_dispatch(
        self,
        schedule: FFcSchedule | None,
        in_place: bool = False,
    ) -> FFcSchedule:
        if schedule is None:
            return self._create_empty_schedule()
        if in_place:
            return schedule
        return schedule.deepcopy()
