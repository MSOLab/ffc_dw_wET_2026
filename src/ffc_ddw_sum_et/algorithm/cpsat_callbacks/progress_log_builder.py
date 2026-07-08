"""Shared helper to merge CP-SAT solution and bound callbacks into a progress log.

Both ``CpsatAdapter`` and ``CoarsenSolveReconstructAdapter`` attach the same two
recorder types and apply the same merge rule.  Extracting it here (DRY) keeps
the rule in one place.
"""

from __future__ import annotations

from ..base.alg_record import ProgressLogEntry
from .obj_bound_recorder import ObjectiveBoundRecorder
from .obj_value_recorder import ObjectiveValueRecorder

__all__ = ["build_progress_log"]


def build_progress_log(
    *,
    value_recorder: ObjectiveValueRecorder,
    bound_recorder: ObjectiveBoundRecorder,
) -> tuple[ProgressLogEntry, ...]:
    """Merge solution-callback and best-bound-callback entries into a single
    ``progress_log`` time series.

    - Solution callback fires at solve-time t with both ``v`` and ``b``.
    - Best-bound callback fires at solve-time t with ``b`` only.
    - When the two callbacks fire at the same timestamp, the solution
      callback wins (carries v in addition to b).
    Entries are sorted by ``elapsed_sec`` ascending.
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
