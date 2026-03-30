from __future__ import annotations

from io import StringIO
from typing import TextIO

from ..io import TextDataParser
from .base.job_stage_p import JobStageProcessingTimeManager
from .ffc_params import FFcParameters


class FFcDueDateWindowParameters(FFcParameters):
    """Parsed parameters for the FFC with due date window constraints."""

    _job_2_due_window_map: dict[str, tuple[int, int]]

    def __init__(
        self,
        name: str,
        job_id_list: list[str],
        stage_id_list: list[str],
        stage_2_machines_map: dict[str, list[str]],
        p_manager: JobStageProcessingTimeManager[int],
        job_2_due_window_map: dict[str, tuple[int, int]],
    ) -> None:
        super().__init__(
            name, job_id_list, stage_id_list, stage_2_machines_map, p_manager
        )
        self._job_2_due_window_map = job_2_due_window_map.copy()

    @property
    def job_2_due_window_map(self) -> dict[str, tuple[int, int]]:
        """Get a copy of the per-job due date window bounds."""
        return self._job_2_due_window_map.copy()

    @classmethod
    def from_pra_data(cls, name: str, stream: TextIO) -> FFcDueDateWindowParameters:
        raise NotImplementedError(
            "FFcDueDateWindowParameters.from_pra_data() is not supported. "
            "Use FFcDueDateWindowParameters.from_pra_2017_data() instead."
        )

    @classmethod
    def from_pra_2017_data(
        cls, name: str, stream: TextIO
    ) -> FFcDueDateWindowParameters:
        marker = TextDataParser.strip_a_line(stream)
        if marker != "HFSDDW":
            raise ValueError(
                f"Expected 'HFSDDW' marker at the top of PRA2017 data, got '{marker}'."
            )

        header = TextDataParser.strip_a_typed_list(stream, int)
        if len(header) != 3:
            raise ValueError(
                "Expected PRA2017 header with 3 integers: "
                "<job_count> <total_machine_count> <stage_count>."
            )
        job_count, total_machine_count, stage_count = header
        if stage_count <= 0:
            raise ValueError(
                f"Stage count must be positive in PRA2017 data, got {stage_count}."
            )
        if total_machine_count % stage_count != 0:
            raise ValueError(
                "PRA2017 large-instance format requires an identical machine count "
                "per stage; total_machine_count must be divisible by stage_count."
            )

        machine_count_per_stage = [total_machine_count // stage_count] * stage_count
        cls._validate_machine_count_per_stage(stage_count, machine_count_per_stage)

        job_id_num_digits = len(str(job_count - 1))
        job_id_list = [
            f"j{str(job_idx).zfill(job_id_num_digits)}" for job_idx in range(job_count)
        ]

        stage_id_num_digits = len(str(stage_count - 1))
        stage_id_list = [
            f"i{str(stage_idx).zfill(stage_id_num_digits)}"
            for stage_idx in range(stage_count)
        ]

        stage_2_machines_map: dict[str, list[str]] = {}
        for stage_idx, stage_id in enumerate(stage_id_list):
            machine_id_num_digits = len(str(machine_count_per_stage[stage_idx] - 1))
            stage_2_machines_map[stage_id] = [
                f"{stage_id}_{str(machine_idx).zfill(machine_id_num_digits)}"
                for machine_idx in range(machine_count_per_stage[stage_idx])
            ]

        processing_rows: list[list[int]] = []
        expected_stage_indices = list(range(stage_count))
        for job_idx in range(job_count):
            row = TextDataParser.strip_a_typed_list(stream, int)
            if len(row) != 2 * stage_count:
                raise ValueError(
                    f"Expected {2 * stage_count} integers in processing row "
                    f"{job_idx}, got {len(row)}."
                )
            stage_indices = row[::2]
            if stage_indices != expected_stage_indices:
                raise ValueError(
                    f"Expected stage indices {expected_stage_indices} in processing "
                    f"row {job_idx}, got {stage_indices}."
                )
            processing_rows.append(row[1::2])

        processing_times_stream = StringIO(
            "\n".join(" ".join(str(value) for value in row) for row in processing_rows)
        )
        processing_times = JobStageProcessingTimeManager.from_text_stream(
            processing_times_stream, job_count, dtype=int
        )
        cls._validate_processing_times(job_count, stage_count, processing_times)

        lbcmax_line = TextDataParser.strip_a_line(stream)
        lbcmax_prefix = "LBCmax:"
        if not lbcmax_line.startswith(lbcmax_prefix):
            raise ValueError(
                f"Expected '{lbcmax_prefix}' section after processing times, "
                f"got '{lbcmax_line}'."
            )
        try:
            int(lbcmax_line[len(lbcmax_prefix) :].strip())
        except ValueError as exc:
            raise ValueError(
                f"Failed to parse integer lower bound from line '{lbcmax_line}'."
            ) from exc

        rel_due_marker = TextDataParser.strip_a_line(stream)
        if rel_due_marker != "RELDUE":
            raise ValueError(
                f"Expected 'RELDUE' section after LBCmax, got '{rel_due_marker}'."
            )
        for job_idx in range(job_count):
            rel_due_row = TextDataParser.strip_a_typed_list(stream, int)
            if len(rel_due_row) != 4:
                raise ValueError(
                    f"Expected 4 integers in RELDUE row {job_idx}, "
                    f"got {len(rel_due_row)}."
                )

        ddw_marker = TextDataParser.strip_a_line(stream)
        if ddw_marker != "DDW":
            raise ValueError(
                f"Expected 'DDW' section after RELDUE, got '{ddw_marker}'."
            )

        job_2_due_window_map: dict[str, tuple[int, int]] = {}
        for job_idx, job_id in enumerate(job_id_list):
            due_window_row = TextDataParser.strip_a_typed_list(stream, int)
            if len(due_window_row) != 2:
                raise ValueError(
                    f"Expected 2 integers in DDW row {job_idx}, "
                    f"got {len(due_window_row)}."
                )
            window_start, window_end = due_window_row
            if window_start > window_end:
                raise ValueError(
                    f"Expected DDW row {job_idx} to satisfy start <= end, "
                    f"got {due_window_row}."
                )
            job_2_due_window_map[job_id] = (window_start, window_end)

        return cls(
            name,
            job_id_list,
            stage_id_list,
            stage_2_machines_map,
            processing_times,
            job_2_due_window_map,
        )
