"""Per-batch step-log entry for NEH-CP runs."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

__all__ = ["NehCpStepEntry", "trunc4"]


def trunc4(x: float | None) -> float | None:
    if x is None:
        return None
    return math.trunc(x * 10000) / 10000


@dataclass(frozen=True, slots=True, kw_only=True)
class NehCpStepEntry:
    """One incremental-batch entry in the NEH-CP step log."""

    step: int
    elapsed_time: float | None
    TL: float | None
    elapsed_portion: float | None
    sub_obj: float
    sub_obj_lb: float
    gap: float | None
    job_count: int
    makespan: int
    ran_2nd_obj: bool

    def as_dict(self) -> dict:
        return asdict(self)
