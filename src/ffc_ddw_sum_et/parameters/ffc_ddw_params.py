from __future__ import annotations

import re
import statistics as _statistics
from dataclasses import dataclass
from functools import cached_property
from io import StringIO
from typing import Literal, Self, TextIO

import numpy as np
import pandas as pd

from .base.job_stage_p import JobStageProcessingTimeManager
from .ffc_params import FFcParameters


@dataclass(frozen=True, kw_only=True)
class InstanceParams:
    """Generation parameters parsed from PRA2017 instance filename."""

    n: int  # number of jobs
    c: int  # number of stages
    m: int  # machines per stage
    T_factor: float  # due-date tightness factor (T)
    R_factor: float  # due-date range factor (R)
    W_factor: int  # due window width factor (W)
    rep: int  # replication index


# Matches: Instance_{n}_{c}_{m}_{beta1}_{beta2}_{cv}_Rep{rep}.txt
# beta1/beta2 use comma as decimal separator (e.g. "0,2" → 0.2)
_INSTANCE_NAME_RE = re.compile(
    r"^Instance_(\d+)_(\d+)_(\d+)_(\d+,\d+)_(\d+,\d+)_(\d+)_Rep(\d+)\.txt$"
)


class FFcDDWParameters(FFcParameters):
    """Parsed parameters for the FFC with due date window constraints."""

    _job_2_due_window_map: dict[str, tuple[int, int]]
    _job_2_ewt_map: dict[str, int]
    _job_2_twt_map: dict[str, int]
    _generation_params: InstanceParams | None

    def __init__(
        self,
        name: str,
        job_id_list: list[str],
        stage_id_list: list[str],
        stage_2_machines_map: dict[str, list[str]],
        p_manager: JobStageProcessingTimeManager[int],
        job_2_due_window_map: dict[str, tuple[int, int]],
        job_2_ewt_map: dict[str, int] | None = None,
        job_2_twt_map: dict[str, int] | None = None,
        generation_params: InstanceParams | None = None,
    ) -> None:
        super().__init__(
            name, job_id_list, stage_id_list, stage_2_machines_map, p_manager
        )
        self._job_2_due_window_map = job_2_due_window_map.copy()
        self._job_2_ewt_map = job_2_ewt_map.copy() if job_2_ewt_map else {}
        self._job_2_twt_map = job_2_twt_map.copy() if job_2_twt_map else {}
        self._generation_params = generation_params

    @property
    def job_2_due_window_map(self) -> dict[str, tuple[int, int]]:
        """Get a copy of the per-job due date window bounds."""
        return self._job_2_due_window_map.copy()

    @cached_property
    def job_2_dw_lb_map(self) -> dict[str, int]:
        """Get a mapping from job ID to due window lower bound (d^-_j)."""
        return {
            job_id: due_window[0]
            for job_id, due_window in self._job_2_due_window_map.items()
        }

    @cached_property
    def job_2_dw_ub_map(self) -> dict[str, int]:
        """Get a mapping from job ID to due window upper bound (d^+_j)."""
        return {
            job_id: due_window[1]
            for job_id, due_window in self._job_2_due_window_map.items()
        }

    @property
    def job_2_ewt_map(self) -> dict[str, int]:
        """Get a copy of the per-job earliness weights (w^{-}_j)."""
        return self._job_2_ewt_map.copy()

    @property
    def job_2_twt_map(self) -> dict[str, int]:
        """Get a copy of the per-job tardiness weights (w^{+}_j)."""
        return self._job_2_twt_map.copy()

    @property
    def generation_params(self) -> InstanceParams | None:
        """Get the generation parameters parsed from the instance filename."""
        return self._generation_params

    @classmethod
    def reverse_stages(cls, instance: FFcParameters) -> Self:
        """Create a new instance of FFcDDWParameters with the order of stages reversed.

        Args:
            instance (FFcParameters): Original parameters instance.
                Should be an FFcDDWParameters instance, but we use FFcParameters as the
                type hint to allow calling this method from the base class.

        Raises:
            TypeError: If the input instance is not an FFcDDWParameters.

        Returns:
            Self: A new FFcDDWParameters instance with stages reversed.
        """
        if not isinstance(instance, FFcDDWParameters):
            raise TypeError(
                f"{cls.__name__}.reverse_stages requires FFcDDWParameters, "
                f"got {type(instance).__name__}"
            )
        base = FFcParameters.reverse_stages(instance)
        return cls(
            base.name,
            base.job_id_list,
            base.stage_id_list,
            base.stage_2_machines_map,
            base.p_manager,
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
            instance.generation_params,
        )

    @classmethod
    def create_instance_of_job_subset(
        cls,
        instance: FFcParameters,
        job_id_subset: set[str],
    ) -> Self:
        """Create a new FFcDDWParameters restricted to a subset of jobs.

        The returned instance keeps the parent's stage and machine layout and
        the forward job order from ``instance.job_id_list`` (filtered down to
        ``job_id_subset``). Arbitrary job permutations are not exposed.
        """
        if not isinstance(instance, FFcDDWParameters):
            raise TypeError(
                f"{cls.__name__}.create_instance_of_job_subset requires "
                f"FFcDDWParameters, got {type(instance).__name__}"
            )
        if not job_id_subset:
            raise ValueError("Job subset must be non-empty.")
        if not job_id_subset.issubset(instance.job_id_list):
            raise ValueError("Job subset contains invalid job IDs.")

        ordered_job_ids = [j for j in instance.job_id_list if j in job_id_subset]
        job_id_2_index = {j: idx for idx, j in enumerate(instance.job_id_list)}
        job_index_list = [job_id_2_index[j] for j in ordered_job_ids]
        new_p_manager = instance.p_manager.filter_by_job_indices(job_index_list)

        new_stage_2_machines_map = {
            stage_id: list(instance.stage_2_machines_map[stage_id])
            for stage_id in instance.stage_id_list
        }
        new_due_window = {j: instance.job_2_due_window_map[j] for j in ordered_job_ids}
        new_ewt = {j: instance.job_2_ewt_map[j] for j in ordered_job_ids}
        new_twt = {j: instance.job_2_twt_map[j] for j in ordered_job_ids}

        return cls(
            instance.name,
            ordered_job_ids,
            instance.stage_id_list,
            new_stage_2_machines_map,
            new_p_manager,
            new_due_window,
            new_ewt,
            new_twt,
            instance.generation_params,
        )

    @classmethod
    def with_stage_processing_time_increment(
        cls,
        instance: FFcParameters,
        stage_id: str,
        increment: int,
    ) -> Self:
        """Return a new FFcDDWParameters identical to ``instance`` except the
        processing time of every job at ``stage_id`` is increased by
        ``increment``.

        ``increment`` must be a non-negative ``int``; ``0`` produces a copy
        with identical processing times. Other stages, due windows, weights,
        and machine layout are preserved.
        """
        if not isinstance(instance, FFcDDWParameters):
            raise TypeError(
                f"{cls.__name__}.with_stage_processing_time_increment requires "
                f"FFcDDWParameters, got {type(instance).__name__}"
            )
        if not isinstance(increment, int) or increment < 0:
            raise ValueError(
                f"increment must be a non-negative integer; got {increment!r}."
            )
        if stage_id not in instance.stage_id_list:
            raise ValueError(
                f"stage_id {stage_id!r} not in instance.stage_id_list "
                f"{instance.stage_id_list!r}."
            )

        # ``p_manager.df`` uses a positional column layout (RangeIndex),
        # so look up the stage column by index rather than by name.
        stage_index = instance.stage_id_list.index(stage_id)
        new_df = instance.p_manager.df.copy()
        new_df.iloc[:, stage_index] = new_df.iloc[:, stage_index] + increment
        new_p_manager = JobStageProcessingTimeManager(instance.p_manager.name, new_df)
        new_stage_2_machines_map = {
            s: list(instance.stage_2_machines_map[s]) for s in instance.stage_id_list
        }
        return cls(
            instance.name,
            list(instance.job_id_list),
            list(instance.stage_id_list),
            new_stage_2_machines_map,
            new_p_manager,
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
            instance.generation_params,
        )

    @classmethod
    def create_instance_of_stage_subset(
        cls,
        instance: FFcParameters,
        stage_id_subset: set[str],
        reverse_stage_seq: bool = False,
    ) -> Self:
        """Create a new FFcDDWParameters restricted to a subset of stages.

        Arbitrary stage permutations are not allowed: only the original forward
        order of the remaining stages (or its reverse, when ``reverse_stage_seq``
        is ``True``) is meaningful for a flow shop instance. ``stage_id_subset``
        is therefore typed as ``set[str]`` and the final ordering is derived
        from ``instance.stage_id_list``.
        """
        if not isinstance(instance, FFcDDWParameters):
            raise TypeError(
                f"{cls.__name__}.create_instance_of_stage_subset requires "
                f"FFcDDWParameters, got {type(instance).__name__}"
            )
        if not stage_id_subset.issubset(instance.stage_id_list):
            raise ValueError("Stage subset contains invalid stage IDs.")
        if not stage_id_subset:
            raise ValueError("Stage subset must be non-empty.")

        ordered_stage_ids = [s for s in instance.stage_id_list if s in stage_id_subset]
        if reverse_stage_seq:
            ordered_stage_ids.reverse()

        stage_id_2_index = {
            stage_id: idx for idx, stage_id in enumerate(instance.stage_id_list)
        }
        stage_index_list = [stage_id_2_index[s] for s in ordered_stage_ids]
        new_p_manager = instance.p_manager.filter_by_stage_indices(stage_index_list)
        new_stage_2_machines_map = {
            stage_id: list(instance.stage_2_machines_map[stage_id])
            for stage_id in ordered_stage_ids
        }

        return cls(
            instance.name,
            instance.job_id_list,
            ordered_stage_ids,
            new_stage_2_machines_map,
            new_p_manager,
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
            instance.generation_params,
        )

    @classmethod
    def coarsen_processing_times(
        cls,
        instance: FFcParameters,
        factor: int,
        mode: Literal["ceil", "round", "floor", "cumulative"] = "ceil",
    ) -> Self:
        """Return a new FFcDDWParameters with processing times coarsened by
        ``factor``, while preserving the original due windows.

        ``mode`` selects the rounding rule:

        - ``"ceil"``  → ``ceil(p / factor)`` (current default)
        - ``"round"`` → ``max(round(p / factor), 1)``
        - ``"floor"`` → ``max(p // factor, 1)``
        - ``"cumulative"`` → round the cumulative sum per job stage-by-stage,
          then derive per-stage values by subtraction, floor 1

        All formulas guarantee ``p' >= 1`` when ``p >= 1``, so no
        zero-length operations are produced. Due window bounds are
        **preserved at the original scale** and must be interpreted
        together with ``time_factor=factor``.

        Weights, job/stage/machine layout, and generation_params are
        preserved.  The new instance name is
        ``"{instance.name}_coarsen_k{factor}"`` when ``mode="ceil"``,
        otherwise ``"{instance.name}_coarsen_k{factor}_{mode}"``.

        Scale-consistency invariant (caller's responsibility): the coarsened
        instance carries coarse processing times but original due windows.
        Evaluating it *without* ``time_factor=factor`` (e.g. a naive
        ``compute_weighted_earliness_tardiness(coarsened)``) silently mixes
        scales.  Always pass ``time_factor=factor`` when scoring the coarsened
        instance.

        Args:
            instance: Source FFcDDWParameters instance.
            factor: Positive integer divisor.
            mode: Rounding rule for ``p → p'``.

        Raises:
            TypeError: If ``instance`` is not an FFcDDWParameters.
            ValueError: If ``factor <= 0`` or ``mode`` is not one of
                the recognised values.

        Returns:
            Self: A new coarsened FFcDDWParameters instance.
        """
        _valid_modes = {"ceil", "round", "floor", "cumulative"}
        if mode not in _valid_modes:
            raise ValueError(
                f"mode must be one of {sorted(_valid_modes)}, got {mode!r}"
            )
        if not isinstance(instance, FFcDDWParameters):
            raise TypeError(
                f"{cls.__name__}.coarsen_processing_times requires FFcDDWParameters, "
                f"got {type(instance).__name__}"
            )
        if factor <= 0:
            raise ValueError(f"factor must be a positive integer; got {factor!r}.")

        df = instance.p_manager.df.copy()
        if mode == "ceil":
            new_df = np.ceil(df / factor).astype(int)
        elif mode == "round":
            new_df = np.maximum(np.round(df / factor), 1).astype(int)
        elif mode == "cumulative":
            values: np.ndarray = df.to_numpy()  # (n_job, n_stage) original p, int
            cum: np.ndarray = (
                np.round(  # (n_job, n_stage) rounded cumulative C_i, float
                    np.cumsum(values, axis=1) / factor
                )
            )
            new: np.ndarray = np.empty(
                values.shape, dtype=int
            )  # (n_job, n_stage) output p'
            running: np.ndarray = np.zeros(
                values.shape[0]
            )  # (n_job,) per-job Σ p'[<col], float
            for col in range(values.shape[1]):
                p_col: np.ndarray = np.maximum(  # (n_job,) coarse p' for this stage
                    cum[:, col] - running,
                    1,  # cum[:,col] − Σ p'[<col], floor at 1
                )
                new[:, col] = p_col
                running = running + p_col
            new_df = pd.DataFrame(new, index=df.index, columns=df.columns)
        else:
            new_df = np.maximum(df // factor, 1)
        new_p_manager = JobStageProcessingTimeManager(instance.p_manager.name, new_df)

        # Preserve original due windows — caller interprets with time_factor.
        new_due_window_map = dict(instance._job_2_due_window_map)

        new_stage_2_machines_map = {
            s: list(instance.stage_2_machines_map[s]) for s in instance.stage_id_list
        }

        name = (
            f"{instance.name}_coarsen_k{factor}"
            if mode == "ceil"
            else f"{instance.name}_coarsen_k{factor}_{mode}"
        )
        return cls(
            name,
            list(instance.job_id_list),
            list(instance.stage_id_list),
            new_stage_2_machines_map,
            new_p_manager,
            new_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
            instance.generation_params,
        )

    @classmethod
    def from_pra_data(cls, name: str, stream: TextIO) -> FFcDDWParameters:
        raise NotImplementedError(
            "FFcDDWParameters.from_pra_data() is not supported. "
            "Use FFcDDWParameters.from_pra_2017_data() instead."
        )

    @classmethod
    def from_pra_2017_data(cls, name: str, stream: TextIO) -> FFcDDWParameters:
        from ffc_ddw_sum_et.io import TextDataParser

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
        job_2_ewt_map: dict[str, int] = {}
        job_2_twt_map: dict[str, int] = {}
        for job_idx, job_id in enumerate(job_id_list):
            rel_due_row = TextDataParser.strip_a_typed_list(stream, int)
            if len(rel_due_row) != 4:
                raise ValueError(
                    f"Expected 4 integers in RELDUE row {job_idx}, "
                    f"got {len(rel_due_row)}."
                )
            job_2_ewt_map[job_id] = rel_due_row[2]  # earliness weight
            job_2_twt_map[job_id] = rel_due_row[3]  # tardiness weight

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
            d_lower, d_upper = due_window_row
            if d_lower > d_upper:
                raise ValueError(
                    "Expected DDW row "
                    + str(job_idx)
                    + " to satisfy d^{-}_j <= d^{+}_j, got "
                    + str(due_window_row)
                )
            job_2_due_window_map[job_id] = (d_lower, d_upper)

        gen_params = cls._parse_instance_name(name)
        return cls(
            name,
            job_id_list,
            stage_id_list,
            stage_2_machines_map,
            processing_times,
            job_2_due_window_map,
            job_2_ewt_map,
            job_2_twt_map,
            gen_params,
        )

    @staticmethod
    def _parse_instance_name(name: str) -> InstanceParams | None:
        """Parse generation parameters from a PRA2017 instance filename."""
        match = _INSTANCE_NAME_RE.match(name)
        if not match:
            return None
        n, c, m = (int(v) for v in match.groups()[:3])
        beta1_str, beta2_str, cv_str, rep_str = match.groups()[3:7]
        return InstanceParams(
            n=n,
            c=c,
            m=m,
            T_factor=float(beta1_str.replace(",", ".")),
            R_factor=float(beta2_str.replace(",", ".")),
            W_factor=int(cv_str),
            rep=int(rep_str),
        )

    # -------------------------
    # Job → priority score maps
    # -------------------------

    def get_job_2_due_date_lb_minus_p_map(self) -> dict[str, float]:
        """Get a mapping from job ID to due date lower bound minus processing time."""
        job_2_stage_2_value_map = self.p_manager.job_2_stage_2_value_map(
            self.job_id_list, self.stage_id_list
        )
        return {
            job_id: self.job_2_dw_lb_map[job_id]
            - sum(job_2_stage_2_value_map[job_id].values())
            for job_id in self.job_id_list
        }

    def get_job_2_due_date_ub_minus_p_map(self) -> dict[str, float]:
        """Get a mapping from job ID to due date upper bound minus processing time."""
        job_2_stage_2_value_map = self.p_manager.job_2_stage_2_value_map(
            self.job_id_list, self.stage_id_list
        )
        return {
            job_id: self.job_2_dw_ub_map[job_id]
            - sum(job_2_stage_2_value_map[job_id].values())
            for job_id in self.job_id_list
        }

    def get_job_2_due_date_star_map(self) -> dict[str, float]:
        """
        Get a mapping from job ID to due date star
        (d^{*}_j = (w^{-}_j * d^{-}_j + w^{+}_j * d^{+}_j) / (w^{-}_j + w^{+}_j).
        """
        return {
            job_id: (
                (
                    self._job_2_ewt_map[job_id] * self._job_2_due_window_map[job_id][0]
                    + self._job_2_twt_map[job_id]
                    * self._job_2_due_window_map[job_id][1]
                )
                / (self._job_2_ewt_map[job_id] + self._job_2_twt_map[job_id])
            )
            for job_id in self.job_id_list
        }

    def get_job_2_due_date_star_minus_p_map(self) -> dict[str, float]:
        """
        Get a mapping from job ID to due date star minus processing time
        (d^{*}_j - sum_i p_{ij}).
        """
        job_2_due_date_star_map = self.get_job_2_due_date_star_map()
        job_2_stage_2_value_map = self.p_manager.job_2_stage_2_value_map(
            self.job_id_list, self.stage_id_list
        )
        return {
            job_id: job_2_due_date_star_map[job_id]
            - sum(job_2_stage_2_value_map[job_id].values())
            for job_id in self.job_id_list
        }

    def get_job_2_due_date_star_minus_half_p_map(self) -> dict[str, float]:
        """
        Get a mapping from job ID to due date star minus half processing time
        (d^{*}_j - 0.5 * sum_i p_{ij}).
        """
        job_2_due_date_star_map = self.get_job_2_due_date_star_map()
        job_2_stage_2_value_map = self.p_manager.job_2_stage_2_value_map(
            self.job_id_list, self.stage_id_list
        )
        return {
            job_id: job_2_due_date_star_map[job_id]
            - 0.5 * sum(job_2_stage_2_value_map[job_id].values())
            for job_id in self.job_id_list
        }

    def get_job_2_due_date_star_plus_half_p_map(self) -> dict[str, float]:
        """
        Get a mapping from job ID to due date star plus half processing time
        (d^{*}_j + 0.5 * sum_i p_{ij}).
        """
        job_2_due_date_star_map = self.get_job_2_due_date_star_map()
        job_2_stage_2_value_map = self.p_manager.job_2_stage_2_value_map(
            self.job_id_list, self.stage_id_list
        )
        return {
            job_id: job_2_due_date_star_map[job_id]
            + 0.5 * sum(job_2_stage_2_value_map[job_id].values())
            for job_id in self.job_id_list
        }

    def get_job_2_due_date_star_plus_p_map(self) -> dict[str, float]:
        """
        Get a mapping from job ID to due date star plus processing time
        (d^{*}_j + sum_i p_{ij}).
        """
        job_2_due_date_star_map = self.get_job_2_due_date_star_map()
        job_2_stage_2_value_map = self.p_manager.job_2_stage_2_value_map(
            self.job_id_list, self.stage_id_list
        )
        return {
            job_id: job_2_due_date_star_map[job_id]
            + sum(job_2_stage_2_value_map[job_id].values())
            for job_id in self.job_id_list
        }

    def get_eddub_job_sequence(self) -> list[str]:
        """EDDUB (Earliest Due Date Upper Bound) job sequence.

        Sort by ``d⁺_j`` ascending; ties break by native ``job_id_list`` order.

        Pan et al. (2017)의 초기화 휴리스틱 중 EDD에 해당한다(같은 논문의 LSL =
        :meth:`get_lsl_job_sequence`, OSL = :meth:`get_osl_job_sequence`). due-window
        모델에서는 단일 due date ``d`` 를 상한 ``d⁺_j`` 로 둔다.
        """
        job_2_pos = {job_id: pos for pos, job_id in enumerate(self._job_id_list)}
        return sorted(
            self.job_id_list, key=lambda j: (self.job_2_dw_ub_map[j], job_2_pos[j])
        )

    def get_eddub_twt_job_sequence(self) -> list[str]:
        """EDDUB + tardiness-weight priority job sequence.

        Sort by ``(d⁺_j asc, w⁺_j desc, position asc)``. Same ``d⁺`` primary
        key as ``get_eddub_job_sequence`` but with ``w⁺`` (tardiness weight)
        descending as the secondary key — when deadlines tie, jobs with larger
        tardiness penalty go first. Matches the dispatch-seed ordering from
        commit c7f54d0.
        """
        twt = self._job_2_twt_map
        dw_ub = self.job_2_dw_ub_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}
        return sorted(
            self.job_id_list,
            key=lambda j: (dw_ub[j], -twt[j], job_2_pos[j]),
        )

    def get_lsl_job_sequence(self) -> list[str]:
        """LSL (smallest slack on the last machine) job sequence — Pan et al. (2017).

        Sort ascending by last-stage slack ``d⁺_j − p_{m,j}`` (m = 마지막 stage),
        ties break by native ``job_id_list`` position. ``d⁺_j`` 는 due window 상한.
        ``get_due_weight_pos_job_sequence`` 의 ``max(0, d⁺−p_last)`` 변형과 달리 0
        클램프·추가 tie-break 없이 논문식 그대로다.
        """
        p_last = self.get_job_2_p_map_for_stage(self.stage_id_list[-1])
        dw_ub = self.job_2_dw_ub_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}
        return sorted(
            self.job_id_list,
            key=lambda j: (dw_ub[j] - p_last[j], job_2_pos[j]),
        )

    def get_osl_job_sequence(self) -> list[str]:
        """OSL (overall slack time) job sequence — Pan et al. (2017).

        Sort ascending by overall slack ``d⁺_j − Σ_i p_{i,j}`` (모든 stage 합),
        ties break by native ``job_id_list`` position. LSL의 일반화로, slack을 전
        stage 처리시간 합 기준으로 계산한다. 키는 기존 점수 맵
        :meth:`get_job_2_due_date_ub_minus_p_map` 를 재사용한다(SSOT).
        """
        osl = self.get_job_2_due_date_ub_minus_p_map()  # d⁺_j − Σ_i p_{i,j}
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}
        return sorted(self.job_id_list, key=lambda j: (osl[j], job_2_pos[j]))

    def get_weight_due_pos_job_sequence(self) -> list[str]:
        """
        Get the "weight-due-pos" priority job sequence.
        Sort by (max(w⁻, w⁺) desc, w⁻+w⁺ desc, due-window width asc, position asc).
        """
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        def key(j: str) -> tuple[int, int, int, int]:
            w_e = ewt[j]
            w_t = twt[j]
            d_lower, d_upper = ddw[j]
            return (
                -max(w_e, w_t),
                -(w_e + w_t),
                int(d_upper - d_lower),
                job_2_pos[j],
            )

        return sorted(self.job_id_list, key=key)

    def get_due_weight_pos_job_sequence(self) -> list[str]:
        """
        Get the "due-weight-pos" priority job sequence.
        Sort by (max(0, d⁺-p_last) asc, d⁺ asc, d⁻ asc, w⁻+w⁺ desc, position asc).
        """
        last_stage_id = self.stage_id_list[-1]
        p_last = self.get_job_2_p_map_for_stage(last_stage_id)
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        def key(j: str) -> tuple[int, int, int, int, int]:
            d_lower, d_upper = ddw[j]
            return (
                max(0, d_upper - p_last[j]),
                d_upper,
                d_lower,
                -(ewt[j] + twt[j]),
                job_2_pos[j],
            )

        return sorted(self.job_id_list, key=key)

    def get_due2_weight_pos_job_sequence(self) -> list[str]:
        """
        Job sequence prioritised by max(r_j, d⁺-p_last) asc, d⁺ asc, d⁻ asc,
        w_sum desc, position asc.
        """
        last_stage_id = self.stage_id_list[-1]
        p_last = self.get_job_2_p_map_for_stage(last_stage_id)
        r_j = self.get_job_2_p_sum_except_last_stage()
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        def key(j: str) -> tuple[int, int, int, int, int]:
            d_lower, d_upper = ddw[j]
            return (
                max(r_j[j], d_upper - p_last[j]),
                d_upper,
                d_lower,
                -(ewt[j] + twt[j]),
                job_2_pos[j],
            )

        return sorted(self.job_id_list, key=key)

    def get_w1_job_sequence(self) -> list[str]:
        """
        Get the "w1" priority job sequence.
        Sort descending by (w⁺_j - w⁻_j), then by position.
        """
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        def key(j: str) -> tuple[float, int]:
            return (-(twt[j] - ewt[j]), job_2_pos[j])

        return sorted(self.job_id_list, key=key)

    def get_wspt_twt_job_sequence(self) -> list[str]:
        """WSPT (tardiness weight) job sequence.

        Sort descending by ``w⁺_j / P_j`` (``P_j = Σ_i p_{ij}``), ties break by
        native position. Classic WSPT rule for total weighted tardiness: when
        the instance is congested enough that most jobs finish late (the
        ``T=0.6, R=0.2`` regime — tight, narrowly-ranged due dates), sequencing
        by weight-to-processing-time ratio is the single-machine optimum. The
        ``w1`` rule (``w⁺−w⁻`` desc) ignores ``p`` entirely; this restores it.
        """
        twt = self._job_2_twt_map
        p_total = {
            j: sum(self.job_2_stage_2_p_map[j].values()) for j in self._job_id_list
        }
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        def key(j: str) -> tuple[float, int]:
            return (-(twt[j] / p_total[j]), job_2_pos[j])

        return sorted(self.job_id_list, key=key)

    def get_wxd1_job_sequence(self) -> list[str]:
        """
        Get the "wxd1" priority job sequence.

        Same split as ``wxd2`` but each group's sort key is multiplied by
        ``(d_j - d_bar)`` (negative inside the early group, non-negative
        inside the late group):
          - "early" group: ``d_j - d_bar < 0``, sort ascending by
                           ``(w⁺_j - 2·w⁻_j + 2·w_max) * (d_j - d_bar)``
          - "late"  group: ``d_j - d_bar >= 0``, sort ascending by
                           ``(w⁻_j - 2·w⁺_j + 2·w_max) * (d_j - d_bar)``
        with ``w_max = max(max(w⁻_j), max(w⁺_j))``. Ties break by
        native position. Returned sequence is early-list ++ late-list.
        """
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
        d_bar = sum(d_mid.values()) / len(d_mid)
        w_max = max(max(ewt.values()), max(twt.values()))

        early = [j for j in self._job_id_list if d_mid[j] - d_bar < 0]
        late = [j for j in self._job_id_list if d_mid[j] - d_bar >= 0]

        def early_key(j: str) -> tuple[float, int]:
            return (
                (twt[j] - 2 * ewt[j] + 2 * w_max) * (d_mid[j] - d_bar),
                job_2_pos[j],
            )

        def late_key(j: str) -> tuple[float, int]:
            return (
                (ewt[j] - 2 * twt[j] + 2 * w_max) * (d_mid[j] - d_bar),
                job_2_pos[j],
            )

        return sorted(early, key=early_key) + sorted(late, key=late_key)

    def get_wxd2_job_sequence(self) -> list[str]:
        """
        Get the "wxd2" priority job sequence.
        Partition criterion uses additive scores:

        - Earliness aversion score = w⁻_j + (d⁻_j - d̄)
        (larger means more averse to early completion)
        - Tardiness aversion score = w⁺_j + (d̄  - d⁺_j)
        (larger means more averse to late completion)

        where d̄ = mean of job midpoints (d⁻+d⁺)/2.

        - "early" group: Tardiness aversion score > Earliness aversion score,
        sort ascending by (w⁺_j - 2·w⁻_j + 2·ew_max) * (d⁻_j - d̄)
        - "late"  group: Tardiness aversion score <= Earliness aversion score,
        sort ascending by (w⁻_j - 2·w⁺_j + 2·tw_max) * (d⁺_j - d̄)

        where ew_max = max(w⁻_j), tw_max = max(w⁺_j).
        Ties break by native position.
        Returned sequence is early-list ++ late-list.
        """
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
        d_bar = sum(d_mid.values()) / len(d_mid)
        ew_max = max(ewt.values())
        tw_max = max(twt.values())

        earliness_aversion_score = {
            j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list
        }
        tardiness_aversion_score = {
            j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list
        }

        early = [
            j
            for j in self._job_id_list
            if earliness_aversion_score[j] < tardiness_aversion_score[j]
        ]
        late = [
            j
            for j in self._job_id_list
            if earliness_aversion_score[j] >= tardiness_aversion_score[j]
        ]

        def early_key(j: str) -> tuple[float, int]:
            return (
                (twt[j] - 2 * ewt[j] + 2 * ew_max) * (ddw[j][0] - d_bar),
                job_2_pos[j],
            )

        def late_key(j: str) -> tuple[float, int]:
            return (
                (ewt[j] - 2 * twt[j] + 2 * tw_max) * (ddw[j][1] - d_bar),
                job_2_pos[j],
            )

        return sorted(early, key=early_key) + sorted(late, key=late_key)

    def get_wxd3_job_sequence(self) -> list[str]:
        """wxd2 partition (tie→early) + 그룹 내 d̄-center 가중 penalty 정렬.

        Partition: earliness_aversion <= tardiness_aversion → early, else late.
        Tie (==) goes to early (wxd2 와 반대).

        Group sorting uses actual weighted penalty if the job sits at d̄:
        - early group: sort by tp_j(d̄) descending = (-tp_j, native_pos) ascending
        - late group:  sort by ep_j(d̄) ascending = (ep_j, native_pos) ascending
        """
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
        d_bar = sum(d_mid.values()) / len(d_mid)

        earliness_aversion_score = {
            j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list
        }
        tardiness_aversion_score = {
            j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list
        }
        early = [
            j
            for j in self._job_id_list
            if earliness_aversion_score[j] <= tardiness_aversion_score[j]
        ]
        late = [
            j
            for j in self._job_id_list
            if earliness_aversion_score[j] > tardiness_aversion_score[j]
        ]

        def early_key(j: str) -> tuple[float, int]:
            tp = twt[j] * max(d_bar - ddw[j][1], 0)
            return (-tp, job_2_pos[j])

        def late_key(j: str) -> tuple[float, int]:
            ep = ewt[j] * max(ddw[j][0] - d_bar, 0)
            return (ep, job_2_pos[j])

        return sorted(early, key=early_key) + sorted(late, key=late_key)

    def get_wxd4_job_sequence(self) -> list[str]:
        """wxd3 와 동일하나 그룹 내 penalty 를 앞-group 마지막-stage 완료 추정
        baseline = max(min_j r_j + Σ_{early} p_last_j / m_last, d̄) 에서 측정."""
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
        d_bar = sum(d_mid.values()) / len(d_mid)

        earliness_aversion_score = {
            j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list
        }
        tardiness_aversion_score = {
            j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list
        }
        early = [
            j
            for j in self._job_id_list
            if earliness_aversion_score[j] <= tardiness_aversion_score[j]
        ]
        late = [
            j
            for j in self._job_id_list
            if earliness_aversion_score[j] > tardiness_aversion_score[j]
        ]

        last_stage_id = self.stage_id_list[-1]
        p_last = self.get_job_2_p_map_for_stage(last_stage_id)
        r_j = self.get_job_2_p_sum_except_last_stage()
        early_p_last_sum = sum(p_last[j] for j in early)
        baseline = max(
            min(r_j.values()) + early_p_last_sum / self.last_stage_mc_count,
            d_bar,
        )

        def early_key(j: str) -> tuple[float, int]:
            tp = twt[j] * max(baseline - ddw[j][1], 0)
            return (-tp, job_2_pos[j])

        def late_key(j: str) -> tuple[float, int]:
            ep = ewt[j] * max(ddw[j][0] - baseline, 0)
            return (ep, job_2_pos[j])

        return sorted(early, key=early_key) + sorted(late, key=late_key)

    def get_wxd5_job_sequence(self) -> list[str]:
        """wxd2 와 동일(partition·정렬·tie 모두)하나 d̄ 만 교체.

        d̄ = max(윈도우 중점 평균,
                 min_j r_j + Σ_j p_last_j / (m_last × 2))
        — 마지막 stage 완료 추정 하한으로 center 를 뒤로 민다. 이 d̄ 가
        wxd2 의 partition aversion score 와 양 그룹 정렬식에 모두 들어간다.
        """
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
        mean_midpoint = sum(d_mid.values()) / len(d_mid)
        last_stage_id = self.stage_id_list[-1]
        p_last = self.get_job_2_p_map_for_stage(last_stage_id)
        r_j = self.get_job_2_p_sum_except_last_stage()
        p_last_total = sum(p_last.values())
        d_bar = max(
            mean_midpoint,
            min(r_j.values()) + p_last_total / (self.last_stage_mc_count * 2),
        )
        ew_max = max(ewt.values())
        tw_max = max(twt.values())

        earliness_aversion_score = {
            j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list
        }
        tardiness_aversion_score = {
            j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list
        }

        early = [
            j
            for j in self._job_id_list
            if earliness_aversion_score[j] < tardiness_aversion_score[j]
        ]
        late = [
            j
            for j in self._job_id_list
            if earliness_aversion_score[j] >= tardiness_aversion_score[j]
        ]

        def early_key(j: str) -> tuple[float, int]:
            return (
                (twt[j] - 2 * ewt[j] + 2 * ew_max) * (ddw[j][0] - d_bar),
                job_2_pos[j],
            )

        def late_key(j: str) -> tuple[float, int]:
            return (
                (ewt[j] - 2 * twt[j] + 2 * tw_max) * (ddw[j][1] - d_bar),
                job_2_pos[j],
            )

        return sorted(early, key=early_key) + sorted(late, key=late_key)

    def get_wxd6_job_sequence(self) -> list[str]:
        """wxd2/5 곱셈형 키 + 2-center group sorting.

        Partition is identical to wxd5:
            d̄₅ = max(mean_midpoint, min r_j + Σ_j p_last_j / (m_last × 2))
            early if earliness_aversion < tardiness_aversion, else late (tie→late).

        Sorting centers are separated from partition:
            early_center = min r_j + Σ_all p_last_j / m_last   (approx makespan, no ÷2, no floor)
            late_center  = min r_j                              (raw)

        - early group: sort ascending by (w⁺_j - 2·w⁻_j + 2·ew_max) * (d⁻_j - early_center)
        - late group:  sort ascending by (w⁻_j - 2·w⁺_j + 2·tw_max) * (d⁺_j - late_center)
        """
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
        mean_midpoint = sum(d_mid.values()) / len(d_mid)

        last_stage_id = self.stage_id_list[-1]
        p_last = self.get_job_2_p_map_for_stage(last_stage_id)
        r_j = self.get_job_2_p_sum_except_last_stage()
        p_last_total = sum(p_last.values())
        min_r = min(r_j.values())
        m_last = self.last_stage_mc_count

        # partition center (wxd5 d̄, ×2 유지)
        d_bar = max(mean_midpoint, min_r + p_last_total / (m_last * 2))

        # sorting centers (group-specific)
        early_center = min_r + p_last_total / m_last
        late_center = min_r

        # partition (wxd5 와 동일: aversion score + tie>=→late)
        earliness_aversion = {
            j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list
        }
        tardiness_aversion = {
            j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list
        }
        early = [
            j
            for j in self._job_id_list
            if earliness_aversion[j] < tardiness_aversion[j]
        ]
        late = [
            j
            for j in self._job_id_list
            if earliness_aversion[j] >= tardiness_aversion[j]
        ]

        ew_max = max(ewt.values())
        tw_max = max(twt.values())

        def early_key(j: str) -> tuple[float, int]:
            return (
                (twt[j] - 2 * ewt[j] + 2 * ew_max) * (ddw[j][0] - early_center),
                job_2_pos[j],
            )

        def late_key(j: str) -> tuple[float, int]:
            return (
                (ewt[j] - 2 * twt[j] + 2 * tw_max) * (ddw[j][1] - late_center),
                job_2_pos[j],
            )

        return sorted(early, key=early_key) + sorted(late, key=late_key)

    def get_wxd7_job_sequence(self) -> list[str]:
        """쌩 weighted penalty + 2-center group sorting.

        Partition is identical to wxd5:
            d̄₅ = max(mean_midpoint, min r_j + Σ_j p_last_j / (m_last × 2))
            early if earliness_aversion < tardiness_aversion, else late (tie→late).

        Sorting centers are separated from partition:
            early_center = min r_j + Σ_all p_last_j / m_last   (approx makespan, no ÷2, no floor)
            late_center  = min r_j                              (raw)

        - early group: sort ascending by -tp_j(early_center)
            where tp_j(c) = w⁺_j × max(c - d⁺_j, 0)
        - late group:  sort ascending by ep_j(late_center)
            where ep_j(c) = w⁻_j × max(d⁻_j - c, 0)
        """
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
        mean_midpoint = sum(d_mid.values()) / len(d_mid)

        last_stage_id = self.stage_id_list[-1]
        p_last = self.get_job_2_p_map_for_stage(last_stage_id)
        r_j = self.get_job_2_p_sum_except_last_stage()
        p_last_total = sum(p_last.values())
        min_r = min(r_j.values())
        m_last = self.last_stage_mc_count

        # partition center (wxd5 d̄, ×2 유지)
        d_bar = max(mean_midpoint, min_r + p_last_total / (m_last * 2))

        # sorting centers (group-specific)
        early_center = min_r + p_last_total / m_last
        late_center = min_r

        # partition (wxd5 와 동일: aversion score + tie>=→late)
        earliness_aversion = {
            j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list
        }
        tardiness_aversion = {
            j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list
        }
        early = [
            j
            for j in self._job_id_list
            if earliness_aversion[j] < tardiness_aversion[j]
        ]
        late = [
            j
            for j in self._job_id_list
            if earliness_aversion[j] >= tardiness_aversion[j]
        ]

        def early_key(j: str) -> tuple[float, int]:
            tp = twt[j] * max(early_center - ddw[j][1], 0)
            return (-tp, job_2_pos[j])

        def late_key(j: str) -> tuple[float, int]:
            ep = ewt[j] * max(ddw[j][0] - late_center, 0)
            return (ep, job_2_pos[j])

        return sorted(early, key=early_key) + sorted(late, key=late_key)

    def _center_penalty_job_sequence(self, center: float) -> list[str]:
        """Sort jobs lexicographically by ``(-tp_j(c), ep_j(c), d⁺_j, native pos)``.

        ``tp_j``/``ep_j`` are the tardiness/earliness penalties incurred if job
        ``j`` completes at ``center``. Tardiness leads the key (descending, via
        ``-tp``) because — unlike earliness, which ``insert_idle_time`` can
        recover by delaying the start — tardiness is irreversible once the job
        is dispatched too late. So a heavy-tardiness job is rushed to the front
        regardless of its earliness, never letting earliness override the
        tardiness ranking. Earliness breaks ties (ascending → most
        earliness-averse last). The third key ``d⁺_j`` (EDD⁺) orders the central
        block — jobs with ``tp=ep=0`` whose window straddles ``center`` — by due
        upper bound; native position is the final stable tie-break.
        """
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        def key(j: str) -> tuple[float, float, int, int]:
            ep = ewt[j] * max(ddw[j][0] - center, 0)
            tp = twt[j] * max(center - ddw[j][1], 0)
            return (-tp, ep, ddw[j][1], job_2_pos[j])

        return sorted(self._job_id_list, key=key)

    def get_cpd_mean_job_sequence(self) -> list[str]:
        """center = mean of midpoints (= wxd2's d_bar)."""
        ddw = self._job_2_due_window_map
        mids = [(ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list]
        return self._center_penalty_job_sequence(sum(mids) / len(mids))

    def get_cpd_wmean_job_sequence(self) -> list[str]:
        """center = penalty-weighted mean of midpoints."""
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        num = sum(
            (ewt[j] + twt[j]) * (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list
        )
        den = sum(ewt[j] + twt[j] for j in self._job_id_list)
        return self._center_penalty_job_sequence(num / den)

    def get_cpd_median_job_sequence(self) -> list[str]:
        """center = median of midpoints (outlier-robust)."""
        ddw = self._job_2_due_window_map
        mids = [(ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list]
        return self._center_penalty_job_sequence(_statistics.median(mids))

    def get_due_star_weight_pos_job_sequence(self) -> list[str]:
        """
        Get the "due-star-weight-pos" priority job sequence.
        Sort by (d* asc, d+ asc, w⁻+w⁺ desc, position asc).
        """
        job_2_due_date_star_map = self.get_job_2_due_date_star_map()
        ewt = self._job_2_ewt_map
        twt = self._job_2_twt_map
        ddw = self._job_2_due_window_map
        job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

        def key(j: str) -> tuple[float, int, int, int]:
            d_star = job_2_due_date_star_map[j]
            d_upper = ddw[j][1]
            return (
                d_star,
                d_upper,
                -(ewt[j] + twt[j]),
                job_2_pos[j],
            )

        return sorted(self.job_id_list, key=key)
