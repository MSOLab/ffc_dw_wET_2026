# Plan: `neh_cp` subroutine for FFcDDWSubroutineController

## Context

Port the NEH-CP incremental constructor
(`hybridflowshop/hybridflowshop/controller/hfs_cp_lns.py::neh_cp`, backed by
`hybridflowshop/controller/neh_cp.py::NehCpConstructor`) into this project,
adapted to the FFc DDW sum-E/T problem. The upstream version is driven by a
reference makespan schedule and a secondary objective; here we minimise
weighted earliness + tardiness (outer objective per this repo's convention —
see `feedback_alg_record_obj_value`) and have no incumbent to start from.

User-specified deviations from upstream:

1. **No reference schedule.** Decide the job sequence up-front (see point 3).
2. **Sub-schedule only in obj log.** Don't dispatch remaining (unadded) jobs at
   each iteration. Record sub-schedule weighted E+T at each step (one value
   per iteration, no "after-dispatch" bound value).
3. **Priority-based job sequence**, tie-break in this order:
   1. `max(w^-_j, w^+_j)` DESC
   2. `(w^-_j + w^+_j)` DESC
   3. `(d^+_j - d^-_j)` ASC
   4. original `job_id_list` position
4. **First batch size** = `max(added_batch_size, max_m_per_stage * 2)`,
   where `max_m_per_stage = max(instance.machine_count_per_stage)`.

## Scope (KISS / YAGNI)

Dropped upstream options: `job_seq_by_1st_stage`,
`job_seq_by_bottleneck_stage`, `preserved_head_job_portion`,
`minimize_sum_ci_lex/lin`, `cp_tl_*_multiplier_2nd_obj`, `tighten_ranges`,
`link_job_completion`, `make_semi_active_every_cp`, `draw_gantt`.

Kept/adapted:
- `solver_thread_cnt: int = 1`
- `added_batch_size: int = 1`
- `cp_tl: float | str | None = None` — per-batch CP-SAT time limit, resolved
  via the existing `_resolve_cp_tl` helper in
  `src/ffc_ddw_sum_et/orchestration/controller.py:41`.
- `pf_method: PFMethod | None = "PF1"` — per-machine adjacency precedence by
  default; pass another literal to override or `None` to skip PF arcs.
- `error_if_infeasible: bool = False`

## Files to modify / create

1. `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py`
   - Add classmethod `create_instance_of_job_subset(cls, instance,
     job_id_subset: set[str]) -> Self`, mirroring the existing
     `create_instance_of_stage_subset` pattern
     (`parameters/ffc_ddw_params.py:114`). Uses
     `JobStageProcessingTimeManager.filter_by_job_indices`
     (`parameters/base/job_stage_p.py:151`). Preserves original job order.

2. `src/ffc_ddw_sum_et/solution/schedule_build.py`
   - Extend `build_schedule_from_op_starts` to accept optional
     `jobs: Sequence[str] | None = None`. When provided, greedy interval
     colouring iterates only over that subset; the returned `FFcSchedule`
     still uses the full `instance.job_id_list`. Backward-compatible.

3. `src/ffc_ddw_sum_et/orchestration/controller.py`
   - Add `neh_cp(self, *, solver_thread_cnt, added_batch_size, cp_tl,
     pf_method, error_if_infeasible) -> SubroutineReport` on
     `FFcDDWSubroutineController`. Default `pf_method="PF1"`.
   - Add private helper `_neh_cp_job_sequence(self) -> list[str]`.

4. `tests/orchestration/test_controller.py`
   - `test_neh_cp_registers_full_schedule`: runs on the 3-job fixture,
     asserts `report.obj_value` matches
     `compute_weighted_earliness_tardiness(schedule)` sum.
   - `test_neh_cp_job_sequence_priority`: constructed instance forces a
     specific priority ordering.

## Algorithm (inside `neh_cp`)

