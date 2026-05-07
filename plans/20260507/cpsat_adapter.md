# Plan: `algorithm/cpsat_adapter.py` + controller pipelining

## Goal

Port the role of `solve_base_cp_model` from
`/home/hjt/code/hybridflowshop/hybridflowshop/controller/hfs_cp_lns.py:240`
into this project as a new `Algorithm` Protocol implementation that
solves the **fresh full FFc-DDW base CP model**, optionally warm-started
by `spec.ref_solution`. Pipeline it through
`src/ffc_ddw_sum_et/orchestration/controller.py` as a new step method.

The first cut keeps the surface intentionally small. A follow-up
(explicitly out of scope here) will add a CP-SAT solver callback that
emits `(time, obj_value, obj_bound)` snapshots into
`AlgRecord.progress_log` (`ProgressLogEntry` already exists).

## Why this shape (recap of decisions)

- **File `algorithm/cpsat_adapter.py`** (not `base_cp.py`) — anticipates
  later non-CP-SAT solver adapters living alongside (e.g.,
  `cplex_adapter.py`, `lns_adapter.py`). The "adapter" word makes the
  layering vs. `algorithm/cumulative.py` (model builder) and
  `algorithm/cumulative_routine.py` (solve routines) explicit.
- **Class `CpsatOption(AlgOption)`** — solver-named, future-proof for
  sibling option classes per solver.
- **Single file**, not a package — only one Algorithm class + one
  Option dataclass + one `run` body. No phases. (KISS / YAGNI; promote
  to `cpsat_adapter/` later if it grows.)
- **Always build a fresh CP model** (no "delete added constraints"
  shortcut from the source). This project has no long-lived
  `cp_model_is_set` cache to maintain — `BaseModelBuilder.build` is
  cheap and stateless. Removes a state-management surface.
- **Always make-semi-active after solve** (no flag). Source had this
  as an option; here it's universally desirable for the post-CP
  schedule.
- **No `is_initial_solution` flag.** Hint application is driven purely
  by `spec.ref_solution is None`. This matches the AlgSpec contract
  already used by `FAMOption` / `NehCpDispatcher`.
- **No CP-SAT tuning options yet** (`encode_cumulative_as_reservoir`,
  `interleave_search`, `use_lns_only`, `cp_model_probing_level`, …).
  `CpsatSolverOptions` already has slots for them — when needed,
  expose a passthrough on `CpsatOption` rather than re-declaring.
  YAGNI today.
- **No `use_final_time_reserve` / `consume_all_remaining_with_final_reserve`**
  — this project has no reserve mechanism, and the controller's
  `self.timer.get_remaining_sec(self.stopping_criteria.timelimit)`
  already provides "remaining global time."
- **Time limit semantics — single field, controller pre-merges.**
  `CpsatOption` carries one scalar: `timelimit_sec: float | None`.
  The controller is responsible for taking the strict-min of any
  user-specified per-call cap and the remaining global time, and
  passing the result. `None` means "no time cap from the caller."
  Inside `run()` the adapter still subtracts pre-solve setup elapsed
  from `timelimit_sec` (clamped at 0) before setting
  `max_time_in_seconds`, so the cap accounts for model-build cost.
  Single source of truth for "where is the time budget computed":
  the controller.
- **`obj_store` / progress log is deferred.** `AlgRecord.progress_log`
  is the eventual home (`ProgressLogEntry` already has the right
  fields); the callback that fills it lands in the next change.

## Public surface

### `algorithm/cpsat_adapter.py`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CpsatOption(AlgOption):
    timelimit_sec: float | None = None
    solver_thread_cnt: int = 1
    log_search_progress: bool = False
    error_if_infeasible: bool = False
    draw_gantt: bool = False


class CpsatAdapter:
    """Algorithm Protocol implementation: solve the FFc-DDW base CP
    model on the full instance via CP-SAT. Honors `spec.ref_solution`
    as a warm-start hint when provided."""

    def run(self, spec: AlgSpec) -> AlgRecord: ...
