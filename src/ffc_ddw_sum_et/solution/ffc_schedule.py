from __future__ import annotations

import bisect
from typing import Mapping, Sequence

JobIdType = str
StageIdType = str
McIdType = str
OperationType = tuple[JobIdType, StageIdType, McIdType]


class FFcSchedule:
    """Flexible flow shop schedule representation."""
    # Parameters

    jobs: Sequence[JobIdType]
    """ID of all jobs (scheduled or not)"""

    stages: Sequence[StageIdType]
    """ID of stages"""

    machines_per_stage: Mapping[StageIdType, Sequence[McIdType]]
    """map(stage ID -> ID of machines)"""

    # Variables

    __stage_2_mc_2_job_tuple_seq: dict[
        StageIdType, dict[McIdType, list[tuple[JobIdType, int, int]]]
    ]
    """map(stage ID -> map(machine ID -> sequence of (job ID, start time, end time)))"""

    # Derived variables

    __stage_2_job_2_start_time: dict[StageIdType, dict[JobIdType, int]]
    """map(stage ID -> map(job ID -> start time))"""

    __stage_2_job_2_end_time: dict[StageIdType, dict[JobIdType, int]]
    """map(stage ID -> map(job ID -> end time))"""

    def __init__(
        self,
        jobs: Sequence[JobIdType],
        stages: Sequence[StageIdType],
        machines_per_stage: Mapping[StageIdType, Sequence[McIdType]],
    ):
        self.jobs = jobs
        self.stages = stages
        self.machines_per_stage = machines_per_stage
        self._initialize_variables()

    def _initialize_variables(self):
        self.__stage_2_mc_2_job_tuple_seq = {
            stage: {mc: [] for mc in self.machines_per_stage[stage]}
            for stage in self.stages
        }
        self.__stage_2_job_2_start_time = {stage: {} for stage in self.stages}
        self.__stage_2_job_2_end_time = {stage: {} for stage in self.stages}

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
                    (job, start_time, end_time)
                    for job, start_time, end_time in self.__stage_2_mc_2_job_tuple_seq[
                        stage
                    ][mc]
                    if job_subset is None or job in job_subset
                ]
            new_schedule.__stage_2_job_2_start_time[stage] = {
                job: start_time
                for job, start_time in self.__stage_2_job_2_start_time[stage].items()
                if job_subset is None or job in job_subset
            }
            new_schedule.__stage_2_job_2_end_time[stage] = {
                job: end_time
                for job, end_time in self.__stage_2_job_2_end_time[stage].items()
                if job_subset is None or job in job_subset
            }
        return new_schedule

    def validate(
        self,
        stage_id: StageIdType | None = None,
        mc_id: McIdType | None = None,
        job_id: JobIdType | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> None:
        if stage_id is not None:
            if stage_id not in self.stages:
                raise ValueError(f"Invalid stage ID: {stage_id}")
            if mc_id not in self.machines_per_stage[stage_id]:
                raise ValueError(f"Invalid machine ID {mc_id} for stage ID {stage_id}")
        if job_id is not None:
            if job_id not in self.jobs:
                raise ValueError(f"Invalid job ID: {job_id}")
            if stage_id is not None:
                if job_id in self.__stage_2_job_2_end_time[stage_id]:
                    raise ValueError(
                        f"Job ID {job_id} already scheduled in stage ID {stage_id}"
                    )
        if start_time is not None:
            if start_time < 0:
                raise ValueError(f"Start time cannot be negative: {start_time}")
        if end_time is not None:
            if end_time < 0:
                raise ValueError(f"End time cannot be negative: {end_time}")
        if start_time is not None and end_time is not None:
            if end_time < start_time:
                raise ValueError(
                    f"End time {end_time} cannot be earlier than start time {start_time}"
                )

    # Getters

    def get_job_sequence(
        self, stage_id: StageIdType, mc_id: McIdType
    ) -> list[tuple[JobIdType, int, int]]:
        self.validate(stage_id=stage_id, mc_id=mc_id)
        return self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id]

    # Setters

    def add_ops_times_2_mc(
        self,
        stage_id: StageIdType,
        mc_id: McIdType,
        job_id: JobIdType,
        start_time: int,
        end_time: int,
    ):
        self.validate(
            stage_id=stage_id,
            mc_id=mc_id,
            job_id=job_id,
            start_time=start_time,
            end_time=end_time,
        )

        mc_job_tuple_seq = self.get_job_sequence(stage_id, mc_id)
        start_times = [start for _, start, _ in mc_job_tuple_seq]
        insert_index = bisect.bisect_right(start_times, start_time)

        # Keep the per-machine timeline consistent (sorted, non-overlapping) with O(1) neighbor checks.
        if insert_index > 0:
            prev_job_id, prev_start, prev_end = mc_job_tuple_seq[insert_index - 1]
            if start_time < prev_end:
                raise ValueError(
                    f"Overlapping operations: new (job ID {job_id}, start time "
                    f"{start_time}, end time {end_time}) with previous (job ID "
                    f"{prev_job_id}, start time {prev_start}, end time {prev_end}) "
                    f"on machine ID {mc_id} in stage ID {stage_id}"
                )
        if insert_index < len(mc_job_tuple_seq):
            next_job_id, next_start, next_end = mc_job_tuple_seq[insert_index]
            if end_time > next_start:
                raise ValueError(
                    f"Overlapping operations: new (job ID {job_id}, start time "
                    f"{start_time}, end time {end_time}) with next (job ID "
                    f"{next_job_id}, start time {next_start}, end time {next_end}) "
                    f"on machine ID {mc_id} in stage ID {stage_id}"
                )

        mc_job_tuple_seq.insert(insert_index, (job_id, start_time, end_time))
        self.__stage_2_job_2_start_time[stage_id][job_id] = start_time
        self.__stage_2_job_2_end_time[stage_id][job_id] = end_time
