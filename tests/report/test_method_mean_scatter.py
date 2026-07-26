"""Unit tests for ``load_method_mean_metrics`` batch expansion.

The run-level method-mean scatter must plot one point *per* incremental_sw_cp
batch (``incremental_sw_cp.<n>-batch_<id>``) instead of collapsing the whole
call_index into a single marker — mirroring the per-batch guide markers on
the flow-comparison chart. These tests pin that contract.
"""

from __future__ import annotations

from ffc_ddw_sum_et.report.method_mean_scatter import load_method_mean_metrics
from ffc_ddw_sum_et.report.obj_log_loader import (
    CallSegment,
    InstanceProgression,
    ProgPoint,
)


def _seg(call_index: int, name: str, end: float, obj: float) -> CallSegment:
    return CallSegment(
        call_index=call_index,
        subroutine_name=name,
        prefixed_subroutine_name=f"{call_index}-{name}",
        global_start_sec=end - 1.0,
        global_end_sec=end,
        points=(ProgPoint(global_sec=end, value=obj),),
    )


def _progression(instance_id: str, segs: list[CallSegment]) -> InstanceProgression:
    return InstanceProgression(
        instance_id=instance_id,
        job_cnt=50,
        stage_cnt=5,
        timelimit_sec=10.0,
        obj_value_calls=tuple(segs),
        obj_bound_calls=(),
    )


def _batch_run(instance_id: str, base_obj: float) -> InstanceProgression:
    # neh_cp (call 3), then three incremental_sw_cp batches (call 4), each
    # strictly improving, then solve (call 5).
    return _progression(
        instance_id,
        [
            _seg(3, "neh_cp", 3.0, base_obj),
            _seg(4, "incremental_sw_cp.1-batch_002", 4.0, base_obj - 10),
            _seg(4, "incremental_sw_cp.2-batch_003", 5.0, base_obj - 20),
            _seg(4, "incremental_sw_cp.3-batch_004", 6.0, base_obj - 30),
            _seg(5, "solve_base_model_cpsat", 9.0, base_obj - 35),
        ],
    )


def test_incremental_batches_become_separate_points() -> None:
    progs = [_batch_run("InstA", 100.0), _batch_run("InstB", 120.0)]
    baseline = {"InstA": 50.0, "InstB": 60.0}

    points = load_method_mean_metrics(progs, baseline_obj_by_instance=baseline)

    labels = [p["label"] for p in points]
    assert labels == [
        "neh_cp",
        "incremental_sw_cp.1-batch_002",
        "incremental_sw_cp.2-batch_003",
        "incremental_sw_cp.3-batch_004",
        "solve_base_model_cpsat",
    ]
    # All three batch points share the base method name (one symbol/colour).
    batch_pts = [p for p in points if p["method"] == "incremental_sw_cp"]
    assert len(batch_pts) == 3
    # mean_rpdf strictly decreases across the batches (each batch improves).
    rpdfs = [p["mean_rpdf"] for p in batch_pts]
    assert rpdfs == sorted(rpdfs, reverse=True)
    # mean time% increases across the batches.
    times = [p["mean_time_pct"] for p in batch_pts]
    assert times == sorted(times)


def test_non_improving_batch_dropped_when_opted_in() -> None:
    # Middle batch does not improve the objective for any instance.
    def run(instance_id: str) -> InstanceProgression:
        return _progression(
            instance_id,
            [
                _seg(4, "incremental_sw_cp.1-batch_002", 4.0, 90.0),
                _seg(4, "incremental_sw_cp.2-batch_003", 5.0, 90.0),  # no gain
                _seg(4, "incremental_sw_cp.3-batch_004", 6.0, 80.0),
            ],
        )

    progs = [run("InstA"), run("InstB")]
    baseline = {"InstA": 50.0, "InstB": 50.0}

    # Default keeps non-improving steps (flat "time wasted" segments).
    kept = load_method_mean_metrics(progs, baseline)
    assert "incremental_sw_cp.2-batch_003" in [p["label"] for p in kept]

    # Opt-in drops them.
    points = load_method_mean_metrics(progs, baseline, drop_non_improving_methods=True)
    labels = [p["label"] for p in points]
    assert "incremental_sw_cp.2-batch_003" not in labels
    assert "incremental_sw_cp.1-batch_002" in labels
    assert "incremental_sw_cp.3-batch_004" in labels


