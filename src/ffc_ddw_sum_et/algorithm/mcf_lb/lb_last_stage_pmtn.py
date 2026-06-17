"""MCF-based last-stage preemptive lower bound.

Two layers live here, both pure (no controller / orchestration dependency):

  - ``solve_mcf_lb`` (with ``McfLbResult`` and ``MCFLBStopRequested``) is
    the LP-solving core: build the parallel-machine preemption MCF on the
    last stage, solve it, and return the bound + preemptive schedule.

  - ``apply_lb_by_mcf`` is the algorithm-level entry point used by the
    controller wrapper / composite. It builds the (possibly augmented)
    instance, calls ``solve_mcf_lb``, optionally dumps a signed C-cost
    heatmap, and packages everything into an :class:`ApplyLbByMcfResult`.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ...io.parallel_mc_cost_heatmap import HeatmapSort
from ...parameters.ffc_ddw_params import FFcDDWParameters
from ...solution.mcf_preemptive_schedule import MCFPreemptiveSchedule
from ..parallel_mc_pmtn import ParallelMachinePreemptionMcf

__all__ = [
    "ApplyLbByMcfResult",
    "MCFLBStopRequested",
    "McfLbResult",
    "apply_lb_by_mcf",
    "solve_mcf_lb",
]


class MCFLBStopRequested(Exception):
    """Raised by ``solve_mcf_lb`` when the caller's ``stop_predicate``
    returned True before the MCF LP was solved. Callers should catch and
    short-circuit to a stop-report.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class McfLbResult:
    """Bare result of solving the MCF relaxation: bound + preemptive schedule.

    Used by ``apply_lb_by_mcf`` to report a global lower bound and by
    downstream heuristics that consume the preemptive schedule. The
    ``mcf`` handle is retained so callers can extract MCF-derived priority
    maps without re-solving.
    """

    mcf: ParallelMachinePreemptionMcf  # TODO: remove; use mcf_preemptive_schedule instead
    mcf_solve_sec: float
    mcf_lb: float
    mcf_preemptive_schedule: MCFPreemptiveSchedule


