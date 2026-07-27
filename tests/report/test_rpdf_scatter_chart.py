"""Unit tests for the strict-global-improvement filter that drives the
per-scenario scatter chart's marker set."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from ffc_ddw_sum_et.report.rpdf_scatter_chart import (
    _build_html_payload,
    export_method_rpdf_scatter_html,
)
from ffc_ddw_sum_et.report.trajectory_utils import (
    keep_strict_global_improvements_or_endpoints,
)


def _make_progression_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_filter_drops_intra_call_plateau_keeps_endpoint() -> None:
    """Within one call, plateau points are dropped; the endpoint is kept
    even when it equals the running min."""
    df = _make_progression_df(
        [
            {"call_index": 1, "norm_time": 0.1, "rpd_f": 0.10, "tag": "a"},
            {"call_index": 1, "norm_time": 0.2, "rpd_f": 0.10, "tag": "b"},
            {"call_index": 1, "norm_time": 0.3, "rpd_f": 0.10, "tag": "c"},
            {"call_index": 1, "norm_time": 0.4, "rpd_f": 0.08, "tag": "d"},
        ]
    )
    out = keep_strict_global_improvements_or_endpoints(df)
    assert out["tag"].tolist() == ["a", "d"]


def test_filter_keeps_endpoint_even_when_call_does_not_improve_global() -> None:
    """A whole call (call_index=2) sitting above the global min from the
    prior call (call_index=1) shows only its endpoint, not its intra-call
    fluctuations."""
    df = _make_progression_df(
        [
            {"call_index": 1, "norm_time": 0.1, "rpd_f": 0.05, "tag": "1a"},
            {"call_index": 2, "norm_time": 0.2, "rpd_f": 0.20, "tag": "2a"},
            {"call_index": 2, "norm_time": 0.3, "rpd_f": 0.15, "tag": "2b"},
            {"call_index": 2, "norm_time": 0.4, "rpd_f": 0.18, "tag": "2c"},
        ]
    )
    out = keep_strict_global_improvements_or_endpoints(df)
    assert out["tag"].tolist() == ["1a", "2c"]


def test_filter_keeps_strict_global_improvements_across_calls() -> None:
    """Strict global improvements survive even when interleaved with
    plateaus; per-call endpoints are always kept."""
    df = _make_progression_df(
        [
            {"call_index": 1, "norm_time": 0.1, "rpd_f": 0.30, "tag": "1a"},
            {"call_index": 1, "norm_time": 0.2, "rpd_f": 0.30, "tag": "1b"},
            {"call_index": 2, "norm_time": 0.3, "rpd_f": 0.25, "tag": "2a"},
            {"call_index": 2, "norm_time": 0.4, "rpd_f": 0.20, "tag": "2b"},
            {"call_index": 2, "norm_time": 0.5, "rpd_f": 0.20, "tag": "2c"},
        ]
    )
    out = keep_strict_global_improvements_or_endpoints(df)
    assert out["tag"].tolist() == ["1a", "1b", "2a", "2b", "2c"]


def test_html_payload_marker_y_values_are_unique_per_instance() -> None:
    """End-to-end: a flat-but-non-improving call leaves only its endpoint
    in the marker payload, so marker y-values are unique."""
    common = {"t_factor": 0.2, "r_factor": 0.2, "instance_id": "I0"}
    endpoint_df = pd.DataFrame(
        [
            {
                **common,
                "subroutine_name": "step_a",
                "call_index": 1,
                "norm_time": 0.10,
                "rpd_f": 0.05,
            },
            {
                **common,
                "subroutine_name": "step_b",
                "call_index": 2,
                "norm_time": 0.40,
                "rpd_f": 0.05,
            },
        ]
    )
    raw_progression_df = pd.DataFrame(
        [
            {
                **common,
                "subroutine_name": "step_a",
                "call_index": 1,
                "norm_time": 0.10,
                "rpd_f": 0.05,
            },
            {
                **common,
                "subroutine_name": "step_b",
                "call_index": 2,
                "norm_time": 0.20,
                "rpd_f": 0.30,
            },
            {
                **common,
                "subroutine_name": "step_b",
                "call_index": 2,
                "norm_time": 0.30,
                "rpd_f": 0.20,
            },
            {
                **common,
                "subroutine_name": "step_b",
                "call_index": 2,
                "norm_time": 0.40,
                "rpd_f": 0.05,
            },
        ]
    )
    payload = _build_html_payload(endpoint_df, raw_progression_df)
    series = payload["raw_series"][0]
    assert series["x"] == [0.10, 0.40]
    assert series["y"] == [0.05, 0.05]
    assert series["text"] == ["step_a", "step_b"]


def test_html_payload_drops_flat_intra_call_cluster() -> None:
    """Reproduces the user's bug: 4 intra-call points whose rpd_f never
    beats the global best collapse to just the call's endpoint."""
    common = {"t_factor": 0.2, "r_factor": 0.2, "instance_id": "I0"}
    endpoint_df = pd.DataFrame(
        [
            {
                **common,
                "subroutine_name": "mcf_lb",
                "call_index": 1,
                "norm_time": 0.10,
                "rpd_f": 0.14,
            },
            {
                **common,
                "subroutine_name": "neh_cp",
                "call_index": 2,
                "norm_time": 0.50,
                "rpd_f": 0.18,
            },
        ]
    )
    raw_progression_df = pd.DataFrame(
        [
            {
                **common,
                "subroutine_name": "mcf_lb",
                "call_index": 1,
                "norm_time": 0.10,
                "rpd_f": 0.14,
            },
            {
                **common,
                "subroutine_name": "neh_cp",
                "call_index": 2,
                "norm_time": 0.20,
                "rpd_f": 0.22,
            },
            {
                **common,
                "subroutine_name": "neh_cp",
                "call_index": 2,
                "norm_time": 0.30,
                "rpd_f": 0.20,
            },
            {
                **common,
                "subroutine_name": "neh_cp",
                "call_index": 2,
                "norm_time": 0.40,
                "rpd_f": 0.19,
            },
            {
                **common,
                "subroutine_name": "neh_cp",
                "call_index": 2,
                "norm_time": 0.50,
                "rpd_f": 0.18,
            },
        ]
    )
    payload = _build_html_payload(endpoint_df, raw_progression_df)
    series = payload["raw_series"][0]
    assert series["x"] == [0.10, 0.50]
    assert series["text"] == ["mcf_lb", "neh_cp"]


