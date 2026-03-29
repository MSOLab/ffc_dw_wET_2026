from __future__ import annotations

from typing import Generic, Self, TextIO, Type

import pandas as pd

from src.io import Table2DManager
from src.type_defs import NumericTV, ScalarTV, numeric_type_set


class JobStageProcessingTimeManager(Table2DManager, Generic[NumericTV]):
    """row for each job, column for each stage"""

    def __init__(self, name: str, df: pd.DataFrame) -> None:
        super().__init__(name, df)
        self._on_df_updated()

    def _on_df_updated(self) -> None:
        super()._on_df_updated()
        self._values_cache = self.df.to_numpy()
        self._stage_2_job_2_value_map_cache: dict[
            tuple[tuple[str, ...], tuple[str, ...]], dict[str, dict[str, NumericTV]]
        ] = {}
        self._stage_job_2_value_map_cache: dict[
            tuple[tuple[str, ...], tuple[str, ...]], dict[tuple[str, str], NumericTV]
        ] = {}
        self._job_stage_2_value_map_cache: dict[
            tuple[tuple[str, ...], tuple[str, ...]], dict[tuple[str, str], NumericTV]
        ] = {}
        self._job_2_stage_2_value_map_cache: dict[
            tuple[tuple[str, ...], tuple[str, ...]], dict[str, dict[str, NumericTV]]
        ] = {}

    def _validate_dimensions(
        self, stage_id_tuple: tuple[str, ...], job_id_tuple: tuple[str, ...]
    ) -> None:
        if len(stage_id_tuple) != self.col_count():
            raise ValueError("stage count mismatch")
        if len(job_id_tuple) != self.row_count():
            raise ValueError("job count mismatch")

    @classmethod
    def from_text_stream(
        cls,
        stream: TextIO,
        row_count: int,
        dtype: Type[ScalarTV] | None = None,
        sep: str | None = None,
        name: str = "JobStageProcessingTimeManager",
        transpose: bool = False,
    ) -> Self:
        if dtype is not None:
            if not isinstance(dtype, type):  # Ensure dtype is a type
                raise TypeError(f"Expected dtype to be a type, got {dtype}")
            if dtype is bool:
                raise TypeError("Boolean dtype is not supported for processing times.")
            if dtype not in numeric_type_set:
                raise TypeError(f"Expected dtype to be a numeric type, got '{dtype}'")
        return super().from_text_stream(
            stream, row_count, dtype, sep, name, transpose=transpose
        )

    def as_stage_reversed(self) -> JobStageProcessingTimeManager[NumericTV]:
        """Return a new manager with the stage (column) order reversed.

        The original manager is not modified. The returned
        JobStageProcessingTimeManager wraps a new DataFrame created by
        reversing the columns of the existing DataFrame; the underlying
        data are deep-copied to ensure full independence.
        """
        reversed_df = self.df.iloc[:, ::-1].copy()
        return JobStageProcessingTimeManager(
            name=self.name + "_reversed", df=reversed_df
        )

    def stage_2_job_2_value_map(
        self, stage_id_list: list[str], job_id_list: list[str]
    ) -> dict[str, dict[str, NumericTV]]:
        """
        Cached version: If called with same arguments, return cached result.
        """
        stage_id_tuple = tuple(stage_id_list)
        job_id_tuple = tuple(job_id_list)
        self._validate_dimensions(stage_id_tuple, job_id_tuple)
        cache_key = (stage_id_tuple, job_id_tuple)
        if cache_key not in self._stage_2_job_2_value_map_cache:
            self._stage_2_job_2_value_map_cache[cache_key] = {
                stage_id: {
                    job_id: self._values_cache[row_idx, col_idx]
                    for row_idx, job_id in enumerate(job_id_tuple)
                }
                for col_idx, stage_id in enumerate(stage_id_tuple)
            }
        return self._stage_2_job_2_value_map_cache[cache_key]

    def stage_job_2_value_map(
        self, stage_id_list: list[str], job_id_list: list[str]
    ) -> dict[tuple[str, str], NumericTV]:
        """
        Cached version: If called with same arguments, return cached result.
        """
        stage_id_tuple = tuple(stage_id_list)
        job_id_tuple = tuple(job_id_list)
        self._validate_dimensions(stage_id_tuple, job_id_tuple)
        cache_key = (stage_id_tuple, job_id_tuple)
        if cache_key not in self._stage_job_2_value_map_cache:
            self._stage_job_2_value_map_cache[cache_key] = {
                (stage_id, job_id): self._values_cache[row_idx, col_idx]
                for row_idx, job_id in enumerate(job_id_tuple)
                for col_idx, stage_id in enumerate(stage_id_tuple)
            }
        return self._stage_job_2_value_map_cache[cache_key]

    def job_stage_2_value_map(
        self, job_id_list: list[str], stage_id_list: list[str]
    ) -> dict[tuple[str, str], NumericTV]:
        """
        Cached version: If called with same arguments, return cached result.
        """
        stage_id_tuple = tuple(stage_id_list)
        job_id_tuple = tuple(job_id_list)
        self._validate_dimensions(stage_id_tuple, job_id_tuple)
        cache_key = (job_id_tuple, stage_id_tuple)
        if cache_key not in self._job_stage_2_value_map_cache:
            self._job_stage_2_value_map_cache[cache_key] = {
                (job_id, stage_id): self._values_cache[row_idx, col_idx]
                for col_idx, stage_id in enumerate(stage_id_tuple)
                for row_idx, job_id in enumerate(job_id_tuple)
            }
        return self._job_stage_2_value_map_cache[cache_key]

    def job_2_stage_2_value_map(
        self, job_id_list: list[str], stage_id_list: list[str]
    ) -> dict[str, dict[str, NumericTV]]:
        """
        Cached version: If called with same arguments, return cached result.
        """
        stage_id_tuple = tuple(stage_id_list)
        job_id_tuple = tuple(job_id_list)
        self._validate_dimensions(stage_id_tuple, job_id_tuple)
        cache_key = (job_id_tuple, stage_id_tuple)
        if cache_key not in self._job_2_stage_2_value_map_cache:
            self._job_2_stage_2_value_map_cache[cache_key] = {
                job_id: {
                    stage_id: self._values_cache[row_idx, col_idx]
                    for col_idx, stage_id in enumerate(stage_id_tuple)
                }
                for row_idx, job_id in enumerate(job_id_tuple)
            }
        return self._job_2_stage_2_value_map_cache[cache_key]

    def filter_by_job_indices(
        self, job_index_list: list[int]
    ) -> JobStageProcessingTimeManager[NumericTV]:
        """
        Filter the processing times by job indices.

        Args:
            job_index_list (list[int]): List of job indices to filter.
                The order of job indices in the list will be preserved
                in the filtered result.

        Returns:
            JobStageProcessingTimeManager: Filtered processing times.
        """
        filtered_df = self.df.iloc[job_index_list, :]
        return JobStageProcessingTimeManager(self.name, filtered_df)

    def filter_by_stage_indices(
        self, stage_index_list: list[int]
    ) -> JobStageProcessingTimeManager[NumericTV]:
        """
        Filter the processing times by stage indices.

        Args:
            stage_index_list (list[int]): List of stage indices to filter.
                The order of stage indices in the list will be preserved
                in the filtered result.

        Returns:
            JobStageProcessingTimeManager: Filtered processing times.
        """
        filtered_df = self.df.iloc[:, stage_index_list]
        return JobStageProcessingTimeManager(self.name, filtered_df)
