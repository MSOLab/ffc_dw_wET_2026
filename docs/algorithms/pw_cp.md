# `pw_cp`

`FFcDDWSubroutineController.pw_cp` — sliding-window CP-SAT refinement of an
existing feasible incumbent for the FFcDDW weighted earliness/tardiness
problem. Operations on each stage are partitioned into time-ordered batches;
a window slides across them, and at each position a CP-SAT sub-model
re-optimises the time and machine assignment of operations inside the window
while preserving the surrounding schedule structure via dummy bars and
profile-fixed precedence chains.

A companion composite, `FFcDDWSubroutineController.incremental_pw_cp`,
iterates `pw_cp` over a range of `unfixed_batch_count` values with
configurable repetition policies.

The algorithm-side entry point is
[`PwCpDispatcher`](../../src/ffc_ddw_sum_et/algorithm/pw_cp/dispatcher.py)
(conforms to the `Algorithm` / `AlgSpec` / `AlgRecord` contract — see
[algorithm-principles.md](../algorithm-principles.md)). The controller-side
adapter
[`controller.pw_cp`](../../src/ffc_ddw_sum_et/orchestration/controller.py)
resolves expression-grammar inputs, builds a
[`PwCpOption`](../../src/ffc_ddw_sum_et/algorithm/pw_cp/option.py),
dispatches via `PwCpDispatcher`, registers the resulting schedule, and
emits the per-step `_step_log.yaml` next to the controller's working
directory.

This algorithm mirrors the structural skeleton of `hybridflowshop`'s
`hfs_cp_lns.py:PwCpDispatcher`, but replaces the `common_spacing` /
makespan objective with a **partial weighted E/T** objective covering only
jobs whose last-stage operation is non-time-fixed in the partition.

## Signature

```python
def pw_cp(
    self,
    solver_thread_cnt: int = 1,
    batch_size: int | float | str = "m",
    step_size: int = 1,
    unfixed_batch_count: int = 1,
    left_profile_fixed_batch_count: int = 0,
    right_profile_fixed_batch_count: int = 0,
    enable_promotion_profile_fixed: bool = False,
    pf_method: PFMethod = "PF1",
    cp_tl: float | str | None = None,
    total_timelimit: float | str | None = None,
    batch_tl_mode: BatchTlMode = "constant",
    batch_tl_offset_seconds: float = 0.01,
    apply_cumulative_tl: bool = False,
    error_if_infeasible: bool = False,
    keep_step_schedules: bool = False,
    log_search_progress: bool = False,
    log_search_progress_max_steps: int | None = None,
    draw_gantt: bool = False,
    horizon_makespan_multiplier: float = 1.25,
) -> SubroutineReport: ...
```

