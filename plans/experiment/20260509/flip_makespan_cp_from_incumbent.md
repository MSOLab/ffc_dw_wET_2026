# Plan: `run_flip_makespan_cp_from_incumbent`

## Intent

Add a new controller step that takes the current incumbent, time-flips it
on a stage-reversed instance, fixes the (now-first) reverse-stage to the
incumbent's right-shifted last stage, hints all remaining stages from the
incumbent, and minimises makespan with CP-SAT. Re-flip the result and
push it through the standard `make_semi_active` + `insert_idle_time`
post-processing.

The step is a sibling of Phase 3 of `run_mcf_lb_4`
(`reverse_dispatch_full_schedule`) but replaces the dispatcher-based
filling of stages 2..C of the reversed instance with a CP-SAT solve
warm-started from the full incumbent.

## Decisions (confirmed with user)

- **Right-shift**: reuse `FFcSchedule.delay_job_latest_leq_obj_contrib(job_2_dw_ub_map)`.
  Tardy ops stay; non-tardy ops are pushed up to `min(d_plus, next_op_new_start)`.
  Tardiness cannot increase; earliness can only decrease.
- **Hint source for stages 2..C of flipped CP**: incumbent's original
  schedule (no upstream-stage shifting) time-flipped by `delayed_makespan`,
  then compacted in flipped time via `make_semi_active(start_from_stage=2nd stage,...)`
  to remove the precedence-induced idle gaps before being read back as hints.
- **CP objective**: pure makespan minimisation. Realised by extending
  `BaseModelBuilder.build` with `objective: Literal["et", "makespan"] = "et"`.
- **Return type of `BaseModelBuilder.build`**: 4-tuple's last element
  becomes `EarlinessTardinessVars | None` (None when `objective="makespan"`).
- **First stage fix in flipped CP**: `add_start_time_freezed_operation_constraints`
  on the flipped seed (machine assignment is reconstructed post-solve via
  `build_schedule_from_op_starts`, so per-machine fixing is unnecessary
  for the cumulative model).
- **Post-solve**: `as_reversed` → `make_semi_active(stage_2_job_2_p_map)`
  → `insert_idle_time(...)` (Phase 3 ordering).
- **Failure**: keep incumbent, `_register(report, None, ...)`, log warning.
- **Dispatcher port**: full `AlgSpec`/`AlgRecord` port via
  `FlipMakespanCpDispatcher` + `FlipMakespanCpOption`. Incumbent is
  passed via `AlgSpec.ref_solution`.
- **Sub-schedules**: in addition to the compact `progress_log` JSON via
  the `obj_log_json` aggregator, emit **seven** intermediate schedules
  as **compact JSON** when `emit_phase_schedules=true`, with a
  2-digit prefix in `phase_name` so files sort by index on disk:
  `01_incumbent` (input), `02_right_shifted`, `03_flipped` (pre-compaction),
  `04_flipped_compacted`, `05_cp_solved`, `06_unflipped_semi_active`,
  `07_unflipped_final`. Files are registered through a new
  `flip_makespan_cp_phase_schedule` artifact kind
  (`{instance_name}_{phase_name}.json` in `progress/`), routed via
  `ArtifactLayout.artifact_path` from the controller. The reporter
  discovers them with `find_artifacts` and reuses
  `_render_gantt_from_solution_json` to produce one
  `phase_gantt_png` per phase (the JSON shape matches
  `dump_solution_json`). The `07_unflipped_final` file duplicates the
  canonical `solution_json` by design, so the phase trace can be
  inspected as a self-contained sequence. No new
  `mcf_lb_phase_schedules`-style controller-side collector.
- **Hint coverage debug**: `log_search_progress: bool` keyword on the
  option; when True the CP-SAT solve log (which prints hint coverage) is
  written to a per-step file via `solver_log_path_getter`.
- **Horizon**: the **compacted** flipped seed's makespan
  (`make_semi_active` applied with `start_from_stage = reversed.stage_id_list[1]`,
  so the fixed first stage is preserved). This is `≤ delayed_makespan` in
  general — strictly less when the incumbent had idle time on stages
  2..C in flipped time. The `compute_parallel_mc_horizon` helper stays
  in `algorithm/horizon.py` for reuse by `parallel_mc_pmtn`, but is
  *not* used in this dispatcher.
- **No `pf_method` exposure**, no `repeat_while_improving`, no
  `horizon_multiplier` knob (all deferred — YAGNI).
- **Prior step in the experiment scenario**: `calc_mcf_lb_and_derive_full_sch`.
- **`solution_manager` already keeps "best incumbent only"**, so no
  per-step better-than guard needed.

## File-level changes

### A. `src/ffc_ddw_sum_et/algorithm/horizon.py` (new)

```python
def compute_parallel_mc_horizon(
    p: Mapping[str, int],
    r: Mapping[str, int],
    mc_count: int,
    d_lower: Mapping[str, int] | None = None,
) -> int
```

- Returns `max_j(max(r_j, (d_lower_j - p_j) if d_lower else r_j)) + ceil(sum(p_j) / mc_count)`.
- Empty `p` raises `ValueError`.

### B. `src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py`

- `_define_parameters` calls the new helper for `t_max` (semantic-equivalent).

### C. `src/ffc_ddw_sum_et/algorithm/cumulative.py`

- `BaseModelBuilder.build`: add `objective: Literal["et", "makespan"] = "et"`.
  - `objective="makespan"`: forbid `obj_lb`, `et_ub`, `minimize_makespan_lex`;
    skip E/T variables; introduce `makespan` int-var via `add_max_equality`
    over `op_end[j, last_i]`; `mdl.minimize(makespan)`. Return `None` for
    the EarlinessTardinessVars slot.
