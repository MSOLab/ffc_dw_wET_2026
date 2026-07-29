from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ffc_ddw_sum_et.report.multi_scenario_method_chart import (
    _ALL_SERIES_MAX_POINTS,
    _CELL_SERIES_MAX_POINTS,
    _build_scenario_mean_series,
    export_multi_scenario_method_rpdf_comparison_html,
)


def _endpoint_row(
    instance_id: str,
    subroutine_name: str,
    norm_time: float,
    rpd_f: float,
    obj_value: float,
    t_factor: float = 0.2,
    r_factor: float = 0.2,
    job_cnt: int = 50,
    stage_cnt: int = 5,
    subroutine_order: int = 1,
) -> dict[str, Any]:
    return {
        "instance_id": instance_id,
        "subroutine_name": subroutine_name,
        "norm_time": norm_time,
        "rpd_f": rpd_f,
        "obj_value": obj_value,
        "t_factor": t_factor,
        "r_factor": r_factor,
        "job_cnt": job_cnt,
        "stage_cnt": stage_cnt,
        "subroutine_order": subroutine_order,
    }


def _make_endpoint_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ── 11. payload structure ────────────────────────────────────────────────────


def test_payload_has_all_and_no_step_fields() -> None:
    """C4-11: payload has `all`/`cells` and no `step_x`/`step_y`."""
    ep = _make_endpoint_df(
        [
            _endpoint_row("A", "step_alpha", 0.1, 0.05, 100.0),
            _endpoint_row("A", "step_beta", 0.5, 0.02, 95.0),
            _endpoint_row("B", "step_alpha", 0.2, 0.06, 110.0),
            _endpoint_row("B", "step_beta", 0.6, 0.03, 105.0),
        ]
    )
    cell_map = {
        "A": ("0.2", "0.2", "50", "5"),
        "B": ("0.2", "0.2", "50", "5"),
    }
    result = _build_scenario_mean_series("test", ep, None, cell_by_instance=cell_map)
    assert result is not None
    assert "all" in result
    assert "x" in result["all"]
    assert "y" in result["all"]
    assert "step_x" not in result
    assert "step_y" not in result


# ── 12. mergeCells Python mirror ─────────────────────────────────────────────


def _step_value_at(xs: list[float], ys: list[float], t: float) -> float | None:
    """Value of the piecewise-constant series ``(xs, ys)`` at ``t``."""
    val: float | None = None
    for x, y in zip(xs, ys, strict=True):
        if x <= t:
            val = y
        else:
            break
    return val


def _merge_cells_py(
    cell_tuples: list[tuple[int, list[float], list[float]]],
) -> dict[str, Any] | None:
    if not cell_tuples:
        return None
    if len(cell_tuples) == 1:
        n, xs, ys = cell_tuples[0]
        return {"x": xs, "y": ys, "n": n}
    start = max(c[1][0] for c in cell_tuples)
    grid = sorted({t for _, xs, _ in cell_tuples for t in xs if t >= start})
    total = sum(c[0] for c in cell_tuples)
    ptr = [0] * len(cell_tuples)
    y = []
    for t in grid:
        acc = 0.0
        for i, (n, xs, ys) in enumerate(cell_tuples):
            while ptr[i] + 1 < len(xs) and xs[ptr[i] + 1] <= t:
                ptr[i] += 1
            acc += n * ys[ptr[i]]
        y.append(acc / total)
    return {"x": grid, "y": y, "n": total}


def test_merge_cells_matches_all_series(tmp_path: Path) -> None:
    """C4-12: weighted merge of all cells == all.x/all.y within quantum."""
    ep_rows = []
    cell_map: dict[str, tuple[str, ...]] = {}
    for i, (t, r, n, c) in enumerate(
        [
            (0.2, 0.2, 50, 5),
            (0.2, 0.6, 100, 10),
            (0.6, 0.2, 50, 5),
        ]
    ):
        ins_id = f"Inst{i}"
        cell_map[ins_id] = (f"{t:.1f}", f"{r:.1f}", str(n), str(c))
        ep_rows.append(
            _endpoint_row(
                ins_id,
                "step_alpha",
                0.1,
                0.05 * (i + 1),
                100.0 + i * 10,
                t_factor=t,
                r_factor=r,
                job_cnt=n,
                stage_cnt=c,
            )
        )
        ep_rows.append(
            _endpoint_row(
                ins_id,
                "step_beta",
                0.5,
                0.02 * (i + 1),
                90.0 + i * 10,
                t_factor=t,
                r_factor=r,
                job_cnt=n,
                stage_cnt=c,
                subroutine_order=2,
            )
        )
    ep = _make_endpoint_df(ep_rows)
    result = _build_scenario_mean_series("test", ep, None, cell_by_instance=cell_map)
    assert result is not None
    assert "cells" in result
    cells = result["cells"]
    assert len(cells) == 3

    # Merge all cells weightedly
    cell_list = [(c["n"], c["x"], c["y"]) for c in cells.values()]
    merged = _merge_cells_py(cell_list)
    assert merged is not None

    all_x = result["all"]["x"]
    all_y = result["all"]["y"]

    # The whole curve must agree, not just the endpoints: both are step
    # functions, so evaluate each on the union grid and compare pointwise.
    # Comparing only ``[0]`` is vacuous — ``mergeCells`` starts at
    # ``max(cell starts)``, which equals the All start by construction.
    assert merged["x"][0] == pytest.approx(all_x[0], abs=1e-9)
    assert merged["x"][-1] == pytest.approx(all_x[-1], abs=1e-9)
    grid = sorted(set(merged["x"]) | set(all_x))
    for t in grid:
        got = _step_value_at(merged["x"], merged["y"], t)
        want = _step_value_at(all_x, all_y, t)
        # Tolerance covers the cell decimation (M=200) quantum only.
        assert got == pytest.approx(want, abs=1e-4), f"diverged at t={t}"


