"""Option payload for the MCF-LB pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from ..base.alg_option import AlgOption
from ..cumulative import PFMethod

__all__ = ["MCFLBOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MCFLBOption(AlgOption):
    """Option payload for ``MCFLB``.

    - ``last_stage_only_pf_method`` selects the profile-fix precedence policy
      for the Phase 2 last-stage-only solve; ``full_pf_method`` does the same
      for the Phase 4 full solve. ``None`` skips the precedence-arc pass
      while keeping warm-start / ET hints. See :data:`PFMethod`.
    """

    last_stage_only_pf_method: PFMethod | None = None
    full_pf_method: PFMethod | None = None
