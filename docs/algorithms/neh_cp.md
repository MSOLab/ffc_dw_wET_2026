# `neh_cp`

`FFcDDWSubroutineController.neh_cp` — incremental batched CP-SAT constructor
for the FFc DDW weighted earliness/tardiness problem. Jobs are ordered
up-front, appended in batches, and after each batch a CP-SAT model over the
current job subset refines the warm-start that carries into the next step.

The algorithm-side entry point is
[`NehCpDispatcher`](../../src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py)
(conforms to the `Algorithm` / `AlgSpec` / `AlgRecord` contract — see
[algorithm-principles.md](../architecture/algorithm-principles.md)). The
controller-side adapter
[`controller.neh_cp`](../../src/ffc_ddw_sum_et/orchestration/controller.py)
resolves expression-grammar inputs, builds an
[`NehCpOption`](../../src/ffc_ddw_sum_et/algorithm/neh_cp/option.py),
dispatches via `NehCpDispatcher`, registers the resulting schedule, and
emits the per-batch `_step_log.yaml` next to the controller's working
directory. The solve for each batch optionally runs a lexicographic two-stage
optimize: primary minimizes weighted E/T, secondary minimizes makespan under
an E/T ceiling — mirroring `hybridflowshop/controller/neh_cp.py` (where the
primary is makespan and the secondary is sum C_i).

## Signature

### `neh_cp` (job-priority-based ordering)

```python
def run(
    self,
    job_priority: NehCpJobPriority = "weight-due-pos",
    solver_thread_cnt: int = 1,
    ...
) -> SubroutineReport: ...
```

### `neh_cp_midpoint_seq`, `neh_cp_first_stage_seq`, `neh_cp_completion_seq` (incumbent-derived ordering)

These three methods derive the job insertion sequence from the current
incumbent schedule (via
[`schedule_job_sequence`](../../src/ffc_ddw_sum_et/solution/schedule_sequence.py)).
Each uses a different sort key (all ascending, tie-broken by the secondary
key then by `job_priority` rank):

| Method | `job_seq_source` | Primary key | Secondary key |
| --- | --- | --- | --- |
| `neh_cp_midpoint_seq` | `midpoint` | `(first_stage_start + end_stage_end) / 2` | end stage end |
| `neh_cp_first_stage_seq` | `first_stage` | first-stage start | last-stage end |
| `neh_cp_completion_seq` | `completion` | end stage end | first-stage start |

`end_stage_end` defaults to the **last** stage's end time
(`seq_end_stage=-1`). The `seq_end_stage` parameter (available on
`neh_cp_midpoint_seq` and `neh_cp_completion_seq`) selects a different
stage — see "`seq_end_stage` parameter" below.

**Fallback**: when no incumbent schedule is available,
`job_priority` is used instead (a `WARNING` is logged).
`job_priority` is always computed — it serves as tie-break rank
and as fallback ordering.

**Diversity diagnostics**: when an incumbent is present, all three
sequence modes are computed and their pairwise distances (via
`normalized_mean_rank_distance`) are emitted in a single `INFO` log line,
along with distance to the `job_priority` sequence and the previous
NEH-CP sequence (if any). The first five jobs of the chosen sequence are
also logged (`head=`).

All three methods accept the same parameters as `neh_cp` and delegate
to the shared private core `_run_neh_cp`.

### `seq_tiebreak` parameter (`neh_cp_midpoint_seq` only)

`neh_cp_midpoint_seq` additionally accepts `seq_tiebreak:
ScheduleSeqSource | None = None`. It overrides the secondary sort key
within midpoint tie groups — where `m = (first_stage_start +
last_stage_end) / 2` is equal, the default secondary key is first-stage
start; `seq_tiebreak="completion"` uses last-stage end instead, which
reverses the order within each tie group (because `ls = 2m − fs` is
decreasing in `fs` for fixed `m`).

Only `midpoint` is exposed because the other modes have no
distinguishable tie-break keys:

