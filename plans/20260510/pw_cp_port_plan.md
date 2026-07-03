# Plan: Port `sw_cp` into `ffc_ddw_sum_et`

## Context

`ffc_ddw_sum_et` solves FFcDDW (hybrid flowshop with due windows, weighted E/T from due window). Two upstream PW-CP implementations exist:

- `/home/hjt/code/flowshop-tardiness/flowshop_tardiness/controller/sw_cp.py` — permutation flowshop, indirect-precedence CP. **Not** the structural template (assumes single permutation per stage; incompatible with parallel machines per stage).
- `/home/hjt/code/hybridflowshop/hybridflowshop/controller/sw_cp.py` (`PwCpConstructor`) — hybrid flowshop, 5-region sliding window (ltf | lpf | unfixed | rpf | rtf), cumulative + dummy-bar CP. **Structural template** for this port.

User decisions (final):

- Algorithm name: `sw_cp` (single variant).
- Non-final batch objective: **direct partial weighted E/T minimization over the sub-instance** ((a)안). No `common_spacing`, no lex 2-phase (deferred).
- Sub-instance composition: **hybridflowshop fidelity** — left/right dummy bars + release-time trick. Time-fixed ops do NOT get CP variables; their footprint is represented by dummy bars and (for cross-stage precedence) by stage-0 release on jobs with an LTF prefix.
- `spec.ref_solution` is **required**; raise an error if absent.
- Mirror `algorithm/neh_cp/dispatcher.py` mechanically: AlgSpec validation, option resolve, per-step TL via `resolve_per_step_tl`, wall-clock deadline plumbing, `ObjectiveValueRecorder`-based progress log, `keep_step_schedules` parity, `AlgRecord` assembly.
- **Right-justification is enabled, using an objective-preserving extension.** The repo already has `FFcSchedule.delay_job_latest_leq_obj_contrib(job_2_dw_ub_map)` (`solution/ffc_schedule.py:1451`) which delays last-stage ops as late as possible while keeping per-job objective contribution non-increasing (early jobs may shift into the due window; on-time jobs may slide later within the window; tardy jobs are pinned). Add a sibling method `delay_job_latest_leq_obj_contrib_all_stages` that (i) runs the existing last-stage delay first to fix every C_j, then (ii) for stages `c-1, c-2, …, 1` in order, scans each machine's sequence latest→earliest and pushes each op to `min(op_start[j, i+1], next_op_new_start_on_same_machine)`. Result: every operation is at its latest objective-preserving position; rtf jobs' last-stage completion times are unchanged; the unfixed window has maximal slack on its left. PW-CP calls this on `incumbent.deepcopy()` once per batch to produce `rj_schedule`, the source of truth for `right_boundary[i,k]`, the "i time-fixed → i+1 non-time-fixed" precedence constants, and the warm-start hints. The original incumbent stays untouched (used only for accept/reject baseline).
- **In scope (added)**: subroutine integration on `FFcDDWSubroutineController`, dedicated experiment config wired into `main.py`, two-phase Gantt emission (before/after the subroutine) and hfs_summary-style per-step CSV. Use the project's `add-subroutine` skill to apply the full convention stack during implementation; the controller-side method follows the per-step contract in CLAUDE.md (at-most-one `_register` per call; `elapsed_time = monotonic() - start; report; _register` with no work in between).

## Goal

Add a sliding-window CP refiner that improves an FFcDDW incumbent by re-solving an unfixed window plus a profile-fixed buffer at each step, directly minimizing weighted E/T over the sub-window. Algorithm id: `"sw_cp"`. Self-contained under `src/ffc_ddw_sum_et/algorithm/sw_cp/`.

## File layout

**Solution-layer extension** (single new method, used by PW-CP for objective-preserving RJ):

