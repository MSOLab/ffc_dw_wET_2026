# Bundle Step 1 + Step 2 into `run_mcf_lb`

## Context

Today `run_mcf_lb` at [controller.py:73-124](src/ffc_ddw_sum_et/orchestration/controller.py#L73-L124) only performs step 1a (MCF LB + `MixedDispatcher` seed → full-schedule incumbent). Step 1b+1c lives in `run_last_stage_cp_sat_lb` at [controller.py:126-268](src/ffc_ddw_sum_et/orchestration/controller.py#L126-L268), stored separately on `self.last_stage_cp_sat_solution`. Step 2-1 (reverse-dispatch with last-stage fixed), step 2-2 (right-shift if negative), and step 2-3 (profile-fix CP-SAT full solve — `run_profile_fixed_ns`) are chained via `metadata/20260420/1_mcf_lb_init_3_config.yaml` — which today runs only step 1a+1b+1c since 2-1/2-2 do not exist.

The user wants one invocation — `run_mcf_lb` — to cover step 1 **and** step 2 end to end (final full-schedule CP-SAT solution registered as incumbent). Function size growth is acceptable.

## Approach

Extend `run_mcf_lb` to inline the full pipeline:

1. **Step 1a (unchanged body)** — MCF solve → `MixedDispatcher` seed → register full incumbent.
2. **Step 1b+1c** — inline the body currently in `run_last_stage_cp_sat_lb`, producing the last-stage-only CP-SAT solution (`last_stage_cpsat_sched`, `last_stage_cpsat_makespan`).
3. **Step 2-1** — reverse-dispatch with last-stage pinned as seed:
   - Extract last-stage end-time map from the CP-SAT last-stage schedule:

     ```python
     last_stage_end = {
         j: j_i_2_end[j, last_stage_id] for j in self.instance.job_id_list
     }
     job_2_pos = {j: i for i, j in enumerate(self.instance.job_id_list)}
     job_sequence = sorted(
         self.instance.job_id_list,
         key=lambda j: (-last_stage_end[j], job_2_pos[j]),
     )
     ```

     Descending by last-stage CP-SAT end time, ties by native `job_id_list` order ascending (matches the determinism convention in existing `run_mcf_lb`).
   - `reversed_instance = FFcDDWParameters.reverse_stages(self.instance)` — stages in reverse, DDW fields preserved but irrelevant under `criteria="makespan"`.
   - Build an empty `FFcSchedule` laid out over `reversed_instance` stages; for each job `j`, add its last-stage CP-SAT op to the same machine via [`add_ops_times_2_mc`](src/ffc_ddw_sum_et/solution/ffc_schedule.py#L395) with flipped coords:
     - `start_r = last_stage_cpsat_makespan - cpsat_end[j]`
     - `end_r   = last_stage_cpsat_makespan - cpsat_start[j]`
     - stage_id = original last stage (= `reversed_instance.stage_id_list[0]`), mc_id = same machine from the CP-SAT last-stage assignment.
   - `dispatcher = MixedDispatcher(reversed_instance)` → `reversed_full = dispatcher.get_best_mixed_schedule_by_sequence(job_sequence, schedule=seeded, from_stage=reversed_instance.stage_id_list[1], criteria="makespan")`. The dispatcher respects the seed via `get_prev_stage_end_time` inside `get_job_priority_queue_for_stage_dispatch` ([ffc_schedule.py:485-504](src/ffc_ddw_sum_et/solution/ffc_schedule.py#L485-L504)) and iterates from `from_stage` ([utils.py:33-40](src/ffc_ddw_sum_et/algorithm/dispatcher/utils.py#L33-L40)).
4. **Step 2-2** — unflip and shift:
   - `flipped = reversed_full.as_reversed()` ([ffc_schedule.py:174-192](src/ffc_ddw_sum_et/solution/ffc_schedule.py#L174-L192)) — note `as_reversed` flips around the current (reversed-dispatch) makespan `M`, so last-stage ops end up at `cpsat_times + (M - last_stage_cpsat_makespan)`.
   - `flipped.right_shift(last_stage_cpsat_makespan - M)` ([ffc_schedule.py:1408-1417](src/ffc_ddw_sum_et/solution/ffc_schedule.py#L1408-L1417)) — negative shift that re-aligns last stage back to the CP-SAT times; earlier stages may now start before 0.
   - Compute `min_start = min(flipped.get_jik_2_start_time_map().values())`. If `min_start < 0`, `flipped.right_shift(-min_start)` (this shifts everything, so last stage drifts right from the CP-SAT solution — acceptable; step 2-3's CP-SAT only uses this as a warm start).
   - Register `flipped` as the incumbent, overwriting step 1a's seed (it is the better warm start for step 2-3).
5. **Step 2-3** — profile-fix CP-SAT full solve, identical body to [`run_profile_fixed_ns`](src/ffc_ddw_sum_et/orchestration/controller.py#L341-L442) except called inline against the step-2-2 incumbent.

Emit a **single** `SubroutineReport` at the end covering elapsed_time for the whole `run_mcf_lb` invocation, `obj_value` = final CP-SAT objective, `obj_bound` = final CP-SAT best bound (fall back to MCF LB).

### Time budgets

- Step 1c (last-stage-only CP-SAT, "parallel machine scheduling"): `min(0.05 * n * c, remaining_timelimit)` — **updated** from the current `0.01 * n * c`.
- Step 2-3 (profile-fix CP-SAT full solve): `min(0.01 * n * c, remaining_timelimit)`.
- `remaining_timelimit = max(stopping_criteria.timelimit - self.timer.elapsed_sec, 0)` at the point each solve starts.

### Keeping `run_last_stage_cp_sat_lb` public

Leave it as a standalone method — useful for debugging and already covered in `metadata/20260420/1_mcf_lb_init_3_config.yaml`-style configs. The inlining in `run_mcf_lb` duplicates body rather than calling it, to avoid a hidden double-MCF-solve if someone chained both.

## Changes

### 1. [src/ffc_ddw_sum_et/orchestration/controller.py](src/ffc_ddw_sum_et/orchestration/controller.py)

Rewrite `run_mcf_lb` body to the 5-step pipeline above. Signature stays:

```python
def run_mcf_lb(
    self, dispatching_criteria: Literal["weighted_et", "makespan"] = "weighted_et"
) -> SubroutineReport:
```

- `dispatching_criteria` is the step-1a MixedDispatcher criterion (kept for backward compat).
- Step 2-1 MixedDispatcher always uses `criteria="makespan"` (user-specified).
- Step 2-3 uses `profile_fix_by_machine=False`, `machine_precedence_stride=1` (same defaults as `run_profile_fixed_ns`).
- `solver_thread_cnt`: hardcode to `1` for both CP-SAT solves inside the new `run_mcf_lb` (matches current step-1c default); can be promoted to a kwarg later.

No new methods are introduced. The existing helpers (`ParallelMachinePreemptionMcf`, `BaseModelBuilder`, `_build_schedule_from_op_starts`, `MixedDispatcher`, `FFcSchedule.as_reversed` / `right_shift`, `FFcDDWParameters.reverse_stages`) are all reused.

### 2. [metadata/20260420/1_mcf_lb_init_3_config.yaml](metadata/20260420/1_mcf_lb_init_3_config.yaml)

Collapse the flow to a single step:

```yaml
subroutine_flow:
  - method: run_mcf_lb
    dispatching_criteria: makespan
```

Remove the chained `run_last_stage_cp_sat_lb` entry — it is now inside `run_mcf_lb`.

### No changes

- [src/ffc_ddw_sum_et/orchestration/controller_core.py](src/ffc_ddw_sum_et/orchestration/controller_core.py) — `self.last_stage_cp_sat_solution` stays for the standalone `run_last_stage_cp_sat_lb`; the bundled path uses a local variable instead.
- `BaseModelBuilder`, `MixedDispatcher`, `FFcSchedule`, `FFcDDWParameters` — no edits, all reused as-is.

## Critical files to read before editing

- [src/ffc_ddw_sum_et/orchestration/controller.py](src/ffc_ddw_sum_et/orchestration/controller.py) — `run_mcf_lb`, `run_last_stage_cp_sat_lb`, `run_profile_fixed_ns`, `_build_schedule_from_op_starts`.
- [src/ffc_ddw_sum_et/algorithm/dispatcher/utils.py](src/ffc_ddw_sum_et/algorithm/dispatcher/utils.py) — `from_job_sequence_get_schedule_mixed` (seed + `from_stage` semantics).
- [src/ffc_ddw_sum_et/solution/ffc_schedule.py](src/ffc_ddw_sum_et/solution/ffc_schedule.py) — `as_reversed`, `right_shift`, `add_ops_times_2_mc`, `get_job_priority_queue_for_stage_dispatch`.

## Reuse (no edits)

- [`ParallelMachinePreemptionMcf.from_instance / get_job_2_start_time_map / get_obj_value`](src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py)
- [`BaseModelBuilder.build / apply_{start,end}_hints_from_* / add_stage_ops_precedence_constraints_after_dispatch_from_schedule`](src/ffc_ddw_sum_et/algorithm/cumulative.py)
- [`MixedDispatcher.get_best_mixed_schedule_by_sequence`](src/ffc_ddw_sum_et/algorithm/dispatcher/mixed.py) — internally tries multiple `np` heads and returns the best under `criteria="makespan"` — this is the "여러 dispatched schedule 중 제일 좋은 것" the user referenced.
- [`FFcDDWParameters.reverse_stages`](src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py#L82)
- [`FFcSchedule.as_reversed` / `right_shift` / `add_ops_times_2_mc`](src/ffc_ddw_sum_et/solution/ffc_schedule.py)
- [`compute_window_et`](src/ffc_ddw_sum_et/solution/objectives.py)

## Verification

1. `uv run ruff check` and `uv run ruff format`.
2. Pick one small PRA2017 instance from `benchmarks/PRA2017/large` (smallest `n`, `c`). Run:
   ```
   uv run python main.py
   ```
   with `metadata/20260420/1_mcf_lb_init_3_config.yaml` trimmed to that one instance. Confirm via the output CSV:
   - `run_mcf_lb` produces a final incumbent (`obj_value` is set, `obj_bound ≥ mcf_lb`).
   - `obj_value` is **no worse** than running the old two-step flow (`run_mcf_lb` → `run_last_stage_cp_sat_lb`) on the same instance.
   - Gantt output exists for the final schedule (draw_gantt post-run pass).
3. Assertions during development (add as temporary `assert`/log, remove before commit):
   - After step 2-2, `min(flipped.get_jik_2_start_time_map().values()) >= 0`.
   - Last-stage (original) op count in `flipped` equals `instance.job_count`.
   - `schedule.makespan` after step 2-2 ≥ `last_stage_cpsat_makespan`.
4. Regression — run the existing `run_last_stage_cp_sat_lb` standalone on the same instance and confirm its `self.last_stage_cp_sat_solution` is identical to main (the standalone path is untouched).
