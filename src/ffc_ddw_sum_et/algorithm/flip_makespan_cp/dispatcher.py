"""Stage-flip + makespan CP-SAT dispatcher warm-started from an incumbent.

High-level recipe:

1. Right-shift the incumbent's last stage via
   :meth:`FFcSchedule.delay_job_latest_leq_obj_contrib` so each op ends as
   late as possible without increasing per-job ET contribution. The new
   makespan ``T`` becomes the flip horizon.
2. Stage-reverse the instance (:meth:`FFcDDWParameters.reverse_stages`)
   and time-flip the right-shifted incumbent into a seed schedule on the
   reversed instance using ``(s', e') = (T - e, T - s)``.
3. Compact stages 2..C of the flipped seed via
   :meth:`FFcSchedule.make_semi_active` with
   ``start_from_stage=stage_id_list[1]``. The fixed first stage is
   skipped so the right-shifted incumbent's last-stage layout (= the
   CP fix positions) is preserved; only the remaining stages are
   left-shifted in flipped time to tighten the horizon.
4. Build a base CP-SAT model on the reversed instance with
   ``objective='makespan'`` (no E/T variables). The CP horizon is the
   compacted seed's makespan -- a feasible upper bound on the optimal
   flipped-instance makespan.
5. Fix the model's first stage (in flipped order) to the seed's start
   times via :func:`add_start_time_freezed_operation_constraints`.
6. Hint every operation start/end from the (compacted) seed.
7. Solve CP-SAT under the option's time cap.
8. Reconstruct the flipped schedule, ``as_reversed`` to original time,
   ``make_semi_active`` then ``insert_idle_time`` on the last stage --
   matching the post-process of Phase 3.

When ``option.emit_phase_schedules=True`` the dispatcher dumps the
seven load-bearing intermediate schedules as compact JSON. The path
for each phase is resolved by calling
``option.phase_schedule_path_getter(phase_name)`` (production wires
this through ``ArtifactLayout.artifact_path("flip_makespan_cp_phase_schedule", ...)``).
Phase names are 2-digit-prefixed so files sort by phase index on
disk:

1. ``01_incumbent`` -- input, ``spec.ref_solution``.
2. ``02_right_shifted`` -- after right-shift on the original instance.
3. ``03_flipped`` -- time-flipped seed on the reversed instance,
   pre-compaction.
4. ``04_flipped_compacted`` -- after ``make_semi_active`` on stages
   2..C (preserves the fixed first stage).
5. ``05_cp_solved`` -- CP-SAT result on the reversed instance.
6. ``06_unflipped_semi_active`` -- after ``as_reversed`` and
   ``make_semi_active`` on the original instance.
7. ``07_unflipped_final`` -- after ``insert_idle_time`` (= the
   schedule that gets registered as the solution).
"""

from __future__ import annotations

import logging
import time

from ortools.sat.python import cp_model

from ...io.schedule_json import dump_solution_json
from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ...solution.schedule_build import build_schedule_from_op_starts
from ..base.alg_record import (
    AlgRecord,
    AlgResult,
    ProgressLogEntry,
    TerminationReason,
    WorkStatus,
)
from ..base.alg_spec import AlgSpec
from ..cpsat_callbacks.obj_bound_recorder import ObjectiveBoundRecorder
from ..cpsat_callbacks.obj_value_recorder import ObjectiveValueRecorder
from ..cpsat_solver_options import CpsatSolverOptions, get_solver
from ..cumulative import BaseModelBuilder
from .option import FlipMakespanCpOption

__all__ = ["FlipMakespanCpDispatcher"]


