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
    # Per-seed (elapsed_time, obj_value) trajectory when Phase 2 runs the
    # cumulative heuristic. Empty when Phase 2 uses CP-SAT.
    heuristic_progress_per_seed: dict[str, list[tuple[float, float]]] = field(
        default_factory=dict
    )
    # Per-seed per-job-scan timing stats: {"mean_sec", "max_sec", "n_scans"}.
    heuristic_scan_stats_per_seed: dict[str, dict] = field(default_factory=dict)
    chosen_seed_tag: str | None = None

    # Populated when a subroutine runs with one of the
    # ``adjust_(p|r)_by_full_sch_and_last_stage_(only_pmtn|only)_sch=True``
    # knobs. The two reference-schedule sources are mutually exclusive
    # within a single call (enforced by the controller), and exactly one
    # of ``adjust_params_last_stage_only_pmtn_makespan`` /
    # ``adjust_params_last_stage_only_makespan`` is populated:
    # - ``adjust_params_last_stage_only_pmtn_makespan`` is set when an
    #   ``_only_pmtn_sch`` knob fires, reading
    #   ``mcf_preemptive_schedule.makespan`` (the preemptive MCF
    #   relaxation schedule).
    # - ``adjust_params_last_stage_only_makespan`` is set when an
    #   ``_only_sch`` knob fires, reading
    #   ``last_stage_only_sol.schedule.makespan`` (the non-preemptive
    #   last-stage-only heuristic schedule).
    # ``adjust_p`` and ``adjust_r`` (within the same source family)
    # share the same makespan triple, so only a single set is stored.
    # Per-knob increments are recorded separately:
    # ``adjust_p_increment_added`` equals ``ceil(delta * m_last / n)``
    # (added to ``p_increment``); ``adjust_r_increment_added`` equals
    # ``makespan_delta`` (added straight to ``r_increment``).
    adjust_params_last_stage_only_pmtn_makespan: int | None = None
    adjust_params_last_stage_only_makespan: int | None = None
    adjust_params_incumbent_makespan: int | None = None
    adjust_params_makespan_delta: int | None = None
    adjust_p_increment_added: int | None = None
    adjust_r_increment_added: int | None = None
