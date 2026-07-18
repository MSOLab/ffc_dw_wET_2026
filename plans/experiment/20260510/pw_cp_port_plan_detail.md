# PW-CP Port — Detail Decisions (companion to `sw_cp_port_plan.md`)

This file freezes the user-confirmed implementation decisions surfaced
during the planning conversation on 2026-05-10. Read together with
`sw_cp_port_plan.md`; this file overrides any conflicting wording.

## Naming and placement

- **Controller step method**: `sw_cp` (no `run_` prefix). Matches the
  `neh_cp` peer. The `subroutine_flow.method:` key in YAML resolves
  directly to this attribute.
- **Algorithm package path**: `src/ffc_ddw_sum_et/algorithm/sw_cp/`
  (`__init__`, `option`, `step_log`, `partition`, `cp_model`,
  `dispatcher`).
- **Experiment YAML**: `metadata/20260510/sw_cp_grid.yaml`. The
  `configs/` path mentioned in the plan does not exist in this repo —
  every scenario YAML lives under `metadata/<date>/`.

## Subroutine chain in YAML

Each scenario starts with the MCF-LB seed (cheapest seed that produces
a fully-feasible incumbent) and refines via PW-CP:

```yaml
subroutine_flow:
  - method: calc_mcf_lb_and_derive_full_sch
    # …default seeding params (mirror 20260507_*)
  - method: sw_cp
    # …PW-CP option fields below
```

Grid (single YAML, 4 scenarios):

| `unfixed_batch_count` | `pf_method` | scenario suffix |
| --- | --- | --- |
| 2 | PF0 | `_u2_pf0` |
| 2 | PF1 | `_u2_pf1` |
| 3 | PF0 | `_u3_pf0` |
| 3 | PF1 | `_u3_pf1` |

## Wall-clock plumbing (controller → dispatcher)

`sw_cp` step method computes `wall_clock_deadline_sec = time.monotonic()
+ self.timer.get_remaining_sec(self.stopping_criteria.timelimit)` and
threads it through `PwCpOption.wall_clock_deadline_sec`. The dispatcher
clamps each batch's CP-SAT `max_time_in_seconds` by the remaining
deadline (mirror `neh_cp` lines 258-274), and breaks the loop with
`TerminationReason.STOP_REQUESTED` when the deadline is hit. The
controller-side `stop_predicate=self.is_stopping_condition` is also
threaded into `AlgSpec` so the loop can react to *either* the deadline
or the controller-managed stop predicate.

## `_build_full_schedule_from_cp` — machine reassignment policy

Use hybridflowshop's validated pattern (`create_sw_cp_schedule` in
`/home/hjt/code/hybridflowshop/.../cpsat_model_2/sw_cp.py`):

1. Start from `rj_schedule.deepcopy()` and remove every non-time-fixed
   `(j, i, k)` op via `FFcSchedule.remove_operations`.
2. For each non-time-fixed `(j, i)`:
   - sort by `(start_time, -end_time)` for deterministic dispatch order;
   - call `schedule.add_operation_2_stage(stage_id=i, job_id=j,
     duration=p[j,i], release_t=cp_start)` — the stage selects the
     earliest-start-then-idle machine via the existing
     `select_machine_by_earliest_start_then_idle` policy;
   - assert `schedule.get_job_end_time(i, j) == cp_end`. If the
     assertion fires, the cumulative-vs-explicit-machine policy
     diverged and we want a loud failure rather than a silent skew.

## `delay_job_latest_leq_obj_contrib_all_stages` — semantics

Two-pass right-justification on `FFcSchedule`:

1. **Last stage**: delegate to existing
   `self.delay_job_latest_leq_obj_contrib(job_2_dw_ub_map)`. This
   freezes every `C_j` at an objective-non-increasing position (early
   jobs may slide into the due window, on-time jobs may slide right
   inside it, tardy jobs are pinned).
2. **Stages c-1, c-2, …, 1**: same shape as hybridflowshop's
   `make_right_justified` (verified at
   `/home/hjt/code/hybridflowshop/.../schedule_lite.py:1571`):

   ```text
   for stage_idx in range(len(self.stages) - 2, -1, -1):
       stage_id = self.stages[stage_idx]
       next_stage_id = self.stages[stage_idx + 1]
       next_stage_start_map = (cache: job -> start at next stage)
       for mc_id in machines_per_stage[stage_id]:
           seq = self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id]
           machine_next_start = +inf
           new_seq_rev = []
           for job_id, old_start, old_end in reversed(seq):
               duration = old_end - old_start
               cap = min(next_stage_start_map[job_id],
                         machine_next_start)
               new_end = cap
               new_start = new_end - duration
               new_seq_rev.append((job_id, new_start, new_end))
               machine_next_start = new_start
           self.__stage_2_mc_2_job_tuple_seq[stage_id][mc_id] = list(reversed(new_seq_rev))
       self._rebuild_stage_time_caches(stage_id)
   ```

