"""FFcDWwET subroutine controller for routix-based experiment orchestration."""

import csv
import math
import time
from pathlib import Path
from typing import Callable, Literal, Sequence

from ortools.sat.python import cp_model
from routix.io import dump_yaml
from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.base.alg_record import TerminationReason
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
    CoarsenSolveReconstructOption,
    run_coarsen_solve_reconstruct,
)
from ffc_ddw_sum_et.algorithm.cpsat_adapter import CpsatAdapter, CpsatOption
from ffc_ddw_sum_et.algorithm.cumulative import (
    BaseModelBuilder,
    PFMethod,
    decode_pf_method,
)
from ffc_ddw_sum_et.algorithm.dispatcher import (
    BN2DDispatcher,
    BN2DOption,
    MixedDispatcher,
)
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.algorithm.flip_makespan_cp import (
    FlipMakespanCpDispatcher,
    FlipMakespanCpOption,
)
from ffc_ddw_sum_et.algorithm.mcf_lb import (
    BuildFullSchDiagnostic,
    CalcMcfLbAndDeriveFullSchDiagnostic,
    CalcMcfLbAndDeriveFullSchResult,
    HeuristicLastStageOnlyDiagnostic,
    MCFLBDiagnostic,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.full_sch_builder import (
    build_full_sch_from_last_stage_only_sch as algo_build_full_sch_from_last_stage_only_sch,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.last_stage_sch_builder import (
    heuristic_last_stage_only_from_mcf_lb,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import (
    MCFLBStopRequested,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.lb_last_stage_pmtn import (
    apply_lb_by_mcf as algo_apply_lb_by_mcf,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.mcf_lb_pipeline import (
    calc_mcf_lb_and_derive_full_sch as algo_calc_mcf_lb_and_derive_full_sch,
)
from ffc_ddw_sum_et.algorithm.neh_cp import (
    NehCpDispatcher,
    NehCpJobPriority,
    NehCpOption,
)
from ffc_ddw_sum_et.algorithm.pm_pmtn_sorter import PmPrmpSortKey
from ffc_ddw_sum_et.algorithm.pw_cp import (
    PwCpDispatcher,
    PwCpOption,
)
from ffc_ddw_sum_et.algorithm.step_tl_resolver import BatchTlMode
from ffc_ddw_sum_et.io import dump_preemptive_schedule_json, dump_solution_json
from ffc_ddw_sum_et.io.parallel_mc_cost_heatmap import HeatmapSort
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.parameters.sorter import (
    DispatchSeqKey,
    dispatch_seq_job_sequence,
)
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ffc_ddw_sum_et.solution.objectives import (
    compute_phase_obj_value,
    compute_weighted_earliness_tardiness,
)
from ffc_ddw_sum_et.solution.schedule_build import (
    build_schedule_from_op_starts,
    reconstruct_coarse_schedule,
)

from .controller_core import FFcDDWSubroutineControllerCore, MCFLBPhaseSchedule
from .mcf_lb_phase_labels import (
    MCF_LB_R1_LABEL_ORDER,
    MCF_LB_R2_LABEL_ORDER,
)
from .solution_manager import FFcDDWSolution
from .value_resolver import resolve_value_expr

__all__ = ["FFcDDWSubroutineController", "MCFLBDiagnostic", "NehCpJobPriority"]


# Maps unprefixed phase labels emitted by
# ``build_full_sch_from_last_stage_only_sch`` (algorithm-side) onto the
# numbered prefixes the controller records on
# ``mcf_lb_phase_schedules`` (and therefore the on-disk filenames).
_BUILD_FULL_SCH_LABEL_TO_INDEX: dict[str, int] = {
    "lastS_only_before_rs": 4,
    "lastS_only_after_rs": 5,
    "lastS_only_flipped": 6,
    "fullS_before_unflip": 7,
    "fullS_after_unflip": 8,
    "fullS_after_sa_iti": 9,
}


class FFcDDWSubroutineController(FFcDDWSubroutineControllerCore):
    def run_fam(self, job_sequence: Sequence[str] | None = None) -> SubroutineReport:
        """Step method: run FAMDispatcher and return a SubroutineReport.

        Args:
            job_sequence: Full permutation of instance job IDs. Must include every
                instance job exactly once. When omitted, the instance's native
                ``job_id_list`` order is used.
        """
        start_elapsed = time.monotonic()

        if job_sequence is None:
            option = FAMOption()
        else:
            option = FAMOption(job_sequence=tuple(job_sequence))

        spec = AlgSpec(
            instance=self.instance,
            option=option,
            logger=self.logger,
        )

        record = FAMDispatcher().run(spec)
        elapsed = time.monotonic() - start_elapsed

        result = record.result
        obj_value = (
            float(result.obj_value) if result and result.obj_value is not None else None
        )
        obj_bound = (
            float(result.obj_bound) if result and result.obj_bound is not None else None
        )

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=obj_bound,
        )

        if result is not None and result.schedule is not None:
            fam_solution = FFcDDWSolution(
                schedule=result.schedule,
                obj_value=obj_value,
                obj_bound=obj_bound,
            )
            self._register(report, fam_solution)
        else:
            self._register(report, None)

        return report

    def run_bn2d(
        self,
        left_cap_multiplier: int | None = None,
        right_cap_multiplier: int | None = None,
        left_cap_portion: float | None = None,
        right_cap_portion: float | None = None,
        normalize_by_stage_cnt: bool = False,
        randomize_mid_all: bool = False,
        reverse_mid_even: bool = False,
        reverse_mid_all: bool = False,
        mixed_schedule_for_former_stages: bool = False,
        mixed_schedule_for_later_stages: bool = False,
        machine_then_job: bool = False,
        all_stages_as_bottleneck: bool = False,
        random_seed: int | None = None,
        solver_thread_cnt: int = 1,
        iit_after_dispatch: bool = False,
    ) -> SubroutineReport:
        """Step method: run BN2DDispatcher and return a SubroutineReport.

        BN2D internally minimises makespan (the upstream algorithm's objective);
        the returned ``obj_value`` is weighted earliness+tardiness (this
        project's primary objective), while makespan is kept in
        ``AlgResult.metrics``.
        """
        start_elapsed = time.monotonic()

        option = BN2DOption(
            left_cap_multiplier=left_cap_multiplier,
            right_cap_multiplier=right_cap_multiplier,
            left_cap_portion=left_cap_portion,
            right_cap_portion=right_cap_portion,
            normalize_by_stage_cnt=normalize_by_stage_cnt,
            randomize_mid_all=randomize_mid_all,
            reverse_mid_even=reverse_mid_even,
            reverse_mid_all=reverse_mid_all,
            mixed_schedule_for_former_stages=mixed_schedule_for_former_stages,
            mixed_schedule_for_later_stages=mixed_schedule_for_later_stages,
            machine_then_job=machine_then_job,
            all_stages_as_bottleneck=all_stages_as_bottleneck,
            random_seed=random_seed,
            solver_thread_cnt=solver_thread_cnt,
            iit_after_dispatch=iit_after_dispatch,
        )

        spec = AlgSpec(
            instance=self.instance,
            option=option,
            logger=self.logger,
        )

        record = BN2DDispatcher().run(spec)
        elapsed = time.monotonic() - start_elapsed

        result = record.result
        obj_value = (
            float(result.obj_value) if result and result.obj_value is not None else None
        )
        obj_bound = (
            float(result.obj_bound) if result and result.obj_bound is not None else None
        )

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=obj_bound,
        )

        if result is not None and result.schedule is not None:
            bn2d_solution = FFcDDWSolution(
                schedule=result.schedule,
                obj_value=obj_value,
                obj_bound=obj_bound,
            )
            self._register(report, bn2d_solution)
        else:
            self._register(report, None)

        return report

    def _get_schedule_by_best_of_mixed_dispatches(
        self,
        *,
        machine_then_job: bool = False,
        head_for_all_stages: bool = False,
    ) -> dict[str, FFcSchedule | None]:
        """Build a map of candidate schedules from CDS / Gupta / Palmer on the
        forward instance and the stage-reversed instance.

        The reversed-instance candidates are mapped back to forward stage
        indexing via :meth:`FFcSchedule.as_reversed` followed by
        :meth:`FFcSchedule.make_semi_active` using the forward stage/job
        processing-time map.
        """
        instance = self.instance
        fwd_mixed = MixedDispatcher(instance, logger=self.logger)

        reversed_instance = FFcDDWParameters.reverse_stages(instance)
        rev_mixed = MixedDispatcher(reversed_instance, logger=self.logger)

        stage_2_job_2_p = instance.stage_2_job_2_p_map
        method_pairs = [
            ("cds", fwd_mixed.get_schedule_by_cds, rev_mixed.get_schedule_by_cds),
            ("gupta", fwd_mixed.get_schedule_by_gupta, rev_mixed.get_schedule_by_gupta),
            (
                "palmer",
                fwd_mixed.get_schedule_by_palmer,
                rev_mixed.get_schedule_by_palmer,
            ),
        ]

        candidates: dict[str, FFcSchedule | None] = {}
        for name, fwd_fn, rev_fn in method_pairs:
            fwd_sch = fwd_fn(
                machine_then_job=machine_then_job,
                head_for_all_stages=head_for_all_stages,
            )
            candidates[f"mixed.{name}"] = fwd_sch

            rev_sch = rev_fn(
                machine_then_job=machine_then_job,
                head_for_all_stages=head_for_all_stages,
            )
            if rev_sch is not None:
                converted = rev_sch.as_reversed()
                converted.make_semi_active(stage_2_job_2_p)
                candidates[f"mixed.{name}_rev"] = converted
            else:
                candidates[f"mixed.{name}_rev"] = None
        return candidates

    def initialize_by_best_of_selected_dispatches(
        self,
        left_cap_multiplier: int | None = None,
        right_cap_multiplier: int | None = None,
        left_cap_portion: float | None = None,
        right_cap_portion: float | None = None,
        normalize_by_stage_cnt: bool = False,
        randomize_mid_all: bool = False,
        reverse_mid_even: bool = False,
        reverse_mid_all: bool = False,
        mixed_schedule_for_former_stages: bool = False,
        mixed_schedule_for_later_stages: bool = False,
        machine_then_job: bool = False,
        all_stages_as_bottleneck: bool = False,
        random_seed: int | None = None,
        error_if_infeasible: bool = False,
        method_list: Sequence[str] | None = None,
        iit_after_each_dispatch: bool = False,
    ) -> SubroutineReport:
        """Seed an incumbent by taking the best of several dispatching
        heuristics (BN2D and/or CDS / Gupta / Palmer on the forward + reversed
        instance).

        ``method_list`` defaults to
        ``["run_bn2d", "select_best_of_mixed_dispatches"]`` — matching the
        upstream ``initialize_by_best_of_selected_dispatches`` step from
        ``hybridflowshop``. Unknown method names are logged and skipped.

        When ``iit_after_each_dispatch`` is ``True``, last-stage idle-time
        insertion is applied to each non-None candidate **before** comparison,
        and candidates are compared by weighted E+T; otherwise comparison is
        by makespan (mirroring the upstream default, which minimizes
        makespan). The reported ``obj_value`` is always weighted E+T of the
        chosen schedule (project convention).
        """
        start_elapsed = time.monotonic()
        instance = self.instance

        methods = (
            list(method_list)
            if method_list
            else [
                "run_bn2d",
                "select_best_of_mixed_dispatches",
            ]
        )

        candidates: dict[str, FFcSchedule | None] = {}
        for name in methods:
            if name == "run_bn2d":
                bn2d_option = BN2DOption(
                    left_cap_multiplier=left_cap_multiplier,
                    right_cap_multiplier=right_cap_multiplier,
                    left_cap_portion=left_cap_portion,
                    right_cap_portion=right_cap_portion,
                    normalize_by_stage_cnt=normalize_by_stage_cnt,
                    randomize_mid_all=randomize_mid_all,
                    reverse_mid_even=reverse_mid_even,
                    reverse_mid_all=reverse_mid_all,
                    mixed_schedule_for_former_stages=mixed_schedule_for_former_stages,
                    mixed_schedule_for_later_stages=mixed_schedule_for_later_stages,
                    machine_then_job=machine_then_job,
                    all_stages_as_bottleneck=all_stages_as_bottleneck,
                    random_seed=random_seed,
                    iit_after_dispatch=False,
                )
                bn2d_spec = AlgSpec(
                    instance=instance,
                    option=bn2d_option,
                    logger=self.logger,
                )
                bn2d_record = BN2DDispatcher().run(bn2d_spec)
                bn2d_result = bn2d_record.result
                candidates["run_bn2d"] = (
                    bn2d_result.schedule if bn2d_result is not None else None
                )
            elif name == "select_best_of_mixed_dispatches":
                candidates.update(
                    self._get_schedule_by_best_of_mixed_dispatches(
                        machine_then_job=machine_then_job,
                        head_for_all_stages=all_stages_as_bottleneck,
                    )
                )
            else:
                self.logger.warning(
                    "initialize_by_best_of_selected_dispatches: "
                    "unknown candidate method '%s'; skipping.",
                    name,
                )

        if iit_after_each_dispatch:
            for sch in candidates.values():
                if sch is None:
                    continue
                sch.insert_idle_time(
                    instance.job_2_due_window_map,
                    instance.job_2_ewt_map,
                    instance.job_2_twt_map,
                )

        best_name = ""
        best_sch: FFcSchedule | None = None
        best_cmp: float | None = None
        for name, sch in candidates.items():
            if sch is None:
                continue
            if iit_after_each_dispatch:
                sum_e, sum_t = compute_weighted_earliness_tardiness(sch, instance)
                cmp = float(sum_e + sum_t)
            else:
                cmp = float(sch.makespan)
            if best_cmp is None or cmp < best_cmp:
                best_cmp = cmp
                best_sch = sch
                best_name = name

        elapsed = time.monotonic() - start_elapsed

        if best_sch is None:
            if error_if_infeasible:
                raise RuntimeError(
                    "initialize_by_best_of_selected_dispatches produced no "
                    f"feasible schedule for instance {instance.name}."
                )
            self.logger.warning(
                "initialize_by_best_of_selected_dispatches: no feasible "
                "candidate; returning empty report."
            )
            report = SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=None
            )
            self._register(report, None)
            return report

        sum_e, sum_t = compute_weighted_earliness_tardiness(best_sch, instance)
        obj_value = float(sum_e + sum_t)
        self.logger.info(
            "initialize_by_best_of_selected_dispatches: best=%s cmp=%s "
            "obj_value(weighted_et)=%s makespan=%s",
            best_name,
            best_cmp,
            obj_value,
            best_sch.makespan,
        )

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,
        )
        self._register(
            report,
            FFcDDWSolution(schedule=best_sch, obj_value=obj_value, obj_bound=None),
        )
        return report

    def _log_effective_release_stats(
        self, caller: str, *, r_multiplier: float, r_increment: int
    ) -> None:
        """Log min/max/mean of the per-job release map that ``solve_mcf_lb``
        and ``heuristic_last_stage_only_from_mcf_lb`` will reconstruct
        internally. Mirrors their formula
        ``ceil(p_sum_except_last_stage(j) * r_multiplier) + r_increment``
        so we can verify the effective r values before the solver runs.
        """
        base = self.instance.get_job_2_p_sum_except_last_stage()
        if r_multiplier != 1.0:
            base = {j: math.ceil(v * r_multiplier) for j, v in base.items()}
        if r_increment != 0:
            base = {j: v + r_increment for j, v in base.items()}
        values = list(base.values())
        n = len(values)
        self.logger.info(
            "%s: effective r stats — min=%d, max=%d, mean=%.1f (n=%d)",
            caller,
            min(values),
            max(values),
            sum(values) / n,
            n,
        )

    def apply_lb_by_mcf(
        self,
        draw_heatmap: bool = False,
        heatmap_sort: HeatmapSort = "due2-weight-pos",
        p_increment: int = 0,
        r_multiplier: float = 1.0,
        r_increment: int = 0,
    ) -> SubroutineReport:
        """Step method: compute the MCF preemptive lower bound and report it
        without constructing a feasible full schedule.

        Solves the MCF relaxation, records ``mcf_lb`` on the diagnostic, and
        returns a :class:`SubroutineReport` with ``obj_value=None`` and
        ``obj_bound = mcf_lb`` (when ``p_increment == 0``). No incumbent is
        registered with the solution manager (this subroutine produces no
        full schedule), so no Gantt or ``*_schedule.yaml`` is emitted for
        this step. The MCF preemptive schedule is still stored on
        ``self.mcf_preemptive_schedule`` and appended to
        ``self.mcf_lb_phase_schedules`` so downstream diagnostics keyed off
        those attributes continue to work.

        Args:
            draw_heatmap: When ``True``, build the parallel-machine signed
                C-cost matrix for the instance and dump it to
                ``<ins>_C_heatmap.yaml`` next to the other per-instance
                artifacts. The post-run reporter (gated by ``draw_gantt``)
                renders the matching HTML.
            heatmap_sort: Row ordering for the heatmap. ``"due2-weight-pos"``
                sorts by ``max(r_j, d⁺-p)`` then ``d⁺`` then ``d⁻``;
                ``"weight-due-pos"`` sorts by ``(max(w⁻, w⁺), w⁻+w⁺, window width)``;
                ``"1_rj_prmp_rel_dev"`` reproduces the job order used by
                the ``heuristic_last_stage_only_sch_from_mcf_lb`` step
                (ascending normalized MCF preemptive window width
                ``(t_max - t_min) / p_{c,j}``, tie-break by total weight
                DESC then native position ASC). Ignored when
                ``draw_heatmap`` is ``False``.
            p_increment: Integer ``≥ 0``. When non-zero, the MCF
                relaxation is solved on an *augmented* instance whose
                last-stage processing times are increased by
                ``p_increment`` for every job. The resulting MCF LB is a
                bound on the augmented problem only — it is **not** a
                global LB on the original instance — so the returned
                ``SubroutineReport.obj_bound`` is ``None`` in that case.
                ``p_increment = 0`` (default) preserves the current
                behaviour. The value used is recorded on
                ``self.mcf_preemptive_sch_p_increment``.
            r_multiplier: Scales the per-job MCF release dates ``r_j``
                (sum of upstream processing times) by this factor; each
                value becomes ``ceil(r_j * r_multiplier)``. Must be
                ``>= 0``. ``1.0`` (default) preserves the current
                behaviour. Values ``<= 1`` keep the resulting MCF
                objective a valid LB on the original instance (looser
                when ``< 1``); values ``> 1`` make it no longer a
                global LB, so ``SubroutineReport.obj_bound`` is set to
                ``None`` in that case (mirroring ``p_increment != 0``).
            r_increment: Integer ``>= 0`` added to every ``r_j``
                *after* the ``r_multiplier`` scaling, so the effective
                release becomes ``ceil(r_j * r_multiplier) + r_increment``.
                ``0`` (default) preserves the current behaviour. Any
                positive value pushes releases later than the original
                instance and therefore makes the MCF objective no
                longer a global LB; ``SubroutineReport.obj_bound`` is
                set to ``None`` in that case (mirroring
                ``p_increment != 0`` and ``r_multiplier > 1``).
        """
        if p_increment < 0:
            raise ValueError(
                f"p_increment must be 0 or a positive integer; got {p_increment}."
            )
        if r_multiplier < 0:
            raise ValueError(f"r_multiplier must be >= 0; got {r_multiplier}.")
        if r_increment < 0:
            raise ValueError(
                f"r_increment must be 0 or a positive integer; got {r_increment}."
            )

        start_elapsed = time.monotonic()
        prev_diag = self.mcf_lb_diagnostic
        diag = MCFLBDiagnostic(
            p_increment_used=p_increment,
            r_multiplier_used=r_multiplier,
            r_increment_used=r_increment,
        )
        self.mcf_lb_diagnostic = diag

        if r_multiplier != 1.0 or r_increment != 0:
            self._log_effective_release_stats(
                "apply_lb_by_mcf",
                r_multiplier=r_multiplier,
                r_increment=r_increment,
            )

        try:
            result = algo_apply_lb_by_mcf(
                self.instance,
                p_increment=p_increment,
                r_multiplier=r_multiplier,
                r_increment=r_increment,
                draw_heatmap=draw_heatmap,
                heatmap_sort=heatmap_sort,
                heatmap_yaml_path=(
                    self.try_get_file_path_for_subroutine("_C_heatmap.yaml")
                    if draw_heatmap
                    else None
                ),
                stop_predicate=self.is_stopping_condition,
                logger=self.logger,
            )
        except MCFLBStopRequested:
            self.mcf_lb_diagnostic = prev_diag
            self.logger.info(
                "apply_lb_by_mcf: stop predicate fired before MCF solve; skipping."
            )
            return self._make_stop_report(start_elapsed)
        diag.mcf_lb = result.mcf_lb
        diag.mcf_solve_sec = result.mcf_solve_sec

        self.mcf_preemptive_schedule = result.mcf_preemptive_schedule
        self.mcf_preemptive_sch_p_increment = p_increment
        self.mcf_lb_phase_schedules.clear()
        self._record_mcf_lb_phase(("1_mcf_preemptive", result.mcf_preemptive_schedule))

        self.logger.info(
            "apply_lb_by_mcf: MCF LB = %d, p_increment=%d, "
            "r_multiplier=%.4g, r_increment=%d",
            int(result.mcf_lb),
            p_increment,
            r_multiplier,
            r_increment,
        )

        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=None,
            obj_bound=(result.mcf_lb if result.obj_bound_is_valid else None),
        )
        self._register(report, None)
        return report

    def heuristic_last_stage_only_sch_from_mcf_lb(
        self,
        job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
        placement_priority: Literal["contrib", "dist"] = "contrib",
        p_increment: int = 0,
        r_multiplier: float = 1.0,
        r_increment: int = 0,
    ) -> SubroutineReport:
        """Step method: midpoint warm-start across all jobs from the MCF
        preemptive LB, then a CP-free heuristic refinement
        (``make_semi_active`` on the last stage with upstream release
        times, followed by last-stage ``insert_idle_time``).

        Midpoint placement comes from the MCF preemptive window for each
        job, then ``make_semi_active`` left-shifts the last stage and
        ``insert_idle_time`` inserts idle time at ET-optimal positions.
        No CP solve is involved.

        Pre-conditions (else ``ValueError``):
          - ``self.mcf_preemptive_schedule`` set by a prior
            ``apply_lb_by_mcf`` (or compatible) step.
          - ``self.mcf_lb_diagnostic`` set so the MCF LB context exists.

        Args:
            job_priority: Job-ordering priority used by the midpoint
                warm-start placement.
            placement_priority: Lex-tiebreak between weighted-ET
                contribution and start-time distance when the midpoint
                slot is occupied; see ``_insert_jobs_at_desired_starts``.
            p_increment: Integer ``≥ 0``. When non-zero, the placement
                + heuristic refinement run on an augmented instance
                whose last-stage processing times are increased by
                ``p_increment`` for every job. The resulting
                last-stage-only schedule is feasible for the augmented
                problem only; ``build_full_sch_from_last_stage_only_sch``
                rebuilds it under original durations before
                reverse-dispatch. The value used is recorded on
                ``self.last_stage_only_sol_p_increment``.
            r_multiplier: Scales the per-job release times used for
                midpoint placement and the subsequent
                ``make_semi_active`` left-shift; each value becomes
                ``ceil(r_j * r_multiplier)``. Must be ``>= 0``. ``1.0``
                (default) preserves the current behaviour.
            r_increment: Integer ``>= 0`` added to every release time
                *after* the ``r_multiplier`` scaling, so the effective
                release becomes ``ceil(r_j * r_multiplier) + r_increment``.
                ``0`` (default) preserves the current behaviour.

        Side effects:
          - Stores the resulting last-stage-only schedule on
            ``self.last_stage_only_sol``.
          - Appends ``2_lastS_only_from_mcf_lb_before_sa_iti`` (midpoint
            placement deepcopy, before ``make_semi_active`` /
            ``insert_idle_time``) and ``3_lastS_only_from_mcf_lb_after_sa_iti``
            (final result) to ``self.mcf_lb_phase_schedules``.

        The returned ``SubroutineReport.obj_bound`` is always ``None``:
        this step does not produce a lower bound, it only consumes the
        MCF preemptive schedule from the prior ``apply_lb_by_mcf`` step.
        """
        if p_increment < 0:
            raise ValueError(
                f"p_increment must be 0 or a positive integer; got {p_increment}."
            )
        if r_multiplier < 0:
            raise ValueError(f"r_multiplier must be >= 0; got {r_multiplier}.")
        if r_increment < 0:
            raise ValueError(
                f"r_increment must be 0 or a positive integer; got {r_increment}."
            )
        if self.mcf_preemptive_schedule is None:
            raise ValueError(
                "heuristic_last_stage_only_sch_from_mcf_lb requires a prior "
                "apply_lb_by_mcf step to populate self.mcf_preemptive_schedule."
            )

        h_diag = HeuristicLastStageOnlyDiagnostic(
            p_increment_used=p_increment,
            r_multiplier_used=r_multiplier,
            r_increment_used=r_increment,
        )
        self.heuristic_last_stage_only_diagnostic = h_diag

        if r_multiplier != 1.0 or r_increment != 0:
            self._log_effective_release_stats(
                "heuristic_last_stage_only_sch_from_mcf_lb",
                r_multiplier=r_multiplier,
                r_increment=r_increment,
            )

        result = heuristic_last_stage_only_from_mcf_lb(
            self.instance,
            self.mcf_preemptive_schedule,
            logger=self.logger,
            job_priority=job_priority,
            placement_priority=placement_priority,
            p_increment=p_increment,
            r_multiplier=r_multiplier,
            r_increment=r_increment,
        )

        self.last_stage_only_sol = FFcDDWSolution(
            schedule=result.schedule,
            obj_value=result.obj_value,
            obj_bound=None,
        )
        self.last_stage_only_sol_p_increment = p_increment
        self._record_mcf_lb_phases(
            [(f"2_{label}", sched) for label, sched in result.intermediate_schedules]
        )
        self._record_mcf_lb_phase(
            ("3_lastS_only_from_mcf_lb_after_sa_iti", result.schedule)
        )

        h_diag.status = result.status
        h_diag.obj_value = result.obj_value
        h_diag.elapsed_sec = result.elapsed_time

        self.logger.info(
            "heuristic_last_stage_only_sch_from_mcf_lb: status=%s, "
            "obj=%.2f, elapsed=%.2fs, p_increment=%d, "
            "r_multiplier=%.4g, r_increment=%d.",
            result.status,
            result.obj_value,
            result.elapsed_time,
            p_increment,
            r_multiplier,
            r_increment,
        )
        report = SubroutineReport(
            elapsed_time=result.elapsed_time,
            obj_value=result.obj_value,
            obj_bound=None,
        )
        self._register(report, None)
        return report

    def build_full_sch_from_last_stage_only_sch(
        self,
    ) -> SubroutineReport:
        """Step method: build a full dispatched ``FFcSchedule`` from
        ``self.last_stage_only_sol.schedule`` via reverse-dispatch + unflip
        (Phase 3 of the MCF-LB pipeline applied standalone).

        Pre-condition (else ``ValueError``): ``self.last_stage_only_sol`` is
        set by a prior ``heuristic_last_stage_only_sch_from_mcf_lb`` call.

        The reversed dispatcher is run twice (``machine_then_job=False``
        and ``machine_then_job=True``) in ``reverse_dispatch_full_schedule``;
        the candidate with the shorter makespan is unflipped.

        Side effects:
          - Registers the dispatched schedule as a full incumbent on
            ``self.solution_manager``.
          - Appends (in order) ``4_lastS_only_before_rs`` (input deepcopy
            for multi-stage; rebuilt under original last-stage durations
            when the prior step inflated them via
            ``self.last_stage_only_sol_p_increment != 0``),
            ``5_lastS_only_after_rs`` (right-shifted), ``6_lastS_only_flipped``
            (reversed-instance seed), ``7_fullS_before_unflip``,
            ``8_fullS_after_unflip`` (deepcopy after ``as_reversed()``,
            before final ``make_semi_active`` / ``insert_idle_time``),
            and ``9_fullS_after_sa_iti`` to ``self.mcf_lb_phase_schedules``.
            The right-shifted / flipped / before-unflip entries are
            skipped when ``self.instance.stage_count == 1``.

        Returns:
            ``SubroutineReport`` with ``obj_value`` = dispatched weighted ET
            and ``obj_bound = None``. The step itself does not compute a
            global LB; callers chain ``apply_lb_by_mcf`` earlier in the
            flow when an LB is needed.
        """
        if self.last_stage_only_sol is None:
            raise ValueError(
                "build_full_sch_from_last_stage_only_sch requires "
                "self.last_stage_only_sol; run "
                "heuristic_last_stage_only_sch_from_mcf_lb first."
            )

        ls_p_inc = self.last_stage_only_sol_p_increment
        rebuild_with_original_p = ls_p_inc is not None and ls_p_inc != 0

        result = algo_build_full_sch_from_last_stage_only_sch(
            self.instance,
            self.last_stage_only_sol.schedule,
            rebuild_last_stage_with_original_p=rebuild_with_original_p,
            logger=self.logger,
        )
        if result.schedule is None:
            self.logger.warning(
                "build_full_sch_from_last_stage_only_sch: reverse-dispatch "
                "produced no schedule"
            )
            report = SubroutineReport(
                elapsed_time=result.dispatch_sec,
                obj_value=None,
                obj_bound=None,
            )
            self.build_full_sch_diagnostic = BuildFullSchDiagnostic(
                dispatched_obj=None,
                full_sch_makespan=None,
                dispatch_sec=result.dispatch_sec,
            )
            self._register(report, None)
            return report

        for label, sched in result.intermediate_schedules:
            self._record_mcf_lb_phase(
                (f"{_BUILD_FULL_SCH_LABEL_TO_INDEX[label]}_{label}", sched)
            )

        self.logger.info(
            "build_full_sch_from_last_stage_only_sch: dispatched obj=%.2f, "
            "makespan=%d, elapsed=%.2fs",
            result.dispatched_obj,
            result.full_sch_makespan,
            result.dispatch_sec,
        )
        report = SubroutineReport(
            elapsed_time=result.dispatch_sec,
            obj_value=result.dispatched_obj,
            obj_bound=None,
        )
        solution = FFcDDWSolution(
            schedule=result.schedule,
            obj_value=result.dispatched_obj,
            obj_bound=None,
        )
        self.build_full_sch_diagnostic = BuildFullSchDiagnostic(
            dispatched_obj=result.dispatched_obj,
            full_sch_makespan=int(result.schedule.makespan),
            dispatch_sec=result.dispatch_sec,
        )
        self._register(report, solution)
        return report

    def _emit_calc_mcf_lb_phase_metrics_csv(
        self, result: CalcMcfLbAndDeriveFullSchResult
    ) -> None:
        """Write per-instance wET / makespan CSVs from a composite result.

        Iterates ``r1_phase_schedules`` / ``r2_phase_schedules`` directly
        (no regex parsing). Strips the ``"<n>_"`` index prefix to recover
        the unprefixed label, then looks each label up against
        ``MCF_LB_R1_LABEL_ORDER`` / ``MCF_LB_R2_LABEL_ORDER``. Silently
        no-ops when the controller has no artifact layout bound (tests,
        scripted use). Round-2 cells are blank when round 2 did not run;
        wET cells are blank for reversed-instance snapshots (``flipped``,
        ``fullS_before_unflip``).
        """
        layout = self._artifact_layout
        scenario = self._artifact_scenario_name
        instance = self._artifact_instance_name
        if layout is None or scenario is None or instance is None:
            return

        def _strip_index(label: str) -> str:
            sep = label.find("_")
            return label[sep + 1 :] if sep >= 0 else label

        per_round: dict[str, dict[str, MCFLBPhaseSchedule]] = {
            "r1": {
                _strip_index(lbl): sched for lbl, sched in result.r1_phase_schedules
            },
            "r2": {
                _strip_index(lbl): sched for lbl, sched in result.r2_phase_schedules
            },
        }

        obj_path = layout.artifact_path(
            "mcf_lb_phase_obj_csv",
            scenario_name=scenario,
            instance_name=instance,
        )
        makespan_path = layout.artifact_path(
            "mcf_lb_phase_makespan_csv",
            scenario_name=scenario,
            instance_name=instance,
        )

        rows: list[tuple[str, str, str, str]] = []  # (round, label, obj, ms)
        for round_key, labels in (
            ("r1", MCF_LB_R1_LABEL_ORDER),
            ("r2", MCF_LB_R2_LABEL_ORDER),
        ):
            for label in labels:
                sched = per_round[round_key].get(label)
                if sched is None:
                    rows.append((round_key, label, "", ""))
                    continue
                obj = compute_phase_obj_value(sched, self.instance)
                obj_cell = "" if obj is None else f"{obj:.6f}"
                makespan_cell = str(int(sched.makespan))
                rows.append((round_key, label, obj_cell, makespan_cell))

        obj_path.parent.mkdir(parents=True, exist_ok=True)
        with obj_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["round", "label", "obj_value"])
            for r, label, obj_cell, _ in rows:
                writer.writerow([r, label, obj_cell])
        with makespan_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["round", "label", "makespan"])
            for r, label, _, ms_cell in rows:
                writer.writerow([r, label, ms_cell])

    def _emit_calc_mcf_lb_phase_schedule_jsons(
        self, result: CalcMcfLbAndDeriveFullSchResult
    ) -> None:
        """Dump per-round JSON snapshots from a composite result under
        ``progress/<scenario>/<instance>/calc_mcf_lb_and_derive_full_sch/<round>/``.

        Mirrors the runner's flat dispatcher
        (`MCFPreemptiveSchedule` → ``dump_preemptive_schedule_json``;
        `FFcSchedule` → ``dump_solution_json``) but resolves paths via
        the ``calc_mcf_lb_phase_schedule`` artifact kind so the on-disk
        layout is round-nested. Silently no-ops without a bound layout.
        """
        layout = self._artifact_layout
        scenario = self._artifact_scenario_name
        instance = self._artifact_instance_name
        if layout is None or scenario is None or instance is None:
            return

        for round_key, items in (
            ("r1", result.r1_phase_schedules),
            ("r2", result.r2_phase_schedules),
        ):
            for prefixed_label, sched in items:
                if sched is None:
                    continue
                sep = prefixed_label.find("_")
                if sep < 0:
                    continue
                index_str, label = prefixed_label[:sep], prefixed_label[sep + 1 :]
                json_path = layout.artifact_path(
                    "calc_mcf_lb_phase_schedule",
                    scenario_name=scenario,
                    instance_name=instance,
                    round=round_key,
                    index=index_str,
                    label=label,
                )
                json_path.parent.mkdir(parents=True, exist_ok=True)
                phase_obj = compute_phase_obj_value(sched, self.instance)
                if isinstance(sched, MCFPreemptiveSchedule):
                    dump_preemptive_schedule_json(
                        json_path,
                        instance_name=instance,
                        stage_id=sched.stage_id,
                        machines=sched.machines,
                        jobs=self.instance.job_id_list,
                        segments=sched.to_gantt_segments(),
                        all_jobs=self.instance.job_id_list,
                        obj_value=phase_obj,
                        compact=True,
                    )
                else:
                    dump_solution_json(
                        sched,
                        json_path,
                        instance_name=instance,
                        obj_value=phase_obj,
                        compact=True,
                    )

    def _emit_calc_mcf_lb_r1_summary_yaml(
        self,
        result: CalcMcfLbAndDeriveFullSchResult,
    ) -> None:
        """Write the ``r1/r1_summary.yaml`` sidecar from a composite
        result. Always written when an artifact layout is bound, so the
        Rep3-style ``delta <= 0`` rows are auditable on disk even when
        round 2 produced no JSON snapshots.

        Eleven fields (round-1 stage timings/objectives/makespans plus
        delta-driven adjust knobs):
          - ``mcfLbElapsedTime`` LP solve seconds.
          - ``mcfLbObjValue`` MCF LP objective value.
          - ``mcfLbMakespan`` makespan of the MCF preemptive schedule.
          - ``lastStageOnlyObjValue`` wET of the heuristic last-stage-only
            schedule.
          - ``lastStageOnlyMakespan`` makespan of the heuristic last-stage-only
            schedule.
          - ``fullSchObjValue`` wET of the full schedule.
          - ``fullSchMakespan`` makespan of the full schedule.
          - ``totalTime`` sum of the three r1 stage seconds (LP solve +
            heuristic + reverse-dispatch).
          - ``makespanDelta`` (signed; can be negative).
          - ``pIncrementAdded`` / ``rIncrementAdded``: ``None`` when
            round 2 did not run, the actual increment (which is 0 when
            the matching ``adjust_*`` flag was off but round 2 still
            ran for the other knob) otherwise.
        """
        layout = self._artifact_layout
        scenario = self._artifact_scenario_name
        instance = self._artifact_instance_name
        if layout is None or scenario is None or instance is None:
            return

        r1_apply = result.r1_apply
        r1_heuristic = result.r1_heuristic
        r1_build_full = result.r1_build_full

        mcf_lb_elapsed_time = r1_apply.mcf_solve_sec if r1_apply is not None else None
        mcf_lb_obj_value = (
            r1_apply.mcf_lb
            if r1_apply is not None and r1_apply.obj_bound_is_valid
            else None
        )
        mcf_lb_makespan = (
            int(r1_apply.mcf_preemptive_schedule.makespan)
            if r1_apply is not None
            else None
        )
        last_stage_only_obj_value = (
            r1_heuristic.obj_value if r1_heuristic is not None else None
        )
        last_stage_only_makespan = (
            int(r1_heuristic.schedule.makespan) if r1_heuristic is not None else None
        )
        full_sch_obj_value = (
            r1_build_full.dispatched_obj if r1_build_full is not None else None
        )
        full_sch_makespan = (
            r1_build_full.full_sch_makespan if r1_build_full is not None else None
        )
        total_time = sum(
            t
            for t in (
                mcf_lb_elapsed_time,
                r1_heuristic.elapsed_time if r1_heuristic is not None else None,
                r1_build_full.dispatch_sec if r1_build_full is not None else None,
            )
            if t is not None
        )
        p_increment_added = result.r2_p_increment if result.r2_ran else None
        r_increment_added = result.r2_r_increment if result.r2_ran else None

        payload = {
            "mcfLbElapsedTime": mcf_lb_elapsed_time,
            "mcfLbObjValue": mcf_lb_obj_value,
            "mcfLbMakespan": mcf_lb_makespan,
            "lastStageOnlyObjValue": last_stage_only_obj_value,
            "lastStageOnlyMakespan": last_stage_only_makespan,
            "fullSchObjValue": full_sch_obj_value,
            "fullSchMakespan": full_sch_makespan,
            "totalTime": total_time,
            "makespanDelta": result.makespan_delta,
            "pIncrementAdded": p_increment_added,
            "rIncrementAdded": r_increment_added,
        }
        out_path = layout.artifact_path(
            "calc_mcf_lb_r1_summary_yaml",
            scenario_name=scenario,
            instance_name=instance,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        dump_yaml(payload, out_path)

    def _emit_calc_mcf_lb_r2_summary_yaml(
        self,
        result: CalcMcfLbAndDeriveFullSchResult,
    ) -> None:
        """Write the ``r2/r2_summary.yaml`` sidecar from a composite
        result. Emitted only when round 2 was attempted (``r2_apply``
        populated); silently skipped otherwise.

        Eight fields (round-2 stage timings/objectives/makespans):
          - ``mcfLbElapsedTime`` / ``mcfLbObjValue`` / ``mcfLbMakespan``
            from the r2 augmented MCF LP. ``mcfLbObjValue`` is the raw
            LP objective for the augmented instance — it is *not* a
            valid global lower bound on the original instance, and is
            recorded here for inspection only. Downstream consumers
            (``final_obj_bound``, the reporting pipeline) read only
            ``r1_apply.mcf_lb`` / ``r1_mcf_lb`` and never use this
            value as a bound.
          - ``lastStageOnlyObjValue`` / ``lastStageOnlyMakespan`` from
            the r2 heuristic's final schedule.
          - ``fullSchObjValue`` / ``fullSchMakespan`` from the r2 full
            schedule (``None`` when reverse-dispatch produced nothing).
          - ``totalTime`` sum of the three r2 stage seconds.
        """
        layout = self._artifact_layout
        scenario = self._artifact_scenario_name
        instance = self._artifact_instance_name
        if layout is None or scenario is None or instance is None:
            return

        r2_apply = result.r2_apply
        r2_heuristic = result.r2_heuristic
        r2_build_full = result.r2_build_full
        if r2_apply is None:
            return

        mcf_lb_elapsed_time = r2_apply.mcf_solve_sec
        mcf_lb_obj_value = r2_apply.mcf_lb
        mcf_lb_makespan = int(r2_apply.mcf_preemptive_schedule.makespan)
        last_stage_only_obj_value = (
            r2_heuristic.obj_value if r2_heuristic is not None else None
        )
        last_stage_only_makespan = (
            int(r2_heuristic.schedule.makespan) if r2_heuristic is not None else None
        )
        full_sch_obj_value = (
            r2_build_full.dispatched_obj if r2_build_full is not None else None
        )
        full_sch_makespan = (
            r2_build_full.full_sch_makespan if r2_build_full is not None else None
        )
        total_time = sum(
            t
            for t in (
                mcf_lb_elapsed_time,
                r2_heuristic.elapsed_time if r2_heuristic is not None else None,
                r2_build_full.dispatch_sec if r2_build_full is not None else None,
            )
            if t is not None
        )

        payload = {
            "mcfLbElapsedTime": mcf_lb_elapsed_time,
            "mcfLbObjValue": mcf_lb_obj_value,
            "mcfLbMakespan": mcf_lb_makespan,
            "lastStageOnlyObjValue": last_stage_only_obj_value,
            "lastStageOnlyMakespan": last_stage_only_makespan,
            "fullSchObjValue": full_sch_obj_value,
            "fullSchMakespan": full_sch_makespan,
            "totalTime": total_time,
        }
        out_path = layout.artifact_path(
            "calc_mcf_lb_r2_summary_yaml",
            scenario_name=scenario,
            instance_name=instance,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        dump_yaml(payload, out_path)

    def calc_mcf_lb_and_derive_full_sch(
        self,
        draw_pmtn_sch_heatmap: bool = False,
        heatmap_sort: HeatmapSort = "end_time",
        job_placement_priority: PmPrmpSortKey = "end_time",
        last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
        makespan_delta_ref: Literal[
            "mcfLbMakespan", "lastStageOnlyMakespan"
        ] = "mcfLbMakespan",
        adjust_p: bool = False,
        adjust_r: bool = False,
        p_adjust_coeff: float = 1.0,
        r_adjust_coeff: float = 0.5,
        last_stage_rebuild_config: Literal[
            "original_pr", "increased_pr", "best"
        ] = "increased_pr",
        proceed_r2_when_nonpositive_cmax: bool = False,
        emit_phase_schedules: bool = False,
    ) -> SubroutineReport:
        """Composite step: MCF-LB → full schedule, then a conditional
        second round with p/r adjustments.

        Thin wrapper around the algorithm-side
        ``mcf_lb_pipeline.calc_mcf_lb_and_derive_full_sch`` pipeline.
        Round 1 always runs (no augmentation, so the resulting MCF LB
        is a valid global bound on the original instance). Round 2 runs
        when ``(adjust_p or adjust_r)`` is True, the stop predicate is
        False, r1 produced a full schedule, AND either the signed
        makespan delta ``r1_full_sch_makespan - ref_makespan``
        is strictly positive OR ``proceed_r2_when_nonpositive_cmax`` is
        True (in which case the delta is clamped to ``>=1`` for
        increment computation only — the raw signed delta is still
        recorded on the diagnostic, preserving the Rep3-fix invariant).
        The reference makespan source is selected by
        ``makespan_delta_ref`` (default ``"mcfLbMakespan"``).

        Registers exactly once per call: the synthesized
        ``SubroutineReport`` whose ``obj_bound`` is round-1's MCF LB and
        whose paired solution is the better of round-1 / round-2
        results. Stop guards that fire before round 1 produces a full
        schedule return ``_make_stop_report`` without registering;
        guards that fire after round 1 has a result still register the
        round-1 result once before returning.

        Side effects (always, when an artifact layout is bound):
          * Two per-instance phase-metric CSVs are emitted via the
            ``mcf_lb_phase_obj_csv`` / ``mcf_lb_phase_makespan_csv``
            kinds. One row per user-spec snapshot; r2 rows carry blank
            cells when round 2 did not run. wET cells are blank for
            reversed-instance snapshots (``flipped``,
            ``fullS_before_unflip``).
          * One per-instance ``r1/r1_summary.yaml`` sidecar via the
            ``calc_mcf_lb_r1_summary_yaml`` kind, plus a matching
            ``r2/r2_summary.yaml`` via ``calc_mcf_lb_r2_summary_yaml``
            when round 2 was attempted.

        When ``emit_phase_schedules=True``, also emits per-round JSON
        snapshots under
        ``progress/<inst>/calc_mcf_lb_and_derive_full_sch/<round>/<n>_<label>.json``
        via the ``calc_mcf_lb_phase_schedule`` kind. The runner-side
        flat ``mcf_lb_phase_schedule`` emission is bypassed for this
        step (the composite never appends to
        ``self.mcf_lb_phase_schedules``).

        Args:
            draw_pmtn_sch_heatmap: When True, ``apply_lb_by_mcf`` dumps
                the C-cost heatmap YAML for each round.
            heatmap_sort: Forwarded to ``apply_lb_by_mcf``.
            job_placement_priority: Forwarded as ``job_priority`` to
                the heuristic.
            last_stage_only_placement_criteria: Forwarded as
                ``placement_priority`` to the heuristic.
            makespan_delta_ref: Reference makespan in
                ``makespan_delta = r1_full_sch_makespan - ref_makespan``.
                ``"mcfLbMakespan"`` (default) uses the r1 MCF preemptive
                LP schedule's makespan; ``"lastStageOnlyMakespan"`` uses
                the r1 heuristic non-preemptive last-stage schedule's
                makespan. Any other value raises ``ValueError`` from the
                algorithm-side function.
            adjust_p: When True, round 2 inflates last-stage processing
                times by ``ceil(p_adjust_coeff * makespan_delta * m_last / n)``.
            adjust_r: When True, round 2 inflates per-job releases by
                ``ceil(makespan_delta * r_adjust_coeff)`` (the historical
                ``adjust_r_by_half`` behaviour is the default).
            p_adjust_coeff: Coefficient on ``makespan_delta * m_last / n``
                used in the ``adjust_p`` formula. Default ``1.0``
                reproduces the historical ``ceil(delta * m_last / n)``
                factor.
            r_adjust_coeff: Coefficient on ``makespan_delta`` used in
                the ``adjust_r`` formula. Default ``0.5`` reproduces the
                historical ``ceil(delta / 2)`` factor.
            last_stage_rebuild_config: Round-2 last-stage generation
                policy. ``"increased_pr"`` (default) generates the
                last-stage schedule with the increased p/r and rebuilds it
                to original ``p`` (preserving completion times) before
                reverse-dispatch — the historical behaviour.
                ``"original_pr"`` generates with the original p/r and
                reverse-dispatches directly. ``"best"`` runs both and keeps
                the smaller pre-unflip makespan.
            proceed_r2_when_nonpositive_cmax: When False (default),
                preserves the historical ``delta_le_0`` skip — round 2
                is skipped whenever the signed delta is ``<= 0``. When
                True, round 2 runs anyway with the delta clamped to
                ``>=1`` for increment math.
            emit_phase_schedules: Gates the per-round JSON / paired
                Gantt-PNG output. Default ``False``.

        Returns:
            The single registered ``SubroutineReport`` whose
            ``obj_bound`` is round-1's MCF LB and whose ``obj_value``
            matches the registered (best) solution. When the stop
            predicate fires before round 1 produces a full schedule,
            returns a stop-report from ``_make_stop_report`` without
            registering.
        """
        start_elapsed = time.monotonic()

        c_diag = CalcMcfLbAndDeriveFullSchDiagnostic()
        self.calc_mcf_lb_and_derive_full_sch_diagnostic = c_diag

        r1_heatmap_yaml_path = (
            self.try_get_file_path_for_subroutine("_r1_C_heatmap.yaml")
            if draw_pmtn_sch_heatmap
            else None
        )
        r2_heatmap_yaml_path = (
            self.try_get_file_path_for_subroutine("_r2_C_heatmap.yaml")
            if draw_pmtn_sch_heatmap
            else None
        )

        result = algo_calc_mcf_lb_and_derive_full_sch(
            self.instance,
            draw_pmtn_sch_heatmap=draw_pmtn_sch_heatmap,
            heatmap_sort=heatmap_sort,
            job_placement_priority=job_placement_priority,
            last_stage_only_placement_criteria=last_stage_only_placement_criteria,
            makespan_delta_ref=makespan_delta_ref,
            adjust_p=adjust_p,
            adjust_r=adjust_r,
            p_adjust_coeff=p_adjust_coeff,
            r_adjust_coeff=r_adjust_coeff,
            last_stage_rebuild_config=last_stage_rebuild_config,
            proceed_r2_when_nonpositive_cmax=proceed_r2_when_nonpositive_cmax,
            stop_predicate=self.is_stopping_condition,
            logger=self.logger,
            r1_heatmap_yaml_path=r1_heatmap_yaml_path,
            r2_heatmap_yaml_path=r2_heatmap_yaml_path,
        )

        # ---- Populate diagnostic from result (mostly straight-through). ----
        if result.r1_apply is not None:
            c_diag.r1_mcf_lb = (
                result.r1_apply.mcf_lb if result.r1_apply.obj_bound_is_valid else None
            )
            c_diag.r1_mcf_solve_sec = result.r1_apply.mcf_solve_sec
            c_diag.r1_ls_only_pmtn_makespan = int(
                result.r1_apply.mcf_preemptive_schedule.makespan
            )
        if result.r1_heuristic is not None:
            c_diag.r1_ls_only_makespan = int(result.r1_heuristic.schedule.makespan)
        if (
            result.r1_build_full is not None
            and result.r1_build_full.schedule is not None
        ):
            c_diag.r1_full_sch_makespan = int(result.r1_build_full.schedule.makespan)
            c_diag.r1_full_sch_obj = (
                float(result.r1_build_full.dispatched_obj)
                if result.r1_build_full.dispatched_obj is not None
                else None
            )
        c_diag.makespan_delta = result.makespan_delta
        if result.makespan_delta is not None:
            c_diag.makespan_delta_ref_used = makespan_delta_ref
        if result.r2_ran:
            c_diag.last_stage_rebuild_config_used = last_stage_rebuild_config
        c_diag.r2_ran = result.r2_ran
        c_diag.r2_skip_reason = result.r2_skip_reason
        if result.r2_apply is not None:
            c_diag.r2_mcf_lb = (
                result.r2_apply.mcf_lb if result.r2_apply.obj_bound_is_valid else None
            )
            c_diag.r2_mcf_solve_sec = result.r2_apply.mcf_solve_sec
            c_diag.r2_ls_only_pmtn_makespan = int(
                result.r2_apply.mcf_preemptive_schedule.makespan
            )
        if (
            result.r2_build_full is not None
            and result.r2_build_full.schedule is not None
        ):
            c_diag.r2_full_sch_makespan = int(result.r2_build_full.schedule.makespan)
            c_diag.r2_full_sch_obj = (
                float(result.r2_build_full.dispatched_obj)
                if result.r2_build_full.dispatched_obj is not None
                else None
            )
        c_diag.r2_p_increment_added = result.r2_p_increment if result.r2_ran else None
        c_diag.r2_r_increment_added = result.r2_r_increment if result.r2_ran else None

        # ---- Maintain backward-compat state slots so subsequent steps that
        # read these attributes (e.g. a follow-on
        # ``build_full_sch_from_last_stage_only_sch``) keep working. ----
        last_apply = result.r2_apply or result.r1_apply

        # Pair the p_increment with whichever apply was chosen: r2's apply
        # carries the augmented increment even when r2 stopped early (so
        # r2_ran is False), whereas r1's apply is always un-augmented.
        if last_apply is result.r2_apply and result.r2_apply is not None:
            last_p_inc = result.r2_p_increment or 0
        else:
            last_p_inc = 0
        if last_apply is not None:
            self.mcf_preemptive_schedule = last_apply.mcf_preemptive_schedule
            self.mcf_preemptive_sch_p_increment = last_p_inc
        last_heuristic = result.r2_heuristic or result.r1_heuristic
        if last_heuristic is not None:
            self.last_stage_only_sol = FFcDDWSolution(
                schedule=last_heuristic.schedule,
                obj_value=last_heuristic.obj_value,
                obj_bound=None,
            )
            self.last_stage_only_sol_p_increment = last_p_inc

        # ---- Stop case: r1 was halted before producing a build_full result. ----
        if result.r1_build_full is None:
            c_diag.elapsed_sec = time.monotonic() - start_elapsed
            return self._make_stop_report(start_elapsed)

        # ---- Artifact emission (always when layout bound). ----
        if emit_phase_schedules:
            self._emit_calc_mcf_lb_phase_schedule_jsons(result)
        self._emit_calc_mcf_lb_r1_summary_yaml(result)
        self._emit_calc_mcf_lb_r2_summary_yaml(result)
        self._emit_calc_mcf_lb_phase_metrics_csv(result)

        # ---- Build best solution + register exactly once. ----
        best_sol: FFcDDWSolution | None = None
        if result.best_schedule is not None:
            best_sol = FFcDDWSolution(
                schedule=result.best_schedule,
                obj_value=result.best_obj,
                obj_bound=result.final_obj_bound,
            )
        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=result.best_obj,
            obj_bound=result.final_obj_bound,
        )
        c_diag.final_obj = result.best_obj
        c_diag.final_obj_bound = result.final_obj_bound
        c_diag.elapsed_sec = elapsed
        self._register(report, best_sol)
        return report

    def _dispatch_by_sequence(
        self,
        job_sequence: Sequence[str],
        dispatcher: Literal["mixed", "fam"] = "mixed",
        dispatching_criteria: Literal["weighted_et", "makespan"] = "weighted_et",
    ) -> tuple[FFcSchedule, float | None]:
        if dispatcher == "mixed":
            mixed = MixedDispatcher(self.instance)
            schedule = mixed.get_best_mixed_schedule_by_sequence(
                job_sequence, criteria=dispatching_criteria
            )
            if schedule is None:
                raise RuntimeError(
                    f"MixedDispatcher produced no schedule for {self.instance.name}"
                )
            sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, self.instance)
            obj_value = float(sum_e + sum_t)
        elif dispatcher == "fam":
            spec = AlgSpec(
                instance=self.instance,
                option=FAMOption(job_sequence=tuple(job_sequence)),
                logger=self.logger,
            )
            record = FAMDispatcher().run(spec)
            result = record.result
            if result is None or result.schedule is None:
                raise RuntimeError(
                    f"FAMDispatcher produced no schedule for {self.instance.name}"
                )
            schedule = result.schedule
            obj_value = (
                float(result.obj_value) if result.obj_value is not None else None
            )
        else:
            raise ValueError(
                f"Unknown dispatcher {dispatcher!r}; expected 'mixed' or 'fam'."
            )
        return schedule, obj_value

    def _dispatch_by_reversed_sequence_with_iit(
        self,
        job_sequence: Sequence[str],
        instance: FFcDDWParameters | None = None,
    ) -> tuple[FFcSchedule, float]:
        """Dispatch ``job_sequence`` via the reverse-instance + IIT pipeline.

        Steps: stage-reverse the instance, dispatch ``reversed(job_sequence)``
        with :meth:`MixedDispatcher.get_best_mixed_schedule_by_sequence`
        minimising makespan, unflip the result with
        :meth:`FFcSchedule.as_reversed`, push left to semi-active form, then
        insert idle time on the last stage.

        ``instance`` defaults to ``self.instance``; passing a coarsened
        instance enables dispatch on a time-resolved copy (see
        :meth:`initialize_by_eddub_twt`).
        """
        instance = instance or self.instance
        reversed_instance = FFcDDWParameters.reverse_stages(instance)
        rev_seq = list(reversed(job_sequence))

        rev_dispatcher = MixedDispatcher(reversed_instance, logger=self.logger)
        reversed_full_1 = rev_dispatcher.get_best_mixed_schedule_by_sequence(
            rev_seq,
            machine_then_job=True,
            criteria="makespan",
        )
        reversed_full_2 = rev_dispatcher.get_best_mixed_schedule_by_sequence(
            rev_seq,
            machine_then_job=False,
            criteria="makespan",
        )
        if reversed_full_1 is None and reversed_full_2 is None:
            raise RuntimeError(
                f"_dispatch_by_reversed_sequence_with_iit: MixedDispatcher "
                f"produced no schedule for {instance.name}"
            )
        if reversed_full_1 is not None:
            schedule_1 = reversed_full_1.as_reversed()
        else:
            schedule_1 = None
        if reversed_full_2 is not None:
            schedule_2 = reversed_full_2.as_reversed()
        else:
            schedule_2 = None

        if schedule_1 is not None and schedule_2 is not None:
            sum_e_1, sum_t_1 = compute_weighted_earliness_tardiness(
                schedule_1, instance
            )
            obj_1 = float(sum_e_1 + sum_t_1)

            sum_e_2, sum_t_2 = compute_weighted_earliness_tardiness(
                schedule_2, instance
            )
            obj_2 = float(sum_e_2 + sum_t_2)

            if obj_1 <= obj_2:
                schedule = schedule_1
                self.logger.info(
                    "_dispatch_by_reversed_sequence_with_iit: "
                    "machine_then_job=True better (obj=%s) than "
                    "machine_then_job=False (obj=%s)",
                    obj_1,
                    obj_2,
                )
            else:
                schedule = schedule_2
                self.logger.info(
                    "_dispatch_by_reversed_sequence_with_iit: "
                    "machine_then_job=False better (obj=%s) than "
                    "machine_then_job=True (obj=%s)",
                    obj_2,
                    obj_1,
                )
        else:
            schedule = schedule_1 or schedule_2

        if schedule is None:
            raise RuntimeError(
                f"_dispatch_by_reversed_sequence_with_iit: no schedule after "
                f"unflipping for {instance.name}"
            )
        schedule.make_semi_active(instance.stage_2_job_2_p_map)
        schedule.insert_idle_time(
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
        )
        sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
        return schedule, float(sum_e + sum_t)

    def initialize_by_edd(
        self,
        dispatcher: Literal["mixed", "fam"] = "mixed",
        dispatching_criteria: Literal["weighted_et", "makespan"] = "weighted_et",
    ) -> SubroutineReport:
        """Step method: seed an incumbent by dispatching jobs in EDD order.

        With due-date windows ``[d^-_j, d^+_j]``, EDD uses ``d^+_j`` (the
        latest on-time moment) so tight deadlines go first and slack jobs
        drop to the tail. Ties on ``d^+`` break by native ``job_id_list``
        order for determinism, matching the tie-break rule used by
        :meth:`run_mcf_lb`.

        ``dispatcher`` selects the decoder that turns the EDD permutation
        into a schedule: ``"mixed"`` uses :class:`MixedDispatcher` (with
        ``dispatching_criteria`` for its internal selection rule); ``"fam"`` uses
        :class:`FAMDispatcher` and ignores ``dispatching_criteria``.
        """
        start_elapsed = time.monotonic()

        job_sequence = self.instance.get_eddub_job_sequence()

        schedule, obj_value = self._dispatch_by_sequence(
            job_sequence,
            dispatcher=dispatcher,
            dispatching_criteria=dispatching_criteria,
        )

        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,
        )
        self._register(
            report,
            FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=None),
        )
        return report

    def _initialize_by_reversed_sequence(
        self, sequence_getter: Callable[[], Sequence[str]]
    ) -> SubroutineReport:
        """Time ``sequence_getter()``, dispatch via the reverse-instance + IIT
        pipeline (:meth:`_dispatch_by_reversed_sequence_with_iit`), then
        register the resulting incumbent.
        """
        start_elapsed = time.monotonic()
        job_sequence = sequence_getter()
        schedule, obj_value = self._dispatch_by_reversed_sequence_with_iit(job_sequence)
        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,
        )
        self._register(
            report,
            FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=None),
        )
        return report

    def initialize_by_due2_weight_pos(self) -> SubroutineReport:
        """Step method: seed an incumbent by dispatching jobs in
        ``due2-weight-pos`` order (see
        :meth:`FFcDDWParameters.get_due2_weight_pos_job_sequence`) via the
        reverse-instance + IIT pipeline.
        """
        return self._initialize_by_reversed_sequence(
            self.instance.get_due2_weight_pos_job_sequence
        )

    def initialize_by_w1(self) -> SubroutineReport:
        """Step method: seed an incumbent by dispatching jobs in the
        ``w1`` order — descending by ``(w⁺_j - w⁻_j)`` — via the
        reverse-instance + IIT pipeline.
        """
        return self._initialize_by_reversed_sequence(self.instance.get_w1_job_sequence)

    def initialize_by_wxd1(self) -> SubroutineReport:
        """Step method: seed an incumbent by dispatching jobs in the
        ``wxd1`` order — early group (``d_j - d_bar < 0``) sorted ascending
        by ``(w⁺_j - 2·w⁻_j + 2·w_max) * (d_j - d_bar)``, late group
        (``>= 0``) sorted ascending by
        ``(w⁻_j - 2·w⁺_j + 2·w_max) * (d_j - d_bar)``, concatenated
        early ++ late — via the reverse-instance + IIT pipeline.
        """
        return self._initialize_by_reversed_sequence(
            self.instance.get_wxd1_job_sequence
        )

    def initialize_by_wxd2(self) -> SubroutineReport:
        """Step method: seed an incumbent by dispatching jobs in the
        ``wxd2`` order — partition by aversion scores
        (ea = w⁻_j + (d⁻_j - d̄), ta = w⁺_j + (d̄ - d⁺_j)):
        early group (ta > ea) sorted ascending by
        ``(w⁺_j - 2·w⁻_j + 2·ew_max) * (d⁻_j - d̄)``, late group
        (ta ≤ ea) sorted ascending by
        ``(w⁻_j - 2·w⁺_j + 2·tw_max) * (d⁺_j - d̄)``, concatenated
        early ++ late — via the reverse-instance + IIT pipeline.
        """
        return self._initialize_by_reversed_sequence(
            self.instance.get_wxd2_job_sequence
        )

    def initialize_by_eddub_twt(self, factor: int = 1) -> SubroutineReport:
        """Step method: seed an incumbent by dispatching jobs in EDDUB+w⁺ order.

        Sort by ``(d⁺_j asc, w⁺_j desc, position asc)``
        (:meth:`FFcDDWParameters.get_eddub_twt_job_sequence`). Feed that
        sequence into the reverse-instance + IIT pipeline
        (:meth:`_dispatch_by_reversed_sequence_with_iit`) — i.e. flip stages,
        mixed-dispatch in reverse priority order, un-flip, and apply
        ``make_semi_active`` → ``insert_idle_time``. Same family as
        ``initialize_by_w1`` / ``initialize_by_wxd*``; the pipeline differs
        from the forward-dispatch ``initialize_by_edd``.

        When ``factor > 1``, the instance is coarsened (time units divided by
        ``factor``) before dispatch, then the coarse schedule is reconstructed
        onto the original scale via :func:`reconstruct_coarse_schedule`.
        ``factor == 1`` is identical to the no-factor path.
        """
        if factor == 1:
            return self._initialize_by_reversed_sequence(
                self.instance.get_eddub_twt_job_sequence
            )

        start_elapsed = time.monotonic()

        coarsened = FFcDDWParameters.coarsen_time_resolution(self.instance, factor)
        coarse_sched, _coarse_obj = self._dispatch_by_reversed_sequence_with_iit(
            coarsened.get_eddub_twt_job_sequence(), instance=coarsened
        )
        schedule = reconstruct_coarse_schedule(coarse_sched, self.instance, factor)
        sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, self.instance)
        obj_value = float(sum_e + sum_t)

        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,
        )
        self._register(
            report,
            FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=None),
        )
        return report

    def initialize_by_reversed_dispatch(
        self, sequence: DispatchSeqKey
    ) -> SubroutineReport:
        """Step: ``sequence`` 규칙으로 정렬한 뒤 reverse-instance + IIT pipeline
        (:meth:`_dispatch_by_reversed_sequence_with_iit`)으로 incumbent를 seed한다.

        디코더(stage-flip → mixed dispatch(역순) → un-flip → make_semi_active →
        insert_idle_time)는 고정이고 정렬 규칙만 ``sequence`` 로 바뀐다. 키는
        :func:`dispatch_seq_job_sequence` 참조. ``initialize_by_w1`` /
        ``initialize_by_eddub_twt`` 와 같은 reverse 계열이며, 이들을 단일 진입점으로
        일반화한 것이다.
        """
        return self._initialize_by_reversed_sequence(
            lambda: dispatch_seq_job_sequence(self.instance, sequence)
        )

    def initialize_by_simple_dispatch(
        self, sequence: DispatchSeqKey
    ) -> SubroutineReport:
        """Step: seed an incumbent by a single job-centric MixedDispatcher decode.

        ``sequence`` 를 forward 우선순위로 정렬한 뒤, 모든 job을 전 stage에 걸쳐
        job-centric으로 한 번에 dispatch한다
        (:meth:`MixedDispatcher.get_job_centric_schedule_by_sequence`,
        ``np = job_count`` head). decode 후 :meth:`FFcSchedule.make_semi_active` +
        :meth:`FFcSchedule.insert_idle_time` 으로 E/T timing 보정하며, reverse
        파이프라인(:meth:`_dispatch_by_reversed_sequence_with_iit`)과 동일한
        보정을 공유한다. 정렬 키는 :func:`dispatch_seq_job_sequence` 와 동일
        레지스트리를 쓴다(디코더 무관, sequence만 제공).
        """
        start_elapsed = time.monotonic()
        job_sequence = dispatch_seq_job_sequence(self.instance, sequence)
        dispatcher = MixedDispatcher(self.instance, logger=self.logger)
        schedule = dispatcher.get_job_centric_schedule_by_sequence(job_sequence)
        schedule.make_semi_active(self.instance.stage_2_job_2_p_map)
        schedule.insert_idle_time(
            self.instance.job_2_due_window_map,
            self.instance.job_2_ewt_map,
            self.instance.job_2_twt_map,
        )
        sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, self.instance)
        obj_value = float(sum_e + sum_t)

        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed, obj_value=obj_value, obj_bound=None
        )
        self._register(
            report,
            FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=None),
        )
        return report

    def run_profile_fixed_ns(
        self,
        cp_tl: float | str | None = None,
        solver_thread_cnt: int = 1,
        pf_method: PFMethod = "PF0",
        horizon_makespan_multiplier: float = 1.25,
    ) -> SubroutineReport:
        """Step method: warm-start CP-SAT from the incumbent by fixing its
        dispatch profile (precedence arcs derived from the incumbent's
        operation ordering), then solve under a time budget.
        """
        start_elapsed = time.monotonic()

        incumbent = self.solution_manager.get_incumbent()
        if incumbent is None or incumbent.schedule is None:
            raise RuntimeError(
                "run_profile_fixed_ns requires an incumbent schedule; "
                "chain it after a seeding subroutine such as run_mcf_lb."
            )

        instance = self.instance
        cp_tl_seconds = resolve_value_expr(
            cp_tl,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )
        if horizon_makespan_multiplier < 1.0:
            raise ValueError(
                "horizon_makespan_multiplier must be >= 1.0, got "
                f"{horizon_makespan_multiplier}"
            )
        horizon = max(
            1,
            int(math.ceil(incumbent.schedule.makespan * horizon_makespan_multiplier)),
        )

        builder = BaseModelBuilder()
        mdl, params, op_vars, et_vars = builder.build(instance, horizon=horizon)

        by_machine, stride_set = decode_pf_method(pf_method)
        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            mdl,
            params,
            op_vars,
            incumbent.schedule,
            profile_fix_by_machine=by_machine,
            machine_precedence_stride_set=stride_set,
        )
        start_map = incumbent.schedule.get_jik_2_start_time_map()
        end_map = incumbent.schedule.get_jik_2_end_time_map()
        BaseModelBuilder.apply_start_hints_from_start_time_map(
            mdl, params, op_vars, start_map
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            mdl, params, op_vars, end_map
        )
        if et_vars is not None:
            BaseModelBuilder.apply_et_hints_from_ref_schedule(
                mdl, params, et_vars, incumbent.schedule
            )

        solver = cp_model.CpSolver()
        if cp_tl_seconds is not None:
            solver.parameters.max_time_in_seconds = cp_tl_seconds
        solver.parameters.num_workers = solver_thread_cnt
        status = solver.solve(mdl)

        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

        if not has_solution:
            elapsed = time.monotonic() - start_elapsed
            self.logger.warning(
                "run_profile_fixed_ns: no feasible solution (status=%s)",
                solver.StatusName(status),
            )
            report = SubroutineReport(
                elapsed_time=elapsed,
                obj_value=None,
                obj_bound=None,
            )
            self._register(report, None)
            return report

        j_i_2_start = {
            (j, i): int(solver.Value(op_vars.op_start[j, i]))
            for j in params.j_list
            for i in params.i_list
        }
        j_i_2_end = {
            (j, i): int(solver.Value(op_vars.op_end[j, i]))
            for j in params.j_list
            for i in params.i_list
        }
        schedule = build_schedule_from_op_starts(instance, j_i_2_start, j_i_2_end)

        sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)
        obj_value = float(sum_e + sum_t)
        cp_obj = float(solver.objective_value)
        if obj_value != cp_obj:
            self.logger.warning(
                "run_profile_fixed_ns: post-build objective %.3f != CP-SAT "
                "objective %.3f",
                obj_value,
                cp_obj,
            )

        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,  # objBound by profile-fixed model is not a valid global bound
        )
        self._register(
            report,
            FFcDDWSolution(schedule=schedule, obj_value=obj_value),
        )
        return report

    def _build_flip_phase_path_getter(self) -> Callable[[str], Path] | None:
        """Return a callable that maps a phase_label (e.g. ``"01_incumbent"``)
        to the registered ``flip_makespan_cp_phase_schedule`` artifact path,
        or ``None`` if the artifact layout/scope is not bound (test/scripted
        runs).

        The closure prepends the call_context (``<step_idx>-<method_name>``)
        to the phase_label before resolving via ``artifact_path``, so files
        sort by subroutine-flow step on disk and don't collide with phases
        from other steps. Routing through ``artifact_path`` is what makes
        the reporter's ``find_artifacts`` call discover the file.
        """
        layout = self._artifact_layout
        scenario_name = self._artifact_scenario_name
        instance_name = self._artifact_instance_name
        if layout is None or scenario_name is None or instance_name is None:
            return None
        call_context = self._get_call_context_of_current_method()

        def _phase_path(phase_label: str) -> Path:
            return layout.artifact_path(
                "flip_makespan_cp_phase_schedule",
                phase_name=f"{call_context}_{phase_label}",
                scenario_name=scenario_name,
                instance_name=instance_name,
            )

        return _phase_path

    def run_flip_makespan_cp_from_incumbent(
        self,
        cp_tl: float | str | None = None,
        solver_thread_cnt: int = 1,
        log_search_progress: bool = False,
        emit_phase_schedules: bool = False,
    ) -> SubroutineReport:
        """Step method: stage-flip + makespan CP-SAT, warm-started from the
        incumbent.

        Right-shifts the incumbent's last stage (per-job ET-non-positive),
        time-flips the right-shifted incumbent onto the stage-reversed
        instance, fixes the (now-first) reverse-stage, hints the rest, and
        minimises makespan with CP-SAT. Re-flips and applies the standard
        ``make_semi_active`` + ``insert_idle_time`` post-process.

        ``cp_tl`` is the user-specified per-call cap (absolute seconds, or any
        :func:`resolve_value_expr` expression). The actual time budget passed
        to the dispatcher is the strict-min of ``cp_tl`` and the controller's
        remaining global time. ``cp_tl=None`` means "no per-call cap" — only
        the global time limit is enforced.

        ``emit_phase_schedules=True`` writes compact-JSON snapshots of the
        seven load-bearing intermediate schedules into the instance's
        progress zone, mirroring the ``mcf_lb_phase_schedule`` convention.
        """
        start_elapsed = time.monotonic()
        if self.is_stopping_condition():
            return self._make_stop_report(start_elapsed)

        instance = self.instance
        incumbent = self.solution_manager.get_incumbent()
        if incumbent is None or incumbent.schedule is None:
            raise RuntimeError(
                "run_flip_makespan_cp_from_incumbent requires an incumbent "
                "schedule; chain it after a seeding subroutine such as "
                "calc_mcf_lb_and_derive_full_sch."
            )

        cp_tl_resolved = resolve_value_expr(
            cp_tl,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )
        remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
        eff_tl_sec = (
            min(cp_tl_resolved, remaining_sec)
            if cp_tl_resolved is not None
            else remaining_sec
        )

        self.logger.info(
            "run_flip_makespan_cp_from_incumbent: effective=%.3fs (cp_tl=%s, "
            "remaining=%.3fs), incumbent_obj=%s",
            eff_tl_sec,
            f"{cp_tl_resolved:.3f}s" if cp_tl_resolved is not None else "None",
            remaining_sec,
            f"{incumbent.obj_value:.2f}" if incumbent.obj_value is not None else "None",
        )

        option = FlipMakespanCpOption(
            cp_tl_seconds=eff_tl_sec,
            solver_thread_cnt=solver_thread_cnt,
            log_search_progress=log_search_progress,
            solver_log_path_getter=self.get_file_path_for_subroutine,
            emit_phase_schedules=emit_phase_schedules,
            phase_schedule_path_getter=self._build_flip_phase_path_getter()
            if emit_phase_schedules
            else None,
        )
        spec = AlgSpec(
            instance=instance,
            option=option,
            ref_solution=incumbent.schedule,
            logger=self.logger,
            stop_predicate=self.is_stopping_condition,
        )
        record = FlipMakespanCpDispatcher().run(spec)

        elapsed = time.monotonic() - start_elapsed
        result = record.result
        obj_value = (
            float(result.obj_value)
            if result is not None and result.obj_value is not None
            else None
        )
        schedule = result.schedule if result is not None else None

        prev_obj = incumbent.obj_value
        if obj_value is None:
            obj_value_str = "None"
        elif prev_obj is None:
            obj_value_str = f"{int(obj_value)}"
        else:
            obj_value_str = f"{int(obj_value)}({int(obj_value) - int(prev_obj):+d})"
        self.logger.info(
            "run_flip_makespan_cp_from_incumbent: elapsed=%.3fs, obj_value=%s",
            elapsed,
            obj_value_str,
        )

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,
        )
        progress_log = record.progress_log or ()
        if schedule is not None:
            self._register(
                report,
                FFcDDWSolution(schedule=schedule, obj_value=obj_value),
                progress_log=progress_log,
            )
        else:
            self._register(report, None, progress_log=progress_log)
        return report

    def solve_base_model_cpsat(
        self,
        timelimit: float | str | None = None,
        solver_thread_cnt: int = 1,
        log_search_progress: bool = False,
        error_if_infeasible: bool = False,
        draw_gantt: bool = False,
        horizon_makespan_multiplier: float = 1.25,
    ) -> SubroutineReport:
        """Step method: solve the FFc-DDW base CP model on the full instance
        via :class:`CpsatAdapter`, optionally warm-started from the
        incumbent schedule.

        ``timelimit`` is the user-specified per-call cap (absolute seconds, or
        any expression supported by :func:`resolve_value_expr`). The actual
        time budget passed to the algorithm is the strict-min of ``timelimit``
        and the controller's remaining global time. ``timelimit=None`` means
        "no per-call cap" — only the global time limit is enforced.
        """
        start_elapsed = time.monotonic()
        instance = self.instance

        timelimit_resolved = resolve_value_expr(
            timelimit,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )
        remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
        eff_timelimit_sec = (
            min(timelimit_resolved, remaining_sec)
            if timelimit_resolved is not None
            else remaining_sec
        )

        incumbent = self.solution_manager.get_incumbent()
        ref_solution = incumbent.schedule if incumbent is not None else None

        valid_lb = self.get_current_valid_lb()
        obj_lb = valid_lb if valid_lb > 0 else None

        self.logger.info(
            "solve_base_model_cpsat: effective=%.3fs (timelimit=%s, "
            "remaining=%.3fs), ref_solution=%s, obj_lb=%s",
            eff_timelimit_sec,
            f"{timelimit_resolved:.3f}s" if timelimit_resolved is not None else "None",
            remaining_sec,
            "given" if ref_solution is not None else "None",
            f"{obj_lb:.2f}" if obj_lb is not None else "None",
        )

        option = CpsatOption(
            timelimit_sec=eff_timelimit_sec,
            solver_thread_cnt=solver_thread_cnt,
            log_search_progress=log_search_progress,
            error_if_infeasible=error_if_infeasible,
            draw_gantt=draw_gantt,
            obj_lb=obj_lb,
            horizon_makespan_multiplier=horizon_makespan_multiplier,
        )
        spec = AlgSpec(
            instance=instance,
            option=option,
            ref_solution=ref_solution,
            logger=self.logger,
            stop_predicate=self.is_stopping_condition,
        )
        record = CpsatAdapter().run(spec)

        elapsed = time.monotonic() - start_elapsed
        result = record.result
        obj_value = (
            float(result.obj_value)
            if result is not None and result.obj_value is not None
            else None
        )
        obj_bound = (
            float(result.obj_bound)
            if result is not None and result.obj_bound is not None
            else None
        )
        schedule = result.schedule if result is not None else None

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=obj_bound,
        )
        cpsat_progress_log = record.progress_log or ()
        if schedule is not None:
            self._register(
                report,
                FFcDDWSolution(
                    schedule=schedule, obj_value=obj_value, obj_bound=obj_bound
                ),
                progress_log=cpsat_progress_log,
            )
        else:
            self._register(report, None, progress_log=cpsat_progress_log)
        return report

    def neh_cp(
        self,
        job_priority: NehCpJobPriority = "weight-due-pos",
        solver_thread_cnt: int = 1,
        added_batch_size: int = 1,
        extra_batch_size_expr: str | None = None,
        cp_tl: float | str | None = None,
        total_timelimit: float | str | None = None,
        num_batches: int | None = None,
        batch_tl_mode: BatchTlMode = "constant",
        batch_tl_offset_seconds: float = 0.01,
        apply_cumulative_tl: bool = False,
        pf_method: PFMethod = "PF1",
        skip_pf_below_obj: str | float | None = None,
        make_semi_active_after_cp: bool = False,
        make_semi_active_after_cp_obj_threshold: int = -1,
        minimize_makespan_lex: bool = False,
        cp_tl_2nd_obj: float | str | None = None,
        error_if_infeasible: bool = False,
    ) -> SubroutineReport:
        """Step method: run :class:`NehCpDispatcher` and register its schedule.

        Resolves expression-grammar inputs (``cp_tl`` / ``total_timelimit`` /
        ``cp_tl_2nd_obj`` / ``extra_batch_size_expr``) into pre-resolved
        scalars, hands them to :class:`NehCpOption`, dispatches via
        :class:`NehCpDispatcher`, then registers the resulting schedule and
        emits the per-batch ``_step_log.yaml`` next to the controller's
        working directory.
        """
        start_elapsed = time.monotonic()
        if self.is_stopping_condition():
            return self._make_stop_report(start_elapsed)
        instance = self.instance
        n = instance.job_count
        c = instance.stage_count
        m = instance.last_stage_mc_count

        cp_tl_seconds = resolve_value_expr(cp_tl, n, c, m)
        total_timelimit_seconds = (
            resolve_value_expr(total_timelimit, n, c, m)
            if total_timelimit is not None
            else None
        )
        cp_tl_2nd_obj_seconds = (
            resolve_value_expr(
                cp_tl_2nd_obj if cp_tl_2nd_obj is not None else cp_tl, n, c, m
            )
            if minimize_makespan_lex
            else None
        )
        extra_batch_size_extra = 0
        if num_batches is None and extra_batch_size_expr is not None:
            extra = resolve_value_expr(extra_batch_size_expr, n, c, m)
            if extra is not None:
                extra_batch_size_extra = int(extra)

        skip_pf_below_obj_resolved = NehCpOption.coerce_skip_pf_below_obj(
            skip_pf_below_obj
        )

        remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
        wall_clock_deadline_sec = time.monotonic() + remaining_sec

        valid_lb = self.get_current_valid_lb()
        obj_lb = valid_lb if valid_lb > 0 else None

        self.logger.info(
            "neh_cp: threading wall_clock_deadline=%.3fs (remaining=%.3fs), obj_lb=%s",
            wall_clock_deadline_sec,
            remaining_sec,
            f"{obj_lb:.2f}" if obj_lb is not None else "None",
        )

        option = NehCpOption(
            job_priority=job_priority,
            solver_thread_cnt=solver_thread_cnt,
            added_batch_size=added_batch_size,
            extra_batch_size_extra=extra_batch_size_extra,
            cp_tl_seconds=cp_tl_seconds,
            total_timelimit_seconds=total_timelimit_seconds,
            num_batches=num_batches,
            batch_tl_mode=batch_tl_mode,
            batch_tl_offset_seconds=batch_tl_offset_seconds,
            apply_cumulative_tl=apply_cumulative_tl,
            pf_method=pf_method,
            skip_pf_below_obj=skip_pf_below_obj_resolved,
            make_semi_active_after_cp=make_semi_active_after_cp,
            make_semi_active_after_cp_obj_threshold=make_semi_active_after_cp_obj_threshold,
            minimize_makespan_lex=minimize_makespan_lex,
            cp_tl_2nd_obj_seconds=cp_tl_2nd_obj_seconds,
            error_if_infeasible=error_if_infeasible,
            wall_clock_deadline_sec=wall_clock_deadline_sec,
            objective_lower_bound=obj_lb,
        )
        spec = AlgSpec(
            instance=instance,
            option=option,
            logger=self.logger,
            stop_predicate=self.is_stopping_condition,
        )
        record = NehCpDispatcher().run(spec)

        if record.termination_reason == TerminationReason.STOP_REQUESTED:
            stopped_after = (
                record.result.metrics.get("stopped_after_batch")
                if record.result is not None and record.result.metrics is not None
                else None
            )
            self.logger.info(
                "neh_cp: dispatcher stopped early after batch %s; "
                "registering recovered schedule.",
                stopped_after,
            )

        elapsed = time.monotonic() - start_elapsed
        result = record.result
        obj_value = (
            float(result.obj_value)
            if result is not None and result.obj_value is not None
            else None
        )
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,
        )
        neh_cp_progress_log = record.progress_log or ()
        if result is not None and result.schedule is not None:
            self._register(
                report,
                FFcDDWSolution(schedule=result.schedule, obj_value=obj_value),
                progress_log=neh_cp_progress_log,
            )
        else:
            self._register(report, None, progress_log=neh_cp_progress_log)

        if result is not None and result.metrics is not None:
            step_log = result.metrics.get("step_log")
            if step_log:
                log_path = self.try_get_file_path_for_subroutine("_step_log.yaml")
                if log_path is not None:
                    dump_yaml(
                        [entry.as_dict() for entry in step_log],
                        log_path,
                    )

        return report

    def pw_cp(
        self,
        solver_thread_cnt: int = 1,
        batch_size: int | float | str = "m",
        step_size: int = 1,
        unfixed_batch_count: int = 1,
        left_profile_fixed_batch_count: int = 0,
        right_profile_fixed_batch_count: int = 0,
        enable_promotion_profile_fixed: bool = False,
        pf_method: PFMethod = "PF1",
        cp_tl: float | str | None = None,
        total_timelimit: float | str | None = None,
        batch_tl_mode: BatchTlMode = "constant",
        batch_tl_offset_seconds: float = 0.01,
        apply_cumulative_tl: bool = False,
        error_if_infeasible: bool = False,
        keep_step_schedules: bool = False,
        log_search_progress: bool = False,
        log_search_progress_max_steps: int | None = None,
        debug_partition_gantt: bool = False,
        debug_partition_gantt_max_steps: int | None = None,
        draw_gantt: bool = False,
        horizon_makespan_multiplier: float = 1.25,
    ) -> SubroutineReport:
        """Step method: refine the incumbent via :class:`PwCpDispatcher`.

        Resolves expression-grammar inputs (``cp_tl`` / ``total_timelimit``)
        into pre-resolved scalars, hands them to :class:`PwCpOption`,
        dispatches via :class:`PwCpDispatcher` (with the controller-level
        wall-clock deadline and stop predicate threaded in), then
        registers the resulting schedule and emits the per-step
        ``_step_log.yaml`` next to the controller's working directory.

        ``draw_gantt=True`` snapshots the incumbent before/after the call
        into ``mcf_lb_phase_schedules`` so the post-run reporter renders
        them as PNGs (the container is generic despite the name).

        Per CLAUDE.md subroutine step contract: a single ``_register``
        per call, ``elapsed_time`` measured immediately before report
        construction with no work in between.
        """
        start_elapsed = time.monotonic()
        if self.is_stopping_condition():
            return self._make_stop_report(start_elapsed)

        instance = self.instance
        incumbent = self.solution_manager.get_incumbent()
        if incumbent is None or incumbent.schedule is None:
            raise RuntimeError(
                "pw_cp requires an incumbent schedule; chain it after a "
                "seeding subroutine such as calc_mcf_lb_and_derive_full_sch."
            )

        n = instance.job_count
        c = instance.stage_count
        m = instance.last_stage_mc_count
        cp_tl_seconds = resolve_value_expr(cp_tl, n, c, m)
        total_timelimit_seconds = (
            resolve_value_expr(total_timelimit, n, c, m)
            if total_timelimit is not None
            else None
        )
        batch_size_resolved = max(
            1, int(math.ceil(resolve_value_expr(batch_size, n, c, m)))
        )
        self.logger.info(
            "pw_cp: batch_size=%r -> %d (n=%d, c=%d, m=%d)",
            batch_size,
            batch_size_resolved,
            n,
            c,
            m,
        )

        remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
        wall_clock_deadline_sec = time.monotonic() + remaining_sec

        if draw_gantt:
            self._record_mcf_lb_phase(("pw_cp_before", incumbent.schedule.deepcopy()))

        debug_partition_gantt_path_getter = None
        if debug_partition_gantt:

            def _gantt_path(step_idx: int) -> Path | None:
                p = self.try_get_file_path_for_subroutine(
                    f"step_{step_idx:03d}_partition.svg"
                )
                if p is not None:
                    p.parent.mkdir(parents=True, exist_ok=True)
                return p

            debug_partition_gantt_path_getter = _gantt_path

        option = PwCpOption(
            solver_thread_cnt=solver_thread_cnt,
            batch_size=batch_size_resolved,
            step_size=step_size,
            unfixed_batch_count=unfixed_batch_count,
            left_profile_fixed_batch_count=left_profile_fixed_batch_count,
            right_profile_fixed_batch_count=right_profile_fixed_batch_count,
            enable_promotion_profile_fixed=enable_promotion_profile_fixed,
            pf_method=pf_method,
            cp_tl_seconds=cp_tl_seconds,
            total_timelimit_seconds=total_timelimit_seconds,
            batch_tl_mode=batch_tl_mode,
            batch_tl_offset_seconds=batch_tl_offset_seconds,
            apply_cumulative_tl=apply_cumulative_tl,
            wall_clock_deadline_sec=wall_clock_deadline_sec,
            error_if_infeasible=error_if_infeasible,
            keep_step_schedules=keep_step_schedules,
            log_search_progress=log_search_progress,
            log_search_progress_max_steps=log_search_progress_max_steps,
            debug_partition_gantt=debug_partition_gantt,
            debug_partition_gantt_max_steps=debug_partition_gantt_max_steps,
            debug_partition_gantt_path_getter=debug_partition_gantt_path_getter,
            horizon_makespan_multiplier=horizon_makespan_multiplier,
        )
        spec = AlgSpec(
            instance=instance,
            option=option,
            ref_solution=incumbent.schedule,
            logger=self.logger,
            stop_predicate=self.is_stopping_condition,
        )
        record = PwCpDispatcher().run(spec)

        elapsed = time.monotonic() - start_elapsed
        result = record.result
        obj_value = (
            float(result.obj_value)
            if result is not None and result.obj_value is not None
            else None
        )
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=None,
        )
        progress_log = record.progress_log or ()
        if result is not None and result.schedule is not None:
            self._register(
                report,
                FFcDDWSolution(schedule=result.schedule, obj_value=obj_value),
                progress_log=progress_log,
            )
        else:
            self._register(report, None, progress_log=progress_log)

        # Post-register diagnostics (per contract: after _register, not before).
        if draw_gantt and result is not None and result.schedule is not None:
            self._record_mcf_lb_phase(("pw_cp_after", result.schedule.deepcopy()))

        if result is not None and result.metrics is not None:
            step_log = result.metrics.get("step_log")
            if step_log:
                log_path = self.try_get_file_path_for_subroutine("_step_log.yaml")
                if log_path is not None:
                    dump_yaml(
                        [entry.as_dict() for entry in step_log],
                        log_path,
                    )

        return report

    def incremental_pw_cp(
        self,
        solver_thread_cnt: int = 1,
        batch_size: int | float | str = "m",
        step_size: int = 1,
        unfixed_batch_count_min: int = 1,
        unfixed_batch_count_max: int = 1,
        increment_unfixed_batch_count_flag: Literal[
            "always", "if_no_improvement"
        ] = "always",
        left_profile_fixed_batch_count: int = 0,
        right_profile_fixed_batch_count: int = 0,
        enable_promotion_profile_fixed: bool = False,
        pf_method: PFMethod = "PF1",
        cp_tl: float | str | None = None,
        total_timelimit: float | str | None = None,
        batch_tl_mode: BatchTlMode = "constant",
        batch_tl_offset_seconds: float = 0.01,
        apply_cumulative_tl: bool = False,
        error_if_infeasible: bool = False,
        keep_step_schedules: bool = False,
        log_search_progress: bool = False,
        log_search_progress_max_steps: int | None = None,
        draw_gantt: bool = False,
        horizon_makespan_multiplier: float = 1.25,
    ) -> None:
        """Composite step: iterate :meth:`pw_cp` over a range of
        ``unfixed_batch_count`` values.

        Mirrors ``hybridflowshop/controller/hfs_cp_lns.py:incremental_pw_cp``.
        For each ``count`` in ``[unfixed_batch_count_min, unfixed_batch_count_max]``:

        - ``"always"``: invoke ``self.pw_cp(unfixed_batch_count=count, ...)``
          once.
        - ``"if_no_improvement"``: invoke ``self.pw_cp(...)`` repeatedly at
          this count until a pass produces no improvement on the incumbent's
          weighted E+T (FFcDDW's primary objective, replacing
          hybridflowshop's makespan criterion).

        Each inner ``pw_cp`` call registers its own report in the standard
        way; this composite does not register itself. Per-iteration
        ``temporarily_extended_context`` tags each inner call's
        ``call_context`` so per-instance step-log paths don't collide
        across iterations. ``is_stopping_condition()`` short-circuits both
        loops cleanly.
        """
        if unfixed_batch_count_min < 1:
            raise ValueError("unfixed_batch_count_min must be >= 1")
        if unfixed_batch_count_max < unfixed_batch_count_min:
            raise ValueError(
                "unfixed_batch_count_max must be >= unfixed_batch_count_min"
            )
        if increment_unfixed_batch_count_flag not in {"always", "if_no_improvement"}:
            raise ValueError(
                "increment_unfixed_batch_count_flag must be one of "
                "{'always', 'if_no_improvement'}"
            )

        incumbent = self.solution_manager.get_incumbent()
        if incumbent is None or incumbent.schedule is None:
            raise RuntimeError(
                "incremental_pw_cp requires an incumbent schedule; chain it "
                "after a seeding subroutine such as "
                "calc_mcf_lb_and_derive_full_sch."
            )

        instance = self.instance
        batch_size_resolved = max(
            1,
            int(
                math.ceil(
                    resolve_value_expr(
                        batch_size,
                        instance.job_count,
                        instance.stage_count,
                        instance.last_stage_mc_count,
                    )
                )
            ),
        )
        self.logger.info(
            "incremental_pw_cp: batch_size=%r -> %d (m=%d)",
            batch_size,
            batch_size_resolved,
            instance.last_stage_mc_count,
        )

        base_kwargs = dict(
            solver_thread_cnt=solver_thread_cnt,
            batch_size=batch_size_resolved,
            step_size=step_size,
            left_profile_fixed_batch_count=left_profile_fixed_batch_count,
            right_profile_fixed_batch_count=right_profile_fixed_batch_count,
            enable_promotion_profile_fixed=enable_promotion_profile_fixed,
            pf_method=pf_method,
            cp_tl=cp_tl,
            total_timelimit=total_timelimit,
            batch_tl_mode=batch_tl_mode,
            batch_tl_offset_seconds=batch_tl_offset_seconds,
            apply_cumulative_tl=apply_cumulative_tl,
            error_if_infeasible=error_if_infeasible,
            keep_step_schedules=keep_step_schedules,
            log_search_progress=log_search_progress,
            log_search_progress_max_steps=log_search_progress_max_steps,
            draw_gantt=draw_gantt,
            horizon_makespan_multiplier=horizon_makespan_multiplier,
        )

        self.logger.info(
            "incremental_pw_cp: policy=%s, unfixed_batch_count=[%d, %d]",
            increment_unfixed_batch_count_flag,
            unfixed_batch_count_min,
            unfixed_batch_count_max,
        )

        for unfixed_batch_count in range(
            unfixed_batch_count_min, unfixed_batch_count_max + 1
        ):
            if self.is_stopping_condition():
                self.logger.info(
                    "incremental_pw_cp: stopping condition met before "
                    "unfixed_batch_count=%d",
                    unfixed_batch_count,
                )
                break

            context_name = f"batch_{unfixed_batch_count:03d}"
            with self.temporarily_extended_context(context_name):
                if increment_unfixed_batch_count_flag == "if_no_improvement":
                    self.logger.info(
                        "incremental_pw_cp[count=%d]: repeat-until-no-improvement.",
                        unfixed_batch_count,
                    )
                    rep = 0
                    while True:
                        if self.is_stopping_condition():
                            self.logger.info(
                                "incremental_pw_cp[count=%d]: stop after rep=%d.",
                                unfixed_batch_count,
                                rep,
                            )
                            break
                        rep += 1
                        obj_before = self.solution_manager.best_obj_value
                        with self.temporarily_extended_context(f"reps_{rep:03d}"):
                            self.pw_cp(
                                unfixed_batch_count=unfixed_batch_count,
                                **base_kwargs,
                            )
                        obj_after = self.solution_manager.best_obj_value
                        if (
                            obj_before is None
                            or obj_after is None
                            or obj_after >= obj_before
                        ):
                            self.logger.info(
                                "incremental_pw_cp[count=%d]: no improvement "
                                "(%s -> %s); advancing to next count.",
                                unfixed_batch_count,
                                f"{obj_before:.0f}"
                                if obj_before is not None
                                else "None",
                                f"{obj_after:.0f}" if obj_after is not None else "None",
                            )
                            break
                        self.logger.info(
                            "incremental_pw_cp[count=%d, rep=%d]: improved "
                            "%.0f -> %.0f; repeating.",
                            unfixed_batch_count,
                            rep,
                            obj_before,
                            obj_after,
                        )
                else:
                    self.logger.info(
                        "incremental_pw_cp[count=%d]: single pass.",
                        unfixed_batch_count,
                    )
                    self.pw_cp(unfixed_batch_count=unfixed_batch_count, **base_kwargs)

    def coarsen_solve_reconstruct(
        self,
        factor: int = 50,
        timelimit: float | str | None = None,
        solver_thread_cnt: int = 1,
        log_search_progress: bool = False,
        error_if_infeasible: bool = False,
        seed_dispatch: str = "mixed",
        draw_gantt: bool = False,
        emit_phase_schedules: bool = False,
        draw_cp_trajectory: bool = False,
    ) -> SubroutineReport:
        """Step method: coarsen the instance, solve the base CP, and
        reconstruct to the original scale.

        Coarsens all processing times and due-window bounds by ``factor``
        via ``ceil(value / factor)``, solves the coarsened model via
        :func:`run_coarsen_solve_reconstruct`, then inflates the raw
        coarse start times back to original scale and restores original
        processing times. Post-processing (``make_semi_active`` →
        ``insert_idle_time``) and objective evaluation are done against
        the original instance.

        ``timelimit`` is the user-specified per-call cap (absolute seconds,
        or any expression supported by :func:`resolve_value_expr`). The
        actual time budget passed to the algorithm is the strict-min of
        ``timelimit`` and the controller's remaining global time.
        ``timelimit=None`` means "no per-call cap" — only the global time
        limit is enforced.

        ``seed_dispatch`` selects the dispatch seed strategy for warm-start
        hints before solving: ``"job_wise"`` (single job-wise dispatch) or
        ``"mixed"`` (best among mixed-dispatch np-list candidates). Default
        is ``"mixed"``.

        ``draw_gantt`` is accepted for API consistency but does not
        currently render a Gantt chart; any post-work would be placed after
        ``_register``.

        ``emit_phase_schedules``: when ``True`` and the solve finds a
        solution, records three schedule snapshots onto
        ``self.csr_phase_schedules`` (1_coarse_solver_result,
        2_reconstructed_raw, 3_final) via ``_record_csr_phase``.

        ``draw_cp_trajectory``: when ``True`` and the solve finds a
        solution, captures the coarsened-scale CP-SAT UB/LB trajectory
        into ``self.csr_cp_trajectory``. The trajectory is NOT inserted
        into the report's ``progress_log`` (kept as a dedicated artifact
        only, separate from the shared obj_log).

        The two flags are independent — any combination is valid.

        Per CLAUDE.md subroutine step contract: a single ``_register`` per
        call, ``elapsed_time`` measured immediately before report
        construction with no work in between.
        """
        start_elapsed = time.monotonic()
        if self.is_stopping_condition():
            return self._make_stop_report(start_elapsed)

        instance = self.instance
        timelimit_resolved = resolve_value_expr(
            timelimit,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )
        remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
        eff_timelimit_sec = (
            min(timelimit_resolved, remaining_sec)
            if timelimit_resolved is not None
            else remaining_sec
        )

        self.logger.info(
            "coarsen_solve_reconstruct: factor=%d, effective=%.3fs "
            "(timelimit=%s, remaining=%.3fs)",
            factor,
            eff_timelimit_sec,
            f"{timelimit_resolved:.3f}s" if timelimit_resolved is not None else "None",
            remaining_sec,
        )

        option = CoarsenSolveReconstructOption(
            factor=factor,
            timelimit_sec=eff_timelimit_sec,
            solver_thread_cnt=solver_thread_cnt,
            log_search_progress=log_search_progress,
            error_if_infeasible=error_if_infeasible,
            seed_dispatch=seed_dispatch,
        )
        trace = run_coarsen_solve_reconstruct(instance, option, self.logger)

        elapsed = time.monotonic() - start_elapsed
        obj_value = float(trace.obj_value) if trace.obj_value is not None else None
        obj_bound = None  # CSR does not produce a valid global lower bound

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=obj_bound,
        )
        if trace.final_schedule is not None:
            self._register(
                report,
                FFcDDWSolution(
                    schedule=trace.final_schedule,
                    obj_value=obj_value,
                    obj_bound=obj_bound,
                ),
            )
        else:
            self._register(report, None)

        # Post-register artifact work (per contract: after _register, not before).
        # The two flags are evaluated independently so either can be used alone.
        if trace.final_schedule is not None:
            if emit_phase_schedules:
                self._record_csr_phase("1_coarse_solver_result", trace.coarse_schedule)
                self._record_csr_phase(
                    "2_reconstructed_raw", trace.reconstructed_raw_schedule
                )
                self._record_csr_phase("3_final", trace.final_schedule)
            if draw_cp_trajectory:
                self.csr_cp_trajectory = trace.cp_progress_log

        return report
