# Plan: Port `check_feasibility` from hybridflowshop to FFcDDWSubroutineControllerCore

## Goal

Port the structural feasibility check from
`hybridflowshop/controller/controller_core.py:1039-1085`
(`HybridFlowShopCpLnsControllerCore.check_feasibility`) onto this project's
`FFcDDWSubroutineControllerCore`, and wire it into `post_run_process` so the
incumbent is automatically validated at the end of every run — exactly as the
hybridflowshop counterpart does.

This port is **structural only**:

- start times must be non-negative
- every `(j, i)` in `instance.job_id_list × instance.stage_id_list` is
  scheduled exactly once (no missing op, no duplicate)
- the assigned machine `k` belongs to `instance.stage_2_machines_map[i]`
- durations match `p_{j,i}`
- precedence between consecutive stages holds
- no two ops overlap on the same machine

The first three checks are **additional** to the hybridflowshop original —
that controller relied on schedules built from CP-SAT solutions that were
guaranteed-by-construction to be complete and machine-valid, so those
invariants were never re-verified. Here we don't have that guarantee for
every producer of a `start_time_map`, so we check explicitly.

The objective value (wET) is **not** computed here — wET is owned by
`solution/objectives.py::compute_weighted_earliness_tardiness` and is
already attached to `FFcDDWSolution.obj_value` upstream. To keep that single
source of truth, `check_feasibility` returns `None`.

## Source vs. target

### Source (hybridflowshop)

```python
def check_feasibility(
    self, start_time_map: dict[tuple[str, str, str], int]
) -> float:
    logging.info("Feasibility check starts")
    for (j, i, k), start_time in start_time_map.items():
        if start_time < 0:
            raise ValueError(...)
    makespan = 0
    end_time_map: dict[tuple[str, str, str], int] = {}
    for (j, i, k), start_time in start_time_map.items():
        end_time = start_time + self.job_2_stage_2_p_dict[j][i]
        end_time_map[(j, i, k)] = end_time
        if end_time > makespan:
            makespan = end_time

    from ..schedule_lite import (
        validate_duration, validate_no_overlap, validate_precedence,
    )

    validate_duration(start_time_map, end_time_map, self.stage_2_job_2_p_dict)
    validate_precedence(start_time_map, end_time_map, self.instance.stage_id_list)
    validate_no_overlap(
        start_time_map, end_time_map,
        self.instance.stage_id_list, self.instance.stage_2_machines_map,
    )

    logging.info("Feasibility check passed")
    return makespan
```

### Target

The three validators already exist in `src/ffc_ddw_sum_et/solution/ffc_schedule.py`
with the same signatures:

- `validate_duration(start_map, end_map, stage_2_job_2_duration)` (line 1588)
- `validate_precedence(start_map, end_map, stages)` (line 1603)
- `validate_no_overlap(start_map, end_map, stages, machines_per_stage)` (line 1627)

`FFcDDWParameters` exposes the matching attributes:

- `instance.stage_2_job_2_p_map` (ffc_params.py:174)
- `instance.stage_id_list`
- `instance.stage_2_machines_map`
- `instance.job_2_stage_2_p_map` (ffc_params.py:168)

`FFcSchedule` already provides `get_jik_2_start_time_map()` (line 338), so
`post_run_process` can hand the incumbent's start map straight to
`check_feasibility`.

## Edits

All in `src/ffc_ddw_sum_et/orchestration/controller_core.py`.

### 1. Add module-level imports

Add the three validators next to the existing solution imports:

```python
from ..solution.ffc_schedule import (
    validate_duration,
    validate_no_overlap,
    validate_precedence,
)
```