# ── 13. template JS markers ──────────────────────────────────────────────────


def test_template_contains_required_js_markers(tmp_path: Path) -> None:
    """C4-13: template contains mergeCells, buildStepPath, 4 select ids,
    and TRACES_PER_SCENARIO == 3."""
    # Render an empty-like payload to exercise template.
    ep = _make_endpoint_df(
        [
            _endpoint_row("A", "step_alpha", 0.1, 0.05, 100.0),
            _endpoint_row("A", "step_beta", 0.5, 0.02, 95.0),
        ]
    )
    cell_map = {"A": ("0.2", "0.2", "50", "5")}
    dim_values = {
        "t_factor": ["0.2"],
        "r_factor": ["0.2"],
        "job_cnt": ["50"],
        "stage_cnt": ["5"],
    }
    out = tmp_path / "test_js_markers.html"
    ok = export_multi_scenario_method_rpdf_comparison_html(
        [{"label": "test", "endpoint_df": ep, "raw_progression_df": None}],
        out,
        cell_by_instance=cell_map,
        dim_values=dim_values,
    )
    assert ok
    html = out.read_text(encoding="utf-8")

    assert "function mergeCells" in html
    assert "function buildStepPath" in html
    assert 'id="filter-t_factor"' in html
    assert 'id="filter-r_factor"' in html
    assert 'id="filter-job_cnt"' in html
    assert 'id="filter-stage_cnt"' in html
    assert re.search(r"TRACES_PER_SCENARIO\s*=\s*3", html)


# ── 14. cell start time ──────────────────────────────────────────────────────


def test_single_cell_start_matches_max_first_time() -> None:
    """C4-14: a single cell's start = its max(first_times).
    With one cell this equals all.x[0]."""
    ep = _make_endpoint_df(
        [
            _endpoint_row("A", "step_alpha", 0.1, 0.05, 100.0),
            _endpoint_row("A", "step_beta", 0.5, 0.02, 95.0),
            _endpoint_row("B", "step_alpha", 0.3, 0.06, 110.0),
            _endpoint_row("B", "step_beta", 0.7, 0.03, 105.0),
        ]
    )
    cell_map = {
        "A": ("0.2", "0.2", "50", "5"),
        "B": ("0.2", "0.2", "50", "5"),
    }
    result = _build_scenario_mean_series("test", ep, None, cell_by_instance=cell_map)
    assert result is not None
    # Single cell's first x should match all.x[0]
    cells = result.get("cells", {})
    if cells:
        c = next(iter(cells.values()))
        assert abs(c["x"][0] - result["all"]["x"][0]) < 1e-4


# ── 15. size budget ──────────────────────────────────────────────────────────


def test_all_series_length_within_budget() -> None:
    """C4-15: all series length <= _ALL_SERIES_MAX_POINTS."""
    ep = _make_endpoint_df(
        [
            _endpoint_row("A", "step_alpha", 0.1, 0.05, 100.0),
            _endpoint_row("A", "step_beta", 0.5, 0.02, 95.0),
        ]
    )
    result = _build_scenario_mean_series("test", ep, None)
    assert result is not None
    assert len(result["all"]["x"]) <= _ALL_SERIES_MAX_POINTS
    assert len(result["all"]["y"]) <= _ALL_SERIES_MAX_POINTS


