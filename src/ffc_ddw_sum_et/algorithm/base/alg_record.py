"""Execution record contract for algorithms."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ...solution.ffc_schedule import FFcSchedule
from .alg_option import AlgOption

__all__ = [
    "AlgResult",
    "AlgRecord",
    "ProgressLogEntry",
    "TerminationReason",
    "TimingInfo",
    "WorkStatus",
]


class WorkStatus(StrEnum):
    """Outcome semantics for a single algorithm run."""

    FEASIBLE = "feasible"
    OPTIMAL = "optimal"
    INFEASIBLE = "infeasible"
    ERROR = "error"


class TerminationReason(StrEnum):
    """Reason why a run stopped."""

    COMPLETED = "completed"
    TIME_LIMIT = "time_limit"
    INTERRUPTED = "interrupted"
    ERROR = "error"


@dataclass(frozen=True, slots=True, kw_only=True)
class TimingInfo:
    """Timing information for one execution."""

    wall_ms: float
    cpu_ms: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgressLogEntry:
    """Machine-readable progress snapshot for one run."""

    elapsed_ms: float
    obj_value: int | float | None = None
    obj_bound: int | float | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AlgResult:
    """Primary result payload for one algorithm run."""

    schedule: FFcSchedule | None = None
    obj_value: int | float | None = None
    obj_bound: int | float | None = None
    metrics: Mapping[str, int | float] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AlgRecord:
    """Immutable record for one algorithm execution."""

    work_status: WorkStatus
    instance_id: str | None = None
    algorithm_id: str | None = None
    option: AlgOption | None = None
    result: AlgResult | None = None
    timing: TimingInfo | None = None
    progress_log: tuple[ProgressLogEntry, ...] | None = None
    termination_reason: TerminationReason | None = None
    error: str | None = None
