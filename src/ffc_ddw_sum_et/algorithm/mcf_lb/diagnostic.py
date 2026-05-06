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

    @property
    def mcf_lb_is_valid_for_main_problem(self) -> bool:
        """Whether ``mcf_lb`` is a valid global lower bound on the
        *original* (un-augmented) instance, judged only from the
        ``adjust_*_increment_added`` fields recorded on this diagnostic.

        Returns ``True`` when:
          - ``mcf_lb`` is set, **and**
          - neither adjust knob fired (both ``adjust_p_increment_added``
            and ``adjust_r_increment_added`` are ``None``), **or** every
            knob that fired added a non-positive increment (``<= 0``).

        Returns ``False`` when:
          - ``mcf_lb`` is ``None``, **or**
          - any positive ``adjust_*_increment_added`` is recorded — the
            adjustment inflated processing time / pushed releases, so the
            MCF objective bounds the *augmented* problem only.

        Important caveat — direct kwargs are NOT inspected:
          The property only reads the post-fact ``adjust_*_increment_added``
          fields populated by the ``adjust_*_by_full_sch_and_last_stage_*``
          knobs on ``apply_lb_by_mcf`` /
          ``heuristic_last_stage_only_sch_from_mcf_lb``. It does **not**
          look at the direct ``p_increment``, ``r_increment``, or
          ``r_multiplier`` arguments those callers may pass. If a caller
          invokes ``apply_lb_by_mcf(p_increment=k>0)`` (or
          ``r_increment > 0``, or ``r_multiplier > 1.0``) without firing
          an adjust knob, this property still returns ``True`` even though
          the MCF objective is no longer a global LB on the original
          instance. Today both call sites of ``apply_lb_by_mcf``
          (``calc_mcf_lb_and_derive_full_sch`` round-1 and round-2) leave
          the direct kwargs at their defaults, so the gap is theoretical;
          but a future caller that uses those kwargs must either persist
          its own validity flag or extend this property.
        """
        if self.mcf_lb is None:
            return False
        if (
            self.adjust_p_increment_added is None
            and self.adjust_r_increment_added is None
        ):
            return True
        if (
            self.adjust_p_increment_added is not None
            and self.adjust_p_increment_added > 0
        ):
            return False
        if (
            self.adjust_r_increment_added is not None
            and self.adjust_r_increment_added > 0
        ):
            return False
        return True