def test_cell_series_length_within_budget() -> None:
    """C4-15: each cell series length <= _CELL_SERIES_MAX_POINTS."""
    ep = _make_endpoint_df(
        [
            _endpoint_row(
                "A",
                "step_alpha",
                0.1,
                0.05,
                100.0,
                t_factor=0.2,
                r_factor=0.2,
                job_cnt=50,
                stage_cnt=5,
            ),
            _endpoint_row(
                "A",
                "step_beta",
                0.5,
                0.02,
                95.0,
                t_factor=0.2,
                r_factor=0.2,
                job_cnt=50,
                stage_cnt=5,
                subroutine_order=2,
            ),
            _endpoint_row(
                "B",
                "step_alpha",
                0.2,
                0.06,
                110.0,
                t_factor=0.6,
                r_factor=0.6,
                job_cnt=100,
                stage_cnt=10,
            ),
            _endpoint_row(
                "B",
                "step_beta",
                0.6,
                0.03,
                105.0,
                t_factor=0.6,
                r_factor=0.6,
                job_cnt=100,
                stage_cnt=10,
                subroutine_order=2,
            ),
        ]
    )
    cell_map = {
        "A": ("0.2", "0.2", "50", "5"),
        "B": ("0.6", "0.6", "100", "10"),
    }
    result = _build_scenario_mean_series("test", ep, None, cell_by_instance=cell_map)
    assert result is not None
    cells = result.get("cells", {})
    for ck, c in cells.items():
        assert len(c["x"]) <= _CELL_SERIES_MAX_POINTS, f"cell {ck} x len exceeds budget"
        assert len(c["y"]) <= _CELL_SERIES_MAX_POINTS, f"cell {ck} y len exceeds budget"


# ── 16. guide_x weighted average ─────────────────────────────────────────────


def test_guide_x_weighted_avg_matches_all(tmp_path: Path) -> None:
    """C4-16: weighted average of cell guide_x matches all.guide_x."""
    ep_rows = []
    cell_map: dict[str, tuple[str, ...]] = {}
    for i, (t, r, n, c) in enumerate(
        [
            (0.2, 0.2, 50, 5),
            (0.6, 0.6, 100, 10),
        ]
    ):
        ins_id = f"ZInst{i}"
        cell_map[ins_id] = (f"{t:.1f}", f"{r:.1f}", str(n), str(c))
        ep_rows.append(
            _endpoint_row(
                ins_id,
                "step_alpha",
                0.1,
                0.05,
                100.0,
                t_factor=t,
                r_factor=r,
                job_cnt=n,
                stage_cnt=c,
            )
        )
        ep_rows.append(
            _endpoint_row(
                ins_id,
                "step_beta",
                0.5,
                0.02,
                90.0,
                t_factor=t,
                r_factor=r,
                job_cnt=n,
                stage_cnt=c,
                subroutine_order=2,
            )
        )
    ep = _make_endpoint_df(ep_rows)
    result = _build_scenario_mean_series("test", ep, None, cell_by_instance=cell_map)
    assert result is not None
    all_guide = result["all"]["guide_x"]
    cells = result.get("cells", {})
    if not cells:
        pytest.skip("no cells generated")
    total_n = sum(c["n"] for c in cells.values())
    for gi in range(len(all_guide)):
        weighted = sum(c["n"] * c["guide_x"][gi] for c in cells.values()) / total_n
        assert abs(weighted - all_guide[gi]) < 1e-4


# ── JS helper availability, guide-shape sourcing, per-cell cost ──────────────


@pytest.mark.parametrize("with_dims", [True, False])
def test_flow_template_defines_every_helper_it_calls(
    tmp_path: Path, with_dims: bool
) -> None:
    """Emitting the filter JS only alongside the toolbar left callers that pass
    no ``dim_values`` (scripts/build_cross_run_flow_chart.py) with a
    ReferenceError and a blank chart."""
    ep = _make_endpoint_df(
        [
            _endpoint_row("A", "step_alpha", 0.1, 0.05, 100.0),
            _endpoint_row("A", "step_beta", 0.5, 0.02, 95.0),
        ]
    )
    out = tmp_path / f"flow_{with_dims}.html"
    assert export_multi_scenario_method_rpdf_comparison_html(
        [{"label": "test", "endpoint_df": ep, "raw_progression_df": None}],
        out,
        dim_values={"t_factor": ["0.2"]} if with_dims else None,
    )
    html = out.read_text()
    for fn in ("getSelectedCellKeys", "buildStepPath", "mergeCells"):
        assert f"function {fn}(" in html, f"{fn} called but never defined"
    assert ('id="cell-filter-toolbar"' in html) is with_dims


