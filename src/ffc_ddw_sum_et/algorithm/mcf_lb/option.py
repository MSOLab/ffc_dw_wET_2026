"""Option payload for the MCF-LB pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from ..base.alg_option import AlgOption

__all__ = ["MCFLBOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MCFLBOption(AlgOption):
    """Option payload for ``MCFLB``.

    - ``last_stage_only_timelimit`` applies to the Phase 2 last-stage-only
      CP-SAT solver. Accepts either seconds as ``float``/``int`` or a
      ``"<x>nc"`` string parsed as ``float(x) * n * c`` seconds. ``None``
      leaves the solver unbounded.
    - ``profile_fix_by_machine`` / ``machine_precedence_stride`` are passed
      through to
      ``BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule``
      during the Phase 4 profile-fix full solve.
    """

    last_stage_only_timelimit: float | str | None = None
    profile_fix_by_machine: bool = False
    machine_precedence_stride: int = 1
