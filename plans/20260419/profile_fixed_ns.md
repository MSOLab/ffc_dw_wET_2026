# Plan: Add `profile_fixed_ns` subroutine to FFcDDWSubroutineController

## Context

The `hybridflowshop` repo has a CP-SAT-based subroutine
`HybridFlowShopCpLnsController.profile_fixed_ns` (`hfs_cp_lns.py:1298`)
that warm-starts the CP-SAT solver from the incumbent schedule by
**fixing the dispatch profile** — i.e., adding precedence arcs that
preserve the relative ordering of operations seen in the incumbent —
then solving the constrained CP-SAT model under a time budget. This
acts as a "narrow search around the incumbent": the CP-SAT solver can
re-time operations within the fixed profile but cannot reshuffle them.

We want the same subroutine in `ffc_ddw_sum_et` so that, after
`run_mcf_lb` (or `run_fam`) seeds an incumbent, we can call CP-SAT to
locally improve the window earliness/tardiness objective. The CP-SAT
model already exists in `algorithm/cumulative.py` (`BaseModelBuilder`)
and supports both the precedence-fixing helper
(`add_stage_ops_precedence_constraints_after_dispatch_from_schedule`,
`cumulative.py:374`) and start/end hint helpers
(`apply_start_hints_from_start_time_map`, `cumulative.py:506`;
`apply_end_hints_from_end_time_map`, `cumulative.py:524`).

The `hfs` version is a thin wrapper around `_fix_profile_solve_reset`,
which itself bundles many features not yet needed here (swap operator,
semi-active repair, LNS-only, persistent `cp_model` reuse, post-solve
visualization). YAGNI: port only the core flow — build fresh CP model,
fix profile from incumbent, hint, solve, register.

### Key adaptation: machine assignment after solve

The HFS controller carries machine-assignment variables alongside the
intervals, but `cumulative.py` here uses **stage-level cumulative
constraints only** — it has `op_start[j,i]` and `op_end[j,i]` but no
machine variables. After CP-SAT solves, we only know per-stage start
times; we must construct the machine assignments ourselves.

Because the cumulative constraint guarantees that at most `|M_i|`
intervals overlap at stage `i`, a greedy interval-graph coloring (sort
jobs by start time per stage, pick the first machine whose previous
end ≤ current start) is always feasible. We use this to populate
`FFcSchedule.add_ops_times_2_mc(...)`.

## Files to modify

- `src/ffc_ddw_sum_et/orchestration/controller.py` — add
  `run_profile_fixed_ns` method (and one private module-level helper).
- `metadata/` — add a new experiment config
  `20260420_profile_fixed_ns_config.yaml` that chains
  `run_mcf_lb` → `run_profile_fixed_ns` so the CP-SAT step has an
  incumbent to fix.

No changes to `algorithm/cumulative.py` — the helpers we need are
already there.

## Implementation

### 1. New method `run_profile_fixed_ns` in `controller.py`

```python
def run_profile_fixed_ns(
    self,
    computational_time: float,
    solver_thread_cnt: int = 1,
    profile_fix_by_machine: bool = False,
    machine_precedence_stride: int = 1,
) -> SubroutineReport:
```

Steps:

1. `start_elapsed = self.timer.elapsed_sec`
2. Get incumbent: `incumbent = self.solution_manager.get_incumbent()`.
   If `None` or `incumbent.schedule is None`, raise `RuntimeError` —
   the subroutine requires an incumbent to fix.
3. Compute horizon as `sum(p[j,i])` over all jobs/stages (matches
   `BaseModelBuilder._define_objective` default at `cumulative.py:319`).
4. Build the CP-SAT model:
   ```python
   builder = BaseModelBuilder()
   mdl, params, op_vars, et_vars = builder.build(
       self.instance, horizon=horizon
   )
   ```
   (Defaults: `tighten_ranges=False`, `link_job_completion=False`,
   `use_max_equality_for_obj=True`.)
5. Add precedence arcs from the incumbent profile:
   `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule(...)`.
