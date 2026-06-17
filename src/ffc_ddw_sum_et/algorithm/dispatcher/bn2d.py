"""BN2D (Bottleneck-based Two-Way Dispatching) dispatcher.

Ported from ``hybridflowshop/dispatcher/bn2d.py``. The upstream algorithm
targets minimum makespan, and this port preserves that internal objective:
candidate schedules are scored by ``FFcSchedule.makespan`` throughout. For
integration with the rest of ffc_ddw_sum_et, the final ``AlgRecord.obj_value``
is the weighted earliness+tardiness of the chosen schedule; makespan and the
weighted-ET components are recorded in ``AlgResult.metrics``.

The anchor-band post-processing path (``get_schedule_by_two_way_stage_band``)
from the upstream file is intentionally not ported — it depends on strict-order
dispatch helpers that ``FFcSchedule`` does not currently expose.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from typing import Mapping

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.ffc_schedule import FFcSchedule
from ...solution.objectives import compute_weighted_earliness_tardiness
from ..base.alg_option import AlgOption
from ..base.alg_record import AlgRecord, AlgResult, TerminationReason, WorkStatus
from ..base.alg_spec import AlgSpec
from .base import BaseDispatcher
from .mixed import MixedDispatcher
from .select_and_assign import solve_selection_problem
from .utils import (
    dispatch_job_sequence_by_stages,
    dispatch_stages_by_job_sequence,
    reverse_even_positions,
)

__all__ = ["BN2DDispatcher", "BN2DOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class BN2DOption(AlgOption):
    """Option payload for BN2DDispatcher."""

    left_cap_multiplier: int | None = None
    right_cap_multiplier: int | None = None
    left_cap_portion: float | None = None
    right_cap_portion: float | None = None
    normalize_by_stage_cnt: bool = False
    randomize_mid_all: bool = False
    reverse_mid_even: bool = False
    reverse_mid_all: bool = False
    mixed_schedule_for_former_stages: bool = False
    mixed_schedule_for_later_stages: bool = False
    machine_then_job: bool = False
    all_stages_as_bottleneck: bool = False
    random_seed: int | None = None
    solver_thread_cnt: int = 1
    iit_after_dispatch: bool = False


class BN2DDispatcher:
    """Bottleneck-based Two-Way Dispatching decoder.

    The bottleneck stage is scheduled first with a priority that separates jobs
    into left-cap / mid / right-cap groups; later stages are dispatched forward
    from the bottleneck completion times, and former stages are dispatched on a
    reversed sub-instance with release times derived from the bottleneck start
    times.
    """

    algorithm_id = "bn2d"

    def run(self, spec: AlgSpec) -> AlgRecord:
        instance = self._validate_instance(spec)
        option = self._resolve_option(spec)
        if spec.ref_solution is not None:
            raise NotImplementedError(
                "BN2DDispatcher does not support ref_solution yet."
            )

        rng = random.Random(option.random_seed)
        base = BaseDispatcher(instance, logger=spec.logger)
        mixed = MixedDispatcher(instance, logger=spec.logger)

        if option.all_stages_as_bottleneck:
            best_sch = self._run_all_stages(base, mixed, option, rng, spec)
        else:
            bottleneck_stage_id = self._get_bottleneck_stage(base)
            best_sch = self._get_schedule_from_bottleneck_stage(
                base, mixed, bottleneck_stage_id, option, rng, spec
            )
        if option.iit_after_dispatch:
            best_sch.insert_idle_time(
                instance.job_2_due_window_map,
                instance.job_2_ewt_map,
                instance.job_2_twt_map,
            )

        sum_e, sum_t = compute_weighted_earliness_tardiness(best_sch, instance)
        obj_value = sum_e + sum_t

        self._debug(
            spec,
            "Completed BN2D decode for instance=%s obj_value=%s makespan=%s",
            instance.name,
            obj_value,
            best_sch.makespan,
        )

        return AlgRecord(
            work_status=WorkStatus.FEASIBLE,
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=best_sch,
                obj_value=obj_value,
                obj_bound=None,
                metrics={
                    "sum_earliness": sum_e,
                    "sum_tardiness": sum_t,
                    "makespan": best_sch.makespan,
                },
            ),
            termination_reason=TerminationReason.COMPLETED,
        )

    # ---- anchored full-schedule entry point ----

    def get_full_schedule_from_anchor(
        self,
        instance: FFcDDWParameters,
        anchor_schedule: FFcSchedule,
        anchor_stage_id: str,
        *,
        option: BN2DOption | None = None,
        logger: logging.Logger | None = None,
    ) -> FFcSchedule:
        """Build a full schedule from a fixed stage-``anchor_stage_id`` schedule.

        ``anchor_schedule`` is treated as the fixed schedule on
        ``anchor_stage_id``; ``anchor_cmax`` is the maximum end time of
        ``anchor_schedule`` on that stage. Later stages are dispatched forward
        and former stages on a reversed sub-instance (right-shifted to fit) using
        exactly the same two-way extension as the bottleneck path.
        """
        if option is None:
            option = BN2DOption()
        base = BaseDispatcher(instance, logger=logger)
        mixed = MixedDispatcher(instance, logger=logger)
        spec = AlgSpec(instance=instance, logger=logger)

        anchor_end_time_map = self._get_job_2_end_time_map(
            anchor_schedule, anchor_stage_id
        )
        if not anchor_end_time_map:
            raise ValueError(
                f"anchor_schedule has no operations on stage {anchor_stage_id!r}."
            )
        anchor_cmax = max(anchor_end_time_map.values())

        return self._extend_full_schedule_from_anchor(
            base,
            mixed,
            anchor_stage_id,
            anchor_schedule,
            anchor_cmax,
            option,
            spec,
        )

    # ---- spec validation ----

    def _validate_instance(self, spec: AlgSpec) -> FFcDDWParameters:
        if not isinstance(spec.instance, FFcDDWParameters):
            raise TypeError(
                "BN2DDispatcher requires FFcDDWParameters as spec.instance."
            )
        return spec.instance

    def _resolve_option(self, spec: AlgSpec) -> BN2DOption:
        if spec.option is None:
            return BN2DOption()
        if not isinstance(spec.option, BN2DOption):
            raise TypeError("BN2DDispatcher requires BN2DOption as spec.option.")
        return spec.option

    # ---- orchestration ----

    def _run_all_stages(
        self,
        base: BaseDispatcher,
        mixed: MixedDispatcher,
        option: BN2DOption,
        rng: random.Random,
        spec: AlgSpec,
    ) -> FFcSchedule:
        best_cmax: int | None = None
        best_sch: FFcSchedule | None = None
        for bottleneck_stage_id in base.stage_id_list:
            sch = self._get_schedule_from_bottleneck_stage(
                base, mixed, bottleneck_stage_id, option, rng, spec
            )
            cmax = sch.makespan
            if best_cmax is None or cmax < best_cmax:
                best_cmax = cmax
                best_sch = sch
        if best_sch is None:
            raise RuntimeError("BN2D produced no feasible schedule.")
        return best_sch

    def _get_bottleneck_stage(self, base: BaseDispatcher) -> str:
        stage_id_2_total_p: dict[str, int] = {}
        for stage_id in base.stage_id_list:
            stage_id_2_total_p[stage_id] = sum(
                base.stage_2_job_2_p[stage_id][job_id] for job_id in base.job_id_list
            )
        stage_id_2_bottleneck_index: dict[str, float] = {
            stage_id: total_p / len(base.machines_per_stage[stage_id])
            for stage_id, total_p in stage_id_2_total_p.items()
        }
        return max(
            stage_id_2_bottleneck_index, key=lambda s: stage_id_2_bottleneck_index[s]
        )

    def _get_schedule_from_bottleneck_stage(
        self,
        base: BaseDispatcher,
        mixed: MixedDispatcher,
        bottleneck_stage_id: str,
        option: BN2DOption,
        rng: random.Random,
        spec: AlgSpec,
    ) -> FFcSchedule:
        bottleneck_schedule, bcmax = self._get_bottleneck_stage_schedule_heuristic(
            base, bottleneck_stage_id, option, rng, spec
        )

        return self._extend_full_schedule_from_anchor(
            base,
            mixed,
            bottleneck_stage_id,
            bottleneck_schedule,
            bcmax,
            option,
            spec,
        )

    def _extend_full_schedule_from_anchor(
        self,
        base: BaseDispatcher,
        mixed: MixedDispatcher,
        anchor_stage_id: str,
        anchor_schedule: FFcSchedule,
        anchor_cmax: int,
        option: BN2DOption,
        spec: AlgSpec,
    ) -> FFcSchedule:
        """Extend a stage-anchored schedule into a full schedule.

        Treats ``anchor_schedule`` as the fixed schedule on ``anchor_stage_id``
        (with ``anchor_cmax`` its completion time on that stage). Later stages
        are dispatched forward from the anchor end times; former stages are
        dispatched on a reversed sub-instance and right-shifted to fit. This is
        the two-way extension formerly inlined in
        ``_get_schedule_from_bottleneck_stage`` and is therefore byte-identical
        for the bottleneck path.
        """
        anchor_stage_index = base.stage_id_list.index(anchor_stage_id)
        later_stage_list = base.stage_id_list[anchor_stage_index + 1 :]
        if later_stage_list:
            self._debug(spec, "Later stages: %s", later_stage_list)
            job_2_bottleneck_end_time = self._get_job_2_end_time_map(
                anchor_schedule, anchor_stage_id
            )
            sorted_j_list = sorted(
                base.job_id_list,
                key=lambda j: (
                    job_2_bottleneck_end_time.get(j, 0),
                    base._get_rank_tiebreak_key(j),
                ),
            )

            if option.mixed_schedule_for_later_stages:
                schedule = mixed.get_best_mixed_schedule_by_sequence(
                    sorted_j_list,
                    schedule=anchor_schedule.deepcopy(),
                    from_stage=later_stage_list[0],
                    job_2_release_t=job_2_bottleneck_end_time,
                    machine_then_job=option.machine_then_job,
                    criteria="makespan",
                )
                if schedule is None:
                    raise ValueError("Failed to get mixed schedule for later stages.")
            else:
                later_ds_schedule = anchor_schedule.deepcopy()
                for stage_id in later_stage_list:
                    later_ds_schedule.dispatch_stage_by_jobs(
                        stage_id,
                        sorted_j_list,
                        base.stage_2_job_2_p[stage_id],
                    )

                later_dj_schedule = anchor_schedule.deepcopy()
                for job_id in sorted_j_list:
                    later_dj_schedule.dispatch_job_by_stages(
                        job_id,
                        base.job_2_stage_2_p[job_id],
                        from_stage=later_stage_list[0],
                    )

                if later_ds_schedule.makespan <= later_dj_schedule.makespan:
                    schedule = later_ds_schedule
                else:
                    schedule = later_dj_schedule
        else:
            schedule = anchor_schedule.deepcopy()

        before_stage_list = base.stage_id_list[:anchor_stage_index]
        if before_stage_list:
            self._debug(spec, "Before stages: %s", before_stage_list)
            job_2_bottleneck_start_time = self._get_job_2_start_time_map(
                anchor_schedule, anchor_stage_id
            )
            instance_for_former_stages, job_2_release_t = (
                self._create_reversed_instance_for_former_stages(
                    base, before_stage_list, job_2_bottleneck_start_time, anchor_cmax
                )
            )
            former_schedule = self._dispatch_former_stages(
                base,
                instance_for_former_stages,
                job_2_release_t,
                get_mixed_schedule=option.mixed_schedule_for_former_stages,
                machine_then_job=option.machine_then_job,
            )

            former_schedule_makespan = former_schedule.makespan
            discrepancy = former_schedule_makespan - anchor_cmax
            self._debug(
                spec,
                "Former stages makespan: %s, discrepancy with bottleneck: %s",
                former_schedule_makespan,
                discrepancy,
            )

            if discrepancy > 0:
                schedule.right_shift(discrepancy)

            former_schedule_end_time_map = former_schedule.get_jik_2_end_time_map()
            for op, end_time in former_schedule_end_time_map.items():
                job_id, stage_id, mc_id = op
                start_time = former_schedule_makespan - end_time
                duration = base.job_2_stage_2_p[job_id][stage_id]
                schedule.add_ops_times_2_mc(
                    stage_id, mc_id, job_id, start_time, start_time + duration
                )

        return schedule

    # ---- bottleneck stage heuristic ----

    def _get_bottleneck_stage_schedule_heuristic(
        self,
        base: BaseDispatcher,
        bottleneck_stage_id: str,
        option: BN2DOption,
        rng: random.Random,
        spec: AlgSpec,
    ) -> tuple[FFcSchedule, int]:
        instance = base.instance
        job_2_stage_2_p = base.job_2_stage_2_p
        stage_2_job_2_p = base.stage_2_job_2_p

        bottleneck_stage_index = base.stage_id_list.index(bottleneck_stage_id)
        before_stage_id_list = base.stage_id_list[:bottleneck_stage_index]
        after_stage_id_list = base.stage_id_list[bottleneck_stage_index + 1 :]
        before_stage_cnt = len(before_stage_id_list)
        after_stage_cnt = len(after_stage_id_list)

        r_dict: dict[str, int] = {
            j: sum(job_2_stage_2_p[j][s] for s in before_stage_id_list)
            for j in base.job_id_list
        }
        if option.normalize_by_stage_cnt and before_stage_cnt > 0:
            r_dict = {j: math.ceil(r / before_stage_cnt) for j, r in r_dict.items()}

        p_dict: dict[str, int] = stage_2_job_2_p[bottleneck_stage_id]

        tr_dict: dict[str, int] = {
            j: sum(job_2_stage_2_p[j][s] for s in after_stage_id_list)
            for j in base.job_id_list
        }
        if option.normalize_by_stage_cnt and after_stage_cnt > 0:
            tr_dict = {j: math.ceil(tr / after_stage_cnt) for j, tr in tr_dict.items()}

        machine_cnt = len(instance.stage_2_machines_map[bottleneck_stage_id])
        job_cnt = instance.job_count

        left_cap_op_cnt = 0
        if option.left_cap_multiplier is not None:
            left_cap_op_cnt = option.left_cap_multiplier * machine_cnt
        elif option.left_cap_portion is not None:
            left_cap_op_cnt = int(option.left_cap_portion * job_cnt)

        right_cap_op_cnt = 0
        if option.right_cap_multiplier is not None:
            right_cap_op_cnt = option.right_cap_multiplier * machine_cnt
        elif option.right_cap_portion is not None:
            right_cap_op_cnt = int(option.right_cap_portion * job_cnt)

        left_cap_job_id_list: list[str] = []
        right_cap_job_id_list: list[str] = []

        if left_cap_op_cnt > 0 or right_cap_op_cnt > 0:
            result = solve_selection_problem(
                jobs=base.job_id_list,
                r=r_dict,
                t=tr_dict,
                K_L=left_cap_op_cnt,
                K_R=right_cap_op_cnt,
                solver_thread_cnt=option.solver_thread_cnt,
            )

            if result["status"] in ("OPTIMAL", "FEASIBLE"):
                left_cap_job_id_list = list(result["L_set"])
                left_cap_job_id_list.sort(
                    key=lambda j: (r_dict[j], base._get_rank_tiebreak_key(j))
                )
                right_cap_job_id_list = list(result["R_set"])
                right_cap_job_id_list.sort(
                    key=lambda j: (-tr_dict[j], base._get_rank_tiebreak_key(j))
                )
            else:
                if left_cap_op_cnt > 0:
                    sorted_by_r = sorted(
                        r_dict.items(),
                        key=lambda x: (x[1], base._get_rank_tiebreak_key(x[0])),
                    )
                    left_cap_job_id_list = [j for j, _ in sorted_by_r[:left_cap_op_cnt]]
                if right_cap_op_cnt > 0:
                    sorted_by_tr = sorted(
                        tr_dict.items(),
                        key=lambda x: (x[1], base._get_rank_tiebreak_key(x[0])),
                    )
                    sorted_by_tr = [
                        (j, t) for j, t in sorted_by_tr if j not in left_cap_job_id_list
                    ]
                    right_cap_job_id_list = [
                        j for j, _ in sorted_by_tr[:right_cap_op_cnt]
                    ]

            for j in left_cap_job_id_list:
                self._debug(
                    spec,
                    "Left cap job %s: r=%s, p=%s, tr=%s",
                    j,
                    r_dict[j],
                    p_dict[j],
                    tr_dict[j],
                )
            for j in right_cap_job_id_list:
                self._debug(
                    spec,
                    "Right cap job %s: r=%s, p=%s, tr=%s",
                    j,
                    r_dict[j],
                    p_dict[j],
                    tr_dict[j],
                )

        mid_job_id_list = [
            j
            for j in base.job_id_list
            if j not in left_cap_job_id_list and j not in right_cap_job_id_list
        ]

        if option.randomize_mid_all:
            rng.shuffle(mid_job_id_list)
        else:
            mid_job_id_list.sort(
                key=lambda j: (r_dict[j] - tr_dict[j], base._get_rank_tiebreak_key(j))
            )
            if option.reverse_mid_even:
                reverse_even_positions(mid_job_id_list, in_place=True)
            elif option.reverse_mid_all:
                mid_job_id_list.reverse()

        sorted_j_list = left_cap_job_id_list + mid_job_id_list + right_cap_job_id_list

        dispatched_schedule = base._create_empty_schedule()
        dispatched_schedule.dispatch_stage_by_jobs(
            bottleneck_stage_id,
            sorted_j_list,
            p_dict,
            job_2_release=r_dict,
        )

        end_time_dict = dispatched_schedule.get_jik_2_end_time_map()
        partial_mk = 0
        for op, end_time in end_time_dict.items():
            last_stage_completion = end_time + tr_dict[op[0]]
            if last_stage_completion > partial_mk:
                partial_mk = last_stage_completion

        self._debug(spec, "Bottleneck parallel MC: partial_obj=%s", partial_mk)
        return dispatched_schedule, partial_mk

    # ---- former-stages dispatch on reversed sub-instance ----

    def _create_reversed_instance_for_former_stages(
        self,
        base: BaseDispatcher,
        before_stage_list: list[str],
        job_2_bottleneck_start_time: Mapping[str, int],
        bcmax: int,
    ) -> tuple[FFcDDWParameters, dict[str, int]]:
        reversed_instance = FFcDDWParameters.create_instance_of_stage_subset(
            base.instance,
            set(before_stage_list),
            reverse_stage_seq=True,
        )
        job_2_release_t: dict[str, int] = {
            j: bcmax - job_2_bottleneck_start_time[j] for j in base.job_id_list
        }
        return reversed_instance, job_2_release_t

    def _dispatch_former_stages(
        self,
        base: BaseDispatcher,
        instance_for_former_stages: FFcDDWParameters,
        job_2_release_t: dict[str, int],
        get_mixed_schedule: bool = False,
        machine_then_job: bool = False,
    ) -> FFcSchedule:
        sorted_j_list = sorted(
            instance_for_former_stages.job_id_list,
            key=lambda j: (job_2_release_t[j], base._get_rank_tiebreak_key(j)),
        )

        if get_mixed_schedule:
            sub_dispatcher = MixedDispatcher(
                instance_for_former_stages,
                logger=base.logger,
                job_tiebreak_rank=base.job_tiebreak_rank,
            )
            schedule = sub_dispatcher.get_best_mixed_schedule_by_sequence(
                sorted_j_list,
                job_2_release_t=job_2_release_t,
                machine_then_job=machine_then_job,
                criteria="makespan",
            )
            if schedule is None:
                raise ValueError("Failed to get schedule for former stages.")
            return schedule

        former_stage_list = list(instance_for_former_stages.stage_id_list)
        stage_2_job_2_p_sub = {
            stage_id: {j: base.job_2_stage_2_p[j][stage_id] for j in sorted_j_list}
            for stage_id in former_stage_list
        }
        job_2_stage_2_p_sub = {
            j: {
                stage_id: base.job_2_stage_2_p[j][stage_id]
                for stage_id in former_stage_list
            }
            for j in sorted_j_list
        }

        ds_schedule = base._create_empty_schedule(instance=instance_for_former_stages)
        dispatch_stages_by_job_sequence(
            ds_schedule,
            sorted_j_list,
            stage_2_job_2_p_sub,
            job_2_release_t=job_2_release_t,
        )

        dj_schedule = base._create_empty_schedule(instance=instance_for_former_stages)
        dispatch_job_sequence_by_stages(
            dj_schedule,
            sorted_j_list,
            job_2_stage_2_p_sub,
            job_2_release_t=job_2_release_t,
        )

        if ds_schedule.makespan < dj_schedule.makespan:
            return ds_schedule
        return dj_schedule

    # ---- helpers ----

    def _get_job_2_start_time_map(
        self, schedule: FFcSchedule, stage_id: str
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        for (job_id, sid, _), start_time in schedule.get_jik_2_start_time_map().items():
            if sid == stage_id:
                result[job_id] = start_time
        return result

    def _get_job_2_end_time_map(
        self, schedule: FFcSchedule, stage_id: str
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        for (job_id, sid, _), end_time in schedule.get_jik_2_end_time_map().items():
            if sid == stage_id:
                result[job_id] = end_time
        return result

    def _debug(self, spec: AlgSpec, msg: str, *args: object) -> None:
        if spec.logger is not None:
            spec.logger.debug(msg, *args)
        else:
            logging.debug(msg, *args)
