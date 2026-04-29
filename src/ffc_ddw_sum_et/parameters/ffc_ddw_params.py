from __future__ import annotations

import re
from dataclasses import dataclass
from io import StringIO
from typing import Self, TextIO

from ..io import TextDataParser
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
    def from_pra_data(cls, name: str, stream: TextIO) -> FFcDDWParameters:
        raise NotImplementedError(
            "FFcDDWParameters.from_pra_data() is not supported. "
            "Use FFcDDWParameters.from_pra_2017_data() instead."
        )

    @classmethod
    def from_pra_2017_data(cls, name: str, stream: TextIO) -> FFcDDWParameters:
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

    def get_job_2_due_date_lb_map(self) -> dict[str, int]:
        """Get a mapping from job ID to due date lower bound (d^{-}_j)."""
        return {
            job_id: due_window[0]
            for job_id, due_window in self._job_2_due_window_map.items()
        }

    def get_job_2_due_date_lb_minus_p_map(self) -> dict[str, float]:
        """Get a mapping from job ID to due date lower bound minus processing time."""
        job_2_due_date_lb_map = self.get_job_2_due_date_lb_map()
        job_2_stage_2_value_map = self.p_manager.job_2_stage_2_value_map(
            self.job_id_list, self.stage_id_list
        )
        return {
            job_id: job_2_due_date_lb_map[job_id]
            - sum(job_2_stage_2_value_map[job_id].values())
            for job_id in self.job_id_list
        }

    def get_job_2_due_date_ub_map(self) -> dict[str, int]:
        """Get a mapping from job ID to due date upper bound (d^{+}_j)."""
        return {
            job_id: due_window[1]
            for job_id, due_window in self._job_2_due_window_map.items()
        }

    def get_job_2_due_date_ub_minus_p_map(self) -> dict[str, float]:
        """Get a mapping from job ID to due date upper bound minus processing time."""
        job_2_due_date_ub_map = self.get_job_2_due_date_ub_map()
        job_2_stage_2_value_map = self.p_manager.job_2_stage_2_value_map(
            self.job_id_list, self.stage_id_list
        )
        return {
            job_id: job_2_due_date_ub_map[job_id]
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
        """
        Get the EDDUB (Earliest Due Date Upper Bound) job sequence.
        Sort by job sequence in job_id_list to break ties.
        """
        job_2_due_date_ub_map = self.get_job_2_due_date_ub_map()
        job_2_pos = {job_id: pos for pos, job_id in enumerate(self._job_id_list)}
        return sorted(
            self.job_id_list, key=lambda j: (job_2_due_date_ub_map[j], job_2_pos[j])
        )

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
