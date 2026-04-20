"""Option payload for the MCF-LB pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from ..base.alg_option import AlgOption

__all__ = ["MCFLBOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MCFLBOption(AlgOption):
    """Option payload for ``MCFLB``.

    - ``profile_fix_by_machine`` / ``machine_precedence_stride`` are passed
      through to
      ``BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule``
      in both the Phase 2 last-stage-only solve and the Phase 4
      profile-fix full solve.
    """

    profile_fix_by_machine: bool = False
    machine_precedence_stride: int = 1
