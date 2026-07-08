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
