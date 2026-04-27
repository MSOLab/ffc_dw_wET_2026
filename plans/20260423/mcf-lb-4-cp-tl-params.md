# Plan: Add `last_stage_only_tl` / `full_cp_tl` to `run_mcf_lb_4`

## Goal

Add two optional time-limit parameters to `run_mcf_lb_4`:

- `last_stage_only_tl: float | str | None = None` — per-solve wall-clock cap for the Phase 2 last-stage-only CP-SAT model.
- `full_cp_tl: float | str | None = None` — per-solve wall-clock cap for the Phase 4 full CP-SAT model.

### `float | str | None` resolution rules

| Input | Resolved `float | None` |
|---|---|
| `None` | `None` (no limit) |
| `float` | use as-is |
| `str` ending with `"nc"`, prefixed by a number | `number * job_count * stage_count` |
| other `str` | `float(value)`, raise `ValueError` if it fails |

---

## Call chain

```
run_mcf_lb_4                            (controller.py)
  → resolve at call site → float | None
  → run_phase2(..., cp_tl=...)          (phase2_last_stage.py)
      → _solve_last_stage_for_seed(..., cp_tl=...)
          → solve_last_stage_with_profile_fix(..., max_time_in_seconds=...)
                                        (cumulative_routine.py)
              → CpsatSolverOptions(max_time_in_seconds=...)
  → run_phase4(..., cp_tl=...)          (phase4_profile_fix.py)
      → solve_full_cp_with_profile_fix(..., max_time_in_seconds=...)
                                        (cumulative_routine.py)
              → pf_solver.parameters.max_time_in_seconds = ...
```

---

## Files to change

### 1. `controller.py`

- Add module-level helper `_resolve_cp_tl(tl_raw: float | str | None, job_count: int, stage_count: int) -> float | None`.
- Add `last_stage_only_tl: float | str | None = None` and `full_cp_tl: float | str | None = None` to `run_mcf_lb_4` signature.
- Resolve both early in the body (before Phase 1).
- Pass resolved values to `run_phase2(..., cp_tl=resolved_ls_tl)` and `run_phase4(..., cp_tl=resolved_full_tl)`.

### 2. `phase2_last_stage.py`

- Add `cp_tl: float | None = None` to `run_phase2` and `_solve_last_stage_for_seed`.
- Thread through to `solve_last_stage_with_profile_fix(..., max_time_in_seconds=cp_tl)`.

### 3. `phase4_profile_fix.py`

- Add `cp_tl: float | None = None` to `run_phase4`.
- Thread through to `solve_full_cp_with_profile_fix(..., max_time_in_seconds=cp_tl)`.

### 4. `cumulative_routine.py`

- `solve_last_stage_with_profile_fix`: add `max_time_in_seconds: float | None = None`; pass to `CpsatSolverOptions(max_time_in_seconds=max_time_in_seconds)`.
- `solve_full_cp_with_profile_fix`: add `max_time_in_seconds: float | None = None`; set `if max_time_in_seconds is not None: pf_solver.parameters.max_time_in_seconds = max_time_in_seconds` after the solver is created.

---

## No changes needed

- `MCFLBOption` — not wired to `run_mcf_lb_4` directly; leave it unchanged (YAGNI).
- Tests — callers use `run_mcf_lb_4()` with no args; default `None` is backward-compatible, no test changes required.
