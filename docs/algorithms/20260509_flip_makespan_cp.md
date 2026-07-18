# `run_flip_makespan_cp_from_incumbent`

A controller step that takes the current incumbent, time-flips it onto a
stage-reversed instance, fixes the (now-first) reverse-stage to the
incumbent's right-shifted last stage, hints the rest of the schedule from
the incumbent, and minimises makespan with CP-SAT. The result is
re-flipped and post-processed with `make_semi_active` +
`insert_idle_time` on the original instance.

## Pre-conditions

- An incumbent must be registered on `solution_manager` before this step
  runs. The step is intended to follow `calc_mcf_lb_and_derive_full_sch`
  (or any other seeder).

## Algorithm

Let `T` be the right-shifted incumbent's makespan (`delayed_makespan`).

1. **Right-shift** the incumbent's last stage via
   `FFcSchedule.delay_job_latest_leq_obj_contrib(job_2_dw_ub_map)`. Tardy
   ops stay; non-tardy ops are pushed up to `min(d_plus, next_op_new_start)`.
   Tardiness cannot increase; earliness can only decrease.
2. **Stage-reverse** the instance via `FFcDDWParameters.reverse_stages`.
3. Build a **flipped seed** schedule on the reversed instance. Every
   operation `(stage, mc, job, s, e)` on the right-shifted incumbent
   becomes `(stage, mc, job, T - e, T - s)` on the seed. Per-machine
   non-overlap is preserved.
4. **Compact** stages 2..C of the flipped seed via
   `FFcSchedule.make_semi_active(reversed.stage_2_job_2_p_map,
   start_from_stage=reversed.stage_id_list[1])`. The fixed first stage
   is left untouched, so the right-shifted incumbent's last-stage
   layout (= the CP fix positions) is preserved. Stages 2..C are
   left-shifted in flipped time, removing idle gaps and shrinking the
   seed's makespan from `T` to `T_compact ≤ T`.
5. Build a **base CP-SAT model** on the reversed instance with
   `objective='makespan'` (no E/T variables). The horizon is
   `T_compact` -- a feasible upper bound on the optimal flipped
   makespan, tighter than `T` whenever the seed had idle gaps on
   stages 2..C.
6. **Fix** the model's first stage (in flipped order, i.e. the reversed
   instance's stage 1) to the seed's start times via
   `add_start_time_freezed_operation_constraints`.
7. **Hint** every operation start/end from the compacted seed (full
   schedule). Precedence holds in flipped time because
   `make_semi_active` enforces `prev_stage_end ≤ next_stage_start`
   per job and per-machine non-overlap.
8. **Solve** CP-SAT under the option's time cap. Hint coverage is visible
   in the CP-SAT search log when `log_search_progress=True`.
9. **Reconstruct** the flipped schedule (`build_schedule_from_op_starts`),
   call `as_reversed()` to flip back to original time, then
   `make_semi_active(stage_2_job_2_p_map)` and
   `insert_idle_time(due_window_map, ewt_map, twt_map)`. Mirrors Phase 3.

## Contrast with Phase 3 of `run_mcf_lb_4`

`reverse_dispatch_full_schedule` (Phase 3) fills stages 2..C of the
reversed instance with `MixedDispatcher` (a greedy heuristic) starting
from the same kind of right-shifted, time-flipped seed.

This step replaces the heuristic dispatcher with a CP-SAT solve that:

- gets the seed as fixed constraints (not just a starting point), and
- gets *every* operation hinted (not only the seed stage), so the solver
  can verify and improve the incumbent's structure rather than build one
  from scratch.

## Failure modes

- `incumbent is None` ⇒ `RuntimeError`.
- CP-SAT returns `INFEASIBLE` / `UNKNOWN` ⇒ no new solution registered;
  the existing incumbent is preserved (`solution_manager` ignores the
  None payload). A warning is logged.

## Phase schedule emission

When `emit_phase_schedules=true` is passed to the controller step, the
dispatcher writes seven intermediate schedules as **compact JSON**
(single-line, tight separators). Files are registered through the
`flip_makespan_cp_phase_schedule` artifact kind
(`{instance_name}_{phase_name}.json` in the instance's `progress/`
zone), so the post-run reporter discovers them via `find_artifacts`
and renders one PNG per phase under `phase_gantt_png`. The 2-digit
prefix in `phase_name` orders files naturally on disk:

| `phase_name`                  | Schedule snapshot                                                       |
| ----------------------------- | ----------------------------------------------------------------------- |
| `01_incumbent`                | Input (`spec.ref_solution`), original instance                          |
| `02_right_shifted`            | After step 1 (right-shifted incumbent, original)                        |
| `03_flipped`                  | After step 3 (time-flipped seed, reversed instance, pre-compaction)     |
| `04_flipped_compacted`        | After step 4 (`make_semi_active` on stages 2..C, reversed instance)     |
| `05_cp_solved`                | CP-SAT result before `as_reversed` (reversed instance)                  |
| `06_unflipped_semi_active`    | After `as_reversed` + `make_semi_active` (original instance)            |
| `07_unflipped_final`          | After `insert_idle_time` (= the registered solution)                    |

The final phase (`07_unflipped_final`) and the canonical `solution_json`
artifact carry the same schedule; both are kept so the phase trace can
be inspected as a self-contained sequence without cross-referencing.
JSON shape mirrors `dump_solution_json`'s standard form, so the
existing `_render_gantt_from_solution_json` renderer is reused for
phase PNGs (no JSON-specific renderer needed).

## Out of scope (deferred)

- Profile-fix arcs on stages 2..C of the flipped CP (hints only for now).
- `repeat_while_improving` loop.

## Related

- Implementation: `src/ffc_ddw_sum_et/algorithm/flip_makespan_cp/`
- Controller wiring: `FFcDDWSubroutineController.run_flip_makespan_cp_from_incumbent`
- Plan: `plans/experiment/20260507/flip_makespan_cp_from_incumbent.md`
- Sample experiment: `metadata/20260507/flip_makespan_cp_debug.yaml`
- Phase 3 baseline: `src/ffc_ddw_sum_et/algorithm/mcf_lb/phase3_dispatch.py`
