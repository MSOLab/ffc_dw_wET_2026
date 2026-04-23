"""Structured result payload for ``MCFLB``."""

from __future__ import annotations

from dataclasses import dataclass

from ...solution.ffc_schedule import FFcSchedule
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from .diagnostic import MCFLBDiagnostic

__all__ = ["MCFLBResult"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MCFLBResult:
    """Side-car result bundle produced by one ``MCFLB.run`` call.

    ``AlgRecord.result`` carries only the primary schedule per the
    algorithm contract. ``MCFLBResult`` exposes every phase's artifact so
    the controller layer can register intermediate incumbents and keep the
    diagnostic schedules that are not structurally valid as full
    incumbents.
    """

    # Progress diagnostics
    diagnostic: MCFLBDiagnostic

    # Schedules - incomplete
    mcf_preemptive_schedule: MCFPreemptiveSchedule | None = None
    last_stage_only_init_schedule: FFcSchedule | None = None
    last_stage_only_schedule: FFcSchedule | None = None
    last_stage_only_schedule_flipped: FFcSchedule | None = None

    # Schedules - complete
    dispatched_schedule_before_unflipping: FFcSchedule | None = None
    dispatched_schedule: FFcSchedule | None = None
    final_schedule: FFcSchedule | None = None

    # Results
    obj_value: float | None = None
    obj_bound: float | None = None