- `src/ffc_ddw_sum_et/solution/ffc_schedule.py` — add `FFcSchedule.delay_job_latest_leq_obj_contrib_all_stages(self, job_2_dw_ub_map)`. Body: call existing `self.delay_job_latest_leq_obj_contrib(job_2_dw_ub_map)` first (fixes every last-stage end / every C_j); then for `i` in `reversed(self.stages[:-1])`, for each `mc_id` in `self.machines_per_stage[i]`, scan that machine's `__stage_2_mc_2_job_tuple_seq[i][mc_id]` from latest to earliest and rewrite each op's end as `min(op_start_of_same_job_on_stage_i+1, next_op_new_start_on_same_machine_or_+inf)`, with `new_start = new_end - duration`. After each stage's pass, call `self._rebuild_stage_time_caches(i)` to refresh the cached lookup maps. No mutation of `__stage_2_mc_2_job_tuple_seq` ordering — only timestamps shift; the per-machine sequence order is preserved.

**Algorithm core** under `src/ffc_ddw_sum_et/algorithm/sw_cp/`:

- `__init__.py` — re-export `PwCpDispatcher`, `PwCpOption`.
- `option.py` — `PwCpOption(AlgOption)` dataclass.
- `partition.py` — `OperationPartition`, `_build_operation_partition`, `build_stage_2_batch_list`, `validate_and_get_batch_count`. Ported from hybridflowshop, trimmed (drop `slack_occupying`; keep `promote_job_contained_ops`, `non_time_fixed`, `non_profile_fixed`).
- `cp_model.py` — `PwCpModelBuilder` (partition-aware CP-SAT builder; see "CP model" below).
- `dispatcher.py` — `PwCpDispatcher.run(spec)` (mirrors `NehCpDispatcher.run` mechanically).
- `step_log.py` — `PwCpStepEntry` (frozen dataclass).

**Subroutine integration** (mirrors existing `mcf_lb`/`neh_cp` step methods on the controller):

- `src/ffc_ddw_sum_et/orchestration/controller.py` — add `run_sw_cp(self, ...)` method. Body: build `AlgSpec(instance, option=PwCpOption(**kwargs), ref_solution=current_incumbent, alg_root=self.subroutine_dir, logger=self.logger, stop_predicate=self.stop_predicate)`, call `PwCpDispatcher().run(spec)`, register the resulting incumbent via `self._register(report, sol)`. Two-phase Gantt: emit before/after Gantt PNGs around the call, gated on the controller's `draw_gantt` flag. Per CLAUDE.md subroutine step contract: a single `_register` per call; `elapsed_time = time.monotonic() - start_elapsed` measured immediately before report construction, no work between measurement and `_register`.
- `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py` — no change required if the existing `_save_obj_log` aggregator already re-bases trajectories per step (verify during implementation; controller contract already covers this).
- `configs/sw_cp/<scenario>.yaml` (or wherever existing scenario YAMLs live — locate by reading existing `mcf_lb` / `neh_cp` configs, e.g. `configs/neh_cp/*.yaml`) — a new dedicated experiment scenario invoking `run_sw_cp` with default option values, plus a small grid (`unfixed_batch_count ∈ {2,3}`, `pf_method ∈ {"PF0","PF1"}`).
- `main.py` — wire the new scenario name into the dispatch table (mirror the existing `neh_cp` / `mcf_lb` registration). The `add-subroutine` skill is the canonical executor of this wiring; invoke it once the algorithm core is in place.

**Tests** under `tests/algorithm/sw_cp/` (shape mirrored from `tests/algorithm/neh_cp/`).

**Two-phase Gantt and per-step CSV** follow the conventions used by existing controller methods — reuse the controller-level Gantt helper (e.g. `self._draw_gantt(...)`) and per-step CSV writer (e.g. `hfs_summary`-style helper) rather than re-implementing inside `sw_cp/`.

Reuse without copy from `algorithm/`:

- `step_tl_resolver.resolve_per_step_tl` + `BatchTlMode` (per-batch TL resolver) — already promoted out of `neh_cp/` into `algorithm/step_tl_resolver.py`. Import directly.
- `utils.trunc4` — already promoted into `algorithm/utils.py`. Import directly.
- `cpsat_callbacks.obj_value_recorder.ObjectiveValueRecorder` — progress recorder.

