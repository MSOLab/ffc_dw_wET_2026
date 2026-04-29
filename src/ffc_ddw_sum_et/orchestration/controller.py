"""FAM subroutine controller for routix-based experiment orchestration."""

from __future__ import annotations

import time
from typing import Callable, Literal, Sequence

from ortools.sat.python import cp_model
from routix.io import dump_yaml
from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
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
from ffc_ddw_sum_et.algorithm.mcf_lb import MCFLBDiagnostic
from ffc_ddw_sum_et.algorithm.mcf_lb.phase1_mcf import (
    SeedTag,
    run_phase1,
    solve_mcf_lb,
)
from ffc_ddw_sum_et.algorithm.mcf_lb.phase2_last_stage import run_phase2
from ffc_ddw_sum_et.algorithm.mcf_lb.phase3_dispatch import run_phase3
from ffc_ddw_sum_et.algorithm.mcf_lb.phase4_profile_fix import run_phase4
from ffc_ddw_sum_et.algorithm.neh_cp import (
    NehCpBatchTlMode,
    NehCpDispatcher,
    NehCpJobPriority,
    NehCpOption,
)
from ffc_ddw_sum_et.algorithm.parallel_mc_pmtn import ParallelMachinePreemptionMcf
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness
from ffc_ddw_sum_et.solution.schedule_build import build_schedule_from_op_starts