```
timer start
n = instance.job_count
horizon = sum(BaseModelBuilder.make_params(instance).p.values())
cp_tl_seconds = _resolve_cp_tl(cp_tl, n, instance.stage_count)

job_sequence = _neh_cp_job_sequence()
max_m = max(instance.machine_count_per_stage)
first_batch_size = max(added_batch_size, max_m * 2)
batches = [job_sequence[:first_batch_size]] + chunks(job_sequence[first_batch_size:], added_batch_size)

partial_sol: FFcSchedule | None = None
current_jobs: list[str] = []
sub_obj_log: list[dict] = []

for step, batch in enumerate(batches):
    current_jobs.extend(batch)

    base = partial_sol.deepcopy() if partial_sol else FFcSchedule(full instance)
    dispatched = MixedDispatcher(instance).get_best_mixed_schedule_by_sequence(
        batch, schedule=base, from_stage=instance.stage_id_list[0],
        head_for_all_stages=True, criteria="weighted_et",
    )
    if dispatched is None:
        dispatched = base
        for j in batch:
            dispatched.dispatch_job_by_stages(j, instance.job_2_stage_2_p_map[j])

    sub_instance = FFcDDWParameters.create_instance_of_job_subset(instance, set(current_jobs))
    mdl, params, op_vars, et_vars = BaseModelBuilder().build(sub_instance, horizon=horizon)
    start_map = dispatched.get_jik_2_start_time_map()
    end_map   = dispatched.get_jik_2_end_time_map()
    # Filter to subset jobs only (dispatched already contains only current_jobs, but be defensive)
    BaseModelBuilder.apply_start_hints_from_start_time_map(mdl, params, op_vars, start_map)
    BaseModelBuilder.apply_end_hints_from_end_time_map(mdl, params, op_vars, end_map)
    BaseModelBuilder.apply_et_hints_from_ref_schedule(mdl, params, et_vars, dispatched)

    if partial_sol is not None and pf_method is not None:
        by_machine, stride = decode_pf_method(pf_method)
        BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(
            mdl, params, op_vars, partial_sol,
            profile_fix_by_machine=by_machine, machine_precedence_stride=stride,
        )

    solver = cp_model.CpSolver()
    if cp_tl_seconds is not None:
        solver.parameters.max_time_in_seconds = cp_tl_seconds
    solver.parameters.num_workers = solver_thread_cnt
    status = solver.solve(mdl)

    if status in (OPTIMAL, FEASIBLE):
        j_i_2_start = {(j,i): int(solver.Value(op_vars.op_start[j,i])) for j in params.j_list for i in params.i_list}
        j_i_2_end   = {(j,i): int(solver.Value(op_vars.op_end[j,i]))   for j in params.j_list for i in params.i_list}
        new_sch = build_schedule_from_op_starts(instance, j_i_2_start, j_i_2_end, jobs=current_jobs)
        se_new, st_new = compute_weighted_earliness_tardiness(new_sch, sub_instance)
        se_dis, st_dis = compute_weighted_earliness_tardiness(dispatched, sub_instance)
        partial_sol = new_sch if (se_new + st_new) <= (se_dis + st_dis) else dispatched
    else:
        partial_sol = dispatched

    se, st = compute_weighted_earliness_tardiness(partial_sol, sub_instance)
    sub_obj_log.append({
        "step": step,
        "elapsed_time": float(timer.elapsed),
        "sub_obj": float(se + st),
        "job_count": len(current_jobs),
    })

final = partial_sol
if error_if_infeasible and final is None:
    raise RuntimeError(...)

se, st = compute_weighted_earliness_tardiness(final, instance)
obj_value = float(se + st)
report = SubroutineReport(elapsed_time=timer.elapsed, obj_value=obj_value, obj_bound=None)
self.solution_manager.register(report, FFcDDWSolution(schedule=final, obj_value=obj_value))

try:
    dump_yaml(sub_obj_log, self.get_file_path_for_subroutine("_obj_log.yaml"))
except AttributeError:
    pass  # working dir not set (e.g. unit test); skip log

return report
```

## Existing utilities reused

- `_resolve_cp_tl` — `orchestration/controller.py:41`
- `BaseModelBuilder.build` / `make_params` / `apply_*_hints_*` /
  `add_stage_ops_precedence_constraints_after_dispatch_from_schedule` —
  `algorithm/cumulative.py`
- `decode_pf_method`, `PFMethod` — `algorithm/cumulative.py:18-40`
- `MixedDispatcher.get_best_mixed_schedule_by_sequence` —
  `algorithm/dispatcher/mixed.py:34`
- `compute_weighted_earliness_tardiness` — `solution/objectives.py`
- `build_schedule_from_op_starts` (extended) — `solution/schedule_build.py`
- `FFcDDWParameters` + new `create_instance_of_job_subset`
- `dump_yaml` — `routix.io`
- `FFcDDWSolution`, `solution_manager.register`

## Verification

- `uv run ruff check` and `uv run ruff format`.
- `uv run pytest tests/orchestration/test_controller.py -k neh_cp -x`.
- Smoke run via the 3-job fixture; assert `obj_value` recomputes exactly from
  the registered schedule's `compute_weighted_earliness_tardiness`.
