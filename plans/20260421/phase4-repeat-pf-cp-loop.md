# Phase 4 repeat profile-fix CP-SAT loop

## Context

Last commit `a028684` ("feat(mcf-lb): add repeat pf-cp loop, config 10")
added a "repeat the profile-fix CP-SAT solve until objective stalls" loop
to **Phase 2** (last-stage-only solve) by extracting the solve into
`cumulative_routine.solve_last_stage_with_profile_fix(..., repeat_while_improving=True)`
and wiring a `repeat_pf_cp_while_improving` flag through the controller.

We want to apply the **same idea to Phase 4** (the full-model profile-fix
CP-SAT solve in [src/ffc_ddw_sum_et/algorithm/mcf_lb/phase4_profile_fix.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase4_profile_fix.py)).
After Phase 4 returns a feasible schedule, that schedule itself defines a
new (potentially tighter) profile-fix precedence pattern; re-solving with
the new schedule as the dispatched-schedule reference can sometimes shave
the objective further before the time budget is up.

The flag `repeat_pf_cp_while_improving` is reused — it already turns on
the Phase 2 loop in config 10, and after this change it will additionally
turn on the Phase 4 loop. Time-budget considerations (Phase 4 has no
internal time limit) are the user's responsibility via the scenario
`timelimit`.

## Changes

### 1. Add `solve_full_cp_with_profile_fix` to `cumulative_routine.py`

[src/ffc_ddw_sum_et/algorithm/cumulative_routine.py](src/ffc_ddw_sum_et/algorithm/cumulative_routine.py)

New function mirroring `solve_last_stage_with_profile_fix` but for the
**full** CP-SAT model:

- Builds the model with `BaseModelBuilder().build(instance, horizon=...)`
  — no `last_stage_only`, no `job_2_release`, no `obj_lb`.
- Reuses the existing precedence + start/end-hint helpers.
- Returns a new dataclass `FullCpSolveResult` with
  `status_name, schedule, objective, bound, j_i_2_start, j_i_2_end`.
- Returns `(result_or_None, total_solve_sec, last_status_name)`.
- When `repeat_while_improving=True`, loops: feed solved schedule back
  as `current_schedule`, rebuild and re-solve until
  `new_obj >= prev_obj` (matches Phase 2 stop condition).
- Match Phase 4's current lenient handling: any non-OPTIMAL/FEASIBLE
  status (including INFEASIBLE) breaks the loop and returns the best
  result so far rather than raising. (Phase 2 raises on INFEASIBLE
  because the last-stage model should always be feasible after MCF;
  Phase 4 has no such guarantee codified today, so keep it lenient to
  preserve current behavior.)
- The full schedule is built via
  `build_schedule_from_op_starts(instance, j_i_2_start, j_i_2_end)`
  (no `stages=` filter, since all stages are present).

### 2. Refactor `run_phase4` to call the new routine

[src/ffc_ddw_sum_et/algorithm/mcf_lb/phase4_profile_fix.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase4_profile_fix.py)

- Add kwarg `repeat_pf_cp_while_improving: bool = False`.
- Replace the inline build/solve block with a call to
  `solve_full_cp_with_profile_fix(phase3.dispatched_schedule, instance,
  profile_fix_by_machine=..., machine_precedence_stride=...,
  solver_thread_cnt=..., repeat_while_improving=repeat_pf_cp_while_improving)`.
- Diagnostic recording (per user choice "시간 누적 + 마지막 iter 값"):
  - `diagnostic.profile_fix_cp_sat_sec = total_solve_sec` (already
    accumulated inside the routine).
  - `diagnostic.pf_status = last_status_name`.
  - `diagnostic.profile_fix_bound = result.bound` (or fall back to
    `phase1.mcf_lb` if no result, matching today's `try/except` path).
  - `diagnostic.profile_fix_obj = final_obj` (computed via
    `compute_window_et` from the final schedule, same as today).
  - `obj_bound_final = max(phase1.mcf_lb, profile_fix_bound)` unchanged.
- Keep the existing post-build vs CP-SAT objective consistency warning
  (uses `result.objective` as the CP-SAT obj of the last iteration).
- Keep the no-feasible-solution fallback path returning a
  `Phase4State(obj_bound_final=...)` with `final_schedule=None`.

### 3. Wire the flag through the controller

[src/ffc_ddw_sum_et/orchestration/controller.py](src/ffc_ddw_sum_et/orchestration/controller.py#L96-L226)

In `run_mcf_lb_4`, pass `repeat_pf_cp_while_improving=repeat_pf_cp_while_improving`
to `run_phase4(...)` (parameter already exists on the controller method
since the Phase 2 commit). Update the controller method's docstring to
mention the flag now also drives the Phase 4 loop.

### 4. Config 10 — header comment update only

[metadata/20260421/1_mcf_lb_init_10_config.yaml](metadata/20260421/1_mcf_lb_init_10_config.yaml)

Because the flag is shared, every scenario in config 10 already sets
`repeat_pf_cp_while_improving: true` — Phase 4 will automatically pick
it up after the code change. The only edit is to update the top-of-file
comment block to note that the flag now controls both phases (Phase 2
last-stage solve and Phase 4 full profile-fix solve), and to bump the
plan reference to this plan file.

No new YAML scenarios are added.

## Critical files

- [src/ffc_ddw_sum_et/algorithm/cumulative_routine.py](src/ffc_ddw_sum_et/algorithm/cumulative_routine.py) — add `solve_full_cp_with_profile_fix` next to `solve_last_stage_with_profile_fix`; reuse the same shape and conventions.
- [src/ffc_ddw_sum_et/algorithm/mcf_lb/phase4_profile_fix.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase4_profile_fix.py) — switch to the new routine, add `repeat_pf_cp_while_improving` kwarg.
- [src/ffc_ddw_sum_et/orchestration/controller.py](src/ffc_ddw_sum_et/orchestration/controller.py) — thread the flag into `run_phase4(...)`.
- [metadata/20260421/1_mcf_lb_init_10_config.yaml](metadata/20260421/1_mcf_lb_init_10_config.yaml) — header comment only.

## Reuse checklist

- `BaseModelBuilder.build(instance, horizon=...)` — full-model entrypoint, already used by today's Phase 4 and by `run_profile_fixed_ns`.
- `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule` — the profile-fix precedence helper used by both Phase 2 and Phase 4.
- `BaseModelBuilder.apply_start_hints_from_start_time_map` / `apply_end_hints_from_end_time_map` — warm-start helpers.
- `build_schedule_from_op_starts(instance, j_i_2_start, j_i_2_end)` — full-schedule constructor.
- `compute_window_et` — windowed E/T objective (kept inside `run_phase4`, not pushed into the routine, so the routine stays generic and matches the Phase 2 routine's responsibility split).

## Verification

1. Lint & format:
   - `uv run ruff check`
   - `uv run ruff format`
2. Sanity smoke run on a single small instance (edit `ins_index: [0]`,
   keep `instance_worker_cnt: 48`):
   - `uv run python main.py metadata/20260421/1_mcf_lb_init_10_config.yaml`
   - Inspect log for: Phase 4 making more than one CP-SAT call when
     `repeat_pf_cp_while_improving: true`, and stopping when objective
     stalls.
   - Verify diagnostic CSV: `profile_fix_cp_sat_sec` should be the sum
     across iterations; `pf_status`, `profile_fix_bound`, `profile_fix_obj`
     should match the final iteration.
3. Compare summary against config 9 (Phase 4 loop off) on a couple of
   instances to confirm objective is monotonically <= the single-shot
   Phase 4 result (modulo stochastic CP-SAT behavior at the time limit).
