"""Sequence -> schedule decoder via reverse-direction MixedDispatcher.

Given a forward last-stage job permutation, build the dual schedule by:
  (1) ``FFcDDWParameters.reverse_stages(instance)``
  (2) ``MixedDispatcher.get_best_mixed_schedule_by_sequence`` on the
      reversed instance with the reversed permutation
  (3) ``FFcSchedule.as_reversed`` to map back to forward time
  (4) ``FFcSchedule.insert_idle_time`` to balance weighted E/T

Decoupled variant of the pattern in ``mcf_lb.phase3_dispatch.run_phase3``
with no Phase1/Phase2 dependency and no last-stage warm-start seed.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..parameters.ffc_ddw_params import FFcDDWParameters
from ..solution.ffc_schedule import FFcSchedule
from .dispatcher import MixedDispatcher

__all__ = ["decode_by_reverse_dispatch"]


def decode_by_reverse_dispatch(
    instance: FFcDDWParameters,
    job_sequence: Sequence[str],
    *,
    head_for_all_stages: bool = False,
    machine_then_job: bool = False,
    apply_idle_insertion: bool = True,
) -> FFcSchedule | None:
    """Decode a forward last-stage permutation via reverse dispatch.

    Returns ``None`` if the reversed ``MixedDispatcher`` fails to produce a
    schedule (mirrors ``phase3_dispatch.run_phase3``'s None-on-failure
    contract).
    """
    rev_instance = FFcDDWParameters.reverse_stages(instance)
    rev_seq = list(reversed(job_sequence))

    rev_sch = MixedDispatcher(rev_instance).get_best_mixed_schedule_by_sequence(
        rev_seq,
        criteria="makespan",
        head_for_all_stages=head_for_all_stages,
        machine_then_job=machine_then_job,
    )
    if rev_sch is None:
        return None

    fwd_sch = rev_sch.as_reversed()
    if apply_idle_insertion:
        fwd_sch.insert_idle_time(
            instance.job_2_due_window_map,
            instance.job_2_ewt_map,
            instance.job_2_twt_map,
        )
    return fwd_sch
