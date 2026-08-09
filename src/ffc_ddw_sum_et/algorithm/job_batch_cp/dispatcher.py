"""JobBatchCpDispatcher: sweep destroy-repair over ordered job batches.

Keeps the incumbent intact, unfixes one batch of jobs at a time
(profile-fixes the rest), lets CP-SAT re-insert the batch, and accepts
only strict improvements.  Every job is destroyed exactly once per pass.
"""

from __future__ import annotations

import logging
import math
import time

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..base.alg_record import (
    AlgRecord,
    AlgResult,
    ProgressLogEntry,
    TerminationReason,
    WorkStatus,
)
from ..base.alg_spec import AlgSpec
from ..job_contrib_cp import JobContribCpDispatcher, JobContribCpOption
from ..step_tl_resolver import resolve_per_step_tl
from .option import JobBatchCpOption
from .step_log import JobBatchCpStepEntry

__all__ = ["JobBatchCpDispatcher"]

_OBJ_IMPROVEMENT_TOLERANCE = 1e-6


class JobBatchCpDispatcher:
    """Sweep destroy-repair over job batches defined by a job sequence."""

    algorithm_id = "job_batch_cp"

    def run(self, spec: AlgSpec) -> AlgRecord:
        instance = self._validate_instance(spec)
        option = self._resolve_option(spec)
        logger = spec.logger or logging.getLogger(__name__)

        if spec.ref_solution is None:
            raise RuntimeError(
                "job_batch_cp requires an incumbent schedule; chain it after a "
                "seeding subroutine such as calc_mcf_lb_and_derive_full_sch."
            )

        n = instance.job_count
        expected = set(instance.job_id_list)
        provided = set(option.job_sequence)
        if len(option.job_sequence) != len(expected) or provided != expected:
            missing = sorted(expected - provided)
            extra = sorted(provided - expected)
            raise ValueError(
                "JobBatchCpOption.job_sequence must be a permutation of "
                f"instance.job_id_list ({len(expected)} jobs); "
                f"got {len(option.job_sequence)} entries (missing={missing[:5]}, "
                f"extra={extra[:5]})."
            )

        if option.num_batches is not None:
            batch_size = math.ceil(n / option.num_batches)
        else:
            batch_size = option.batch_size

        job_sequence = list(option.job_sequence)
        batches: list[list[str]] = [
            job_sequence[i : i + batch_size]
            for i in range(0, len(job_sequence), batch_size)
        ]

        per_batch_tl = resolve_per_step_tl(
            cp_tl_from_arg=option.cp_tl_seconds,
            total_seconds=option.total_timelimit_seconds,
            num_batches=option.num_batches,
            batch_count=len(batches),
            batch_tl_mode=option.batch_tl_mode,
            batch_tl_offset_seconds=option.batch_tl_offset_seconds,
            logger=logger,
        )

        current = spec.ref_solution
        current_obj = compute_weighted_earliness_tardiness(
            current, instance, time_factor=option.time_factor
        )
        current_obj = float(current_obj[0] + current_obj[1])

        loop_start = time.monotonic()
        step_entries: list[JobBatchCpStepEntry] = []
        progress_entries: list[ProgressLogEntry] = []
        stopped_early = False

        for step_idx, batch in enumerate(batches):
            now = time.monotonic()
            remaining = (
                option.wall_clock_deadline_sec - now
                if option.wall_clock_deadline_sec is not None
                else None
            )
            if remaining is not None and remaining <= 0.0:
                logger.info(
                    "job_batch_cp: wall-clock deadline exceeded before batch %d/%d",
                    step_idx + 1,
                    len(batches),
                )
                stopped_early = True
                break

            if spec.stop_predicate is not None and spec.stop_predicate():
                logger.info(
                    "job_batch_cp: stop requested before batch %d/%d",
                    step_idx + 1,
                    len(batches),
                )
                stopped_early = True
                break

            # "proportional" is delegated: resolve_per_step_tl returns None for
            # it by contract, and the sub-dispatcher derives
            # ``destroyed_op_tl_multiplier * |batch| * c`` itself — which is
            # what makes the limit track a growing batch_size across passes and
            # gives the short trailing batch proportionally less. Passing a
            # constant ``cp_tl_seconds`` alongside would win the sub-dispatcher's
            # min() and defeat that, so the two are mutually exclusive here.
            proportional = option.batch_tl_mode == "proportional"
            sub_cp_tl: float | None = None
            if per_batch_tl is not None:
                sub_cp_tl = per_batch_tl[step_idx]
                if remaining is not None:
                    sub_cp_tl = min(sub_cp_tl, remaining)

            sub_option = JobContribCpOption(
                destroy_job_ids=tuple(batch),
                pf_method=option.pf_method,
                horizon_multiplier=option.horizon_multiplier,
                cp_tl_seconds=None if proportional else sub_cp_tl,
                cp_tl_mode="proportional" if proportional else "constant",
                destroyed_op_tl_multiplier=option.destroyed_op_tl_multiplier,
                wall_clock_deadline_sec=option.wall_clock_deadline_sec,
                solver_thread_cnt=option.solver_thread_cnt,
                time_factor=option.time_factor,
                error_if_infeasible=option.error_if_infeasible,
                log_search_progress=False,
            )
            sub_spec = AlgSpec(
                instance=instance,
                option=sub_option,
                ref_solution=current,
                logger=logger,
                stop_predicate=spec.stop_predicate,
            )

            # MODEL_INVALID and error_if_infeasible are deliberate bug signals
            # from the sub-dispatcher; swallowing them here would hide a broken
            # model behind a silently unimproved incumbent. Let them propagate.
            batch_start_offset = time.monotonic() - loop_start
            rec = JobContribCpDispatcher().run(sub_spec)
            elapsed_since_loop = time.monotonic() - loop_start

            batch_obj_after_raw = (
                float(rec.result.obj_value)
                if rec.result is not None and rec.result.obj_value is not None
                else current_obj
            )
            candidate = rec.result.schedule if rec.result is not None else None
            cpsat_status: str | None = None
            setup_seconds: float | None = None
            makespan: int = int(current.makespan)
            # In proportional mode the sweep has no limit of its own to log;
            # the sub-dispatcher reports the one it derived and applied.
            tl_for_log: float | None = sub_cp_tl
            if rec.result is not None:
                if rec.result.metrics is not None:
                    cpsat_status = rec.result.metrics.get("cpsat_status")
                    setup_seconds = rec.result.metrics.get("setup_seconds")
                    if tl_for_log is None:
                        tl_for_log = rec.result.metrics.get("cp_tl_seconds")
                if candidate is not None:
                    makespan = int(candidate.makespan)

            # A schedule is required to accept: taking the objective without the
            # schedule it belongs to would leave ``current_obj`` describing a
            # solution the sweep never holds, and every later batch would be
            # compared against a baseline the incumbent cannot match.
            obj_before = current_obj
            accepted = False
            if (
                candidate is not None
                and batch_obj_after_raw < current_obj - _OBJ_IMPROVEMENT_TOLERANCE
            ):
                accepted = True
                current = candidate
                current_obj = batch_obj_after_raw

            entry = JobBatchCpStepEntry(
                step=step_idx,
                batch_size=len(batch),
                batch_head=batch[0],
                elapsed_time=elapsed_since_loop,
                TL=tl_for_log,
                elapsed_portion=(
                    elapsed_since_loop / option.total_timelimit_seconds
                    if option.total_timelimit_seconds is not None
                    else None
                ),
                obj_before=obj_before,
                obj_after=batch_obj_after_raw,
                accepted=accepted,
                cpsat_status=cpsat_status,
                setup_seconds=setup_seconds,
                makespan=makespan,
            )
            step_entries.append(entry)

            # Sub-record timestamps are in the batch's own frame; rebase them
            # onto the loop frame with the batch *start* offset (same
            # convention as NehCpDispatcher). Offsetting by the batch end
            # would push points past the run's own final entry.
            if rec.progress_log:
                for pe in rec.progress_log:
                    progress_entries.append(
                        ProgressLogEntry(
                            elapsed_sec=pe.elapsed_sec + batch_start_offset,
                            obj_value=pe.obj_value,
                            obj_bound=pe.obj_bound,
                        )
                    )

            logger.info(
                "job_batch_cp: batch %d/%d head=%s size=%d "
                "obj_before=%.1f obj_after=%.1f accepted=%s",
                step_idx + 1,
                len(batches),
                batch[0],
                len(batch),
                obj_before,
                batch_obj_after_raw,
                accepted,
            )

        total_elapsed = time.monotonic() - loop_start

        final_obj = compute_weighted_earliness_tardiness(
            current, instance, time_factor=option.time_factor
        )
        final_obj_val = float(final_obj[0] + final_obj[1])

        progress_entries.append(
            ProgressLogEntry(
                elapsed_sec=total_elapsed,
                obj_value=final_obj_val,
                obj_bound=None,
            )
        )

        return AlgRecord(
            work_status=WorkStatus.FEASIBLE,
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=current,
                obj_value=final_obj_val,
                obj_bound=None,
                metrics={
                    "batch_size": batch_size,
                    "batch_count": len(batches),
                    "stopped_early": stopped_early,
                    "completed_batches": len(step_entries),
                    "step_log": tuple(step_entries),
                    "makespan": int(current.makespan),
                },
            ),
            progress_log=tuple(progress_entries),
            termination_reason=(
                TerminationReason.STOP_REQUESTED
                if stopped_early
                else TerminationReason.COMPLETED
            ),
        )

    @staticmethod
    def _validate_instance(spec: AlgSpec) -> FFcDDWParameters:
        if not isinstance(spec.instance, FFcDDWParameters):
            raise TypeError(
                "JobBatchCpDispatcher requires FFcDDWParameters as spec.instance."
            )
        return spec.instance

    @staticmethod
    def _resolve_option(spec: AlgSpec) -> JobBatchCpOption:
        if spec.option is None:
            raise ValueError("JobBatchCpDispatcher requires a JobBatchCpOption.")
        if not isinstance(spec.option, JobBatchCpOption):
            raise TypeError(
                "JobBatchCpDispatcher requires JobBatchCpOption as spec.option."
            )
        return spec.option
