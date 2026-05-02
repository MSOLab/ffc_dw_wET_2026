# Plan: delay last-stage operations before reverse-flip in Phase 3

## Goal

In `reverse_dispatch_full_schedule`, when `instance.stage_count > 1`,
push last-stage operations as late as possible (without increasing per-job
objective contribution) **before** computing `last_stage_end_map` and
flipping. Expose the delayed schedule on `Phase3State` and let the
controller register it for the reporter so the Gantt chart is rendered
when `draw_gantt=True`.

## Files

1. `src/ffc_ddw_sum_et/algorithm/mcf_lb/phase3_dispatch.py`
2. `src/ffc_ddw_sum_et/orchestration/controller.py` (two call-sites)

## Changes

### 1. `phase3_dispatch.py`

- Add field to `Phase3State`:
  ```python
  last_stage_only_schedule_delayed: FFcSchedule | None = None
  ```
  Placed next to the other reversed-instance intermediates (above
  `last_stage_only_schedule_flipped`).

- In `reverse_dispatch_full_schedule`, multi-stage branch, before the
  `last_stage_end_map` loop:
  ```python
  last_stage_only_schedule_delayed = last_stage_only_schedule.deepcopy()
  last_stage_only_schedule_delayed.delay_job_latest_leq_obj_contrib(
      instance.job_2_dw_ub_map
  )
  delayed_makespan = last_stage_only_schedule_delayed.makespan
  ```
  Then iterate `last_stage_only_schedule_delayed` for `last_stage_end_map`,
  feed it to the flip loop, and use `delayed_makespan` (not the input
  parameter `last_stage_only_makespan`) as the flip horizon.

  - Rationale: delays only push end times forward, so the new max-end on
    the last stage is `>= last_stage_only_schedule.makespan`. Using the
    old horizon would produce negative flipped start times for the
    delayed last op (which now ends at `delayed_makespan > old_makespan`).
  - The `last_stage_only_makespan` parameter becomes unused for the
    multi-stage path. Keep the parameter for API stability — it's still
    documented and the single-stage short-circuit doesn't touch it.

- Pass the delayed schedule into `Phase3State(...)` at the bottom.

- Single-stage branch unchanged; `last_stage_only_schedule_delayed`
  stays `None`.

### 2. `controller.py`

Two call-sites build `mcf_lb_phase_schedules`. Both follow the same
pattern: append the flipped / before-unflipping schedules guarded by
`is not None`. We slot the delayed entry just before the flipped one,
named `3_last_stage_only_schedule_delayed` (sorts after
`3_last_stage_only_schedule_chosen`, before `4_last_stage_only_schedule_flipped`).

- `build_full_sch_from_last_stage_only_sch` (~line 736): add
  ```python
  if state.last_stage_only_schedule_delayed is not None:
      self.mcf_lb_phase_schedules.append(
          ("3_last_stage_only_schedule_delayed",
           state.last_stage_only_schedule_delayed)
      )
  ```
  Just before the existing `4_last_stage_only_schedule_flipped` append.

- `run_mcf_lb_4` (~line 962): same insertion just before the existing
  `4_last_stage_only_schedule_flipped` append.

Gantt rendering itself is gated by `Reporter.draw_gantt` — adding the
schedule to `mcf_lb_phase_schedules` is sufficient; no other reporter
changes needed.

## Out of scope

- `MCFLBResult` (side-car for `MCFLB.run`) does not currently flow
  through Phase 3 here — leave untouched.
- Tests — none requested. The existing Phase-3 logic doesn't have
  dedicated unit tests in `tests/`.

## Verification

- `uv run ruff check` on the touched files.
- Smoke-run the Phase-3 path on a small multi-stage instance to confirm
  the delayed schedule is populated and the dispatched objective is
  not worse than baseline.
