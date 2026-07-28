"""Unit tests for ``ffc_ddw_sum_et.report.obj_log_loader``.

The loader's job is to turn the controller-frame ``<instance>_obj_log.json``
plus the sibling ``<instance>_instance_result.yaml`` into per-step
:class:`CallSegment` objects. These tests focus on the contract: each
``notes`` endpoint becomes exactly one segment, every ``data`` point lands
in exactly one segment, and required manifest fields propagate through.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ffc_ddw_sum_et.report.obj_log_loader import (
    _normalize_subroutine_name,
    build_endpoint_df,
    build_raw_progression_df,
    load_instance_progression,
)


def _write_pair(
    tmp_path: Path,
    *,
    obj_log_payload: dict,
    manifest_payload: dict,
    instance_name: str = "Inst1",
) -> tuple[Path, Path]:
    obj_log_path = tmp_path / f"{instance_name}_obj_log.json"
    manifest_path = tmp_path / f"{instance_name}_instance_result.yaml"
    obj_log_path.write_text(json.dumps(obj_log_payload), encoding="utf-8")
    manifest_path.write_text(yaml.safe_dump(manifest_payload), encoding="utf-8")
    return obj_log_path, manifest_path


def _manifest(instance_name: str = "Inst1", timelimit: float = 10.0) -> dict:
    return {
        "instance_name": instance_name,
        "job_count": 5,
        "stage_count": 3,
        "timelimit": timelimit,
    }


def test_loads_two_step_progression(tmp_path: Path) -> None:
    obj_log = {
        "obj_value": {
            "name": "obj_value",
            "data": {
                "0.5": 100.0,
                "1.0": 90.0,
                "1.5": 90.0,
                "3.0": 80.0,
            },
            "notes": {
                "1.0": "1-step_a",
                "3.0": "2-step_b",
            },
        },
        "obj_bound": {
            "name": "obj_bound",
            "data": {"1.0": 50.0, "3.0": 60.0},
            "notes": {"1.0": "1-step_a", "3.0": "2-step_b"},
        },
    }
    obj_log_path, manifest_path = _write_pair(
        tmp_path, obj_log_payload=obj_log, manifest_payload=_manifest()
    )

    prog = load_instance_progression(obj_log_path, manifest_path)

    assert prog.instance_id == "Inst1"
    assert prog.job_cnt == 5
    assert prog.stage_cnt == 3
    assert prog.timelimit_sec == 10.0

    assert len(prog.obj_value_calls) == 2
    a, b = prog.obj_value_calls
    assert a.call_index == 1
    assert a.subroutine_name == "step_a"
    assert a.global_start_sec == 0.0
    assert a.global_end_sec == 1.0
    assert [(p.global_sec, p.value) for p in a.points] == [(0.5, 100.0), (1.0, 90.0)]

    assert b.call_index == 2
    assert b.subroutine_name == "step_b"
    assert b.global_start_sec == 1.0
    assert b.global_end_sec == 3.0
    assert [(p.global_sec, p.value) for p in b.points] == [(1.5, 90.0), (3.0, 80.0)]


def test_endpoint_and_raw_dfs(tmp_path: Path) -> None:
    obj_log = {
        "obj_value": {
            "name": "obj_value",
            "data": {"0.5": 100.0, "1.0": 90.0, "3.0": 80.0},
            "notes": {"1.0": "1-step_a", "3.0": "2-step_b"},
        },
        "obj_bound": {"name": "obj_bound", "data": {}, "notes": {}},
    }
    obj_log_path, manifest_path = _write_pair(
        tmp_path, obj_log_payload=obj_log, manifest_payload=_manifest()
    )
    prog = load_instance_progression(obj_log_path, manifest_path)

    ep = build_endpoint_df([prog])
    assert list(ep["subroutine_name"]) == ["step_a", "step_b"]
    assert list(ep["norm_time"]) == [0.1, 0.3]  # 1.0/10 and 3.0/10
    assert list(ep["obj_value"]) == [90.0, 80.0]

    raw = build_raw_progression_df([prog])
    # 0.5, 1.0 in step_a; 3.0 in step_b
    assert len(raw) == 3
    assert list(raw["global_sec"]) == [0.5, 1.0, 3.0]


def test_bad_label_raises(tmp_path: Path) -> None:
    obj_log = {
        "obj_value": {
            "name": "obj_value",
            "data": {"1.0": 100.0},
            "notes": {"1.0": "no-prefix-format"},
        },
        "obj_bound": {"name": "obj_bound", "data": {}, "notes": {}},
    }
    obj_log_path, manifest_path = _write_pair(
        tmp_path, obj_log_payload=obj_log, manifest_payload=_manifest()
    )
    with pytest.raises(ValueError, match="<idx>-<subroutine_name>"):
        load_instance_progression(obj_log_path, manifest_path)


def test_missing_manifest_field_raises(tmp_path: Path) -> None:
    obj_log = {
        "obj_value": {
            "name": "obj_value",
            "data": {"1.0": 100.0},
            "notes": {"1.0": "1-step_a"},
        },
        "obj_bound": {"name": "obj_bound", "data": {}, "notes": {}},
    }
    bad_manifest = {"instance_name": "x"}  # missing timelimit / counts
    obj_log_path, manifest_path = _write_pair(
        tmp_path,
        obj_log_payload=obj_log,
        manifest_payload=bad_manifest,
    )
    with pytest.raises(KeyError):
        load_instance_progression(obj_log_path, manifest_path)


def test_empty_obj_value_yields_no_calls(tmp_path: Path) -> None:
    """When an instance errors before any step registers, the obj_log is
    written with empty ``data``/``notes`` mappings. The loader must succeed
    and produce zero call segments — the chart writer relies on this to
    skip such instances cleanly."""
    obj_log = {
        "obj_value": {"name": "obj_value", "data": {}, "notes": {}},
        "obj_bound": {"name": "obj_bound", "data": {}, "notes": {}},
    }
    obj_log_path, manifest_path = _write_pair(
        tmp_path, obj_log_payload=obj_log, manifest_payload=_manifest()
    )

    prog = load_instance_progression(obj_log_path, manifest_path)
    assert prog.obj_value_calls == ()
    assert prog.obj_bound_calls == ()
    assert build_endpoint_df([prog]).empty
    assert build_raw_progression_df([prog]).empty


def test_malformed_series_block_raises(tmp_path: Path) -> None:
    """An obj_value block that is not a mapping must raise instead of
    silently producing no rows."""
    obj_log = {
        "obj_value": "not-a-mapping",
        "obj_bound": {"name": "obj_bound", "data": {}, "notes": {}},
    }
    obj_log_path, manifest_path = _write_pair(
        tmp_path, obj_log_payload=obj_log, manifest_payload=_manifest()
    )
    with pytest.raises(ValueError, match="expected mapping"):
        load_instance_progression(obj_log_path, manifest_path)


# ── _normalize_subroutine_name unit tests ───────────────────────────────


@pytest.mark.parametrize(
    "raw_name, expected",
    [
        (
            "incremental_job_contrib_cp.3-jd006_r001",
            "incremental_job_contrib_cp.jd006",
        ),
        (
            "incremental_job_contrib_cp.4-jd006_r002",
            "incremental_job_contrib_cp.jd006",
        ),
        (
            "incremental_job_contrib_cp.12-jd006_r1000",
            "incremental_job_contrib_cp.jd006",
        ),
        (
            "coarsen_solve_reconstruct-5-incremental_job_contrib_cp.2-jd010_r001",
            "coarsen_solve_reconstruct-5-incremental_job_contrib_cp.jd010",
        ),
        ("incremental_sw_cp.1-batch_002", "incremental_sw_cp.1-batch_002"),
        (
            "coarsen_solve_reconstruct-1-calc_mcf_lb_and_derive_full_sch",
            "coarsen_solve_reconstruct-1-calc_mcf_lb_and_derive_full_sch",
        ),
        ("neh_cp", "neh_cp"),
    ],
)
def test_normalize_subroutine_name(raw_name: str, expected: str) -> None:
    assert _normalize_subroutine_name(raw_name) == expected


def test_normalize_merges_same_jd_retries() -> None:
    """Two rep variants of the same jd level normalize to the same name."""
    a = _normalize_subroutine_name("incremental_job_contrib_cp.3-jd006_r001")
    b = _normalize_subroutine_name("incremental_job_contrib_cp.4-jd006_r002")
    assert a == b == "incremental_job_contrib_cp.jd006"


# ── loader-level: rep collapse in CallSegment ──────────────────────────


def test_rep_collapse_preserves_prefixed_name(tmp_path: Path) -> None:
    """Two rep entries for the same jd produce CallSegments with identical
    ``subroutine_name`` but original ``prefixed_subroutine_name`` values."""
    obj_log = {
        "obj_value": {
            "name": "obj_value",
            "data": {
                "10.0": 100.0,
                "12.0": 90.0,
                "16.0": 85.0,
            },
            "notes": {
                "12.0": "2-incremental_job_contrib_cp.3-jd006_r001",
                "16.0": "2-incremental_job_contrib_cp.4-jd006_r002",
            },
        },
        "obj_bound": {"name": "obj_bound", "data": {}, "notes": {}},
    }
    obj_log_path, manifest_path = _write_pair(
        tmp_path, obj_log_payload=obj_log, manifest_payload=_manifest()
    )
    prog = load_instance_progression(obj_log_path, manifest_path)

    calls = prog.obj_value_calls
    assert len(calls) == 2

    # Both have the same normalized subroutine_name
    assert calls[0].subroutine_name == calls[1].subroutine_name
    assert calls[0].subroutine_name == "incremental_job_contrib_cp.jd006"

    # prefixed_subroutine_name keeps the original raw labels
    assert calls[0].prefixed_subroutine_name == (
        "2-incremental_job_contrib_cp.3-jd006_r001"
    )
    assert calls[1].prefixed_subroutine_name == (
        "2-incremental_job_contrib_cp.4-jd006_r002"
    )
