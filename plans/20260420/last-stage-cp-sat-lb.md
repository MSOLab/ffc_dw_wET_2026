# Last-stage-only CP-SAT LB subroutine

## Context

`run_mcf_lb` at [controller.py:74-133](src/ffc_ddw_sum_et/orchestration/controller.py#L74-L133) has dead WIP at lines 88-96 that builds a last-stage-only CP-SAT model but never solves it. `r_j = self.instance.get_job_2_p_sum_except_last_stage()` (line 96) is also unused.

The user wants a dedicated step that:
1. Takes MCF preemptive start times as a heuristic job release map to build an initial last-stage parallel-machine schedule via [FFcSchedule.dispatch_stage_by_jobs](src/ffc_ddw_sum_et/solution/ffc_schedule.py#L506).
2. Feeds that schedule as a warm-start hint into a last-stage-only CP-SAT model.
3. Solves with `max_time_in_seconds = 0.01 * n * c` (n=`job_count`, c=`stage_count`).
4. Produces a **good last-stage-only** FFcSchedule as an artifact.

Follow-up steps (fix last stage, reverse-dispatch, profile-fix CP) will be wired up in **separate future conversations**, so this change must (a) stop at producing the last-stage artifact and (b) not disturb `run_mcf_lb`'s current full-incumbent contract.

`BaseModelBuilder.build(..., job_2_release=...)` is already wired — [cumulative.py:176-179](src/ffc_ddw_sum_et/algorithm/cumulative.py#L176-L179) applies `max(release, head)` to the **first stage** of `params.i_list`, which is exactly the last stage when `last_stage_only=True`.

## Changes

### 1. `controller.py` — revert `run_mcf_lb` WIP

Delete lines 89-96 inside `run_mcf_lb` (the dead `builder.make_params` / `build(..., last_stage_only=True)` / unused `r_j`). Keep line 88 (`mcf_start_map = mcf.get_job_2_start_time_map()`) and the MixedDispatcher flow intact.

### 2. `controller_core.py` — new attribute

In `FFcDDWSubroutineControllerCore.__init__` (after `self.solution_manager = FFcDDWSolutionManager()` at [controller_core.py:55](src/ffc_ddw_sum_et/orchestration/controller_core.py#L55)):

```python
self.last_stage_cp_sat_solution: FFcDDWSolution | None = None
```

### 3. `controller.py` — new method `run_last_stage_cp_sat_lb`

Add right after `run_mcf_lb`. Signature:

```python
def run_last_stage_cp_sat_lb(
    self,
    solver_thread_cnt: int = 1,
) -> SubroutineReport:
```

Body steps:

1. Solve MCF — reuse the same `ParallelMachinePreemptionMcf.from_instance(self.instance).solve()` block as `run_mcf_lb`, assert optimal, grab `mcf_start_map` and `mcf_lb = float(mcf.get_obj_value())`.
2. Compute releases and basic maps:
   - `last_stage_id = self.instance.stage_id_list[-1]`
   - `r_j_map = self.instance.get_job_2_p_sum_except_last_stage()`
   - `duration_map = self.instance.get_job_2_p_map_for_stage(last_stage_id)`
   - `n, c = self.instance.job_count, self.instance.stage_count`
3. Build the CP-SAT model:
   - `params_for_horizon = BaseModelBuilder.make_params(self.instance)` (full-stage params — gives a safe horizon; last-stage-only horizon could be < `r_j + p_last` for some j and break the domain assertion in [cumulative.py:173](src/ffc_ddw_sum_et/algorithm/cumulative.py#L173)).
   - `horizon = sum(params_for_horizon.p.values())`
   - `pm_mdl, pm_params, pm_ops_vars, _pm_obj_vars = BaseModelBuilder().build(self.instance, horizon=horizon, last_stage_only=True, job_2_release=r_j_map)`
4. Build the initial last-stage-only FFcSchedule:
   - `job_2_pos = {j: i for i, j in enumerate(self.instance.job_id_list)}`
   - `job_sequence = sorted(self.instance.job_id_list, key=lambda j: (mcf_start_map[j] is None, mcf_start_map[j] if mcf_start_map[j] is not None else 0, job_2_pos[j]))` (same tie-break as the existing MCF sort in run_mcf_lb).
   - `job_2_release_for_dispatch = {j: (mcf_start_map[j] if mcf_start_map[j] is not None else r_j_map[j]) for j in self.instance.job_id_list}` — fallback to `r_j_map[j]` because `FFcSchedule.get_job_priority_queue_for_stage_dispatch` calls `max(..., job_2_release.get(j, 0))` and would crash on a literal `None` ([ffc_schedule.py:496-504](src/ffc_ddw_sum_et/solution/ffc_schedule.py#L496-L504)).
   - Construct a full-layout FFcSchedule (jobs, all stages, all machines) — this keeps the stage index and schedule API uniform. Only `dispatch_stage_by_jobs(last_stage_id, ...)` is called, so earlier stages remain empty.
   - `init_schedule.dispatch_stage_by_jobs(last_stage_id, job_sequence, duration_map, job_2_release=job_2_release_for_dispatch)`.
5. Apply hints:
   - `BaseModelBuilder.apply_start_hints_from_start_time_map(pm_mdl, pm_params, pm_ops_vars, init_schedule.get_jik_2_start_time_map())`
   - `BaseModelBuilder.apply_end_hints_from_end_time_map(pm_mdl, pm_params, pm_ops_vars, init_schedule.get_jik_2_end_time_map())`
   - Both helpers accept 3-tuple `(j, i, k)` keys but ignore `k` ([cumulative.py:523,542](src/ffc_ddw_sum_et/algorithm/cumulative.py#L523)), so a partial-stage start/end map is fine.
6. Solve:
   - `solver = cp_model.CpSolver()`
   - `solver.parameters.max_time_in_seconds = float(0.01 * n * c)`
   - `solver.parameters.num_search_workers = int(solver_thread_cnt)`
   - `status = solver.Solve(pm_mdl)`
7. Extract result:
   - If `status in (cp_model.OPTIMAL, cp_model.FEASIBLE)`: extract `(j, last_stage_id) -> start/end` ints and build an output FFcSchedule using a reusable helper (next bullet).
   - Else log a warning and leave `self.last_stage_cp_sat_solution` at its prior value (None); return a report with `obj_value=None`, `obj_bound=mcf_lb`.
8. Store & return:
   - `self.last_stage_cp_sat_solution = FFcDDWSolution(schedule=out_schedule, obj_value=cp_obj, obj_bound=mcf_lb)` — do **not** call `solution_manager.register` (a partial schedule is not a valid full incumbent; `compute_weighted_earliness_tardiness` still works on it but it would wrongly dominate the MixedDispatcher incumbent because the relaxed model has no earlier-stage capacity).
   - Return `SubroutineReport(elapsed_time=..., obj_value=cp_obj, obj_bound=mcf_lb)`.

### 4. `controller.py` — parametrize `_build_schedule_from_op_starts`

The existing helper at [controller.py:311-344](src/ffc_ddw_sum_et/orchestration/controller.py#L311-L344) iterates over `instance.stage_id_list`. Add an optional `stages: Sequence[str] | None = None` param that defaults to `instance.stage_id_list`; the new method passes `stages=[last_stage_id]`. Touch only the loop variable — no change in existing callers' behavior.

## Critical files

- [src/ffc_ddw_sum_et/orchestration/controller.py](src/ffc_ddw_sum_et/orchestration/controller.py)
- [src/ffc_ddw_sum_et/orchestration/controller_core.py](src/ffc_ddw_sum_et/orchestration/controller_core.py)

## Reuse (no edits needed)

- [BaseModelBuilder.build / apply_{start,end}_hints_from_*](src/ffc_ddw_sum_et/algorithm/cumulative.py#L75)
- [FFcSchedule.dispatch_stage_by_jobs / get_jik_2_{start,end}_time_map](src/ffc_ddw_sum_et/solution/ffc_schedule.py#L506)
- [ParallelMachinePreemptionMcf.from_instance / get_job_2_start_time_map / get_obj_value](src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py)
- `FFcDDWParameters.get_job_2_p_sum_except_last_stage`, `get_job_2_p_map_for_stage`, `stage_id_list`, `job_count`, `stage_count` ([ffc_params.py](src/ffc_ddw_sum_et/parameters/ffc_params.py))

## Verification

1. `uv run ruff check` and `uv run ruff format` after edits.
2. Unit smoke: import and call the new method from a small script using an existing test instance (e.g. via `metadata/*_config.yaml` tiny instance). Confirm:
   - `controller.last_stage_cp_sat_solution is not None` after run,
   - `out_schedule` only has last-stage entries (`get_jik_2_start_time_map()` keys all share `last_stage_id`),
   - `cp_obj >= mcf_lb` (feasible vs. preemptive LB),
   - `cp_obj <= init_schedule_obj` (warm start can only help; use `compute_weighted_earliness_tardiness(init_schedule, instance)` for comparison).
3. Regression: run the existing `run_mcf_lb` → `run_profile_fixed_ns` flow on the same instance and confirm its incumbent / bounds are unchanged vs. main (the WIP removal is side-effect-free).
