from __future__ import annotations

import bisect
import logging
from typing import Iterable, Iterator, Mapping, Sequence

JobIdType = str
StageIdType = str
McIdType = str
OperationType = tuple[JobIdType, StageIdType, McIdType]


class FFcSchedule:
    jobs: Sequence[JobIdType]
    stages: Sequence[StageIdType]
    machines_per_stage: Mapping[StageIdType, Sequence[McIdType]]

    stage_2_index: Mapping[StageIdType, int]
    stage_2_prev_stage: Mapping[StageIdType, StageIdType | None]

    __stage_2_mc_2_job_tuple_seq: dict[
        StageIdType, dict[McIdType, list[tuple[JobIdType, int, int]]]
    ]
    """stage -> machine -> [(job_id, start_time, end_time)]"""

    __stage_2_job_2_start_time: dict[StageIdType, dict[JobIdType, int]]
    __stage_2_job_2_end_time: dict[StageIdType, dict[JobIdType, int]]

    def __init__(
        self,
        jobs: Sequence[JobIdType],
        stages: Sequence[StageIdType],
        machines_per_stage: Mapping[StageIdType, Sequence[McIdType]],
    ):
        self.jobs = jobs
        self.stages = stages
        self.machines_per_stage = machines_per_stage
        self.stage_2_index = {stage: i for i, stage in enumerate(stages)}
        self.stage_2_prev_stage = {
            stage: stages[i - 1] if i > 0 else None for i, stage in enumerate(stages)
        }
        self._initialize_variables()

    def _initialize_variables(self) -> None:
        self.__stage_2_mc_2_job_tuple_seq = {
            stage: {mc: [] for mc in self.machines_per_stage[stage]}
            for stage in self.stages
        }
        self.__stage_2_job_2_start_time = {stage: {} for stage in self.stages}
        self.__stage_2_job_2_end_time = {stage: {} for stage in self.stages}

    def _validate_stage(self, stage_id: StageIdType) -> None:
        if stage_id not in self.stages:
            raise ValueError(f"Invalid stage ID: {stage_id}")

    def _validate_machine(self, stage_id: StageIdType, mc_id: McIdType) -> None:
        self._validate_stage(stage_id)
        if mc_id not in self.machines_per_stage[stage_id]:
            raise ValueError(f"Invalid machine ID: {mc_id} for stage ID: {stage_id}")

    def _validate_job(self, job_id: JobIdType) -> None:
        if job_id not in self.jobs:
            raise ValueError(f"Invalid job ID: {job_id}")

    def validate(
        self,
        stage_id: StageIdType | None = None,
        mc_id: McIdType | None = None,
        job_id: JobIdType | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> None:
        if stage_id is not None:
            self._validate_stage(stage_id)
        if mc_id is not None:
            if stage_id is None:
                raise ValueError("stage_id is required when validating mc_id")
            self._validate_machine(stage_id, mc_id)
        if job_id is not None:
            self._validate_job(job_id)
        if start_time is not None and start_time < 0:
            raise ValueError(f"Start time cannot be negative: {start_time}")
        if end_time is not None and end_time < 0:
            raise ValueError(f"End time cannot be negative: {end_time}")
        if start_time is not None and end_time is not None and end_time < start_time:
            raise ValueError(
                f"End time {end_time} cannot be earlier than start time {start_time}"
            )

    def _stage_contains_job(self, stage_id: StageIdType, job_id: JobIdType) -> bool:
        if job_id in self.__stage_2_job_2_start_time[stage_id]:
            return True
        if job_id in self.__stage_2_job_2_end_time[stage_id]:
            return True
        return any(
            scheduled_job_id == job_id
            for mc_id in self.machines_per_stage[stage_id]
            for scheduled_job_id, _, _ in self.__stage_2_mc_2_job_tuple_seq[stage_id][
                mc_id
            ]
        )

    def _validate_new_operation(
        self,
        stage_id: StageIdType,
        mc_id: McIdType,
        job_id: JobIdType,
        start_time: int,
        end_time: int,
    ) -> None:
        self.validate(
            stage_id=stage_id,
            mc_id=mc_id,
            job_id=job_id,
            start_time=start_time,
            end_time=end_time,
        )
        if self._stage_contains_job(stage_id, job_id):
            raise ValueError(
                f"Job ID {job_id} already scheduled in stage ID {stage_id}"
            )

    def _rebuild_stage_time_caches(self, stage_id: StageIdType) -> None:
        self._validate_stage(stage_id)
        job_2_start_time: dict[JobIdType, int] = {}
        job_2_end_time: dict[JobIdType, int] = {}
        for mc_id in self.machines_per_stage[stage_id]:
            for job_id, start_time, end_time in self.__stage_2_mc_2_job_tuple_seq[
                stage_id
            ][mc_id]:
                job_2_start_time[job_id] = start_time
                job_2_end_time[job_id] = end_time
        self.__stage_2_job_2_start_time[stage_id] = job_2_start_time
        self.__stage_2_job_2_end_time[stage_id] = job_2_end_time

    def _invalidate_stage_jobs(
        self, stage_id: StageIdType, job_ids: Iterable[JobIdType]
    ) -> None:
        self._validate_stage(stage_id)
        for job_id in job_ids:
            self.__stage_2_job_2_start_time[stage_id].pop(job_id, None)
            self.__stage_2_job_2_end_time[stage_id].pop(job_id, None)

    def deepcopy(self, job_subset: set[JobIdType] | None = None) -> FFcSchedule:
        new_schedule = FFcSchedule(
            jobs=list(self.jobs),
            stages=list(self.stages),
            machines_per_stage={
                stage: list(machines)
                for stage, machines in self.machines_per_stage.items()
            },
        )
        for stage in self.stages:
            for mc in self.machines_per_stage[stage]:
                new_schedule.__stage_2_mc_2_job_tuple_seq[stage][mc] = [
                    (job_id, start_time, end_time)
                    for job_id, start_time, end_time in self.__stage_2_mc_2_job_tuple_seq[
                        stage
                    ][mc]
                    if job_subset is None or job_id in job_subset
                ]
            new_schedule.__stage_2_job_2_start_time[stage] = {
                job_id: start_time
                for job_id, start_time in self.__stage_2_job_2_start_time[stage].items()
                if job_subset is None or job_id in job_subset
            }
            new_schedule.__stage_2_job_2_end_time[stage] = {
                job_id: end_time
                for job_id, end_time in self.__stage_2_job_2_end_time[stage].items()
                if job_subset is None or job_id in job_subset
            }
        return new_schedule

    def as_reversed(self) -> FFcSchedule:
        makespan = self.makespan
        new_schedule = FFcSchedule(
            jobs=list(self.jobs),
            stages=list(reversed(self.stages)),
            machines_per_stage={
                stage: list(machines)
                for stage, machines in self.machines_per_stage.items()
            },
        )
        for stage_id, mc_id, start_time, end_time, job_id in self._iter_operations():
            new_schedule.add_ops_times_2_mc(
                stage_id=stage_id,
                mc_id=mc_id,
                job_id=job_id,
                start_time=makespan - end_time,
                end_time=makespan - start_time,
            )
        return new_schedule

    def get_job_sequence(
        self, stage_id: StageIdType, mc_id: McIdType
    ) -> list[tuple[JobIdType, int, int]]:
        self._validate_machine(stage_id, mc_id)
        return self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id]

    def get_machine_latest_end_time(
        self, stage_id: StageIdType, mc_id: McIdType
    ) -> int:
        job_tuple_seq = self.get_job_sequence(stage_id, mc_id)
        if not job_tuple_seq:
            return 0
        return job_tuple_seq[-1][2]

    def get_machine_earliest_start_time(
        self,
        stage_id: StageIdType,
        mc_id: McIdType,
        duration: int,
        release_t: int | None = None,
        after_last: bool = False,
    ) -> int:
        self._validate_machine(stage_id, mc_id)
        if duration <= 0:
            raise ValueError("Duration must be greater than 0")

        earliest_start = 0 if release_t is None else release_t
        if after_last:
            return max(
                earliest_start, self.get_machine_latest_end_time(stage_id, mc_id)
            )

        job_tuple_seq = self.get_job_sequence(stage_id, mc_id)
        if not job_tuple_seq:
            return earliest_start

        starts = [start_time for _, start_time, _ in job_tuple_seq]
        idx = bisect.bisect_left(starts, earliest_start)
        if idx > 0 and earliest_start < job_tuple_seq[idx - 1][2]:
            earliest_start = job_tuple_seq[idx - 1][2]

        while idx < len(job_tuple_seq):
            _, next_start, next_end = job_tuple_seq[idx]
            if earliest_start + duration <= next_start:
                return earliest_start
            earliest_start = next_end
            idx += 1
        return earliest_start

    def get_eat_for_machine(
        self,
        stage_id: StageIdType,
        mc_id: McIdType,
        duration: int,
        release_t: int | None = None,
    ) -> tuple[int, int]:
        eat = self.get_machine_earliest_start_time(
            stage_id, mc_id, duration, release_t=release_t
        )
        idle = max(eat - self.get_machine_latest_end_time(stage_id, mc_id), 0)
        return eat, idle

    def select_machine_by_earliest_start_then_idle(
        self, stage_id: StageIdType, duration: int, release_t: int | None = None
    ) -> tuple[McIdType, int]:
        self._validate_stage(stage_id)
        if not self.machines_per_stage[stage_id]:
            raise ValueError(f"No machines available in stage {stage_id}.")
        if duration <= 0:
            raise ValueError("Duration must be greater than 0")

        best_mc = self.machines_per_stage[stage_id][0]
        best_eat, best_idle = self.get_eat_for_machine(
            stage_id, best_mc, duration, release_t
        )
        for mc_id in self.machines_per_stage[stage_id][1:]:
            eat, idle = self.get_eat_for_machine(stage_id, mc_id, duration, release_t)
            if (eat, idle) < (best_eat, best_idle):
                best_mc, best_eat, best_idle = mc_id, eat, idle
        return best_mc, best_eat

    def get_job_end_time(
        self,
        stage_id: StageIdType,
        job_id: JobIdType,
        default_if_missing: int | None = None,
    ) -> int:
        self._validate_stage(stage_id)
        if job_id not in self.__stage_2_job_2_end_time[stage_id]:
            if default_if_missing is not None:
                return default_if_missing
            raise ValueError(f"Job ID {job_id} not found in stage ID {stage_id}")
        return self.__stage_2_job_2_end_time[stage_id][job_id]

    def get_prev_stage_end_time(
        self,
        stage_id: StageIdType,
        job_id: JobIdType,
        default_if_missing: int | None = None,
    ) -> int:
        self._validate_stage(stage_id)
        prev_stage_id = self.stage_2_prev_stage[stage_id]
        if prev_stage_id is None:
            return 0
        return self.get_job_end_time(
            prev_stage_id, job_id, default_if_missing=default_if_missing
        )

    @property
    def makespan(self) -> int:
        if not self.stages:
            return 0
        last_stage = self.stages[-1]
        return max(
            (
                self.get_machine_latest_end_time(last_stage, mc_id)
                for mc_id in self.machines_per_stage[last_stage]
            ),
            default=0,
        )

    def iter_operations_on_stage(
        self, stage_id: StageIdType
    ) -> Iterator[tuple[McIdType, int, int, JobIdType]]:
        self._validate_stage(stage_id)
        for mc_id in self.machines_per_stage[stage_id]:
            for job_id, start_time, end_time in self.get_job_sequence(stage_id, mc_id):
                yield mc_id, start_time, end_time, job_id

    def _iter_operations(
        self,
    ) -> Iterator[tuple[StageIdType, McIdType, int, int, JobIdType]]:
        for stage_id in self.stages:
            for mc_id, start_time, end_time, job_id in self.iter_operations_on_stage(
                stage_id
            ):
                yield stage_id, mc_id, start_time, end_time, job_id

    def get_operation_set(self) -> set[OperationType]:
        return {
            (job_id, stage_id, mc_id)
            for stage_id, mc_id, _, _, job_id in self._iter_operations()
        }

    def get_jik_2_start_time_map(self) -> dict[OperationType, int]:
        return {
            (job_id, stage_id, mc_id): int(start_time)
            for stage_id, mc_id, start_time, _, job_id in self._iter_operations()
        }

    def get_jik_2_end_time_map(self) -> dict[OperationType, int]:
        return {
            (job_id, stage_id, mc_id): int(end_time)
            for stage_id, mc_id, _, end_time, job_id in self._iter_operations()
        }

    def get_ji_2_end_time_map(self) -> dict[tuple[JobIdType, StageIdType], int]:
        return {
            (job_id, stage_id): int(end_time)
            for stage_id, _, _, end_time, job_id in self._iter_operations()
        }

    def get_stage_2_mc_2_last_end_time_map(
        self,
    ) -> dict[StageIdType, dict[McIdType, int]]:
        return {
            stage_id: {
                mc_id: self.get_machine_latest_end_time(stage_id, mc_id)
                for mc_id in self.machines_per_stage[stage_id]
            }
            for stage_id in self.stages
        }

    def get_stage_2_mc_2_idle_time_map(
        self, include_idle_before_first_op: bool = False
    ) -> dict[StageIdType, dict[McIdType, int]]:
        stage_2_mc_2_idle_time = {
            stage_id: {mc_id: 0 for mc_id in self.machines_per_stage[stage_id]}
            for stage_id in self.stages
        }
        for stage_id in self.stages:
            for mc_id in self.machines_per_stage[stage_id]:
                former_end_time: int | None = None
                for _, start_time, end_time in self.get_job_sequence(stage_id, mc_id):
                    if former_end_time is None:
                        former_end_time = (
                            0 if include_idle_before_first_op else start_time
                        )
                    idle_time = start_time - former_end_time
                    if idle_time > 0:
                        stage_2_mc_2_idle_time[stage_id][mc_id] += idle_time
                    former_end_time = end_time
        return stage_2_mc_2_idle_time

    def sort_by_start_times(self) -> None:
        for stage_id in self.stages:
            for mc_id in self.machines_per_stage[stage_id]:
                self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id].sort(
                    key=lambda item: item[1]
                )

    def add_ops_times_2_mc(
        self,
        stage_id: StageIdType,
        mc_id: McIdType,
        job_id: JobIdType,
        start_time: int,
        end_time: int,
    ) -> None:
        self._validate_new_operation(stage_id, mc_id, job_id, start_time, end_time)

        mc_job_tuple_seq = self.get_job_sequence(stage_id, mc_id)
        start_times = [scheduled_start for _, scheduled_start, _ in mc_job_tuple_seq]
        insert_index = bisect.bisect_right(start_times, start_time)

        if insert_index > 0:
            prev_job_id, prev_start, prev_end = mc_job_tuple_seq[insert_index - 1]
            if start_time < prev_end:
                raise ValueError(
                    f"Operation overlap on {stage_id}.{mc_id}: {prev_job_id} "
                    f"[{prev_start}, {prev_end}) overlaps {job_id} [{start_time}, {end_time})"
                )
        if insert_index < len(mc_job_tuple_seq):
            next_job_id, next_start, next_end = mc_job_tuple_seq[insert_index]
            if end_time > next_start:
                raise ValueError(
                    f"Operation overlap on {stage_id}.{mc_id}: {job_id} "
                    f"[{start_time}, {end_time}) overlaps {next_job_id} [{next_start}, {next_end})"
                )

        mc_job_tuple_seq.insert(insert_index, (job_id, start_time, end_time))
        self.__stage_2_job_2_start_time[stage_id][job_id] = start_time
        self.__stage_2_job_2_end_time[stage_id][job_id] = end_time

    def add_operation_2_mc(
        self,
        stage_id: StageIdType,
        mc_id: McIdType,
        job_id: JobIdType,
        duration: int,
        release_t: int | None = None,
    ) -> None:
        self._validate_machine(stage_id, mc_id)
        self._validate_job(job_id)
        if self._stage_contains_job(stage_id, job_id):
            raise ValueError(
                f"Job ID {job_id} already scheduled in stage ID {stage_id}"
            )
        if duration <= 0:
            raise ValueError("Duration must be greater than 0")

        prev_ops_end_time = self.get_prev_stage_end_time(
            stage_id, job_id, default_if_missing=0
        )
        if release_t is None or release_t < prev_ops_end_time:
            release_t = prev_ops_end_time
        start_time = self.get_machine_earliest_start_time(
            stage_id, mc_id, duration, release_t=release_t
        )
        self.add_ops_times_2_mc(
            stage_id, mc_id, job_id, start_time, start_time + duration
        )

    def add_operation_2_stage(
        self,
        stage_id: StageIdType,
        job_id: JobIdType,
        duration: int,
        release_t: int | None = None,
    ) -> None:
        self._validate_stage(stage_id)
        self._validate_job(job_id)
        if self._stage_contains_job(stage_id, job_id):
            raise ValueError(
                f"Job ID {job_id} already scheduled in stage ID {stage_id}"
            )
        if duration <= 0:
            raise ValueError("Duration must be greater than 0")

        prev_ops_end_time = self.get_prev_stage_end_time(
            stage_id, job_id, default_if_missing=0
        )
        if release_t is None or release_t < prev_ops_end_time:
            release_t = prev_ops_end_time
        mc_id, start_time = self.select_machine_by_earliest_start_then_idle(
            stage_id, duration, release_t=release_t
        )
        self.add_ops_times_2_mc(
            stage_id, mc_id, job_id, start_time, start_time + duration
        )

    def get_job_priority_queue_for_stage_dispatch(
        self,
        stage_id: StageIdType,
        job_id_seq: Sequence[JobIdType],
        job_2_release: Mapping[JobIdType, int] | None = None,
    ) -> list[JobIdType]:
        self._validate_stage(stage_id)
        job_id_2_pos = {job_id: pos for pos, job_id in enumerate(job_id_seq)}
        return sorted(
            job_id_seq,
            key=lambda job_id: (
                max(
                    self.get_prev_stage_end_time(
                        stage_id, job_id, default_if_missing=0
                    ),
                    job_2_release.get(job_id, 0) if job_2_release else 0,
                ),
                job_id_2_pos[job_id],
            ),
        )

    def dispatch_stage_by_jobs(
        self,
        stage_id: StageIdType,
        job_id_seq: Sequence[JobIdType],
        job_2_duration: Mapping[JobIdType, int],
        job_2_release: Mapping[JobIdType, int] | None = None,
        force_job_id_seq_as_priority: bool = False,
    ) -> None:
        self._validate_stage(stage_id)
        if force_job_id_seq_as_priority:
            job_priority_queue = list(job_id_seq)
        else:
            job_priority_queue = self.get_job_priority_queue_for_stage_dispatch(
                stage_id, job_id_seq, job_2_release=job_2_release
            )
        for job_id in job_priority_queue:
            if job_id not in job_2_duration:
                raise ValueError(f"Duration for job ID {job_id} not provided")
            release_t = job_2_release.get(job_id) if job_2_release else None
            self.add_operation_2_stage(
                stage_id, job_id, job_2_duration[job_id], release_t=release_t
            )

    def _get_next_stage_start_time(
        self,
        stage_id: StageIdType,
        job_id: JobIdType,
        default_if_missing: int | None = None,
    ) -> int:
        self._validate_stage(stage_id)
        stage_idx = self.stage_2_index[stage_id]
        if stage_idx >= len(self.stages) - 1:
            if default_if_missing is not None:
                return default_if_missing
            raise ValueError(f"Stage ID {stage_id} has no next stage")

        next_stage_id = self.stages[stage_idx + 1]
        if job_id in self.__stage_2_job_2_start_time[next_stage_id]:
            return self.__stage_2_job_2_start_time[next_stage_id][job_id]
        for mc_id in self.machines_per_stage[next_stage_id]:
            for scheduled_job_id, start_time, _ in self.get_job_sequence(
                next_stage_id, mc_id
            ):
                if scheduled_job_id == job_id:
                    return start_time
        if default_if_missing is not None:
            return default_if_missing
        raise ValueError(f"Job ID {job_id} not found in next stage after {stage_id}")

    def _get_latest_feasible_slot_on_machine(
        self,
        stage_id: StageIdType,
        mc_id: McIdType,
        duration: int,
        upper_bound: int,
    ) -> tuple[int, int]:
        self._validate_machine(stage_id, mc_id)
        if duration <= 0:
            raise ValueError("Duration must be greater than 0")
        if upper_bound < duration:
            raise ValueError(
                f"No feasible slot on {stage_id}.{mc_id} before {upper_bound}"
            )

        job_tuple_seq = self.get_job_sequence(stage_id, mc_id)
        next_end = upper_bound
        for _, start_time, end_time in reversed(job_tuple_seq):
            gap_end = next_end
            gap_start = end_time
            if gap_end - gap_start >= duration:
                latest_end = gap_end
                return latest_end - duration, latest_end
            next_end = min(next_end, start_time)
        if next_end >= duration:
            return next_end - duration, next_end
        raise ValueError(f"No feasible slot on {stage_id}.{mc_id} before {upper_bound}")

    def dispatch_stage_reversed_by_jobs(
        self,
        stage_id: StageIdType,
        job_id_seq: Sequence[JobIdType],
        job_2_duration: Mapping[JobIdType, int],
        mc_2_lct: Mapping[McIdType, int],
        *,
        job_2_deadline: Mapping[JobIdType, int] | None = None,
    ) -> None:
        self._validate_stage(stage_id)
        mc_2_index = {
            mc_id: idx for idx, mc_id in enumerate(self.machines_per_stage[stage_id])
        }

        for job_id in job_id_seq:
            if job_id not in job_2_duration:
                raise ValueError(f"Duration for job ID {job_id} not provided")

            duration = job_2_duration[job_id]
            next_stage_start = (
                self._get_next_stage_start_time(
                    stage_id, job_id, default_if_missing=None
                )
                if self.stage_2_index[stage_id] < len(self.stages) - 1
                else None
            )
            job_deadline = (
                job_2_deadline[job_id]
                if job_2_deadline is not None and job_id in job_2_deadline
                else None
            )

            best_candidate: tuple[int, int, int, McIdType] | None = None
            machine_failure_info: dict[McIdType, dict[str, object]] = {}

            for mc_id, mc_lct in mc_2_lct.items():
                if mc_id not in mc_2_index:
                    raise ValueError(
                        f"Invalid machine ID: {mc_id} for stage ID: {stage_id}"
                    )

                upper_bound = mc_lct
                if job_deadline is not None:
                    upper_bound = min(upper_bound, job_deadline)
                if next_stage_start is not None:
                    upper_bound = min(upper_bound, next_stage_start)

                try:
                    start_time, end_time = self._get_latest_feasible_slot_on_machine(
                        stage_id=stage_id,
                        mc_id=mc_id,
                        duration=duration,
                        upper_bound=upper_bound,
                    )
                except ValueError:
                    machine_failure_info[mc_id] = {
                        "mc_lct": mc_lct,
                        "upper_bound": upper_bound,
                        "job_sequence": self.get_job_sequence(stage_id, mc_id),
                    }
                    continue

                candidate = (start_time, end_time, -mc_2_index[mc_id], mc_id)
                if best_candidate is None or candidate > best_candidate:
                    best_candidate = candidate

            if best_candidate is None:
                logging.error(
                    "Reverse dispatch failed for %s on %s: duration=%s "
                    "job_deadline=%s next_stage_start=%s mc_2_lct=%s "
                    "machine_failure_info=%s",
                    job_id,
                    stage_id,
                    duration,
                    job_deadline,
                    next_stage_start,
                    dict(mc_2_lct),
                    machine_failure_info,
                )
                raise ValueError(
                    f"Unable to reverse-dispatch job {job_id} on stage {stage_id}"
                )

            start_time, end_time, _neg_mc_idx, target_mc_id = best_candidate
            self.add_ops_times_2_mc(
                stage_id, target_mc_id, job_id, start_time, end_time
            )

    def dispatch_job_by_stages(
        self,
        job_id: JobIdType,
        stage_2_duration: Mapping[StageIdType, int],
        from_stage: StageIdType | None = None,
        release_t: int | None = None,
    ) -> None:
        self._validate_job(job_id)
        stage_iter = self.stages
        if from_stage is not None:
            self._validate_stage(from_stage)
            stage_iter = self.stages[self.stage_2_index[from_stage] :]
        for stage_id in stage_iter:
            if stage_id not in stage_2_duration:
                raise ValueError(f"Duration for stage ID {stage_id} not provided")
            self.add_operation_2_stage(
                stage_id, job_id, stage_2_duration[stage_id], release_t=release_t
            )

    def get_job_2_palmer_index(
        self,
        stage_2_job_2_p: Mapping[StageIdType, Mapping[JobIdType, int]],
        jobs: Iterable[JobIdType] | None = None,
        from_stage: StageIdType | None = None,
    ) -> dict[JobIdType, int]:
        if not jobs:
            job_id_list = list(self.jobs)
        else:
            for job_id in jobs:
                self._validate_job(job_id)
            job_id_list = list(jobs)

        if from_stage is None:
            stage_id_list = list(self.stages)
        else:
            self._validate_stage(from_stage)
            stage_id_list = self.stages[self.stage_2_index[from_stage] :]

        m = len(stage_id_list)
        job_2_index: dict[JobIdType, int] = {}
        for job_id in job_id_list:
            stage_2_p = {
                stage_id: stage_2_job_2_p.get(stage_id, {}).get(job_id, 0)
                for stage_id in stage_id_list
            }
            job_2_index[job_id] = sum(
                (m - 2 * (stage_idx + 1) + 1) * stage_2_p[stage_id]
                for stage_idx, stage_id in enumerate(stage_id_list)
            )
        return job_2_index

    def get_job_2_gupta_index(
        self,
        stage_2_job_2_p: Mapping[StageIdType, Mapping[JobIdType, int]],
        jobs: Iterable[JobIdType] | None = None,
        from_stage: StageIdType | None = None,
    ) -> dict[JobIdType, float]:
        if not jobs:
            job_id_list = list(self.jobs)
        else:
            for job_id in jobs:
                self._validate_job(job_id)
            job_id_list = list(jobs)

        if from_stage is None:
            stage_id_list = list(self.stages)
        else:
            self._validate_stage(from_stage)
            stage_id_list = self.stages[self.stage_2_index[from_stage] :]

        if len(stage_id_list) == 1:
            stage_id = stage_id_list[0]
            return {
                job_id: stage_2_job_2_p.get(stage_id, {}).get(job_id, 0)
                for job_id in job_id_list
            }

        job_2_index: dict[JobIdType, float] = {}
        for job_id in job_id_list:
            stage_2_p = {
                stage_id: stage_2_job_2_p.get(stage_id, {}).get(job_id, 0)
                for stage_id in stage_id_list
            }
            p_first = stage_2_p[stage_id_list[0]]
            p_last = stage_2_p[stage_id_list[-1]]
            sign = 1 if p_last <= p_first else -1
            min_sum = min(
                stage_2_p[stage_id_list[idx]] + stage_2_p[stage_id_list[idx + 1]]
                for idx in range(len(stage_id_list) - 1)
            )
            job_2_index[job_id] = float("inf") if min_sum == 0 else sign / min_sum
        return job_2_index

    def machine_centric_dispatch_4(
        self,
        stage_id: StageIdType,
        job_id_seq: Sequence[JobIdType],
        stage_2_job_2_p: Mapping[StageIdType, Mapping[JobIdType, int]],
        job_2_release: Mapping[JobIdType, int] | None = None,
        use_palmer_index: bool = False,
    ) -> None:
        self._validate_stage(stage_id)
        if not job_id_seq:
            return
        if stage_id not in stage_2_job_2_p:
            raise ValueError(f"Missing processing time map for stage_id={stage_id}")

        mc_list = list(self.machines_per_stage[stage_id])
        if not mc_list:
            return

        job_id_2_pos = {job_id: pos for pos, job_id in enumerate(job_id_seq)}
        stage_idx = self.stage_2_index[stage_id]
        remaining_stages = self.stages[stage_idx + 1 :]
        is_last_stage = stage_id == self.stages[-1]

        job_id_2_release: dict[JobIdType, int] = {}
        for job_id in job_id_seq:
            prev_end = self.get_prev_stage_end_time(
                stage_id, job_id, default_if_missing=0
            )
            ext_release = job_2_release.get(job_id, 0) if job_2_release else 0
            job_id_2_release[job_id] = max(prev_end, ext_release)

        sorted_job_ids = sorted(
            job_id_seq,
            key=lambda job_id: (job_id_2_release[job_id], job_id_2_pos[job_id]),
        )
        n = len(sorted_job_ids)
        job_indexes = list(range(n))
        idx_2_job_id = {idx: job_id for idx, job_id in enumerate(sorted_job_ids)}
        release_times = {
            idx: job_id_2_release[idx_2_job_id[idx]] for idx in job_indexes
        }

        p_j: dict[int, int] = {}
        tr_j: dict[int, int] = {}
        for idx in job_indexes:
            job_id = idx_2_job_id[idx]
            if job_id not in stage_2_job_2_p[stage_id]:
                raise ValueError(
                    f"Duration for job {job_id} at stage {stage_id} not provided"
                )
            duration = stage_2_job_2_p[stage_id][job_id]
            if duration <= 0:
                raise ValueError(
                    f"Invalid processing time p={duration} for job {job_id} at stage {stage_id}"
                )
            p_j[idx] = duration
            tr_j[idx] = sum(stage_2_job_2_p[s].get(job_id, 0) for s in remaining_stages)

        p_max = max(p_j.values()) if p_j else 0
        r_max = max(release_times.values()) if release_times else 0
        max_existing_end = 0
        for mc_id in mc_list:
            seq = self.get_job_sequence(stage_id, mc_id)
            if seq:
                max_existing_end = max(
                    max_existing_end, max(end_time for _, _, end_time in seq)
                )
        inf_end = max(r_max, max_existing_end) + n * p_max + 1

        mc_2_gaps: dict[McIdType, list[list[int]]] = {}
        mc_2_gap_idx: dict[McIdType, int] = {}
        t_k: dict[McIdType, int] = {}
        e_k: dict[McIdType, int] = {}

        initial_release = release_times[0]
        for mc_id in mc_list:
            gaps = _build_idle_gaps_from_ops(
                self.get_job_sequence(stage_id, mc_id), inf_end
            )
            gap_idx = _find_gap_index(gaps, initial_release, start_from=0)
            gaps[gap_idx][0] = max(gaps[gap_idx][0], initial_release)
            mc_2_gaps[mc_id] = gaps
            mc_2_gap_idx[mc_id] = gap_idx
            t_k[mc_id] = max(initial_release, gaps[gap_idx][0])
            e_k[mc_id] = gaps[gap_idx][1]

        tp = min(t_k.values())
        mc_2_index = {mc_id: idx for idx, mc_id in enumerate(mc_list)}

        stage_count = len(self.stages)
        tie_breaker_sign = -1 if is_last_stage else 1
        func_j: dict[int, float] = {}
        if use_palmer_index:
            job_id_2_func = self.get_job_2_palmer_index(
                stage_2_job_2_p, jobs=job_id_seq, from_stage=stage_id
            )
            func_j = {
                idx: job_id_2_func[job_id] for idx, job_id in idx_2_job_id.items()
            }
        else:
            p_multiplier = -(stage_count - stage_idx - 2) * stage_count / 80
            for idx in job_indexes:
                func_j[idx] = -(tr_j[idx] + p_multiplier * p_j[idx])

        def job_sort_key(job_idx: int) -> tuple[float, int, int]:
            return (func_j[job_idx], tie_breaker_sign * p_j[job_idx], job_idx)

        unscheduled_jobs = list(job_indexes)
        candidate_jobs: list[int] = []
        next_unscheduled_idx = 0

        while unscheduled_jobs or candidate_jobs:
            if unscheduled_jobs:
                if tp >= release_times[next_unscheduled_idx]:
                    last_available = next_unscheduled_idx
                    for job_idx in unscheduled_jobs:
                        if release_times[job_idx] <= tp:
                            last_available = job_idx
                        else:
                            break
                    candidate_jobs.extend(
                        range(next_unscheduled_idx, last_available + 1)
                    )
                    removed_count = last_available - next_unscheduled_idx + 1
                    unscheduled_jobs = unscheduled_jobs[removed_count:]
                    next_unscheduled_idx = last_available + 1
                elif not candidate_jobs:
                    tp = release_times[unscheduled_jobs[0]]
                    for mc_id in mc_list:
                        gaps = mc_2_gaps[mc_id]
                        gap_idx = _find_gap_index(
                            gaps, tp, start_from=mc_2_gap_idx[mc_id]
                        )
                        mc_2_gap_idx[mc_id] = gap_idx
                        gaps[gap_idx][0] = max(gaps[gap_idx][0], tp)
                        t_k[mc_id] = max(tp, gaps[gap_idx][0])
                        e_k[mc_id] = gaps[gap_idx][1]
                    tp = min(t_k.values())
                    continue

            ordered_machines = sorted(
                mc_list,
                key=lambda mc_id: (
                    t_k[mc_id],
                    e_k[mc_id] - t_k[mc_id],
                    mc_2_index[mc_id],
                ),
            )

            target_mc: McIdType | None = None
            feasible_candidates: list[int] = []
            for mc_id in ordered_machines:
                gap_len = e_k[mc_id] - t_k[mc_id]
                if gap_len <= 0:
                    continue
                feasible = [
                    job_idx for job_idx in candidate_jobs if p_j[job_idx] <= gap_len
                ]
                if feasible:
                    target_mc = mc_id
                    feasible_candidates = feasible
                    break

            if target_mc is None:
                future_releases = [
                    release_times[job_idx]
                    for job_idx in unscheduled_jobs
                    if release_times[job_idx] > tp
                ]
                if future_releases:
                    tp = min(future_releases)
                    for mc_id in mc_list:
                        gaps = mc_2_gaps[mc_id]
                        gap_idx = _find_gap_index(
                            gaps, tp, start_from=mc_2_gap_idx[mc_id]
                        )
                        mc_2_gap_idx[mc_id] = gap_idx
                        gaps[gap_idx][0] = max(gaps[gap_idx][0], tp)
                        t_k[mc_id] = max(tp, gaps[gap_idx][0])
                        e_k[mc_id] = gaps[gap_idx][1]
                    tp = min(t_k.values())
                    continue

                for mc_id in mc_list:
                    gaps = mc_2_gaps[mc_id]
                    gap_idx = mc_2_gap_idx[mc_id] + 1
                    if gap_idx >= len(gaps):
                        gap_idx = len(gaps) - 1
                    mc_2_gap_idx[mc_id] = gap_idx
                    t_k[mc_id] = gaps[gap_idx][0]
                    e_k[mc_id] = gaps[gap_idx][1]
                tp = min(t_k.values())
                continue

            selected_job_idx = min(feasible_candidates, key=job_sort_key)
            start_time = t_k[target_mc]
            end_time = start_time + p_j[selected_job_idx]
            self.add_ops_times_2_mc(
                stage_id,
                target_mc,
                idx_2_job_id[selected_job_idx],
                start_time,
                end_time,
            )

            candidate_jobs.remove(selected_job_idx)
            gaps = mc_2_gaps[target_mc]
            gap_idx = mc_2_gap_idx[target_mc]
            gaps[gap_idx][0] = end_time

            if gaps[gap_idx][0] >= gaps[gap_idx][1]:
                gap_idx = _find_gap_index(gaps, end_time, start_from=gap_idx + 1)
                mc_2_gap_idx[target_mc] = gap_idx
                gaps[gap_idx][0] = max(gaps[gap_idx][0], end_time)
            else:
                mc_2_gap_idx[target_mc] = gap_idx

            t_k[target_mc] = max(end_time, gaps[mc_2_gap_idx[target_mc]][0])
            e_k[target_mc] = gaps[mc_2_gap_idx[target_mc]][1]
            tp = min(t_k.values())

    def remove_operations(self, removed_ops: set[OperationType]) -> None:
        stage_2_mc_2_job_ids: dict[StageIdType, dict[McIdType, set[JobIdType]]] = {}
        for job_id, stage_id, mc_id in removed_ops:
            stage_2_mc_2_job_ids.setdefault(stage_id, {}).setdefault(mc_id, set()).add(
                job_id
            )

        for stage_id, mc_2_job_ids in stage_2_mc_2_job_ids.items():
            self._validate_stage(stage_id)
            for mc_id, job_ids in mc_2_job_ids.items():
                self._validate_machine(stage_id, mc_id)
                self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id] = [
                    job_tuple
                    for job_tuple in self.get_job_sequence(stage_id, mc_id)
                    if job_tuple[0] not in job_ids
                ]
            self._rebuild_stage_time_caches(stage_id)

    def remove_jobs(self, job_ids: set[JobIdType]) -> None:
        if not job_ids:
            return
        for stage_id in self.stages:
            for mc_id in self.machines_per_stage[stage_id]:
                self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id] = [
                    job_tuple
                    for job_tuple in self.get_job_sequence(stage_id, mc_id)
                    if job_tuple[0] not in job_ids
                ]
            self._rebuild_stage_time_caches(stage_id)

    def _is_selected_operation(
        self,
        operation_set: set[OperationType] | frozenset[OperationType],
        stage_id: StageIdType,
        mc_id: McIdType,
        job_id: JobIdType,
    ) -> bool:
        return not operation_set or (job_id, stage_id, mc_id) in operation_set

    def make_semi_active(
        self,
        stage_2_job_2_duration: Mapping[StageIdType, Mapping[JobIdType, int]],
        start_from_stage: StageIdType | None = None,
        *,
        operation_set: set[OperationType] | frozenset[OperationType] = frozenset(),
        job_2_release_map: Mapping[JobIdType, int] | None = None,
    ) -> None:
        """Shift operations left to produce a semi-active schedule in-place.

        Each selected operation is moved as early as possible without changing
        machine order, respecting both machine availability and precedence
        (previous-stage end time). Non-selected operations keep their original
        times but are validated for feasibility.

        Args:
            stage_2_job_2_duration: Processing durations indexed by stage then job.
            start_from_stage: If given, only stages from this stage onward are
                updated; stages before it are left unchanged. Defaults to None
                (all stages).
            operation_set: Set of ``(job_id, stage_id, mc_id)`` triples to
                left-shift. When empty (default), every operation is shifted.
                Operations absent from a non-empty set are treated as fixed.
            job_2_release_map: External release times applied only at the first
                processed stage. Each job's effective release time is
                ``max(prev_stage_end, job_2_release_map[job])``; missing keys
                default to 0. Ignored at subsequent stages.

        Raises:
            ValueError: If a fixed operation's original start time is earlier
                than ``max(release_t, machine_available)``, i.e. the pinned
                position is infeasible given current precedence constraints.
        """
        first_idx = 0
        if start_from_stage is not None:
            self._validate_stage(start_from_stage)
            first_idx = self.stage_2_index[start_from_stage]

        for stage_idx in range(first_idx, len(self.stages)):
            stage_id = self.stages[stage_idx]
            prev_stage_id = self.stages[stage_idx - 1] if stage_idx > 0 else None
            job_2_duration = stage_2_job_2_duration[stage_id]
            prev_stage_end_times = (
                self.__stage_2_job_2_end_time[prev_stage_id]
                if prev_stage_id is not None
                else {}
            )

            for mc_id in self.machines_per_stage[stage_id]:
                job_tuple_seq = self.get_job_sequence(stage_id, mc_id)
                if not job_tuple_seq:
                    continue

                machine_available = 0
                new_tuple_seq: list[tuple[JobIdType, int, int]] = []
                for job_id, old_start, old_end in job_tuple_seq:
                    duration = job_2_duration[job_id]
                    release_t = prev_stage_end_times.get(job_id, 0)
                    if job_2_release_map is not None and stage_idx == first_idx:
                        release_t = max(release_t, job_2_release_map.get(job_id, 0))
                    if self._is_selected_operation(
                        operation_set, stage_id, mc_id, job_id
                    ):
                        start_time = max(release_t, machine_available)
                        end_time = start_time + duration
                    else:
                        start_time = old_start
                        end_time = old_end
                        if start_time < max(release_t, machine_available):
                            raise ValueError(
                                f"Fixed operation {job_id}@{stage_id}.{mc_id} "
                                "violates precedence during make_semi_active"
                            )
                    new_tuple_seq.append((job_id, start_time, end_time))
                    machine_available = end_time
                self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id] = new_tuple_seq

            self._rebuild_stage_time_caches(stage_id)

    def make_right_justified(
        self,
        stage_2_job_2_duration: Mapping[StageIdType, Mapping[JobIdType, int]],
        *,
        operation_set: set[OperationType] | frozenset[OperationType] = frozenset(),
    ) -> None:
        original_makespan = self.makespan
        next_stage_job_2_start_time: dict[JobIdType, int] = {}

        for stage_idx in range(len(self.stages) - 1, -1, -1):
            stage_id = self.stages[stage_idx]
            job_2_duration = stage_2_job_2_duration[stage_id]

            for mc_id in self.machines_per_stage[stage_id]:
                job_tuple_seq = self.get_job_sequence(stage_id, mc_id)
                if not job_tuple_seq:
                    continue

                machine_next_start = original_makespan
                new_tuple_seq_rev: list[tuple[JobIdType, int, int]] = []
                for job_id, old_start, old_end in reversed(job_tuple_seq):
                    duration = job_2_duration[job_id]
                    next_stage_start = next_stage_job_2_start_time.get(
                        job_id, original_makespan
                    )
                    if self._is_selected_operation(
                        operation_set, stage_id, mc_id, job_id
                    ):
                        end_time = min(next_stage_start, machine_next_start)
                        start_time = end_time - duration
                    else:
                        start_time = old_start
                        end_time = old_end
                        if end_time > min(next_stage_start, machine_next_start):
                            raise ValueError(
                                f"Fixed operation {job_id}@{stage_id}.{mc_id} "
                                "violates precedence during make_right_justified"
                            )
                    new_tuple_seq_rev.append((job_id, start_time, end_time))
                    machine_next_start = start_time

                self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id] = list(
                    reversed(new_tuple_seq_rev)
                )

            self._rebuild_stage_time_caches(stage_id)
            next_stage_job_2_start_time = dict(
                self.__stage_2_job_2_start_time[stage_id]
            )

    def swap_two_operations_within_stage(
        self,
        stage_id: StageIdType,
        job_id_1: JobIdType,
        job_id_2: JobIdType,
        stage_2_job_2_duration: Mapping[StageIdType, Mapping[JobIdType, int]],
        *,
        do_make_semi_active: bool = True,
    ) -> None:
        self._validate_stage(stage_id)
        if job_id_1 == job_id_2:
            raise ValueError(
                f"Cannot swap a job with itself: job_id_1 == job_id_2 == {job_id_1}"
            )

        mc1: McIdType | None = None
        idx1 = -1
        mc2: McIdType | None = None
        idx2 = -1
        for mc_id in self.machines_per_stage[stage_id]:
            for idx, (job_id, _, _) in enumerate(
                self.get_job_sequence(stage_id, mc_id)
            ):
                if job_id == job_id_1 and mc1 is None:
                    mc1, idx1 = mc_id, idx
                elif job_id == job_id_2 and mc2 is None:
                    mc2, idx2 = mc_id, idx
            if mc1 is not None and mc2 is not None:
                break

        if mc1 is None:
            raise ValueError(f"Job ID {job_id_1} not found in stage {stage_id}")
        if mc2 is None:
            raise ValueError(f"Job ID {job_id_2} not found in stage {stage_id}")

        seq1 = self.__stage_2_mc_2_job_tuple_seq[stage_id][mc1]
        seq2 = self.__stage_2_mc_2_job_tuple_seq[stage_id][mc2]
        _, start_1, end_1 = seq1[idx1]
        _, start_2, end_2 = seq2[idx2]
        seq1[idx1] = (job_id_2, start_1, end_1)
        seq2[idx2] = (job_id_1, start_2, end_2)

        if do_make_semi_active:
            self.make_semi_active(stage_2_job_2_duration, start_from_stage=stage_id)
        else:
            self._invalidate_stage_jobs(stage_id, {job_id_1, job_id_2})

    def collect_stage_machine_suffix_job_ids(
        self,
        stage_id: StageIdType,
        machine_id: McIdType,
        start_job_id: JobIdType,
    ) -> list[JobIdType]:
        self._validate_machine(stage_id, machine_id)
        job_tuple_seq = self.get_job_sequence(stage_id, machine_id)
        start_idx = next(
            (
                idx
                for idx, (job_id, _, _) in enumerate(job_tuple_seq)
                if job_id == start_job_id
            ),
            None,
        )
        if start_idx is None:
            raise ValueError(
                f"Job ID {start_job_id} not found on {stage_id}.{machine_id}"
            )
        return [job_id for job_id, _, _ in job_tuple_seq[start_idx:]]

    def swap_stage_machine_operation_sets(
        self,
        stage_id: StageIdType,
        from_machine_id: McIdType,
        from_job_ids: Sequence[JobIdType],
        to_machine_id: McIdType,
        to_job_ids: Sequence[JobIdType],
        stage_2_job_2_duration: Mapping[StageIdType, Mapping[JobIdType, int]],
        *,
        do_make_semi_active: bool = False,
    ) -> None:
        self._validate_machine(stage_id, from_machine_id)
        self._validate_machine(stage_id, to_machine_id)
        if from_machine_id == to_machine_id:
            if list(from_job_ids) == list(to_job_ids):
                return
            raise ValueError("swap_stage_machine_operation_sets requires two machines")

        from_job_ids = list(from_job_ids)
        to_job_ids = list(to_job_ids)
        if len(set(from_job_ids)) != len(from_job_ids):
            raise ValueError("Duplicate job IDs in from_job_ids")
        if len(set(to_job_ids)) != len(to_job_ids):
            raise ValueError("Duplicate job IDs in to_job_ids")

        overlap_job_ids = set(from_job_ids) & set(to_job_ids)
        if overlap_job_ids:
            overlap_job_id = next(iter(sorted(overlap_job_ids)))
            raise ValueError(
                f"Job ID {overlap_job_id} cannot be swapped from both machines"
            )

        from_job_id_set = set(from_job_ids)
        to_job_id_set = set(to_job_ids)
        from_seq = self.get_job_sequence(stage_id, from_machine_id)
        to_seq = self.get_job_sequence(stage_id, to_machine_id)

        existing_from_job_ids = [job_id for job_id, _, _ in from_seq]
        existing_to_job_ids = [job_id for job_id, _, _ in to_seq]
        for job_id in from_job_ids:
            if job_id not in existing_from_job_ids:
                raise ValueError(
                    f"Job ID {job_id} not found on {stage_id}.{from_machine_id}"
                )
        for job_id in to_job_ids:
            if job_id not in existing_to_job_ids:
                raise ValueError(
                    f"Job ID {job_id} not found on {stage_id}.{to_machine_id}"
                )

        from_selected = [
            job_tuple for job_tuple in from_seq if job_tuple[0] in from_job_id_set
        ]
        to_selected = [
            job_tuple for job_tuple in to_seq if job_tuple[0] in to_job_id_set
        ]
        from_remaining = [
            job_tuple for job_tuple in from_seq if job_tuple[0] not in from_job_id_set
        ]
        to_remaining = [
            job_tuple for job_tuple in to_seq if job_tuple[0] not in to_job_id_set
        ]
        from_insert_idx = next(
            (
                idx
                for idx, (job_id, _, _) in enumerate(from_seq)
                if job_id in from_job_id_set
            ),
            len(from_remaining),
        )
        to_insert_idx = next(
            (
                idx
                for idx, (job_id, _, _) in enumerate(to_seq)
                if job_id in to_job_id_set
            ),
            len(to_remaining),
        )

        self.__stage_2_mc_2_job_tuple_seq[stage_id][from_machine_id] = (
            from_remaining[:from_insert_idx]
            + to_selected
            + from_remaining[from_insert_idx:]
        )
        self.__stage_2_mc_2_job_tuple_seq[stage_id][to_machine_id] = (
            to_remaining[:to_insert_idx] + from_selected + to_remaining[to_insert_idx:]
        )

        affected_job_ids = set(from_job_ids) | set(to_job_ids)
        if do_make_semi_active:
            self.make_semi_active(stage_2_job_2_duration, start_from_stage=stage_id)
        else:
            self._invalidate_stage_jobs(stage_id, affected_job_ids)

    def calculate_slack(
        self, stage_2_job_2_duration: Mapping[StageIdType, Mapping[JobIdType, int]]
    ) -> dict[StageIdType, dict[JobIdType, int]]:
        earliest_start: dict[StageIdType, dict[JobIdType, int]] = {
            stage_id: {} for stage_id in self.stages
        }
        earliest_finish: dict[StageIdType, dict[JobIdType, int]] = {
            stage_id: {} for stage_id in self.stages
        }

        for stage_idx, stage_id in enumerate(self.stages):
            job_2_duration = stage_2_job_2_duration[stage_id]
            prev_stage_id = self.stages[stage_idx - 1] if stage_idx > 0 else None
            for mc_id in self.machines_per_stage[stage_id]:
                prev_end_on_mc = 0
                for job_id, _, _ in self.get_job_sequence(stage_id, mc_id):
                    prev_stage_end = (
                        self.__stage_2_job_2_end_time[prev_stage_id].get(job_id, 0)
                        if prev_stage_id is not None
                        else 0
                    )
                    start_time = max(prev_stage_end, prev_end_on_mc)
                    end_time = start_time + job_2_duration[job_id]
                    earliest_start[stage_id][job_id] = start_time
                    earliest_finish[stage_id][job_id] = end_time
                    prev_end_on_mc = end_time

        all_finish_times = [
            end_time
            for stage_id in self.stages
            for end_time in earliest_finish[stage_id].values()
        ]
        if not all_finish_times:
            return {}

        makespan = max(all_finish_times)
        if makespan == 0:
            return {
                stage_id: {job_id: 0 for job_id in earliest_start[stage_id]}
                for stage_id in self.stages
            }

        latest_finish: dict[StageIdType, dict[JobIdType, int]] = {
            stage_id: {} for stage_id in self.stages
        }
        latest_start: dict[StageIdType, dict[JobIdType, int]] = {
            stage_id: {} for stage_id in self.stages
        }

        for stage_idx in range(len(self.stages) - 1, -1, -1):
            stage_id = self.stages[stage_idx]
            job_2_duration = stage_2_job_2_duration[stage_id]
            next_stage_id = (
                self.stages[stage_idx + 1] if stage_idx < len(self.stages) - 1 else None
            )

            for mc_id in self.machines_per_stage[stage_id]:
                next_ls_on_mc = makespan
                for job_id, _, _ in reversed(self.get_job_sequence(stage_id, mc_id)):
                    lft_1 = (
                        latest_start[next_stage_id].get(job_id, makespan)
                        if next_stage_id is not None
                        else makespan
                    )
                    lft_2 = next_ls_on_mc
                    latest_finish_time = min(lft_1, lft_2)
                    latest_start_time = latest_finish_time - job_2_duration[job_id]
                    latest_finish[stage_id][job_id] = latest_finish_time
                    latest_start[stage_id][job_id] = latest_start_time
                    next_ls_on_mc = latest_start_time

        return {
            stage_id: {
                job_id: latest_start[stage_id][job_id]
                - earliest_start[stage_id][job_id]
                for job_id in earliest_start[stage_id]
            }
            for stage_id in self.stages
        }

    def find_critical_blocks(
        self,
        stage_2_job_2_duration: Mapping[StageIdType, Mapping[JobIdType, int]],
        tolerance: float = 1e-9,
        include_singletons: bool = False,
    ) -> list[list[OperationType]]:
        slack = self.calculate_slack(stage_2_job_2_duration)
        if not slack:
            return []

        blocks: list[list[OperationType]] = []
        for stage_id in self.stages:
            critical_jobs = {
                job_id
                for job_id, job_slack in slack.get(stage_id, {}).items()
                if abs(job_slack) < tolerance
            }
            stage_start_time = self.__stage_2_job_2_start_time[stage_id]
            stage_end_time = self.__stage_2_job_2_end_time[stage_id]

            for mc_id in self.machines_per_stage[stage_id]:
                critical_job_seq = [
                    job_id
                    for job_id, _, _ in self.get_job_sequence(stage_id, mc_id)
                    if job_id in critical_jobs
                ]
                if not critical_job_seq:
                    continue

                current_block: list[OperationType] = []
                for idx, job_id in enumerate(critical_job_seq):
                    current_block.append((job_id, stage_id, mc_id))
                    if idx < len(critical_job_seq) - 1:
                        next_job_id = critical_job_seq[idx + 1]
                        current_end_time = stage_end_time[job_id]
                        next_start_time = stage_start_time[next_job_id]
                        if next_start_time > current_end_time:
                            if include_singletons or len(current_block) >= 2:
                                blocks.append(current_block)
                            current_block = []
                    elif include_singletons or len(current_block) >= 2:
                        blocks.append(current_block)
        return blocks

    def right_shift(self, shift_amount: int) -> None:
        for stage_id in self.stages:
            for mc_id in self.machines_per_stage[stage_id]:
                self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id] = [
                    (job_id, start_time + shift_amount, end_time + shift_amount)
                    for job_id, start_time, end_time in self.get_job_sequence(
                        stage_id, mc_id
                    )
                ]
            self._rebuild_stage_time_caches(stage_id)

    def insert_idle_time(
        self,
        due_window_map: Mapping[JobIdType, tuple[int, int]],
        ewt_map: Mapping[JobIdType, int],
        twt_map: Mapping[JobIdType, int],
    ) -> None:
        """Insert idle time on the last stage to minimise earliness-tardiness.

        Implements the pseudocode from the paper exactly:
          j starts at the last job and counts down to 0.
          j is only decremented (j -= 1) when the current block is NOT
          shifted. When a shift is applied, j stays fixed so the same
          starting position is re-evaluated with updated completion times
          (sets S_E/S_D/S_T may have changed, or S_M grew by merging with
          the next block when delta == delta2).
        """
        last_stage_id = self.stages[-1]
        INF = 10**9

        for mc_id in self.machines_per_stage[last_stage_id]:
            seq = self.__stage_2_mc_2_job_tuple_seq[last_stage_id][mc_id]
            if not seq:
                continue

            job_ids = [j for j, _, _ in seq]
            starts = [s for _, s, _ in seq]
            ends = [e for _, _, e in seq]
            n = len(seq)

            j = n - 1
            while j >= 0:
                block_end = j
                while block_end < n - 1 and starts[block_end + 1] == ends[block_end]:
                    block_end += 1

                delta2 = (
                    starts[block_end + 1] - ends[block_end]
                    if block_end < n - 1
                    else INF
                )

                s_e, s_t, s_d = [], [], []
                for i in range(j, block_end + 1):
                    d_lo, d_hi = due_window_map[job_ids[i]]
                    c = ends[i]
                    if c < d_lo:
                        s_e.append(i)
                    elif c >= d_hi:
                        s_t.append(i)
                    else:
                        s_d.append(i)

                sum_e = sum(ewt_map.get(job_ids[i], 1) for i in s_e)
                sum_t = sum(twt_map.get(job_ids[i], 1) for i in s_t)

                if sum_e > sum_t:
                    delta1_vals = [due_window_map[job_ids[i]][0] - ends[i] for i in s_e]
                    delta1_vals += [
                        due_window_map[job_ids[i]][1] - ends[i] for i in s_d
                    ]
                    delta1 = min(delta1_vals) if delta1_vals else INF
                    delta = min(delta1, delta2)
                    for i in range(j, block_end + 1):
                        starts[i] += delta
                        ends[i] += delta
                    # j stays fixed — re-evaluate same position with updated times
                else:
                    j -= 1

            self.__stage_2_mc_2_job_tuple_seq[last_stage_id][mc_id] = list(
                zip(job_ids, starts, ends)
            )

        self._rebuild_stage_time_caches(last_stage_id)


