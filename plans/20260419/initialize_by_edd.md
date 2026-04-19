# Plan: `initialize_by_edd` step method on `FFcDDWSubroutineController`

## Context

`FFcDDWSubroutineController` already has two seeding step methods: `run_fam`
(dispatching by the instance's native job order via `FAMDispatcher`) and
`run_mcf_lb` (dispatching by MCF last-stage start-time order via
`MixedDispatcher`). We want a third seeding path: EDD — dispatch jobs in
ascending order of their due-date-window *upper* bound `d^+_j`. Because the
FFcDDW problem uses a due-date window `[d^-_j, d^+_j]` rather than a single
due date, "earliest due date" is interpreted as the latest on-time moment
(`d^+`), so tight deadlines go first while slack jobs drop to the tail.

No analytical lower bound falls out of this procedure, so `obj_bound` is
`None`. The dispatching engine is shared with `run_mcf_lb`
(`MixedDispatcher.get_best_mixed_schedule_by_sequence`), which means the same
`criteria` knob (`"weighted_et"` vs `"makespan"`) is exposed for parity.

## Critical file to modify

- `src/ffc_ddw_sum_et/orchestration/controller.py` — add one new step method
  `initialize_by_edd` between `run_mcf_lb` and `run_profile_fixed_ns`.

## Method signature

```python
def initialize_by_edd(
    self, criteria: Literal["weighted_et", "makespan"] = "weighted_et"
) -> SubroutineReport: ...
```

Rationale:

- Returns `SubroutineReport` to stay compatible with the routix step-method
  contract (same as `run_fam` / `run_mcf_lb`).
- `criteria` mirrors `run_mcf_lb` so callers can swap the dispatcher
  selection rule without rewriting the flow.

## Implementation outline

```python
start_elapsed = self.timer.elapsed_sec

due_window_map = self.instance.job_2_due_window_map
job_2_pos = {j: i for i, j in enumerate(self.instance.job_id_list)}
# EDD on windows → sort by d^+ ascending; ties break by native order
# for determinism (same pattern as run_mcf_lb).
job_sequence = sorted(
    self.instance.job_id_list,
    key=lambda j: (due_window_map[j][1], job_2_pos[j]),
)

dispatcher = MixedDispatcher(self.instance)
schedule = dispatcher.get_best_mixed_schedule_by_sequence(
    job_sequence, criteria=criteria
)
if schedule is None:
    raise RuntimeError(
        f"MixedDispatcher produced no schedule for {self.instance.name}"
    )

sum_e, sum_t = compute_window_et(schedule, self.instance)
obj_value = float(sum_e + sum_t)

elapsed = self.timer.elapsed_sec - start_elapsed
report = SubroutineReport(
    elapsed_time=elapsed,
    obj_value=obj_value,
    obj_bound=None,
)
self.solution_manager.register(
    report,
    FFcDDWSolution(schedule=schedule, obj_value=obj_value, obj_bound=None),
)
return report
```

Reused symbols (already imported at the top of `controller.py`):
`MixedDispatcher`, `compute_window_et`, `SubroutineReport`, `FFcDDWSolution`,
`Literal`. No new imports required.

## Design choices

1. **Name.** `initialize_by_edd` per the user's request (not `run_edd`).
2. **Window bound choice.** Sort by `d^+` (window upper bound) per the user's
   description ("window이니까 d^+ (큰 값) 사용").
3. **Tie-break.** Ties on `d^+` resolve by native `job_id_list` index — same
   deterministic rule as `run_mcf_lb`.
4. **`obj_bound`.** `None`. EDD dispatch does not yield a valid LB.

## Verification

1. **Static:** `uv run ruff check` and `uv run ruff format`.
2. **End-to-end:** `uv run python main.py` on the config where
   `initialize_by_edd` is wired in; confirm the run completes, an incumbent
   schedule is produced, and the summary CSV / `*_schedule.yaml` /
   `*_gantt.png` artifacts appear.
