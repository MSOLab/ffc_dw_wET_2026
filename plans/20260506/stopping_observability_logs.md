# Plan: stopping/optimality observability INFO logs

## Goal

Make the inferred (but currently silent) behaviors of the last 4 commits
directly observable in `*_SubroutineController.log` so future verification
runs do not need to triangulate from `instance_result.yaml` + code reading.

Pure-additive INFO logs only — no behavioral change. User intends to demote
to DEBUG or delete later as needed.

## What is currently silent vs already logged

| Behavior | Currently logged? |
|---|---|
| `apply_lb_by_mcf` catches `MCFLBStopRequested` | ✅ "apply_lb_by_mcf: stop predicate fired before MCF solve; skipping." |
| `run_mcf_lb_then_neh_cp` catches `MCFLBStopRequested` | ✅ "run_mcf_lb_then_neh_cp: stop predicate fired before MCF solve; skipping." |
| `NehCpDispatcher` batch-boundary stop | ✅ "neh_cp: stop_predicate fired after batch K/N; stopping." |
| `NehCpDispatcher` deadline before batch | ✅ "neh_cp: wall_clock_deadline_sec exceeded before batch K/N primary CP-SAT solve; stopping." |
| `NehCpDispatcher` deadline after batch | ✅ "neh_cp: wall_clock_deadline_sec exceeded after batch K/N; stopping." |
| `controller.neh_cp` early-stop registration | ✅ "neh_cp: dispatcher stopped early after batch X; registering recovered schedule." |
| `solve_mcf_lb` raising `MCFLBStopRequested` | ❌ no log inside `solve_mcf_lb` itself |
| `_make_stop_report` — every other stop short-circuit (timelimit OR optimality, all in-flow guards in `calc_mcf_lb_and_derive_full_sch`) | ❌ silent return |
| `_optimality_proven` returning True | ❌ silent — pipeline just halts at next `is_stopping_condition()` |
| `controller.neh_cp` / `run_mcf_lb_then_neh_cp` setting `objective_lower_bound` | ❌ no log of the value being threaded |
| `controller.neh_cp` / `run_mcf_lb_then_neh_cp` setting `wall_clock_deadline_sec` | ❌ no log of the deadline being threaded |
| `NehCpDispatcher` last-batch `obj_lb` injection into CP-SAT | ❌ no log |
| `NehCpDispatcher` per-batch primary CP-SAT status/obj/bound | ❌ logged only on infeasible |
| `NehCpDispatcher` recovery dispatch (#remaining jobs) | ❌ silent |
| `NehCpDispatcher` natural completion | ❌ silent |

## Edits

### 1. `src/ffc_ddw_sum_et/orchestration/controller_core.py`

**1a — `_make_stop_report` (line 132)**: log every call with the reason.

```python
def _make_stop_report(self, start_elapsed: float | None = None) -> SubroutineReport:
    diag = self.mcf_lb_diagnostic
    bound = (
        float(diag.mcf_lb)
        if diag is not None
        and diag.mcf_lb is not None
        and diag.mcf_lb_is_valid_for_main_problem
        else None
    )
    elapsed = time.monotonic() - start_elapsed if start_elapsed is not None else 0.0
    timelimit = self.stopping_criteria.timelimit
    timer_elapsed = self.timer.elapsed_sec
    ub = self.solution_manager.best_obj_value
    lb = self.get_current_valid_lb()
    if self.timer.time_over(timelimit):
        reason = "timelimit"
    elif self._optimality_proven_no_log():
        reason = "optimality_proven"
    else:
        reason = "unknown"
    self.logger.info(
        "_make_stop_report: reason=%s, subroutine_elapsed=%.3fs, "
        "timer_elapsed=%.3fs/%.3fs, valid_lb=%.2f, best_ub=%s, bound=%s",
        reason,
        elapsed,
        timer_elapsed,
        timelimit,
        lb,
        ub if ub is not None else "None",
        bound if bound is not None else "None",
    )
    return SubroutineReport(elapsed_time=elapsed, obj_value=None, obj_bound=bound)
```

**1b — `_optimality_proven` (line 113)**: log on True transition (use `_optimality_logged` flag to avoid spam since `is_stopping_condition` is called many times). Provide `_optimality_proven_no_log` alias for use inside `_make_stop_report` so we don't double-log.

```python
def _optimality_proven_no_log(self) -> bool:
    ub = self.solution_manager.best_obj_value
    if ub is None:
        return False
    lb_int = math.ceil(self.get_current_valid_lb())
    ub_int = int(ub)
    if lb_int > ub_int:
        raise ValueError(
            f"{self._instance_name}: MCF global LB ({lb_int}) exceeds "
            f"incumbent UB ({ub_int}); LB or UB is inconsistent."
        )
    return lb_int == ub_int

def _optimality_proven(self) -> bool:
    proven = self._optimality_proven_no_log()
    if proven and not getattr(self, "_optimality_logged", False):
        self.logger.info(
            "_optimality_proven: ceil(LB)=%d == int(UB)=%d (LB=%.2f, UB=%.2f)",
            math.ceil(self.get_current_valid_lb()),
            int(self.solution_manager.best_obj_value),
            self.get_current_valid_lb(),
            self.solution_manager.best_obj_value,
        )
        self._optimality_logged = True
    return proven
```

(`_optimality_logged` defaulted False via `getattr`; no `__init__` change needed.)

### 2. `src/ffc_ddw_sum_et/orchestration/controller.py`

**2a — `calc_mcf_lb_and_derive_full_sch` (lines 1432, 1438, 1444, 1450, 1470, 1479, 1488)**:
Log immediately before each `_make_stop_report` / `return report` short-circuit so the phase boundary is recorded. The `_make_stop_report` log added in (1a) gives the why; this log gives the where.

Pattern (adjust phase tag per site):
```python
if self.is_stopping_condition():
    self.logger.info(
        "calc_mcf_lb_and_derive_full_sch: stop guard fired before %s",
        "<phase tag>",  # e.g. "round1_apply_lb_by_mcf", "round1_heuristic_last_stage_only", "round1_build_full_sch", "round2_check", "round2_apply_lb_by_mcf", "round2_heuristic_last_stage_only", "round2_build_full_sch"
    )
    return self._make_stop_report(start_elapsed)  # or `return report`
```

**2b — `controller.neh_cp` (lines 2318-2344)**: log the deadline + LB threading.
```python
remaining_sec = self.timer.get_remaining_sec(self.stopping_criteria.timelimit)
wall_clock_deadline_sec = time.monotonic() + remaining_sec

valid_lb = self.get_current_valid_lb()
objective_lower_bound = valid_lb if valid_lb > 0 else None

self.logger.info(
    "neh_cp: threading wall_clock_deadline=%.3fs (remaining=%.3fs), "
    "objective_lower_bound=%s",
    wall_clock_deadline_sec,
    remaining_sec,
    f"{objective_lower_bound:.2f}" if objective_lower_bound is not None else "None",
)
```

**2c — `controller.run_mcf_lb_then_neh_cp` (lines 2526-2553)**: same log pattern as 2b, prefix `"run_mcf_lb_then_neh_cp:"`.

### 3. `src/ffc_ddw_sum_et/algorithm/mcf_lb/preemptive.py`

**3a — `solve_mcf_lb` (line 80)**: add optional `logger` param, log on raise.

```python
def solve_mcf_lb(
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    *,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
) -> McfLbResult:
    ...
    if stop_predicate is not None and stop_predicate():
        if logger is not None:
            logger.info(
                "solve_mcf_lb: stop_predicate True before LP solve; "
                "raising MCFLBStopRequested."
            )
        raise MCFLBStopRequested
```

Plus a log right after `mcf.solve()` returns:
```python
if logger is not None:
    logger.info(
        "solve_mcf_lb: solved in %.3fs, mcf_lb=%.2f",
        diagnostic.mcf_solve_sec,
        mcf_lb,
    )
```

**3b — Update both call sites in `controller.py`** (`apply_lb_by_mcf` line 658, `run_mcf_lb_then_neh_cp` line 2462) to pass `logger=self.logger`.

### 4. `src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py`

**4a — After batches built (line 102)**: log batch plan.
```python
logger.info(
    "neh_cp: %d jobs split into %d batches (sizes=%s); "
    "objective_lower_bound=%s, wall_clock_deadline_sec=%s",
    n,
    len(batches),
    [len(b) for b in batches],
    f"{option.objective_lower_bound:.2f}" if option.objective_lower_bound is not None else "None",
    f"{option.wall_clock_deadline_sec:.3f}" if option.wall_clock_deadline_sec is not None else "None",
)
```

**4b — When last-batch obj_lb fires (line 134-139)**: log the injection.
```python
is_last_batch = step == len(batches) - 1
obj_lb_for_build = (
    option.objective_lower_bound
    if is_last_batch and option.objective_lower_bound is not None
    else None
)
if obj_lb_for_build is not None:
    logger.info(
        "neh_cp step %d: last batch — passing obj_lb=%.2f to CP-SAT",
        step,
        obj_lb_for_build,
    )
```

**4c — After every primary CP-SAT solve (line 255)**: log status + obj + bound + time.
```python
status = solver.solve(mdl)
logger.info(
    "neh_cp step %d: primary CP-SAT status=%s, obj=%s, bound=%.2f, "
    "wall=%.3fs, applied_tl=%s",
    step,
    solver.StatusName(status),
    f"{int(solver.ObjectiveValue())}" if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else "None",
    float(solver.best_objective_bound) if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else 0.0,
    solver.wall_time,
    f"{applied_tl_seconds:.3f}" if applied_tl_seconds is not None else "None",
)
```

**4d — Inside the recovery branch (line 480)**: log #recovered jobs.
```python
if stopped_early:
    remaining_jobs = [j for j in job_sequence if j not in scheduled_job_set]
    logger.info(
        "neh_cp: recovery dispatch — %d/%d remaining jobs (scheduled=%d).",
        len(remaining_jobs),
        n,
        len(scheduled_job_set),
    )
    if remaining_jobs:
        ...
```

**4e — At natural completion (line 528-530)**: log obj.
```python
final = partial_sol
sum_e, sum_t = compute_weighted_earliness_tardiness(final, instance)
obj_value = float(sum_e + sum_t)
logger.info(
    "neh_cp: completed all %d batches naturally; obj=%.0f, makespan=%d.",
    len(batches),
    obj_value,
    int(final.makespan),
)
```

## Files touched

- `src/ffc_ddw_sum_et/orchestration/controller_core.py` (1a, 1b)
- `src/ffc_ddw_sum_et/orchestration/controller.py` (2a × 7 sites, 2b, 2c, 3b × 2 sites)
- `src/ffc_ddw_sum_et/algorithm/mcf_lb/preemptive.py` (3a)
- `src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py` (4a, 4b, 4c, 4d, 4e)

## Out of scope

- `is_stopping_condition` itself (called many times; reason is captured via `_make_stop_report` and `_optimality_proven`).
- Tests (no behavioral change; existing tests should pass).
- DEBUG-level logs (per user direction, all INFO so they show up in default-level run logs).

## Verification (after edits)

Re-run both configs:
```
uv run python main.py --config metadata/20260506/stopping_verify_small_config.yaml
uv run python main.py --config metadata/20260506/stopping_verify_large_config.yaml
```

Then `grep` for the new log lines in `*_SubroutineController.log` to confirm
each commit's behavior fired (or didn't) with direct evidence.