def validate_schedule(
    sched: FFcSchedule,
    stage_2_job_2_duration: Mapping[StageIdType, Mapping[JobIdType, int]],
) -> None:
    start_map = sched.get_jik_2_start_time_map()
    end_map = sched.get_jik_2_end_time_map()
    validate_duration(start_map, end_map, stage_2_job_2_duration)
    validate_precedence(start_map, end_map, list(sched.stages))
    validate_no_overlap(
        start_map, end_map, list(sched.stages), sched.machines_per_stage
    )


def validate_duration(
    start_map: Mapping[OperationType, int],
    end_map: Mapping[OperationType, int],
    stage_2_job_2_duration: Mapping[StageIdType, Mapping[JobIdType, int]],
) -> None:
    for (job_id, stage_id, mc_id), start_time in start_map.items():
        end_time = end_map[(job_id, stage_id, mc_id)]
        expected = stage_2_job_2_duration[stage_id][job_id]
        if end_time - start_time != expected:
            raise ValueError(
                f"Duration mismatch: {job_id}@{stage_id}.{mc_id}: "
                f"end-start={end_time - start_time} != duration={expected}"
            )


def validate_precedence(
    start_map: Mapping[OperationType, int],
    end_map: Mapping[OperationType, int],
    stages: Sequence[StageIdType],
) -> None:
    for idx in range(1, len(stages)):
        prev_stage = stages[idx - 1]
        cur_stage = stages[idx]
        prev_ends: dict[JobIdType, int] = {}
        for (job_id, stage_id, _), end_time in end_map.items():
            if stage_id == prev_stage:
                prev_ends[job_id] = end_time
        for (job_id, stage_id, _), start_time in start_map.items():
            if (
                stage_id == cur_stage
                and job_id in prev_ends
                and start_time < prev_ends[job_id]
            ):
                raise ValueError(
                    f"Precedence violated: {job_id}@{cur_stage} start={start_time} "
                    f"< {job_id}@{prev_stage} end={prev_ends[job_id]}"
                )