| Source | Default 2nd key | Alternative candidate | Why identical |
| --- | --- | --- | --- |
| `first_stage` | `ls` | `midpoint` = `(fs+ls)/2` | For fixed `fs`, `midpoint` is monotonic in `ls` — same order. |
| `completion` | `fs` | `midpoint` = `(fs+ls)/2` | For fixed `ls`, `midpoint` is monotonic in `fs` — same order. |
| `midpoint` | `fs` | `ls` | For fixed `m`, `ls = 2m − fs` is **decreasing** in `fs` — order reverses. |

The same algebra is enforced by regression tests in
`tests/solution/test_schedule_sequence.py`.

### `seq_end_stage` parameter (`neh_cp_midpoint_seq`, `neh_cp_completion_seq`)

`neh_cp_midpoint_seq` and `neh_cp_completion_seq` additionally accept
`seq_end_stage: int = -1`. It selects which stage provides the end time
(`ls`) for the sort key.

The sort key tables use `end_stage_end` (the end time of the stage at
`schedule.stages[seq_end_stage]`) in place of `last_stage_end`:

| Mode `(/param)` | Primary key | Notes |
| --- | --- | --- |
| `completion3` | `end_stage_end` (`ls'`) | `neh_cp_completion_seq`, `seq_end_stage: -2` |
| `midpoint3` | `(first_stage_start + end_stage_end) / 2` (`m'`) | `neh_cp_midpoint_seq`, `seq_end_stage: -2`, `seq_tiebreak: completion` |

`seq_end_stage` is a negative index into `schedule.stages`: `-1`
(default) = last stage (byte-identical to the previous behaviour),
`-2` = second to last. It is validated by `schedule_job_sequence`
(`schedule_sequence.py`): values outside
`[-len(stages), -1]` raise `ValueError`. In the controller, a value
whose absolute value exceeds the instance's stage count is clamped to
`-c` with a `WARNING` log.

**Motivation.** The post-processing pipeline applies
`make_semi_active` (left-shift) to every stage, but
`insert_idle_time` uses due windows to shift ops only on the **last
stage** — so `ls` carries both schedule structure and due-date
adjustment while `ls'` isolates the upstream schedule structure.
Moving the axis to `(last-1)` tests whether the sort key should see
the schedule as it is before the due-window adjustment.

The tie-break degeneracy argument for `end_stage_index=-1` does **not**
carry over: `ls'` is not monotonic in `ls` (the last-stage idle
insertion varies per job), so `completion3` / `midpoint3` produce
distinct orders from their `-1` counterparts.
Verification: `tests/solution/test_schedule_sequence.py`
(`test_non_alias_completion_end_stage_minus2_vs_minus1`).

`first_stage` is deliberately **not** exposed (see §8 of
`plans/experiment/20260804/neh_cp_last1_stage_seq.md`): it is
consistently the worst-performing mode across the 1440-instance
grid, and a `seq_end_stage` knob on it would add unused config
surface.

