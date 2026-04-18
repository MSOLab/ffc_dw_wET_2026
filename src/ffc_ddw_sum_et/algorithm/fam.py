"""First Available Machine decoder for DDW flexible flow shop instances."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.ffc_schedule import FFcSchedule
from .base.alg_option import AlgOption
from .base.alg_record import AlgRecord, AlgResult, TerminationReason, WorkStatus
from .base.alg_spec import AlgSpec

__all__ = ["FAMDispatcher", "FAMOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class FAMOption(AlgOption):
    """Option payload for the FAM decoder."""

    job_sequence: tuple[str, ...] | None = None
    job_2_release_t: Mapping[str, int] | None = None

    def resolve_initial_job_sequence(
        self, instance: FFcDDWParameters
    ) -> tuple[str, ...]:
        """Return the initial permutation for the decoder."""
        if self.job_sequence is None:
            return tuple(instance.job_id_list)

        expected_jobs = tuple(instance.job_id_list)
        if len(self.job_sequence) != len(expected_jobs):
            raise ValueError(
                "FAM job_sequence must include every instance job exactly once."
            )
        if set(self.job_sequence) != set(expected_jobs):
            raise ValueError(
                "FAM job_sequence must be a permutation of the instance job list."
            )
        if len(set(self.job_sequence)) != len(self.job_sequence):
            raise ValueError("FAM job_sequence must not contain duplicate jobs.")
        return self.job_sequence

    def resolve_job_2_release_t(self, instance: FFcDDWParameters) -> dict[str, int]:
        """Return the effective job release times for the decoder."""
        if self.job_2_release_t is None:
            return {job_id: 0 for job_id in instance.job_id_list}
        return {
            job_id: int(self.job_2_release_t.get(job_id, 0))
            for job_id in instance.job_id_list
        }


class FAMDispatcher:
    """Decode a permutation using the First Available Machine rule."""

    algorithm_id = "fam"

    def run(self, spec: AlgSpec) -> AlgRecord:
        instance = self._validate_instance(spec)
        option = self._resolve_option(spec)
        if spec.ref_solution is not None:
            raise NotImplementedError(
                "FAMDispatcher does not support ref_solution yet."
            )

        initial_job_sequence = option.resolve_initial_job_sequence(instance)
        job_2_release_t = option.resolve_job_2_release_t(instance)
        initial_job_2_pos = {
            job_id: idx for idx, job_id in enumerate(initial_job_sequence)
        }
        stage_2_job_2_p = instance.stage_2_job_2_p_map

        self._debug(
            spec,
            "Starting FAM decode for instance=%s with initial_job_sequence=%s",
            instance.name,
            initial_job_sequence,
        )

        schedule = FFcSchedule(
            jobs=list(instance.job_id_list),
            stages=list(instance.stage_id_list),
            machines_per_stage={
                stage_id: list(machine_ids)
                for stage_id, machine_ids in instance.stage_2_machines_map.items()
            },
        )

        stage_id_list = list(instance.stage_id_list)
        if not stage_id_list:
            raise ValueError("FAMAlgorithm requires at least one stage.")

        first_stage_id = stage_id_list[0]
        schedule.dispatch_stage_by_jobs(
            first_stage_id,
            initial_job_sequence,
            stage_2_job_2_p[first_stage_id],
            job_2_release=job_2_release_t,
        )
        self._debug(
            spec,
            "Decoded stage=%s with job_sequence=%s",
            first_stage_id,
            initial_job_sequence,
        )

        for stage_idx in range(1, len(stage_id_list)):
            prev_stage_id = stage_id_list[stage_idx - 1]
            stage_id = stage_id_list[stage_idx]
            stage_job_sequence = self._build_stage_job_sequence(
                schedule=schedule,
                instance=instance,
                prev_stage_id=prev_stage_id,
                initial_job_2_pos=initial_job_2_pos,
            )
            schedule.dispatch_stage_by_jobs(
                stage_id,
                stage_job_sequence,
                stage_2_job_2_p[stage_id],
            )
            self._debug(
                spec,
                "Decoded stage=%s from prev_stage=%s with job_sequence=%s",
                stage_id,
                prev_stage_id,
                stage_job_sequence,
            )

        sum_earliness, sum_tardiness = self._calculate_window_et(schedule, instance)
        obj_value = sum_earliness + sum_tardiness
        self._debug(
            spec,
            "Completed FAM decode for instance=%s obj_value=%s makespan=%s",
            instance.name,
            obj_value,
            schedule.makespan,
        )

        return AlgRecord(
            work_status=WorkStatus.FEASIBLE,
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=schedule,
                obj_value=obj_value,
                obj_bound=None,
                metrics={
                    "sum_earliness": sum_earliness,
                    "sum_tardiness": sum_tardiness,
                    "makespan": schedule.makespan,
                },
            ),
            termination_reason=TerminationReason.COMPLETED,
        )

    def _validate_instance(self, spec: AlgSpec) -> FFcDDWParameters:
        if not isinstance(spec.instance, FFcDDWParameters):
            raise TypeError("FAMAlgorithm requires FFcDDWParameters as spec.instance.")
        return spec.instance

    def _resolve_option(self, spec: AlgSpec) -> FAMOption:
        if spec.option is None:
            return FAMOption()
        if not isinstance(spec.option, FAMOption):
            raise TypeError("FAMAlgorithm requires FAMOption as spec.option.")
        return spec.option

    def _build_stage_job_sequence(
        self,
        schedule: FFcSchedule,
        instance: FFcDDWParameters,
        prev_stage_id: str,
        initial_job_2_pos: Mapping[str, int],
    ) -> tuple[str, ...]:
        due_upper = {
            job_id: due_window[1]
            for job_id, due_window in instance.job_2_due_window_map.items()
        }
        return tuple(
            sorted(
                instance.job_id_list,
                key=lambda job_id: (
                    schedule.get_job_end_time(prev_stage_id, job_id),
                    self._slack_at_previous_stage(
                        schedule, prev_stage_id, job_id, due_upper
                    ),
                    initial_job_2_pos[job_id],
                ),
            )
        )

    def _slack_at_previous_stage(
        self,
        schedule: FFcSchedule,
        prev_stage_id: str,
        job_id: str,
        due_upper: Mapping[str, int],
    ) -> int:
        completion_time = schedule.get_job_end_time(prev_stage_id, job_id)
        return due_upper[job_id] - completion_time

    def _calculate_window_et(
        self,
        schedule: FFcSchedule,
        instance: FFcDDWParameters,
    ) -> tuple[int, int]:
        last_stage_id = instance.stage_id_list[-1]
        sum_earliness = 0
        sum_tardiness = 0
        for job_id in instance.job_id_list:
            completion_time = schedule.get_job_end_time(last_stage_id, job_id)
            due_lower, due_upper = instance.job_2_due_window_map[job_id]
            ewt = instance.job_2_ewt_map.get(job_id, 1)
            twt = instance.job_2_twt_map.get(job_id, 1)
            sum_earliness += ewt * max(due_lower - completion_time, 0)
            sum_tardiness += twt * max(completion_time - due_upper, 0)
        return sum_earliness, sum_tardiness

    def _debug(self, spec: AlgSpec, msg: str, *args: object) -> None:
        if spec.logger is not None:
            spec.logger.debug(msg, *args)
        else:
            logging.debug(msg, *args)