def validate_no_overlap(
    start_map: Mapping[OperationType, int],
    end_map: Mapping[OperationType, int],
    stages: Sequence[StageIdType],
    machines_per_stage: Mapping[StageIdType, Sequence[McIdType]],
) -> None:
    for stage_id in stages:
        for mc_id in machines_per_stage[stage_id]:
            ops = sorted(
                (
                    start_time,
                    end_map[(job_id, stage_id_, mc_id_)],
                )
                for (job_id, stage_id_, mc_id_), start_time in start_map.items()
                if stage_id_ == stage_id and mc_id_ == mc_id
            )
            for idx in range(len(ops) - 1):
                if ops[idx][1] > ops[idx + 1][0]:
                    raise ValueError(
                        f"Overlap on {stage_id}.{mc_id}: {ops[idx]} vs {ops[idx + 1]}"
                    )


def _build_idle_gaps_from_ops(
    ops: list[tuple[JobIdType, int, int]],
    inf_end: int,
) -> list[list[int]]:
    if not ops:
        return [[0, inf_end]]

    ops_sorted = sorted(ops, key=lambda item: item[1])
    gaps: list[list[int]] = []

    first_start = ops_sorted[0][1]
    if first_start > 0:
        gaps.append([0, first_start])

    for (_, _, prev_end), (_, next_start, _) in zip(ops_sorted, ops_sorted[1:]):
        if next_start > prev_end:
            gaps.append([prev_end, next_start])

    last_end = ops_sorted[-1][2]
    if inf_end > last_end:
        gaps.append([last_end, inf_end])

    if not gaps:
        gaps = [[inf_end, inf_end]]
    return gaps


