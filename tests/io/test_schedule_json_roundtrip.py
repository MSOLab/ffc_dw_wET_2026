"""Round-trip tests for the solution-JSON loader used by resume-from-base.

``load_schedule_json`` must reconstruct an ``FFcSchedule`` identical to the one
``dump_solution_json`` wrote (exact machines + start/end times) and return the
stored obj_value/obj_bound. See plans/experiment/20260709/resume_from_base.md § 4.1.
"""

from __future__ import annotations

import pickle

from ffc_ddw_sum_et.io import dump_solution_json, load_schedule_json
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule


def _tiny_schedule() -> FFcSchedule:
    sch = FFcSchedule(
        jobs=["j1", "j2"],
        stages=["s1", "s2"],
        machines_per_stage={"s1": ["s1m1"], "s2": ["s2m1"]},
    )
    # j1: s1[0,3) s2[3,5); j2: s1[3,6) s2[6,9)
    sch.add_ops_times_2_mc("s1", "s1m1", "j1", 0, 3)
    sch.add_ops_times_2_mc("s1", "s1m1", "j2", 3, 6)
    sch.add_ops_times_2_mc("s2", "s2m1", "j1", 3, 5)
    sch.add_ops_times_2_mc("s2", "s2m1", "j2", 6, 9)
    return sch


def test_load_schedule_json_round_trips_times_and_obj(tmp_path):
    sch = _tiny_schedule()
    path = tmp_path / "sol.json"
    dump_solution_json(sch, path, instance_name="tiny", obj_value=42.0, obj_bound=7.0)

    loaded, obj_value, obj_bound = load_schedule_json(path)

    assert loaded.get_jik_2_start_time_map() == sch.get_jik_2_start_time_map()
    assert loaded.get_jik_2_end_time_map() == sch.get_jik_2_end_time_map()
    assert dict(loaded.machines_per_stage) == {"s1": ["s1m1"], "s2": ["s2m1"]}
    assert list(loaded.jobs) == ["j1", "j2"]
    assert list(loaded.stages) == ["s1", "s2"]
    assert obj_value == 42.0
    assert obj_bound == 7.0


def test_load_schedule_json_none_obj_bound(tmp_path):
    # The canonical incumbent solution JSON usually carries objBound=None; the
    # global LB is sourced from the manifest on resume.
    sch = _tiny_schedule()
    path = tmp_path / "sol.json"
    dump_solution_json(sch, path, instance_name="tiny", obj_value=1.0, obj_bound=None)

    _loaded, obj_value, obj_bound = load_schedule_json(path)
    assert obj_value == 1.0
    assert obj_bound is None


def test_loaded_schedule_is_picklable(tmp_path):
    # Resume injects the reconstructed schedule into single-instance runners that
    # are shipped to a ProcessPoolExecutor by value — it must pickle cleanly.
    sch = _tiny_schedule()
    path = tmp_path / "sol.json"
    dump_solution_json(sch, path, instance_name="tiny", obj_value=1.0, obj_bound=None)
    loaded, _ov, _ob = load_schedule_json(path)

    restored = pickle.loads(pickle.dumps(loaded))
    assert restored.get_jik_2_start_time_map() == sch.get_jik_2_start_time_map()
