# Plan: Enforce One-Registration-Per-Call Contract

## Context

The project contract requires every config-level step method to register exactly one
`SubroutineReport` per invocation. Two methods violate this:

- **`run_mcf_lb_4`**: Phase 3 registers unconditionally, then Phase 4 also registers
  (2 history entries on every Phase 4 path). Additionally, the Phase 4-infeasible path
  registers `solution=None`, silently discarding the already-computed Phase 3 schedule.
- **`calc_mcf_lb_and_derive_full_sch`**: delegates to `apply_lb_by_mcf`,
  `heuristic_last_stage_only_sch_from_mcf_lb`, and `build_full_sch_from_last_stage_only_sch`,
  each of which registers independently — 3 entries per round, up to 6 for the 2-round path.

Both are fixed so that each call produces **exactly one** `SolutionRecord` in
`solution_manager.history`, regardless of which internal branch is taken.

**File modified:** `src/ffc_ddw_sum_et/orchestration/controller.py`

---

## Change 1 — `run_mcf_lb_4` (two edits)

### 1a. Remove Phase 3 registration (lines 1736–1747)

Delete the entire `self.solution_manager.register(...)` block after the
`mcf_lb_phase_schedules` appends for Phase 3. No return is added; execution falls
through to Phase 4 as before.

### 1b. Fix Phase 4-infeasible path (lines 1779–1784)

Change `self.solution_manager.register(report, None)` → register with Phase 3's
full schedule so the single registration also sets the incumbent:

```python
self.solution_manager.register(
    report,
    FFcDDWSolution(
        schedule=phase3.full_sch_from_ls_only_sch,
        obj_value=phase3.dispatched_obj,
        obj_bound=obj_bound_by_mcf,
    ),
)
```

**Result per path:**

| Path | Old count | New count |
|---|---|---|
| Phase 2 → None | 1 | 1 ✓ |
| Phase 3 → None | 1 | 1 ✓ |
| Phase 4 infeasible | 2 | 1 ✓ |
| Phase 4 feasible | 2 | 1 ✓ |

---

## Change 2 — Add `_register: bool = True` to two sub-methods

### 2a. `apply_lb_by_mcf` (line 728 area, signature lines 431–443)

Add `_register: bool = True` as the last parameter. Wrap the register call:

```python
if _register:
    self.solution_manager.register(report, None)
```

Return `report` unconditionally (already the case).

### 2b. `heuristic_last_stage_only_sch_from_mcf_lb` (line 1257 area)

Same pattern: add `_register: bool = True`, wrap `self.solution_manager.register(...)`.

Public callers (config.yaml direct calls, outer loop) use the default `_register=True`
— **no behavioral change for standalone use**.

---

## Change 3 — Extract `_build_full_sch_core()` from `build_full_sch_from_last_stage_only_sch`

Extract a private method containing all existing computation:

```python
def _build_full_sch_core(
    self,
) -> tuple[SubroutineReport, FFcDDWSolution | None]:
    """Compute full schedule from last_stage_only_sol. Does NOT register."""
    start_elapsed = time.monotonic()
    state = reverse_dispatch_full_schedule(...)
    elapsed = time.monotonic() - start_elapsed
    if state is None:
        self.logger.warning(...)
        return SubroutineReport(elapsed_time=elapsed, obj_value=None, obj_bound=0.0), None
    # ... mcf_lb_phase_schedules appends, logger.info ...
    solution = FFcDDWSolution(
        schedule=state.full_sch_from_ls_only_sch,
        obj_value=state.dispatched_obj,
        obj_bound=0.0,
    )
    return SubroutineReport(elapsed_time=elapsed, obj_value=state.dispatched_obj, obj_bound=0.0), solution
```

The public method becomes a thin wrapper (1 registration, return value unchanged):

```python
def build_full_sch_from_last_stage_only_sch(self) -> SubroutineReport:
    report, solution = self._build_full_sch_core()
    self.solution_manager.register(report, solution)
    return report
```

---

## Change 4 — Redesign `calc_mcf_lb_and_derive_full_sch`

### Sub-method calls

Replace all three public sub-method calls with their no-registration equivalents:

- `r_lb_r1 = self.apply_lb_by_mcf(..., _register=False)` — captures `obj_bound=mcf_lb`
- `self.heuristic_last_stage_only_sch_from_mcf_lb(..., _register=False)`
- `r1, s1 = self._build_full_sch_core()`

Round 2 sub-calls similarly use `_register=False` and `_build_full_sch_core()`.

### Makespan-delta check

The current check reads `self.solution_manager.get_incumbent()` to get the round-1
makespan. In the new design the incumbent has not been registered yet, so use `s1`
directly:

```python
if s1 is None:
    self.solution_manager.register(r1, s1)
    return r1
incumbent_makespan = int(s1.schedule.makespan)
ls_only_pmtn_makespan = int(self.mcf_preemptive_schedule.makespan)
makespan_delta = incumbent_makespan - ls_only_pmtn_makespan
```

### Stop-guard return paths

Stop guards that fire **before** round-1 produces a result (before `_build_full_sch_core`)
continue to return `_make_stop_report(start_elapsed)` **without registering** —
consistent with current behaviour.

Stop guards that fire **after** round-1 has a result (all `return report` sites in the
current code) must now register `(r1, s1)` before returning:

```python
self.solution_manager.register(r1, s1)
return r1
```

### Final registration (single call per path)

After all rounds complete, create one synthesized report carrying the total elapsed
time and the MCF LB (which `_build_full_sch_core` does not carry):

```python
best_r, best_s = r1, s1
if s2 is not None and (s1 is None or s2.obj_value <= s1.obj_value):
    best_r, best_s = r2, s2

final_report = SubroutineReport(
    elapsed_time=time.monotonic() - start_elapsed,
    obj_value=best_r.obj_value,
    obj_bound=r_lb_r1.obj_bound,   # MCF LB from round-1 apply_lb (always valid)
)
self.solution_manager.register(final_report, best_s)
return final_report
```

`obj_bound=r_lb_r1.obj_bound` ensures the MCF LB appears in the single registered
report and is picked up by the runner's `max(bound_values)` query — preserving the
existing observable bound-tracking behaviour.

**Result per path:**

| Path | Old count | New count |
|---|---|---|
| Stop before any work | 0 | 0 (unchanged) |
| Stop after apply_lb or heuristic (no solution yet) | 1–2 | 0 (unchanged) |
| Round 1 only (`adjust_p=adjust_r=False`) | 3 | 1 ✓ |
| Round 1 + stop before/during round 2 | 3 | 1 ✓ |
| Round 1 + round 2 (delta > 0) | 6 | 1 ✓ |

---

## Verification

```bash
uv run ruff check src/ffc_ddw_sum_et/orchestration/controller.py
uv run pytest tests/orchestration/test_controller.py -v
```

Existing tests (`test_run_mcf_lb_registers_dispatch_incumbent`,
`test_run_mcf_lb_not_greater_than_fam`) check incumbent correctness and bound
relationships — both should still pass unchanged.

Optional: add `assert len(controller.solution_manager.history) == 1` to
`test_run_mcf_lb_registers_dispatch_incumbent` to explicitly lock the contract.
