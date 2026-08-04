"""FFcDWwET subroutine controller for routix-based experiment orchestration."""

import csv
import json
import logging
import math
import time
from pathlib import Path
from typing import Callable, Iterable, Literal, Sequence

from ortools.sat.python import cp_model
from routix.constants import SubroutineFlowKeys
from routix.io import dump_yaml
from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.base.alg_record import ProgressLogEntry, TerminationReason
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.coarsen_solve_reconstruct import (
    DEFAULT_COARSEN_FACTOR,
    CoarsenSolveReconstructOption,
    CsrCandidate,
    dedup_candidates,
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
    build_v3_paired_dispatch_schedule,
    build_v4_paired_dispatch_schedule,
    dispatch_forward_with_iit,
    dispatch_reversed_with_iit,
)
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.algorithm.flip_makespan_cp import (
    FlipMakespanCpDispatcher,
    FlipMakespanCpOption,
)
from ffc_ddw_sum_et.algorithm.job_contrib_cp import (
    JobContribCpDispatcher,
    JobContribCpOption,
    select_jd_jobs,
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
from ffc_ddw_sum_et.algorithm.neh_cp.sequence import neh_cp_job_sequence
from ffc_ddw_sum_et.algorithm.pm_pmtn_sorter import PmPrmpSortKey
from ffc_ddw_sum_et.algorithm.step_tl_resolver import BatchTlMode
from ffc_ddw_sum_et.algorithm.sw_cp import (
    SwCpDispatcher,
    SwCpOption,
)
from ffc_ddw_sum_et.io import dump_preemptive_schedule_json, dump_solution_json
from ffc_ddw_sum_et.io.parallel_mc_cost_heatmap import HeatmapSort
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.parameters.sorter import (
    V3_PRIORITY_SET,
    V4_PRIORITY_SET,
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
    build_active_except_last_from_reference,
    build_active_from_reference,
    build_schedule_from_op_starts,
    reconstruct_active_coarse_schedule,
    reconstruct_active_except_last_coarse_schedule,
    reconstruct_coarse_schedule,
    reconstruct_raw_coarse_schedule,
)
from ffc_ddw_sum_et.solution.schedule_sequence import (
    ScheduleSeqSource,
    normalized_mean_rank_distance,
    schedule_job_sequence,
)

from .controller_core import FFcDDWSubroutineControllerCore, MCFLBPhaseSchedule
from .mcf_lb_phase_labels import (
    MCF_LB_R1_LABEL_ORDER,
    MCF_LB_R2_LABEL_ORDER,
)
from .solution_manager import FFcDDWSolution
from .value_resolver import resolve_jd_count_target, resolve_value_expr

__all__ = ["FFcDDWSubroutineController", "MCFLBDiagnostic", "NehCpJobPriority"]

_OBJ_IMPROVEMENT_TOLERANCE = 1e-6

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


def _best_valid_lb(bounds: Iterable[float | None]) -> float | None:
    """Return the tightest lower bound in ``bounds``, or ``None`` when none is
    present.

    A lower bound is tighter the *larger* it is, so "best" means ``max``. This
    matches the two other LB aggregation sites:
    ``solution_manager.FFcDDWSolutionManager._a_is_better_obj_bound``
    (``bound_a > bound_b``) and ``ffcddw_single_instance_runner``'s
    ``bestBound = max(...)``.

    Callers must gate validity themselves — every value passed in has to
    already be a valid LB for the *original* problem (see the soundness
    contract on ``_a_is_better_obj_bound``).
    """
    valid = [float(b) for b in bounds if b is not None]
    return max(valid) if valid else None


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
            (
                "3_lastS_only_from_mcf_lb_after_sa_iti",
                result.schedule,
            )
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
                (
                    f"{_BUILD_FULL_SCH_LABEL_TO_INDEX[label]}_{label}",
                    sched,
                )
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
            time_factor=self.time_factor,
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

    def _dispatch_by_simple_sequence_with_iit(
        self, job_sequence: Sequence[str]
    ) -> tuple[FFcSchedule, float]:
        """sd pipeline thin wrapper → dispatch_forward_with_iit(self.instance, ...)."""
        return dispatch_forward_with_iit(self.instance, job_sequence, self.logger)

    def _dispatch_by_reversed_sequence_with_iit(
        self,
        job_sequence: Sequence[str],
        instance: FFcDDWParameters | None = None,
    ) -> tuple[FFcSchedule, float]:
        """rd pipeline thin wrapper → dispatch_reversed_with_iit(instance or self.instance, ...)."""
        return dispatch_reversed_with_iit(
            instance or self.instance, job_sequence, self.logger
        )

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

    def _log_dispatch_seed_diagnostics(self, label: str, schedule: FFcSchedule) -> None:
        """DEBUG-log the E/T balance of a dispatch seed schedule.

        Records weighted earliness/tardiness, the tardiness share
        ``T/(E+T)``, and the early/on-time/tardy job counts on the last stage.
        Used to characterise *why* a job-priority rule wins in a given regime
        (e.g. confirming the ``T=0.6, R=0.2`` region is tardiness-dominated and
        observing how a WSPT-style rule shifts the balance). Called after
        ``_register`` so it adds no work to the step's timed trajectory.
        """
        if not self.logger.isEnabledFor(logging.DEBUG):
            return
        sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, self.instance)
        last_stage_id = self.instance.stage_id_list[-1]
        ddw = self.instance.job_2_due_window_map
        n_early = n_ontime = n_tardy = 0
        for job_id in self.instance.job_id_list:
            ct = schedule.get_job_end_time(last_stage_id, job_id)
            d_lower, d_upper = ddw[job_id]
            if ct < d_lower:
                n_early += 1
            elif ct > d_upper:
                n_tardy += 1
            else:
                n_ontime += 1
        n = self.instance.job_count
        self.logger.debug(
            "dispatch-seed[%s]: wE=%d wT=%d T/(E+T)=%.3f | "
            "early=%d ontime=%d tardy=%d tardy%%=%.1f",
            label,
            sum_e,
            sum_t,
            sum_t / max(sum_e + sum_t, 1),
            n_early,
            n_ontime,
            n_tardy,
            100.0 * n_tardy / n,
        )

    def _initialize_by_reversed_sequence(
        self,
        sequence_getter: Callable[[], Sequence[str]],
        diag_label: str | None = None,
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
        if diag_label is not None:
            self._log_dispatch_seed_diagnostics(diag_label, schedule)
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

    def initialize_by_eddub_twt(
        self,
        factor: int = 1,
        coarsen_mode: Literal["ceil", "round", "floor", "cumulative"] = "ceil",
    ) -> SubroutineReport:
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
        ``coarsen_mode`` selects the rounding rule (see
        :meth:`FFcDDWParameters.coarsen_processing_times`).
        ``factor == 1`` is identical to the no-factor path.
        """
        if factor == 1:
            return self._initialize_by_reversed_sequence(
                self.instance.get_eddub_twt_job_sequence
            )

        start_elapsed = time.monotonic()

        coarsened = FFcDDWParameters.coarsen_processing_times(
            self.instance, factor, mode=coarsen_mode
        )
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
            lambda: dispatch_seq_job_sequence(self.instance, sequence),
            diag_label=f"rd:{sequence}",
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
        schedule, obj_value = self._dispatch_by_simple_sequence_with_iit(job_sequence)

        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed, obj_value=obj_value, obj_bound=None
        )
        self._register(
            report,
            FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=None),
        )
        self._log_dispatch_seed_diagnostics(f"sd:{sequence}", schedule)
        return report

    def initialize_by_dispatch_v3(
        self, priorities: Sequence[DispatchSeqKey] = V3_PRIORITY_SET
    ) -> SubroutineReport:
        """Step: justification-v3 paired dispatch pool. 각 priority 를 sd/rd 두
        방향으로 디코드(2·len(priorities) 스케줄)한 뒤 weighted-ET 최소 incumbent
        하나만 register — history 에 점 하나. 기본 P* = {edd, wspt_twt, wxd2}.
        """
        start_elapsed = time.monotonic()
        best_sch, best_obj, best_label = build_v3_paired_dispatch_schedule(
            self.instance, priorities, self.logger
        )
        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed, obj_value=best_obj, obj_bound=None
        )
        self._register(
            report,
            FFcDDWSolution(schedule=best_sch, obj_value=best_obj, obj_bound=None),
        )
        self._log_dispatch_seed_diagnostics(f"v3:{best_label}", best_sch)
        return report

    def initialize_by_dispatch_v4(
        self,
        priorities: Sequence[DispatchSeqKey] = V4_PRIORITY_SET,
        reconstruct_mode: Literal["none", "active", "active_but_last_semi"] = "none",
    ) -> SubroutineReport:
        """Step: justification-v4 paired dispatch pool. 각 priority 를 sd/rd 두
        방향으로 디코드(2·len(priorities) 스케줄)한 뒤 weighted-ET 최소 incumbent
        하나만 register — history 에 점 하나. 기본 P* = {wxd2, wspt_twt, wxd7}.

        ``reconstruct_mode`` 는 pool 우승자에 적용할 재구성 방식이다. 어휘는
        :meth:`coarsen_solve_reconstruct` 의 동명 인자와 같으며, coarsening 이
        없는 여기서는 factor 되돌리기 경로인 ``"semi_active"`` 대신 "재구성하지
        않음"을 뜻하는 ``"none"`` 을 쓴다.

        * ``"none"`` (기본): seed 를 그대로 등록 — 기존 동작.
        * ``"active"``: :func:`reconstruct_active_coarse_schedule` — stage 별
          start-order 만 남기고 machine 을 earliest-start 로 재배정.
        * ``"active_but_last_semi"``: 마지막 stage 만 seed 의 machine 배정과
          순서를 유지 (:func:`reconstruct_active_except_last_coarse_schedule`).

        재구성을 켜면 ``factor=1`` / ``solve=False`` 로 돌린
        :meth:`coarsen_solve_reconstruct` 와 값이 같아진다 (CSR 래퍼를 빌려
        쓰지 않고 같은 초기해를 얻는 경로). 재구성은 **우승자 1개에만** 적용하며
        (후보 전체 재구성 후 최소 선택이 아님), seed 와 재구성 결과 중 좋은 쪽을
        고르지도 않는다 — 두 경로의 값 동일성을 유지하기 위해서다. 설계 근거:
        ``plans/experiment/20260728/dispatch_v4_reconstruct_mode.md``.
        """
        reconstructors = {
            "active": reconstruct_active_coarse_schedule,
            "active_but_last_semi": reconstruct_active_except_last_coarse_schedule,
        }
        if reconstruct_mode != "none" and reconstruct_mode not in reconstructors:
            raise ValueError(
                "initialize_by_dispatch_v4: reconstruct_mode must be one of "
                "'none', 'active', 'active_but_last_semi'; got "
                f"{reconstruct_mode!r}"
            )
        mode_tag = "" if reconstruct_mode == "none" else f"/{reconstruct_mode}"

        start_elapsed = time.monotonic()
        best_sch, best_obj, best_label = build_v4_paired_dispatch_schedule(
            self.instance, priorities, self.logger
        )
        if reconstruct_mode != "none":
            best_sch = reconstructors[reconstruct_mode](best_sch, self.instance)
            sum_e, sum_t = compute_weighted_earliness_tardiness(best_sch, self.instance)
            best_obj = float(sum_e + sum_t)
        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed, obj_value=best_obj, obj_bound=None
        )
        self._register(
            report,
            FFcDDWSolution(schedule=best_sch, obj_value=best_obj, obj_bound=None),
        )
        self._log_dispatch_seed_diagnostics(f"v4{mode_tag}:{best_label}", best_sch)
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
        mdl, params, op_vars, et_vars = builder.build(
            instance, horizon=horizon, time_factor=self.time_factor
        )

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

        sum_e, sum_t = compute_weighted_earliness_tardiness(
            schedule, instance, time_factor=self.time_factor
        )
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
            time_factor=self.time_factor,
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
            time_factor=self.time_factor,
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
        return self._run_neh_cp(
            job_seq_source=None,
            step_label="neh_cp",
            job_priority=job_priority,
            solver_thread_cnt=solver_thread_cnt,
            added_batch_size=added_batch_size,
            extra_batch_size_expr=extra_batch_size_expr,
            cp_tl=cp_tl,
            total_timelimit=total_timelimit,
            num_batches=num_batches,
            batch_tl_mode=batch_tl_mode,
            batch_tl_offset_seconds=batch_tl_offset_seconds,
            apply_cumulative_tl=apply_cumulative_tl,
            pf_method=pf_method,
            skip_pf_below_obj=skip_pf_below_obj,
            make_semi_active_after_cp=make_semi_active_after_cp,
            make_semi_active_after_cp_obj_threshold=make_semi_active_after_cp_obj_threshold,
            minimize_makespan_lex=minimize_makespan_lex,
            cp_tl_2nd_obj=cp_tl_2nd_obj,
            error_if_infeasible=error_if_infeasible,
        )

    def neh_cp_midpoint_seq(
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
        seq_tiebreak: ScheduleSeqSource | None = None,
    ) -> SubroutineReport:
        """NEH-CP with job sequence derived from incumbent via midpoint sort.

        Sorts by ``(first_stage_start + last_stage_end) / 2`` ascending,
        tie-broken by first-stage start then by ``job_priority`` rank.

        ``seq_tiebreak`` overrides the secondary sort key within midpoint
        tie groups. ``"completion"`` reverses the tie-group order relative
        to the default (first-stage start). Only meaningful for
        ``midpoint`` — other modes have no distinguishable tie-break keys.

        Falls back to ``job_priority`` when no incumbent schedule is
        available (warning logged). ``job_priority`` is always computed
        for tie-break rank and fallback.
        """
        return self._run_neh_cp(
            job_seq_source="midpoint",
            step_label="neh_cp_midpoint_seq",
            job_priority=job_priority,
            solver_thread_cnt=solver_thread_cnt,
            added_batch_size=added_batch_size,
            extra_batch_size_expr=extra_batch_size_expr,
            cp_tl=cp_tl,
            total_timelimit=total_timelimit,
            num_batches=num_batches,
            batch_tl_mode=batch_tl_mode,
            batch_tl_offset_seconds=batch_tl_offset_seconds,
            apply_cumulative_tl=apply_cumulative_tl,
            pf_method=pf_method,
            skip_pf_below_obj=skip_pf_below_obj,
            make_semi_active_after_cp=make_semi_active_after_cp,
            make_semi_active_after_cp_obj_threshold=make_semi_active_after_cp_obj_threshold,
            minimize_makespan_lex=minimize_makespan_lex,
            cp_tl_2nd_obj=cp_tl_2nd_obj,
            error_if_infeasible=error_if_infeasible,
            seq_tiebreak=seq_tiebreak,
        )

    def neh_cp_first_stage_seq(
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
        """NEH-CP with job sequence derived from incumbent via first-stage start sort.

        Sorts by first-stage start ascending, tie-broken by last-stage end
        then by ``job_priority`` rank.

        Falls back to ``job_priority`` when no incumbent schedule is
        available (warning logged). ``job_priority`` is always computed
        for tie-break rank and fallback.
        """
        return self._run_neh_cp(
            job_seq_source="first_stage",
            step_label="neh_cp_first_stage_seq",
            job_priority=job_priority,
            solver_thread_cnt=solver_thread_cnt,
            added_batch_size=added_batch_size,
            extra_batch_size_expr=extra_batch_size_expr,
            cp_tl=cp_tl,
            total_timelimit=total_timelimit,
            num_batches=num_batches,
            batch_tl_mode=batch_tl_mode,
            batch_tl_offset_seconds=batch_tl_offset_seconds,
            apply_cumulative_tl=apply_cumulative_tl,
            pf_method=pf_method,
            skip_pf_below_obj=skip_pf_below_obj,
            make_semi_active_after_cp=make_semi_active_after_cp,
            make_semi_active_after_cp_obj_threshold=make_semi_active_after_cp_obj_threshold,
            minimize_makespan_lex=minimize_makespan_lex,
            cp_tl_2nd_obj=cp_tl_2nd_obj,
            error_if_infeasible=error_if_infeasible,
        )

    def neh_cp_completion_seq(
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
        """NEH-CP with job sequence derived from incumbent via completion sort.

        Sorts by last-stage end ascending, tie-broken by first-stage start
        then by ``job_priority`` rank.

        Falls back to ``job_priority`` when no incumbent schedule is
        available (warning logged). ``job_priority`` is always computed
        for tie-break rank and fallback.
        """
        return self._run_neh_cp(
            job_seq_source="completion",
            step_label="neh_cp_completion_seq",
            job_priority=job_priority,
            solver_thread_cnt=solver_thread_cnt,
            added_batch_size=added_batch_size,
            extra_batch_size_expr=extra_batch_size_expr,
            cp_tl=cp_tl,
            total_timelimit=total_timelimit,
            num_batches=num_batches,
            batch_tl_mode=batch_tl_mode,
            batch_tl_offset_seconds=batch_tl_offset_seconds,
            apply_cumulative_tl=apply_cumulative_tl,
            pf_method=pf_method,
            skip_pf_below_obj=skip_pf_below_obj,
            make_semi_active_after_cp=make_semi_active_after_cp,
            make_semi_active_after_cp_obj_threshold=make_semi_active_after_cp_obj_threshold,
            minimize_makespan_lex=minimize_makespan_lex,
            cp_tl_2nd_obj=cp_tl_2nd_obj,
            error_if_infeasible=error_if_infeasible,
        )

    def _run_neh_cp(
        self,
        *,
        job_seq_source: ScheduleSeqSource | None,
        step_label: str,
        job_priority: NehCpJobPriority,
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
        seq_tiebreak: ScheduleSeqSource | None = None,
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
            "%s: threading wall_clock_deadline=%.3fs (remaining=%.3fs), obj_lb=%s",
            step_label,
            wall_clock_deadline_sec,
            remaining_sec,
            f"{obj_lb:.2f}" if obj_lb is not None else "None",
        )

        priority_sequence = neh_cp_job_sequence(instance, job_priority)
        rank_map = {job_id: idx for idx, job_id in enumerate(priority_sequence)}

        custom_job_sequence: tuple[str, ...] | None = None
        used_sequence: list[str] | None = None
        sequence_fallback = True
        if job_seq_source is not None:
            incumbent = self.solution_manager.get_incumbent()
            if incumbent is None or incumbent.schedule is None:
                self.logger.warning(
                    "%s: no incumbent schedule (job_seq_source=%s); "
                    "falling back to job_priority='%s'. "
                    "Chain this step after a seeding subroutine such as "
                    "calc_mcf_lb_and_derive_full_sch.",
                    step_label,
                    job_seq_source,
                    job_priority,
                )
            else:
                seq = schedule_job_sequence(
                    incumbent.schedule,
                    job_seq_source,
                    tiebreak_source=seq_tiebreak,
                    tiebreak_rank=rank_map,
                )
                instance_jobs = set(instance.job_id_list)
                seq_jobs = set(seq)
                if seq_jobs != instance_jobs:
                    self.logger.warning(
                        "%s: derived sequence has %d jobs vs instance's %d; "
                        "correcting.",
                        step_label,
                        len(seq),
                        len(instance_jobs),
                    )
                    seen: set[str] = set()
                    deduped: list[str] = []
                    for j in seq:
                        if j in instance_jobs and j not in seen:
                            deduped.append(j)
                            seen.add(j)
                    missing = [
                        j
                        for j in priority_sequence
                        if j not in seen and j in instance_jobs
                    ]
                    seq = deduped + missing
                custom_job_sequence = tuple(seq) if seq else None
                used_sequence = list(seq)
                sequence_fallback = False

                all_sources: list[ScheduleSeqSource] = [
                    "midpoint",
                    "first_stage",
                    "completion",
                ]
                all_seqs: dict[str, list[str]] = {}
                for src in all_sources:
                    all_seqs[src] = schedule_job_sequence(
                        incumbent.schedule,
                        src,
                        tiebreak_rank=rank_map,
                    )

                dist_to_priority = normalized_mean_rank_distance(
                    priority_sequence, list(seq)
                )
                prev_str = "N/A"
                if self._last_neh_job_sequence is not None:
                    dist_prev = normalized_mean_rank_distance(
                        self._last_neh_job_sequence, list(seq)
                    )
                    prev_str = f"{dist_prev:.4f}"
                head = list(seq)[:5]

                self.logger.info(
                    "%s: seq source=%s tiebreak=%s dist_to_midpoint=%.4f "
                    "dist_to_first_stage=%.4f "
                    "dist_to_completion=%.4f dist_to_job_priority=%.4f "
                    "dist_to_prev_neh=%s head=%s",
                    step_label,
                    job_seq_source,
                    seq_tiebreak if seq_tiebreak is not None else "default",
                    normalized_mean_rank_distance(all_seqs["midpoint"], list(seq)),
                    normalized_mean_rank_distance(all_seqs["first_stage"], list(seq)),
                    normalized_mean_rank_distance(all_seqs["completion"], list(seq)),
                    dist_to_priority,
                    prev_str,
                    head,
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
            time_factor=self.time_factor,
            custom_job_sequence=custom_job_sequence,
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
                "%s: dispatcher stopped early after batch %s; "
                "registering recovered schedule.",
                step_label,
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
                    if job_seq_source is None:
                        dump_yaml(
                            [entry.as_dict() for entry in step_log],
                            log_path,
                        )
                    else:
                        source_label = (
                            job_seq_source
                            if not sequence_fallback
                            else f"job_priority:{job_priority}"
                        )
                        dump_yaml(
                            {
                                "job_sequence_source": source_label,
                                "job_sequence_tiebreak": seq_tiebreak
                                if not sequence_fallback and seq_tiebreak is not None
                                else None,
                                "job_sequence_fallback": sequence_fallback,
                                "job_sequence": custom_job_sequence
                                if custom_job_sequence is not None
                                else list(priority_sequence),
                                "steps": [entry.as_dict() for entry in step_log],
                            },
                            log_path,
                        )

        if used_sequence is not None:
            self._last_neh_job_sequence = used_sequence

        return report

    def sw_cp(
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
        non_time_fixed_op_time_limit_multiplier: float | None = None,
        apply_cumulative_tl: bool = False,
        error_if_infeasible: bool = False,
        keep_step_schedules: bool = False,
        log_search_progress: bool = False,
        log_search_progress_max_steps: int | None = None,
        debug_partition_gantt: bool = False,
        debug_partition_gantt_max_steps: int | None = None,
        draw_gantt: bool = False,
        horizon_makespan_multiplier: float = 1.25,
        rj_right_justify_scope: Literal["rtf_only", "all_ops"] = "rtf_only",
    ) -> SubroutineReport:
        """Step method: refine the incumbent via :class:`SwCpDispatcher`.

        Resolves expression-grammar inputs (``cp_tl`` / ``total_timelimit``)
        into pre-resolved scalars, hands them to :class:`SwCpOption`,
        dispatches via :class:`SwCpDispatcher` (with the controller-level
        wall-clock deadline and stop predicate threaded in), then
        registers the resulting schedule and emits the per-step
        ``_step_log.yaml`` next to the controller's working directory.

        ``draw_gantt=True`` snapshots the incumbent before/after the call
        into ``mcf_lb_phase_schedules`` so the post-run reporter renders
        them as PNGs (the container is generic despite the name).

        Per AGENTS.md in this package: a single ``_register``
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
                "sw_cp requires an incumbent schedule; chain it after a "
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
            "sw_cp: batch_size=%r -> %d (n=%d, c=%d, m=%d)",
            batch_size,
            batch_size_resolved,
            n,
            c,
            m,
        )

        remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
        wall_clock_deadline_sec = time.monotonic() + remaining_sec

        if draw_gantt:
            self._record_mcf_lb_phase(("sw_cp_before", incumbent.schedule.deepcopy()))

        debug_partition_gantt_path_getter = None
        if debug_partition_gantt:

            def _gantt_path(step_idx: int, phase: str) -> Path | None:
                p = self.try_get_file_path_for_subroutine(
                    f"step_{step_idx:03d}_partition_{phase}.svg"
                )
                if p is not None:
                    p.parent.mkdir(parents=True, exist_ok=True)
                return p

            debug_partition_gantt_path_getter = _gantt_path

        option = SwCpOption(
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
            non_time_fixed_op_time_limit_multiplier=non_time_fixed_op_time_limit_multiplier,
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
            rj_right_justify_scope=rj_right_justify_scope,
            time_factor=self.time_factor,
        )
        spec = AlgSpec(
            instance=instance,
            option=option,
            ref_solution=incumbent.schedule,
            logger=self.logger,
            stop_predicate=self.is_stopping_condition,
        )
        record = SwCpDispatcher().run(spec)

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
            self._record_mcf_lb_phase(("sw_cp_after", result.schedule.deepcopy()))

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

    def incremental_sw_cp(
        self,
        solver_thread_cnt: int = 1,
        batch_size: int | float | str = "m",
        extra_batch_size_expr: int | float | str | None = None,
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
        non_time_fixed_op_time_limit_multiplier: float | None = None,
        apply_cumulative_tl: bool = False,
        error_if_infeasible: bool = False,
        keep_step_schedules: bool = False,
        log_search_progress: bool = False,
        log_search_progress_max_steps: int | None = None,
        draw_gantt: bool = False,
        horizon_makespan_multiplier: float = 1.25,
        rj_right_justify_scope: Literal["rtf_only", "all_ops"] = "rtf_only",
    ) -> None:
        """Composite step: iterate :meth:`sw_cp` over a range of
        ``unfixed_batch_count`` values.

        Mirrors ``hybridflowshop/controller/hfs_cp_lns.py:incremental_pw_cp``.
        ``unfixed_batch_count_max`` is clamped to the instance's actual batch
        count (``ceil(job_count / batch_size)``) so that iterations above
        ``batch_count`` — which are guaranteed dispatcher no-ops — are skipped.

        ``extra_batch_size_expr`` is an additive offset on the resolved
        ``batch_size``, mirroring ``neh_cp``'s parameter of the same name.
        :func:`resolve_value_expr` has no arithmetic grammar, so ``"m+2"``
        does not parse; express it as ``batch_size="m"`` plus
        ``extra_batch_size_expr=2``. The sum is floored at 1.

        For each ``count`` in ``[unfixed_batch_count_min, effective_max]``:

        - ``"always"``: invoke ``self.sw_cp(unfixed_batch_count=count, ...)``
          once.
        - ``"if_no_improvement"``: invoke ``self.sw_cp(...)`` repeatedly at
          this count until a pass produces no improvement on the incumbent's
          weighted E+T (FFcDDW's primary objective, replacing
          hybridflowshop's makespan criterion).

        Each inner ``sw_cp`` call registers its own report in the standard
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
                "incremental_sw_cp requires an incumbent schedule; chain it "
                "after a seeding subroutine such as "
                "calc_mcf_lb_and_derive_full_sch."
            )

        instance = self.instance
        n = instance.job_count
        c = instance.stage_count
        m = instance.last_stage_mc_count
        batch_size_resolved = int(math.ceil(resolve_value_expr(batch_size, n, c, m)))
        if extra_batch_size_expr is not None:
            extra = resolve_value_expr(extra_batch_size_expr, n, c, m)
            if extra is not None:
                batch_size_resolved += int(extra)
        batch_size_resolved = max(1, batch_size_resolved)
        self.logger.info(
            "incremental_sw_cp: batch_size=%r (+%r) -> %d (m=%d)",
            batch_size,
            extra_batch_size_expr,
            batch_size_resolved,
            m,
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
            non_time_fixed_op_time_limit_multiplier=non_time_fixed_op_time_limit_multiplier,
            apply_cumulative_tl=apply_cumulative_tl,
            error_if_infeasible=error_if_infeasible,
            keep_step_schedules=keep_step_schedules,
            log_search_progress=log_search_progress,
            log_search_progress_max_steps=log_search_progress_max_steps,
            draw_gantt=draw_gantt,
            horizon_makespan_multiplier=horizon_makespan_multiplier,
            rj_right_justify_scope=rj_right_justify_scope,
        )

        batch_count = math.ceil(instance.job_count / batch_size_resolved)
        effective_max = min(unfixed_batch_count_max, batch_count)
        self.logger.info(
            "incremental_sw_cp: policy=%s, unfixed_batch_count=[%d, %d] "
            "(requested_max=%d, batch_count=%d)",
            increment_unfixed_batch_count_flag,
            unfixed_batch_count_min,
            effective_max,
            unfixed_batch_count_max,
            batch_count,
        )

        for unfixed_batch_count in range(unfixed_batch_count_min, effective_max + 1):
            if self.is_stopping_condition():
                self.logger.info(
                    "incremental_sw_cp: stopping condition met before "
                    "unfixed_batch_count=%d",
                    unfixed_batch_count,
                )
                break

            context_name = f"batch_{unfixed_batch_count:03d}"
            with self.temporarily_extended_context(context_name):
                if increment_unfixed_batch_count_flag == "if_no_improvement":
                    self.logger.info(
                        "incremental_sw_cp[count=%d]: repeat-until-no-improvement.",
                        unfixed_batch_count,
                    )
                    rep = 0
                    while True:
                        if self.is_stopping_condition():
                            self.logger.info(
                                "incremental_sw_cp[count=%d]: stop after rep=%d.",
                                unfixed_batch_count,
                                rep,
                            )
                            break
                        rep += 1
                        obj_before = self.solution_manager.best_obj_value
                        with self.temporarily_extended_context(f"reps_{rep:03d}"):
                            self.sw_cp(
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
                                "incremental_sw_cp[count=%d]: no improvement "
                                "(%s -> %s); advancing to next count.",
                                unfixed_batch_count,
                                f"{obj_before:.0f}"
                                if obj_before is not None
                                else "None",
                                f"{obj_after:.0f}" if obj_after is not None else "None",
                            )
                            break
                        self.logger.info(
                            "incremental_sw_cp[count=%d, rep=%d]: improved "
                            "%.0f -> %.0f; repeating.",
                            unfixed_batch_count,
                            rep,
                            obj_before,
                            obj_after,
                        )
                else:
                    self.logger.info(
                        "incremental_sw_cp[count=%d]: single pass.",
                        unfixed_batch_count,
                    )
                    self.sw_cp(unfixed_batch_count=unfixed_batch_count, **base_kwargs)

    def coarsen_solve_reconstruct(
        self,
        factor: int = DEFAULT_COARSEN_FACTOR,
        coarsen_mode: Literal["ceil", "round", "floor", "cumulative"] = "ceil",
        reconstruct_mode: Literal[
            "semi_active", "active", "active_but_last_semi"
        ] = "semi_active",
        timelimit: float | str | None = None,
        solver_thread_cnt: int = 1,
        log_search_progress: bool = False,
        error_if_infeasible: bool = False,
        seed_dispatch: str = "mixed",
        solve: bool = True,
        draw_gantt: bool = False,
        emit_phase_schedules: bool = False,
        solve_flow: list[dict] | None = None,
        dump_csr_coarse: bool = False,
    ) -> SubroutineReport:
        """Step method: coarsen the instance, solve the base CP, and
        reconstruct to the original scale.

        Coarsens all processing times by ``factor`` using the rule selected
        by ``coarsen_mode`` (see
        :meth:`FFcDDWParameters.coarsen_processing_times` for the formulas).
        Solves the coarsened model via
        :func:`run_coarsen_solve_reconstruct`, then reconstructs onto
        the original scale by carrying machine assignment and per-machine
        job order. Post-processing (``insert_idle_time``) and objective
        evaluation are done against the original instance.

        ``reconstruct_mode`` selects how the coarse solution is carried onto
        the original scale: ``"semi_active"`` (default, prior behavior) freezes
        the coarse machine assignment and per-machine order; ``"active"`` keeps
        only the coarse per-stage operation start-order and re-assigns machines
        by earliest start (:func:`reconstruct_active_coarse_schedule`). The
        mode threads through both the direct path and ``solve_flow`` candidate
        reconstruction. Default preserves existing behavior; see
        plans/experiment/20260723/active_schedule_reconstruction.md.

        ``timelimit`` is the user-specified per-call cap (absolute seconds,
        or any expression supported by :func:`resolve_value_expr`). The
        actual time budget passed to the algorithm is the strict-min of
        ``timelimit`` and the controller's remaining global time.
        ``timelimit=None`` means "no per-call cap" — only the global time
        limit is enforced.

        ``seed_dispatch`` selects the dispatch seed strategy for warm-start
        hints before solving: ``"job_wise"`` (single job-wise dispatch),
        ``"mixed"`` (best among mixed-dispatch np-list candidates),
        ``"v3"`` (v3 paired-dispatch pool: priority×{sd,rd} min-wET on
        coarsened scale), or ``"v4"`` (v4 paired-dispatch with expanded
        priority set). Default is ``"mixed"``.

        ``solve``: when ``False``, skip the CP-SAT solve and use the dispatch
        seed directly as the coarse schedule (seed-only deterministic mode).
        The output equals the reconstruct of the seed — no CP noise, no
        re-run variance. ``cp_progress_log`` is empty; ``coarsened_status``
        is ``"SEED_ONLY"``. Default is ``True`` (preserve existing behavior).

        Idle insertion has a single rule since 2026-07-22 — the former
        ``idle_mode`` parameter (``"flooring"`` / ``"ceiling"`` /
        ``"lookahead"``) was removed from every layer and only the lookahead
        rule survives, on the coarse-grid seed and on the final original-scale
        post-process alike. See
        ``plans/experiment/20260722/csr_idle_mode_lookahead_only.md``.

        ``draw_gantt`` is accepted for API consistency but does not
        currently render a Gantt chart; any post-work would be placed after
        ``_register``.

        ``emit_phase_schedules``: when ``True`` and the solve finds a
        solution, records three schedule snapshots onto
        ``self.csr_phase_schedules`` (1_coarse_solver_result,
        2_reconstructed_raw, 3_final) via ``_record_csr_phase``.

        ``solve_flow`` (optional): when provided, a non-empty list of step
        dicts (same schema as a scenario ``subroutine_flow``) that REPLACES
        the built-in dispatch-seed init AND the hard-coded base-CP solve. The
        flow runs on the coarsened instance via a CHILD
        :class:`FFcDDWSubroutineController` (``time_factor=factor``, headless
        — no artifact layout / working directory bound). Every child
        registration with a schedule becomes a coarse candidate; candidates
        are structurally de-duplicated, each reconstructed to the original
        scale, validated-and-dropped if infeasible, scored by original-scale
        wET, and the argmin (earlier-wins tie-break) is registered as the
        incumbent (``obj_bound=None`` — a coarse solve is never a valid
        original-scale bound). ``seed_dispatch`` / ``solve`` are ignored in
        this mode (a warning is logged if set to non-defaults). See
        plans/experiment/20260711/csr_solve_flow.md §4. v1 solve_flow configs must keep
        child gantt / emission / log-search flags OFF (the child has no sink).

        ``dump_csr_coarse`` (``False`` by default): when ``True`` and
        ``solve_flow`` is set, dumps every deduped coarse candidate schedule
        as a compact JSON in the instance progress directory
        (``_csr_coarse_cand_<NN>_<source>.json``). Intended for offline
        reconstruction replay experiments; kept off by default to avoid
        high file counts on full-grid runs.

        Per AGENTS.md in this package: a single ``_register`` per
        call, ``elapsed_time`` measured immediately before report
        construction with no work in between.
        """
        start_elapsed = time.monotonic()
        if self.is_stopping_condition():
            return self._make_stop_report(start_elapsed)

        if solve_flow is not None:
            return self._coarsen_solve_reconstruct_via_flow(
                start_elapsed,
                factor=factor,
                coarsen_mode=coarsen_mode,
                reconstruct_mode=reconstruct_mode,
                timelimit=timelimit,
                error_if_infeasible=error_if_infeasible,
                seed_dispatch=seed_dispatch,
                solve=solve,
                emit_phase_schedules=emit_phase_schedules,
                solve_flow=solve_flow,
                dump_csr_coarse=dump_csr_coarse,
            )

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
            coarsen_mode=coarsen_mode,
            reconstruct_mode=reconstruct_mode,
            timelimit_sec=eff_timelimit_sec,
            solver_thread_cnt=solver_thread_cnt,
            log_search_progress=log_search_progress,
            error_if_infeasible=error_if_infeasible,
            seed_dispatch=seed_dispatch,
            solve=solve,
        )
        trace = run_coarsen_solve_reconstruct(instance, option, self.logger)

        elapsed = time.monotonic() - start_elapsed
        obj_value = float(trace.obj_value) if trace.obj_value is not None else None
        obj_bound: float | None = None
        if factor == 1:
            obj_bound = _best_valid_lb(e.obj_bound for e in trace.cp_progress_log)

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

        return report

    def _coarsen_solve_reconstruct_via_flow(
        self,
        start_elapsed: float,
        *,
        factor: int,
        coarsen_mode: Literal["ceil", "round", "floor", "cumulative"],
        reconstruct_mode: Literal["semi_active", "active", "active_but_last_semi"],
        timelimit: float | str | None,
        error_if_infeasible: bool,
        seed_dispatch: str,
        solve: bool,
        emit_phase_schedules: bool,
        solve_flow: list[dict],
        dump_csr_coarse: bool = False,
    ) -> SubroutineReport:
        """``coarsen_solve_reconstruct`` in ``solve_flow`` mode (plan §4).

        Runs ``solve_flow`` on the coarsened instance via a headless child
        controller, harvests + de-duplicates + reconstructs the coarse
        candidates, and registers the original-scale argmin (or a no-solution
        report). ``start_elapsed`` is the parent step-entry monotonic clock —
        the whole child run + reconstruction counts toward this step's
        ``elapsed_time`` (measured immediately before ``_register``).
        """
        instance = self.instance

        # --- validate solve_flow (non-empty list of parseable step dicts) ---
        if isinstance(solve_flow, (str, bytes)) or not isinstance(solve_flow, Sequence):
            raise ValueError("solve_flow must be a non-empty list of step dicts")
        solve_flow_list = list(solve_flow)
        if not solve_flow_list:
            raise ValueError("solve_flow must be a non-empty list of step dicts")
        for step in solve_flow_list:
            step_obj = step.to_obj() if hasattr(step, "to_obj") else step
            SubroutineFlowKeys.parse_step(step_obj)  # raises on malformed step

        # seed_dispatch / solve are meaningless in solve_flow mode (the flow
        # owns both seeding and solving). Warn only when set to non-defaults.
        if seed_dispatch != "mixed" or solve is not True:
            self.logger.warning(
                "coarsen_solve_reconstruct: solve_flow set together with explicit "
                "seed_dispatch=%r / solve=%r; both are ignored in solve_flow mode.",
                seed_dispatch,
                solve,
            )

        # --- resolve CSR budget (same as the legacy path) ---
        timelimit_resolved = resolve_value_expr(
            timelimit,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )
        remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
        csr_budget = (
            min(timelimit_resolved, remaining_sec)
            if timelimit_resolved is not None
            else remaining_sec
        )
        child_timelimit = min(csr_budget, remaining_sec)

        self.logger.info(
            "coarsen_solve_reconstruct[solve_flow]: factor=%d, child_timelimit=%.3fs "
            "(timelimit=%s, remaining=%.3fs), steps=%d",
            factor,
            child_timelimit,
            f"{timelimit_resolved:.3f}s" if timelimit_resolved is not None else "None",
            remaining_sec,
            len(solve_flow_list),
        )

        # --- coarsen + run the child controller headless ---
        coarse_instance = FFcDDWParameters.coarsen_processing_times(
            instance, factor, mode=coarsen_mode
        )
        child = FFcDDWSubroutineController(
            instance=coarse_instance,
            subroutine_flow=solve_flow_list,
            stopping_criteria={"timelimit": child_timelimit},
            time_factor=factor,
        )
        child_offset = time.monotonic() - start_elapsed
        child.run()

        # --- harvest candidates (every child registration with a schedule) ---
        raw_candidates: list[CsrCandidate] = []
        for rec in child.solution_manager.history:
            sol = rec.solution
            if sol is None or sol.schedule is None:
                continue
            source = getattr(rec.report, "step_label", None) or "unknown"
            report = rec.report
            sec_elapsed_step = (
                getattr(report, "start_time", 0.0) + report.elapsed_time
                if report is not None
                else None
            )
            raw_candidates.append(
                CsrCandidate(
                    source=source,
                    coarse_schedule=sol.schedule,
                    coarse_obj=sol.obj_value,
                    coarse_bound=sol.obj_bound,
                    sec_elapsed_step=sec_elapsed_step,
                )
            )
        deduped = dedup_candidates(raw_candidates)

        # Preserve child history for coarse-scale inner obj_log emission.
        self.csr_child_history = list(child.solution_manager.history)

        # --- dump coarse candidate schedules (config-flag-gated) ---
        if dump_csr_coarse:
            for idx, cand in enumerate(deduped):
                source_slug = str(cand.source).replace("/", "_").replace(" ", "_")
                fname = f"_csr_coarse_cand_{idx:02d}_{source_slug}.json"
                path = self.try_get_file_path_for_subroutine(fname)
                if path is not None:
                    dump_solution_json(
                        cand.coarse_schedule,
                        path,
                        compact=True,
                        instance_name=instance.name,
                        obj_value=cand.coarse_obj,
                    )

        # --- reconstruct + validate + score every deduped candidate ---
        candidate_rows: list[dict[str, object]] = []
        winner: CsrCandidate | None = None
        winner_final: FFcSchedule | None = None
        winner_obj: float | None = None
        dropped_count = 0
        for cand in deduped:
            recon_start = time.monotonic()
            restored_obj: float | None = None
            valid = False
            final_sch: FFcSchedule | None = None
            try:
                if reconstruct_mode == "active":
                    final_sch = reconstruct_active_coarse_schedule(
                        cand.coarse_schedule, instance
                    )
                elif reconstruct_mode == "active_but_last_semi":
                    final_sch = reconstruct_active_except_last_coarse_schedule(
                        cand.coarse_schedule, instance
                    )
                else:
                    final_sch = reconstruct_coarse_schedule(
                        cand.coarse_schedule, instance, factor
                    )
                self.check_feasibility(final_sch.get_jik_2_start_time_map())
                sum_e, sum_t = compute_weighted_earliness_tardiness(final_sch, instance)
                restored_obj = float(sum_e + sum_t)
                valid = True
            except Exception:
                self.logger.warning(
                    "coarsen_solve_reconstruct[solve_flow]: dropping invalid "
                    "candidate from %s",
                    cand.source,
                    exc_info=True,
                )
                dropped_count += 1
                final_sch = None
            recon_elapsed = time.monotonic() - recon_start
            candidate_rows.append(
                {
                    "source": cand.source,
                    "coarse_obj": cand.coarse_obj,
                    "coarse_bound": cand.coarse_bound,
                    "restored_obj": restored_obj,
                    "valid": valid,
                    "sec_elapsed_step": cand.sec_elapsed_step,
                    "sec_elapsed_recon": recon_elapsed,
                }
            )
            if valid and (winner_obj is None or restored_obj < winner_obj):
                winner = cand
                winner_final = final_sch
                winner_obj = restored_obj

        # --- no-candidate fallback (mirror legacy error_if_infeasible branch) ---
        if winner_final is None and error_if_infeasible:
            raise RuntimeError(
                "coarsen_solve_reconstruct[solve_flow]: no feasible candidate "
                f"survived reconstruction for {instance.name} "
                f"(candidates={len(raw_candidates)}, deduped={len(deduped)})."
            )

        # --- build progress_log from candidate_rows (plan §3) ---
        call_context = self._get_call_context_of_current_method()
        progress_log_entries: list[ProgressLogEntry] = []
        running_min_obj: float | None = None
        for row in candidate_rows:
            rst = row["restored_obj"]
            if rst is None:
                continue
            rst_f = float(rst)
            if running_min_obj is None or rst_f < running_min_obj:
                running_min_obj = rst_f
            elapsed_sec = child_offset + float(row["sec_elapsed_step"])
            obj_bound_val: float | None = None
            note_val: str | None = None
            if factor == 1 and row.get("coarse_bound") is not None:
                obj_bound_val = float(row["coarse_bound"])
            src = str(row["source"])
            if src != "unknown":
                note_val = f"{call_context}-{src}"
            progress_log_entries.append(
                ProgressLogEntry(
                    elapsed_sec=elapsed_sec,
                    obj_value=running_min_obj,
                    obj_bound=obj_bound_val,
                    note=note_val,
                )
            )
        progress_log = tuple(progress_log_entries)

        # --- compute best child LB at factor==1 (original-scale valid) ---
        best_child_bound: float | None = None
        if factor == 1:
            best_child_bound = _best_valid_lb(
                rec.report.obj_bound
                for rec in self.csr_child_history
                if rec.report is not None
            )

        # --- measure elapsed, then register EXACTLY ONCE (contract) ---
        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=winner_obj,
            obj_bound=best_child_bound,
        )
        if winner_final is not None:
            self._register(
                report,
                FFcDDWSolution(
                    schedule=winner_final,
                    obj_value=winner_obj,
                    obj_bound=best_child_bound,
                ),
                progress_log=progress_log,
            )
        else:
            self._register(report, None, progress_log=progress_log)

        # --- post-register work (per contract: after _register, not before) ---
        self.csr_candidate_rows.extend(candidate_rows)
        self.csr_solve_flow_summary = {
            "candidate_count": len(raw_candidates),
            "deduped_count": len(deduped),
            "dropped_count": dropped_count,
            "winner_source": winner.source if winner is not None else None,
            "winner_coarse_obj": winner.coarse_obj if winner is not None else None,
            "winner_original_obj": winner_obj,
        }
        self.logger.info(
            "coarsen_solve_reconstruct[solve_flow]: candidates=%d deduped=%d "
            "dropped=%d winner_source=%s winner_coarse_obj=%s winner_original_obj=%s",
            len(raw_candidates),
            len(deduped),
            dropped_count,
            winner.source if winner is not None else None,
            winner.coarse_obj if winner is not None else None,
            f"{winner_obj:.3f}" if winner_obj is not None else None,
        )
        if winner_final is not None:
            if emit_phase_schedules:
                self._record_csr_phase("1_coarse_solver_result", winner.coarse_schedule)
                raw_snapshot = (
                    build_active_from_reference(
                        winner.coarse_schedule, instance, instance.stage_2_job_2_p_map
                    )
                    if reconstruct_mode == "active"
                    else (
                        build_active_except_last_from_reference(
                            winner.coarse_schedule,
                            instance,
                            instance.stage_2_job_2_p_map,
                        )
                        if reconstruct_mode == "active_but_last_semi"
                        else reconstruct_raw_coarse_schedule(
                            winner.coarse_schedule, instance, factor
                        )
                    )
                )
                self._record_csr_phase("2_reconstructed_raw", raw_snapshot)
                self._record_csr_phase("3_final", winner_final)

        return report

    def job_contrib_cp(
        self,
        jd_target: int | str = 1,
        pf_method: PFMethod = "PF1",
        cp_tl: float | str | None = None,
        cp_tl_mode: Literal["constant", "proportional"] = "constant",
        destroyed_op_tl_multiplier: float | None = None,
        solver_thread_cnt: int = 1,
        horizon_multiplier: float = 1.25,
        error_if_infeasible: bool = False,
        log_search_progress: bool = False,
        draw_gantt: bool = False,
    ) -> SubroutineReport:
        """Step method: destroy top-contributing jobs, CP-SAT re-inserts.

        Resolves ``jd_target`` (``1`` / ``"2"`` / ``"0.05n"``) into
        ``jd_count_target`` (int ≥ 1), hands it to
        :class:`JobContribCpOption`, dispatches via
        :class:`JobContribCpDispatcher`, then registers the result.
        """
        start_elapsed = time.monotonic()
        if self.is_stopping_condition():
            return self._make_stop_report(start_elapsed)

        incumbent = self.solution_manager.get_incumbent()
        if incumbent is None or incumbent.schedule is None:
            raise RuntimeError(
                "job_contrib_cp requires an incumbent schedule; chain it after a "
                "seeding subroutine such as calc_mcf_lb_and_derive_full_sch."
            )

        instance = self.instance
        n = instance.job_count
        c = instance.stage_count
        m = instance.last_stage_mc_count
        cp_tl_seconds = resolve_value_expr(cp_tl, n, c, m)
        jd_count_target = resolve_jd_count_target(jd_target, n)
        self.logger.info(
            "job_contrib_cp: jd_target=%r -> jd_count_target=%d (n=%d)",
            jd_target,
            jd_count_target,
            n,
        )
        remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
        wall_clock_deadline_sec = time.monotonic() + remaining_sec

        incumbent_copy = incumbent.schedule.deepcopy() if draw_gantt else None

        option = JobContribCpOption(
            jd_count_target=jd_count_target,
            pf_method=pf_method,
            horizon_multiplier=horizon_multiplier,
            cp_tl_seconds=cp_tl_seconds,
            cp_tl_mode=cp_tl_mode,
            destroyed_op_tl_multiplier=destroyed_op_tl_multiplier,
            wall_clock_deadline_sec=wall_clock_deadline_sec,
            solver_thread_cnt=solver_thread_cnt,
            time_factor=self.time_factor,
            error_if_infeasible=error_if_infeasible,
            log_search_progress=log_search_progress,
            solver_log_path_getter=self.get_file_path_for_subroutine,
        )
        spec = AlgSpec(
            instance=instance,
            option=option,
            ref_solution=incumbent.schedule,
            logger=self.logger,
            stop_predicate=self.is_stopping_condition,
        )
        record = JobContribCpDispatcher().run(spec)

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
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=obj_value,
            obj_bound=obj_bound,
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

        # Logged after _register so the log IO stays outside the measured
        # elapsed_time window (see orchestration/AGENTS.md, invariant 2).
        metrics = result.metrics if result is not None else None
        self._last_cp_progress = (
            metrics.get("cp_progress") if metrics is not None else None
        )
        self.logger.info(
            "job_contrib_cp: work_status=%s, cpsat_status=%s, "
            "jd_count_eff=%s, obj=%s, elapsed=%.3fs",
            record.work_status.name,
            metrics.get("cpsat_status", "N/A") if metrics is not None else "N/A",
            metrics.get("jd_count_eff", "N/A") if metrics is not None else "N/A",
            f"{obj_value:.1f}" if obj_value is not None else "N/A",
            elapsed,
        )

        if draw_gantt and incumbent_copy is not None:
            selected = (
                result.metrics.get("selected_jobs")
                if result is not None and result.metrics is not None
                else None
            )
            hl: set[str] | None = set(selected) if selected else None
            self._record_mcf_lb_phase(
                ("job_contrib_cp_before", incumbent_copy),
                highlight_jobs=hl,
            )
            if result is not None and result.schedule is not None:
                self._record_mcf_lb_phase(
                    ("job_contrib_cp_after", result.schedule.deepcopy()),
                    highlight_jobs=hl,
                )

        if result is not None and result.metrics is not None:
            log_path = self.try_get_file_path_for_subroutine("_metrics.yaml")
            if log_path is not None:
                dump_yaml(dict(result.metrics), log_path)

        return report

    def incremental_job_contrib_cp(
        self,
        jd_start: int | str = 1,
        jd_end: int | str = "0.1n",
        jd_step_size: int = 1,
        destroyed_op_tl_multiplier: float = 0.005,
        min_remaining_sec: float | None = None,
        pf_method: PFMethod = "PF1",
        solver_thread_cnt: int = 1,
        horizon_multiplier: float = 1.25,
        error_if_infeasible: bool = False,
        log_search_progress: bool = False,
        draw_gantt: bool = False,
    ) -> None:
        """Composite step: iterate :meth:`job_contrib_cp` over a ramp of
        ``jd`` (destroy-count) values.

        Each inner ``job_contrib_cp`` call registers independently via
        ``temporarily_extended_context`` namespacing. The composite registers
        its own endpoint at the end (or on early exit) so charts show a
        top-level marker closing this flow section.
        """
        incumbent = self.solution_manager.get_incumbent()
        if incumbent is None or incumbent.schedule is None:
            raise RuntimeError(
                "incremental_job_contrib_cp requires an incumbent schedule; "
                "chain it after a seeding subroutine such as "
                "calc_mcf_lb_and_derive_full_sch."
            )

        # Step entry — every ``_register`` below reports elapsed against this,
        # and per-iteration CP progress is offset from it.
        step_start = time.monotonic()

        instance = self.instance
        n = instance.job_count
        c = instance.stage_count

        jd_start_cnt = resolve_jd_count_target(jd_start, n)
        jd_end_cnt = resolve_jd_count_target(jd_end, n)

        if jd_step_size < 1:
            raise ValueError(f"jd_step_size must be >= 1, got {jd_step_size}")

        if jd_end_cnt < jd_start_cnt:
            raise ValueError(
                f"jd_end ({jd_end!r} -> {jd_end_cnt}) must be >= "
                f"jd_start ({jd_start!r} -> {jd_start_cnt})"
            )

        if jd_start_cnt >= n:
            self.logger.info(
                "incremental_job_contrib_cp: jd_start=%d >= n=%d, "
                "nothing to do (no meaningful neighbourhood)",
                jd_start_cnt,
                n,
            )
            self._dump_incremental_job_contrib_cp_log("jd_ge_n", [])
            # C3: register the composite's own endpoint on early exit too.
            elapsed = time.monotonic() - step_start
            self._register(
                SubroutineReport(
                    elapsed_time=elapsed,
                    obj_value=self.solution_manager.best_obj_value,
                    obj_bound=None,
                ),
                self.solution_manager.get_incumbent(),
            )
            return

        level_list = list(range(jd_start_cnt, jd_end_cnt + 1, jd_step_size))
        self.logger.info(
            "incremental_job_contrib_cp: jd_start=%r -> %d, jd_end=%r -> %d, "
            "jd_step_size=%d, levels=%s (n=%d, c=%d)",
            jd_start,
            jd_start_cnt,
            jd_end,
            jd_end_cnt,
            jd_step_size,
            level_list,
            n,
            c,
        )

        base_kwargs: dict = dict(
            cp_tl_mode="proportional",
            destroyed_op_tl_multiplier=destroyed_op_tl_multiplier,
            pf_method=pf_method,
            solver_thread_cnt=solver_thread_cnt,
            horizon_multiplier=horizon_multiplier,
            error_if_infeasible=error_if_infeasible,
            log_search_progress=log_search_progress,
            draw_gantt=draw_gantt,
        )

        summary_rows: list[dict] = []
        exit_reason: str = "completed"
        last_cp_tl_seconds: float | None = None
        # Skipped-iteration counter. ``rows`` holds one entry per CP solve, so
        # same-destroy-set skips would otherwise be invisible outside the log.
        same_set_skips = 0
        all_cp_progress: list[dict] = []

        try:
            _outer_break = False
            for jd in level_list:
                if jd >= n:
                    self.logger.info(
                        "incremental_job_contrib_cp: jd=%d >= n=%d, "
                        "stopping (no meaningful neighbourhood)",
                        jd,
                        n,
                    )
                    exit_reason = "jd_ge_n"
                    break

                if self.is_stopping_condition():
                    self.logger.info(
                        "incremental_job_contrib_cp: stopping condition met before jd=%d",
                        jd,
                    )
                    exit_reason = "stopping_condition"
                    break

                prev_selected: list[str] | None = None
                rep = 0
                while True:
                    if self.is_stopping_condition():
                        self.logger.info(
                            "incremental_job_contrib_cp[jd=%d]: stopping "
                            "condition met at rep=%d",
                            jd,
                            rep,
                        )
                        exit_reason = "stopping_condition"
                        _outer_break = True
                        break

                    tl = self.stopping_criteria.timelimit
                    remaining = self.timer.get_remaining_sec(tl)
                    dynamic_min = (
                        last_cp_tl_seconds / 2.0
                        if last_cp_tl_seconds is not None
                        else destroyed_op_tl_multiplier * jd * c / 2.0
                    )
                    effective_min = (
                        min_remaining_sec
                        if min_remaining_sec is not None
                        else dynamic_min
                    )
                    if remaining < effective_min:
                        self.logger.info(
                            "incremental_job_contrib_cp[jd=%d]: budget "
                            "exhausted before rep=%d "
                            "(remaining=%.3fs < min=%.3fs)",
                            jd,
                            rep + 1,
                            remaining,
                            effective_min,
                        )
                        exit_reason = "budget"
                        _outer_break = True
                        break

                    rep += 1
                    cur_incumbent = self.solution_manager.get_incumbent()
                    selected = select_jd_jobs(
                        cur_incumbent.schedule,
                        instance,
                        jd,
                        time_factor=self.time_factor,
                    )

                    if not selected:
                        self.logger.info(
                            "incremental_job_contrib_cp[jd=%d]: zero "
                            "positive-contribution jobs, stopping",
                            jd,
                        )
                        exit_reason = "zero_obj"
                        _outer_break = True
                        break

                    jd_count_eff = len(selected)
                    saturated = jd_count_eff < jd

                    if selected == prev_selected:
                        # No row is appended: this iteration is *skipped* before any
                        # CP solve, so counting it would break the summary's
                        # "one row per CP solve" invariant (Phase B divides by it).
                        # ``same_set_skips`` keeps the occurrence observable.
                        same_set_skips += 1
                        self.logger.info(
                            "incremental_job_contrib_cp[jd=%d, rep=%d]: "
                            "same destroy set as previous; advancing jd "
                            "(skipped, no CP solve)",
                            jd,
                            rep,
                        )
                        break

                    prev_selected = list(selected)

                    obj_before = self.solution_manager.best_obj_value
                    iter_start = time.monotonic()
                    context_name = f"jd{jd:03d}_r{rep:03d}"
                    self._last_cp_progress = None
                    with self.temporarily_extended_context(context_name):
                        self.job_contrib_cp(
                            jd_target=jd,
                            **base_kwargs,
                        )
                    iter_elapsed = time.monotonic() - iter_start
                    obj_after = self.solution_manager.best_obj_value

                    cp = getattr(self, "_last_cp_progress", None)
                    if cp:
                        offset = iter_start - step_start
                        for entry in cp:
                            all_cp_progress.append(
                                {
                                    "jd": jd,
                                    "rep": rep,
                                    "t": offset + entry["t"],
                                    "obj_value": entry["obj_value"],
                                    "obj_bound": entry["obj_bound"],
                                }
                            )

                    destroyed_op_count = jd_count_eff * c
                    cp_tl_seconds = destroyed_op_tl_multiplier * destroyed_op_count
                    last_cp_tl_seconds = cp_tl_seconds

                    improved = (
                        obj_before is not None
                        and obj_after is not None
                        and obj_before - obj_after > _OBJ_IMPROVEMENT_TOLERANCE
                    )

                    row_exit_reason: str
                    if saturated:
                        row_exit_reason = "saturated"
                    elif improved:
                        row_exit_reason = "improved"
                    else:
                        row_exit_reason = "no_improvement"

                    summary_rows.append(
                        {
                            "jd": jd,
                            "rep": rep,
                            "jd_count_eff": jd_count_eff,
                            "destroyed_op_count": destroyed_op_count,
                            "cp_tl_seconds": cp_tl_seconds,
                            "obj_before": (
                                float(obj_before) if obj_before is not None else None
                            ),
                            "obj_after": (
                                float(obj_after) if obj_after is not None else None
                            ),
                            "elapsed": round(iter_elapsed, 6),
                            "exit_reason": row_exit_reason,
                        }
                    )

                    if saturated:
                        self.logger.info(
                            "incremental_job_contrib_cp[jd=%d, rep=%d]: "
                            "jd_count_eff=%d < jd=%d, saturated; "
                            "stopping after this iteration",
                            jd,
                            rep,
                            jd_count_eff,
                            jd,
                        )
                        exit_reason = "saturated"
                        _outer_break = True
                        break

                    if not improved:
                        self.logger.info(
                            "incremental_job_contrib_cp[jd=%d, rep=%d]: "
                            "no improvement (%.1f -> %.1f); advancing jd",
                            jd,
                            rep,
                            obj_before if obj_before is not None else float("nan"),
                            obj_after if obj_after is not None else float("nan"),
                        )
                        break

                    self.logger.info(
                        "incremental_job_contrib_cp[jd=%d, rep=%d]: "
                        "improved %.1f -> %.1f; repeating",
                        jd,
                        rep,
                        obj_before,
                        obj_after,
                    )

                if _outer_break:
                    break

            # Composite step registers its own endpoint so charts show a
            # top-level (open-circle) marker at the end of this flow
            # section, matching coarsen_solve_reconstruct's convention.
            # The current incumbent is passed (not None): ``work_status``
            # reads ``history[-1].solution`` and would report None — i.e. a
            # successful run recorded as status-unknown — for a solution-less
            # tail entry. Re-registering the incumbent cannot displace it
            # (``register`` only swaps on a strictly better objective).
            # contract: elapsed measured immediately before _register.
            elapsed = time.monotonic() - step_start
            self._register(
                SubroutineReport(
                    elapsed_time=elapsed,
                    obj_value=self.solution_manager.best_obj_value,
                    obj_bound=None,
                ),
                self.solution_manager.get_incumbent(),
            )
        except Exception as exc:
            exit_reason = f"error:{type(exc).__name__}"
            raise
        finally:
            log_path = self._dump_incremental_job_contrib_cp_log(
                exit_reason, summary_rows, same_set_skips=same_set_skips
            )
            self._dump_incremental_job_contrib_cp_progress(
                all_cp_progress,
                same_set_skips=same_set_skips,
                global_lb=self.solution_manager.best_obj_bound,
            )
            self.logger.info(
                "incremental_job_contrib_cp: done, exit_reason=%s, "
                "total_iterations=%d, same_set_skips=%d, log=%s",
                exit_reason,
                len(summary_rows),
                same_set_skips,
                log_path,
            )

    def _dump_incremental_job_contrib_cp_log(
        self, exit_reason: str, rows: list[dict], *, same_set_skips: int = 0
    ) -> Path | None:
        """Emit the composite's per-run summary, or skip when no working dir.

        Uses ``try_get_file_path_for_subroutine`` so a run without a working
        directory (tests, scripted calls) silently skips the artifact instead
        of raising ``AttributeError`` after all the work is done — same
        convention as ``sw_cp``'s ``_step_log.yaml`` and ``job_contrib_cp``'s
        ``_metrics.yaml``.
        """
        log_path = self.try_get_file_path_for_subroutine(
            "_incremental_job_contrib_cp_log.yaml"
        )
        if log_path is None:
            return None
        dump_yaml(
            {
                "exit_reason": exit_reason,
                "same_set_skips": same_set_skips,
                "rows": rows,
            },
            log_path,
        )
        return log_path

    def _dump_incremental_job_contrib_cp_progress(
        self,
        cp_progress: list[dict],
        *,
        same_set_skips: int = 0,
        global_lb: float | None = None,
    ) -> None:
        """Dump the step-local CP trajectory for the dedicated progress plot.

        The resulting ``<call_context>_incremental_job_contrib_cp_progress.json``
        matches the ``job_contrib_progress_json`` layout kind, whose template
        carries ``{call_context}`` as a free placeholder so the reporter can
        ``find_artifacts`` it.
        """
        path = self.try_get_file_path_for_subroutine(
            "_incremental_job_contrib_cp_progress.json"
        )
        if path is None:
            return
        payload: dict = {
            "same_set_skips": same_set_skips,
            "cp_progress": cp_progress,
        }
        if global_lb is not None:
            payload["global_lb"] = global_lb
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
