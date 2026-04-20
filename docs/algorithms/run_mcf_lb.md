# `run_mcf_lb`

`FFcDDWSubroutineController.run_mcf_lb` — end-to-end pipeline that produces an
FFcDDW full-schedule incumbent seeded from an MCF preemptive lower bound.

Defined at [controller.py](../../src/ffc_ddw_sum_et/orchestration/controller.py).

## Signature

```python
def run_mcf_lb(
    self,
    last_stage_only_timelimit: float | str | None = None,
    profile_fix_by_machine: bool = False,
    machine_precedence_stride: int = 1,
) -> SubroutineReport: ...
```

- `last_stage_only_timelimit` — time budget for the step-1 CP-SAT solver.
  Parsed by `_parse_nc_timelimit`:
  - `None` → no explicit limit,
  - `float`/`int` → seconds,
  - `"<x>nc"` → `float(x) * n * c` seconds (e.g. `"0.01nc"`).
- `profile_fix_by_machine`, `machine_precedence_stride` — passed through to
  `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule`
  in step 2-3.
- The step-2-3 CP-SAT solver currently runs **without** an explicit time limit.
- `num_search_workers` is hard-coded to `1` for both CP-SAT solves.

## Pipeline

Let `n = instance.job_count`, `c = instance.stage_count`,
`last_stage_id = instance.stage_id_list[-1]`.

### Step 1-1 — MCF preemptive LB

- Build and solve `ParallelMachinePreemptionMcf.from_instance(instance)`.
- Abort with `RuntimeError` if the flow is not optimal.
- Record `mcf_lb = mcf.get_obj_value()` (used as `obj_bound` throughout) and
  the priority score
  `mcf.get_job_2_avg_time_minus_half_processing_time_sum_map()` (average flow
  midpoint minus `(r_j + p_j)/2`; jobs with no flow carry `None`).

### Step 1-2 — last-stage-only dispatch seed

- Sort jobs by priority score ascending; ties broken by native
  `job_id_list` order; `None` scores sink to the tail.
- Build a last-stage-only CP-SAT model:
  - `BaseModelBuilder.build(..., last_stage_only=True, job_2_release=r_j_map, obj_lb=mcf_lb)`
    where `r_j_map = instance.get_job_2_p_sum_except_last_stage()`.
  - `obj_lb=mcf_lb` adds `sum(et_terms) >= ceil(mcf_lb)` as a cut.
- Dispatch `mcf_job_sequence` onto the last stage via
  `FFcSchedule.dispatch_stage_by_jobs(last_stage_id, …, job_2_release=r_j_map)`
  to produce `last_stage_only_init_schedule` (only the last stage is filled).

### Step 1-3 — last-stage-only CP-SAT warm-start & solve

- Apply `last_stage_only_init_schedule` start/end values as CP-SAT hints via
  `BaseModelBuilder.apply_{start,end}_hints_from_*_map`.
- Solve under `last_stage_only_timelimit`.
- If the solver returns neither `OPTIMAL` nor `FEASIBLE`: log a warning and
  return `SubroutineReport(obj_value=None, obj_bound=mcf_lb)` — no incumbent
  is registered.
- Otherwise extract last-stage `(j, last_stage_id) → start/end`, build a
  partial `last_stage_only_schedule` via `_build_schedule_from_op_starts(..., stages=[last_stage_id])`,
  and store the result on
  `self.last_stage_cp_sat_solution: FFcDDWSolution`.

### Step 2-1 — reverse-dispatch with last stage pinned

`c == 1` short-circuits to `dispatched_schedule = last_stage_only_schedule`. Otherwise:

- Sort jobs descending by CP-SAT last-stage end time, tie-break by native order.
- Build `reversed_instance = FFcDDWParameters.reverse_stages(instance)` and an
  empty `reversed_seed` schedule laid out over its stages.
- For every op `(mc_id, s, e, j)` in the CP-SAT last-stage schedule, insert
  its **mirror** into `reversed_seed`:
  `start = ls_makespan - e`, `end = ls_makespan - s`, same machine.
- `MixedDispatcher(reversed_instance).get_best_mixed_schedule_by_sequence(...,
  schedule=reversed_seed, from_stage=reversed_instance.stage_id_list[1],
  criteria="makespan")` fills the remaining reversed stages.
