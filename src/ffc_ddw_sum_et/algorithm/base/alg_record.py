"""Execution record contract for algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from ...solution.ffc_schedule import FFcSchedule
from .alg_option import AlgOption

__all__ = [
    "AlgResult",
    "AlgRecord",
    "ProgressLogEntry",
    "TerminationReason",
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
    STOP_REQUESTED = "stop_requested"
    ERROR = "error"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgressLogEntry:
    """Machine-readable progress snapshot for one run."""

    elapsed_sec: float
    obj_value: int | float | None = None
    obj_bound: int | float | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AlgResult:
    """Primary result payload for one algorithm run.

    ``metrics`` carries algorithm-specific auxiliary data; the value type is
    intentionally ``Any`` so individual algorithms can publish structured
    payloads (e.g. NEH-CP per-batch step entries) without each one carving
    out a bespoke top-level field on ``AlgRecord``.
    """

    schedule: FFcSchedule | None = None
    obj_value: int | float | None = None
    obj_bound: int | float | None = None
    metrics: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AlgRecord:
    """Immutable record for one algorithm execution."""

    work_status: WorkStatus
    instance_id: str | None = None
    algorithm_id: str | None = None
    option: AlgOption | None = None
    result: AlgResult | None = None
    progress_log: tuple[ProgressLogEntry, ...] | None = None
    termination_reason: TerminationReason | None = None
    error: str | None = None
