"""BaseDispatcher extracted from hybridflowshop/dispatcher/base.py.

Trimmed to the fields and helpers that `MixedDispatcher` actually needs; the
CDS/Gupta/Palmer/Johnson sequence generators are intentionally omitted (not
used by the MCF LB-init path).
"""

from __future__ import annotations

import logging

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule


class BaseDispatcher:
    def __init__(
        self,
        instance: FFcDDWParameters,
        logger: logging.Logger | None = None,
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
        self.logger: logging.Logger = logger or logging.getLogger(__name__)

    def _create_empty_schedule(self) -> FFcSchedule:
        return FFcSchedule(
            jobs=list(self.job_id_list),
            stages=list(self.stage_id_list),
            machines_per_stage={
                stage_id: list(mc_ids)
                for stage_id, mc_ids in self.machines_per_stage.items()
            },
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
