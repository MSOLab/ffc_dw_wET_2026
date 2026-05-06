"""MCF preemptive lower bound: solve and result wrapper.

Owns ``solve_mcf_lb`` and ``McfLbResult``. Kept separate from
``phase1_mcf`` so callers that only need the LB / preemptive schedule do
not pull in the seed-generation machinery.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ..parallel_mc_pmtn import ParallelMachinePreemptionMcf
from .diagnostic import MCFLBDiagnostic

__all__ = ["McfLbResult", "MCFLBStopRequested", "solve_mcf_lb"]


class MCFLBStopRequested(Exception):
    """Raised by ``solve_mcf_lb`` when the caller's ``stop_predicate``
    returned True before the MCF LP was solved. Callers should catch and
    short-circuit to a stop-report.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class McfLbResult:
    """Bare result of solving the MCF relaxation: bound + preemptive schedule.

    Used by ``run_phase1`` to seed the full 4-phase pipeline, and by
    LB-only subroutines that report a global lower bound with no schedule.
    The ``mcf`` handle is retained so callers can extract MCF-derived
    priority maps without re-solving.
    """

    mcf_lb: float
    mcf_preemptive_schedule: MCFPreemptiveSchedule
    mcf: ParallelMachinePreemptionMcf  # TODO: remove; use mcf_preemptive_schedule instead


def solve_mcf_lb(
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    *,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
    stop_predicate: Callable[[], bool] | None = None,
) -> McfLbResult:
    """Solve the MCF relaxation and record the bound on ``diagnostic``.

    Mutates ``diagnostic`` in place: sets ``mcf_solve_sec``, ``mcf_lb``,
    and advances ``reached_phase`` to ``"mcf"``.

    Args:
        r_multiplier: Scales the MCF release dates ``r_j`` (sum of upstream
            processing times) by this factor; the scaled value is
            ``ceil(r_j * r_multiplier)``. ``1.0`` (default) preserves the
            current behaviour. Values ``<= 1`` keep the resulting bound a
            valid LB on the original instance (looser when ``< 1``);
            values ``> 1`` make it no longer a global LB.
        r_increment: Integer ``>= 0`` added to every ``r_j`` *after* the
            ``r_multiplier`` scaling, so the effective release date is
            ``ceil(r_j * r_multiplier) + r_increment``. ``0`` (default)
            preserves the current behaviour. Any positive value pushes
            releases later than the original instance and therefore
            makes the resulting MCF objective no longer a global LB.
        stop_predicate: Optional caller-side termination probe. Checked
            once before ``mcf.solve()``; raises ``MCFLBStopRequested`` if
            it returns True. The MCF LP itself is not interruptible mid-
            solve, so post-solve termination is left to the caller.

    Raises:
        RuntimeError: if the MCF flow is not optimal for ``instance``.
        MCFLBStopRequested: if ``stop_predicate`` requested stop before
            solve.
    """
    if stop_predicate is not None and stop_predicate():
        raise MCFLBStopRequested

    last_stage_id = instance.stage_id_list[-1]

    t_mcf = time.monotonic()
    mcf = ParallelMachinePreemptionMcf.from_instance(
        instance, r_multiplier=r_multiplier, r_increment=r_increment
    )
    mcf.solve()
    if not mcf.is_optimal():
        raise RuntimeError(f"MCF not optimal for instance {instance.name}")
    mcf_lb = float(mcf.get_obj_value())
    diagnostic.mcf_solve_sec = time.monotonic() - t_mcf
    diagnostic.mcf_lb = mcf_lb
    diagnostic.reached_phase = "mcf"

    mcf_preemptive_schedule = MCFPreemptiveSchedule.from_flow_dict(
        mcf.get_variable_value_dict(),
        stage_id=last_stage_id,
        machines=instance.stage_2_machines_map[last_stage_id],
    )
    return McfLbResult(
        mcf_lb=mcf_lb,
        mcf_preemptive_schedule=mcf_preemptive_schedule,
        mcf=mcf,
    )
