from __future__ import annotations

from ffc_ddw_sum_et.orchestration.ffcddw_single_instance_runner import InstanceResult


def test_default_values() -> None:
    result = InstanceResult(
        instance_name="inst",
        elapsed_time=1.5,
        obj_value=None,
        obj_bound=None,
        work_status=None,
    )

    assert result.instance_name == "inst"
    assert result.elapsed_time == 1.5
    assert result.obj_value is None
    assert result.obj_bound is None
    assert result.work_status is None
    assert result.solution_path is None
    assert result.has_incumbent is False
    assert result.method_call_counts == {}
    assert result.report_count == 0
    assert result.first_obj_value is None
    assert result.first_obj_bound is None
    assert result.error is None


def test_all_fields() -> None:
    result = InstanceResult(
        instance_name="inst",
        elapsed_time=2.5,
        obj_value=10.0,
        obj_bound=8.0,
        work_status="FEASIBLE",
        solution_path="/tmp/s.json",
        has_incumbent=True,
        method_call_counts={"run_fam": 3},
        report_count=3,
        first_obj_value=20.0,
        first_obj_bound=7.0,
        error=None,
    )

    assert result.obj_value == 10.0
    assert result.obj_bound == 8.0
    assert result.work_status == "FEASIBLE"
    assert result.solution_path == "/tmp/s.json"
    assert result.has_incumbent is True
    assert result.method_call_counts == {"run_fam": 3}
    assert result.report_count == 3
    assert result.first_obj_value == 20.0
    assert result.first_obj_bound == 7.0