def _find_gap_index(gaps: list[list[int]], t: int, start_from: int = 0) -> int:
    idx = start_from
    while idx < len(gaps) and gaps[idx][1] <= t:
        idx += 1
    if idx >= len(gaps):
        return len(gaps) - 1
    return idx


def get_midpoint_sequence(schedule: FFcSchedule) -> list[JobIdType]:
    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()
    jobs = schedule.jobs
    idx_map = {job_id: idx for idx, job_id in enumerate(jobs)}
    first_stage = schedule.stages[0]
    last_stage = schedule.stages[-1]

    seq_info: list[tuple[float, int, int, JobIdType]] = []
    for job_id in jobs:
        start_first = next(
            start_time
            for (scheduled_job_id, stage_id, _), start_time in start_map.items()
            if scheduled_job_id == job_id and stage_id == first_stage
        )
        end_last = next(
            end_time
            for (scheduled_job_id, stage_id, _), end_time in end_map.items()
            if scheduled_job_id == job_id and stage_id == last_stage
        )
        midpoint = (start_first + end_last) / 2
        seq_info.append((midpoint, start_first, idx_map[job_id], job_id))

    seq_info.sort(key=lambda item: (item[0], item[1], item[2]))
    return [job_id for _, _, _, job_id in seq_info]


