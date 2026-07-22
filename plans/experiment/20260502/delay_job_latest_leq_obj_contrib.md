# Plan: `FFcSchedule.delay_job_latest_leq_obj_contrib`

## Goal

Add an in-place last-stage retiming method to `FFcSchedule` that delays
each operation to the latest end time which does not increase the per-job
objective contribution.

## Method spec (from user)

- Adjusts only the **last stage** of the schedule.
- Processes operations in order of **latest end time first**.
- For each operation on its current machine:
  - target end = `min(d_plus[j], next_op_start_time)` if a next operation
    exists on the same machine,
  - target end = `d_plus[j]` otherwise.
- The operation's end time is moved to that target.

## Interpretation / edge cases

- "Delay" only — never move an operation earlier. Concretely:
  `new_end = max(old_end, target_end)`. Reason:
  - The previous operation on the same machine has not been retimed yet,
    so moving the current operation earlier could violate machine
    capacity.
  - A job whose current `C_j > d_plus[j]` is already tardy; pulling its
    `C_j` back to `d_plus[j]` would *reduce* tardiness but would also
    move it to a position that is potentially infeasible w.r.t. the
    previous-stage end time. The user's name "...leq_obj_contrib"
    confirms the intent: the *latest* end time at which the obj
    contribution does not exceed today's contribution.
- New start = `new_end - duration` where `duration = old_end - old_start`.
  Since we only delay, `new_start >= old_start`, so:
  - precedence with the previous stage is preserved (`old_start` was
    already feasible),
  - no overlap with the previous op on the machine
    (`old_end[i-1] <= old_start[i] <= new_start[i]`).
- Iteration on each machine goes from the last (latest) op back to the
  first; "next op start" uses the *retimed* start of `i+1` (already
  processed). Across machines we process each machine independently —
  there is no cross-machine dependency in the rule.

## Signature

```python
def delay_job_latest_leq_obj_contrib(
    self,
    due_window_map: Mapping[JobIdType, tuple[int, int]],
) -> None:
```

Use the same `due_window_map` shape as `insert_idle_time` for consistency
(`job_id -> (d_minus, d_plus)`). Only `d_plus` is read.

## Sketch

```
last_stage = self.stages[-1]
for mc in self.machines_per_stage[last_stage]:
    seq = self.__stage_2_mc_2_job_tuple_seq[last_stage][mc]
    if not seq: continue
    next_start = None
    new_seq_rev = []
    for job_id, old_start, old_end in reversed(seq):
        duration = old_end - old_start
        d_plus = due_window_map[job_id][1]
        target = d_plus if next_start is None else min(d_plus, next_start)
        new_end = max(old_end, target)
        new_start = new_end - duration
        new_seq_rev.append((job_id, new_start, new_end))
        next_start = new_start
    self.__stage_2_mc_2_job_tuple_seq[last_stage][mc] = list(reversed(new_seq_rev))
self._rebuild_stage_time_caches(last_stage)
```

## Placement

Insert directly above `insert_idle_time` (both are last-stage retimers).

## Out of scope

- Call-site integration (which dispatcher/algorithm uses it) — separate task.
- Tests — none requested. Existing parity tests target other methods.
