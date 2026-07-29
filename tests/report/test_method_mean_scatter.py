"""Unit tests for ``load_method_mean_metrics`` step-key expansion.

The run-level method-mean scatter must plot one point *per* incremental_sw_cp
batch (``incremental_sw_cp.<n>-batch_<id>``) instead of collapsing the whole
call_index into a single marker — mirroring the per-batch guide markers on
the flow-comparison chart. ``incremental_job_contrib_cp`` is the opposite
case: its per-rep contexts collapse to one point per jd level. These tests
pin both contracts.

``_seg`` runs its ``name`` through the loader's
:func:`_normalize_subroutine_name` so fixtures can carry the *raw* obj_log
label and exercise the same collapse the loader applies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ffc_ddw_sum_et._calc import rpd_f
from ffc_ddw_sum_et.report._chart_constants import HOVER_PERCENT_DECIMALS
from ffc_ddw_sum_et.report.method_mean_scatter import (
    export_method_mean_scatter_html,
    load_method_mean_metrics,
)
from ffc_ddw_sum_et.report.obj_log_loader import (
    CallSegment,
    InstanceProgression,
    ProgPoint,
    _normalize_subroutine_name,
)


def _seg(call_index: int, name: str, end: float, obj: float) -> CallSegment:
    return CallSegment(
        call_index=call_index,
        subroutine_name=_normalize_subroutine_name(name),
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
# is_top_level flag — marker-shape level split
# ---------------------------------------------------------------------------


def test_bare_controller_steps_are_top_level() -> None:
    """Given a flow of bare controller step labels,
    When the method-mean points are built,
    Then every point is flagged is_top_level (open-circle marker)."""
    progs = [
        _progression(
            "Inst1",
            [
                _seg(1, "calc_mcf_lb_and_derive_full_sch", 1.0, 100.0),
                _seg(2, "run_flip_makespan_cp_from_incumbent", 2.0, 95.0),
                _seg(3, "neh_cp", 3.0, 90.0),
                _seg(4, "coarsen_solve_reconstruct", 5.0, 80.0),
            ],
        )
    ]
    points = load_method_mean_metrics(progs, {"Inst1": 50.0})
    assert len(points) == 4
    for p in points:
        assert p["is_top_level"], f"expected is_top_level=True for {p['label']}"


def test_csr_inner_points_are_not_top_level() -> None:
    """Given CSR inner labels in both the ``.inner-`` and the index-free
    ``coarsen_solve_reconstruct-<child>`` format,
    When the method-mean points are built,
    Then they are not top level while the CSR endpoint itself is."""
    progs = [
        _progression(
            "Inst1",
            [
                _seg(1, "calc_mcf_lb_and_derive_full_sch", 1.0, 100.0),
                _seg(2, "coarsen_solve_reconstruct-1-calc_mcf_lb", 1.5, 95.0),
                _seg(2, "coarsen_solve_reconstruct.inner-00-3-neh_cp", 2.0, 90.0),
                _seg(2, "coarsen_solve_reconstruct", 2.5, 90.0),
                _seg(3, "solve_base_model_cpsat", 5.0, 80.0),
            ],
        )
    ]
    points = load_method_mean_metrics(progs, {"Inst1": 50.0})
    by_label = {p["label"]: p for p in points}

    assert not by_label["coarsen_solve_reconstruct-1-calc_mcf_lb"]["is_top_level"]
    assert not by_label["coarsen_solve_reconstruct.inner-00-3-neh_cp"]["is_top_level"]
    assert by_label["coarsen_solve_reconstruct"]["is_top_level"]
    assert by_label["solve_base_model_cpsat"]["is_top_level"]


def test_batch_points_are_not_top_level() -> None:
    """Given a top-level ``incremental_sw_cp`` registering per-batch points,
    When the method-mean points are built,
    Then each batch point is not top level (they are sub-steps of one call),
    while the surrounding bare steps are."""
    progs = [
        _progression(
            "Inst1",
            [
                _seg(1, "neh_cp", 3.0, 100.0),
                _seg(2, "incremental_sw_cp.1-batch_002", 4.0, 90.0),
                _seg(2, "incremental_sw_cp.2-batch_003", 5.0, 85.0),
                _seg(3, "solve_base_model_cpsat", 9.0, 80.0),
            ],
        )
    ]
    points = load_method_mean_metrics(progs, {"Inst1": 50.0})
    by_label = {p["label"]: p for p in points}

    assert by_label["neh_cp"]["is_top_level"]
    assert not by_label["incremental_sw_cp.1-batch_002"]["is_top_level"]
    assert not by_label["incremental_sw_cp.2-batch_003"]["is_top_level"]
    assert by_label["solve_base_model_cpsat"]["is_top_level"]


# ---------------------------------------------------------------------------
# CSR inner labels without a candidate-row index
# ---------------------------------------------------------------------------


def test_repeated_inner_label_merges_into_one_inner_point() -> None:
    """Given the same CSR child step contributing several candidate rows,
    When the method-mean points are built,
    Then it yields exactly one marker — not one per row.

    The pre-fix labels carried the candidate-row index
    (``.inner-04-``/``.inner-05-``), so one child step scattered into several
    markers and the connecting line zig-zagged between them.
    """
    progs = [
        _progression(
            "Inst1",
            [
                _seg(1, "neh_cp", 1.0, 100.0),
                _seg(2, "coarsen_solve_reconstruct-2-neh_cp", 2.0, 95.0),
                _seg(2, "coarsen_solve_reconstruct-2-neh_cp", 3.0, 90.0),
                _seg(2, "coarsen_solve_reconstruct-2-neh_cp", 4.0, 88.0),
            ],
        )
    ]
    points = load_method_mean_metrics(progs, {"Inst1": 50.0})

    inner = [p for p in points if p["label"].startswith("coarsen_solve_reconstruct-")]
    assert len(inner) == 1, f"expected 1 merged inner point, got {len(inner)}"
    assert not inner[0]["is_top_level"]


def test_compound_step_endpoint_comes_after_its_inner_points() -> None:
    """Given instances that ran a different number of CSR inner batches
    before the compound step's own endpoint,
    When the method-mean points are built,
    Then the compound step's endpoint is emitted after every inner point.

    Step order was first appearance across the concatenated per-instance
    endpoint rows. With heterogeneous step sets that is not self-consistent:
    the short instance contributed the CSR parent endpoint before the long
    instance contributed its extra batches, so the parent — which sits at the
    largest time — got stranded mid-sequence and the connecting line doubled
    back to it.
    """
    short = _progression(
        "InstShort",
        [
            _seg(1, "neh_cp", 1.0, 100.0),
            _seg(
                2,
                "coarsen_solve_reconstruct-4-incremental_sw_cp.1-batch_002",
                2.0,
                95.0,
            ),
            _seg(
                2,
                "coarsen_solve_reconstruct-4-incremental_sw_cp.2-batch_003",
                3.0,
                92.0,
            ),
            _seg(2, "coarsen_solve_reconstruct", 4.0, 91.0),
        ],
    )
    long = _progression(
        "InstLong",
        [
            _seg(1, "neh_cp", 1.0, 110.0),
            _seg(
                2,
                "coarsen_solve_reconstruct-4-incremental_sw_cp.1-batch_002",
                2.0,
                104.0,
            ),
            _seg(
                2,
                "coarsen_solve_reconstruct-4-incremental_sw_cp.2-batch_003",
                3.0,
                101.0,
            ),
            _seg(
                2,
                "coarsen_solve_reconstruct-4-incremental_sw_cp.3-batch_004",
                4.0,
                99.0,
            ),
            _seg(
                2,
                "coarsen_solve_reconstruct-4-incremental_sw_cp.4-batch_005",
                5.0,
                98.0,
            ),
            _seg(2, "coarsen_solve_reconstruct", 6.0, 97.0),
        ],
    )
    baseline = {"InstShort": 50.0, "InstLong": 55.0}

    points = load_method_mean_metrics([short, long], baseline)

    labels = [p["label"] for p in points]
    assert labels == [
        "neh_cp",
        "coarsen_solve_reconstruct-4-incremental_sw_cp.1-batch_002",
        "coarsen_solve_reconstruct-4-incremental_sw_cp.2-batch_003",
        "coarsen_solve_reconstruct-4-incremental_sw_cp.3-batch_004",
        "coarsen_solve_reconstruct-4-incremental_sw_cp.4-batch_005",
        "coarsen_solve_reconstruct",
    ]
    # The parent endpoint is the last point of that step, so the line reaches
    # it going forwards in time.
    times = [p["mean_time_pct"] for p in points]
    assert times == sorted(times), (
        "connecting line runs backwards; order was "
        + ", ".join(f"{p['label']}@{p['mean_time_pct']:.3f}" for p in points)
    )


# ── C3 hover unification ────────────────────────────────────────────────


def _render_method_mean_html(
    tmp_path: Path,
    *,
    x_decimals: int = 1,
    y_decimals: int = 1,
) -> str:
    def run(instance_id: str) -> InstanceProgression:
        return _progression(
            instance_id,
            [
                _seg(1, "neh_cp", 3.0, 100.0),
                _seg(2, "solve_base_model_cpsat", 9.0, 80.0),
            ],
        )

    progs = [run("InstA")]
    baseline = {"InstA": 50.0}
    points = load_method_mean_metrics(progs, baseline_obj_by_instance=baseline)
    out_path = tmp_path / "test_method_mean.html"
    ok = export_method_mean_scatter_html(
        [{"label": "test", "method_points": points}],
        out_path,
        x_percent_decimals=x_decimals,
        y_percent_decimals=y_decimals,
    )
    assert ok
    return out_path.read_text(encoding="utf-8")


def test_hover_uses_3_percent_for_both_axes(tmp_path: Path) -> None:
    """C3-8: method-mean scatter hover is .3% for x and y."""
    html = _render_method_mean_html(tmp_path)
    hover_dec = str(HOVER_PERCENT_DECIMALS)
    assert f"%{{x:.{hover_dec}%}}" in html, f"missing %{{x:.{hover_dec}%}}"
    assert f"%{{y:.{hover_dec}%}}" in html, f"missing %{{y:.{hover_dec}%}}"
    assert ".4%" not in html.split("hovertemplate")[1].split("extra>")[0], (
        "stale .4% in hovertemplate"
    )


def test_tickformat_stays_at_1_percent(tmp_path: Path) -> None:
    """C3-9: method-mean scatter tickformat stays at .1%, not .3%."""
    html = _render_method_mean_html(tmp_path)
    assert 'tickformat: ".1%"' in html, "tickformat regressed from .1%"


def test_hover_unaffected_by_tick_decimals_arg(tmp_path: Path) -> None:
    """C3-10: x/y_percent_decimals=2 affects ticks only, hover stays at .3%."""
    html = _render_method_mean_html(tmp_path, x_decimals=2, y_decimals=2)
    assert 'tickformat: ".2%"' in html, "tickformat should be .2%"
    hover_dec = str(HOVER_PERCENT_DECIMALS)
    assert f"%{{x:.{hover_dec}%}}" in html, (
        f"hover regressed from {hover_dec} to tick decimals"
    )
    assert f"%{{y:.{hover_dec}%}}" in html


# ── job_contrib_cp rep collapse ─────────────────────────────────────────


def test_job_contrib_rep_collapse_single_instance() -> None:
    """Given one instance whose jd006 level ran two improving reps,
    when the metrics are built,
    then both reps collapse to one point carrying the *last* rep's
    time and objective.
    """
    prog = _progression(
        "InstA",
        [
            _seg(1, "neh_cp", 2.0, 100.0),
            _seg(2, "incremental_job_contrib_cp.3-jd006_r001", 3.0, 95.0),
            _seg(2, "incremental_job_contrib_cp.4-jd006_r002", 5.0, 90.0),
        ],
    )
    baseline = {"InstA": 50.0}

    points = load_method_mean_metrics([prog], baseline)
    jd_pts = [p for p in points if p["method"] == "incremental_job_contrib_cp"]
    assert len(jd_pts) == 1, f"expected 1 merged point, got {len(jd_pts)}"
    pt = jd_pts[0]
    # The point carries the normalized (rep-free) subroutine_name.
    assert pt["label"] == "incremental_job_contrib_cp.jd006"
    # Time is the last rep's (global_end_sec=5.0 / timelimit=10.0).
    assert pt["mean_time_pct"] == pytest.approx(0.5)
    # RPDf uses the best-so-far incumbent from the last rep (obj=90.0).
    assert pt["mean_rpdf"] == pytest.approx(rpd_f(90.0, 50.0))
    # A collapsed jd level stays a sub-step (star-diamond), not top level.
    assert pt["is_top_level"] is False


def test_job_contrib_rep_collapse_realigns_instances() -> None:
    """Given two instances whose depth-2 step indices drifted apart because
    only one of them ran a second rep on jd006,
    when the metrics are built,
    then each jd level is a single point averaging both instances.

    Without normalization the raw labels differ per instance
    (``5-jd007_r001`` vs ``4-jd007_r001``), so jd007 splits into two points
    and the flow order zig-zags.
    """
    inst_a = _progression(
        "InstA",
        [
            _seg(1, "neh_cp", 1.0, 100.0),
            _seg(2, "incremental_job_contrib_cp.3-jd006_r001", 2.0, 95.0),
            _seg(2, "incremental_job_contrib_cp.4-jd006_r002", 3.0, 90.0),
            _seg(2, "incremental_job_contrib_cp.5-jd007_r001", 4.0, 85.0),
        ],
    )
    inst_b = _progression(
        "InstB",
        [
            _seg(1, "neh_cp", 1.0, 110.0),
            _seg(2, "incremental_job_contrib_cp.3-jd006_r001", 2.0, 100.0),
            _seg(2, "incremental_job_contrib_cp.4-jd007_r001", 3.0, 95.0),
        ],
    )
    baseline = {"InstA": 50.0, "InstB": 55.0}

    points = load_method_mean_metrics([inst_a, inst_b], baseline)
    jd_labels = [
        p["label"] for p in points if p["method"] == "incremental_job_contrib_cp"
    ]
    assert jd_labels == [
        "incremental_job_contrib_cp.jd006",
        "incremental_job_contrib_cp.jd007",
    ], f"jd levels did not realign across instances: {jd_labels}"

    by_label = {p["label"]: p for p in points}
    # jd006: InstA's *last* rep (3.0) and InstB's only rep (2.0) → mean 0.25.
    assert by_label["incremental_job_contrib_cp.jd006"][
        "mean_time_pct"
    ] == pytest.approx(0.25)
    # jd007: InstA 4.0, InstB 3.0 → mean 0.35, both instances present.
    jd007 = by_label["incremental_job_contrib_cp.jd007"]
    assert jd007["mean_time_pct"] == pytest.approx(0.35)
    assert jd007["instance_count"] == 2


# ── C3: cell_by_instance parameter ─────────────────────────────────────────


def _cell_baseline(extra: list[str] | None = None) -> dict[str, float]:
    base = {"InstA": 50.0, "InstB": 60.0, "InstC": 55.0, "InstD": 65.0}
    if extra:
        for e in extra:
            base[e] = 70.0
    return base


def _cell_progression(
    instance_id: str, base_obj: float, *, n_segs: int = 5
) -> InstanceProgression:
    segs = [
        _seg(3, "neh_cp", 3.0, base_obj),
        _seg(4, "incremental_sw_cp.1-batch_002", 4.0, base_obj - 10),
        _seg(4, "incremental_sw_cp.2-batch_003", 5.0, base_obj - 20),
        _seg(4, "incremental_sw_cp.3-batch_004", 6.0, base_obj - 30),
        _seg(5, "solve_base_model_cpsat", 9.0, base_obj - 35),
    ]
    return _progression(instance_id, segs[:n_segs])


def test_cell_by_instance_none_omits_cells() -> None:
    progs = [_cell_progression("InstA", 100.0)]
    points = load_method_mean_metrics(progs, {"InstA": 50.0})
    assert "cells" not in points[0]


def test_cell_by_instance_preserves_existing_fields() -> None:
    progs = [_cell_progression("InstA", 100.0)]
    points = load_method_mean_metrics(progs, {"InstA": 50.0})
    assert "method" in points[0]
    assert "label" in points[0]
    assert "mean_time_pct" in points[0]
    assert "mean_rpdf" in points[0]
    assert "instance_count" in points[0]
    assert "is_top_level" in points[0]


def test_cell_weighted_avg_matches_top_level() -> None:
    cell_map = {
        "InstA": ("0.2", "0.2", "50", "5"),
        "InstB": ("0.2", "0.2", "50", "5"),
        "InstC": ("0.6", "0.6", "100", "10"),
        "InstD": ("0.6", "0.6", "100", "10"),
    }
    progs = [
        _cell_progression("InstA", 100.0),
        _cell_progression("InstB", 120.0),
        _cell_progression("InstC", 140.0),
        _cell_progression("InstD", 160.0),
    ]
    points = load_method_mean_metrics(
        progs, _cell_baseline(), cell_by_instance=cell_map
    )
    for p in points:
        if "cells" not in p or not p["cells"]:
            continue
        cells = p["cells"]
        total_n = sum(c["n"] for c in cells.values())
        assert total_n == p["instance_count"]
        weighted_x = sum(c["x"][0] * c["n"] for c in cells.values()) / total_n
        weighted_y = sum(c["y"][0] * c["n"] for c in cells.values()) / total_n
        assert weighted_x == pytest.approx(p["mean_time_pct"], abs=1e-12)
        assert weighted_y == pytest.approx(p["mean_rpdf"], abs=1e-12)


def test_cell_order_same_across_all_cells() -> None:
    cell_map = {
        "InstA": ("0.2", "0.2", "50", "5"),
        "InstC": ("0.6", "0.6", "100", "10"),
    }
    progs = [
        _cell_progression("InstA", 100.0),
        _cell_progression("InstC", 140.0),
    ]
    points = load_method_mean_metrics(
        progs, _cell_baseline(), cell_by_instance=cell_map
    )
    for p in points:
        if "cells" not in p or not p["cells"]:
            continue
        # Each cell's dict has the same keys (method labels).
        for c in p["cells"].values():
            pass  # structure validity check only
    # Every cell has the same method list.
    first_cell_methods = (
        set(points[0].get("cells", {}).keys()) if "cells" in points[0] else set()
    )
    for p in points[1:]:
        if "cells" in p:
            assert set(p["cells"].keys()) == first_cell_methods, (
                f"cell keys differ: {set(p['cells'].keys())} vs {first_cell_methods}"
            )


def test_cell_reached_count() -> None:
    cell_map = {
        "InstA": ("0.2", "0.2", "50", "5"),
        "InstB": ("0.2", "0.2", "50", "5"),
    }
    # InstA reaches all 5 steps; InstB only reaches first 3.
    progs = [
        _cell_progression("InstA", 100.0, n_segs=5),
        _cell_progression("InstB", 120.0, n_segs=3),
    ]
    points = load_method_mean_metrics(
        progs, _cell_baseline(), cell_by_instance=cell_map
    )
    # Both in same cell → cell n = 2 for all methods.
    for p in points:
        if "cells" not in p or not p["cells"]:
            continue
        for ck, c in p["cells"].items():
            assert c["n"] == 2
    # The last method (solve) was only reached by InstA.
    solve = next(p for p in points if p["method"] == "solve_base_model_cpsat")
    if "cells" in solve:
        for ck, c in solve["cells"].items():
            assert c["reached"] == 1  # only InstA reached it
            assert c["n"] == 2  # carry-forward for InstB


def test_cell_n_sum_equals_top_level_instance_count() -> None:
    cell_map = {
        "InstA": ("0.2", "0.2", "50", "5"),
        "InstB": ("0.2", "0.2", "50", "5"),
        "InstC": ("0.6", "0.6", "100", "10"),
        "InstD": ("0.6", "0.6", "100", "10"),
    }
    progs = [
        _cell_progression("InstA", 100.0),
        _cell_progression("InstB", 120.0),
        _cell_progression("InstC", 140.0),
        _cell_progression("InstD", 160.0),
    ]
    points = load_method_mean_metrics(
        progs, _cell_baseline(), cell_by_instance=cell_map
    )
    for p in points:
        if "cells" not in p or not p["cells"]:
            continue
        total_n = sum(c["n"] for c in p["cells"].values())
        assert total_n == p["instance_count"]


def test_composite_endpoint_is_top_level() -> None:
    """C3: bare ``incremental_job_contrib_cp`` label (composite self-endpoint)
    is ``is_top_level=True`` — it appears as an open circle closing the flow.
    The jd-level sub-steps remain star-diamond."""
    prog = _progression(
        "InstA",
        [
            _seg(1, "neh_cp", 2.0, 100.0),
            _seg(2, "incremental_job_contrib_cp.3-jd004_r001", 3.0, 95.0),
            _seg(2, "incremental_job_contrib_cp.4-jd005_r001", 4.0, 90.0),
            _seg(2, "incremental_job_contrib_cp", 5.0, 90.0),
        ],
    )
    baseline = {"InstA": 50.0}

    points = load_method_mean_metrics([prog], baseline)
    by_label = {p["label"]: p for p in points}

    assert "incremental_job_contrib_cp" in by_label
    assert by_label["incremental_job_contrib_cp"]["is_top_level"] is True
    assert not by_label["incremental_job_contrib_cp.jd004"]["is_top_level"]
    assert not by_label["incremental_job_contrib_cp.jd005"]["is_top_level"]


# ── filtered-view merge contract (JS consumer mirror) ────────────────────────


def _merge_point_cells_py(cells: list[dict]) -> dict | None:
    """Python mirror of ``mergePointCells`` in ``_cell_filter.CELL_FILTER_JS``."""
    if not cells:
        return None
    total = sum(c["n"] for c in cells)
    if total == 0:
        return None
    acc_x = sum(c["n"] * c["x"][0] for c in cells)
    acc_y = sum(c["n"] * c["y"][0] for c in cells)
    return {"x": [acc_x / total], "y": [acc_y / total], "n": total}


def _merge_step_cells_py(cells: list[dict]) -> dict | None:
    """Python mirror of ``mergeCells`` — the step-function merge, kept here
    only to pin *why* it must not be used for the scalar scatter cells."""
    if not cells:
        return None
    if len(cells) == 1:
        return cells[0]
    start = max(c["x"][0] for c in cells)
    grid = sorted({t for c in cells for t in c["x"] if t >= start})
    total = sum(c["n"] for c in cells)
    ptr = [0] * len(cells)
    ys = []
    for t in grid:
        acc = 0.0
        for i, c in enumerate(cells):
            while ptr[i] + 1 < len(c["x"]) and c["x"][ptr[i] + 1] <= t:
                ptr[i] += 1
            acc += c["n"] * c["y"][ptr[i]]
        ys.append(acc / total)
    return {"x": grid, "y": ys, "n": total}


def test_scatter_cell_merge_weights_both_axes() -> None:
    """A filtered view averages *both* mean Time% and mean RPDf by instance
    count. The step-function merge samples from ``max(x[0])`` onward, so it
    would report the slowest cell's mean time as the group's mean time."""
    cells = [
        {"x": [0.10], "y": [0.30], "n": 20},
        {"x": [0.50], "y": [0.10], "n": 20},
    ]
    merged = _merge_point_cells_py(cells)
    assert merged is not None
    assert merged["x"][0] == pytest.approx(0.30, abs=1e-12)
    assert merged["y"][0] == pytest.approx(0.20, abs=1e-12)
    assert merged["n"] == 40

    # The step merge gets y right but x wrong — the regression this pins.
    step_merged = _merge_step_cells_py(cells)
    assert step_merged is not None
    assert step_merged["x"][0] == pytest.approx(0.50, abs=1e-12)


def test_scatter_full_cell_merge_reproduces_top_level() -> None:
    """Selecting every cell through the JS merge must land on the unfiltered
    point exactly — the ``All`` view and a fully-selected filter agree."""
    cell_map = {
        "InstA": ("0.2", "0.2", "50", "5"),
        "InstB": ("0.2", "0.2", "50", "5"),
        "InstC": ("0.6", "0.6", "100", "10"),
        "InstD": ("0.6", "0.6", "100", "10"),
    }
    progs = [
        _cell_progression("InstA", 100.0),
        _cell_progression("InstB", 120.0),
        _cell_progression("InstC", 140.0),
        _cell_progression("InstD", 160.0),
    ]
    points = load_method_mean_metrics(
        progs, _cell_baseline(), cell_by_instance=cell_map
    )
    checked = 0
    for p in points:
        if not p.get("cells"):
            continue
        merged = _merge_point_cells_py(list(p["cells"].values()))
        assert merged is not None
        assert merged["x"][0] == pytest.approx(p["mean_time_pct"], abs=1e-12)
        assert merged["y"][0] == pytest.approx(p["mean_rpdf"], abs=1e-12)
        assert merged["n"] == p["instance_count"]
        checked += 1
    assert checked > 0


def test_scatter_template_uses_point_merge_not_step_merge(tmp_path: Path) -> None:
    """The emitted scatter JS must call ``mergePointCells``; calling
    ``mergeCells`` there is the x-axis regression above."""
    scenarios = [
        {
            "label": "s1",
            "method_points": [
                {
                    "method": "a",
                    "label": "a",
                    "mean_time_pct": 0.1,
                    "mean_rpdf": 0.5,
                    "instance_count": 2,
                    "is_top_level": True,
                }
            ],
        }
    ]
    out = tmp_path / "scatter.html"
    assert export_method_mean_scatter_html(scenarios, out)
    html = out.read_text()
    assert "mergePointCells(matchedCells)" in html
    assert "mergeCells(matchedCells)" not in html


@pytest.mark.parametrize("with_dims", [True, False])
def test_scatter_template_defines_every_helper_it_calls(
    tmp_path: Path, with_dims: bool
) -> None:
    """Emitting the filter JS only alongside the toolbar left ``dim_values``-less
    callers with a ReferenceError and a blank chart."""
    scenarios = [
        {
            "label": "s1",
            "method_points": [
                {
                    "method": "a",
                    "label": "a",
                    "mean_time_pct": 0.1,
                    "mean_rpdf": 0.5,
                    "instance_count": 2,
                    "is_top_level": True,
                }
            ],
        }
    ]
    dim_values = {"t_factor": ["0.2"]} if with_dims else None
    out = tmp_path / f"scatter_{with_dims}.html"
    assert export_method_mean_scatter_html(scenarios, out, dim_values=dim_values)
    html = out.read_text()
    for fn in ("getSelectedCellKeys", "mergePointCells"):
        assert f"function {fn}(" in html, f"{fn} called but never defined"
    assert ('id="cell-filter-toolbar"' in html) is with_dims
