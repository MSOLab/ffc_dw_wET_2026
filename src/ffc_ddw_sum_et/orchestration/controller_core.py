"""FFcDWwET subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from routix.constants import SubroutineFlowKeys
from routix.dynamic_data_object import DynamicDataObject
from routix.report import SubroutineReport
from routix.stopping_criteria import StoppingCriteria
from routix.subroutine_controller import SubroutineController

from ..algorithm.base.alg_record import ProgressLogEntry, WorkStatus
from ..algorithm.mcf_lb.diagnostic import (
    BuildFullSchDiagnostic,
    CalcMcfLbAndDeriveFullSchDiagnostic,
    HeuristicLastStageOnlyDiagnostic,
    MCFLBDiagnostic,
)
from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.ffc_schedule import (
    FFcSchedule,
    validate_duration,
    validate_no_overlap,
    validate_precedence,
)
from ..solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from .solution_manager import FFcDDWSolution, FFcDDWSolutionManager
from .subroutine_report import FFcDDWSubroutineReport

MCFLBPhaseSchedule = FFcSchedule | MCFPreemptiveSchedule

RESUME_SEED_STEP_NAME = "resume_seed"
"""Method-context name pushed around the RunMode.RESUME incumbent registration
(see ``seed_resume_incumbent``). Produces a valid ``<idx>-resume_seed`` obj_log
step_label; the single-instance runner suppresses this note when it has merged
the base run's obj_log (whose real prefix-end note already marks the join)."""


def _to_ddo(data: Any) -> Any:
    """Convert raw dicts/lists from YAML to DynamicDataObject."""
    if isinstance(data, dict):
        return DynamicDataObject(data)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return [
            DynamicDataObject(item) if isinstance(item, dict) else item for item in data
        ]
    return data