# ── C3 hover unification ────────────────────────────────────────────────


def _make_minimal_endpoint_df() -> pd.DataFrame:
    common = {"t_factor": 0.2, "r_factor": 0.2, "instance_id": "I0"}
    return pd.DataFrame(
        [
            {
                **common,
                "subroutine_name": "step_a",
                "call_index": 1,
                "norm_time": 0.10,
                "rpd_f": 0.05,
            },
            {
                **common,
                "subroutine_name": "step_b",
                "call_index": 2,
                "norm_time": 0.50,
                "rpd_f": -0.10,
            },
        ]
    )


def test_hover_uses_3_percent(tmp_path: Path) -> None:
    """C3-8: rpdf scatter hover is .3% for x and y, not .4% or .1%."""
    endpoint_df = _make_minimal_endpoint_df()
    out_path = tmp_path / "test_scatter.html"
    ok = export_method_rpdf_scatter_html(endpoint_df, out_path)
    assert ok
    html = out_path.read_text(encoding="utf-8")
    hovertemplates = re.findall(r"hovertemplate:.*?extra>", html, re.S)
    for ht in hovertemplates:
        assert re.search(r"%\{x:\.3%}", ht), f"missing %{{x:.3%}} in: {ht!r}"
        if "RPDf" in ht:
            assert re.search(r"%\{y:\.3%}", ht), f"missing %{{y:.3%}} in: {ht!r}"
        assert not re.search(r"%\{[xy]:\.(?:4|[12])%}", ht), f"stale format in: {ht!r}"


def test_tickformat_stays_at_1_percent(tmp_path: Path) -> None:
    """C3-9: rpdf scatter tickformat stays at .1%, not .3%."""
    endpoint_df = _make_minimal_endpoint_df()
    out_path = tmp_path / "test_scatter.html"
    ok = export_method_rpdf_scatter_html(endpoint_df, out_path)
    assert ok
    html = out_path.read_text(encoding="utf-8")
    assert re.search(r'[xy]TickFormat\s*=\s*"\.1%"', html), (
        "tickformat regressed from .1%"
    )


def test_hover_format_injection_leaves_payload_untouched(tmp_path: Path) -> None:
    """C3-3: injecting the hover format must not rewrite payload strings.

    A blanket ``str.replace`` over the rendered HTML would also hit the
    embedded ``data_json``, silently corrupting any label that happens to
    contain the placeholder token.
    """
    endpoint_df = _make_minimal_endpoint_df()
    endpoint_df.loc[0, "subroutine_name"] = "step_HF_marker"
    out_path = tmp_path / "test_scatter.html"
    ok = export_method_rpdf_scatter_html(endpoint_df, out_path)
    assert ok
    html = out_path.read_text(encoding="utf-8")
    assert "step_HF_marker" in html, "payload label was rewritten by the injection"