## `PwCpOption` fields

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class PwCpOption(AlgOption):
    solver_thread_cnt: int = 1
    batch_size: int = 1
    step_size: int = 1
    unfixed_batch_count: int = 1
    left_profile_fixed_batch_count: int = 0
    right_profile_fixed_batch_count: int = 0
    enable_promotion_profile_fixed: bool = False
    pf_method: PFMethod = "PF1"

    cp_tl_seconds: float | None = None
    total_timelimit_seconds: float | None = None
    batch_tl_mode: BatchTlMode = "constant"
    batch_tl_offset_seconds: float = 0.01
    apply_cumulative_tl: bool = False
    wall_clock_deadline_sec: float | None = None

    error_if_infeasible: bool = False
    keep_step_schedules: bool = False
```

Skipped intentionally (deferred): `tighten_ranges`, `use_lns_only`, `non_time_fixed_op_time_limit_multiplier`, `minimize_makespan_lex`, `cp_tl_2nd_obj_seconds`, lex/common_spacing toggles.

## CP model (`PwCpModelBuilder`)

Cannot reuse `BaseModelBuilder.build` directly because it creates op vars for every `(j, i)`. PW-CP needs partition-aware variable creation. New builder, with shapes deliberately mirroring hybridflowshop's `cpsat_model_2/sw_cp.py:PwCpModelBuilder`:

**Reference schedule**: every CP-model construction step below reads start/end constants from `rj_schedule = incumbent.deepcopy(); rj_schedule.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)` — NOT from the raw incumbent. `rj_schedule` has identical per-job objective contribution (E_j, T_j) but every op is at its latest objective-preserving position, giving non-time-fixed ops maximal left-side slack inside the window.

1. **Sub-instance**: `sub_jobs = {j : non_time_fixed_op_count(j) > 0}` — i.e., jobs with at least one op in `lpf ∪ unfixed ∪ rpf`. Build `sub_instance = FFcDDWParameters.create_instance_of_job_subset(instance, sub_jobs)`.

2. **Op vars (non-time-fixed only)**: for each `(j, i)` in `sub_jobs × stages` where `(j, machine_in_rj_for_(j,i))` is in `stage_2_partition[i].non_time_fixed`, create `op_start[j,i]`, `op_end[j,i]`, `op_intvl[j,i]`. **Not** for `(j, i)` whose op falls in `time_fixed` — those ops contribute via dummy bars and the precedence constants below.

3. **Dummy bar vars** (per `(stage, machine)`, both sourced from `rj_schedule`):
   - **Left dummy bar**: fixed interval `[0, left_boundary[i,k]]` where `left_boundary[i,k] = max end-time of LTF ops on (i,k)` in `rj_schedule` (0 if none).
   - **Right dummy bar**: fixed interval `[right_boundary[i,k], horizon]` where `right_boundary[i,k] = min start-time of RTF ops on (i,k)` in `rj_schedule` (horizon if none). **No `common_spacing` variable** — both endpoints are constants.

4. **Job-stage precedence** (`add_non_fixed_job_precedence_constraints`):
   - For each `j ∈ sub_jobs` and consecutive stage pair `(i, i+1)`:
     - both non-time-fixed → `op_end[j,i] <= op_start[j,i+1]`
     - i time-fixed, i+1 non-time-fixed → `op_start[j,i+1] >= rj_schedule_end[j,i]` (constant lower bound from `rj_schedule`)
     - i non-time-fixed, i+1 time-fixed → `op_end[j,i] <= rj_schedule_start[j,i+1]` (constant upper bound from `rj_schedule`)
   - Equivalent to hybridflowshop's `add_non_fixed_job_precedence_constraints`. Constants come from `rj_schedule` rather than the raw incumbent — this is the entire benefit of the RJ pre-pass.

5. **Stage-0 release-time trick**: where applicable, `_make_vars`-style release on the first non-time-fixed stage of each `j` is implicit via the bullet above (i time-fixed, i+1 non-time-fixed). Do NOT use `BaseModelBuilder._make_vars`'s `job_2_release` parameter — that hook only fires on `i_list[0]` and the partition can leave a job's stage-0 op time-fixed while later stages are not. The per-stage "constant lower bound" handles all cases uniformly.

6. **Capacity (cumulative with dummy bars)**: for each stage `i`, `mdl.add_cumulative([op_intvl[j,i] for non_time_fixed (j, k_in_partition)] + [left_dummy[i,k] for k in M_i] + [right_dummy[i,k] for k in M_i], demands=[1]*N, capacity=|M_i|)`. Equivalent to `add_capacity_with_dummy_bar_constraints` in hybridflowshop.

7. **Profile-fix precedence**: build `profile_fixed_schedule = rj_schedule.deepcopy()` then `remove_operations(non_profile_fixed_ops)`. Call existing `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule` (`cumulative.py:457`) with `decode_pf_method(option.pf_method)`. **Reuse this — do not re-implement.**

8. **Hints**: from `rj_schedule` restricted to non-time-fixed ops. Reuse `BaseModelBuilder.apply_start_hints_from_start_time_map` and `apply_end_hints_from_end_time_map` (`cumulative.py:592, 611`) over the non-time-fixed `(j, i, k)` subset. E/T hints via `apply_et_hints_from_ref_schedule` on E/T vars built in step 9 — `rj_schedule` and the raw incumbent have identical C_j, so either works; using `rj_schedule` keeps the hint set internally consistent.

9. **Objective (partial weighted E/T)**: for each `j ∈ sub_jobs` whose **last-stage** op is non-time-fixed (i.e., `(j, k_last) ∈ partition[last_i].non_time_fixed`):
   - Create `E_j = max(0, d^-_j - op_end[j, last_i])` and `T_j = max(0, op_end[j, last_i] - d^+_j)` via `add_max_equality`.
   - Add `w_e[j] * E_j + w_t[j] * T_j` to the objective.

   For `j ∈ sub_jobs` whose last-stage op is **time-fixed** in this partition: skip — its `C_j` is a constant from the incumbent and contributes a constant `et_offset_partial` (logged but not added to the CP objective; constants don't affect `argmin`).

   `mdl.minimize(sum(et_terms))`. No `obj_lb`, no `et_ub`, no makespan secondary.

## Dispatcher flow (`PwCpDispatcher.run`)

```text
run(spec):
  instance = _validate_instance(spec)              # FFcDDWParameters
  if spec.ref_solution is None:
      raise ValueError("sw_cp requires spec.ref_solution")
  option   = _resolve_option(spec)                  # PwCpOption()
  logger   = spec.logger or logging.getLogger(__name__)

  incumbent = spec.ref_solution.deepcopy()
  incumbent.make_semi_active(instance.stage_2_job_2_p_map)
  incumbent.insert_idle_time(instance.job_2_due_window_map,
                             instance.job_2_ewt_map, instance.job_2_twt_map)

  params_for_horizon = BaseModelBuilder.make_params(instance)
  horizon = sum(params_for_horizon.p.values())

  initial_batches = build_stage_2_batch_list(incumbent, option.batch_size)
  max_batch_cnt   = validate_and_get_batch_count(initial_batches)
  iteration_idxs  = list(range(0,
                                max_batch_cnt - option.unfixed_batch_count + 1,
                                option.step_size))

  per_step_tl = resolve_per_step_tl(
      cp_tl_from_arg=option.cp_tl_seconds,
      total_seconds=option.total_timelimit_seconds,
      num_batches=None,
      batch_count=len(iteration_idxs),
      batch_tl_mode=option.batch_tl_mode,
      batch_tl_offset_seconds=option.batch_tl_offset_seconds,
      logger=logger)

  start_elapsed   = time.monotonic()
  step_entries: list[PwCpStepEntry] = []
  progress_entries: list[ProgressLogEntry] = []
  step_schedules: list[tuple[int, FFcSchedule, FFcSchedule | None]] = []
  stopped_early = False

  for step, unfixed_start in enumerate(iteration_idxs):
      # 1) re-batch from current incumbent (positions shift as incumbent improves)
      stage_2_batch  = build_stage_2_batch_list(incumbent, option.batch_size)
      assert validate_and_get_batch_count(stage_2_batch) == max_batch_cnt

      stage_2_partition = {
          i: _build_operation_partition(
              stage_2_batch[i], i,
              unfixed_batch_start_idx=unfixed_start,
              unfixed_batch_count=option.unfixed_batch_count,
              left_profile_fixed_batch_count=option.left_profile_fixed_batch_count,
              right_profile_fixed_batch_count=option.right_profile_fixed_batch_count)
          for i in instance.stage_id_list }

      if option.enable_promotion_profile_fixed:
          unfixed_jobs = { j for p in stage_2_partition.values()
                             for (j, _) in p.unfixed }
          stage_2_partition = { i: p.promote_job_contained_ops(unfixed_jobs)
                                for i, p in stage_2_partition.items() }

      # 2) Objective-preserving right-justification on a copy
      rj_schedule = incumbent.deepcopy()
      rj_schedule.delay_job_latest_leq_obj_contrib_all_stages(instance.job_2_dw_ub_map)

      # 3) build sub-problem
      sub_jobs = { j for p in stage_2_partition.values()
                       for (j, _) in p.non_time_fixed }
      if not sub_jobs:
          continue                                  # nothing to optimize this batch
      sub_instance = FFcDDWParameters.create_instance_of_job_subset(instance, sub_jobs)

      builder = PwCpModelBuilder()
      mdl, sub_params, op_vars, et_vars, last_i_partial_jobs = builder.build(
          sub_instance, instance, rj_schedule, stage_2_partition,
          horizon=horizon, pf_method=option.pf_method)

      # 4) solve with TL + wall-clock deadline (mirror neh_cp lines 223-273)
      solver = cp_model.CpSolver()
      applied_tl_seconds = _apply_tl_and_deadline(
          solver, option, per_step_tl[step], start_elapsed, step,
          len(iteration_idxs), logger)
      if applied_tl_seconds is False:                # deadline already exceeded
          stopped_early = True
          break
      solver.parameters.num_workers = option.solver_thread_cnt
      value_recorder = ObjectiveValueRecorder()
      status = solver.solve(mdl, solution_callback=value_recorder)
      _append_progress_entries(progress_entries, value_recorder, start_elapsed)

      # 5) reconstruct candidate over FULL instance and accept/reject
      cand = None
      cand_obj = None
      if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
          # Time-fixed ops carry over from rj_schedule (same E/T as incumbent
          # but at latest objective-preserving positions); non-time-fixed ops
          # come from the CP solution.
          cand = _build_full_schedule_from_cp(
              instance, rj_schedule, stage_2_partition,
              op_vars, sub_params, solver)
          cand.make_semi_active(instance.stage_2_job_2_p_map)
          cand.insert_idle_time(instance.job_2_due_window_map,
                                instance.job_2_ewt_map, instance.job_2_twt_map)
          se, st = compute_weighted_earliness_tardiness(cand, instance)
          cand_obj = float(se + st)

      incumbent_obj_before = _full_obj(incumbent, instance)
      accepted = cand is not None and cand_obj < incumbent_obj_before
      if accepted:
          incumbent = cand
      else:
          incumbent.make_semi_active(instance.stage_2_job_2_p_map)
          incumbent.insert_idle_time(...)

      step_entries.append(PwCpStepEntry(
          step=step,
          unfixed_batch_start_idx=unfixed_start,
          non_time_fixed_op_count=sum(len(p.non_time_fixed) for p in stage_2_partition.values()),
          incumbent_obj_before=incumbent_obj_before,
          cp_obj=cand_obj,
          incumbent_obj_after=_full_obj(incumbent, instance),
          accepted=accepted,
          status=solver.StatusName(status),
          applied_tl_seconds=applied_tl_seconds,
          wall_seconds=solver.wall_time))

      if option.keep_step_schedules:
          step_schedules.append((step, incumbent.deepcopy(),
                                 cand.deepcopy() if cand is not None else None))

      if spec.stop_predicate is not None and spec.stop_predicate():
          stopped_early = True
          break

  termination_reason = (TerminationReason.STOP_REQUESTED if stopped_early
                        else TerminationReason.COMPLETED)
  obj_value = _full_obj(incumbent, instance)
  metrics = {"step_log": tuple(step_entries)}
  if option.keep_step_schedules:
      metrics["step_schedules"] = step_schedules
  return AlgRecord(
      work_status=WorkStatus.FEASIBLE,
      instance_id=instance.name,
      algorithm_id="sw_cp",
      option=option,
      result=AlgResult(schedule=incumbent, obj_value=obj_value,
                       obj_bound=None, metrics=metrics),
      progress_log=tuple(progress_entries),
      termination_reason=termination_reason,
  )