| Parameter | Role |
| --- | --- |
| `solver_thread_cnt` | `CpSolver.parameters.num_workers` for each step’s solve. |
| `batch_size` | Operations per batch. `"m"` resolves to `⌈last_stage_mc_count⌉`; `"<x>nc"` resolves to `⌈x·n·c⌉`; other numeric strings parsed as `float → ceil`. |
| `step_size` | Window slide stride (in batches). |
| `unfixed_batch_count` | Width of the unfixed window in batches. `1` with `pf_method="PF1"` means fully fixed chain order (CP solver can only retime); `>=2` or `pf_method="PF0"` allows true reordering. |
| `left_profile_fixed_batch_count` | Batches on the left side of the window where precedence chain order is preserved but start times are free. |
| `right_profile_fixed_batch_count` | Batches on the right side of the window where precedence chain order is preserved but start times are free. |
| `enable_promotion_profile_fixed` | When `True`, profile-fixed operations of any job that has an unfixed operation on any stage are promoted into the unfixed set (widens exploration). |
| `pf_method` | Partial-fix policy for the profile-fixed bands. Decoded via [`decode_pf_method`](../../src/ffc_ddw_sum_et/algorithm/cumulative.py): `PF0` = stage-level time-based selection; `PF1` = per-machine chain stride 1; `PF2` = per-machine chain stride 2. |
| `cp_tl` | Per-step time limit for the CP-SAT solver. `float` = seconds; `"<x>nc"` → `x·n·c`; `None` = no limit. Resolved by [`resolve_value_expr`](../../src/ffc_ddw_sum_et/orchestration/tl_resolver.py). Feeds `batch_tl_mode`-based distribution. |
| `total_timelimit` | Total time budget distributed across steps by `batch_tl_mode`. Same grammar as `cp_tl`. |
| `batch_tl_mode` | `"constant"` divides `total_timelimit` equally among steps; `"proportional"` weights each step by its `cp_tl_seconds`. |
| `batch_tl_offset_seconds` | Per-step overhead margin subtracted from the allocated budget. |
| `apply_cumulative_tl` | When `True`, each step’s budget is `cp_tl_seconds · (step + 1) − elapsed`, floored at `cp_tl_seconds`. |
| `error_if_infeasible` | Raise when no schedule at all was produced; otherwise return empty report. |
| `keep_step_schedules` | When `True`, the dispatcher retains a deepcopy of the incumbent and candidate schedule per step (stored in `result.metrics["step_schedules"]`). |
| `log_search_progress` | When `True`, enable CP-SAT’s `log_search_progress=True` and `log_to_response=True` per step. The solver’s `response_proto.solve_log` is forwarded to the logger at INFO level (`[cp_sat step N]` prefixed). Used to verify hint validity. |
| `log_search_progress_max_steps` | Cap the number of steps logged (e.g. `1` to verify hints per run without bloating logs). `None` = all steps. |
| `draw_gantt` | When `True`, snapshots the incumbent before/after into `mcf_lb_phase_schedules` for post-run PNG rendering. |
| `horizon_makespan_multiplier` | Multiplier on the incumbent’s makespan to size the CP-SAT horizon: `horizon = ceil(incumbent.makespan × multiplier)`. Default `1.25`. Must be `>= 1.0`. |

## Segunda signature: `incremental_pw_cp`

```python
def incremental_pw_cp(
    self,
    solver_thread_cnt: int = 1,
    batch_size: int | float | str = "m",
    step_size: int = 1,
    unfixed_batch_count_min: int = 1,
    unfixed_batch_count_max: int = 1,
    increment_unfixed_batch_count_flag: Literal["always", "if_no_improvement"] = "always",
    left_profile_fixed_batch_count: int = 0,
    right_profile_fixed_batch_count: int = 0,
    enable_promotion_profile_fixed: bool = False,
    pf_method: PFMethod = "PF1",
    cp_tl: float | str | None = None,
    total_timelimit: float | str | None = None,
    batch_tl_mode: BatchTlMode = "constant",
    batch_tl_offset_seconds: float = 0.01,
    apply_cumulative_tl: bool = False,
    error_if_infeasible: bool = False,
    keep_step_schedules: bool = False,
    log_search_progress: bool = False,
    log_search_progress_max_steps: int | None = None,
    draw_gantt: bool = False,
    horizon_makespan_multiplier: float = 1.25,
) -> None: ...
```

For each `unfixed_batch_count` in `[min, max]`:

- **`"always"`**: invoke `self.pw_cp(unfixed_batch_count=count, ...)` once.
- **`"if_no_improvement"`**: invoke `self.pw_cp(...)` repeatedly at this
  count until a pass produces no improvement on the incumbent’s weighted
  E+T (FFcDDW’s primary objective — replaces hybridflowshop’s makespan
  criterion).

Each inner `pw_cp` call registers its own report. The composite itself
does not register. Per-iteration `temporarily_extended_context` tags each
inner call’s `call_context` so per-instance step-log paths do not collide
across iterations. `is_stopping_condition()` short-circuits both loops.

## Algorithm contract

`PwCpDispatcher.run(spec)` accepts an `AlgSpec` carrying:

- `spec.instance: FFcDDWParameters` — the full instance.
- `spec.ref_solution: FFcSchedule` — **required**. A feasible incumbent
  schedule from a preceding seeding subroutine (e.g.
  `calc_mcf_lb_and_derive_full_sch`, `neh_cp`).
- `spec.option: PwCpOption | None` — pre-resolved scalars (no expression
  strings); `None` falls back to `PwCpOption()` defaults.
- `spec.logger: logging.Logger | None` — falls back to
  `logging.getLogger(__name__)` when `None`.
