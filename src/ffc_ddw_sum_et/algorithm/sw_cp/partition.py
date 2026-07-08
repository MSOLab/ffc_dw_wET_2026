"""Operation partitioning for the SW-CP sliding window.

Each batch step partitions the operations on every stage into five
regions around the unfixed window:

    LTF | LPF | UNFIXED | RPF | RTF

    LTF (left-time-fixed)   : start times pinned, contribute via dummy bars
    LPF (left-profile-fixed): non-time-fixed; precedence chain preserved
    UNFIXED                 : non-time-fixed; full freedom inside the window
    RPF (right-profile-fixed): non-time-fixed; precedence chain preserved
    RTF (right-time-fixed)  : start times pinned, contribute via dummy bars
"""

from __future__ import annotations

from dataclasses import dataclass

from ...solution.ffc_schedule import (
    FFcSchedule,
    JobIdType,
    McIdType,
    StageIdType,
)

__all__ = [
    "JobMcType",
    "OperationPartition",
    "build_stage_2_batch_list",
    "build_operation_partition",
    "validate_and_get_batch_count",
]


JobMcType = tuple[JobIdType, McIdType]


@dataclass(frozen=True)
class OperationPartition:
    """Five-region partition of the operations on a single stage."""

    left_time_fixed: tuple[JobMcType, ...]
    left_profile_fixed: tuple[JobMcType, ...]
    unfixed: tuple[JobMcType, ...]
    right_profile_fixed: tuple[JobMcType, ...]
    right_time_fixed: tuple[JobMcType, ...]

    @property
    def all_operations(self) -> tuple[JobMcType, ...]:
        return (
            self.left_time_fixed
            + self.left_profile_fixed
            + self.unfixed
            + self.right_profile_fixed
            + self.right_time_fixed
        )

    @property
    def time_fixed(self) -> tuple[JobMcType, ...]:
        return self.left_time_fixed + self.right_time_fixed

    @property
    def non_time_fixed(self) -> tuple[JobMcType, ...]:
        return self.left_profile_fixed + self.unfixed + self.right_profile_fixed

    @property
    def profile_fixed(self) -> tuple[JobMcType, ...]:
        return self.left_profile_fixed + self.right_profile_fixed

    @property
    def non_profile_fixed(self) -> tuple[JobMcType, ...]:
        return self.left_time_fixed + self.unfixed + self.right_time_fixed

    @property
    def non_time_fixed_jobs(self) -> frozenset[JobIdType]:
        return frozenset(j for j, _ in self.non_time_fixed)

    @property
    def unfixed_jobs(self) -> frozenset[JobIdType]:
        return frozenset(j for j, _ in self.unfixed)

    def promote_job_contained_ops(
        self, promoted_job_id_set: frozenset[JobIdType] | set[JobIdType]
    ) -> "OperationPartition":
        """Promote profile-fixed operations of any job in ``promoted_job_id_set``
        into the unfixed set."""
        if not promoted_job_id_set:
            return self
        promoted_left = tuple(
            sorted(
                op for op in self.left_profile_fixed if op[0] not in promoted_job_id_set
            )
        )
        promoted_right = tuple(
            sorted(
                op
                for op in self.right_profile_fixed
                if op[0] not in promoted_job_id_set
            )
        )
        promoted_unfixed = tuple(
            sorted(
                self.unfixed
                + tuple(
                    op
                    for op in self.left_profile_fixed + self.right_profile_fixed
                    if op[0] in promoted_job_id_set
                )
            )
        )
        return OperationPartition(
            left_time_fixed=self.left_time_fixed,
            left_profile_fixed=promoted_left,
            unfixed=promoted_unfixed,
            right_profile_fixed=promoted_right,
            right_time_fixed=self.right_time_fixed,
        )


def build_stage_2_batch_list(
    schedule: FFcSchedule,
    batch_size: int,
) -> dict[StageIdType, list[tuple[JobMcType, ...]]]:
    """Partition each stage's operations into time-ordered batches.

    Operations are sorted by midpoint ``(start+end)/2``, then start, then
    machine id, then job id (matches hybridflowshop's default).
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    stage_2_batches: dict[StageIdType, list[tuple[JobMcType, ...]]] = {}
    for stage_id in schedule.stages:
        ops = sorted(
            (
                (job_id, mc_id, start_time, end_time)
                for mc_id, start_time, end_time, job_id in schedule.iter_operations_on_stage(
                    stage_id
                )
            ),
            key=lambda op: ((op[2] + op[3]) / 2, op[2], op[1], op[0]),
        )
        stage_2_batches[stage_id] = [
            tuple(
                (job_id, mc_id) for job_id, mc_id, _, _ in ops[idx : idx + batch_size]
            )
            for idx in range(0, len(ops), batch_size)
        ]
    return stage_2_batches


def build_operation_partition(
    batch_list_on_stage: list[tuple[JobMcType, ...]],
    *,
    unfixed_batch_start_idx: int,
    unfixed_batch_count: int,
    left_profile_fixed_batch_count: int = 0,
    right_profile_fixed_batch_count: int = 0,
) -> OperationPartition:
    """Slice a single stage's batch list into the 5 partition regions."""
    l_tf: list[JobMcType] = []
    l_pf: list[JobMcType] = []
    unfixed: list[JobMcType] = []
    r_pf: list[JobMcType] = []
    r_tf: list[JobMcType] = []

    left_pf_start = unfixed_batch_start_idx - left_profile_fixed_batch_count
    right_pf_end = (
        unfixed_batch_start_idx + unfixed_batch_count + right_profile_fixed_batch_count
    )

    for idx, batch in enumerate(batch_list_on_stage):
        if idx < left_pf_start:
            l_tf.extend(batch)
        elif idx < unfixed_batch_start_idx:
            l_pf.extend(batch)
        elif idx < unfixed_batch_start_idx + unfixed_batch_count:
            unfixed.extend(batch)
        elif idx < right_pf_end:
            r_pf.extend(batch)
        else:
            r_tf.extend(batch)

    return OperationPartition(
        left_time_fixed=tuple(sorted(l_tf)),
        left_profile_fixed=tuple(sorted(l_pf)),
        unfixed=tuple(sorted(unfixed)),
        right_profile_fixed=tuple(sorted(r_pf)),
        right_time_fixed=tuple(sorted(r_tf)),
    )


def validate_and_get_batch_count(
    stage_2_batch_list: dict[StageIdType, list[tuple[JobMcType, ...]]],
) -> int:
    """Return the common batch count across stages, or raise."""
    counts = {stage: len(batches) for stage, batches in stage_2_batch_list.items()}
    unique = set(counts.values())
    if len(unique) > 1:
        raise ValueError(
            f"SW-CP requires identical batch counts across stages, got {counts}."
        )
    return next(iter(unique), 0)
