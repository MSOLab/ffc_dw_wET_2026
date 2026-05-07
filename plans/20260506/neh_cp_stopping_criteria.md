# Plan: stopping criteria for NEH-CP

## Context

Commit `27ee89f` ("feat(stopping): prove optimality and stop guards") added
`is_stopping_condition` extension (timelimit OR proven optimality) and
in-flow guards inside `calc_mcf_lb_and_derive_full_sch`. Same commit also
plumbed `stop_predicate` into `solve_mcf_lb` directly — but did **not**
add the field to `AlgSpec` (it was deliberately unstaged: no AlgSpec
consumer existed in the commit's scope, so the field would have been
dead).

NEH-CP is the next surface. Today:

- `NehCpDispatcher.run` (`src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py:44-463`)
  iterates `for step, batch in enumerate(batches)` (line 125) and
  unconditionally runs every batch to completion. There is no outer-stop
  hook. The per-batch CP-SAT call at line 222/225 sets
  `solver.parameters.max_time_in_seconds` purely from the
  pre-computed `cp_tl_seconds_per_step[step]`, ignoring real wall-clock.
- `controller.neh_cp` (`controller.py:2257-2367`) constructs `AlgSpec(
  instance=instance, option=option, logger=self.logger)` (line 2334) and
  receives one `AlgRecord` at the end. It registers exactly one
  incumbent (via `solution_manager.register`) at line 2350 — no
  per-batch incumbent emission, so optimality cannot short-circuit
  mid-NEH-CP today.
- `controller.run_mcf_lb_then_neh_cp` (`controller.py:2369+`) is a
  sibling composite that also constructs `AlgSpec(...)` (line 2502)
  with `NehCpOption`; both sites must be updated together.
- `metadata/20260506/timelimit_debug_config.yaml` has the `neh_cp` step
  commented out. To behavioral-test this PR, that block must be
  uncommented (or a new debug config added).

User intent (from prior conversation, captured before context compact):
- Bring stopping criteria into ALL algorithms; this PR is the NEH-CP
  slice. Both timelimit enforcement and optimality short-circuit should
  apply.
- Re-add `AlgSpec.stop_predicate` together with at least one consumer
  (NehCpDispatcher) so the field is not dead.

## Phase split

This PR is **Phase 1** only. Phase 2 (mid-batch interruption /
per-batch incumbent emission for optimality short-circuit during
NEH-CP) is captured below as deferred — keep `docs/TODO.md` in mind
when promoting it.

## Phase 1 — timelimit enforcement (this PR)

### 1.1 Re-add `AlgSpec.stop_predicate`

**File**: `src/ffc_ddw_sum_et/algorithm/base/alg_spec.py`

Add field `stop_predicate: Callable[[], bool] | None = None` (kw_only,
default None) with a docstring stating: dispatchers should honor the
predicate at natural break-points; `None` means "no extra termination"
and preserves today's behavior.

This is the same change that was unstaged from commit `27ee89f`. It
ships now because NehCpDispatcher consumes it.

### 1.2 NehCpDispatcher honors `stop_predicate` between batches

**File**: `src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py`

After each batch's primary CP-SAT solve (and any 2nd-objective solve
when `minimize_makespan_lex=True`) — i.e. **after** line 407
(`prev_elapsed_seconds = step_elapsed_seconds`), and **after** line
408 (`last_obj_value = se + st`) — but **before** the next iteration:

```python
if spec.stop_predicate is not None and spec.stop_predicate():
    logger.info("neh_cp: stop_predicate fired after batch %d/%d", step + 1, len(batches))
    break
```