- `spec.stop_predicate: Callable[[], bool] | None` — per-step early-exit
  hook.

It returns an `AlgRecord` whose `result.obj_value` is the full-instance
weighted E+T, `result.metrics["step_log"]` is a tuple of `PwCpStepEntry`,
`result.metrics["makespan"]` is the incumbent’s makespan, and
`progress_log` is a tuple of `ProgressLogEntry` entries from each step’s
`ObjectiveValueRecorder`. The dispatcher does not touch the filesystem or
any incumbent registry — those are the controller adapter’s
responsibility.

## Core concept: the five-region partition

Each frame partitions the operations on every stage into five regions
around the sliding unfixed window:

```
    LTF | LPF | UNFIXED | RPF | RTF
```

| Region | Name | Start times | Precedence | Capacity model |
| --- | --- | --- | --- | --- |
| LTF | Left Time Fixed | Pinned | Freely reordered (already placed) | Left dummy bars on each machine |
| LPF | Left Profile Fixed | Free | Chain order preserved (`pf_method`) | IntervalVar in cumulative |
| UNFIXED | Unfixed | Free | Full freedom | IntervalVar in cumulative |
| RPF | Right Profile Fixed | Free | Chain order preserved (`pf_method`) | IntervalVar in cumulative |
| RTF | Right Time Fixed | Pinned | Freely reordered (already placed) | Right dummy bars on each machine |

Only non-time-fixed operations (LPF + Unfixed + RPF) have CP `IntervalVar`
variables. Time-fixed operations contribute fixed dummy bars that reserve
machine capacity consumed by the pinned portions of the schedule.

## Pre-loop setup

1. **Incumbent preparation.** The `ref_solution` is deep-copied, made
   semi-active, and idle-time-inserted. This tidied schedule becomes the
   starting incumbent.
2. **Horizon.** `horizon = max(1, ceil(incumbent.makespan ×
   horizon_makespan_multiplier))`.
3. **Batch partitioning.** `build_stage_2_batch_list` partitions each
   stage’s operations into time-ordered batches (sorted by operation
   midpoint `(start + end) / 2`).
4. **Validation.** `validate_and_get_batch_count` verifies all stages have
   the same batch count. If `max_batch_cnt < unfixed_batch_count`, the
   dispatcher returns immediately with the incumbent.
5. **Iteration schedule.** `iteration_idxs = range(0, max_batch_cnt -
   unfixed_batch_count + 1, step_size)` — one window position per
   iteration.
6. **Time-limit resolution.** `resolve_per_step_tl` distributes the
   `cp_tl_seconds` / `total_timelimit_seconds` budget across iterations
   according to `batch_tl_mode`.

## Per-step loop

For each `(step, unfixed_start)`:

1. **Re-partition.** Rebuild the stage→batch mapping from the current
   incumbent (batch count invariant is asserted).

2. **Build five-region partition.** For each stage,
   `build_operation_partition` slices the batch list into LTF, LPF,
   Unfixed, RPF, RTF according to `unfixed_start` and the four
   `*_batch_count` parameters.

3. **Promotion (optional).** If `enable_promotion_profile_fixed=True`,
   jobs that appear in the unfixed set on any stage have their
   profile-fixed operations on all stages promoted into the unfixed set.

4. **Right-justified reference.** A deep-copy of the incumbent is
   right-justified via `delay_job_latest_leq_obj_contrib_all_stages` — ops
   are shifted as late as possible without exceeding their job’s due
   window upper bound. This `rj_schedule` feeds three roles:
   - LTF/RTF dummy-bar boundaries (per-machine earliest start / latest end
     of time-fixed ops)
   - Cross-stage precedence lower/upper bounds for jobs whose ops straddle
     time-fixed and non-time-fixed regions
   - CP start/end/ET hints

5. **Sub-instance.** `create_instance_of_job_subset` with jobs that have
   at least one non-time-fixed operation.

