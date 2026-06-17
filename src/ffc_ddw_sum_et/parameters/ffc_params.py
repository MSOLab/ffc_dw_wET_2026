from __future__ import annotations

from functools import cached_property
from typing import Self, TextIO

from .base.job_stage_p import JobStageProcessingTimeManager


class FFcParameters:
    """Parameters for the flexible flow shop scheduling problem."""

    name: str
    """Name of the problem instance."""

    __job_id_list: list[str]
    """A list of job IDs."""
    __stage_id_list: list[str]
    """A list of stage IDs."""
    __stage_2_machines_map: dict[str, list[str]]
    """Mapping from stage IDs to lists of machine IDs at that stage."""
    p_manager: JobStageProcessingTimeManager[int]
    """Manager for processing times of jobs at each stage."""

    def __init__(
        self,
        name: str,
        job_id_list: list[str],
        stage_id_list: list[str],
        stage_2_machines_map: dict[str, list[str]],
        p_manager: JobStageProcessingTimeManager,
    ):
        self.name = name
        self._job_id_list = job_id_list
        self._stage_id_list = stage_id_list
        self._stage_2_machines_map = stage_2_machines_map  # e.g., [2, 3, 2]
        self.p_manager = p_manager

    @staticmethod
    def _validate_machine_count_per_stage(
        stage_count: int, machine_count_per_stage: list[int]
    ):
        if len(machine_count_per_stage) != stage_count:
            raise ValueError(
                f"Stage count mismatch; stage_count={stage_count};"
                f" by machine_count_per_stage={len(machine_count_per_stage)}"
            )

    @staticmethod
    def _validate_processing_times(
        job_count: int,
        stage_count: int,
        processing_times: JobStageProcessingTimeManager,
    ):
        if processing_times.df.isnull().values.any():
            raise ValueError("Null value exists in the processing time data.")
        if processing_times.row_count() != job_count:
            raise ValueError(
                f"Job count mismatch; expected {job_count},"
                f" got {processing_times.row_count()}."
            )
        if processing_times.col_count() != stage_count:
            raise ValueError(
                f"Stage count mismatch; expected {stage_count},"
                f" got {processing_times.col_count()}."
            )

    @classmethod
    def from_pra_data(cls, name: str, stream: TextIO) -> FFcParameters:
        """
        Parse hybrid flowshop parameters from a text stream in PRA-style format.

        Expected format:
            <job_count>
            <stage_count>
            <machine_count_per_stage>  # space-separated list
            <processing_time_row_0>
            <processing_time_row_1>
            ...
            <processing_time_row_n-1>

        Args:
            name (str): Name of the problem instance.
            stream (TextIO): Input stream (e.g., open file or StringIO) containing instance data.

        Returns:
            FFcParameters: Parsed parameters instance.
        """  # noqa: E501
        from ffc_ddw_sum_et.io import TextDataParser

        job_count = TextDataParser.strip_a_typed_value(stream, int)
        stage_count = TextDataParser.strip_a_typed_value(stream, int)
        machine_count_per_stage = TextDataParser.strip_a_typed_list(stream, int)

        cls._validate_machine_count_per_stage(stage_count, machine_count_per_stage)

        # Generate a list of job IDs with zero-padded numbers.
        num_digits = len(str(job_count - 1))
        job_id_list = [f"j{str(j).zfill(num_digits)}" for j in range(job_count)]

        # Generate a list of stage IDs with zero-padded numbers.
        num_digits = len(str(stage_count - 1))
        stage_id_list = [f"i{str(s).zfill(num_digits)}" for s in range(stage_count)]

        # Generate a mapping from stage IDs to lists of machine IDs.
        stage_2_machines_map: dict[str, list[str]] = {}
        for stage_idx, stage_id in enumerate(stage_id_list):
            num_digits = len(str(machine_count_per_stage[stage_idx] - 1))
            machine_ids = [
                f"{stage_id}_{str(m).zfill(num_digits)}"
                for m in range(machine_count_per_stage[stage_idx])
            ]
            stage_2_machines_map[stage_id] = machine_ids

        processing_times = JobStageProcessingTimeManager.from_text_stream(
            stream, job_count, dtype=int
        )

        cls._validate_processing_times(job_count, stage_count, processing_times)

        return cls(
            name, job_id_list, stage_id_list, stage_2_machines_map, processing_times
        )

    # Getters

    @property
    def job_id_list(self) -> list[str]:
        """Get list of job IDs."""
        return self._job_id_list.copy()

    @property
    def stage_id_list(self) -> list[str]:
        """Get list of stage IDs."""
        return self._stage_id_list.copy()

    @property
    def stage_2_machines_map(self) -> dict[str, list[str]]:
        """Get mapping from stage IDs to lists of machine IDs."""
        return self._stage_2_machines_map.copy()

    @cached_property
    def job_count(self) -> int:
        return len(self._job_id_list)

    @cached_property
    def stage_count(self) -> int:
        return len(self._stage_id_list)

    @cached_property
    def machine_count_per_stage(self) -> list[int]:
        """List of the number of parallel machines at each stage."""
        return [
            len(self._stage_2_machines_map[stage_id])
            for stage_id in self._stage_id_list
        ]

    @cached_property
    def last_stage_mc_count(self) -> int:
        """Number of parallel machines at the last stage."""
        return len(self._stage_2_machines_map[self._stage_id_list[-1]])

    @cached_property
    def operation_count(self) -> int:
        """Total number of operations (jobs x stages)."""
        return self.job_count * self.stage_count

    @cached_property
    def job_2_stage_2_p_map(self) -> dict[str, dict[str, int]]:
        return self.p_manager.job_2_stage_2_value_map(
            self.job_id_list, self.stage_id_list
        )

    @cached_property
    def stage_2_job_2_p_map(self) -> dict[str, dict[str, int]]:
        return self.p_manager.stage_2_job_2_value_map(
            self.stage_id_list, self.job_id_list
        )

    def get_job_2_p_map_for_stage(self, stage_id: str) -> dict[str, int]:
        """Get a mapping from job IDs to processing times for a given stage."""
        if stage_id not in self.stage_id_list:
            raise ValueError(f"Invalid stage_id: {stage_id}")
        return self.stage_2_job_2_p_map[stage_id]

    def get_job_2_p_sum_except_last_stage(self) -> dict[str, int]:
        """Get a mapping from job IDs to the sum of processing times across all stages except the last."""
        job_2_p_sum = {job_id: 0 for job_id in self.job_id_list}
        for stage_id in self.stage_id_list[:-1]:  # Exclude the last stage
            for job_id in self.job_id_list:
                job_2_p_sum[job_id] += self.stage_2_job_2_p_map[stage_id][job_id]
        return job_2_p_sum

    def get_job_2_p_sum_before_stage(self, stage_id: str) -> dict[str, int]:
        """Get a mapping from job IDs to the sum of processing times across all stages strictly before the given stage."""
        if stage_id not in self.stage_id_list:
            raise ValueError(f"Invalid stage_id: {stage_id}")
        stage_idx = self.stage_id_list.index(stage_id)
        job_2_p_sum = {job_id: 0 for job_id in self.job_id_list}
        for before_stage_id in self.stage_id_list[:stage_idx]:  # Stages before
            for job_id in self.job_id_list:
                job_2_p_sum[job_id] += self.stage_2_job_2_p_map[before_stage_id][job_id]
        return job_2_p_sum

    def get_job_2_p_sum_after_stage(self, stage_id: str) -> dict[str, int]:
        """Get a mapping from job IDs to the sum of processing times across all stages strictly after the given stage."""
        if stage_id not in self.stage_id_list:
            raise ValueError(f"Invalid stage_id: {stage_id}")
        stage_idx = self.stage_id_list.index(stage_id)
        job_2_p_sum = {job_id: 0 for job_id in self.job_id_list}
        for after_stage_id in self.stage_id_list[stage_idx + 1 :]:  # Stages after
            for job_id in self.job_id_list:
                job_2_p_sum[job_id] += self.stage_2_job_2_p_map[after_stage_id][job_id]
        return job_2_p_sum

    @classmethod
    def reverse_stages(cls, instance: FFcParameters) -> Self:
        """Create a new instance of FFcParameters with the order of stages reversed.

        Args:
            instance (FFcParameters): Original parameters instance.

        Returns:
            FFcParameters: New parameters instance with reversed stage order.
        """
        new_stage_ids = instance.stage_id_list[::-1]
        new_processing_times = instance.p_manager.as_stage_reversed()
        new_stage_2_machines_map = {
            stage_id: list(instance.stage_2_machines_map[stage_id])
            for stage_id in new_stage_ids
        }

        return cls(
            instance.name + "_reversed",
            instance.job_id_list,
            new_stage_ids,
            new_stage_2_machines_map,
            new_processing_times,
        )