def test_guide_shapes_track_the_rendered_filter(tmp_path: Path) -> None:
    """The dotted vertical guides must sit on the same x as the guide markers.
    Reading the All-only ``vertical_guides`` payload field left the lines
    behind whenever a filter moved the markers."""
    ep = _make_endpoint_df(
        [
            _endpoint_row("A", "step_alpha", 0.1, 0.05, 100.0),
            _endpoint_row("A", "step_beta", 0.5, 0.02, 95.0),
        ]
    )
    cell_map = {"A": ("0.2", "0.2", "50", "5")}
    result = _build_scenario_mean_series("test", ep, None, cell_by_instance=cell_map)
    assert result is not None
    assert "vertical_guides" not in result

    out = tmp_path / "flow.html"
    assert export_multi_scenario_method_rpdf_comparison_html(
        [{"label": "test", "endpoint_df": ep, "raw_progression_df": None}],
        out,
        cell_by_instance=cell_map,
        dim_values={"t_factor": ["0.2"]},
    )
    html = out.read_text()
    assert "trace.vertical_guides" not in html
    assert "currentGuideX[idx] = guideX;" in html
    assert "(currentGuideX[idx] || [])" in html


def test_cell_guide_x_uses_scenario_level_subroutine_order() -> None:
    """Cell ``guide_x`` is indexed positionally against the shared
    ``all.guide_text`` by the JS merge, so it must follow that exact order."""
    rows = []
    cell_map: dict[str, tuple[str, ...]] = {}
    for i, ins in enumerate(("A", "B")):
        cell_map[ins] = (f"{0.2 + 0.4 * i:.1f}", "0.2", "50", "5")
        rows.append(_endpoint_row(ins, "step_alpha", 0.1 + 0.1 * i, 0.05, 100.0))
        rows.append(
            _endpoint_row(
                ins, "step_beta", 0.5 + 0.1 * i, 0.02, 95.0, subroutine_order=2
            )
        )
    result = _build_scenario_mean_series(
        "test", _make_endpoint_df(rows), None, cell_by_instance=cell_map
    )
    assert result is not None
    guide_text = result["all"]["guide_text"]
    assert guide_text == ["step_alpha", "step_beta"]
    for cell in result["cells"].values():
        assert len(cell["guide_x"]) == len(guide_text)
        # step_alpha always ends before step_beta within a cell.
        assert cell["guide_x"][0] < cell["guide_x"][1]

    # Weighted merge of the cell guides reproduces the All guide positions.
    total = sum(c["n"] for c in result["cells"].values())
    for gi, want in enumerate(result["all"]["guide_x"]):
        got = sum(c["n"] * c["guide_x"][gi] for c in result["cells"].values()) / total
        assert got == pytest.approx(want, abs=1e-6)


def test_cell_series_do_not_refilter_progressions_per_cell(monkeypatch) -> None:
    """The per-instance progression filter is the pipeline's dominant cost.
    Building it inside the cell loop made it run once per (cell, instance) —
    73x the work at 72 cells."""
    from ffc_ddw_sum_et.report import multi_scenario_method_chart as mod

    calls = {"n": 0}
    original = mod.keep_strict_global_improvements_or_endpoints

    def counting(df):
        calls["n"] += 1
        return original(df)

    monkeypatch.setattr(mod, "keep_strict_global_improvements_or_endpoints", counting)

    ep_rows, prog_rows = [], []
    cell_map: dict[str, tuple[str, ...]] = {}
    t_vals, r_vals = [0.2, 0.4, 0.6], [0.2, 0.6, 1.0]
    n_instances = 9
    for i in range(n_instances):
        ins = f"Inst{i}"
        t, r = t_vals[i % 3], r_vals[(i // 3) % 3]
        cell_map[ins] = (f"{t:.1f}", f"{r:.1f}", "50", "5")
        for s, (name, order) in enumerate((("step_alpha", 1), ("step_beta", 2))):
            ep_rows.append(
                _endpoint_row(
                    ins,
                    name,
                    0.1 + 0.4 * s,
                    0.05 - 0.01 * s,
                    100.0 - s,
                    t_factor=t,
                    r_factor=r,
                    subroutine_order=order,
                )
            )
        for p in range(4):
            prog_rows.append(
                {
                    **_endpoint_row(
                        ins,
                        "step_alpha",
                        0.1 * (p + 1),
                        0.06 - 0.01 * p,
                        100.0 - p,
                        t_factor=t,
                        r_factor=r,
                    ),
                    "global_sec": float(p),
                    "call_index": p,
                }
            )

    result = _build_scenario_mean_series(
        "test",
        _make_endpoint_df(ep_rows),
        _make_endpoint_df(prog_rows),
        cell_by_instance=cell_map,
    )
    assert result is not None
    assert len(result["cells"]) == 9
    assert calls["n"] == n_instances, (
        f"progression filter ran {calls['n']}x for {n_instances} instances "
        f"and {len(result['cells'])} cells — it must run once per instance"
    )