```

`__all__ = ["CpsatAdapter", "CpsatOption"]`.

### `run()` flow

1. `start = time.monotonic()`. Pull `instance = spec.instance`,
   `option = spec.option` (assert it's a `CpsatOption`),
   `ref = spec.ref_solution`, `logger = spec.logger or logging.getLogger(__name__)`.
2. Build params + model fresh:
   `params_for_horizon = BaseModelBuilder.make_params(instance)` →
   `horizon = sum(params_for_horizon.p.values())` → `builder.build(instance, horizon=horizon)`.
3. If `ref is not None`, apply hints:
   - `apply_start_hints_from_start_time_map(mdl, params, op_vars, ref.get_jik_2_start_time_map())`
   - `apply_end_hints_from_end_time_map(mdl, params, op_vars, ref.get_jik_2_end_time_map())`
   - `apply_et_hints_from_ref_schedule(mdl, params, et_vars, ref)`
4. Resolve effective time limit:
   `eff_tl = max(0.0, option.timelimit_sec - (time.monotonic() - start))`
   when `option.timelimit_sec is not None`, else `None`.
5. Build `CpsatSolverOptions(max_time_in_seconds=eff_tl,
   num_workers=option.solver_thread_cnt,
   log_search_progress=option.log_search_progress)` →
   `solver = get_solver(cfg)`. (`max_time_in_seconds` simply omitted
   when `eff_tl is None`, since `CpsatSolverOptions.get_dict` filters
   out `None` fields.)
6. `status = solver.solve(mdl)`. Status mapping:
   - `OPTIMAL` → `WorkStatus.OPTIMAL`, `TerminationReason.COMPLETED`.
   - `FEASIBLE` → `WorkStatus.FEASIBLE`, `TerminationReason.TIME_LIMIT`
     (we ran until cap; not provably optimal).
   - `INFEASIBLE` → if `error_if_infeasible`, raise; else `WorkStatus.INFEASIBLE`,
     `TerminationReason.COMPLETED`, return record with no schedule.
   - other (`MODEL_INVALID`, `UNKNOWN`) → `WorkStatus.ERROR`,
     `TerminationReason.ERROR`.
7. On feasible/optimal:
   - Decode `j_i_2_start`, `j_i_2_end` → `build_schedule_from_op_starts(...)`.
   - `stage_2_job_2_p = ...` (mirror of `neh_cp/dispatcher.py:210` /
     existing helper) → `schedule.make_semi_active(stage_2_job_2_p)`.
   - `sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, instance)` →
     `obj_value = float(sum_e + sum_t)`.
   - `obj_bound = float(solver.best_objective_bound)` when valid.
   - Sanity-warn if `obj_value` differs from `solver.objective_value`
     after semi-active (mirrors the warning in `controller.run_profile_fixed_ns`).
8. If `option.draw_gantt` and `spec.alg_root is not None`, write a Gantt
   into `spec.alg_root` (use the same helper other algorithms use; if
   none yet, log a TODO and skip — drawing infra is out of scope).
9. Build and return `AlgRecord(work_status=..., result=AlgResult(schedule=...,
   obj_value=..., obj_bound=..., metrics={...}),
   termination_reason=..., option=option, instance_id=instance.instance_id,
   algorithm_id="cpsat_adapter")`.

   > Note: `TimingInfo`/`AlgRecord.timing`은 후속 정리에서 제거됨 — wall-clock은
   > 오케스트레이션 레이어의 `controller.total_elapsed_time`(sec)이 단일 출처.

### Notes

- `progress_log` stays `None` in this change (filled by next change).
- Memory rule: `obj_value` on `AlgRecord` must always be the weighted
  E+T (per `feedback_alg_record_obj_value.md`) — that's what we set.
- Memory rule: don't use defensive `.get()` on per-job maps — the
  `compute_weighted_earliness_tardiness` call assumes complete maps.

## Controller pipelining

### `src/ffc_ddw_sum_et/orchestration/controller.py`

Add a step method `solve_base_model_cpsat` (place near `run_profile_fixed_ns` /
`neh_cp` so all CP-SAT-driven steps cluster). Skeleton modeled on
`neh_cp` (`controller.py:2297` onward):

```python
def solve_base_model_cpsat(
    self,
    cp_tl: float | str | None = None,
    solver_thread_cnt: int = 1,
    log_search_progress: bool = False,
    error_if_infeasible: bool = False,
    draw_gantt: bool = False,
) -> SubroutineReport:
    start_elapsed = time.monotonic()
    instance = self.instance
    n, c, m = instance.job_count, instance.stage_count, instance.last_stage_mc_count

    cp_tl_seconds = resolve_value_expr(cp_tl, n, c, m)
    remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)

    # Strict-min: whichever cap is more restrictive wins.
    # `remaining_sec` is always a finite float here; `cp_tl_seconds` may be None.
    timelimit_sec = (
        min(cp_tl_seconds, remaining_sec)
        if cp_tl_seconds is not None
        else remaining_sec
    )

    incumbent = self.solution_manager.get_incumbent()
    ref_solution = incumbent.schedule if incumbent is not None else None

    self.logger.info(
        "solve_base_model_cpsat: cp_tl=%s, remaining=%.3fs, effective=%.3fs, ref_solution=%s",
        f"{cp_tl_seconds:.3f}s" if cp_tl_seconds is not None else "None",
        remaining_sec,
        timelimit_sec,
        "given" if ref_solution is not None else "None",
    )

    option = CpsatOption(
        timelimit_sec=timelimit_sec,
        solver_thread_cnt=solver_thread_cnt,
        log_search_progress=log_search_progress,
        error_if_infeasible=error_if_infeasible,
        draw_gantt=draw_gantt,
    )
    spec = AlgSpec(
        instance=instance,
        option=option,
        ref_solution=ref_solution,
        logger=self.logger,
        stop_predicate=self.is_stopping_condition,
        alg_root=...,  # match neh_cp's alg_root convention
    )
    record = CpsatAdapter().run(spec)

    elapsed = time.monotonic() - start_elapsed
    obj_value = record.result.obj_value if record.result is not None else None
    obj_bound = record.result.obj_bound if record.result is not None else None
    schedule = record.result.schedule if record.result is not None else None

    report = SubroutineReport(
        elapsed_time=elapsed, obj_value=obj_value, obj_bound=obj_bound,
    )
    self.solution_manager.register(
        report,
        FFcDDWSolution(schedule=schedule, obj_value=obj_value)
        if schedule is not None else None,
    )
    return report
