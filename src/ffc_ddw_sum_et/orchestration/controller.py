"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

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
from ffc_ddw_sum_et.algorithm.mcf_lb import MCFLBDiagnostic
from ffc_ddw_sum_et.algorithm.mcf_lb.last_stage_only import (
    heuristic_last_stage_only_from_mcf_lb,
    neh_cp_last_stage_only_from_mcf_lb,
    single_pass_last_stage_only_from_mcf_lb,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.phase1_mcf import SeedTag, run_phase1
from ffc_ddw_sum_et.algorithm.mcf_lb.phase2_last_stage import run_phase2
from ffc_ddw_sum_et.algorithm.mcf_lb.phase3_dispatch import (
    reverse_dispatch_full_schedule,
    run_phase3,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.phase4_profile_fix import run_phase4
from ffc_ddw_sum_et.algorithm.mcf_lb.preemptive import (
    MCFLBStopRequested,
    solve_mcf_lb,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.utils import (
    pm_pmtn_sort_job_sequence_with_log,
)
from ffc_ddw_sum_et.algorithm.neh_cp import (
    NehCpBatchTlMode,
    NehCpDispatcher,
    NehCpJobPriority,
    NehCpOption,
)
from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf
from ffc_ddw_sum_et.algorithm.pm_pmtn_sorter import PmPrmpSortKey
from ffc_ddw_sum_et.io.parallel_mc_cost_heatmap import HeatmapSort
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import (
    compute_phase_obj_value,
    compute_weighted_earliness_tardiness,
)
from ffc_ddw_sum_et.solution.schedule_build import build_schedule_from_op_starts

from .controller_core import FFcDDWSubroutineControllerCore
from .mcf_lb_phase_labels import (
    MCF_LB_LOCAL_NAME_RE,
    MCF_LB_R1_LABEL_ORDER,
    MCF_LB_R2_LABEL_ORDER,
    MCF_LB_ROUND_RE,
)
from .solution_manager import FFcDDWSolution
from .value_resolver import resolve_value_expr

__all__ = ["FFcDDWSubroutineController", "MCFLBDiagnostic", "NehCpJobPriority"]


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
        adjust_p_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
        adjust_r_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
        adjust_p_by_full_sch_and_last_stage_only_sch: bool = False,
        adjust_r_by_full_sch_and_last_stage_only_sch: bool = False,
        adjust_r_by_half: bool = False,
        _register_report: bool = True,
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
                the ``neh_cp_last_stage_only_sch_from_mcf_lb`` and
                ``single_pass_last_stage_only_sch_from_mcf_lb`` steps
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

        uses_ls_only_pmtn = (
            adjust_p_by_full_sch_and_last_stage_only_pmtn_sch
            or adjust_r_by_full_sch_and_last_stage_only_pmtn_sch
        )
        uses_ls_only_full = (
            adjust_p_by_full_sch_and_last_stage_only_sch
            or adjust_r_by_full_sch_and_last_stage_only_sch
        )
        if uses_ls_only_pmtn and uses_ls_only_full:
            raise ValueError(
                "apply_lb_by_mcf: cannot combine "
                "adjust_*_by_full_sch_and_last_stage_only_pmtn_sch with "
                "adjust_*_by_full_sch_and_last_stage_only_sch in a "
                "single call; pick one reference schedule."
            )

        ls_only_pmtn_makespan: int | None = None
        ls_only_makespan: int | None = None
        incumbent_makespan: int | None = None
        makespan_delta: int | None = None

        def _ensure_makespans() -> None:
            nonlocal incumbent_makespan, ls_only_pmtn_makespan
            nonlocal ls_only_makespan, makespan_delta
            if makespan_delta is not None:
                return
            ref_sol = self.adjust_ref_full_sol
            if ref_sol is None:
                ref_sol = self.solution_manager.get_incumbent()
            if ref_sol is None or ref_sol.schedule is None:
                raise ValueError(
                    "apply_lb_by_mcf with "
                    "adjust_(p|r)_by_full_sch_and_last_stage_(only_pmtn|only)_sch"
                    "=True requires either self.adjust_ref_full_sol or an "
                    "incumbent schedule on self.solution_manager."
                )
            incumbent_makespan = int(ref_sol.schedule.makespan)
            if uses_ls_only_pmtn:
                if self.mcf_preemptive_schedule is None:
                    raise ValueError(
                        "apply_lb_by_mcf with "
                        "adjust_(p|r)_by_full_sch_and_last_stage_only_pmtn_sch"
                        "=True requires self.mcf_preemptive_schedule set by a "
                        "prior step."
                    )
                ls_only_pmtn_makespan = int(self.mcf_preemptive_schedule.makespan)
                makespan_delta = max(incumbent_makespan - ls_only_pmtn_makespan, 0)
            else:
                if (
                    self.last_stage_only_sol is None
                    or self.last_stage_only_sol.schedule is None
                ):
                    raise ValueError(
                        "apply_lb_by_mcf with "
                        "adjust_(p|r)_by_full_sch_and_last_stage_only_sch=True "
                        "requires self.last_stage_only_sol.schedule set by a "
                        "prior step."
                    )
                ls_only_makespan = int(self.last_stage_only_sol.schedule.makespan)
                makespan_delta = max(incumbent_makespan - ls_only_makespan, 0)

        ref_label = "ls_only_pmtn" if uses_ls_only_pmtn else "ls_only"

        effective_p_increment = p_increment
        p_adjust = 0
        fire_p = (
            adjust_p_by_full_sch_and_last_stage_only_pmtn_sch
            or adjust_p_by_full_sch_and_last_stage_only_sch
        )
        if fire_p:
            _ensure_makespans()
            n = self.instance.job_count
            m_last = self.instance.last_stage_mc_count
            p_adjust = math.ceil(makespan_delta * m_last / n)
            ref_value = ls_only_pmtn_makespan if uses_ls_only_pmtn else ls_only_makespan
            self.logger.info(
                "apply_lb_by_mcf: adjust_p_by_full_sch_and_last_stage_%s_sch=True, "
                "incumbent makespan=%d, %s makespan=%d, delta=%d, "
                "n=%d, m_last=%d, p_adjust=%d",
                ref_label,
                incumbent_makespan,
                ref_label,
                ref_value,
                makespan_delta,
                n,
                m_last,
                p_adjust,
            )
            effective_p_increment = p_increment + p_adjust

        effective_r_increment = r_increment
        r_adjust = 0
        fire_r = (
            adjust_r_by_full_sch_and_last_stage_only_pmtn_sch
            or adjust_r_by_full_sch_and_last_stage_only_sch
        )
        if fire_r:
            _ensure_makespans()
            r_adjust = makespan_delta
            if adjust_r_by_half:
                r_adjust = math.ceil(makespan_delta / 2)
            ref_value = ls_only_pmtn_makespan if uses_ls_only_pmtn else ls_only_makespan
            self.logger.info(
                "apply_lb_by_mcf: adjust_r_by_full_sch_and_last_stage_%s_sch=True, "
                "incumbent makespan=%d, %s makespan=%d, delta=%d, "
                "r_adjust=%d",
                ref_label,
                incumbent_makespan,
                ref_label,
                ref_value,
                makespan_delta,
                r_adjust,
            )
            effective_r_increment = r_increment + r_adjust

        start_elapsed = time.monotonic()
        prev_diag = self.mcf_lb_diagnostic
        diag = MCFLBDiagnostic()
        self.mcf_lb_diagnostic = diag

        if uses_ls_only_pmtn or uses_ls_only_full:
            if uses_ls_only_pmtn:
                diag.adjust_params_last_stage_only_pmtn_makespan = ls_only_pmtn_makespan
            else:
                diag.adjust_params_last_stage_only_makespan = ls_only_makespan
            diag.adjust_params_incumbent_makespan = incumbent_makespan
            diag.adjust_params_makespan_delta = makespan_delta
        if fire_p:
            diag.adjust_p_increment_added = p_adjust
        if fire_r:
            diag.adjust_r_increment_added = r_adjust

        if r_multiplier != 1.0 or effective_r_increment != 0:
            self._log_effective_release_stats(
                "apply_lb_by_mcf",
                r_multiplier=r_multiplier,
                r_increment=effective_r_increment,
            )

        if effective_p_increment == 0:
            instance_for_mcf = self.instance
        else:
            last_stage_id = self.instance.stage_id_list[-1]
            instance_for_mcf = FFcDDWParameters.with_stage_processing_time_increment(
                self.instance, last_stage_id, effective_p_increment
            )

        try:
            mcf_result = solve_mcf_lb(
                instance_for_mcf,
                diag,
                r_multiplier=r_multiplier,
                r_increment=effective_r_increment,
                stop_predicate=self.is_stopping_condition,
                logger=self.logger,
            )
        except MCFLBStopRequested:
            self.mcf_lb_diagnostic = prev_diag
            self.logger.info(
                "apply_lb_by_mcf: stop predicate fired before MCF solve; skipping."
            )
            return self._make_stop_report(start_elapsed)
        obj_bound_by_mcf = mcf_result.mcf_lb

        self.mcf_preemptive_schedule = mcf_result.mcf_preemptive_schedule
        self.mcf_preemptive_sch_p_increment = effective_p_increment
        # Composite drivers (``_register_report=False``) own the phase list
        # and clear it once at composite entry; clearing here would wipe
        # snapshots produced by an earlier round of the composite.
        if _register_report:
            self.mcf_lb_phase_schedules.clear()
        self._record_mcf_lb_phase(
            ("1_mcf_preemptive", mcf_result.mcf_preemptive_schedule)
        )

        self.logger.info(
            "apply_lb_by_mcf: MCF LB = %d, p_increment=%d (effective=%d), "
            "r_multiplier=%.4g, r_increment=%d (effective=%d)",
            int(obj_bound_by_mcf),
            p_increment,
            effective_p_increment,
            r_multiplier,
            r_increment,
            effective_r_increment,
        )

        if draw_heatmap:
            from ..io import build_signed_cost_matrix, dump_signed_cost_heatmap_yaml

            yaml_path = self.try_get_file_path_for_subroutine("_C_heatmap.yaml")
            if yaml_path is not None:
                heatmap_data = build_signed_cost_matrix(
                    instance_for_mcf,
                    sort=heatmap_sort,
                    x_jt_map=mcf_result.mcf.get_variable_value_dict(),
                    obj_value=obj_bound_by_mcf,
                    r_multiplier=r_multiplier,
                    r_increment=effective_r_increment,
                )
                dump_signed_cost_heatmap_yaml(yaml_path, heatmap_data)
                self.logger.info(
                    "apply_lb_by_mcf: wrote heatmap YAML to %s "
                    "(jobs=%d, t-range=[%d..%d], x_jt cells=%d)",
                    yaml_path,
                    len(heatmap_data.y_labels),
                    heatmap_data.t_axis[0],
                    heatmap_data.t_axis[-1],
                    len(heatmap_data.x_cells),
                )

        obj_bound_is_valid = (
            effective_p_increment == 0
            and r_multiplier <= 1.0
            and effective_r_increment == 0
        )
        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=None,
            obj_bound=(obj_bound_by_mcf if obj_bound_is_valid else None),
        )
        if _register_report:
            self._register(report, None)
        return report

    def neh_cp_last_stage_only_sch_from_mcf_lb(
        self,
        job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
        batch_size: int = 5,
        hint_placement_priority: Literal["contrib", "dist"] = "contrib",
        pf_method: PFMethod | None = "PF1",
        solver_thread_cnt: int = 1,
        total_tl: float | str | None = None,
        log_cp_search_progress: bool = False,
    ) -> SubroutineReport:
        """Step method: build a last-stage-only NEH-CP schedule from the
        MCF preemptive LB stored on ``self.mcf_preemptive_schedule``.

        Pre-conditions (else ``ValueError``):
          - ``self.mcf_preemptive_schedule`` set by a prior
            ``apply_lb_by_mcf`` (or compatible) step.
          - ``self.mcf_lb_diagnostic`` set so the MCF LB can be used as
            ``obj_bound``.

        Args:
            batch_size: Jobs added per NEH-CP step (in MCF window-width
                priority order). Defaults to 5.
            pf_method: Profile-fix precedence policy for each batch's
                last-stage CP-SAT solve. Defaults to ``"PF1"``; ``None``
                skips the precedence-arc pass.
            solver_thread_cnt: ``num_search_workers`` per batch CP solve.
            total_tl: Total time budget for the entire NEH-CP loop.
                Accepts a float or a ``"<n>nc"`` / ``"<n>n"`` / ``"<n>c"``
                / ``"<n>m"`` expression. When the budget is exhausted,
                un-placed jobs are greedily re-dispatched onto the last
                successful CP schedule so the returned schedule still
                covers every job.
            log_cp_search_progress: When ``True``, each batch's CP solver
                writes its search-progress log under the subroutine
                output directory.

        Side effects:
          - Stores the resulting full last-stage schedule on
            ``self.last_stage_only_sol``.
          - Appends per-batch and final schedules to
            ``self.mcf_lb_phase_schedules`` for post-run diagnostics.
        """
        if self.mcf_preemptive_schedule is None:
            raise ValueError(
                "neh_cp_last_stage_only_sch_from_mcf_lb requires a prior "
                "apply_lb_by_mcf step to populate self.mcf_preemptive_schedule."
            )
        if self.mcf_lb_diagnostic is None:
            raise ValueError(
                "neh_cp_last_stage_only_sch_from_mcf_lb requires self.mcf_lb_diagnostic "
                "(set by apply_lb_by_mcf)."
            )

        instance = self.instance
        total_tl_seconds = resolve_value_expr(
            total_tl,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )

        mcf_lb = self.mcf_lb_diagnostic.mcf_lb
        result = neh_cp_last_stage_only_from_mcf_lb(
            instance,
            self.mcf_preemptive_schedule,
            logger=self.logger,
            job_priority=job_priority,
            hint_placement_priority=hint_placement_priority,
            batch_size=batch_size,
            pf_method=pf_method,
            solver_thread_cnt=solver_thread_cnt,
            total_tl_seconds=total_tl_seconds,
            mcf_lb=mcf_lb,
            log_cp_search_progress=log_cp_search_progress,
            solver_log_path_getter=self.get_file_path_for_subroutine,
        )

        # Publish the MCF LB as the global ``obj_bound`` only when the
        # diagnostic confirms it is a valid LB on the original problem
        # (no positive p/r augmentation). ``result.obj_bound`` is only the
        # last NEH-CP iteration's sub-instance CP LB and is not a valid
        # global lower bound (see NehCpLastStageOnlyResult docstring).
        valid_global_mcf_lb = (
            mcf_lb if self.mcf_lb_diagnostic.mcf_lb_is_valid_for_main_problem else None
        )
        self.last_stage_only_sol = FFcDDWSolution(
            schedule=result.schedule,
            obj_value=result.obj_value,
            obj_bound=valid_global_mcf_lb,
        )
        self.last_stage_only_sol_p_increment = 0
        self._record_mcf_lb_phases(result.intermediate_schedules)
        self._record_mcf_lb_phase(("2_ls_only_sch_from_neh_cp", result.schedule))

        self.logger.info(
            "neh_cp_last_stage_only_sch_from_mcf_lb: status=%s, obj=%.2f, "
            "mcf_lb=%d, last_iter_cp_lb=%.2f, elapsed=%.2fs "
            "(cp solves total=%.2fs).",
            result.status,
            result.obj_value,
            int(valid_global_mcf_lb) if valid_global_mcf_lb is not None else None,
            result.obj_bound,
            result.elapsed_time,
            result.cp_solve_sec,
        )
        report = SubroutineReport(
            elapsed_time=result.elapsed_time,
            obj_value=result.obj_value,
            obj_bound=valid_global_mcf_lb,
        )
        self._register(report, None)
        return report

    def single_pass_last_stage_only_sch_from_mcf_lb(
        self,
        job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
        placement_priority: Literal["contrib", "dist"] = "contrib",
        pf_method: PFMethod | None = "PF1",
        solver_thread_cnt: int = 1,
        total_tl: float | str | None = None,
        log_cp_search_progress: bool = False,
        p_increment: int = 0,
    ) -> SubroutineReport:
        """Step method: midpoint warm-start across all jobs from the MCF
        preemptive LB, then a single profile-fix CP-SAT solve.

        Pre-conditions (else ``ValueError``):
          - ``self.mcf_preemptive_schedule`` set by a prior
            ``apply_lb_by_mcf`` (or compatible) step.
          - ``self.mcf_lb_diagnostic`` set so the MCF LB can be used as
            ``obj_bound``.

        Args:
            job_priority: Job-ordering priority used by the midpoint
                warm-start placement (only affects the warm-start, not
                the CP solve itself, since the CP model treats jobs
                interchangeably).
            placement_priority: Lex-tiebreak between weighted-ET
                contribution and start-time distance when the midpoint
                slot is occupied; see ``_insert_jobs_at_desired_starts``
                for semantics. Unlike the NEH-CP step, the placement IS
                the profile-fix schedule, so this directly steers the
                final CP solve.
            pf_method: Profile-fix precedence policy for the CP-SAT
                solve. Defaults to ``"PF1"``; ``None`` skips the
                precedence-arc pass.
            solver_thread_cnt: ``num_search_workers`` for the CP solve.
            total_tl: Time budget for the single CP solve. Accepts a
                float or a ``"<n>nc"`` / ``"<n>n"`` / ``"<n>c"`` /
                ``"<n>m"`` expression.
            log_cp_search_progress: When ``True``, the CP solver writes
                its search-progress log under the subroutine output
                directory.
            p_increment: Integer ``≥ 0``. When non-zero, the CP-SAT
                solve runs on an augmented instance whose last-stage
                processing times are increased by ``p_increment`` for
                every job. The resulting last-stage-only schedule is
                feasible for the augmented problem only;
                ``build_full_sch_from_last_stage_only_sch`` rebuilds it
                under original durations before reverse-dispatch (see
                ``Phase3State.ls_only_sch_before_delay``). The value used
                is recorded on ``self.last_stage_only_sol_p_increment``.

        Side effects:
          - Stores the resulting full last-stage schedule on
            ``self.last_stage_only_sol``.
          - Appends ``2_ls_only_sch_from_mcf_lb`` to
            ``self.mcf_lb_phase_schedules``. No per-batch snapshots are
            recorded (single CP solve).

        The returned ``SubroutineReport.obj_bound`` is always ``None``:
        this step does not produce a lower bound, it only consumes the
        MCF LB stored by the prior ``apply_lb_by_mcf`` step. Callers
        wanting the MCF LB should read it from
        ``self.mcf_lb_diagnostic.mcf_lb`` (and check
        ``self.mcf_preemptive_sch_p_increment`` for global validity).
        """
        if p_increment < 0:
            raise ValueError(
                f"p_increment must be 0 or a positive integer; got {p_increment}."
            )
        if self.mcf_preemptive_schedule is None:
            raise ValueError(
                "single_pass_last_stage_only_sch_from_mcf_lb requires a prior "
                "apply_lb_by_mcf step to populate self.mcf_preemptive_schedule."
            )
        if self.mcf_lb_diagnostic is None:
            raise ValueError(
                "single_pass_last_stage_only_sch_from_mcf_lb requires "
                "self.mcf_lb_diagnostic (set by apply_lb_by_mcf)."
            )

        instance = self.instance
        total_tl_seconds = resolve_value_expr(
            total_tl,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )

        if p_increment == 0:
            instance_for_solve = instance
        else:
            last_stage_id = instance.stage_id_list[-1]
            instance_for_solve = FFcDDWParameters.with_stage_processing_time_increment(
                instance, last_stage_id, p_increment
            )

        mcf_lb = self.mcf_lb_diagnostic.mcf_lb
        result = single_pass_last_stage_only_from_mcf_lb(
            instance_for_solve,
            self.mcf_preemptive_schedule,
            logger=self.logger,
            job_priority=job_priority,
            placement_priority=placement_priority,
            pf_method=pf_method,
            solver_thread_cnt=solver_thread_cnt,
            total_tl_seconds=total_tl_seconds,
            mcf_lb=mcf_lb,
            log_cp_search_progress=log_cp_search_progress,
            solver_log_path_getter=self.get_file_path_for_subroutine,
        )

        self.last_stage_only_sol = FFcDDWSolution(
            schedule=result.schedule,
            obj_value=result.obj_value,
            obj_bound=None,
        )
        self.last_stage_only_sol_p_increment = p_increment
        self._record_mcf_lb_phase(("2_ls_only_sch_from_mcf_lb", result.schedule))

        self.logger.info(
            "single_pass_last_stage_only_sch_from_mcf_lb: status=%s, "
            "obj=%.2f, mcf_lb=%d, cp_lb=%.2f, elapsed=%.2fs, p_increment=%d.",
            result.status,
            result.obj_value,
            int(mcf_lb),
            result.obj_bound,
            result.elapsed_time,
            p_increment,
        )
        report = SubroutineReport(
            elapsed_time=result.elapsed_time,
            obj_value=result.obj_value,
            obj_bound=None,
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
        adjust_p_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
        adjust_r_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
        adjust_p_by_full_sch_and_last_stage_only_sch: bool = False,
        adjust_r_by_full_sch_and_last_stage_only_sch: bool = False,
        adjust_r_by_half: bool = False,
        _register_report: bool = True,
    ) -> SubroutineReport:
        """Step method: midpoint warm-start across all jobs from the MCF
        preemptive LB, then a CP-free heuristic refinement
        (``make_semi_active`` on the last stage with upstream release
        times, followed by last-stage ``insert_idle_time``).

        Same construction as
        :meth:`single_pass_last_stage_only_sch_from_mcf_lb` up to the
        midpoint placement; the CP solve is replaced by deterministic
        left-shift + idle-time insertion. As a result this step exposes
        no CP-only knobs (``pf_method``, ``solver_thread_cnt``,
        ``total_tl``, ``log_cp_search_progress``).

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
        if self.mcf_lb_diagnostic is None:
            raise ValueError(
                "heuristic_last_stage_only_sch_from_mcf_lb requires "
                "self.mcf_lb_diagnostic (set by apply_lb_by_mcf)."
            )

        uses_ls_only_pmtn = (
            adjust_p_by_full_sch_and_last_stage_only_pmtn_sch
            or adjust_r_by_full_sch_and_last_stage_only_pmtn_sch
        )
        uses_ls_only_full = (
            adjust_p_by_full_sch_and_last_stage_only_sch
            or adjust_r_by_full_sch_and_last_stage_only_sch
        )
        if uses_ls_only_pmtn and uses_ls_only_full:
            raise ValueError(
                "heuristic_last_stage_only_sch_from_mcf_lb: cannot combine "
                "adjust_*_by_full_sch_and_last_stage_only_pmtn_sch with "
                "adjust_*_by_full_sch_and_last_stage_only_sch in a "
                "single call; pick one reference schedule."
            )

        ls_only_pmtn_makespan: int | None = None
        ls_only_makespan: int | None = None
        incumbent_makespan: int | None = None
        makespan_delta: int | None = None

        def _ensure_makespans() -> None:
            nonlocal incumbent_makespan, ls_only_pmtn_makespan
            nonlocal ls_only_makespan, makespan_delta
            if makespan_delta is not None:
                return
            ref_sol = self.adjust_ref_full_sol
            if ref_sol is None:
                ref_sol = self.solution_manager.get_incumbent()
            if ref_sol is None or ref_sol.schedule is None:
                raise ValueError(
                    "heuristic_last_stage_only_sch_from_mcf_lb with "
                    "adjust_(p|r)_by_full_sch_and_last_stage_(only_pmtn|only)_sch"
                    "=True requires either self.adjust_ref_full_sol or an "
                    "incumbent schedule on self.solution_manager."
                )
            incumbent_makespan = int(ref_sol.schedule.makespan)
            if uses_ls_only_pmtn:
                # self.mcf_preemptive_schedule presence already enforced by the
                # method-level precondition above.
                ls_only_pmtn_makespan = int(self.mcf_preemptive_schedule.makespan)
                makespan_delta = max(incumbent_makespan - ls_only_pmtn_makespan, 0)
            else:
                if (
                    self.last_stage_only_sol is None
                    or self.last_stage_only_sol.schedule is None
                ):
                    raise ValueError(
                        "heuristic_last_stage_only_sch_from_mcf_lb with "
                        "adjust_(p|r)_by_full_sch_and_last_stage_only_sch=True "
                        "requires self.last_stage_only_sol.schedule set by a "
                        "prior step."
                    )
                ls_only_makespan = int(self.last_stage_only_sol.schedule.makespan)
                makespan_delta = max(incumbent_makespan - ls_only_makespan, 0)

        ref_label = "ls_only_pmtn" if uses_ls_only_pmtn else "ls_only"

        effective_p_increment = p_increment
        p_adjust = 0
        fire_p = (
            adjust_p_by_full_sch_and_last_stage_only_pmtn_sch
            or adjust_p_by_full_sch_and_last_stage_only_sch
        )
        if fire_p:
            _ensure_makespans()
            n = self.instance.job_count
            m_last = self.instance.last_stage_mc_count
            p_adjust = math.ceil(makespan_delta * m_last / n)
            ref_value = ls_only_pmtn_makespan if uses_ls_only_pmtn else ls_only_makespan
            self.logger.info(
                "heuristic_last_stage_only_sch_from_mcf_lb: "
                "adjust_p_by_full_sch_and_last_stage_%s_sch=True, "
                "incumbent makespan=%d, %s makespan=%d, delta=%d, "
                "n=%d, m_last=%d, p_adjust=%d",
                ref_label,
                incumbent_makespan,
                ref_label,
                ref_value,
                makespan_delta,
                n,
                m_last,
                p_adjust,
            )
            effective_p_increment = p_increment + p_adjust

        effective_r_increment = r_increment
        r_adjust = 0
        fire_r = (
            adjust_r_by_full_sch_and_last_stage_only_pmtn_sch
            or adjust_r_by_full_sch_and_last_stage_only_sch
        )
        if fire_r:
            _ensure_makespans()
            r_adjust = makespan_delta
            if adjust_r_by_half:
                r_adjust = math.ceil(makespan_delta / 2)
            ref_value = ls_only_pmtn_makespan if uses_ls_only_pmtn else ls_only_makespan
            self.logger.info(
                "heuristic_last_stage_only_sch_from_mcf_lb: "
                "adjust_r_by_full_sch_and_last_stage_%s_sch=True, "
                "incumbent makespan=%d, %s makespan=%d, delta=%d, "
                "r_adjust=%d",
                ref_label,
                incumbent_makespan,
                ref_label,
                ref_value,
                makespan_delta,
                r_adjust,
            )
            effective_r_increment = r_increment + r_adjust

        if uses_ls_only_pmtn or uses_ls_only_full:
            if uses_ls_only_pmtn:
                if (
                    self.mcf_lb_diagnostic.adjust_params_last_stage_only_pmtn_makespan
                    is None
                ):
                    self.mcf_lb_diagnostic.adjust_params_last_stage_only_pmtn_makespan = ls_only_pmtn_makespan
            else:
                if (
                    self.mcf_lb_diagnostic.adjust_params_last_stage_only_makespan
                    is None
                ):
                    self.mcf_lb_diagnostic.adjust_params_last_stage_only_makespan = (
                        ls_only_makespan
                    )
            if self.mcf_lb_diagnostic.adjust_params_incumbent_makespan is None:
                self.mcf_lb_diagnostic.adjust_params_incumbent_makespan = (
                    incumbent_makespan
                )
            if self.mcf_lb_diagnostic.adjust_params_makespan_delta is None:
                self.mcf_lb_diagnostic.adjust_params_makespan_delta = makespan_delta
        if fire_p and self.mcf_lb_diagnostic.adjust_p_increment_added is None:
            self.mcf_lb_diagnostic.adjust_p_increment_added = p_adjust
        if fire_r and self.mcf_lb_diagnostic.adjust_r_increment_added is None:
            self.mcf_lb_diagnostic.adjust_r_increment_added = r_adjust

        if r_multiplier != 1.0 or effective_r_increment != 0:
            self._log_effective_release_stats(
                "heuristic_last_stage_only_sch_from_mcf_lb",
                r_multiplier=r_multiplier,
                r_increment=effective_r_increment,
            )

        instance = self.instance
        if effective_p_increment == 0:
            instance_for_solve = instance
        else:
            last_stage_id = instance.stage_id_list[-1]
            instance_for_solve = FFcDDWParameters.with_stage_processing_time_increment(
                instance, last_stage_id, effective_p_increment
            )

        mcf_lb = self.mcf_lb_diagnostic.mcf_lb
        result = heuristic_last_stage_only_from_mcf_lb(
            instance_for_solve,
            self.mcf_preemptive_schedule,
            logger=self.logger,
            job_priority=job_priority,
            placement_priority=placement_priority,
            r_multiplier=r_multiplier,
            r_increment=effective_r_increment,
        )

        self.last_stage_only_sol = FFcDDWSolution(
            schedule=result.schedule,
            obj_value=result.obj_value,
            obj_bound=None,
        )
        self.last_stage_only_sol_p_increment = effective_p_increment
        self._record_mcf_lb_phases(
            [(f"2_{label}", sched) for label, sched in result.intermediate_schedules]
        )
        self._record_mcf_lb_phase(
            ("3_lastS_only_from_mcf_lb_after_sa_iti", result.schedule)
        )

        self.logger.info(
            "heuristic_last_stage_only_sch_from_mcf_lb: status=%s, "
            "obj=%.2f, mcf_lb=%d, elapsed=%.2fs, p_increment=%d (effective=%d), "
            "r_multiplier=%.4g, r_increment=%d (effective=%d).",
            result.status,
            result.obj_value,
            int(mcf_lb),
            result.elapsed_time,
            p_increment,
            effective_p_increment,
            r_multiplier,
            r_increment,
            effective_r_increment,
        )
        report = SubroutineReport(
            elapsed_time=result.elapsed_time,
            obj_value=result.obj_value,
            obj_bound=None,
        )
        if _register_report:
            self._register(report, None)
        return report

    def build_full_sch_from_last_stage_only_sch(
        self,
    ) -> SubroutineReport:
        """Step method: build a full dispatched ``FFcSchedule`` from
        ``self.last_stage_only_sol.schedule`` via reverse-dispatch + unflip
        (Phase 3 of the MCF-LB pipeline applied standalone).

        Pre-condition (else ``ValueError``): ``self.last_stage_only_sol`` is
        set by a prior step (``single_pass_last_stage_only_sch_from_mcf_lb``,
        ``heuristic_last_stage_only_sch_from_mcf_lb``,
        ``neh_cp_last_stage_only_sch_from_mcf_lb``,
        ``run_last_stage_cp_sat_lb``, or ``run_mcf_lb_4``).

        The reversed dispatcher is run twice (``machine_then_job=False``
        and ``machine_then_job=True``) in `reverse_dispatch_full_schedule`;
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
            global LB; callers chain ``apply_lb_by_mcf`` (or
            ``run_mcf_lb_4``) earlier in the flow when an LB is needed.
        """
        report, solution = self._build_full_sch_core()
        self._register(report, solution)
        return report

    def _build_full_sch_core(
        self,
    ) -> tuple[SubroutineReport, FFcDDWSolution | None]:
        """Compute the full schedule from ``self.last_stage_only_sol`` without
        registering. Returns ``(report, solution_or_none)`` so callers can
        choose when to register (composite steps register once at the end).
        """
        if self.last_stage_only_sol is None:
            raise ValueError(
                "build_full_sch_from_last_stage_only_sch requires "
                "self.last_stage_only_sol; run a step that populates it "
                "first (single_pass_last_stage_only_sch_from_mcf_lb, "
                "heuristic_last_stage_only_sch_from_mcf_lb, "
                "neh_cp_last_stage_only_sch_from_mcf_lb, "
                "run_last_stage_cp_sat_lb, or run_mcf_lb_4)."
            )

        ls_p_inc = self.last_stage_only_sol_p_increment
        rebuild_with_original_p = ls_p_inc is not None and ls_p_inc != 0

        start_elapsed = time.monotonic()
        state = reverse_dispatch_full_schedule(
            self.instance,
            self.last_stage_only_sol.schedule,
            rebuild_last_stage_with_original_p=rebuild_with_original_p,
            logger=self.logger,
        )
        elapsed = time.monotonic() - start_elapsed
        if state is None:
            self.logger.warning(
                "build_full_sch_from_last_stage_only_sch: reverse-dispatch "
                "produced no schedule"
            )
            return (
                SubroutineReport(elapsed_time=elapsed, obj_value=None, obj_bound=None),
                None,
            )

        if state.ls_only_sch_before_delay is not None:
            self._record_mcf_lb_phase(
                ("4_lastS_only_before_rs", state.ls_only_sch_before_delay)
            )
        if state.ls_only_sch_delayed is not None:
            self._record_mcf_lb_phase(
                ("5_lastS_only_after_rs", state.ls_only_sch_delayed)
            )
        if state.ls_only_sch_flipped is not None:
            self._record_mcf_lb_phase(
                ("6_lastS_only_flipped", state.ls_only_sch_flipped)
            )
        if state.full_sch_before_unflip is not None:
            self._record_mcf_lb_phase(
                ("7_fullS_before_unflip", state.full_sch_before_unflip)
            )
        if state.full_sch_after_unflip is not None:
            self._record_mcf_lb_phase(
                ("8_fullS_after_unflip", state.full_sch_after_unflip)
            )
        self._record_mcf_lb_phase(
            ("9_fullS_after_sa_iti", state.full_sch_from_ls_only_sch)
        )

        self.logger.info(
            "build_full_sch_from_last_stage_only_sch: dispatched obj=%.2f, "
            "makespan=%d, elapsed=%.2fs",
            state.dispatched_obj,
            int(state.full_sch_from_ls_only_sch.makespan),
            elapsed,
        )
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=state.dispatched_obj,
            obj_bound=None,
        )
        solution = FFcDDWSolution(
            schedule=state.full_sch_from_ls_only_sch,
            obj_value=state.dispatched_obj,
            obj_bound=None,
        )
        return report, solution

    def _emit_calc_mcf_lb_phase_metrics_csv(self) -> None:
        """Write per-instance wET / makespan CSVs for the snapshots recorded
        under the current ``calc_mcf_lb_and_derive_full_sch`` call.

        Always-on (gated only by artifact layout availability). When the
        controller is running without a layout (tests, scripted use), the
        method silently no-ops. Round-2 cells are blank when round 2 did
        not run; wET cells are blank for snapshots living on the reversed
        instance (``flipped``, ``fullS_before_unflip``).
        """
        layout = self._artifact_layout
        scenario = self._artifact_scenario_name
        instance = self._artifact_instance_name
        if layout is None or scenario is None or instance is None:
            return

        per_round: dict[str, dict[str, object]] = {"r1": {}, "r2": {}}
        for full_name, sched in self.mcf_lb_phase_schedules:
            round_match = MCF_LB_ROUND_RE.search(full_name)
            local_match = MCF_LB_LOCAL_NAME_RE.search(full_name)
            if round_match is None or local_match is None:
                continue
            round_key = f"r{round_match.group(1)}"
            label = local_match.group(2)
            # First writer wins; later snapshots with the same label are
            # ignored. The composite's recording order matches the
            # user-spec order, so this is a no-op in practice.
            per_round[round_key].setdefault(label, sched)

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

    def calc_mcf_lb_and_derive_full_sch(
        self,
        draw_pmtn_sch_heatmap: bool = False,
        heatmap_sort: HeatmapSort = "end_time",
        job_placement_priority: PmPrmpSortKey = "end_time",
        last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
        adjust_p: bool = False,
        adjust_r: bool = False,
        emit_phase_schedules: bool = False,
    ) -> SubroutineReport:
        """Composite step: MCF-LB → full schedule, then a conditional
        second round with p/r adjustments.

        Round 1 always runs (via the no-register internals
        ``apply_lb_by_mcf(_register_report=False)``,
        ``heuristic_last_stage_only_sch_from_mcf_lb(_register_report=False)``,
        ``_build_full_sch_core``). Each round's chain runs inside
        ``self.temporarily_extended_context("r1" | "r2")`` so the
        recorded ``mcf_lb_phase_schedules`` entries are namespaced and
        round 2's recordings do not overwrite round 1's.

        Round 2 runs **only when both** of the following hold:
          * ``adjust_p or adjust_r`` is ``True``;
          * ``makespan_delta = round1_makespan -
            self.mcf_preemptive_schedule.makespan > 0`` (computed with no
            ``max(..., 0)`` clamp; this differs from the per-step
            ``adjust_*`` flags on ``apply_lb_by_mcf`` /
            ``heuristic_last_stage_only_sch_from_mcf_lb``, which clamp
            their internal delta at zero).

        Registers exactly once per call: a single synthesized
        ``SubroutineReport`` whose ``obj_bound`` carries round 1's MCF LB
        and whose paired solution is the better of round 1 / round 2
        results. Stop guards that fire before round 1 produces a full
        schedule return ``_make_stop_report`` without registering;
        guards that fire after round 1 has a result still register the
        round-1 result once before returning.

        Side effects (always, when an artifact layout is bound):
          * Two per-instance phase-metric CSVs are emitted under the
            ``progress`` zone via the ``mcf_lb_phase_obj_csv`` and
            ``mcf_lb_phase_makespan_csv`` artifact kinds. One row per
            user-spec snapshot in fixed order; r2 rows carry blank
            cells when round 2 did not run. wET cells are blank for
            snapshots on the reversed instance (``flipped`` /
            ``fullS_before_unflip``) since the original due-window frame
            does not apply.

        Args:
            draw_pmtn_sch_heatmap: Forwarded as ``draw_heatmap`` to
                round-1 and round-2 ``apply_lb_by_mcf``. Renamed at
                the composite layer to make clear it controls the
                MCF preemptive schedule's C-cost heatmap (not a
                heatmap of the full schedule).
            heatmap_sort: Forwarded to round-1 and round-2
                ``apply_lb_by_mcf``.
            job_placement_priority: Forwarded to round-1 and round-2
                ``heuristic_last_stage_only_sch_from_mcf_lb``.
            last_stage_only_placement_criteria: Forwarded as
                ``placement_priority`` to round-1 and round-2
                ``heuristic_last_stage_only_sch_from_mcf_lb``. Renamed
                at the composite layer to make clear it is the
                last-stage heuristic's tiebreak knob, not an MCF-step
                option.
            adjust_p: When ``True``, round 2 enables
                ``adjust_p_by_full_sch_and_last_stage_only_pmtn_sch``
                on both the MCF and heuristic steps. Default
                ``False``.
            adjust_r: When ``True``, round 2 enables
                ``adjust_r_by_full_sch_and_last_stage_only_pmtn_sch``
                **and** ``adjust_r_by_half`` together; the half-adjust
                is bundled with ``adjust_r`` in this composite.
            emit_phase_schedules: When ``True``, the composite's
                per-snapshot ``mcf_lb_phase_schedule`` JSON files (and
                downstream ``phase_gantt_png`` renderings) are kept on
                disk. Default ``False`` — the composite clears its own
                appended entries from ``mcf_lb_phase_schedules`` before
                returning so the runner does not dump them. The
                per-instance phase-metric CSVs are emitted regardless.

        Returns:
            The single registered ``SubroutineReport`` whose
            ``obj_bound`` is round-1's MCF LB and whose ``obj_value``
            matches the registered (best) solution. When
            ``is_stopping_condition`` fires before round 1 produces a
            full schedule, returns a stop-report from
            ``_make_stop_report`` without registering.
        """
        start_elapsed = time.monotonic()
        self.adjust_ref_full_sol = None
        # Composite owns the phase-schedule list for this call: clear once
        # at entry so each round's recordings live side-by-side under their
        # ``temporarily_extended_context`` namespace prefix.
        self.mcf_lb_phase_schedules.clear()

        def _stop(label: str) -> SubroutineReport:
            self.logger.info("calc_mcf_lb_and_derive_full_sch: %s", label)
            return self._make_stop_report(start_elapsed)

        if self.is_stopping_condition():
            return _stop("stop guard fired at entry (before round1_apply_lb_by_mcf)")

        with self.temporarily_extended_context("r1"):
            r_lb_r1 = self.apply_lb_by_mcf(
                draw_heatmap=draw_pmtn_sch_heatmap,
                heatmap_sort=heatmap_sort,
                _register_report=False,
            )
            if self.is_stopping_condition():
                return _stop("stop guard fired before round1_heuristic_last_stage_only")
            self.heuristic_last_stage_only_sch_from_mcf_lb(
                job_priority=job_placement_priority,
                placement_priority=last_stage_only_placement_criteria,
                _register_report=False,
            )
            if self.is_stopping_condition():
                return _stop("stop guard fired before round1_build_full_sch")
            r1, s1 = self._build_full_sch_core()
        self.adjust_ref_full_sol = s1

        def _finalize(
            best_r: SubroutineReport, best_s: FFcDDWSolution | None
        ) -> SubroutineReport:
            self._emit_calc_mcf_lb_phase_metrics_csv()
            if not emit_phase_schedules:
                self.mcf_lb_phase_schedules.clear()
            final_report = SubroutineReport(
                elapsed_time=time.monotonic() - start_elapsed,
                obj_value=best_r.obj_value,
                obj_bound=r_lb_r1.obj_bound,
            )
            self._register(final_report, best_s)
            self.adjust_ref_full_sol = None
            return final_report

        if not (adjust_p or adjust_r):
            return _finalize(r1, s1)
        if self.is_stopping_condition():
            self.logger.info(
                "calc_mcf_lb_and_derive_full_sch: stop guard fired before "
                "round2_check (registering round1 result)"
            )
            return _finalize(r1, s1)
        if s1 is None:
            return _finalize(r1, s1)

        incumbent_makespan = int(s1.schedule.makespan)
        ls_only_pmtn_makespan = int(self.mcf_preemptive_schedule.makespan)
        makespan_delta = incumbent_makespan - ls_only_pmtn_makespan
        if makespan_delta <= 0:
            self.logger.info(
                "calc_mcf_lb_and_derive_full_sch: round1 makespan=%d, "
                "ls_only_pmtn makespan=%d, delta=%d <= 0 — skipping adjust round",
                incumbent_makespan,
                ls_only_pmtn_makespan,
                makespan_delta,
            )
            return _finalize(r1, s1)

        if self.is_stopping_condition():
            self.logger.info(
                "calc_mcf_lb_and_derive_full_sch: stop guard fired before "
                "round2_apply_lb_by_mcf (registering round1 result)"
            )
            return _finalize(r1, s1)

        with self.temporarily_extended_context("r2"):
            self.apply_lb_by_mcf(
                draw_heatmap=draw_pmtn_sch_heatmap,
                heatmap_sort=heatmap_sort,
                adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=adjust_p,
                adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=adjust_r,
                adjust_r_by_half=adjust_r,
                _register_report=False,
            )
            if self.is_stopping_condition():
                self.logger.info(
                    "calc_mcf_lb_and_derive_full_sch: stop guard fired before "
                    "round2_heuristic_last_stage_only (registering round1 result)"
                )
                return _finalize(r1, s1)
            self.heuristic_last_stage_only_sch_from_mcf_lb(
                job_priority=job_placement_priority,
                placement_priority=last_stage_only_placement_criteria,
                adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=adjust_p,
                adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=adjust_r,
                adjust_r_by_half=adjust_r,
                _register_report=False,
            )
            if self.is_stopping_condition():
                self.logger.info(
                    "calc_mcf_lb_and_derive_full_sch: stop guard fired before "
                    "round2_build_full_sch (registering round1 result)"
                )
                return _finalize(r1, s1)
            r2, s2 = self._build_full_sch_core()

        best_r, best_s = r1, s1
        if s2 is not None and (s1 is None or s2.obj_value <= s1.obj_value):
            best_r, best_s = r2, s2
        return _finalize(best_r, best_s)

    def run_mcf_lb_4(
        self,
        last_stage_only_priority_tags: Sequence[SeedTag] | None = None,
        last_stage_only_use_heuristic: bool = False,
        last_stage_only_heuristic_first_improvement_restart: bool = False,
        last_stage_only_heuristic_insert_radius: int | None = None,
        last_stage_only_cp_pf_method: PFMethod | None = None,
        last_stage_only_cp_solver_thread_cnt: int = 1,
        repeat_last_stage_only_cp_while_improving: bool = False,
        log_last_stage_only_cp_search_progress: bool = False,
        last_stage_only_tl: float | str | None = None,
        full_cp_pf_method: PFMethod | None = None,
        full_cp_solver_thread_cnt: int = 1,
        repeat_full_cp_while_improving: bool = False,
        log_full_cp_search_progress: bool = False,
        full_cp_tl: float | str | None = None,
    ) -> SubroutineReport:
        """Run the 4-phase MCF-LB algorithm and register the best incumbent.

        Phase 1 solves the MCF relaxation and dispatches one last-stage seed
        per priority map. Phase 2 runs a CP-SAT last-stage-only solve for each
        seed and picks the best. Phase 3 reverse-dispatches the best last-stage
        solution to a full schedule. Phase 4 runs a full CP-SAT profile-fix
        solve warm-started from the Phase 3 incumbent.

        Args:
            last_stage_only_priority_tags: Priority tags used in Phase 1 to
                generate dispatch seeds. ``None`` uses all available tags.
            last_stage_only_use_heuristic: When ``True``, Phase 2 solves each
                seed with the cumulative reinsertion heuristic instead of
                CP-SAT. The other ``last_stage_only_cp_*`` /
                ``repeat_last_stage_only_cp_while_improving`` /
                ``log_last_stage_only_cp_search_progress`` arguments are
                ignored on the heuristic path.
            last_stage_only_heuristic_first_improvement_restart: Restart
                policy for the cumulative heuristic. ``False`` (default)
                completes a full pass over the sequence and restarts only if
                any move was made; ``True`` restarts immediately when the
                first improving move is found.
            last_stage_only_heuristic_insert_radius: Maximum number of
                positions a job may move from its current position during a
                single reinsertion scan in the cumulative heuristic. Accepts
                a ``float``, a ``"<n>n"`` / ``"<n>nc"`` / ``"<n>c"`` /
                ``"<n>m"`` expression (resolved against the instance's
                ``n``/``c``/``m``), or ``None`` to allow unlimited radius.
            last_stage_only_cp_pf_method: Profile-fix precedence policy for the
                Phase 2 last-stage CP-SAT solve. ``None`` (default) skips the
                precedence-arc pass entirely while keeping warm-start / ET
                hints. Previously the implicit default was ``"PF0"``
                (stage-level time-based selection); set explicitly to restore
                that behaviour.
            last_stage_only_cp_solver_thread_cnt: Number of CP-SAT solver
                threads for the Phase 2 last-stage-only CP-SAT solve.
            repeat_last_stage_only_cp_while_improving: If ``True``, Phase 2
                re-solves with the updated profile until no improvement.
            log_last_stage_only_cp_search_progress: When ``True``, the Phase 2
                last-stage CP-SAT solver writes its search-progress log to a
                file under the subroutine output directory.
            last_stage_only_tl: Per-solve time limit (seconds) for the
                Phase 2 last-stage-only CP-SAT model. Accepts a ``float``,
                or any expression supported by ``resolve_value_expr``
                (``"<n>nc"``, ``"<n>n"``, ``"<n>c"``, ``"<n>m"``),
                or ``None`` for no limit.
            full_cp_pf_method: Same policy for the Phase 4 full CP-SAT solve.
                Same ``None`` / ``"PF0"`` distinction applies.
            full_cp_solver_thread_cnt: Number of CP-SAT solver threads for the
                Phase 4 full CP-SAT solve.
            repeat_full_cp_while_improving: If ``True``, Phase 4 re-solves
                with the updated profile until no improvement.
            log_full_cp_search_progress: When ``True``, the Phase 4 full
                CP-SAT solver writes its search-progress log to a file under
                the subroutine output directory.
            full_cp_tl: Same for the Phase 4 full CP-SAT model.

        Returns:
            SubroutineReport with ``obj_bound`` = MCF LB and ``obj_value`` =
            Phase 4 objective (or Phase 3 dispatched objective if Phase 4 is
            infeasible).
        """
        start_elapsed = time.monotonic()
        diag = MCFLBDiagnostic()
        self.mcf_lb_diagnostic = diag
        instance = self.instance
        last_stage_only_tl_seconds = resolve_value_expr(
            last_stage_only_tl,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )
        full_cp_tl_seconds = resolve_value_expr(
            full_cp_tl,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )
        last_stage_only_heuristic_insert_radius_count = resolve_value_expr(
            last_stage_only_heuristic_insert_radius,
            instance.job_count,
            instance.stage_count,
            instance.last_stage_mc_count,
        )
        if last_stage_only_heuristic_insert_radius_count is not None:
            last_stage_only_heuristic_insert_radius_count = int(
                last_stage_only_heuristic_insert_radius_count
            )

        # Phase 1: MCF LB + one last-stage dispatch seed per MCF priority map.
        phase1 = run_phase1(
            instance,
            diag,
            logger=self.logger,
            last_stage_only_priority_tags=last_stage_only_priority_tags,
        )
        obj_bound_by_mcf = phase1.mcf_lb
        self.mcf_preemptive_schedule = phase1.mcf_preemptive_schedule
        self.mcf_lb_phase_schedules.clear()
        self._record_mcf_lb_phase(
            ("1_mcf_preemptive_sch", phase1.mcf_preemptive_schedule)
        )
        for seed in phase1.last_stage_seeds:
            self._record_mcf_lb_phase(
                (f"2_last_stage_only_init_schedule__{seed.tag}", seed.init_schedule)
            )

        # Phase 2: solve the last-stage CP-SAT model for each seed, pick best.
        _tl_suffix = (
            f" with time limit {last_stage_only_tl_seconds:.2f} seconds"
            if last_stage_only_tl_seconds is not None
            else ""
        )
        self.logger.info(
            "Phase 1 MCF LB: %d; preparing Phase 2 last-stage-only CP-SAT solves%s",
            int(obj_bound_by_mcf),
            _tl_suffix,
        )
        phase2 = run_phase2(
            phase1,
            instance,
            diag,
            logger=self.logger,
            pf_method=last_stage_only_cp_pf_method,
            solver_thread_cnt=last_stage_only_cp_solver_thread_cnt,
            repeat_last_stage_only_cp_while_improving=repeat_last_stage_only_cp_while_improving,
            tl_seconds=last_stage_only_tl_seconds,
            log_search_progress=log_last_stage_only_cp_search_progress,
            solver_log_path_getter=self.get_file_path_for_subroutine,
            use_heuristic=last_stage_only_use_heuristic,
            heuristic_first_improvement_restart=last_stage_only_heuristic_first_improvement_restart,
            heuristic_insert_radius=last_stage_only_heuristic_insert_radius_count,
        )
        if phase2 is None:
            elapsed = time.monotonic() - start_elapsed
            report = SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=obj_bound_by_mcf
            )
            self._register(report, None)
            return report
        self.last_stage_only_sol = FFcDDWSolution(
            schedule=phase2.last_stage_only_schedule,
            obj_value=phase2.last_stage_only_obj,
            obj_bound=obj_bound_by_mcf,
        )
        self.last_stage_only_sol_p_increment = 0
        for candidate in phase2.candidates:
            self._record_mcf_lb_phase(
                (
                    f"3_last_stage_only_schedule__{candidate.tag}",
                    candidate.last_stage_only_schedule,
                )
            )
        self._record_mcf_lb_phase(
            ("3_last_stage_only_schedule_chosen", phase2.last_stage_only_schedule)
        )

        # Phase 3: reverse-dispatch + unflip.
        phase3 = run_phase3(
            phase1,
            phase2,
            instance,
            diag,
            logger=self.logger,
        )
        if phase3 is None:
            elapsed = time.monotonic() - start_elapsed
            report = SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=obj_bound_by_mcf
            )
            self._register(report, None)
            return report
        if phase3.ls_only_sch_delayed is not None:
            self._record_mcf_lb_phase(
                ("3_ls_only_sch_delayed", phase3.ls_only_sch_delayed)
            )
        if phase3.ls_only_sch_flipped is not None:
            self._record_mcf_lb_phase(
                ("4_ls_only_sch_flipped", phase3.ls_only_sch_flipped)
            )
        if phase3.full_sch_before_unflip is not None:
            self._record_mcf_lb_phase(
                ("5_full_sch_before_unflip", phase3.full_sch_before_unflip)
            )
        self._record_mcf_lb_phase(
            ("6_full_sch_from_ls_only_sch", phase3.full_sch_from_ls_only_sch)
        )

        # Phase 4: profile-fix CP-SAT full solve.
        _tl_suffix = (
            f" with time limit {full_cp_tl_seconds:.2f} seconds"
            if full_cp_tl_seconds is not None
            else ""
        )
        self.logger.info(
            "Phase 3 dispatched objective: %d; preparing Phase 4 full CP-SAT solve%s",
            int(phase3.dispatched_obj),
            _tl_suffix,
        )
        phase4 = run_phase4(
            phase1,
            phase3,
            instance,
            diag,
            pf_method=full_cp_pf_method,
            solver_thread_cnt=full_cp_solver_thread_cnt,
            logger=self.logger,
            repeat_full_cp_while_improving=repeat_full_cp_while_improving,
            cp_tl_seconds=full_cp_tl_seconds,
            log_search_progress=log_full_cp_search_progress,
            solver_log_path_getter=self.get_file_path_for_subroutine,
        )

        elapsed = time.monotonic() - start_elapsed
        if phase4.final_schedule is None:
            # Infeasible profile-fix: register the phase-3 incumbent; the
            # profile-fix bound is not a valid global bound, so report
            # the MCF LB instead.
            report = SubroutineReport(
                elapsed_time=elapsed,
                obj_value=phase3.dispatched_obj,
                obj_bound=obj_bound_by_mcf,
            )
            self._register(
                report,
                FFcDDWSolution(
                    schedule=phase3.full_sch_from_ls_only_sch,
                    obj_value=phase3.dispatched_obj,
                    obj_bound=obj_bound_by_mcf,
                ),
            )
            return report
        self._record_mcf_lb_phase(("7_final_schedule", phase4.final_schedule))

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=phase4.final_obj,
            obj_bound=obj_bound_by_mcf,
        )
        self._register(
            report,
            FFcDDWSolution(
                schedule=phase4.final_schedule,
                obj_value=phase4.final_obj,
                obj_bound=obj_bound_by_mcf,
            ),
        )
        return report

    def run_last_stage_cp_sat_lb(
        self,
        solver_thread_cnt: int = 1,
    ) -> SubroutineReport:
        """Step method: build a last-stage-only CP-SAT schedule tight against
        the MCF preemptive LB.

        MCF provides preemptive start times used twice: as the job-release
        map for an initial single-stage dispatch, and indirectly as warm-start
        hints into the CP-SAT model. True release bounds ``r_j`` (sum of
        processing times on stages 1..c-1) are also enforced as domain lower
        bounds in the CP-SAT model via ``job_2_release``.

        Solves under a time budget of ``0.01 * n * c`` seconds. The resulting
        partial schedule (only the last stage is filled) is stored on
        ``self.last_stage_only_sol`` for downstream subroutines; it is
        NOT registered with the incumbent manager (a partial schedule is not
        a full incumbent).
        """
        start_elapsed = time.monotonic()

        mcf = ParallelMachinePreemptionMcf.from_instance(self.instance)
        mcf.solve()
        if not mcf.is_optimal():
            raise RuntimeError(f"MCF not optimal for instance {self.instance.name}")
        mcf_start_map = mcf.get_job_2_start_time_map()
        mcf_lb = float(mcf.get_obj_value())

        last_stage_id = self.instance.stage_id_list[-1]
        r_j_map = self.instance.get_job_2_p_sum_except_last_stage()
        duration_map = self.instance.get_job_2_p_map_for_stage(last_stage_id)
        n = self.instance.job_count
        c = self.instance.stage_count

        params_for_horizon = BaseModelBuilder.make_params(self.instance)
        horizon = sum(params_for_horizon.p.values())

        builder = BaseModelBuilder()
        pm_mdl, pm_params, pm_ops_vars, pm_et_vars = builder.build(
            instance=self.instance,
            horizon=horizon,
            last_stage_only=True,
            job_2_release=r_j_map,
            obj_lb=mcf_lb,
        )

        job_2_pos = {j: i for i, j in enumerate(self.instance.job_id_list)}
        job_sequence = sorted(
            self.instance.job_id_list,
            key=lambda j: (
                mcf_start_map[j] is None,
                mcf_start_map[j] if mcf_start_map[j] is not None else 0,
                job_2_pos[j],
            ),
        )
        job_2_release_for_dispatch: dict[str, int] = {}
        for j in self.instance.job_id_list:
            mcf_start = mcf_start_map[j]
            job_2_release_for_dispatch[j] = (
                mcf_start if mcf_start is not None else r_j_map[j]
            )

        init_schedule = FFcSchedule(
            jobs=self.instance.job_id_list,
            stages=self.instance.stage_id_list,
            machines_per_stage=self.instance.stage_2_machines_map,
        )
        init_schedule.dispatch_stage_by_jobs(
            last_stage_id,
            job_sequence,
            duration_map,
            job_2_release=job_2_release_for_dispatch,
        )

        BaseModelBuilder.apply_start_hints_from_start_time_map(
            pm_mdl, pm_params, pm_ops_vars, init_schedule.get_jik_2_start_time_map()
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            pm_mdl, pm_params, pm_ops_vars, init_schedule.get_jik_2_end_time_map()
        )
        BaseModelBuilder.apply_et_hints_from_ref_schedule(
            pm_mdl, pm_params, pm_et_vars, init_schedule
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(0.01 * n * c)
        solver.parameters.num_workers = solver_thread_cnt
        status = solver.Solve(pm_mdl)

        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        obj_value: float | None = solver.objective_value
        obj_bound: float | None = None
        # is a valid global LB since no profile-fixing is applied in this model
        try:
            obj_bound = float(solver.best_objective_bound)
            self.logger.info(
                "run_last_stage_cp_sat_lb: UB=%d, LB=%d (MCF LB=%d)",
                obj_value,
                obj_bound,
                mcf_lb,
            )
        except Exception:
            obj_bound = None
            self.logger.warning(
                "run_last_stage_cp_sat_lb: UB=%d, CP-SAT LB unknown (MCF LB=%d)",
                obj_value,
                mcf_lb,
            )

        if not has_solution:
            elapsed = time.monotonic() - start_elapsed
            self.logger.warning(
                "run_last_stage_cp_sat_lb: no feasible solution (status=%s)",
                solver.StatusName(status),
            )
            report = SubroutineReport(
                elapsed_time=elapsed,
                obj_value=None,
                obj_bound=mcf_lb,
            )
            self._register(report, None)
            return report

        j_i_2_start = {
            (j, last_stage_id): int(
                solver.Value(pm_ops_vars.op_start[j, last_stage_id])
            )
            for j in pm_params.j_list
        }
        j_i_2_end = {
            (j, last_stage_id): int(solver.Value(pm_ops_vars.op_end[j, last_stage_id]))
            for j in pm_params.j_list
        }
        out_schedule = build_schedule_from_op_starts(
            self.instance, j_i_2_start, j_i_2_end, stages=[last_stage_id]
        )

        cp_obj = float(solver.objective_value)
        self.last_stage_only_sol = FFcDDWSolution(
            schedule=out_schedule, obj_value=cp_obj, obj_bound=mcf_lb
        )
        self.last_stage_only_sol_p_increment = 0
        self._record_mcf_lb_phase(("2_ls_only_sch_from_cp_sat_lb", out_schedule))

        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=cp_obj,
            obj_bound=obj_bound if obj_bound is not None else mcf_lb,
        )
        self._register(report, None)
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
        self, job_sequence: Sequence[str]
    ) -> tuple[FFcSchedule, float]:
        """Dispatch ``job_sequence`` via the reverse-instance + IIT pipeline.

        Steps: stage-reverse the instance, dispatch ``reversed(job_sequence)``
        with :meth:`MixedDispatcher.get_best_mixed_schedule_by_sequence`
        minimising makespan, unflip the result with
        :meth:`FFcSchedule.as_reversed`, push left to semi-active form, then
        insert idle time on the last stage.
        """
        instance = self.instance
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

    def run_profile_fixed_ns(
        self,
        cp_tl: float | str | None = None,
        solver_thread_cnt: int = 1,
        pf_method: PFMethod = "PF0",
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
        params_for_horizon = BaseModelBuilder.make_params(instance)
        horizon = sum(params_for_horizon.p.values())

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
        batch_tl_mode: NehCpBatchTlMode = "constant",
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

    def run_mcf_lb_then_neh_cp(
        self,
        solver_thread_cnt: int = 1,
        added_batch_size: int = 1,
        extra_batch_size_expr: str | None = None,
        cp_tl: float | str | None = None,
        neh_cp_total_timelimit: float | str | None = None,
        num_batches: int | None = None,
        batch_tl_mode: NehCpBatchTlMode = "constant",
        batch_tl_offset_seconds: float = 0.01,
        apply_cumulative_tl: bool = False,
        pf_method: PFMethod = "PF1",
        skip_pf_below_obj: str | float | None = None,
        make_semi_active_after_cp: bool = False,
        make_semi_active_after_cp_obj_threshold: int = -1,
        minimize_makespan_lex: bool = False,
        cp_tl_2nd_obj: float | str | None = None,
        error_if_infeasible: bool = False,
        draw_heatmap: bool = False,
        heatmap_sort: HeatmapSort = "due2-weight-pos",
        keep_step_schedules: bool = False,
    ) -> SubroutineReport:
        """Step method: solve the MCF preemptive relaxation, derive a job
        sequence by ascending MCF time-window width
        ``(t_max_j - t_min_j)``, then run :class:`NehCpDispatcher` on that
        sequence.

        Job sequence tie-break order:
          1. Window width ``(t_max_j - t_min_j)`` ASC
          2. Total weight ``(w⁻_j + w⁺_j)`` DESC
          3. Last-stage processing time ``p_{c,j}`` DESC
          4. Native ``instance.job_id_list`` position ASC

        ``neh_cp_total_timelimit`` bounds only the NEH-CP CP-SAT phase; the
        MCF solve runs to optimality outside that budget so users do not
        confuse "NEH-CP time limit" with "step time limit".

        Reports ``obj_value`` = weighted E+T of the NEH-CP schedule and
        ``obj_bound`` = MCF lower bound. Emits the MCF preemptive schedule
        to ``self.mcf_lb_phase_schedules`` for post-run Gantt rendering.

        ``draw_heatmap`` mirrors ``apply_lb_by_mcf``: when ``True``,
        builds the signed C-cost matrix with the MCF preemptive flow
        overlaid and writes ``<ins>_C_heatmap.yaml``; the post-run
        reporter (gated by ``draw_gantt``) renders the matching HTML.

        ``keep_step_schedules`` propagates to :class:`NehCpOption`. When
        ``True``, every NEH-CP step's (dispatched, cp_raw, semi_active)
        schedule triplet is appended to ``self.mcf_lb_phase_schedules``
        so the runner emits one ``*_schedule.yaml`` per snapshot and the
        reporter renders one Gantt PNG per snapshot. Heavy on disk; use
        for diagnostics only.
        """
        start_elapsed = time.monotonic()
        if self.is_stopping_condition():
            return self._make_stop_report(start_elapsed)
        instance = self.instance
        n = instance.job_count
        c = instance.stage_count
        m = instance.last_stage_mc_count

        prev_diag = self.mcf_lb_diagnostic
        diag = MCFLBDiagnostic()
        self.mcf_lb_diagnostic = diag
        try:
            mcf_result = solve_mcf_lb(
                instance,
                diag,
                stop_predicate=self.is_stopping_condition,
                logger=self.logger,
            )
        except MCFLBStopRequested:
            self.mcf_lb_diagnostic = prev_diag
            self.logger.info(
                "run_mcf_lb_then_neh_cp: stop predicate fired before MCF solve; "
                "skipping."
            )
            return self._make_stop_report(start_elapsed)
        obj_bound_by_mcf = mcf_result.mcf_lb
        self.mcf_preemptive_schedule = mcf_result.mcf_preemptive_schedule
        self.mcf_lb_phase_schedules.clear()
        self._record_mcf_lb_phase(
            ("1_mcf_preemptive_sch", mcf_result.mcf_preemptive_schedule)
        )
        self.logger.info("run_mcf_lb_then_neh_cp: MCF LB = %d", int(obj_bound_by_mcf))

        if self.is_stopping_condition():
            return self._make_stop_report(start_elapsed)

        if draw_heatmap:
            from ..io import build_signed_cost_matrix, dump_signed_cost_heatmap_yaml

            yaml_path = self.try_get_file_path_for_subroutine("_C_heatmap.yaml")
            if yaml_path is not None:
                heatmap_data = build_signed_cost_matrix(
                    instance,
                    sort=heatmap_sort,
                    x_jt_map=mcf_result.mcf.get_variable_value_dict(),
                    obj_value=obj_bound_by_mcf,
                )
                dump_signed_cost_heatmap_yaml(yaml_path, heatmap_data)
                self.logger.info(
                    "run_mcf_lb_then_neh_cp: wrote heatmap YAML to %s", yaml_path
                )

        custom_job_sequence = self._mcf_window_width_job_sequence(
            mcf_result.mcf, instance
        )

        cp_tl_seconds = resolve_value_expr(cp_tl, n, c, m)
        total_timelimit_seconds = (
            resolve_value_expr(neh_cp_total_timelimit, n, c, m)
            if neh_cp_total_timelimit is not None
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
            "run_mcf_lb_then_neh_cp: threading wall_clock_deadline=%.3fs "
            "(remaining=%.3fs), obj_lb=%s",
            wall_clock_deadline_sec,
            remaining_sec,
            f"{obj_lb:.2f}" if obj_lb is not None else "None",
        )

        option = NehCpOption(
            custom_job_sequence=tuple(custom_job_sequence),
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
            keep_step_schedules=keep_step_schedules,
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
                "run_mcf_lb_then_neh_cp: dispatcher stopped early after batch %s; "
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
            obj_bound=obj_bound_by_mcf,
        )
        neh_cp_progress_log = record.progress_log or ()
        if result is not None and result.schedule is not None:
            self._register(
                report,
                FFcDDWSolution(
                    schedule=result.schedule,
                    obj_value=obj_value,
                    obj_bound=obj_bound_by_mcf,
                ),
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
            step_schedules = result.metrics.get("step_schedules")
            if step_schedules:
                # Numbered prefix continues from "1_mcf_preemptive_sch"
                # so post-run Gantt PNGs sort in the natural execution order.
                for (
                    step_idx,
                    dispatched_sch,
                    cp_raw_sch,
                    semi_active_sch,
                ) in step_schedules:
                    self._record_mcf_lb_phase(
                        (
                            f"2_neh_cp_step_{step_idx:03d}_a_dispatched",
                            dispatched_sch,
                        )
                    )
                    if cp_raw_sch is not None:
                        self._record_mcf_lb_phase(
                            (
                                f"2_neh_cp_step_{step_idx:03d}_b_cp",
                                cp_raw_sch,
                            )
                        )
                    if semi_active_sch is not None:
                        self._record_mcf_lb_phase(
                            (
                                f"2_neh_cp_step_{step_idx:03d}_c_semi_active",
                                semi_active_sch,
                            )
                        )

        return report

    def _mcf_window_width_job_sequence(
        self,
        mcf: ParallelMachinePreemptionMcf,
        instance: FFcDDWParameters,
    ) -> list[str]:
        """Order jobs by ascending MCF normalized window spread
        ``(t_max_j - t_min_j) / p_{c,j}``.

        Thin wrapper over
        `ffc_ddw_sum_et.algorithm.mcf_lb.utils.pm_pmtn_sort_job_sequence_with_log`
        that supplies the live MCF time-window map. The shared helper owns
        the tie-break order and the rank-by-rank diagnostic log.
        """
        last_stage_id = instance.stage_id_list[-1]
        p_map = instance.get_job_2_p_map_for_stage(last_stage_id)
        return pm_pmtn_sort_job_sequence_with_log(
            mcf.get_job_2_time_window_map(),
            p_map,
            instance,
            logger=self.logger,
        )