- If the dispatcher returns `None`: log a warning and return
  `SubroutineReport(obj_value=None, obj_bound=mcf_lb)` — no incumbent.

### Step 2-2 — unflip

`dispatched_schedule = reversed_full.as_reversed()`.

The last-stage ops end up at `s + (M - ls_makespan)` where
`M = reversed_full.makespan` (no additional `right_shift` is performed), so
they may drift right of the CP-SAT step-1-3 times. Earlier stages remain
feasible by construction.

`compute_window_et(dispatched_schedule, instance)` yields `step2_obj`; it is
registered with `solution_manager` as an intermediate incumbent alongside
`obj_bound=mcf_lb`.

### Step 2-3 — profile-fix CP-SAT full solve

- Build a full CP-SAT model with
  `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule`
  applied against `dispatched_schedule`.
- Warm-start with start/end hints from `dispatched_schedule`.
- Solve with `num_search_workers = 1`.
- `obj_bound_final = max(mcf_lb, pf_solver.best_objective_bound)` (falls back
  to `mcf_lb` if the bound is unavailable).
- If the solver returns neither `OPTIMAL` nor `FEASIBLE`: log a warning and
  return the **step-2-2** incumbent with `obj_value=step2_obj,
  obj_bound=obj_bound_final`.
- Otherwise extract `(j, i) → start/end` for every stage, build
  `final_schedule` via `_build_schedule_from_op_starts`, recompute ET via
  `compute_window_et`, register the final incumbent with `solution_manager`,
  and return the final `SubroutineReport`.

If the recomputed ET disagrees with `pf_solver.objective_value`, a warning is
logged but the value from `compute_window_et` is used.

## Side effects

| State | Set by |
| --- | --- |
| `self.solution_manager` (intermediate) | Step 2-2, via `register(step2_obj)` |
| `self.solution_manager` (final) | Step 2-3, on `OPTIMAL`/`FEASIBLE` |
| `self.last_stage_cp_sat_solution` | Step 1-3, on `OPTIMAL`/`FEASIBLE` |

No filesystem I/O is performed directly. `FFcDDWSingleInstanceRunner` later
dumps `self.last_stage_cp_sat_solution.schedule` to
`<working_dir>/<ins>_last_stage_cp_sat_schedule.yaml` if set.

## Early-return paths

| Condition | Return |
| --- | --- |
| MCF not optimal | `RuntimeError` |
| Step 1-3 solver not feasible | `obj_value=None, obj_bound=mcf_lb` |
| Step 2-1 dispatcher returns `None` | `obj_value=None, obj_bound=mcf_lb` |
| Step 2-3 solver not feasible | `obj_value=step2_obj, obj_bound=obj_bound_final` |

In the first three cases no final incumbent is registered by `run_mcf_lb`
(step 2-3 registers the intermediate step-2-2 incumbent).

## Dependencies

- [`ParallelMachinePreemptionMcf`](../../src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py)
- [`BaseModelBuilder`](../../src/ffc_ddw_sum_et/algorithm/cumulative.py) —
  `build(last_stage_only=, job_2_release=, obj_lb=)`,
  `apply_{start,end}_hints_from_*`,
  `add_stage_ops_precedence_constraints_after_dispatch_from_schedule`,
  `make_params`.
- [`MixedDispatcher`](../../src/ffc_ddw_sum_et/algorithm/dispatcher/mixed.py)
- [`FFcSchedule`](../../src/ffc_ddw_sum_et/solution/ffc_schedule.py) —
  `dispatch_stage_by_jobs`, `iter_operations_on_stage`,
  `add_ops_times_2_mc`, `as_reversed`,
  `get_jik_2_{start,end}_time_map`.
- [`FFcDDWParameters.reverse_stages`](../../src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py)
- [`compute_window_et`](../../src/ffc_ddw_sum_et/solution/objectives.py)

## Related

- `run_last_stage_cp_sat_lb` — standalone counterpart of steps 1-1 through
  1-3 (no step 2). Differences: dispatch seed is sorted by MCF **preemptive
  start times** (not the priority score); the per-job release used by the
  dispatcher falls back from MCF start to `r_j` when the MCF value is `None`;
  the CP-SAT time budget is fixed at `0.01 * n * c`. `self.last_stage_cp_sat_solution`
  is still populated; no incumbent is registered.