```

Confirm the `alg_root` convention by re-reading the `neh_cp` step
before the edit (it's not on screen in the snippet above).

### Imports to add

```python
from ..algorithm.cpsat_adapter import CpsatAdapter, CpsatOption
```

(`AlgSpec` / `TerminationReason` / `time` / `resolve_value_expr` /
`SubroutineReport` / `FFcDDWSolution` already imported.)

## Out of scope (deferred follow-ups)

- CP-SAT solver callback emitting `ProgressLogEntry` snapshots into
  `AlgRecord.progress_log`.
- Surfacing CP-SAT tuning knobs on `CpsatOption`.
- Wiring `solve_base_model_cpsat` into a scenario YAML in `metadata/`.
- Gantt-rendering helper if not yet present at the algorithm layer.

## Decisions (confirmed)

1. Step method name: `solve_base_model_cpsat`.
2. INFEASIBLE with `error_if_infeasible=False` → register `None`
   schedule (mirrors `run_profile_fixed_ns`, `controller.py:2248`).
3. `alg_root` convention: copy whatever `neh_cp` step passes.
4. `draw_gantt`: option field present, body logs a TODO and skips
   for now. Wired in a follow-up.

## Edits checklist (when approved)

- [ ] New file: `src/ffc_ddw_sum_et/algorithm/cpsat_adapter.py`.
- [ ] New step method `solve_base_model_cpsat` (or chosen name) in
      `src/ffc_ddw_sum_et/orchestration/controller.py`.
- [ ] Import line in `controller.py`.
- [ ] `uv run ruff check` then `uv run ruff format`.