When the loop breaks early, the existing post-loop code at line 410+
still runs:
- `partial_sol` may be non-None → returns the partial schedule with
  weighted E+T computed for the jobs scheduled so far. **This is
  problematic**: `partial_sol` only covers `current_jobs ⊂
  instance.job_id_list`, but `compute_weighted_earliness_tardiness(final,
  instance)` at line 439 uses the full instance. Either:
  - (a) Skip the registration when broken early (set a sentinel and
    return an INFEASIBLE record), **or**
  - (b) Compute the partial obj using `sub_instance` (the last
    iteration's `current_jobs`-filtered instance), tag a metric like
    `metrics["partial_jobs"] = len(current_jobs)`, and let the
    controller decide whether to register.
  - **Choose (a) for Phase 1** — partial schedules over a job subset
    aren't directly comparable to incumbents over the full instance,
    and registering a non-comparable schedule would corrupt the
    incumbent. Tag termination as `TerminationReason.STOP_REQUESTED`
    (add to `algorithm/base/alg_record.py` if missing) and return
    `WorkStatus.INFEASIBLE` with `result=None` and `metrics =
    {..., "stopped_after_batch": step}`.

### 1.3 NehCpDispatcher clamps per-batch CP-SAT TL by remaining budget

**File**: `src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py`

Today (line 222 / 225): `solver.parameters.max_time_in_seconds =
applied_tl` (or `step_tl`). When `stop_predicate` is supplied, we want
to also cap each batch's CP-SAT solve by an elapsed-budget hint so a
single batch cannot blow past `stopping_criteria.timelimit`.

**Approach**: keep `stop_predicate` as the simple boolean probe used
above; add a second optional field on `AlgSpec` only if needed —
`deadline_monotonic: float | None = None`. **Decision before coding**:
Phase 1 picks the simpler path of injecting a *deadline* derived from
the controller's timer at NEH-CP entry, surfaced via a new optional
on `NehCpOption`:

- Add `wall_clock_deadline_sec: float | None = None` to `NehCpOption`.
- `controller.neh_cp` populates it with
  `self.timer.start_monotonic_equiv + self.stopping_criteria.timelimit`
  if both are available, else `None`. (The exact API on routix's
  `ElapsedTimer` for "monotonic time of start" — check
  `_start_monotonic` attribute, or compute as `time.monotonic() +
  (stopping_criteria.timelimit - self.timer.elapsed_sec)` at NEH-CP
  entry.)
- Inside `NehCpDispatcher.run`, before each `solver.solve(mdl)`:
  ```python
  if option.wall_clock_deadline_sec is not None:
      remaining = option.wall_clock_deadline_sec - time.monotonic()
      if remaining <= 0:
          break  # behave like stop_predicate fired
      solver.parameters.max_time_in_seconds = min(applied_tl, remaining)
  else:
      solver.parameters.max_time_in_seconds = applied_tl
  ```

Keep `option.wall_clock_deadline_sec is None` as a strict no-op path so
isolated tests / scripts that build `NehCpOption` directly without a
deadline retain today's behavior.

### 1.4 controller.neh_cp pre-flight guard + AlgSpec wiring

**File**: `src/ffc_ddw_sum_et/orchestration/controller.py:2257-2367`

- At entry (top of method, before `start_elapsed = time.monotonic()`):
  ```python
  if self.is_stopping_condition():
      return self._make_stop_report()
  ```
  Mirrors the pattern already in
  `calc_mcf_lb_and_derive_full_sch` (committed in `27ee89f`).
- When constructing `NehCpOption` (line 2315-2333), populate
  `wall_clock_deadline_sec`:
  ```python
  remaining = self.stopping_criteria.timelimit - self.timer.elapsed_sec
  wall_clock_deadline_sec = (
      time.monotonic() + remaining if remaining > 0 else time.monotonic()
  )
  ```
- When constructing `AlgSpec` (line 2334), pass
  `stop_predicate=self.is_stopping_condition`.
- After `record = NehCpDispatcher().run(spec)`, handle the new
  `WorkStatus.INFEASIBLE + metrics["stopped_after_batch"]` shape:
  emit a stop-report (`self._make_stop_report(start_elapsed)`) without
  registering an incumbent. If a *previous* step already registered an
  incumbent, `solution_manager` keeps it untouched.

### 1.5 controller.run_mcf_lb_then_neh_cp parity

**File**: `src/ffc_ddw_sum_et/orchestration/controller.py:2369+`

Apply the same three changes (pre-flight guard, deadline in option,
predicate in spec, stop-report on early termination) at the
sibling site at line 2502. Reuse helpers; do not duplicate logic.

### 1.6 Tests

**File**: `tests/algorithm/neh_cp/test_dispatcher_stop.py` (new)

- Build a small instance (4-6 jobs, 2 stages) where NEH-CP would run
  ≥3 batches with a generous `total_timelimit`. Pass a
  `stop_predicate` that returns `True` after the first batch via a
  closure counter. Assert: `record.work_status == INFEASIBLE`,
  `record.termination_reason == STOP_REQUESTED`,
  `record.result is None`, `metrics["stopped_after_batch"] >= 0`.
- Pass `wall_clock_deadline_sec = time.monotonic() - 1.0` (already in
  the past). Assert dispatcher breaks before doing any CP-SAT work
  (or after batch 0 only) and returns the same INFEASIBLE shape.
- Pass `stop_predicate=lambda: False` and no deadline. Assert
  dispatcher behaves identically to today (run a regression baseline
  test: same record shape as the same instance with `option`
  unchanged from prior runs).

**File**: `tests/orchestration/test_neh_cp_stopping.py` (new)

- Construct `FFcDDWSubroutineController` with a tiny instance, scenario
  flow `[{method: "neh_cp", total_timelimit: 1.0, ...}]`, and
  `stopping_criteria.timelimit=0.01`. Run `controller.run()`.
  Assert the controller exits with no incumbent (since the deadline
  fires before any batch completes) — or with an incumbent from a
  prior step if applicable. No exceptions.

### 1.7 Behavioral verification

Uncomment the `neh_cp` block in
`metadata/20260506/timelimit_debug_config.yaml` (lines 26-36) and
run:

```
uv run python main.py --config metadata/20260506/timelimit_debug_config.yaml
```

Inspect each `*_instance_result.yaml`: with `timelimit: 2.0`,
`elapsed_time` should not significantly exceed 2.0s for any instance
(today it can — calc_mcf_lb composite plus full NEH-CP can blow past).
With a tiny `timelimit: 0.05`, NEH-CP should be skipped or stopped
mid-batch.

## Phase 2 — superseded by last-batch LB constraint (shipped)

The original Phase 2 ideas (mid-batch CP-SAT interruption via
`CpSolverSolutionCallback.StopSearch()`; per-batch incumbent emission
with partial-job → full-instance schedule extension; mid-batch
optimality probe against the controller-stored MCF LB) are **dropped**.
Two reasons:

1. **Timelimit control**: The Phase-1 per-batch CP-SAT TL clamp
   (`solver.parameters.max_time_in_seconds = remaining_deadline`) plus
   the post-batch `stop_predicate` / deadline check is sufficient.
   Finer mid-batch interruption isn't worth the added solver-callback
   complexity at this point.

2. **Optimality short-circuit, cheap variant — implemented**: At the
   last NEH-CP batch every job is included by construction, so the
   CP-SAT model's objective at that batch IS the full-instance E+T
   objective and any valid global LB (the controller's
   `_current_valid_lb()` from MCF) is a valid lower bound on it.
   `BaseModelBuilder.build` already accepts an `obj_lb` kwarg that
   adds `sum(et_terms) >= math.ceil(obj_lb)` when set
   (`cumulative.py:382-383`), so no builder change was needed.
   `NehCpOption.objective_lower_bound: float | None` is threaded
   through and the dispatcher passes it to `builder.build` only when
   `step == len(batches) - 1`. CP-SAT then proves optimality whenever
   its solution matches the bound and terminates the last batch early
   without any dispatcher-side incumbent emission or partial-schedule
   extension.

## Files to touch in Phase 1

- `src/ffc_ddw_sum_et/algorithm/base/alg_spec.py` — add
  `stop_predicate` field.
- `src/ffc_ddw_sum_et/algorithm/base/alg_record.py` — add
  `TerminationReason.STOP_REQUESTED` if not already present (verify
  with `grep -n TerminationReason
  src/ffc_ddw_sum_et/algorithm/base/alg_record.py`).
- `src/ffc_ddw_sum_et/algorithm/neh_cp/option.py` — add
  `wall_clock_deadline_sec`.
- `src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py` — between-batch
  predicate check, per-batch CP-SAT TL clamp, early-exit record
  shape.
- `src/ffc_ddw_sum_et/orchestration/controller.py` — pre-flight guard
  + AlgSpec/NehCpOption wiring at both NEH-CP call sites
  (`neh_cp` and `run_mcf_lb_then_neh_cp`).
- `tests/algorithm/neh_cp/test_dispatcher_stop.py` — new.
- `tests/orchestration/test_neh_cp_stopping.py` — new.
- `metadata/20260506/timelimit_debug_config.yaml` — uncomment NEH-CP
  step (or add a sibling debug config) for behavioral verification.

## Reused utilities

- `is_stopping_condition`, `_make_stop_report`, `_optimality_proven`,
  `_current_valid_lb` from `controller_core.py` (committed in
  `27ee89f`). No changes; reuse.
- `MCFLBStopRequested` pattern from `algorithm/mcf_lb/preemptive.py` is
  *not* reused: NEH-CP's batch loop is interruptible without
  exception-based control flow, so a plain `break` is cleaner.

## Verification (pre-merge checklist)

1. `uv run ruff check` and `uv run ruff format --check` clean on touched
   files.
2. `uv run pytest -q` — full suite green; new tests pass.
3. Behavioral run with NEH-CP step uncommented: total per-instance
   elapsed_time stays within `2 × timelimit` even on the largest
   instances.
4. Sanity: with `stop_predicate=None` (default), all NEH-CP regression
   tests under `tests/algorithm/neh_cp/` produce identical records
   compared to before this PR. Spot-check by running a subset of
   existing tests on a clean checkout for byte-equality, or assert
   relevant fields via a parametrized regression test.