def solve_mcf_lb(
    instance: FFcDDWParameters,
    *,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
    stage_id: str | None = None,
    tardiness_only: bool = False,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
) -> McfLbResult:
    """Solve the MCF relaxation and return the bound + preemptive schedule.

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
        stage_id: Target stage on which to build the MCF relaxation and
            preemptive schedule. ``None`` (default) selects the last stage,
            preserving the current behaviour. For an intermediate stage,
            releases use the upstream processing-time sums of that stage.
        tardiness_only: When ``True``, the MCF uses a weighted-tardiness-only
            cost with the upper due date projected by the downstream tail
            (``d_plus_j - sum p over stages after stage_id``); earliness is
            dropped. The resulting objective is still a valid LB on OPT.
            ``False`` (default) uses the full earliness+tardiness cost.
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
        if logger is not None:
            logger.info(
                "solve_mcf_lb: stop_predicate True before LP solve; "
                "raising MCFLBStopRequested."
            )
        raise MCFLBStopRequested

    target_stage_id = stage_id or instance.stage_id_list[-1]

    t_mcf = time.monotonic()
    mcf = ParallelMachinePreemptionMcf.from_instance(
        instance,
        r_multiplier=r_multiplier,
        r_increment=r_increment,
        stage_id=stage_id,
        tardiness_only=tardiness_only,
    )
    mcf.solve()
    if not mcf.is_optimal():
        raise RuntimeError(f"MCF not optimal for instance {instance.name}")
    mcf_lb = float(mcf.get_obj_value())
    mcf_solve_sec = time.monotonic() - t_mcf
    if logger is not None:
        logger.info(
            "solve_mcf_lb: solved in %.3fs, mcf_lb=%.2f "
            "(stage_id=%s, tardiness_only=%s, r_multiplier=%.4g, r_increment=%d)",
            mcf_solve_sec,
            mcf_lb,
            target_stage_id,
            tardiness_only,
            r_multiplier,
            r_increment,
        )

    mcf_preemptive_schedule = MCFPreemptiveSchedule.from_flow_dict(
        mcf.get_variable_value_dict(),
        stage_id=target_stage_id,
        machines=instance.stage_2_machines_map[target_stage_id],
    )
    return McfLbResult(
        mcf_lb=mcf_lb,
        mcf_preemptive_schedule=mcf_preemptive_schedule,
        mcf=mcf,
        mcf_solve_sec=mcf_solve_sec,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplyLbByMcfResult:
    """Aggregate result of one ``apply_lb_by_mcf`` call.

    ``obj_bound_is_valid`` mirrors the validity check on the bound: ``True``
    only when the MCF was solved on the original (non-augmented) instance
    with ``r_multiplier <= 1`` and ``r_increment == 0``. When ``False``,
    callers must report the bound as ``None`` on the outer subroutine
    report.
    """

    mcf_lb: float
    mcf_preemptive_schedule: MCFPreemptiveSchedule
    mcf: ParallelMachinePreemptionMcf
    mcf_solve_sec: float
    p_increment_used: int
    r_multiplier_used: float
    r_increment_used: int
    obj_bound_is_valid: bool


def apply_lb_by_mcf(
    instance: FFcDDWParameters,
    *,
    p_increment: int = 0,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
    stage_id: str | None = None,
    tardiness_only: bool = False,
    draw_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "due2-weight-pos",
    heatmap_yaml_path: Path | None = None,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
) -> ApplyLbByMcfResult:
    """Build the (possibly augmented) instance, solve the MCF relaxation,
    and return the bound + preemptive schedule.

    Raises ``MCFLBStopRequested`` (from :func:`solve_mcf_lb`) if
    ``stop_predicate`` fires before the LP solve; callers catch and
    short-circuit to a stop-report.

    Args:
        instance: Original FFcDDW instance.
        p_increment: Integer ``>= 0``. When non-zero, the MCF relaxation
            is solved on an *augmented* instance whose last-stage
            processing times are increased by ``p_increment`` for every
            job. The resulting bound is valid for the augmented problem
            only — not a global LB on the original instance — so
            ``obj_bound_is_valid`` is ``False`` in that case.
        r_multiplier: Scales the per-job MCF release dates by this factor;
            each value becomes ``ceil(r_j * r_multiplier)``. Must be ``>= 0``.
            ``1.0`` (default) preserves the current behaviour. Values
            ``<= 1`` keep the resulting bound a valid LB; values ``> 1``
            make it no longer a global LB.
        r_increment: Integer ``>= 0`` added to every release date *after*
            the ``r_multiplier`` scaling. Any positive value invalidates
            the global LB.
        stage_id: Target stage for the MCF relaxation and preemptive
            schedule. ``None`` (default) selects the last stage,
            preserving the current behaviour. Forwarded to
            :func:`solve_mcf_lb`.
        tardiness_only: When ``True``, the MCF uses a weighted-tardiness-only
            cost with the upper due date projected by the downstream tail;
            earliness is dropped. The bound stays a valid LB on OPT, so it
            does **not** invalidate ``obj_bound_is_valid``. Forwarded to
            :func:`solve_mcf_lb`. ``False`` (default) uses the full
            earliness+tardiness cost — valid at the last stage, but at an
            intermediate ``stage_id`` the projected earliness arm is
            over-counted, so the objective is **not** a valid LB and
            ``obj_bound_is_valid`` is ``False`` (the result is then an
            approximate objective usable only for seeding a schedule).
        draw_heatmap: When ``True`` and ``heatmap_yaml_path`` is provided,
            build the parallel-machine signed C-cost matrix and dump it
            to that YAML path. The heatmap is never drawn when
            ``tardiness_only`` is ``True`` (intermediate stages).
        heatmap_sort: Row ordering for the heatmap (one of the
            ``HeatmapSort`` literals; see ``io.parallel_mc_cost_heatmap``).
            Ignored when ``draw_heatmap`` is ``False``.
        heatmap_yaml_path: Path for the heatmap YAML. When ``None`` and
            ``draw_heatmap=True``, the heatmap is not written (but the
            MCF solve still runs).
        stop_predicate: Forwarded to :func:`solve_mcf_lb`.
        logger: Optional logger forwarded to :func:`solve_mcf_lb` and
            heatmap logging.
    """
    if p_increment < 0:
        raise ValueError(
            f"p_increment must be 0 or a positive integer; got {p_increment}."
        )
    if r_multiplier < 0:
        raise ValueError(f"r_multiplier must be >= 0; got {r_multiplier}.")
    if r_increment < 0:
        raise ValueError(
            f"r_increment must be 0 or a positive integer; got {r_increment}."
        )

    if p_increment == 0:
        instance_for_mcf = instance
    else:
        last_stage_id = instance.stage_id_list[-1]
        instance_for_mcf = FFcDDWParameters.with_stage_processing_time_increment(
            instance, last_stage_id, p_increment
        )

    mcf_result: McfLbResult = solve_mcf_lb(
        instance_for_mcf,
        r_multiplier=r_multiplier,
        r_increment=r_increment,
        stage_id=stage_id,
        tardiness_only=tardiness_only,
        stop_predicate=stop_predicate,
        logger=logger,
    )

    if draw_heatmap and not tardiness_only and heatmap_yaml_path is not None:
        from ...io import build_signed_cost_matrix, dump_signed_cost_heatmap_yaml

        heatmap_data = build_signed_cost_matrix(
            instance_for_mcf,
            sort=heatmap_sort,
            x_jt_map=mcf_result.mcf.get_variable_value_dict(),
            obj_value=mcf_result.mcf_lb,
            r_multiplier=r_multiplier,
            r_increment=r_increment,
        )
        dump_signed_cost_heatmap_yaml(heatmap_yaml_path, heatmap_data)
        if logger is not None:
            logger.info(
                "apply_lb_by_mcf: wrote heatmap YAML to %s "
                "(jobs=%d, t-range=[%d..%d], x_jt cells=%d)",
                heatmap_yaml_path,
                len(heatmap_data.y_labels),
                heatmap_data.t_axis[0],
                heatmap_data.t_axis[-1],
                len(heatmap_data.x_cells),
            )

    last_stage_id = instance.stage_id_list[-1]
    is_last_stage = stage_id is None or stage_id == last_stage_id
    no_augment = p_increment == 0 and r_multiplier <= 1.0 and r_increment == 0
    # The non-augmented MCF objective is a valid LB on OPT only for the two
    # valid relaxations: last-stage full-ET, or any-stage tardiness-only. The
    # intermediate full-ET cost over-counts earliness (vault/bounds_A_C_P3.tex)
    # and is therefore NOT a valid bound.
    obj_bound_is_valid = no_augment and (tardiness_only or is_last_stage)

    return ApplyLbByMcfResult(
        mcf_lb=mcf_result.mcf_lb,
        mcf_preemptive_schedule=mcf_result.mcf_preemptive_schedule,
        mcf=mcf_result.mcf,
        mcf_solve_sec=mcf_result.mcf_solve_sec,
        p_increment_used=p_increment,
        r_multiplier_used=r_multiplier,
        r_increment_used=r_increment,
        obj_bound_is_valid=obj_bound_is_valid,
    )