6. Apply start/end hints from incumbent:
   `apply_start_hints_from_start_time_map(...)` and
   `apply_end_hints_from_end_time_map(...)`.
7. Configure and run the CP-SAT solver:
   ```python
   from ortools.sat.python import cp_model as cp
   solver = cp.CpSolver()
   solver.parameters.max_time_in_seconds = float(computational_time)
   solver.parameters.num_search_workers = int(solver_thread_cnt)
   status = solver.Solve(mdl)
   ```
8. If `status in (cp.OPTIMAL, cp.FEASIBLE)`:
   - Build `FFcSchedule` via the greedy machine-assignment helper
     (see §2 below).
   - `obj_value = float(sum_e + sum_t)` from
     `compute_window_et(schedule, self.instance)` (sanity-check
     against `solver.objective_value`; log a warning on mismatch).
   - `obj_bound = float(solver.best_objective_bound)`.
   - Register: `self.solution_manager.register(report,
     FFcDDWSolution(...))`.
9. If status is `INFEASIBLE` / `MODEL_INVALID` / `UNKNOWN` with no
   solution: skip registration; report carries `obj_value=None` and
   `obj_bound` from the solver if available else `None`.
10. Return `SubroutineReport(elapsed_time=elapsed, obj_value=...,
    obj_bound=...)`.

### 2. Greedy machine-assignment helper (private module-level function)

```python
def _build_schedule_from_op_starts(
    instance: FFcDDWParameters,
    j_i_2_start: dict[tuple[str, str], int],
    j_i_2_end: dict[tuple[str, str], int],
) -> FFcSchedule:
    """Assign each (j, i) to a machine via greedy interval coloring."""
    schedule = FFcSchedule(
        jobs=instance.job_id_list,
        stages=instance.stage_id_list,
        machines_per_stage=instance.stage_2_machines_map,
    )
    for i in instance.stage_id_list:
        machines = list(instance.stage_2_machines_map[i])
        machine_end: dict[str, int] = {k: 0 for k in machines}
        ordered_jobs = sorted(
            instance.job_id_list,
            key=lambda j: (j_i_2_start[j, i], j_i_2_end[j, i], j),
        )
        for j in ordered_jobs:
            s = j_i_2_start[j, i]
            e = j_i_2_end[j, i]
            picked = next((k for k in machines if machine_end[k] <= s), None)
            if picked is None:
                raise RuntimeError(
                    f"No free machine at stage {i} for job {j} start={s}"
                )
            schedule.add_ops_times_2_mc(i, picked, j, s, e)
            machine_end[picked] = e
    return schedule
```

### 3. New experiment config `metadata/20260420_profile_fixed_ns_config.yaml`

Mirror the LB-init config but chain MCF-LB → profile-fixed CP-SAT so
the CP step has an incumbent to fix. Confirm routix `subroutine_flow`
parameter syntax against existing usage before finalizing kwargs key.

## What we deliberately leave out (YAGNI)

- Swap operator and semi-active repair.
- LNS-only mode (`use_lns_only`).
- Persistent `self.cp_model` reuse and `delete_added_constraints`.
- Pre/post-solve visualizers.
- `error_if_infeasible` flag — raise unconditionally on infeasible
  with the fixed profile (the incumbent should always satisfy its own
  derived precedences).

## Verification

1. `uv run ruff check src/ffc_ddw_sum_et/orchestration/controller.py`
2. `uv run ruff format src/ffc_ddw_sum_et/orchestration/controller.py`
3. Smoke run:
   ```bash
   uv run python -m ffc_ddw_sum_et.main \
       --config metadata/20260420_profile_fixed_ns_config.yaml
   ```
4. Sanity check:
   - `obj_value` after `run_profile_fixed_ns` is `<=` obj_value after
     `run_mcf_lb` (CP-SAT cannot regress under fixed profile + hint).
   - CP-SAT-reported `solver.objective_value` matches
     `compute_window_et` post-build (a mismatch indicates the greedy
     machine assignment changed completion times — it must not).
