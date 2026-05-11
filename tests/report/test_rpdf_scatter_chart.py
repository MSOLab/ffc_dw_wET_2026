"""Unit tests for the strict-global-improvement filter that drives the
per-scenario scatter chart's marker set."""

from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.report.rpdf_scatter_chart import _build_html_payload
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