def get_bottleneck_stage_job_sequence(schedule: FFcSchedule) -> list[JobIdType]:
    stage_2_mc_2_idle_time_map = schedule.get_stage_2_mc_2_idle_time_map()
    stage_2_total_idle_time = {
        stage_id: sum(mc_2_idle_time.values())
        for stage_id, mc_2_idle_time in stage_2_mc_2_idle_time_map.items()
    }
    bottleneck_stage = min(stage_2_total_idle_time, key=stage_2_total_idle_time.get)

    start_map = schedule.get_jik_2_start_time_map()
    end_map = schedule.get_jik_2_end_time_map()
    jobs = schedule.jobs
    idx_map = {job_id: idx for idx, job_id in enumerate(jobs)}

    seq_info: list[tuple[int, float, int, JobIdType]] = []
    for job_id in jobs:
        start_time = next(
            start_t
            for (scheduled_job_id, stage_id, _), start_t in start_map.items()
            if scheduled_job_id == job_id and stage_id == bottleneck_stage
        )
        end_time = next(
            end_t
            for (scheduled_job_id, stage_id, _), end_t in end_map.items()
            if scheduled_job_id == job_id and stage_id == bottleneck_stage
        )
        midpoint = (start_time + end_time) / 2
        seq_info.append((start_time, midpoint, idx_map[job_id], job_id))

    seq_info.sort(key=lambda item: (item[0], item[1], item[2]))
    return [job_id for _, _, _, job_id in seq_info]


def get_first_stage_start_sequence(schedule: FFcSchedule) -> list[JobIdType]:
    start_map = schedule.get_jik_2_start_time_map()
    jobs = schedule.jobs
    idx_map = {job_id: idx for idx, job_id in enumerate(jobs)}
    first_stage = schedule.stages[0]

    seq_info: list[tuple[int, int, JobIdType]] = []
    for job_id in jobs:
        start_time = next(
            start_t
            for (scheduled_job_id, stage_id, _), start_t in start_map.items()
            if scheduled_job_id == job_id and stage_id == first_stage
        )
        seq_info.append((start_time, idx_map[job_id], job_id))

    seq_info.sort(key=lambda item: (item[0], item[1]))
    return [job_id for _, _, job_id in seq_info]