class FlipMakespanCpDispatcher:
    """Solve a stage-flipped makespan CP model warm-started from an incumbent."""

    algorithm_id = "flip_makespan_cp"

    def run(self, spec: AlgSpec) -> AlgRecord:
        instance = self._validate_instance(spec)
        option = self._resolve_option(spec)
        logger = spec.logger if spec.logger is not None else logging.getLogger(__name__)

        if spec.ref_solution is None:
            raise RuntimeError(
                "FlipMakespanCpDispatcher requires spec.ref_solution (incumbent "
                "schedule); chain it after a step that registers an incumbent."
            )

        start = time.monotonic()
        self._maybe_emit_phase(
            option,
            logger,
            "01_incumbent",
            spec.ref_solution,
            instance.name,
            et_instance=instance,
        )

        # Right-shift the incumbent's last stage in-place on a deep copy.
        init_sched = spec.ref_solution.deepcopy()
        init_sched.delay_job_latest_leq_obj_contrib(instance.job_2_dw_ub_map)
        delayed_makespan = int(init_sched.makespan)
        self._maybe_emit_phase(
            option,
            logger,
            "02_right_shifted",
            init_sched,
            instance.name,
            et_instance=instance,
        )

        reversed_instance = FFcDDWParameters.reverse_stages(instance)
        flipped_seed = self._build_flipped_seed(
            init_sched, reversed_instance, flip_horizon=delayed_makespan
        )
        self._maybe_emit_phase(
            option, logger, "03_flipped", flipped_seed, instance.name
        )

        # Compact stages 2..C of the flipped seed to tighten the CP horizon.
        # The fixed first stage is preserved (start_from_stage skips it), so
        # incumbent's last-stage layout still drives the fix and the original
        # right-shift's ET shape is unchanged in flipped time.
        if len(reversed_instance.stage_id_list) > 1:
            flipped_seed.make_semi_active(
                reversed_instance.stage_2_job_2_p_map,
                start_from_stage=reversed_instance.stage_id_list[1],
            )
        cp_horizon = int(flipped_seed.makespan)
        self._maybe_emit_phase(
            option, logger, "04_flipped_compacted", flipped_seed, instance.name
        )

        builder = BaseModelBuilder()
        mdl, params, op_vars, _et_vars = builder.build(
            reversed_instance, horizon=cp_horizon, objective="makespan"
        )

        first_stage_id = reversed_instance.stage_id_list[0]
        first_stage_start_map = {
            (j, i, k): s
            for (j, i, k), s in flipped_seed.get_jik_2_start_time_map().items()
            if i == first_stage_id
        }
        BaseModelBuilder.add_start_time_freezed_operation_constraints(
            mdl, op_vars, first_stage_start_map
        )

        BaseModelBuilder.apply_start_hints_from_start_time_map(
            mdl, params, op_vars, flipped_seed.get_jik_2_start_time_map()
        )
        BaseModelBuilder.apply_end_hints_from_end_time_map(
            mdl, params, op_vars, flipped_seed.get_jik_2_end_time_map()
        )

        eff_tl: float | None
        if option.cp_tl_seconds is None:
            eff_tl = None
        else:
            eff_tl = max(0.0, option.cp_tl_seconds - (time.monotonic() - start))

        solver_cfg = CpsatSolverOptions(
            max_time_in_seconds=eff_tl,
            num_workers=option.solver_thread_cnt,
            log_search_progress=option.log_search_progress,
            log_to_stdout=False if option.log_search_progress else None,
            log_to_response=True if option.log_search_progress else None,
        )
        solver = get_solver(solver_cfg)

        value_recorder = ObjectiveValueRecorder()
        bound_recorder = ObjectiveBoundRecorder()
        solver.best_bound_callback = bound_recorder

        logger.info(
            "FlipMakespanCpDispatcher: instance=%s, delayed_makespan=%d, "
            "cp_horizon=%d, eff_tl=%s, num_workers=%d, log_search_progress=%s",
            instance.name,
            delayed_makespan,
            cp_horizon,
            f"{eff_tl:.3f}s" if eff_tl is not None else "None",
            option.solver_thread_cnt,
            option.log_search_progress,
        )

        status = solver.solve(mdl, solution_callback=value_recorder)
        status_name = solver.status_name(status)

        if option.log_search_progress and option.solver_log_path_getter is not None:
            try:
                solve_log = solver.response_proto.solve_log
                if solve_log:
                    log_path = option.solver_log_path_getter(
                        "_flip_makespan_cp_search.log"
                    )
                    with open(log_path, "w", encoding="utf-8") as fp:
                        fp.write(solve_log)
                        if not solve_log.endswith("\n"):
                            fp.write("\n")
            except Exception:
                logger.exception("Failed to write CP-SAT search log")

        # AlgRecord.progress_log records the problem objective (weighted E+T)
        # trajectory by contract; this dispatcher's CP-SAT minimises makespan,
        # so its solver trajectory cannot be poured into progress_log directly.
        # The makespan trajectory is kept available via _build_makespan_progress_log.
        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        if not has_solution:
            logger.warning(
                "FlipMakespanCpDispatcher: no feasible solution (status=%s)",
                status_name,
            )
            return AlgRecord(
                work_status=(
                    WorkStatus.INFEASIBLE
                    if status == cp_model.INFEASIBLE
                    else WorkStatus.ERROR
                ),
                instance_id=instance.name,
                algorithm_id=self.algorithm_id,
                option=option,
                result=AlgResult(
                    schedule=None,
                    obj_value=None,
                    obj_bound=None,
                    metrics={"cpsat_status": status_name},
                ),
                progress_log=(),
                termination_reason=(
                    TerminationReason.COMPLETED
                    if status == cp_model.INFEASIBLE
                    else TerminationReason.ERROR
                ),
                error=None
                if status == cp_model.INFEASIBLE
                else f"status={status_name}",
            )

        flipped_j_i_2_start = {
            (j, i): int(solver.value(op_vars.op_start[j, i]))
            for j in params.j_list
            for i in params.i_list
        }
        flipped_j_i_2_end = {
            (j, i): int(solver.value(op_vars.op_end[j, i]))
            for j in params.j_list
            for i in params.i_list
        }
        flipped_full = build_schedule_from_op_starts(
            reversed_instance, flipped_j_i_2_start, flipped_j_i_2_end
        )
        self._maybe_emit_phase(
            option, logger, "05_cp_solved", flipped_full, instance.name
        )
        unflipped = flipped_full.as_reversed()
        unflipped.make_semi_active(instance.stage_2_job_2_p_map)
        self._maybe_emit_phase(
            option,
            logger,
            "06_unflipped_semi_active",
            unflipped,
            instance.name,
            et_instance=instance,
        )
        unflipped.insert_idle_time(
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
        )
        self._maybe_emit_phase(
            option,
            logger,
            "07_unflipped_final",
            unflipped,
            instance.name,
            et_instance=instance,
        )

        sum_e, sum_t = compute_weighted_earliness_tardiness(unflipped, instance)
        obj_value = float(sum_e + sum_t)
        cp_makespan = float(solver.objective_value)
        cp_bound = float(solver.best_objective_bound)

        final_elapsed_sec = time.monotonic() - start
        progress_log: tuple[ProgressLogEntry, ...] = (
            ProgressLogEntry(
                elapsed_sec=final_elapsed_sec,
                obj_value=obj_value,
                obj_bound=None,
            ),
        )

        return AlgRecord(
            work_status=(
                WorkStatus.OPTIMAL
                if status == cp_model.OPTIMAL
                else WorkStatus.FEASIBLE
            ),
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=unflipped,
                obj_value=obj_value,
                obj_bound=None,
                metrics={
                    "cpsat_status": status_name,
                    "cpsat_makespan_obj": cp_makespan,
                    "cpsat_makespan_bound": cp_bound,
                    "delayed_makespan": float(delayed_makespan),
                    "cp_horizon": float(cp_horizon),
                    "sum_earliness": float(sum_e),
                    "sum_tardiness": float(sum_t),
                    "makespan": unflipped.makespan,
                },
            ),
            progress_log=progress_log,
            termination_reason=(
                TerminationReason.COMPLETED
                if status == cp_model.OPTIMAL
                else TerminationReason.TIME_LIMIT
            ),
        )

    @staticmethod
    def _build_flipped_seed(
        right_shifted: FFcSchedule,
        reversed_instance: FFcDDWParameters,
        *,
        flip_horizon: int,
    ) -> FFcSchedule:
        """Time-flip ``right_shifted`` onto ``reversed_instance``.

        Operation ``(stage, mc, job, s, e)`` becomes ``(stage, mc, job,
        flip_horizon - e, flip_horizon - s)``. Per-machine non-overlap is
        preserved under flipping.
        """
        seed = FFcSchedule(
            jobs=list(reversed_instance.job_id_list),
            stages=list(reversed_instance.stage_id_list),
            machines_per_stage={
                stage_id: list(reversed_instance.stage_2_machines_map[stage_id])
                for stage_id in reversed_instance.stage_id_list
            },
        )
        for stage_id in reversed_instance.stage_id_list:
            for mc_id, s, e, j in right_shifted.iter_operations_on_stage(stage_id):
                seed.add_ops_times_2_mc(
                    stage_id=stage_id,
                    mc_id=mc_id,
                    job_id=j,
                    start_time=flip_horizon - e,
                    end_time=flip_horizon - s,
                )
        return seed

    @staticmethod
    def _build_makespan_progress_log(
        *,
        value_recorder: ObjectiveValueRecorder,
        bound_recorder: ObjectiveBoundRecorder,
    ) -> tuple[ProgressLogEntry, ...]:
        """Build a (time, makespan, makespan-bound) trajectory.

        Currently unused; kept for future callers that want the makespan
        minimisation log. AlgRecord.progress_log is contracted to record
        the problem objective (weighted E+T), so makespan-scale entries
        cannot flow through it directly -- this helper is split out as a
        future hook.
        """
        entries: list[ProgressLogEntry] = []
        for t, vb in value_recorder.entries:
            entries.append(
                ProgressLogEntry(
                    elapsed_sec=t,
                    obj_value=float(vb.value),
                    obj_bound=float(vb.bound),
                )
            )
        value_t_set = {t for t, _ in value_recorder.entries}
        for t, b in bound_recorder.entries:
            if t in value_t_set:
                continue
            entries.append(
                ProgressLogEntry(
                    elapsed_sec=t,
                    obj_value=None,
                    obj_bound=float(b),
                )
            )
        entries.sort(key=lambda e: e.elapsed_sec)
        return tuple(entries)

    @staticmethod
    def _maybe_emit_phase(
        option: FlipMakespanCpOption,
        logger: logging.Logger,
        phase_name: str,
        schedule: FFcSchedule,
        instance_name: str,
        *,
        et_instance: FFcDDWParameters | None = None,
    ) -> None:
        """Dump ``schedule`` as compact JSON if phase emission is enabled.

        ``option.phase_schedule_path_getter(phase_name)`` returns the full
        destination path. Production callers should resolve this through
        ``ArtifactLayout.artifact_path("flip_makespan_cp_phase_schedule", ...)``
        so the reporter can ``find_artifacts`` them. Failures are logged and
        swallowed -- phase emission is diagnostic, not load-bearing.

        ``et_instance``: pass the original instance to embed
        ``obj_value`` (weighted ET on ``schedule``'s last stage). Pass
        ``None`` for reversed-instance phases where the "last stage"
        corresponds to the original *first* stage and has no due window.
        """
        if not option.emit_phase_schedules:
            return
        getter = option.phase_schedule_path_getter
        if getter is None:
            return
        obj_value: float | None = None
        if et_instance is not None:
            try:
                sum_e, sum_t = compute_weighted_earliness_tardiness(
                    schedule, et_instance
                )
                obj_value = float(sum_e + sum_t)
            except (KeyError, AttributeError):
                obj_value = None
        try:
            path = getter(phase_name)
            dump_solution_json(
                schedule,
                path,
                instance_name=instance_name,
                obj_value=obj_value,
                compact=True,
            )
        except Exception:
            logger.exception("Failed to emit phase schedule %s", phase_name)

    @staticmethod
    def _validate_instance(spec: AlgSpec) -> FFcDDWParameters:
        if not isinstance(spec.instance, FFcDDWParameters):
            raise TypeError(
                "FlipMakespanCpDispatcher requires FFcDDWParameters as spec.instance."
            )
        return spec.instance

    @staticmethod
    def _resolve_option(spec: AlgSpec) -> FlipMakespanCpOption:
        if spec.option is None:
            return FlipMakespanCpOption()
        if not isinstance(spec.option, FlipMakespanCpOption):
            raise TypeError(
                "FlipMakespanCpDispatcher requires FlipMakespanCpOption as spec.option."
            )
        return spec.option
