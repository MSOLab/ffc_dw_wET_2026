"""Per-entrypoint diagnostic records for the MCF-LB pipeline.

Each dataclass below corresponds 1:1 to a controller method that may be
invoked directly from a config flow. The composite step
(``calc_mcf_lb_and_derive_full_sch``) carries r1/r2 sub-results as flat
fields on its own diagnostic, rather than nesting other diagnostics —
so a single controller call produces exactly one diagnostic instance,
and the same data is never written from multiple steps.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MCFLBDiagnostic",
    "HeuristicLastStageOnlyDiagnostic",
    "BuildFullSchDiagnostic",
    "CalcMcfLbAndDeriveFullSchDiagnostic",
]


@dataclass(slots=True)
class MCFLBDiagnostic:
    """Diagnostic for ``apply_lb_by_mcf``.

    Records what was solved and which knob values were *effectively* used
    (after any caller-side adjust-by-gap computation has been folded into
    ``p_increment``/``r_multiplier``/``r_increment``). The validity of
    ``mcf_lb`` as a global LB is judged from these recorded inputs alone,
    without cross-step lookups.
    """

    mcf_lb: float | None = None
    mcf_solve_sec: float | None = None
    p_increment_used: int = 0
    r_multiplier_used: float = 1.0
    r_increment_used: int = 0

    @property
    def mcf_lb_is_valid_for_main_problem(self) -> bool:
        """``mcf_lb`` is a valid global LB on the original instance iff
        no knob was used to inflate the relaxation: ``p_increment == 0``,
        ``r_multiplier <= 1`` (looser releases keep validity), and
        ``r_increment == 0``.
        """
        if self.mcf_lb is None:
            return False
        return (
            self.p_increment_used == 0
            and self.r_multiplier_used <= 1.0
            and self.r_increment_used == 0
        )


@dataclass(slots=True)
class HeuristicLastStageOnlyDiagnostic:
    """Diagnostic for ``heuristic_last_stage_only_sch_from_mcf_lb``.

    Created only when the controller method is invoked directly from a
    config flow. Composite invocations record their r1/r2 heuristic
    sub-results on ``CalcMcfLbAndDeriveFullSchDiagnostic`` instead.
    """

    status: str | None = None
    obj_value: float | None = None
    elapsed_sec: float | None = None
    p_increment_used: int = 0
    r_multiplier_used: float = 1.0
    r_increment_used: int = 0
    # Adjust-knob bookkeeping (populated only when an
    # ``adjust_*_by_full_sch_and_last_stage_*`` knob fired in this call).
    # Exactly one of ``last_stage_only_pmtn_makespan`` /
    # ``last_stage_only_makespan`` is populated per call (mutually
    # exclusive reference-schedule sources).
    incumbent_makespan: int | None = None
    last_stage_only_pmtn_makespan: int | None = None
    last_stage_only_makespan: int | None = None
    makespan_delta: int | None = None  # max(incumbent - ls_only, 0)
    p_increment_added: int | None = None
    r_increment_added: int | None = None


@dataclass(slots=True)
class BuildFullSchDiagnostic:
    """Diagnostic for ``build_full_sch_from_last_stage_only_sch``."""

    dispatched_obj: float | None = None
    full_sch_makespan: int | None = None
    dispatch_sec: float | None = None


@dataclass(slots=True)
class CalcMcfLbAndDeriveFullSchDiagnostic:
    """Diagnostic for ``calc_mcf_lb_and_derive_full_sch``.

    Owns r1/r2 raw sub-results as flat fields. ``makespan_delta`` is the
    raw signed delta (``r1_full_sch_makespan - ref_makespan``) recorded
    *before* the round-2 skip decision, so a non-positive delta is
    captured rather than dropped. The reference makespan source is
    recorded on ``makespan_delta_ref_used``: ``"mcfLbMakespan"`` uses
    ``r1_ls_only_pmtn_makespan`` as the ref; ``"lastStageOnlyMakespan"``
    uses ``r1_ls_only_makespan``. Both makespans are populated whenever
    their source schedule exists, regardless of which one was used for
    the delta.
    """

    # r1 always runs.
    r1_mcf_lb: float | None = None
    r1_mcf_solve_sec: float | None = None
    r1_ls_only_pmtn_makespan: int | None = None
    # Non-preemptive last-stage-only schedule makespan from the r1
    # heuristic. Populated whenever ``r1.heuristic`` is non-None.
    r1_ls_only_makespan: int | None = None
    r1_full_sch_makespan: int | None = None
    r1_full_sch_obj: float | None = None
    # Reference-makespan mode used for the delta computation:
    # "mcfLbMakespan" | "lastStageOnlyMakespan" | None (None when r1 did
    # not reach the delta computation).
    makespan_delta_ref_used: str | None = None
    # Raw signed delta; <= 0 is recorded as-is.
    makespan_delta: int | None = None
    r2_ran: bool = False
    # When r2 did not run, why: "delta_le_0" | "stop_guard" | "no_adjust"
    # | "s1_none" | None (None when r2 ran).
    r2_skip_reason: str | None = None
    # Round-2 last-stage seed policy actually used:
    # "original_pr" | "increased_pr" | "best" | None (None when r2 did not run).
    last_stage_rebuild_config_used: str | None = None
    # r2 fields populated only when r2 actually ran.
    r2_mcf_lb: float | None = None
    r2_mcf_solve_sec: float | None = None
    r2_ls_only_pmtn_makespan: int | None = None
    r2_full_sch_makespan: int | None = None
    r2_full_sch_obj: float | None = None
    r2_p_increment_added: int | None = None
    r2_r_increment_added: int | None = None
    # Final chosen result.
    final_obj: float | None = None
    # ``r1_mcf_lb`` is always a valid global LB (no adjust on r1), so
    # it's the bound the composite reports. Captured separately so the
    # serialized record is self-contained.
    final_obj_bound: float | None = None
    elapsed_sec: float | None = None
