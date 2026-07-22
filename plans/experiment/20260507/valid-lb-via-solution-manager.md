# Plan: Track valid global LB via SolutionManager.best_obj_bound

## Context

`controller_core.get_current_valid_lb()` is the single read-side getter that
downstream steps (e.g. `solve_base_model_cpsat`, `neh_cp`) use to fetch the
"current best valid global lower bound on the original problem" so they can
either prune their own search or hand it to CP-SAT as a hard constraint
(`sum(et_terms) >= ceil(obj_lb)` inside `BaseModelBuilder._define_objective`).

Today the getter pulls from `self.mcf_lb_diagnostic`:

```python
def get_current_valid_lb(self) -> float:
    diag = self.mcf_lb_diagnostic
    if diag is None: return 0.0
    if diag.mcf_lb is None: return 0.0
    if not diag.mcf_lb_is_valid_for_main_problem: return 0.0
    return float(diag.mcf_lb)
```

**Problem:** `mcf_lb_diagnostic.mcf_lb` is a single field that is **overwritten**
by each `apply_lb_by_mcf` call. In `calc_mcf_lb_and_derive_full_sch` round 2,
positive p/r adjustments fire and overwrite the diagnostic's `mcf_lb` with the
augmented-problem LB, simultaneously setting
`adjust_*_increment_added > 0` so `mcf_lb_is_valid_for_main_problem == False`.
Round 1's sound LB (no adjustments, valid global LB) is silently lost.

**Concrete reproducer** — run directory
`output/20260507_debug/20260507T163513_047764/mcf_lb_best_base_cpsat/Instance_50_5_3_0,2_0,2_10_Rep1`:

| Round | MCF LB | p_adjust | r_adjust | Valid for original? |
|---|---|---|---|---|
| 1 | 5771 | 0 | 0 | ✅ |
| 2 | 7705 | +5 | +34 | ❌ (augmented only) |

The composite synthesizes `final_report.obj_bound = r_lb_r1.obj_bound = 5771`
and registers it — but `get_current_valid_lb()` never reads the registered
report. It only reads the (now-tainted) diagnostic and returns 0. So the
follow-up `solve_base_model_cpsat` logs `obj_lb=None` and CP-SAT's internal
bound starts at 0 instead of 5771.

## Approach

Make `solution_manager.best_obj_bound` the single source of truth for the
running best **valid global LB**, leveraging the routix `SolutionManager`
mechanism that already runs on every `register(...)`:

```python
# routix/solution_manager.py:111-114
if report.obj_bound is not None:
    if self._a_is_better_obj_bound(report.obj_bound, self.best_obj_bound):
        self.best_obj_bound = report.obj_bound
```