def test_worse_step_clamped_to_incumbent() -> None:
    # neh_cp (call 3) registers a solution *worse* than the incumbent it
    # received from flip (call 2). The incumbent never degrades, so the neh_cp
    # point must plot best(prev, current) = the flip incumbent, not neh_cp's
    # own worse obj.
    from ffc_ddw_sum_et._calc import rpd_f

    def run(instance_id: str) -> InstanceProgression:
        return _progression(
            instance_id,
            [
                _seg(2, "run_flip_makespan_cp_from_incumbent", 2.0, 100.0),
                _seg(3, "neh_cp", 3.0, 140.0),  # worse than flip's 100
                _seg(4, "incremental_sw_cp.1-batch_002", 4.0, 90.0),
            ],
        )

    progs = [run("InstA")]
    baseline = {"InstA": 50.0}

    points = load_method_mean_metrics(progs, baseline)
    flip = next(p for p in points if p["method"].startswith("run_flip"))
    neh = next(p for p in points if p["method"] == "neh_cp")
    # neh_cp is clamped to the flip incumbent (best-so-far), not rpd_f(140, 50).
    assert neh["mean_rpdf"] == flip["mean_rpdf"] == rpd_f(100.0, 50.0)
    # It is therefore a non-improving step.
    dropped = load_method_mean_metrics(progs, baseline, drop_non_improving_methods=True)
    assert "neh_cp" not in [p["label"] for p in dropped]


def test_endpoint_carries_forward_unreached_instances() -> None:
    # InstA runs the full flow through solve; InstB is cut off after the last
    # batch and never reaches solve. The solve endpoint must still average over
    # BOTH instances (carry-forward), not just the one reacher.
    full = _batch_run("InstA", 100.0)
    cut = _progression(
        "InstB",
        [
            _seg(3, "neh_cp", 3.0, 120.0),
            _seg(4, "incremental_sw_cp.1-batch_002", 4.0, 110.0),
            _seg(4, "incremental_sw_cp.2-batch_003", 5.0, 100.0),
            _seg(4, "incremental_sw_cp.3-batch_004", 6.0, 90.0),
            # no solve_base_model_cpsat
        ],
    )
    baseline = {"InstA": 50.0, "InstB": 60.0}

    points = load_method_mean_metrics([full, cut], baseline)
    solve = next(p for p in points if p["label"] == "solve_base_model_cpsat")
    assert solve["instance_count"] == 2  # carry-forward, not reacher-only (1)


# ---------------------------------------------------------------------------
# W1 C2 — is_inner flag
# ---------------------------------------------------------------------------


def test_inner_points_have_is_inner_true() -> None:
    """C2 §5.3: CSR inner progress points (label containing '.inner-')
    must have is_inner=True."""
    progs = [
        _progression(
            "Inst1",
            [
                _seg(1, "calc_mcf_lb_and_derive_full_sch", 1.0, 100.0),
                _seg(
                    2,
                    "coarsen_solve_reconstruct.inner-00-1-solve_base_model_cpsat",
                    2.0,
                    90.0,
                ),
                _seg(3, "solve_base_model_cpsat", 5.0, 80.0),
            ],
        )
    ]
    baseline = {"Inst1": 50.0}
    points = load_method_mean_metrics(progs, baseline)
    for p in points:
        if "inner" in p["label"]:
            assert p["is_inner"], f"expected is_inner=True for {p['label']}"
        else:
            assert not p["is_inner"], f"expected is_inner=False for {p['label']}"


def test_regular_points_have_is_inner_false() -> None:
    """C2 §5.4 (regression): a flow without '.inner-' labels must have
    is_inner=False for every point."""
    progs = [
        _progression(
            "Inst1",
            [
                _seg(1, "neh_cp", 3.0, 100.0),
                _seg(2, "incremental_sw_cp.1-batch_002", 4.0, 90.0),
                _seg(3, "solve_base_model_cpsat", 9.0, 80.0),
            ],
        )
    ]
    baseline = {"Inst1": 50.0}
    points = load_method_mean_metrics(progs, baseline)
    assert len(points) > 0
    for p in points:
        assert not p["is_inner"], f"expected is_inner=False for {p['label']}"


def test_batch_inner_mixed_regression() -> None:
    """A mixed flow: regular batch points + inner points. Batch points are
    not inner (no '.inner-'), inner points are."""
    progs = [
        _progression(
            "Inst1",
            [
                _seg(1, "neh_cp", 1.0, 100.0),
                _seg(2, "coarsen_solve_reconstruct", 2.0, 90.0),
                _seg(2, "coarsen_solve_reconstruct.inner-00-1-calc_mcf", 2.5, 88.0),
                _seg(2, "coarsen_solve_reconstruct.inner-01-2-neh_cp", 3.0, 85.0),
                _seg(3, "incremental_sw_cp.1-batch_002", 5.0, 82.0),
            ],
        )
    ]
    baseline = {"Inst1": 50.0}
    points = load_method_mean_metrics(progs, baseline)
    for p in points:
        if "inner" in p["label"]:
            assert p["is_inner"], f"expected is_inner=True for {p['label']}"
        else:
            assert not p["is_inner"], f"expected is_inner=False for {p['label']}"

    # Verify the specific counts.
    inner_count = sum(1 for p in points if p["is_inner"])
    assert inner_count == 2, f"expected 2 inner points, got {inner_count}"
    regular_count = sum(1 for p in points if not p["is_inner"])
    assert regular_count == 3, f"expected 3 regular points, got {regular_count}"