6. **CP model build** (`PwCpModelBuilder.build`):
   - **Variables.** `IntVar` (start, end) + `IntervalVar` for each
     non-time-fixed operation.
   - **Objective.** Partial weighted E/T: only jobs whose last-stage
     operation is non-time-fixed contribute `E_j` and `T_j` variables to
     `minimize(Σ w_e·E_j + w_t·T_j)`. Jobs time-fixed on the last stage
     contribute a constant offset (not added to the CP objective, since
     constants do not affect the argmin).
   - **Cumulative capacity.** Per-stage: all non-time-fixed `IntervalVar`s
     plus LTF left-dummy-bar and RTF right-dummy-bar on each machine
     (fixed intervals covering the reserved time regions).
   - **Job precedence.** Non-time-fixed op pairs on consecutive stages
     get `op_end[j,i] ≤ op_start[j,i+1]`. Hybrid pairs get constant
     bounds from `rj_schedule`.
   - **Profile-fix precedence.**
     `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule`
     on the profile-fixed-only schedule, respecting `pf_method` (by-machine
     stride).
   - **Hints.** Start and end hints from `rj_schedule` for all
     non-time-fixed ops; per-job E/T hints for objective jobs.

7. **CP-SAT solve.** A fresh `CpSolver()` with `num_workers =
   solver_thread_cnt`. Time limit resolved per step via `_apply_tl_and_deadline`
   (respects `apply_cumulative_tl`, `wall_clock_deadline_sec`, and
   per-step budget). An `ObjectiveValueRecorder` callback captures the
   progress log.

8. **Candidate reconstruction.** On `OPTIMAL`/`FEASIBLE`, the CP solution
   is decoded in two phases:
   - **Phase A — Replay non-time-fixed.** LPF + Unfixed + RPF ops are
     replayed in `cp_start` ascending order via
     `FFcSchedule.add_operation_2_stage` (greedy machine selection). Ops
     whose realised end-time differs from the CP promise increment the
     divergence counter.
   - **Phase B — Place right-time-fixed.** RTF ops are grouped by
     source (incumbent) machine; source groups are sorted by earliest
     start time; each group is matched 1:1 to an un-dispatched target
     machine with the smallest latest-end-time. RTF ops are placed via
     `add_ops_times_2_mc` (explicit time + machine, no slide). On overlap,
     falls back to `add_operation_2_stage`.
   - The reconstructed schedule is made semi-active and idle-time-inserted.

9. **Acceptance.** The candidate is accepted iff its full-instance
   weighted E+T is **strictly less** than the incumbent’s value before the
   step. Ties are rejected — only strict improvement advances the
   incumbent.

10. **Per-step log.** Appends a `PwCpStepEntry`:
    ```yaml
    step: <int>
    elapsed_time: <sec>
    TL: <sec | null>
    elapsed_portion: <ratio | null>
    unfixed_batch_start_idx: <int>
    non_time_fixed_op_count: <int>
    sub_job_count: <int>
    incumbent_obj_before: <float>
    cp_obj: <float | null>
    incumbent_obj_after: <float>
    accepted: <bool>
    status: <str>
    wall_seconds: <float>
    cp_divergence_count: <int>
    ```

11. **Early-exit checks.** After each step:
    - `spec.stop_predicate()` — honours the controller’s stop signal.
    - `wall_clock_deadline_sec` — stops immediately when exceeded.

## Post-loop finalization

- Computes final weighted E/T for the incumbent against the **full**
  instance.
- Returns an `AlgRecord` with:
  - `work_status=FEASIBLE`
  - `termination_reason=COMPLETED` or `STOP_REQUESTED`
  - `result.obj_value = SE + ST`
  - `result.metrics["step_log"]` = tuple of `PwCpStepEntry`
  - `progress_log` = tuple of `ProgressLogEntry` (one per callback,
    with full-instance E/T offsets applied)
  - On `STOP_REQUESTED`, an extra `ProgressLogEntry` is appended with
    the final incumbent value.
- If `keep_step_schedules=True`, `metrics["step_schedules"]` contains
  `(step_idx, incumbent_before_sch, cand_sch | None)` per step.

## Output conventions

- `SubroutineReport.obj_bound` is always `None` from this subroutine (no
  lower bound is computed).
- The dumped `_step_log.yaml` is a list of dicts, one per step, in the
  shape above.
- The progress log in `_obj_log.json` references the dispatcher’s
  `progress_log` with per-step CP solver callback timestamps.

## Side effects

