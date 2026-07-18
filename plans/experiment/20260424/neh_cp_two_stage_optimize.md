# Two-Stage Optimize Port to `ffc_ddw_sum_et` NEH-CP

## Context

Port the lexicographic two-stage CP solve structure from
`D:\code\hybridflowshop\hybridflowshop\controller\neh_cp.py` into this project's
`src/ffc_ddw_sum_et/orchestration/neh_cp.py`.

- **Source pattern** (hybridflowshop): primary = minimize makespan, secondary
  = minimize sum C_i (lex tie-break). Implemented by re-building the sub CP
  model with `minimize_sum_ci=True` and a tightened horizon equal to the
  stage-1 makespan (so stage-1 optimum acts as a ceiling).
- **Target pattern** (this project): primary = minimize weighted E/T (current
  behavior), secondary = minimize makespan subject to weighted E/T ≤ stage-1
  value (lex tie-break). The docstring of the existing file already marks
  this as a known follow-up ("The two-stage optimize port ... is a follow-up
  task.") so this is the intended direction.

Goal: give each NEH-CP batch solve a principled makespan reduction step once
the E/T-optimal solution is found, improving downstream batches that warm-start
from this partial solution (tighter horizons, less tail work).

The experiment config at `metadata/20260424/neh_cp_config_6.yaml` is already
wired to exercise this flag (`minimize_makespan_lex: true`,
`cp_tl_2nd_obj: "0.05c"`) and is the end-to-end smoke target.

## Files to Modify

1. `src/ffc_ddw_sum_et/algorithm/cumulative.py` — extend `BaseModelBuilder.build`
   / `_define_objective` to support the secondary makespan-minimization mode.
2. `src/ffc_ddw_sum_et/orchestration/neh_cp.py` — add the lex stage-2 solve
   inside `NehCpConstructor.run`'s per-batch loop and update the obj log.
3. `src/ffc_ddw_sum_et/orchestration/controller.py` — pass-through the two
   new parameters on `FFcDDWSubroutineController.neh_cp`.
4. `main.py` — switch `CONFIG_PATH` to `metadata/20260424/neh_cp_config_6.yaml`
   for the smoke run.

No new modules; no API breakage (all additions are keyword-only with safe
defaults).

## Design

### 1. `BaseModelBuilder.build` — secondary objective mode

In `src/ffc_ddw_sum_et/algorithm/cumulative.py` (current objective is defined
in `_define_objective`, called from `build`):

- Add two keyword-only params to `build` and propagate to `_define_objective`:
  - `minimize_makespan_lex: bool = False`
  - `et_ub: int | float | None = None` — required when
    `minimize_makespan_lex=True`.
- In `_define_objective`, always construct the `E_j`, `T_j` vars and the
  weighted E/T terms as today. Then branch:
  - **Default (current behavior):** apply `obj_lb` if given; call
    `mdl.minimize(sum(et_terms))`.
  - **`minimize_makespan_lex=True`:**
    - Require `et_ub is not None`; raise `ValueError` otherwise.
    - Add constraint `mdl.add(sum(et_terms) <= math.floor(et_ub))`.
    - Create a makespan var `M = mdl.new_int_var(0, horizon, "makespan")`
      and tie via `mdl.add_max_equality(M, [op_end[j, last_i] for j in j_list])`.
    - Call `mdl.minimize(M)`.
- Return signature stays `tuple[CpModel, Params, OperationVars,
  EarlinessTardinessVars]` — the makespan var is internal to the builder and
  callers don't need it.

This mirrors the source's `minimize_sum_ci=True` toggle on
`hybridflowshop`'s `BaseModelBuilder.build`.

### 2. `NehCpConstructor.run` — stage-2 solve in the batch loop

In `src/ffc_ddw_sum_et/orchestration/neh_cp.py`:

- Add parameters (keyword-only after the existing ones, matching naming with
  the source):
  - `minimize_makespan_lex: bool = False`
  - `cp_tl_2nd_obj: float | str | None = None` — falls back to `cp_tl` if
    `None` (parallel to source's `max_time_per_add_2nd_obj = max_time_per_add`
    fallback).
- Resolve `cp_tl_2nd_obj_seconds = resolve_cp_tl(cp_tl_2nd_obj or cp_tl, n,
  stage_count)` once before the loop. (`resolve_cp_tl` already in
  `orchestration/tl_resolver.py`.)
- After the existing stage-1 block picks `partial_sol` and
  `last_obj_value = se + st`, and **only if `minimize_makespan_lex=True`**:
  1. `horizon_2 = partial_sol.makespan` (tightens to stage-1 ceiling; mirrors
     source).
  2. Build `mdl_2, params_2, op_vars_2, et_vars_2 = builder.build(sub_instance,
     horizon=horizon_2, minimize_makespan_lex=True, et_ub=last_obj_value)`.
  3. Apply the same partial-fix precedence using `partial_sol` as the
     reference (reuse
     `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule`
     with the decoded `pf_method`, same as stage 1).
  4. Solve with a fresh `cp_model.CpSolver()`, `num_workers =
     solver_thread_cnt`, `max_time_in_seconds = cp_tl_2nd_obj_seconds` (flat —
     intentionally not using `apply_cumulative_tl` for the 2nd objective to
     match the source's simpler policy).
  5. If feasible (`status in (OPTIMAL, FEASIBLE)`):
     - Decode to `cp_sch_2` via `build_schedule_from_op_starts`.
     - **Accept stage-2 result unconditionally** (if feasible). Because the
       horizon and the E/T upper bound constraint jointly guarantee
       `makespan ≤ partial_sol.makespan` and `E/T ≤ last_obj_value`, the
       stage-2 solution is always a lex-improvement or tie. This mirrors the
       source, which also accepts any feasible report_2 output.
     - Recompute `(se, st)` from `cp_sch_2` against `sub_instance` via
       `compute_weighted_earliness_tardiness`; update `last_obj_value` and
       `partial_sol = cp_sch_2`.
  6. If infeasible: log at INFO and keep the stage-1 `partial_sol`.
- Sub-obj log entry (`sub_obj_log.append({...})`): extend the existing dict
  with `"makespan": int(partial_sol.makespan)` and `"ran_2nd_obj": bool(...)`
  so the downstream yaml dump is informative. Keep `"sub_obj"` as the E/T
  value for continuity with existing consumers.

Keep the overall elapsed-time accounting as-is — stage-2 time is already
captured by the outer `time.monotonic()` bookkeeping.

### 3. `FFcDDWSubroutineController.neh_cp` — pass-through

In `src/ffc_ddw_sum_et/orchestration/controller.py` (lines 926–947):

- Add `minimize_makespan_lex: bool = False` and `cp_tl_2nd_obj: float | str |
  None = None` to the method signature.
- Forward them to `NehCpConstructor(self).run(...)`.
- Extend the method docstring-pointer (currently a one-liner delegating to
  `NehCpConstructor.run`).

Also update `NehCpConstructor.run`'s docstring (Args block) to document the
two new parameters, matching the rest of the block's style.

### 4. `main.py` — switch config for smoke run

Update `CONFIG_PATH` to `Path("metadata/20260424/neh_cp_config_6.yaml")` so
`uv run main.py` exercises the new flag on PRA2017 `ins_index: [0]`.

## Non-goals / Deliberate Omissions

- No changes to `MixedDispatcher`, `FFcSchedule`, or the warm-start/idle-time
  flow. Stage 1 still computes `dispatched.make_semi_active(...)` +
  `dispatched.insert_idle_time(...)` exactly as today.
- Stage-2 output is **not** run through `insert_idle_time`: idle insertion
  trades makespan for E/T, and stage 2's whole purpose is to keep E/T bounded
  while cutting makespan.

## Verification

1. **Type/import sanity:** `python -c "from ffc_ddw_sum_et.orchestration.neh_cp
   import NehCpConstructor"` and `...import FFcDDWSubroutineController` still
   succeed.
2. **End-to-end smoke:** `uv run main.py` with `CONFIG_PATH` pointing at
   `metadata/20260424/neh_cp_config_6.yaml`. Expected:
   - Run completes without errors.
   - `sub_obj_log[*].makespan` is weakly lower than (or equal to) what the
     same instance produced under `neh_cp_config_5.yaml`'s idv scenario on
     comparable seeds.
   - Final `obj_value` (weighted E/T) is ≤ the value from the
     `minimize_makespan_lex=False` baseline on the same instance.
3. **Lex guarantee spot-check:** scan the generated `_obj_log.yaml` under the
   run output to confirm, within each step, that the recorded `sub_obj`
   (weighted E/T) stays ≤ the stage-1 value and `makespan` stays ≤ the
   stage-1 makespan.
