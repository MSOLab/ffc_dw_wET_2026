"""FFc-DDW SubroutineReport subclass.

Extends routix's ``SubroutineReport`` with controller-frame timing and
algorithm-frame trajectory needed to emit a per-instance ``_obj_log.json``
combining all step trajectories into a single global timeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from routix.report import SubroutineReport

from ..algorithm.base.alg_record import ProgressLogEntry


@dataclass(frozen=True)
class FFcDDWSubroutineReport(SubroutineReport):
    """
    SubroutineReport extended with controller-frame timing context and
    algorithm-frame trajectory.
    """

    start_time: float = 0.0
    """
    controller-frame elapsed seconds at step entry.
    The end-of-run aggregator uses this to globalize ``progress_log``
    timestamps. Algorithm code never reads this — it lives only on
    the controller side.
    """

    progress_log: tuple[ProgressLogEntry, ...] = field(default_factory=tuple)
    """
    algorithm-frame trajectory propagated from ``AlgRecord.progress_log``.
    Empty for steps that don't capture intra-step trajectories.
    """

    step_label: str | None = None
    """
    ``_get_call_context_of_current_method()`` value at register time
    (e.g. ``"7-solve_base_model_cpsat"``). Stamped onto the last timestamp of
    this step in the aggregated yaml.
    """