```

`_build_full_schedule_from_cp` reconstructs a full `FFcSchedule` by:

1. Starting from `rj_schedule.deepcopy()` and removing all non-time-fixed `(j, i, k)` ops via `FFcSchedule.remove_operations`.
2. For each non-time-fixed `(j, i)`, dispatching the op back with `op_start = solver.Value(op_vars.op_start[j,i])` and `op_end = solver.Value(op_vars.op_end[j,i])` via `FFcSchedule.add_ops_times_2_mc(stage_id=i, mc_id=k, job_id=j, start, end)` where `k` is the assigned machine — chosen by **earliest free machine on stage `i` at time `start`** within the machines listed in `instance.stage_2_machines_map[i]` (matches hybridflowshop's `create_sw_cp_schedule` machine assignment policy).

## Reused helpers (with paths)

- `FFcSchedule.delay_job_latest_leq_obj_contrib` — `solution/ffc_schedule.py:1451` (existing; `_all_stages` extension delegates to it as the first pass)
- `FFcSchedule._rebuild_stage_time_caches` — `solution/ffc_schedule.py` (used by the existing single-stage helper at line 1497; the all-stages extension calls it once per stage after rewriting that stage's tuple sequences)
- `BaseModelBuilder.make_params` — `algorithm/cumulative.py:185`
- `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule` — `cumulative.py:457`
- `BaseModelBuilder.add_start_time_freezed_operation_constraints` — `cumulative.py:582` (only used inside `PwCpModelBuilder` if pinning is needed for any edge case; primary mechanism is non-time-fixed-only var creation + dummy bars)
- `BaseModelBuilder.apply_start_hints_from_start_time_map`, `apply_end_hints_from_end_time_map`, `apply_et_hints_from_ref_schedule` — `cumulative.py:592, 611, 630`
- `decode_pf_method`, `PFMethod` — `cumulative.py:18, 31`
- `FFcDDWParameters.create_instance_of_job_subset` — `parameters/ffc_ddw_params.py` (used by NEH-CP at `dispatcher.py:143`)
- `FFcSchedule.{deepcopy, make_semi_active, insert_idle_time, remove_operations, iter_operations_on_stage, get_jik_2_start_time_map, get_jik_2_end_time_map, get_job_end_time, add_ops_times_2_mc, makespan}` — `solution/ffc_schedule.py`
- `compute_weighted_earliness_tardiness` — `solution/objectives.py:12`
- `resolve_per_step_tl`, `BatchTlMode` — `algorithm/step_tl_resolver.py`
- `trunc4` — `algorithm/utils.py`
- `ObjectiveValueRecorder` — `algorithm/cpsat_callbacks/obj_value_recorder.py`
- `AlgSpec`, `AlgRecord`, `AlgResult`, `ProgressLogEntry`, `WorkStatus`, `TerminationReason`, `AlgOption` — `algorithm/base/`
- Reference for TL/deadline plumbing, semi-active gating, step entry, accept/reject, AlgRecord assembly: `algorithm/neh_cp/dispatcher.py:120-280, 339-371, 480-582`.
- Reference structural template for partition / dummy bars / capacity / non-fixed-precedence: `/home/hjt/code/hybridflowshop/hybridflowshop/cpsat_model_2/sw_cp.py` (PwCpModelBuilder) and `/home/hjt/code/hybridflowshop/hybridflowshop/controller/sw_cp.py` (sliding loop).

## Verification

1. **Unit tests** under `tests/algorithm/sw_cp/` (mirror `tests/algorithm/neh_cp/`):
   - `test_option.py` — defaults, frozen, validation of `step_size>=1`, `unfixed_batch_count>=1`.
   - `test_partition.py` — 5-region splits at edge offsets (start, middle, end of timeline); `enable_promotion_profile_fixed` promotes correctly; `validate_and_get_batch_count` raises on uneven stage batch counts.
   - `test_cp_model.py` — on a 3-job/2-stage/1-machine instance, verify `PwCpModelBuilder.build` creates op vars only for non-time-fixed ops; left/right dummy intervals match `[0, left_boundary]` / `[right_boundary, horizon]` **with boundaries sourced from `rj_schedule`**; profile-fix precedence forces the expected order.
   - `test_dispatcher.py` — load a small PRA instance, build a NEH-CP incumbent (via existing `NehCpDispatcher` in test fixture), pass as `spec.ref_solution` to `PwCpDispatcher`, run with `cp_tl_seconds=1.0` and `unfixed_batch_count=2`, assert (a) `result.obj_value <= ref_obj`, (b) every job appears in `result.schedule`, (c) `result.work_status == FEASIBLE`, (d) `progress_log` is monotonically improving (when present).
   - `test_ffc_schedule_delay_all_stages.py` (under `tests/solution/`) — assert `delay_job_latest_leq_obj_contrib_all_stages` (i) preserves every C_j vs the input, (ii) preserves the per-machine sequence order at every stage, (iii) preserves every duration `p[j,i]`, (iv) `compute_weighted_earliness_tardiness` returns the same value before and after (allowing only non-increasing change), (v) `validate_schedule` passes.
   - `test_dispatcher_stop.py` — assert `wall_clock_deadline_sec` and `spec.stop_predicate` short-circuit cleanly with `termination_reason == STOP_REQUESTED`.
   - `test_dispatcher_no_ref_solution.py` — assert `ValueError` when `spec.ref_solution is None`.

2. **Algorithm contract test** — register `sw_cp` in `tests/algorithm/test_algorithm_contracts.py` so the shared shape-checks (option type, AlgRecord shape, instance-id passthrough) run.

3. **Smoke test (algorithm core)** via `uv run python` ad-hoc:
   ```bash
   uv run python -c "from ffc_ddw_sum_et.algorithm.neh_cp import NehCpDispatcher, NehCpOption; \
     from ffc_ddw_sum_et.algorithm.sw_cp import PwCpDispatcher, PwCpOption; \
     ...; rec1 = NehCpDispatcher().run(spec1); \
     spec2 = AlgSpec(instance=ins, option=PwCpOption(unfixed_batch_count=3, cp_tl_seconds=2.0), ref_solution=rec1.result.schedule); \
     rec2 = PwCpDispatcher().run(spec2); print(rec1.result.obj_value, '->', rec2.result.obj_value)"
   ```
   Expect `rec2.obj_value <= rec1.obj_value`.

4. **Subroutine integration smoke test**: `uv run python main.py` with the new `sw_cp` scenario YAML on one small instance. Verify (a) `_obj_log.json` is produced with monotonic non-increasing E/T, (b) before/after Gantt PNGs are emitted to the subroutine output dir, (c) per-step CSV rows are appended, (d) the run terminates within the configured stopping criterion.

5. **Lint**: `uv run ruff check src/ffc_ddw_sum_et/algorithm/sw_cp tests/algorithm/sw_cp src/ffc_ddw_sum_et/orchestration/controller.py` and `uv run ruff format` on the same paths.

## Risks

1. **`delay_job_latest_leq_obj_contrib_all_stages` correctness.** The new method must preserve every C_j (last-stage end), preserve every per-machine sequence order, and yield a feasible schedule (no overlap, all job-stage precedence satisfied). The latest-end formula on stage `i < c` is `min(op_start_of_same_job_on_stage_(i+1), next_op_start_on_same_machine_or_+inf)`; verify by unit test that on every (j, i): (a) `new_end[j,i] <= op_start[j,i+1]`, (b) `new_start[j,i] >= prev_op_end[j,i]_on_same_machine` (i.e., earlier op on same machine that we have not yet rewritten, since we scan latest→earliest), (c) `new_end[j,i] - new_start[j,i] == p[j,i]` (duration preserved). Build `validate_schedule` (`solution/ffc_schedule.py:1575`) into the test as a final check.

2. **Dummy-bar interaction with `make_semi_active` post-CP.** Cumulative + dummy bars guarantee the CP solution respects LTF/RTF capacity, but `make_semi_active` mutates non-LTF/RTF op start times. If the merge step (`_build_full_schedule_from_cp`) leaves a job's first non-time-fixed stage starting before the LTF prefix end, `make_semi_active` may attempt to left-shift it past the LTF boundary on the same machine. Mitigation: in `_build_full_schedule_from_cp`, restrict `make_semi_active`'s `start_from_stage` to the first non-time-fixed stage of each job, or pass an `operation_set` excluding LTF/RTF ops.

3. **`make_semi_active` machine reassignment ambiguity.** When reconstructing the candidate, the CP-SAT model uses cumulative (capacity-only) and does not bind a specific machine. The earliest-free-machine policy in `_build_full_schedule_from_cp` is one of several valid assignments; if the dispatch policy diverges from what cumulative implicitly counted, the resulting schedule may have ops slightly later than the CP `op_start` values. Acceptable as long as objective is recomputed post-merge from the actual schedule (we do that).

4. **Partition mismatch when batch counts differ across stages.** `validate_and_get_batch_count` raises in such cases. For instances where stages have unequal job counts (some jobs may skip stages?), batching breaks. FFcDDW assumes every job visits every stage (`docs/problem-description.md` constraint 1), so this should not occur — but a defensive `len(jobs) == len(instance.job_id_list)` check on each stage's op count is worth adding.

5. **`MixedDispatcher` not consulted.** Unlike NEH-CP, PW-CP does not run a per-step dispatch fallback when CP is infeasible — it just keeps the incumbent and re-applies `make_semi_active`. This matches hybridflowshop's `_accept_candidate_or_repair_incumbent` and is intentional, but it means a buggy `PwCpModelBuilder` that produces an infeasible model will silently no-op the entire run. Mitigation: log infeasibility loudly at WARNING level on the first occurrence per run, and surface a count in `step_log`.

6. **`PFMethod="PF1"` default may be too tight on small windows.** If `unfixed_batch_count=1`, profile-fix precedence forces the entire chain order from the incumbent and the CP can only swap timings, not order. Documented behavior; users seeking exploration should set `unfixed_batch_count >= 2` or `pf_method="PF0"`. Add a brief docstring note on `PwCpOption.pf_method`.

7. **Subroutine step contract drift.** The CLAUDE.md per-step contract is load-bearing for `_obj_log.json` re-basing. The `run_sw_cp` method must do all work, then measure `elapsed = monotonic() - start`, then construct the report, then `_register` — with **no** intervening work (Gantt PNG emission, CSV row append, etc. happen *after* `_register`). The two-phase Gantt's "before" snapshot must be taken *before* `start = monotonic()` is captured to avoid skewing the trajectory. Verify by reading `_save_obj_log` in `ffcddw_single_instance_runner.py` during implementation.

## Open follow-ups (explicitly deferred)

- Lex 2-phase (E/T then makespan) — straightforward to add by extending `PwCpModelBuilder` to call `BaseModelBuilder._define_objective(..., minimize_makespan_lex=True, et_ub=...)` on a second pass per batch. May resurrect right-justification at that point.
- `tighten_ranges`, `use_lns_only`, `non_time_fixed_op_time_limit_multiplier` — straightforward additions on `PwCpOption` once a need surfaces.
- `common_spacing` proxy objective as an alternative to (a)안 — only worth revisiting if (a)안 underperforms in practice.