| Parameter | Role |
| --- | --- |
| `job_priority` | See "Job ordering". |
| `solver_thread_cnt` | `CpSolver.parameters.num_workers` for **both** stage-1 and stage-2 solves. |
| `added_batch_size` | Jobs appended per step after the first. |
| `cp_tl` | Per-batch time limit for the primary (E/T) solve. `float` = seconds; `"<x>nc"` → `x·n·c`; `"<x>c"` → `x·c`; `None` = no limit. Resolved by [`resolve_cp_tl`](../../src/ffc_ddw_sum_et/orchestration/tl_resolver.py). |
| `apply_cumulative_tl` | When `True`, the stage-1 budget at step `k` is `cp_tl · (k+1) − elapsed`, floored at `cp_tl`. Applies only to stage 1 — stage 2 always uses a flat per-batch limit. |
| `pf_method` | Partial-fix policy fed to `add_stage_ops_precedence_constraints_after_dispatch_from_schedule` (`PF0` = stage-level time-based selection; `PF1` = per-machine chain stride 1; `PF2` = per-machine chain stride 2). Decoded via [`decode_pf_method`](../../src/ffc_ddw_sum_et/algorithm/cumulative.py). Applied in **both** stage 1 (against the previous step's `partial_sol`) and stage 2 (against the same-step stage-1 `partial_sol`). |
| `skip_pf_below_obj` | Suppresses the stage-1 partial-fix for the current batch when the previous step's E/T ≤ threshold. `"makespan"` → previous `partial_sol.makespan`; numeric or numeric-string → parsed as `float`. Affects stage 1 only — stage 2 always applies partial-fix. |
| `make_semi_active_after_cp` | When `True`, the stage-1 CP solution is post-processed with `make_semi_active` + `insert_idle_time` before the stage-1 pick. The tidied schedule replaces the raw CP decode only if its weighted E/T is **strictly** lower. Disabled for `dispatched` (already tidied in step 4) and for stage 2. |
| `minimize_makespan_lex` | Enables the stage-2 solve per batch. |
| `cp_tl_2nd_obj` | Per-batch time limit for stage 2. Same grammar as `cp_tl`. Falls back to `cp_tl` when `None` and `minimize_makespan_lex=True`; ignored otherwise. |
| `error_if_infeasible` | Raise when no schedule at all was produced; otherwise return an empty report. Does **not** police per-batch infeasibility — those steps fall back to `dispatched`. |

## Algorithm contract

`NehCpDispatcher.run(spec)` accepts an `AlgSpec` carrying:

- `spec.instance: FFcDDWParameters` — the full instance.
- `spec.option: NehCpOption | None` — pre-resolved scalars (no expression
  strings); `None` falls back to `NehCpOption()` defaults.
- `spec.logger: logging.Logger | None` — falls back to
  `logging.getLogger(__name__)` when `None`.

It returns an `AlgRecord` whose `result.obj_value` is the final schedule's
weighted E+T, `result.metrics["step_log"]` is a tuple of `NehCpStepEntry`,
and `progress_log` is one `ProgressLogEntry` per batch. The dispatcher does
not touch the filesystem or any incumbent registry — those are the
controller adapter's responsibility.

## Pre-loop setup

Let `n = instance.job_count`, `c = instance.stage_count`.

- **Validation.** `skip_pf_below_obj` that is neither `None` nor `"makespan"`
  is coerced to `float`; `ValueError` on parse failure. `n == 0` →
  `RuntimeError`.
- **Time-limit resolution.** `cp_tl_seconds = resolve_cp_tl(cp_tl, n, c)`.
  When `minimize_makespan_lex=True`, `cp_tl_2nd_obj_seconds =
  resolve_cp_tl(cp_tl_2nd_obj ?? cp_tl, n, c)`; otherwise `None`.
- **Horizon.** `horizon = sum(params.p.values())` over the full instance —
  used for **every** stage-1 model build. Stage 2 tightens it per batch.
- **Job ordering.** One of two paths:
  1. **Priority-based** (`neh_cp`): `job_sequence = neh_cp_job_sequence(instance, job_priority)`
     (module helper in `ffc_ddw_sum_et.algorithm.neh_cp.sequence`).
     - `"weight-due-pos"`: `(max(w⁻, w⁺) desc, w⁻+w⁺ desc, d⁺−d⁻ asc,
       position asc)`.
     - `"due-weight-pos"`: `(max(0, d⁺−p_last) asc, d⁺ asc, d⁻ asc, w⁻+w⁺ desc,
       position asc)`.
  2. **Incumbent-derived** (`neh_cp_*_seq`):
     `schedule_job_sequence(incumbent.schedule, source, tiebreak_rank=rank_map)`
     where `rank_map` is derived from the `job_priority` sequence and serves
     as the final tie-break. Falls back to path 1 when no incumbent is
     registered (warning logged). The derived sequence is validated to be a
     permutation of the instance's `job_id_list`; missing or extra jobs are
     corrected by preserving the derived order and appending missing jobs in
     `job_priority` order (warning logged).
- **Batch shape.** `first_batch_size = max(added_batch_size,
  max_m_per_stage · 2)`. Batch 0 is `job_sequence[:first_batch_size]`;
  subsequent batches slice the tail in chunks of `added_batch_size`.
- **Warm-start state.** A single `MixedDispatcher(instance, logger=ctx.logger)`
  is reused across all batches. `partial_sol`, `current_jobs`, `sub_step_log`
  start empty; `last_obj_value = 0`.

## Per-batch loop

For each `(step, batch)`:

1. **Append & sub-instance.** `current_jobs.extend(batch)`; rebuild
   `sub_instance = FFcDDWParameters.create_instance_of_job_subset(instance,
   set(current_jobs))`.

2. **Stage-1 model build.** A fresh `BaseModelBuilder()` builds a full
   cumulative CP-SAT model for `sub_instance` at horizon
   `sum(full_instance.p.values())`. This is the default E/T-minimization mode
   of `BaseModelBuilder.build` — it returns `(mdl, params, op_vars, et_vars)`.

3. **Stage-1 partial-fix.** If `partial_sol is not None` and `skip_pf` is
   `False`, apply
   `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule`
   against the previous step's `partial_sol` with `(by_machine, stride) =
   decode_pf_method(pf_method)`. `skip_pf` is `True` when
   `skip_pf_below_obj is not None` and `last_obj_value <= criteria_value`,
   where `criteria_value = partial_sol.makespan` for `"makespan"` or the
   numeric threshold otherwise (`0` when `partial_sol is None`).

4. **Warm-start dispatch.**
   - `base = partial_sol.deepcopy()` if available, otherwise a fresh
     `FFcSchedule(jobs=instance.job_id_list, stages=..., machines_per_stage=...)`
     covering all jobs and stages of the full instance.
   - `dispatched = MixedDispatcher.get_best_mixed_schedule_by_sequence(batch,
     schedule=base, from_stage=first_stage_id, head_for_all_stages=True,
     criteria="makespan")`.
   - On `None` return, the code falls back to `dispatch_job_by_stages` for
     each job in `batch` on top of `base` (warning logged).
   - `dispatched.make_semi_active(stage_2_job_2_p)` then
     `dispatched.insert_idle_time(due_window_map, ewt_map, twt_map)`.

5. **Stage-1 hints.** `BaseModelBuilder.apply_hints_from_schedule(mdl, params,
   op_vars, et_vars, dispatched)` — injects start, end, and per-job E/T hints
   computed from the dispatched schedule's last-stage completion times.

6. **Stage-1 solve.** Fresh `cp_model.CpSolver()`;
   `num_workers = solver_thread_cnt`. Time limit:
   - `cp_tl_seconds is None` → unbounded.
   - `apply_cumulative_tl=False` → `max_time_in_seconds = cp_tl_seconds`.
   - `apply_cumulative_tl=True` → `applied_tl = cp_tl_seconds · (step + 1) −
     (time.monotonic() − start_elapsed)`. Below `cp_tl_seconds` it is floored
     at `cp_tl_seconds` (warning logged); otherwise used as-is (info logged).

7. **Stage-1 pick.**
   - `OPTIMAL`/`FEASIBLE`: decode `(j, i) → start/end` for every `(j, i) ∈
     params.j_list × params.i_list`, build `cp_sch` via
     `build_schedule_from_op_starts(instance, …, jobs=current_jobs)`. Compute
     `(se_new, st_new) =
     compute_weighted_earliness_tardiness(cp_sch, sub_instance)`.
   - **Tidy pass (optional).** When `make_semi_active_after_cp=True`, apply
     `cp_sch.make_semi_active(stage_2_job_2_p)` then
     `cp_sch.insert_idle_time(due_window_map, ewt_map, twt_map)`, recompute
     `(se_tidy, st_tidy)`, and when `se_tidy+st_tidy <
     se_new+st_new` (strictly) overwrite `(se_new, st_new) =
     (se_tidy, st_tidy)` and log the reduction. A tie is discarded — the raw
     CP decode wins. The tidy mutation on `cp_sch` is retained either way,
     but it only matters when the new values are strictly better (otherwise
     the subsequent compare happens against the untidied-equivalent totals).
   - **Pick.** Compute `(se_dis, st_dis) =
     compute_weighted_earliness_tardiness(dispatched, sub_instance)`; set
     `partial_sol = cp_sch if (se_new+st_new) <= (se_dis+st_dis) else
     dispatched`.
   - Otherwise (infeasible / unknown): `partial_sol = dispatched`
     (info logged). `make_semi_active_after_cp` has no effect here because
     `dispatched` was already tidied in step 4.

8. **Stage-1 E/T record.** `(se_stage1, st_stage1) =
   compute_weighted_earliness_tardiness(partial_sol, sub_instance)`;
   `stage1_obj = se_stage1 + st_stage1`.

9. **Stage-2 solve (optional).** Triggered when
   `minimize_makespan_lex=True` **and** `partial_sol.makespan > 0`. `ran_2nd_obj`
   is `False` otherwise.
   - **Model build.** Reuses the same `builder` instance:
     `builder.build(sub_instance, horizon=int(partial_sol.makespan),
     minimize_makespan_lex=True, et_ub=stage1_obj)`.
     In this mode the builder still constructs every `E_j`, `T_j` and the
     weighted E/T terms, then adds `sum(et_terms) <= math.floor(et_ub)`, ties
     a new `makespan` `IntVar = max(op_end[j, last_i])` via
     `add_max_equality`, and switches the objective to `minimize(makespan)`.
     The horizon shrink (`=partial_sol.makespan`) plus the E/T constraint
     jointly guarantee any feasible solution is ≤ stage-1 lex.
   - **Partial-fix.** Same `decode_pf_method(pf_method)`, now against the
     **same-step** stage-1 `partial_sol`. `skip_pf_below_obj` does not apply
     here.
   - **Hints.** `apply_hints_from_schedule(mdl_2, params_2, op_vars_2,
     et_vars_2, partial_sol)` seeds starts, ends, and E/T from the stage-1
     pick.
   - **Solve.** Fresh solver; `num_workers = solver_thread_cnt`;
     `max_time_in_seconds = cp_tl_2nd_obj_seconds` when set (flat — no
     cumulative variant).
   - **Accept.** On `OPTIMAL`/`FEASIBLE`, decode the same way as stage 1 and
     overwrite `partial_sol = cp_sch_2`, `ran_2nd_obj = True`. Info-logs the
     transition `horizon_2 → cp_sch_2.makespan` under the `E/T <= stage1_obj`
     ceiling. On infeasible/unknown, keep stage-1 `partial_sol` (info logged).

10. **Per-step log.** Recompute `(se, st) =
    compute_weighted_earliness_tardiness(partial_sol, sub_instance)` and
    append to `sub_step_log`:

    ```yaml
    step: <int>
    elapsed_time: <sec-since-start_elapsed>
    sub_obj: <float, weighted E+T>
    sub_obj_lb: <float, stage-1 solver.best_objective_bound or 0.0>
    gap: <float | None, ub / lb (None when lb=0 and ub>0; 0 when both 0)>
    job_count: <len(current_jobs)>
    makespan: <int, partial_sol.makespan>
    ran_2nd_obj: <bool>
    ```

    `sub_obj_lb` comes from the stage-1 CP-SAT solver
    (`solver.best_objective_bound`) when `status ∈ {OPTIMAL, FEASIBLE}`,
    else `0.0`. The stage-2 makespan solver's bound is **not** used here —
    its objective is makespan, not E/T. `gap = ub / lb` exactly: it is **not**
    a relative gap. `last_obj_value = se + st` feeds the `skip_pf_below_obj`
    check of the **next** batch.

## Post-loop finalization

- `partial_sol is None` (only possible when `batches` was empty — i.e., the
  instance had jobs but the first batch was empty, which the current batch
  construction cannot produce) → `error_if_infeasible` decides between
  `RuntimeError` and an empty `SubroutineReport(obj_value=None,
  obj_bound=None)`.
- Otherwise: compute weighted E/T for `final = partial_sol` against the
  **full** `instance` (not `sub_instance`), build
  `SubroutineReport(elapsed_time=..., obj_value=float(se+st),
  obj_bound=None)`, and
  `ctx.solution_manager.register(report,
  FFcDDWSolution(schedule=final, obj_value=...))`.
- If `ctx.get_file_path_for_subroutine("_step_log.yaml")` succeeds, dump
  `sub_step_log` to that path via `routix.io.dump_yaml`.
  `AttributeError` is swallowed — the file is simply not written.

## Output conventions

- `SubroutineReport.obj_bound` is always `None` from this subroutine (no
  lower bound is computed here).
- **Per-step log (`_step_log.yaml`):**
  - For `neh_cp`: a **list** of dicts, one per batch step, in the shape
    described under "Per-batch loop".
  - For `neh_cp_*_seq` (incumbent-derived) methods: a **mapping** with keys
    `job_sequence_source` (the mode name, or `"job_priority:<name>"` on
    fallback), `job_sequence_fallback` (`bool`), `job_sequence` (the ordered
    job-id list used), and `steps` (the list of per-batch dicts).

## Side effects

| State | Set by |
| --- | --- |
| `ctx.solution_manager` | Final registration only (once, after loop). |
| `<subroutine_dir>/…_step_log.yaml` | Dumped when `ctx.get_file_path_for_subroutine` is callable. |

No intermediate incumbents are registered during the loop; downstream
consumers of the per-step objective trajectory must read `_step_log.yaml`.

## Early-return paths

| Condition | Return |
| --- | --- |
| `n == 0` | `RuntimeError` |
| `skip_pf_below_obj` not `"makespan"` and not float-parseable | `ValueError` |
| `partial_sol is None` post-loop and `error_if_infeasible=True` | `RuntimeError` |
| `partial_sol is None` post-loop and `error_if_infeasible=False` | `SubroutineReport(obj_value=None, obj_bound=None)` |
| Per-batch stage-1 infeasible | Keep going; fall back to `dispatched`. |
| Per-batch stage-2 infeasible/unknown | Keep going; retain stage-1 `partial_sol`. |

## Dependencies

- [`BaseModelBuilder`](../../src/ffc_ddw_sum_et/algorithm/cumulative.py) —
  `build(..., minimize_makespan_lex=, et_ub=)`, `apply_hints_from_schedule`,
  `add_stage_ops_precedence_constraints_after_dispatch_from_schedule`,
  `make_params`.
- [`decode_pf_method`, `PFMethod`](../../src/ffc_ddw_sum_et/algorithm/cumulative.py)
- [`MixedDispatcher`](../../src/ffc_ddw_sum_et/algorithm/dispatcher/mixed.py) —
  `get_best_mixed_schedule_by_sequence(criteria="makespan")`.
- [`FFcSchedule`](../../src/ffc_ddw_sum_et/solution/ffc_schedule.py) —
  `deepcopy`, `dispatch_job_by_stages`, `make_semi_active`, `insert_idle_time`,
  `makespan`.
- [`FFcDDWParameters.create_instance_of_job_subset`](../../src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py)
- [`compute_weighted_earliness_tardiness`](../../src/ffc_ddw_sum_et/solution/objectives.py)
- [`build_schedule_from_op_starts`](../../src/ffc_ddw_sum_et/solution/schedule_build.py)
- [`resolve_cp_tl`](../../src/ffc_ddw_sum_et/orchestration/tl_resolver.py)
- `routix.io.dump_yaml`, `routix.report.SubroutineReport`.

## Related

- `hybridflowshop/controller/neh_cp.py` — source of the two-stage optimize
  pattern. There the primary is makespan and the secondary is sum C_i; here
  the primary is weighted E/T and the secondary is makespan. Both use the
  same device: rebuild the model with a tightened horizon and a hard upper
  bound on the primary objective, then minimize the secondary.
- `run_mcf_lb` — alternative full-schedule constructor that seeds from an
  MCF preemptive lower bound. Disjoint pipelines; they do not share state.