Invariants (test target):

- every `C_j` (last-stage end) is unchanged across the call;
- every per-machine sequence order is unchanged;
- every duration `p[j,i] = end - start` is unchanged;
- `compute_weighted_earliness_tardiness` returns the same value before
  and after (objective-preserving, not just non-increasing — the
  last-stage pass already guarantees per-job ET non-increasing, and
  earlier-stage passes don't touch any `C_j`);
- `validate_schedule(sched, instance.stage_2_job_2_p_map)` passes
  (no overlap, all precedences satisfied).

## Profile-fix precedence range

`PwCpModelBuilder` builds `profile_fixed_schedule = rj_schedule.deepcopy()
.remove_operations(non_profile_fixed_ops)` and passes it to the existing
`BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule`
with `decode_pf_method(option.pf_method)`. Effect: precedence arcs are
added only inside LPF and inside RPF — the unfixed window stays free.
LTF/RTF are absent from the schedule (removed) so the helper never
touches them.

## Step-log artifact

After `_register`, the controller method serialises
`record.result.metrics["step_log"]` (a tuple of `PwCpStepEntry`
dataclasses) as YAML at the path returned by
`self.try_get_file_path_for_subroutine("_step_log.yaml")`. Same shape /
location convention as `neh_cp` so the existing
`reporting._write_analysis_sheets` aggregator and any downstream CSV
analysis pick it up unchanged.

## Per-step Gantt

`sw_cp` accepts a per-call `draw_gantt: bool = False` kwarg. When True,
the controller appends two snapshots to the existing
`mcf_lb_phase_schedules` list (the mechanism is generic despite the
name):

- `(self._mcf_lb_phase_name("sw_cp_before"), incumbent.schedule.deepcopy())`
- `(self._mcf_lb_phase_name("sw_cp_after"), result.schedule.deepcopy())`

The post-run reporter renders these into PNGs alongside MCF-LB phase
PNGs. Naming is deliberately misleading (`mcf_lb_phase_schedules`
predates this feature); a TODO entry should track renaming the
container to a generic `phase_schedules`.

## CP model — boundary constants from `rj_schedule`

Every CP construction step reads start/end constants from
`rj_schedule = incumbent.deepcopy();
rj_schedule.delay_job_latest_leq_obj_contrib_all_stages(
    instance.job_2_dw_ub_map)` — never from the raw incumbent. This is
the load-bearing reason the new schedule method is required.

The right dummy bar is `[right_boundary[i,k], horizon]` with **fixed**
endpoints (no `common_spacing` IntVar). The left dummy bar is
`[0, left_boundary[i,k]]`, also fixed. Bars with zero length are
skipped. All boundaries are sourced from `rj_schedule`, which
maximises the slack available to the unfixed window.

## Objective (partial weighted E/T)

For each `j ∈ sub_jobs` whose **last-stage** op is non-time-fixed
(`(j, k_last) ∈ partition[last_i].non_time_fixed`):

- create `E_j` and `T_j` via `add_max_equality` (matches
  `BaseModelBuilder._define_objective`);
- contribute `w_e[j] * E_j + w_t[j] * T_j` to the CP objective.

Jobs with last-stage op time-fixed contribute a constant `et_offset`
(logged for diagnostic purposes; not added to the CP objective since
constants don't affect `argmin`).

If no job in `sub_jobs` has a non-time-fixed last-stage op, the batch
is skipped (no CP variables to optimise — falls under the "if not
sub_jobs: continue" early exit).

## Test scope

Plan-mandated 5 unit-test files plus the schedule-method test:

1. `tests/algorithm/sw_cp/test_option.py`
2. `tests/algorithm/sw_cp/test_partition.py`
3. `tests/algorithm/sw_cp/test_cp_model.py`
4. `tests/algorithm/sw_cp/test_dispatcher.py`
5. `tests/algorithm/sw_cp/test_dispatcher_stop.py`
6. `tests/algorithm/sw_cp/test_dispatcher_no_ref_solution.py`
7. `tests/solution/test_ffc_schedule_delay_all_stages.py`
8. Register `sw_cp` in
   `tests/algorithm/test_algorithm_contracts.py` so the shared
   AlgRecord shape-checks run.

## Out of scope (deferred)

- `tighten_ranges`, `use_lns_only`, `non_time_fixed_op_time_limit_multiplier`
- `minimize_makespan_lex` lex 2-phase
- `common_spacing` proxy objective
- `make_semi_active_after_cp` toggle (PW-CP recomputes objective from
  the merged full-schedule which is already semi-active'd)
- Renaming `mcf_lb_phase_schedules` → `phase_schedules`
