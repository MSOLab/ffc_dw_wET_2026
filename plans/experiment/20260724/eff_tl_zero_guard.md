# Plan: eff_tl budget guard in CP-SAT dispatchers

## Intent

When the CP-SAT time budget is exhausted **before or during** the solve so
the solver produces no improving solution, do **not** report an error. Time
exhaustion is a normal time-limit outcome, not a fault. Both dispatchers are
warm-started from an incumbent, so the honest result is the incumbent itself,
returned as a `FEASIBLE` / `TIME_LIMIT` record.

## Why not `WorkStatus.ERROR` + `schedule=None`

1. **Semantics.** Running out of time is not an error;
   `work_status=ERROR` + `termination_reason=TIME_LIMIT` is self-contradictory.
2. **Reporting regression.** The instance-level `work_status`
   (`controller_core.py`) is derived from `history[-1].solution`: if the
   **last** step registers `solution=None`, the reported `workStatus` drops to
   `null` even though a good incumbent exists. In the active
   `metadata/20260724/lastsemi_fullgrid.yaml` (tl05) flow,
   `solve_base_model_cpsat` is the last CSR-child step and the budget-guard
   fires on the common path — so `None` would routinely null the status.
3. **Time is still recorded either way.** The controller measures
   `elapsed = monotonic() - start_elapsed` itself and registers the report on
   both the solution and the `None` branch, so returning the incumbent does not
   change how elapsed time is captured — it only keeps `work_status` truthful
   and logs a meaningful `obj_value` (delta `+0`) instead of `None`.

## Design: incumbent fallback

Unified rule for "solver produced no improving solution under the budget":

- **Incumbent present** → return it unchanged:
  `work_status=FEASIBLE`, `termination_reason=TIME_LIMIT`, `error=None`,
  `result.schedule=<incumbent>`, `obj_value=` weighted E/T of the incumbent
  (recomputed with `option.time_factor`),
  `metrics={"cpsat_status": <reason>, "fallback": "incumbent"}`.
  `<reason>` is `"budget_exhausted_before_solve"` (pre-solve) or the CP-SAT
  status name (post-solve, e.g. `UNKNOWN`).
- **No incumbent** (only reachable in `CpsatAdapter`, which allows
  `ref_solution=None`) → keep the existing no-solution return (`schedule=None`);
  there is genuinely nothing to hand back.

`INFEASIBLE` is **out of scope**: a proven-infeasible model is a real terminal
outcome, not a time issue, and stays `WorkStatus.INFEASIBLE` /
`TerminationReason.COMPLETED`.

## Changes

### A. `FlipMakespanCpDispatcher` (`flip_makespan_cp/dispatcher.py`)

`ref_solution` is required, so the fallback always has an incumbent.
- Add `_incumbent_fallback_record(...)` helper.
- Pre-solve guard (after `eff_tl` at line ~182): on `eff_tl <= 0`, return the
  fallback instead of `ERROR`.
- Post-solve no-solution block: keep `INFEASIBLE` as-is; route every other
  no-solution status through the fallback.

### B. `CpsatAdapter` (`cpsat_adapter.py`)

`ref_solution` may be `None`.
- Add `_incumbent_fallback_record(..., base_progress_log=())` helper (prepends
  the recorder-built `progress_log` on the post-solve path).
- Pre-solve guard (after `eff_tl` at line ~133): incumbent present → fallback;
  else → keep the `schedule=None` return.
- Post-solve no-solution block: `INFEASIBLE` as-is; incumbent present →
  fallback; else → keep the existing no-solution return.

## Tests

- `tests/algorithm/test_flip_makespan_cp.py`: `cp_tl_seconds=0.0` with an
  incumbent hits the pre-solve guard and returns `FEASIBLE` / `TIME_LIMIT`,
  `error is None`, `schedule is` the incumbent, `metrics["fallback"] ==
  "incumbent"`.
- `tests/algorithm/test_cpsat_adapter_budget_guard.py` (new): `timelimit_sec=0.0`
  with an incumbent → fallback; without an incumbent → `schedule=None`.

## Out of scope

- Controller-level early exit (controller can already skip steps via
  `remaining_sec`).
- A new `WorkStatus` value for "timed out, no solution" (YAGNI; the enum gap is
  sidestepped by returning the feasible incumbent, and the no-incumbent case is
  a defensive edge the real flows never hit).
