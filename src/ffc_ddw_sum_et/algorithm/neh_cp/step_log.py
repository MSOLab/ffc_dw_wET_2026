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
    """One incremental-batch entry in the NEH-CP step log.

    ``dispatched_obj`` / ``cp_obj`` / ``semi_active_obj`` capture the
    weighted E+T of the three intermediate solutions per step:
      - ``dispatched_obj``: schedule built by the warm-start dispatcher
        (post-IIT) and fed into the CP-SAT model as hints.
      - ``cp_obj``: CP-SAT solver's raw output schedule (or ``None`` if
        the solver returned no feasible solution within its budget).
      - ``semi_active_obj``: weighted E+T after applying
        ``make_semi_active`` + ``insert_idle_time`` to the CP-SAT
        schedule. ``None`` when semi-active rebuild was not applied
        (either because the boolean flag is False *and* the threshold
        gate is off, or because the threshold gate said "skip").
    """

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
    dispatched_obj: float
    cp_obj: float | None
    semi_active_obj: float | None

    def as_dict(self) -> dict:
        return asdict(self)