- Return-type annotation: `EarlinessTardinessVars | None`.
- Existing call sites (default `objective="et"`) unchanged.

### D. `src/ffc_ddw_sum_et/algorithm/flip_makespan_cp/` (new package)

- `__init__.py`
- `option.py` — `FlipMakespanCpOption(AlgOption)`:
  - `cp_tl_seconds: float | None = None`
  - `solver_thread_cnt: int = 1`
  - `log_search_progress: bool = False`
- `dispatcher.py` — `FlipMakespanCpDispatcher`:
  - `algorithm_id = "flip_makespan_cp"`
  - `run(spec)`:
    1. `incumbent = spec.ref_solution` (None ⇒ `RuntimeError`).
    2. `init_sched = incumbent.deepcopy()`;
       `init_sched.delay_job_latest_leq_obj_contrib(instance.job_2_dw_ub_map)`.
    3. `delayed_makespan = init_sched.makespan`.
    4. `reversed_instance = FFcDDWParameters.reverse_stages(instance)`.
    5. Build `flipped_seed: FFcSchedule` on `reversed_instance`: every
       `(stage, mc, j, s, e)` operation in `init_sched` becomes
       `(stage, mc, j, delayed_makespan - e, delayed_makespan - s)`.
       (Stage ids are reused — `reverse_stages` keeps the same set with
       reversed order; `make_params` and `as_reversed` already operate on
       this assumption.)
    6. If `len(reversed_instance.stage_id_list) > 1`:
       `flipped_seed.make_semi_active(reversed_instance.stage_2_job_2_p_map,
       start_from_stage=reversed_instance.stage_id_list[1])`. Compacts
       stages 2..C only; the fixed first stage is left untouched.
    7. `cp_horizon = int(flipped_seed.makespan)` after compaction
       (`≤ delayed_makespan`, strictly less when there were inter-stage
       idle gaps in the original schedule).
    8. `BaseModelBuilder.build(reversed_instance, horizon=cp_horizon, objective="makespan")`.
    9. `add_start_time_freezed_operation_constraints` on the flipped
       first stage (`reversed_instance.stage_id_list[0]`).
   10. `apply_start_hints_from_start_time_map` + `apply_end_hints_from_end_time_map`
       from `flipped_seed` (full schedule, post-compaction).
       E/T hints skipped (no E/T vars).
   11. `CpsatSolverOptions(max_time_in_seconds=cp_tl_seconds, num_workers=…,
       log_search_progress=…)` → `get_solver`.
   12. `ObjectiveValueRecorder` + `ObjectiveBoundRecorder` callbacks
       (mirror `CpsatAdapter`). Solve.
   13. If `log_search_progress`: dump `response_proto.solve_log` via the
       spec-supplied path getter (controller exposes it indirectly through
       `solver_log_path_getter` from the option).
   14. On feasible: build `flipped_full = build_schedule_from_op_starts(reversed_instance, …)`
       → `unflipped = flipped_full.as_reversed()` →
       `unflipped.make_semi_active(instance.stage_2_job_2_p_map)` →
       `unflipped.insert_idle_time(due, ewt, twt)`.
   15. Recompute ET → `AlgRecord(work_status=…, result=AlgResult(schedule=unflipped, …),
       progress_log=…, termination_reason=…)`.
   16. On infeasible/unknown: `AlgRecord` with `result.schedule=None`.

### E. `src/ffc_ddw_sum_et/orchestration/controller.py`

- New step `run_flip_makespan_cp_from_incumbent`:
  - Args: `cp_tl: float | str | None = None`, `solver_thread_cnt: int = 1`,
    `log_search_progress: bool = False`.
  - Resolve `cp_tl` → strict-min with `timer.get_remaining_sec(...)`.
  - Pull incumbent (RuntimeError if None).
  - Build `FlipMakespanCpOption`, `AlgSpec(instance, option, ref_solution=…,
    logger, stop_predicate)`.
  - `record = FlipMakespanCpDispatcher().run(spec)`.
  - Capture `elapsed = monotonic - start_elapsed` immediately, then
    `_register(report, FFcDDWSolution(...) | None,
    progress_log=record.progress_log or ())`.
  - Pass `solver_log_path_getter=self.get_file_path_for_subroutine` to
    the dispatcher (or surface it via the option).

### F. `metadata/20260507/flip_makespan_cp_debug.yaml`

- 2 instances (`ins_index: [0, 1]`), `solver_thread_cnt=8`, prior step
  `calc_mcf_lb_and_derive_full_sch` then `run_flip_makespan_cp_from_incumbent`.

### G. `docs/algorithms/20260507_flip_makespan_cp.md`

- Algorithm description + Phase-3 contrast + diagrams.

### H. Tests

- `tests/algorithm/test_horizon.py` — unit test for the helper (incl.
  `d_lower=None` and the parity with `parallel_mc_pmtn` semantics).
- `tests/algorithm/test_cumulative_makespan_objective.py` — single-stage
  toy, `objective="makespan"` returns None for et_vars and minimises
  makespan.
- `tests/algorithm/test_flip_makespan_cp.py` — small instance, give a
  feasible incumbent, dispatcher returns a feasible schedule;
  ref_solution=None ⇒ RuntimeError.

### I. Verification

- `uv run ruff check`, `uv run ruff format`.
- `uv run pytest tests/algorithm/...` (new tests + adjacent suites).

## Out of scope (deferred)

- Profile-fix arcs on stages 2..C of the flipped CP (would need a
  separate analysis — for now hints alone).
- `repeat_while_improving` loop.
- `horizon_multiplier` knob.
- Per-phase YAML Gantt emission (compact JSON via `obj_log_json` only).
- `algorithm.flip_makespan_cp` re-export from `algorithm/__init__.py`
  (deferred per CLAUDE.md until package-init circular import is
  resolved).
