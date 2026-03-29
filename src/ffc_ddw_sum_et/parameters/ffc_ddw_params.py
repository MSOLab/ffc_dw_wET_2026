from typing import TextIO

from src.io import TextDataParser

from .base.job_stage_p import JobStageProcessingTimeManager
from .ffc_params import FFcParameters


class FFcDueDateWindowParameters(FFcParameters):
    """Parsed parameters for the FFC with due date and window constraints."""

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
