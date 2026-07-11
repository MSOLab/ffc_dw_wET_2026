"""Option payload for the MCF-LB pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from ..base.alg_option import AlgOption
from ..cumulative import PFMethod

__all__ = ["MCFLBOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MCFLBOption(AlgOption):
    """Option payload for ``MCFLB``.

    - ``last_stage_only_cp_pf_method`` selects the profile-fix precedence policy
      for the Phase 2 last-stage-only solve; ``full_cp_pf_method`` does the same
      for the Phase 4 full solve. ``None`` skips the precedence-arc pass
      while keeping warm-start / ET hints. See :data:`PFMethod`.
    """

    last_stage_only_cp_pf_method: PFMethod | None = None
    full_cp_pf_method: PFMethod | None = None

    time_factor: int = 1
    """CSR scaling factor: a coarse last-stage completion ``C^c`` is interpreted
    as original-scale ``time_factor * C^c`` when scored against the instance's
    (original-scale) due window. ``1`` (default) is the ordinary same-scale case
    and reproduces pre-CSR behavior exactly. Only the CSR child controller sets
    ``time_factor = factor`` on a coarsened instance.

    Note (LB soundness, plan 20260711 §3): the coarse-problem MCF *lower bound*
    is **not** exactly re-derived for ``time_factor > 1`` (the arc-cost
    construction lives in ``algorithm/parallel_mc_pmtn.py``, outside this
    package). In coarse mode the pipeline still derives every schedule but
    reports its bound as ``None``. See ``calc_mcf_lb_and_derive_full_sch``.
    """

    def __post_init__(self) -> None:
        if self.time_factor < 1:
            raise ValueError(f"time_factor must be >= 1, got {self.time_factor}")