from .controller_core import FFcDDWSubroutineControllerCore
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
            self.solution_manager.register(report, fam_solution)

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
            self.solution_manager.register(report, bn2d_solution)

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
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=None
            )

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
        self.solution_manager.register(
            report,
            FFcDDWSolution(schedule=best_sch, obj_value=obj_value, obj_bound=None),
        )
        return report

    def apply_lb_by_mcf(
        self,
        draw_heatmap: bool = False,
        heatmap_sort: Literal["due2-window", "neh-cp"] = "due2-window",
    ) -> SubroutineReport:
        """Step method: compute the MCF preemptive lower bound and report it
        without constructing a feasible full schedule.

        Solves the MCF relaxation, records ``mcf_lb`` on the diagnostic, and
        returns a :class:`SubroutineReport` with ``obj_value=None`` and
        ``obj_bound = mcf_lb``. No incumbent is registered with the solution
        manager (this subroutine produces no full schedule), so no Gantt or
        ``*_schedule.yaml`` is emitted for this step. The MCF preemptive
        schedule is still stored on ``self.mcf_preemptive_schedule`` and
        appended to ``self.mcf_lb_phase_schedules`` so downstream diagnostics
        keyed off those attributes continue to work.

        Args:
            draw_heatmap: When ``True``, build the parallel-machine signed
                C-cost matrix for the instance and dump it to
                ``<ins>_C_heatmap.yaml`` next to the other per-instance
                artifacts. The post-run reporter (gated by ``draw_gantt``)
                renders the matching HTML.
            heatmap_sort: Row ordering for the heatmap. ``"due2-window"``
                sorts by ``max(r_j, d⁺-p)`` then ``d⁺`` then ``d⁻``;
                ``"neh-cp"`` sorts by ``(max(w⁻, w⁺), w⁻+w⁺, window width)``.
                Ignored when ``draw_heatmap`` is ``False``.
        """
        start_elapsed = time.monotonic()
        diag = MCFLBDiagnostic()
        self.mcf_lb_diagnostic = diag

        mcf_result = solve_mcf_lb(self.instance, diag)
        obj_bound_by_mcf = mcf_result.mcf_lb

        self.mcf_preemptive_schedule = mcf_result.mcf_preemptive_schedule
        self.mcf_lb_phase_schedules.clear()
        self.mcf_lb_phase_schedules.append(
            ("1_mcf_preemptive_schedule", mcf_result.mcf_preemptive_schedule)
        )

        self.logger.info("apply_lb_by_mcf: MCF LB = %d", int(obj_bound_by_mcf))

        if draw_heatmap:
            from ..io import build_signed_cost_matrix, dump_signed_cost_heatmap_yaml

            yaml_path = self.try_get_file_path_for_subroutine("_C_heatmap.yaml")
            if yaml_path is not None:
                heatmap_data = build_signed_cost_matrix(
                    self.instance,
                    sort=heatmap_sort,
                    x_jt_map=mcf_result.mcf.get_variable_value_dict(),
                    obj_value=obj_bound_by_mcf,
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

        elapsed = time.monotonic() - start_elapsed
        return SubroutineReport(
            elapsed_time=elapsed,
            obj_value=None,
            obj_bound=obj_bound_by_mcf,
        )

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
        machine_then_job: bool = False,
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
            machine_then_job: Passed to Phase 3 reverse-dispatch ordering.
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
        self.mcf_lb_phase_schedules.append(
            ("1_mcf_preemptive_schedule", phase1.mcf_preemptive_schedule)
        )
        for seed in phase1.last_stage_seeds:
            self.mcf_lb_phase_schedules.append(
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
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=obj_bound_by_mcf
            )
        self.last_stage_cp_sat_solution = FFcDDWSolution(
            schedule=phase2.last_stage_only_schedule,
            obj_value=phase2.last_stage_only_obj,
            obj_bound=obj_bound_by_mcf,
        )
        for candidate in phase2.candidates:
            self.mcf_lb_phase_schedules.append(
                (
                    f"3_last_stage_only_schedule__{candidate.tag}",
                    candidate.last_stage_only_schedule,
                )
            )
        self.mcf_lb_phase_schedules.append(
            ("3_last_stage_only_schedule_chosen", phase2.last_stage_only_schedule)
        )

        # Phase 3: reverse-dispatch + unflip.
        phase3 = run_phase3(
            phase1,
            phase2,
            instance,
            diag,
            logger=self.logger,
            machine_then_job=machine_then_job,
        )
        if phase3 is None:
            elapsed = time.monotonic() - start_elapsed
            return SubroutineReport(
                elapsed_time=elapsed, obj_value=None, obj_bound=obj_bound_by_mcf
            )
        if phase3.last_stage_only_schedule_flipped is not None:
            self.mcf_lb_phase_schedules.append(
                (
                    "4_last_stage_only_schedule_flipped",
                    phase3.last_stage_only_schedule_flipped,
                )
            )
        if phase3.dispatched_schedule_before_unflipping is not None:
            self.mcf_lb_phase_schedules.append(
                (
                    "5_dispatched_schedule_before_unflipping",
                    phase3.dispatched_schedule_before_unflipping,
                )
            )
        self.mcf_lb_phase_schedules.append(
            ("6_dispatched_schedule", phase3.dispatched_schedule)
        )
        self.solution_manager.register(
            SubroutineReport(
                elapsed_time=time.monotonic() - start_elapsed,
                obj_value=phase3.dispatched_obj,
                obj_bound=obj_bound_by_mcf,
            ),
            FFcDDWSolution(
                schedule=phase3.dispatched_schedule,
                obj_value=phase3.dispatched_obj,
                obj_bound=obj_bound_by_mcf,
            ),
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
            # Infeasible profile-fix: keep the phase-3 incumbent; the
            # profile-fix bound is not a valid global bound, so report
            # the MCF LB instead.
            return SubroutineReport(
                elapsed_time=elapsed,
                obj_value=phase3.dispatched_obj,
                obj_bound=obj_bound_by_mcf,
            )
        self.mcf_lb_phase_schedules.append(("7_final_schedule", phase4.final_schedule))

        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=phase4.final_obj,
            obj_bound=obj_bound_by_mcf,
        )
        self.solution_manager.register(
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
        ``self.last_stage_cp_sat_solution`` for downstream subroutines; it is
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
            return SubroutineReport(
                elapsed_time=elapsed,
                obj_value=None,
                obj_bound=mcf_lb,
            )

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
        self.last_stage_cp_sat_solution = FFcDDWSolution(
            schedule=out_schedule, obj_value=cp_obj, obj_bound=mcf_lb
        )

        elapsed = time.monotonic() - start_elapsed
        report = SubroutineReport(
            elapsed_time=elapsed,
            obj_value=cp_obj,
            obj_bound=obj_bound if obj_bound is not None else mcf_lb,
        )
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
        self.solution_manager.register(
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
        self.solution_manager.register(
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
            return SubroutineReport(
                elapsed_time=elapsed,
                obj_value=None,
                obj_bound=None,
            )

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
        self.solution_manager.register(
            report,
            FFcDDWSolution(schedule=schedule, obj_value=obj_value),
        )
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
            minimize_makespan_lex=minimize_makespan_lex,
            cp_tl_2nd_obj_seconds=cp_tl_2nd_obj_seconds,
            error_if_infeasible=error_if_infeasible,
        )
        spec = AlgSpec(instance=instance, option=option, logger=self.logger)
        record = NehCpDispatcher().run(spec)

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
        if result is not None and result.schedule is not None:
            self.solution_manager.register(
                report,
                FFcDDWSolution(schedule=result.schedule, obj_value=obj_value),
            )

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