The routix base already keeps a running max if `_a_is_better_obj_bound`
returns the proper comparison. Today `FFcDDWSolutionManager._a_is_better_obj_bound`
returns `False` unconditionally (commented "FAM does not produce useful
bounds"), so `best_obj_bound` is never updated. Flipping that one method
turns on the running-max tracking.

The diagnostic stays as a per-step record but is no longer the source of
truth for the LB getter.

**Soundness invariant the new design relies on:**

> Any `SubroutineReport` registered into `FFcDDWSolutionManager` with
> `obj_bound is not None` must be a valid global LB on the **original**
> (un-augmented) problem.

The plan verifies this invariant at every register site below before
flipping the comparator.

## Files

- `src/ffc_ddw_sum_et/orchestration/solution_manager.py` — flip `_a_is_better_obj_bound`.
- `src/ffc_ddw_sum_et/orchestration/controller_core.py` — rewrite `get_current_valid_lb`.

## Changes

### Change 1 — `solution_manager.py:32-39`

Replace `_a_is_better_obj_bound` so it actually tracks max:

```python
def _a_is_better_obj_bound(self, bound_a: float, bound_b: float | None) -> bool:
    if bound_b is None:
        return True
    return bound_a > bound_b
```

The class-level docstring/comment about "FAM does not produce useful bounds"
no longer applies — FAM's bound entries (`controller.py:104, 111`) come from
its own `AlgRecord.result.obj_bound` which is None for FAM today, so
flipping the comparator is a no-op for FAM and a net improvement for
`apply_lb_by_mcf` and any future LB-producing step.

### Change 2 — `controller_core.py:114-126` `get_current_valid_lb`

Replace the diagnostic-reading body with a thin wrapper over the solution
manager:

```python
def get_current_valid_lb(self) -> int:
    """Return the running max valid global LB tracked by the solution
    manager (updated on every ``register(...)`` whose report carries a
    non-None ``obj_bound``), rounded up via ``math.ceil`` so the value is
    never weakened. Returns ``0`` (the trivial valid LB for weighted
    earliness/tardiness) when no step has reported a bound yet.

    Soundness invariant: every register site in this codebase that emits
    ``obj_bound is not None`` already gates on validity for the original
    problem (see ``apply_lb_by_mcf`` controller.py:720-729 and the
    composite ``calc_mcf_lb_and_derive_full_sch`` synthesizer at
    controller.py:1485). The getter trusts that gate.
    """
    lb = self.solution_manager.best_obj_bound
    return math.ceil(lb) if lb is not None else 0
```

Other usages of `mcf_lb_diagnostic` inside `controller_core.py`
(`_optimality_proven_no_log` at line 136 calls `get_current_valid_lb`,
which is now solution-manager-backed automatically; line 170-175 reads
`diag.mcf_lb_is_valid_for_main_problem` for an unrelated guard and stays
as-is) are reviewed but left unchanged unless the audit below flags them.

### Verification — register sites that emit `obj_bound`

Audit every `SubroutineReport(obj_bound=...)` in `controller.py` to confirm
it cannot leak an augmented-only LB. Sites already grep'd:

- **`apply_lb_by_mcf` (line 729)** — already gated:
  `obj_bound=(obj_bound_by_mcf if obj_bound_is_valid else None)` where
  `obj_bound_is_valid` requires zero p_increment, `r_multiplier <= 1.0`,
  zero r_increment. ✅
- **`calc_mcf_lb_and_derive_full_sch._register_final` (line 1485)** —
  carries `r_lb_r1.obj_bound`, i.e. round-1's already-gated value. ✅
- **`run_fam` (line 104, 111)** — passes through `record.result.obj_bound`
  from the FAM `AlgRecord`. FAM is a heuristic and produces no LB today
  (FAM's `AlgResult.obj_bound` is `None`), but the type allows a future
  LB. **Gate audit needed before flipping the comparator** to confirm
  FAM either always emits None or always emits a valid global LB.
- **`run_bn2d` (line 184, 191)** — same pattern as `run_fam`. **Gate
  audit needed.**
- **`solve_base_model_cpsat` (line 2418)** — passes through
  `record.result.obj_bound` from `CpsatAdapter`, which is
  `solver.best_objective_bound` on the base CP model. The base CP model
  is an exact formulation of the original problem (no p/r augmentation
  in the model itself; the `obj_lb` constraint we're enabling only
  raises the floor, never cuts feasible solutions of the original).
  CP-SAT's bound is therefore a valid global LB. ✅
- **Inner `heuristic_last_stage_only_sch_from_mcf_lb` (line 818, 840)** —
  emits `obj_bound=mcf_lb` reading `self.mcf_lb_diagnostic.mcf_lb`
  directly (no validity gate). When called from outside the composite,
  this could leak an augmented LB. **Today both call sites are
  composite-internal with `_register_report=False`** so register is
  suppressed, but this is fragile. Add a validity gate symmetric to
  `apply_lb_by_mcf:720-729` — emit `obj_bound=mcf_lb` only when the
  diagnostic's `mcf_lb_is_valid_for_main_problem` is True; otherwise
  `obj_bound=None`. Defensive but cheap.

If the FAM/bn2d audit shows their adapters could emit a non-None bound
that is **not** a valid global LB, we either gate at the controller's
register site or fix the adapter to only emit valid bounds. (Likely
no-op based on a quick read of the adapters, but to be confirmed before
flipping the comparator.)

## Order of operations

1. Audit FAM and bn2d adapter `obj_bound` paths; confirm None or valid.
2. Add validity gate to inner `heuristic_last_stage_only_sch_from_mcf_lb`
   register sites (lines 818, 840).
3. Flip `_a_is_better_obj_bound` in `solution_manager.py`.
4. Rewrite `get_current_valid_lb` in `controller_core.py`.
5. Re-run the same scenario and verify:
   - Controller log shows `solve_base_model_cpsat: ... obj_lb=5771.00`
   - Adapter log shows `CpsatAdapter: ... obj_lb=5771.00`
   - `_obj_log.json` `obj_bound` series for step 2 starts at ≥ 5771 (the
     model now has a hard `>= ceil(5771)` constraint, so CP-SAT's
     `best_objective_bound` cannot drop below it).
6. `uv run ruff check && uv run ruff format` clean.

## Why this design vs. alternatives

- **Alternative A (rejected): add a separate `best_valid_global_lb` field
  on `MCFLBDiagnostic`.** Couples the LB getter to one specific algorithm's
  diagnostic. Fragile: any new step that produces a sound LB would need to
  also update the diagnostic.
- **Alternative B (rejected): controller-level `_best_valid_lb` cache.**
  Re-implements what `SolutionManager.best_obj_bound` already does, scatters
  responsibility, and leaves no per-register audit trail.
- **Chosen: route through `SolutionManager.best_obj_bound`.** Single source
  of truth aligned with how the incumbent is tracked
  (`SolutionManager.incumbent_solution`, `best_obj_value`). One central
  invariant ("registered `obj_bound` ⇒ valid global LB"), audited once
  across register sites, then trusted by the getter.
