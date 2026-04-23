"""BaseDispatcher extracted from hybridflowshop/dispatcher/base.py.

Trimmed to the fields and helpers that `MixedDispatcher` and `BN2DDispatcher`
actually need; the CDS/Gupta/Palmer/Johnson sequence generators are intentionally
omitted.
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
