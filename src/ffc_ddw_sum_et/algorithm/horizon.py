"""Horizon estimators shared between MCF and CP-SAT layers.

The :func:`compute_parallel_mc_horizon` formula matches the legacy
``ParallelMachinePreemptionMcf._define_parameters`` ``t_max`` and is
re-used by the flip-makespan CP dispatcher to size its CP-SAT horizon.
"""

from __future__ import annotations

import math
from typing import Mapping

__all__ = ["compute_parallel_mc_horizon"]


def compute_parallel_mc_horizon(
    p: Mapping[str, int],
    r: Mapping[str, int],
    mc_count: int,
    d_lower: Mapping[str, int] | None = None,
) -> int:
    """Parallel-machine completion-time upper bound.

    ``T = max_j(max(r_j, d^-_j - p_j)) + ceil(sum(p_j) / mc_count)``

    The ``d_lower`` term is dropped from the inner ``max`` when
    ``d_lower`` is ``None`` (pure makespan use case — there is no lower
    due-window pressure to consider).

    Args:
        p: Per-job processing time on the stage being bounded.
        r: Per-job release time at the stage.
        mc_count: Machine count at the stage.
        d_lower: Per-job lower due-window bound. ``None`` skips the
            ``d^-_j - p_j`` term in the inner ``max``.

    Returns:
        Integer horizon. Always ``>= max_j(r_j)`` and
        ``>= ceil(sum(p_j) / mc_count)``.

    Raises:
        ValueError: ``p`` is empty or ``mc_count <= 0``.
    """
    if not p:
        raise ValueError("compute_parallel_mc_horizon requires non-empty p map.")
    if mc_count <= 0:
        raise ValueError(
            f"compute_parallel_mc_horizon requires mc_count > 0; got {mc_count}."
        )

    if d_lower is None:
        max_inner = max(r[j] for j in p)
    else:
        max_inner = max(max(r[j], d_lower[j] - p[j]) for j in p)
    p_sum = sum(p.values())
    return max_inner + math.ceil(p_sum / mc_count)
