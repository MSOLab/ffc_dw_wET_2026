"""Per-phase diagnostic record for the MCF-LB pipeline."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MCFLBDiagnostic"]


@dataclass(slots=True)
class MCFLBDiagnostic:
    """Per-phase value/time diagnostic for the MCF-LB pipeline.

    Populated incrementally as the pipeline progresses, so partial data
    survives an early return on infeasibility.
    """

    mcf_lb: float | None = None
    last_stage_only_obj: float | None = None
    last_stage_only_bound: float | None = None
    dispatched_obj: float | None = None
    profile_fix_obj: float | None = None
    profile_fix_bound: float | None = None
    mcf_solve_sec: float | None = None
    last_stage_cp_sat_sec: float | None = None
    dispatch_sec: float | None = None
    profile_fix_cp_sat_sec: float | None = None
    reached_phase: str = "init"
    ls_status: str | None = None
    pf_status: str | None = None
    single_stage: bool = False