| State | Set by |
| --- | --- |
| `ctx.solution_manager` | Single registration (once, after the dispatcher returns). |
| `ctx.mcf_lb_phase_schedules` | Before/after snapshots when `draw_gantt=True`. |
| `<subroutine_dir>/…_step_log.yaml` | Dumped when `try_get_file_path_for_subroutine` is callable. |
| `ctx.call_context` | Extended per iteration by `incremental_pw_cp` (batch_NNN/reps_NNN). |

## Early-return / error paths

| Condition | Behaviour |
| --- | --- |
| `spec.ref_solution is None` | `ValueError` |
| `spec.instance` not `FFcDDWParameters` | `TypeError` |
| `max_batch_cnt < unfixed_batch_count` | Return immediately with the incumbent as-is (info-logged). |
| Step with empty non-time-fixed set | Skip the step (debug-logged). |
| `processing_time > horizon` | `ValueError` |
| Per-step CP infeasible / unknown | No candidate produced; step logged with `cp_obj=None`, `accepted=False`; continue. |
| `stop_predicate()` fires mid-loop | Return `STOP_REQUESTED` record with progress up to that point. |
| `wall_clock_deadline` exceeded | Return `STOP_REQUESTED` record. |

## Dependencies

- [`BaseModelBuilder`](../../src/ffc_ddw_sum_et/algorithm/cumulative.py) —
  `make_params`, `apply_start_hints_from_start_time_map`,
  `apply_end_hints_from_end_time_map`,
  `add_stage_ops_precedence_constraints_after_dispatch_from_schedule`.
- [`decode_pf_method`, `PFMethod`](../../src/ffc_ddw_sum_et/algorithm/cumulative.py)
- [`OperationPartition`](../../src/ffc_ddw_sum_et/algorithm/pw_cp/partition.py) —
  `build_stage_2_batch_list`, `build_operation_partition`,
  `validate_and_get_batch_count`.
- [`PwCpModelBuilder`](../../src/ffc_ddw_sum_et/algorithm/pw_cp/cp_model.py) —
  `build()`, `build_full_schedule_from_cp()`.
- [`PwCpOption`](../../src/ffc_ddw_sum_et/algorithm/pw_cp/option.py)
- [`resolve_per_step_tl`](../../src/ffc_ddw_sum_et/algorithm/step_tl_resolver.py)
- [`ObjectiveValueRecorder`](../../src/ffc_ddw_sum_et/algorithm/cpsat_callbacks/obj_value_recorder.py)
- [`FFcSchedule`](../../src/ffc_ddw_sum_et/solution/ffc_schedule.py) —
  `deepcopy`, `make_semi_active`, `insert_idle_time`,
  `delay_job_latest_leq_obj_contrib_all_stages`, `remove_operations`,
  `add_operation_2_stage`, `add_ops_times_2_mc`,
  `get_job_end_time`, `get_machine_latest_end_time`,
  `get_jik_2_start_time_map`, `get_jik_2_end_time_map`,
  `iter_operations_on_stage`.
- [`FFcDDWParameters.create_instance_of_job_subset`](../../src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py)
- [`compute_weighted_earliness_tardiness`](../../src/ffc_ddw_sum_et/solution/objectives.py)
- [`resolve_value_expr`](../../src/ffc_ddw_sum_et/orchestration/tl_resolver.py)
- `routix.io.dump_yaml`, `routix.report.SubroutineReport`.

## Related

- `hybridflowshop/controller/hfs_cp_lns.py` — source of the sliding-window
  CP and incremental refinement pattern. There the objective is makespan
  plus common spacing with per-batch lower bound; here the objective is
  weighted E/T with a partial-job formulation.
- `hybridflowshop/pw_cp/dispatcher.py` — source of the partition model
  (dummy bars, non-fixed-job precedence constraints, profile-fix,
  three-phase schedule reconstruction).
- `neh_cp` — alternative CP-based refinement that appends jobs
  incrementally. `pw_cp` partitions operations in time instead, making it
  complementary (one refines by job count, the other by time window).
- `mcf_lb` — seeding subroutine that produces the initial incumbent
  required by `pw_cp`. `pw_cp` is typically chained after
  `calc_mcf_lb_and_derive_full_sch` or `neh_cp`.