(Top-level import keeps it consistent with the rest of the module — the
hybridflowshop original used a function-local import; we don't need to.)

### 2. Add `check_feasibility` method

Place it just above `post_run_process` (currently `controller_core.py:232`).
Single pass over `start_time_map.items()` does the per-entry checks
*and* builds `end_time_map`; afterwards verify completeness, then run the
three existing validators:

```python
def check_feasibility(
    self, start_time_map: dict[tuple[str, str, str], int]
) -> None:
    """Validate structural feasibility of start times for a complete schedule.

    Raises:
        ValueError: when any structural invariant fails — negative start time,
            unknown job/stage, machine not at the given stage, duplicate
            (job, stage) entry, missing (job, stage) entry, or any of the
            duration/precedence/no-overlap checks.
    """
    self.logger.info("Feasibility check starts")

    instance = self.instance
    job_id_set = set(instance.job_id_list)
    stage_id_set = set(instance.stage_id_list)
    stage_2_machine_set = {
        stage_id: set(machines)
        for stage_id, machines in instance.stage_2_machines_map.items()
    }
    job_2_stage_2_p = instance.job_2_stage_2_p_map

    end_time_map: dict[tuple[str, str, str], int] = {}
    seen_pairs: set[tuple[str, str]] = set()
    for (j, i, k), start_time in start_time_map.items():
        if start_time < 0:
            raise ValueError(...)
        if j not in job_id_set:
            raise ValueError(...)
        if i not in stage_id_set:
            raise ValueError(...)
        if k not in stage_2_machine_set[i]:
            raise ValueError(...)
        if (j, i) in seen_pairs:
            raise ValueError(...)
        seen_pairs.add((j, i))
        end_time_map[(j, i, k)] = start_time + job_2_stage_2_p[j][i]

    expected_pairs = {
        (j, i) for j in instance.job_id_list for i in instance.stage_id_list
    }
    missing_pairs = expected_pairs - seen_pairs
    if missing_pairs:
        raise ValueError(
            f"Missing (job, stage) operations in start_time_map: "
            f"{sorted(missing_pairs)}"
        )

    validate_duration(
        start_time_map, end_time_map, instance.stage_2_job_2_p_map
    )
    validate_precedence(
        start_time_map, end_time_map, instance.stage_id_list
    )
    validate_no_overlap(
        start_time_map,
        end_time_map,
        instance.stage_id_list,
        instance.stage_2_machines_map,
    )

    self.logger.info("Feasibility check passed")
```

Notes:

- Uses `self.logger` (the per-instance logger configured in `__init__`),
  not the root `logging.*` — matches this project's convention; the
  hybridflowshop original used the root logger.
- No defensive `.get()` on `job_2_stage_2_p_map[j][i]`; missing keys must
  raise (per project convention — feedback memory
  `feedback_no_defensive_get.md`).
- `stage_2_machine_set` precomputes per-stage machine sets so the membership
  test is O(1) inside the loop. `seen_pairs` doubles as the duplicate-
  detection set and as the input to the post-loop completeness check.
- Per-entry checks (negative start, unknown j/i/k, duplicate `(j, i)`) are
  fused into the same loop that builds `end_time_map`, so the entire pass
  is O(|start_time_map|).

### 3. Wire into `post_run_process`

Replace the current no-op (`controller_core.py:232-233`):

```python
def post_run_process(self) -> None:
    """Validate the incumbent's structural feasibility, if any."""
    incumbent = self.solution_manager.get_incumbent()
    if incumbent is not None:
        self.check_feasibility(
            incumbent.schedule.get_jik_2_start_time_map()
        )
```

`incumbent` is `FFcDDWSolution` (orchestration/solution_manager.py:14), whose
`schedule` field is an `FFcSchedule`, so `.get_jik_2_start_time_map()` is the
correct accessor.

## Validation

- `uv run ruff check`
- `uv run ruff format`
- `uv run pytest tests/orchestration/test_controller.py` (if the test exists;
  otherwise skip — there is no behavior to assert beyond "doesn't raise on a
  feasible incumbent" without writing a new fixture).
- Spot-check by running an existing experiment that produces an incumbent
  and confirming the SIR log contains "Feasibility check starts" /
  "Feasibility check passed" lines.

## Out of scope

- No new abstraction, no shared base class, no helper for "validate +
  compute objective". `compute_weighted_earliness_tardiness` already owns
  the wET calculation and is unchanged.
- `check_feasibility` is intentionally **not** called from any other site
  (e.g. inside individual subroutines) in this PR — only from
  `post_run_process`. Adding more call sites can be a follow-up if it
  proves useful.