class FFcDDWSubroutineControllerCore(
    SubroutineController[StoppingCriteria, SubroutineReport]
):
    """Routix subroutine controller for Flexible Flow Shop with Due Date Windows"""

    def __init__(
        self,
        instance: FFcDDWParameters,
        subroutine_flow: Sequence[DynamicDataObject]
        | DynamicDataObject
        | Sequence[dict]
        | dict,
        stopping_criteria: StoppingCriteria | dict,
        time_factor: int = 1,
    ):
        if time_factor < 1:
            raise ValueError(f"time_factor must be >= 1, got {time_factor}")
        self._instance_name = instance.name
        self.logger = logging.getLogger(
            f"ffc_ddw_sum_et.orchestration.controller.{self._instance_name}"
        )
        converted_flow = _to_ddo(subroutine_flow)
        if isinstance(stopping_criteria, dict):
            converted_stopping = StoppingCriteria(stopping_criteria)
        else:
            converted_stopping = stopping_criteria
        super().__init__(
            name=instance.name,
            subroutine_flow=converted_flow,
            stopping_criteria=converted_stopping,
            logger=self.logger,
        )
        self.instance = instance
        # CSR coarse-mode scale bridge. ``1`` for every parent controller
        # (bit-for-bit unchanged behaviour); only the child controller spawned
        # by ``coarsen_solve_reconstruct`` with a ``solve_flow`` sets this to
        # the coarsening ``factor`` so its step methods interpret a coarse
        # completion ``C^c`` as original-scale ``time_factor * C^c`` when they
        # build option payloads / the base CP model. See
        # plans/experiment/20260711/csr_solve_flow.md §3.
        self.time_factor = time_factor
        self.solution_manager = FFcDDWSolutionManager()
        # Prefix step methods that must still run before a resume point (e.g.
        # pure setup that produces state not captured by the restored
        # incumbent). Empty today: the current prefix (mcf_lb / flip / neh_cp)
        # only produces the incumbent + global LB, both restored on resume.
        # See plans/experiment/20260709/resume_from_base.md § 2 "Safety assumption".
        self.method_names_to_run_before_resume: set[str] = set()
        self._define_states()

    def _define_states(self) -> None:
        """Define all state attributes used across subroutine phases."""
        self.mcf_preemptive_schedule: MCFPreemptiveSchedule | None = None
        # Per-entry-point diagnostic slots. Each is populated only by
        # the controller method whose name matches the slot — composite
        # steps record their r1/r2 sub-results on their own diagnostic
        # rather than nesting other diagnostics.
        self.mcf_lb_diagnostic: MCFLBDiagnostic | None = None
        self.heuristic_last_stage_only_diagnostic: (
            HeuristicLastStageOnlyDiagnostic | None
        ) = None
        self.build_full_sch_diagnostic: BuildFullSchDiagnostic | None = None
        self.calc_mcf_lb_and_derive_full_sch_diagnostic: (
            CalcMcfLbAndDeriveFullSchDiagnostic | None
        ) = None
        self.last_stage_only_sol: FFcDDWSolution | None = None
        # `p_increment` value used by the producing step; ``None`` until the
        # step has run. When non-zero, the recorded MCF preemptive schedule
        # / last-stage-only solution belong to an *augmented* problem (last
        # stage processing times inflated by ``p_increment``) and downstream
        # consumers must treat them as such (e.g. MCF LB is not a global LB
        # for the original problem).
        self.mcf_preemptive_sch_p_increment: int | None = None
        self.last_stage_only_sol_p_increment: int | None = None
        # Ordered (name, schedule) pairs per MCF-LB phase, used by the
        # runner to emit numbered progress artifacts (1_mcf_preemptive,
        # 2_last_stage_only_init, ..., 7_final). Only populated entries
        # are appended so early returns retain partial progress.
        self.mcf_lb_phase_schedules: list[tuple[str, MCFLBPhaseSchedule]] = []
        self._mcf_lb_phase_highlight_jobs: dict[str, set[str]] = {}
        # Ordered (name, schedule) pairs per CSR phase (3-Gantt artifact).
        # Populated only when coarsen_solve_reconstruct is called with
        # emit_phase_schedules=True and a solution is found.
        self.csr_phase_schedules: list[tuple[str, FFcSchedule]] = []
        # Per-candidate rows from a CSR solve_flow run (one dict per
        # candidate × reconstruction). Emitted by the single-instance runner
        # as ``<instance>_csr_candidates.csv``. Empty in the legacy CSR path.
        self.csr_candidate_rows: list[dict[str, object]] = []
        # Compact summary of the last CSR solve_flow run (candidate/dedup/drop
        # counts + winner source & objectives). ``None`` in the legacy path.
        # Kept small on purpose (Rules 12/14): bulk per-candidate detail lives
        # in ``csr_candidate_rows`` / the candidates CSV, not here.
        self.csr_solve_flow_summary: dict[str, object] | None = None
        # Headless child controller's solution_manager history from the last
        # CSR solve_flow run. Emitted by the single-instance runner as the
        # coarse-scale ``<instance>_csr_inner_obj_log.json``. None in the
        # legacy CSR path.
        self.csr_child_history: list[Any] | None = None
        # Outer wall-clock around `run()`. Set in the run() override below;
        # the runner reads this for the per-instance summary `elapsedTime`.
        self.total_elapsed_time: float = 0.0  # TODO: apply to routix
        self._optimality_logged: bool = False

    def is_stopping_condition(self, **kwargs: Any) -> bool:
        """Stop when the timelimit is exceeded or optimality is proven."""
        return (
            self.timer.time_over(self.stopping_criteria.timelimit)
            or self._optimality_proven()
        )

    def get_current_valid_lb(self) -> int:
        """Return the running max valid global LB tracked by the solution
        manager (updated on every ``register(...)`` whose report carries a
        non-None ``obj_bound``), rounded up via ``math.ceil`` so the value
        is never weakened. Returns ``0`` (the trivial valid LB for weighted
        earliness/tardiness) when no step has reported a bound yet.

        Soundness invariant: every register site in this codebase that
        emits ``report.obj_bound is not None`` must already gate on
        validity for the original problem (see ``apply_lb_by_mcf``
        controller.py:720-729 and the composite synthesizer at
        controller.py:1485). The getter trusts that gate.
        """
        lb = self.solution_manager.best_obj_bound
        return math.ceil(lb) if lb is not None else 0

    def _optimality_proven_no_log(self) -> bool:
        """``_optimality_proven`` without the transition log; used by
        ``_make_stop_report`` to derive the stop reason without
        double-logging.
        """
        ub = self.solution_manager.best_obj_value
        if ub is None:
            return False
        lb_int = self.get_current_valid_lb()
        ub_int = int(ub)
        if lb_int > ub_int:
            raise ValueError(
                f"{self._instance_name}: MCF global LB ({lb_int}) exceeds "
                f"incumbent UB ({ub_int}); LB or UB is inconsistent."
            )
        return lb_int == ub_int

    def _optimality_proven(self) -> bool:
        """Return True iff ``ceil(valid_lb) == int(best_obj_value)``.

        Raises ``ValueError`` when ``ceil(valid_lb) > int(best_obj_value)``
        — that is an LB-violates-UB inconsistency that should not be
        silently accepted.
        """
        proven = self._optimality_proven_no_log()
        if proven and not self._optimality_logged:
            lb = self.get_current_valid_lb()
            ub = self.solution_manager.best_obj_value
            self.logger.info(
                "_optimality_proven: LB=%d == int(UB)=%d (UB=%.2f)",
                lb,
                int(ub),
                ub,
            )
            self._optimality_logged = True
        return proven

    def _make_stop_report(self, start_elapsed: float | None = None) -> SubroutineReport:
        """Stop-report with elapsed_time measured from start_elapsed (0.0
        when not provided) and obj_bound from the current running best
        valid LB tracked by the solution manager.
        """
        elapsed = time.monotonic() - start_elapsed if start_elapsed is not None else 0.0
        timelimit = self.stopping_criteria.timelimit
        timer_elapsed = self.timer.elapsed_sec
        ub = self.solution_manager.best_obj_value
        lb = self.get_current_valid_lb()
        bound = float(lb) if lb > 0 else None
        if self.timer.time_over(timelimit):
            reason = "timelimit"
        elif self._optimality_proven_no_log():
            reason = "optimality_proven"
        else:
            reason = "unknown"
        self.logger.info(
            "_make_stop_report: reason=%s, subroutine_elapsed=%.3fs, "
            "timer_elapsed=%.3fs/%.3fs, valid_lb=%d, best_ub=%s, bound=%s",
            reason,
            elapsed,
            timer_elapsed,
            timelimit,
            lb,
            f"{ub:.2f}" if ub is not None else "None",
            f"{bound:.2f}" if bound is not None else "None",
        )
        return SubroutineReport(
            elapsed_time=elapsed,
            obj_value=None,
            obj_bound=bound,
        )

    def _wrap_report(
        self,
        report: SubroutineReport,
        *,
        progress_log: tuple[ProgressLogEntry, ...] = (),
    ) -> FFcDDWSubroutineReport:
        """Promote a plain ``SubroutineReport`` to ``FFcDDWSubroutineReport``
        with controller-frame ``start_time`` and ``step_label`` filled.

        ``start_time`` is derived as
        ``self.timer.elapsed_sec - report.elapsed_time``.

        Invariant relied on by this derivation: ``report.elapsed_time`` is
        the duration from step entry to *this* call. All current step
        methods build the report immediately before calling ``_register``,
        with ``elapsed_time = time.monotonic() - start_elapsed`` measured
        at the same point — so step_entry_global ≈ now_global - elapsed_time.
        New step methods that wedge work between ``elapsed_time`` capture
        and ``_register`` will skew ``start_time`` and the resulting obj_log
        timestamps; capture and register together.

        ``progress_log`` is forwarded if the step has a captured trajectory;
        otherwise empty tuple (the aggregator will synthesize a single
        endpoint from ``start_time + elapsed_time``).
        """
        return FFcDDWSubroutineReport(
            elapsed_time=report.elapsed_time,
            obj_value=report.obj_value,
            obj_bound=report.obj_bound,
            start_time=self.timer.elapsed_sec - report.elapsed_time,
            progress_log=progress_log,
            step_label=self._get_call_context_of_current_method(),
        )

    def _register(
        self,
        report: SubroutineReport,
        solution: FFcDDWSolution | None,
        *,
        progress_log: tuple[ProgressLogEntry, ...] = (),
    ) -> bool:
        """Wrap ``report`` into a ``FFcDDWSubroutineReport`` and register it.

        Replaces direct ``self.solution_manager.register(report, sol)`` calls
        in step methods. Single point that always promotes the plain report
        into the project-local subclass — ensures every history entry carries
        ``start_time`` / ``step_label`` for the end-of-run obj_log aggregation.
        """
        wrapped = self._wrap_report(report, progress_log=progress_log)
        return self.solution_manager.register(wrapped, solution)

    def seed_resume_incumbent(
        self,
        solution: FFcDDWSolution,
        *,
        obj_value: float | None,
        obj_bound: float | None,
    ) -> None:
        """Register a base run's restored incumbent as the starting solution of
        a RunMode.RESUME run.

        Wrapped in a pushed method context so the synthesized history entry
        carries a valid ``<idx>-resume_seed`` step_label (an empty context
        would yield ``"ROOT"``, which the obj_log loader rejects). ``elapsed_time
        = 0`` anchors the seed at the current (back-dated) controller clock.
        """
        self._method_context_mgr.push(RESUME_SEED_STEP_NAME)
        try:
            self._register(
                SubroutineReport(
                    elapsed_time=0.0,
                    obj_value=obj_value,
                    obj_bound=obj_bound,
                ),
                solution,
            )
        finally:
            self._method_context_mgr.pop()

    def get_file_path_for_subroutine(self, filename_suffix: str) -> Path:
        """Override: when an `ArtifactLayout` is bound, route per-call-context
        paths into the instance's `progress/` zone instead of the working
        directory. Keeps dynamically-named per-step artifacts (step_log,
        cp-sat solver logs) out of the SSOT-protected `final` zone.
        """
        layout = self._artifact_layout
        if (
            layout is not None
            and self._artifact_scenario_name is not None
            and self._artifact_instance_name is not None
        ):
            filename = self._get_call_context_of_current_method() + filename_suffix
            zone_dir = layout.zone_dir(
                "progress",
                scenario_name=self._artifact_scenario_name,
                instance_name=self._artifact_instance_name,
            )
            return zone_dir / filename
        return super().get_file_path_for_subroutine(filename_suffix)

    def _record_mcf_lb_phase(
        self,
        item: tuple[str, MCFLBPhaseSchedule],
        *,
        highlight_jobs: set[str] | None = None,
    ) -> None:
        """Append one ``(name, schedule)`` tuple to ``mcf_lb_phase_schedules``,
        prefixing ``name`` with the current method's call_context so the
        runner-side artifact filenames sort by subroutine-flow step on disk
        and don't collide across step calls.

        ``highlight_jobs``: optional set of job IDs whose operation bars
        should be rendered thicker/full-opacity in the phase Gantt PNG.
        """
        name, sched = item
        prefixed = self._mcf_lb_phase_name(name)
        self.mcf_lb_phase_schedules.append((prefixed, sched))
        if highlight_jobs is not None:
            self._mcf_lb_phase_highlight_jobs[prefixed] = highlight_jobs

    def _record_mcf_lb_phases(
        self, items: Iterable[tuple[str, MCFLBPhaseSchedule]]
    ) -> None:
        """Bulk variant of ``_record_mcf_lb_phase`` for sub-call results
        (e.g. ``result.intermediate_schedules``).
        """
        prefix = self._get_call_context_of_current_method()
        self.mcf_lb_phase_schedules.extend(
            (f"{prefix}_{name}", sched) for name, sched in items
        )

    def _mcf_lb_phase_name(self, local_name: str) -> str:
        return f"{self._get_call_context_of_current_method()}_{local_name}"

    def _record_csr_phase(self, name: str, sched: FFcSchedule) -> None:
        """Append one ``(name, schedule)`` tuple to ``csr_phase_schedules``,
        prefixing ``name`` with the current method's call_context so the
        runner-side artifact filenames sort by subroutine-flow step on disk
        and don't collide across step calls.
        """
        prefixed = f"{self._get_call_context_of_current_method()}_{name}"
        self.csr_phase_schedules.append((prefixed, sched))

    def try_get_file_path_for_subroutine(self, suffix: str) -> Path | None:
        """Like ``get_file_path_for_subroutine`` but returns ``None`` instead
        of raising when no working directory is configured.

        Use for optional artifact emission (e.g. ``_step_log.yaml``) that
        should be silently skipped in tests or scripted runs without a
        working directory or layout.
        """
        if self._artifact_layout is None and self._working_dir_path is None:
            return None
        return self.get_file_path_for_subroutine(suffix)

    def run(self, flow_resume_idx: int = -1) -> None:
        """Wrap routix's run loop with an outer wall-clock measurement.

        When ``flow_resume_idx > 0`` this is a resume run: the first
        ``flow_resume_idx`` steps are skipped (their outcome was restored from a
        base run's incumbent), except any step whose method is in
        ``method_names_to_run_before_resume``, which is re-run. Steps from
        ``flow_resume_idx`` onward run normally. See
        plans/experiment/20260709/resume_from_base.md.
        """
        start = time.monotonic()
        try:
            flow = self._subroutine_flow
            is_seq = isinstance(flow, Sequence) and not isinstance(flow, (str, bytes))
            if flow_resume_idx > 0 and is_seq:
                for step in flow[:flow_resume_idx]:
                    method_name, _ = SubroutineFlowKeys.parse_step(step.to_obj())
                    run_before = method_name in self.method_names_to_run_before_resume
                    self._run_flow(step, skip_method_call=not run_before)
                self._run_flow(flow[flow_resume_idx:])
                self.post_run_process()
            else:
                if flow_resume_idx > 0 and not is_seq:
                    self.logger.warning(
                        "run: flow_resume_idx=%d but subroutine_flow is not a "
                        "Sequence — resume index ignored; running full flow",
                        flow_resume_idx,
                    )
                super().run()
        finally:  # TODO: apply to routix
            self.total_elapsed_time = time.monotonic() - start

    def check_feasibility(
        self, start_time_map: dict[tuple[str, str, str], int]
    ) -> None:
        """Validate structural feasibility of a complete schedule given by
        ``start_time_map``.

        The objective value (wET) is **not** computed here; that lives in
        ``solution.objectives.compute_weighted_earliness_tardiness`` and is
        attached to ``FFcDDWSolution.obj_value`` upstream.

        Checks (in order, fail-fast):

        1. ``start_time >= 0`` for every entry.
        2. ``j`` is a known job in ``instance.job_id_list``.
        3. ``i`` is a known stage in ``instance.stage_id_list``.
        4. ``k`` is a machine that exists at stage ``i`` according to
           ``instance.stage_2_machines_map[i]``.
        5. No ``(j, i)`` pair appears more than once.
        6. Every ``(j, i)`` in ``job_id_list × stage_id_list`` appears at
           least once (no missing operation).
        7. ``end_time - start_time == p_{j,i}`` for every entry
           (``validate_duration``).
        8. For every job, each stage's start time is no earlier than the
           previous stage's end time (``validate_precedence``).
        9. No two operations overlap on the same machine
           (``validate_no_overlap``).

        Args:
            start_time_map: Mapping from ``(job, stage, machine)`` to the
                operation's start time. Must cover every ``(job, stage)``
                pair in the instance exactly once.

        Raises:
            ValueError: when any of the checks above fails. The message
                identifies the offending entry (or set of missing pairs).
        """
        self.logger.info("Feasibility check starts")

        instance = self.instance
        job_id_set = set(instance.job_id_list)
        stage_id_set = set(instance.stage_id_list)
        stage_2_machine_set = {
            stage_id: set(machines)
            for stage_id, machines in instance.stage_2_machines_map.items()
        }
        job_2_stage_2_p = instance.job_2_stage_2_p_map

        end_time_map: dict[tuple[str, str, str], int] = {}
        seen_pairs: set[tuple[str, str]] = set()
        for (j, i, k), start_time in start_time_map.items():
            if start_time < 0:
                raise ValueError(
                    f"Invalid start time for job {j}, stage {i}, "
                    f"machine {k}: {start_time}"
                )
            if j not in job_id_set:
                raise ValueError(
                    f"Unknown job {j} in start_time_map entry ({j}, {i}, {k})"
                )
            if i not in stage_id_set:
                raise ValueError(
                    f"Unknown stage {i} in start_time_map entry ({j}, {i}, {k})"
                )
            if k not in stage_2_machine_set[i]:
                raise ValueError(
                    f"Machine {k} is not available at stage {i} "
                    f"(entry: job {j}, stage {i}, machine {k})"
                )
            if (j, i) in seen_pairs:
                raise ValueError(f"Job {j} scheduled multiple times at stage {i}")
            seen_pairs.add((j, i))
            end_time_map[(j, i, k)] = start_time + job_2_stage_2_p[j][i]

        expected_pairs = {
            (j, i) for j in instance.job_id_list for i in instance.stage_id_list
        }
        missing_pairs = expected_pairs - seen_pairs
        if missing_pairs:
            raise ValueError(
                f"Missing (job, stage) operations in start_time_map: "
                f"{sorted(missing_pairs)}"
            )

        validate_duration(
            start_time_map, end_time_map, self.instance.stage_2_job_2_p_map
        )
        validate_precedence(start_time_map, end_time_map, self.instance.stage_id_list)
        validate_no_overlap(
            start_time_map,
            end_time_map,
            self.instance.stage_id_list,
            self.instance.stage_2_machines_map,
        )

        self.logger.info("Feasibility check passed")

    def post_run_process(self) -> None:
        """Validate the incumbent's structural feasibility, if any."""
        incumbent = self.solution_manager.get_incumbent()
        if incumbent is not None:
            self.check_feasibility(incumbent.schedule.get_jik_2_start_time_map())

    @property
    def best_solution(self) -> FFcDDWSolution | None:
        return self.solution_manager.get_incumbent()

    @property
    def best_obj_value(self) -> float | None:
        return self.solution_manager.best_obj_value

    @property
    def work_status(self) -> WorkStatus | None:
        if not self.solution_manager.history:
            return None
        last_record = self.solution_manager.history[-1]
        if last_record.report is None or last_record.solution is None:
            return None
        return WorkStatus.FEASIBLE
