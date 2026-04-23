"""Per-phase diagnostic record for the MCF-LB pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["MCFLBDiagnostic"]


@dataclass(slots=True)
class MCFLBDiagnostic:
    """Per-phase value/time diagnostic for the MCF-LB pipeline.

    Populated incrementally as the pipeline progresses, so partial data
    survives an early return on infeasibility.
    """

    mcf_lb: float | None = None
    # Aggregate last-stage fields reflect the *chosen* seed candidate.
    last_stage_only_obj: float | None = None
    # CP-SAT best_objective_bound from the last-stage-only model.
    # Valid as a global LB only if not profile-fixed by any means.
    # At the moment, the bound is not a global LB since profile-fixing is applied
    last_stage_only_bound: float | None = None
    dispatched_obj: float | None = None
    profile_fix_obj: float | None = None
    profile_fix_bound: float | None = None
    mcf_solve_sec: float | None = None
    # Sum of per-seed last-stage CP-SAT solve times.
    last_stage_cp_sat_sec: float | None = None
    dispatch_sec: float | None = None
    profile_fix_cp_sat_sec: float | None = None
    reached_phase: str = "init"
    # Chosen seed's ls solver status name.
    ls_status: str | None = None
    pf_status: str | None = None
    single_stage: bool = False
    # Per-seed last-stage diagnostics.
    ls_status_per_seed: dict[str, str] = field(default_factory=dict)
    last_stage_obj_per_seed: dict[str, float] = field(default_factory=dict)
    chosen_seed_tag: str | None = None
