"""``time_factor`` (CSR coarse-mode) support for ``FlipMakespanCpDispatcher``.

The coarse instance (``coarsen_processing_times``) keeps due windows at the
ORIGINAL scale, so a coarse completion ``C^c`` must be interpreted as
``time_factor * C^c`` against the original window. The flip-makespan CP model
itself is a pure coarse-time makespan model (scale-free), but every wET
evaluation, the ``insert_idle_time`` post-process, and the right-shift's
due-window comparison must run at ``time_factor``.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ffc_ddw_sum_et.algorithm.base.alg_record import WorkStatus
from ffc_ddw_sum_et.algorithm.base.alg_spec import AlgSpec
from ffc_ddw_sum_et.algorithm.fam import FAMDispatcher, FAMOption
from ffc_ddw_sum_et.algorithm.flip_makespan_cp import (
    FlipMakespanCpDispatcher,
    FlipMakespanCpOption,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness

FACTOR = 10


def _make_original_instance() -> FFcDDWParameters:
    """Tiny instance whose processing times are multiples of ``FACTOR`` and
    whose due windows sit at the original (fine) scale, so coarsening yields
    small coarse completions that must be scaled back up by ``FACTOR``.
    """
    return FFcDDWParameters(
        name="flip_tf_instance",
        job_id_list=["j0", "j1", "j2"],
        stage_id_list=["i0", "i1"],
        stage_2_machines_map={"i0": ["i0_0", "i0_1"], "i1": ["i1_0"]},
        p_manager=JobStageProcessingTimeManager(
            name="p", df=pd.DataFrame([[20, 30], [20, 20], [10, 10]])
        ),
        # Original-scale due windows (roughly FACTOR * a small coarse target).
        job_2_due_window_map={"j0": (50, 80), "j1": (40, 70), "j2": (30, 60)},
        job_2_ewt_map={"j0": 1, "j1": 1, "j2": 1},
        job_2_twt_map={"j0": 1, "j1": 1, "j2": 1},
    )


def _seed_coarse_incumbent(coarse: FFcDDWParameters):
    fam_record = FAMDispatcher().run(
        AlgSpec(instance=coarse, option=FAMOption(job_sequence=("j2", "j1", "j0")))
    )
    assert fam_record.result is not None
    assert fam_record.result.schedule is not None
    return fam_record.result.schedule


def test_option_defaults_time_factor_one() -> None:
    assert FlipMakespanCpOption().time_factor == 1


@pytest.mark.parametrize("bad", [0, -1, -10])
def test_option_rejects_time_factor_below_one(bad: int) -> None:
    with pytest.raises(ValueError):
        FlipMakespanCpOption(time_factor=bad)


def test_reported_obj_matches_coarse_wet_at_time_factor() -> None:
    """(a) On a coarsened instance run with ``time_factor=FACTOR``, the
    reported ``obj_value`` must equal the original-scale wET of the returned
    schedule scored with the same ``time_factor``.
    """
    original = _make_original_instance()
    coarse = FFcDDWParameters.coarsen_processing_times(original, FACTOR)
    incumbent = _seed_coarse_incumbent(coarse)

    record = FlipMakespanCpDispatcher().run(
        AlgSpec(
            instance=coarse,
            option=FlipMakespanCpOption(
                cp_tl_seconds=5.0, solver_thread_cnt=1, time_factor=FACTOR
            ),
            ref_solution=incumbent,
        )
    )

    assert record.work_status in (WorkStatus.OPTIMAL, WorkStatus.FEASIBLE)
    assert record.result is not None
    assert record.result.schedule is not None
    assert record.result.obj_value is not None

    sum_e, sum_t = compute_weighted_earliness_tardiness(
        record.result.schedule, coarse, time_factor=FACTOR
    )
    assert record.result.obj_value == pytest.approx(float(sum_e + sum_t))
    # sum_earliness / sum_tardiness metrics must also be the coarse-mode values.
    assert record.result.metrics["sum_earliness"] == pytest.approx(float(sum_e))
    assert record.result.metrics["sum_tardiness"] == pytest.approx(float(sum_t))


def test_time_factor_scoring_differs_from_naive_unscaled() -> None:
    """The coarse-mode obj is a genuinely different (correct) number than the
    scale-mixing ``time_factor=1`` scoring of the same schedule — guards
    against silently dropping the factor.
    """
    original = _make_original_instance()
    coarse = FFcDDWParameters.coarsen_processing_times(original, FACTOR)
    incumbent = _seed_coarse_incumbent(coarse)

    record = FlipMakespanCpDispatcher().run(
        AlgSpec(
            instance=coarse,
            option=FlipMakespanCpOption(
                cp_tl_seconds=5.0, solver_thread_cnt=1, time_factor=FACTOR
            ),
            ref_solution=incumbent,
        )
    )
    assert record.result is not None and record.result.schedule is not None

    scaled_e, scaled_t = compute_weighted_earliness_tardiness(
        record.result.schedule, coarse, time_factor=FACTOR
    )
    naive_e, naive_t = compute_weighted_earliness_tardiness(
        record.result.schedule, coarse, time_factor=1
    )
    assert (scaled_e + scaled_t) != (naive_e + naive_t)


def _run_original(option: FlipMakespanCpOption):
    original = _make_original_instance()
    incumbent = FAMDispatcher().run(
        AlgSpec(instance=original, option=FAMOption(job_sequence=("j2", "j1", "j0")))
    )
    assert incumbent.result is not None and incumbent.result.schedule is not None
    return FlipMakespanCpDispatcher().run(
        AlgSpec(
            instance=original,
            option=option,
            ref_solution=incumbent.result.schedule,
        )
    )


def test_time_factor_one_reproduces_default() -> None:
    """(b) Invariance: an explicit ``time_factor=1`` must reproduce the default
    (no ``time_factor``) behaviour exactly on a non-coarse instance.
    """
    default_rec = _run_original(
        FlipMakespanCpOption(cp_tl_seconds=5.0, solver_thread_cnt=1)
    )
    explicit_rec = _run_original(
        FlipMakespanCpOption(cp_tl_seconds=5.0, solver_thread_cnt=1, time_factor=1)
    )

    assert default_rec.result is not None and explicit_rec.result is not None
    assert default_rec.result.obj_value == explicit_rec.result.obj_value
    assert default_rec.result.metrics["sum_earliness"] == pytest.approx(
        explicit_rec.result.metrics["sum_earliness"]
    )
    assert default_rec.result.metrics["sum_tardiness"] == pytest.approx(
        explicit_rec.result.metrics["sum_tardiness"]
    )
    assert (
        default_rec.result.schedule.get_jik_2_end_time_map()
        == explicit_rec.result.schedule.get_jik_2_end_time_map()
    )
