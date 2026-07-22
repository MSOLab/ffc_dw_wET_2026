# Plan: `p_increment` option for `apply_lb_by_mcf` / `single_pass_last_stage_only_sch_from_mcf_lb`

## Context

Today both step methods solve their respective sub-problems with the
instance's *original* last-stage processing times. We want to study what
happens when every last-stage operation's processing time is inflated by
a constant `p_increment ≥ 1`: the MCF preemptive relaxation and the
profile-fix CP-SAT solve both work on the *augmented* problem, while the
final dispatched schedule emitted by
`build_full_sch_from_last_stage_only_sch` must still be feasible for the
*original* problem.

Two important invariants follow:

* When `p_increment != 0` the MCF lower bound is **not** a global LB on
  the original problem, so the report must declare `obj_bound = None`.
* When `single_pass_last_stage_only_sch_from_mcf_lb` ran with
  `p_increment != 0`, its stored last-stage-only schedule has start
  times computed under inflated durations. Before reverse-dispatch we
  rebuild it with original durations **keeping the same end times** and
  letting the start times slide later (`start_orig = end_aug -
  p_orig_j`, equivalently `start_aug + p_increment`). Same end times
  preserve each job's earliness/tardiness contribution and make the
  rebuild a no-op on the objective; later starts on a uniform shift of
  `+p_increment` keep the per-machine ordering and non-overlap from the
  augmented schedule. The downstream `delay_job_latest_leq_obj_contrib`
  and reverse `MixedDispatcher` then operate on a problem-feasible
  schedule.

The two new state attributes (`mcf_preemptive_sch_p_increment`,
`last_stage_only_sol_p_increment`) record the option used by each
producing method so later steps and post-run analysis can tell whether
the recorded MCF LB / last-stage-only schedule belong to the augmented
or the original problem (`None` = never run, `0` = ran on original, `≥1`
= ran on augmented).

## Files to modify

* `src/ffc_ddw_sum_et/orchestration/controller_core.py` — declare the
  two new state attributes in `_define_states` (lines 68-77).
* `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py` — add a classmethod
  helper to clone an instance with one stage's processing times
  incremented (sits next to `create_instance_of_*` helpers around line
  130-175).
* `src/ffc_ddw_sum_et/algorithm/mcf_lb/phase3_dispatch.py`
  * Add `ls_only_sch_before_delay: FFcSchedule | None = None` field to
    `Phase3State` (placed before `ls_only_sch_delayed` to reflect
    pipeline order).
  * Add `rebuild_last_stage_with_original_p: bool = False` keyword
    argument to `reverse_dispatch_full_schedule`. When `True` and
    `instance.stage_count > 1`, build the rebuilt schedule (same ends,
    starts recomputed with `instance.get_job_2_p_map_for_stage(...)`)
    *before* `delay_job_latest_leq_obj_contrib`, set it on
    `Phase3State.ls_only_sch_before_delay`, and use it as the schedule
    that gets delayed and reverse-dispatched. When `False`: current
    behaviour, field stays `None`.
  * `run_phase3` is unchanged (always passes `False`). The augmented
    flow only enters via the controller's standalone step.
* `src/ffc_ddw_sum_et/orchestration/controller.py`
  * `apply_lb_by_mcf` (lines 393-469): add `p_increment` kwarg,
    validate, swap to augmented instance when needed, set new state,
    drop `obj_bound` when augmented.
  * `single_pass_last_stage_only_sch_from_mcf_lb` (lines 578-681): same
    treatment; pass the augmented instance into the algorithm.
  * `build_full_sch_from_last_stage_only_sch` (lines 683-777): when
    `last_stage_only_sol_p_increment != 0`, set
    `rebuild_last_stage_with_original_p=True` on the
    `reverse_dispatch_full_schedule` call and append the resulting
    `state.ls_only_sch_before_delay` to `self.mcf_lb_phase_schedules`
    under the label `2_1_ls_only_sch_before_delayed`.
* `metadata/20260502/mcf_lb_init_24_config.yaml` — exercise the new
  option.

## Design choices to confirm

**Helper location** — I plan to put the augmented-instance helper as
`FFcDDWParameters.with_stage_processing_time_increment` to mirror the
existing `create_instance_of_job_subset` /
`create_instance_of_stage_subset` / `reverse_stages` classmethods. The
alternative is an inline private helper in `controller.py`. The
classmethod is cleaner and the parameters module already centralises
instance cloning logic.

**Cross-method LB validity** — `single_pass_last_stage_only_sch_from_mcf_lb`
will report `obj_bound = None` *unconditionally*, regardless of its own
`p_increment` and regardless of `mcf_preemptive_sch_p_increment`. Today
the method re-emits `mcf_lb` (set by `apply_lb_by_mcf`) as its own
`obj_bound`, which is redundant — the LB is already produced and
recorded by the prior step — and becomes misleading when either step
runs with `p_increment != 0`. Fixing this is part of this change:
`single_pass` does not produce a bound, so it should not claim one.

`apply_lb_by_mcf` keeps the per-method-own-`p_increment` rule for its
own `obj_bound`: `mcf_lb` when `p_increment == 0`, `None` otherwise.

## Implementation details

### 1. New state attributes (`controller_core.py`)

In `FFcDDWSubroutineControllerCore._define_states`:

```python
self.mcf_preemptive_sch_p_increment: int | None = None
self.last_stage_only_sol_p_increment: int | None = None
```

Both default to `None` (= never run); each producing method overwrites
on success.

### 2. Augmented-instance helper (`ffc_ddw_params.py`)

Add classmethod on `FFcDDWParameters`:

```python
@classmethod
def with_stage_processing_time_increment(
    cls,
    instance: FFcDDWParameters,
    stage_id: str,
    increment: int,
) -> Self:
    """Return a new FFcDDWParameters identical to ``instance`` except
    every job's processing time on ``stage_id`` is increased by
    ``increment`` (must be a non-negative int)."""
    if not isinstance(instance, FFcDDWParameters):
        raise TypeError(...)
    if increment < 0:
        raise ValueError(...)
    if stage_id not in instance.stage_id_list:
        raise ValueError(...)
    new_df = instance.p_manager.df.copy()
    new_df[stage_id] = new_df[stage_id] + increment
    new_p_manager = JobStageProcessingTimeManager(instance.p_manager.name, new_df)
    new_stage_2_machines_map = {
        s: list(instance.stage_2_machines_map[s]) for s in instance.stage_id_list
    }
    return cls(
        instance.name,
        list(instance.job_id_list),
        list(instance.stage_id_list),
        new_stage_2_machines_map,
        new_p_manager,
        instance.job_2_due_window_map,
        instance.job_2_ewt_map,
        instance.job_2_twt_map,
        instance.generation_params,
    )
```

This relies on `JobStageProcessingTimeManager.df` (rows = jobs, cols =
stages) — confirmed in `parameters/base/job_stage_p.py`. `increment=0`
is a no-op clone, so the controller can call this unconditionally if we
want, but we'll only call it when `p_increment != 0` to avoid the
unnecessary copy.

### 3. `apply_lb_by_mcf` (controller.py)

Signature change (keyword-only):

```python
def apply_lb_by_mcf(
    self,
    draw_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "due2-weight-pos",
    p_increment: int = 0,
) -> SubroutineReport:
```

Validation at the top of the method:

```python
if p_increment < 0:
    raise ValueError(
        f"p_increment must be 0 or a positive integer; got {p_increment}."
    )
```

Pick the instance for MCF:

```python
if p_increment == 0:
    instance_for_mcf = self.instance
else:
    last_stage_id = self.instance.stage_id_list[-1]
    instance_for_mcf = FFcDDWParameters.with_stage_processing_time_increment(
        self.instance, last_stage_id, p_increment
    )
```

Pass `instance_for_mcf` into `solve_mcf_lb` (existing call), and into
`build_signed_cost_matrix` if `draw_heatmap` is true.

After successful solve, set new state:

```python
self.mcf_preemptive_sch_p_increment = p_increment
```

Return:

```python
return SubroutineReport(
    elapsed_time=elapsed,
    obj_value=None,
    obj_bound=obj_bound_by_mcf if p_increment == 0 else None,
)
```

Logging: append `, p_increment=%d` to the existing INFO line so logs
make the choice visible.

### 4. `single_pass_last_stage_only_sch_from_mcf_lb` (controller.py)

Signature change (keyword-only at the end):

```python
def single_pass_last_stage_only_sch_from_mcf_lb(
    self,
    job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
    placement_priority: Literal["contrib", "dist"] = "contrib",
    pf_method: PFMethod | None = "PF1",
    solver_thread_cnt: int = 1,
    total_tl: float | str | None = None,
    log_cp_search_progress: bool = False,
    p_increment: int = 0,
) -> SubroutineReport:
```

Same `if p_increment < 0: raise ValueError` guard.

Construct `instance_for_solve` the same way as in `apply_lb_by_mcf`. The
existing `total_tl` resolution can stay on `self.instance` (job_count,
stage_count, last_stage_mc_count are unchanged by the increment).

Pass `instance_for_solve` into `single_pass_last_stage_only_from_mcf_lb`
in place of `self.instance`. The downstream
`solve_last_stage_with_profile_fix` reads
`instance.p_manager` / `instance.get_job_2_p_map_for_stage(...)` so it
will pick up the inflated durations naturally.

Store state and report. **Drop the `obj_bound = mcf_lb` from the
`SubroutineReport`** unconditionally — `single_pass` does not produce a
bound. Stop passing `obj_bound` into the stored `FFcDDWSolution` for the
same reason (the schedule's bound has nothing to do with what this step
proves). The MCF LB remains accessible via
`self.mcf_lb_diagnostic.mcf_lb` for any caller that wants it.

```python
self.last_stage_only_sol = FFcDDWSolution(
    schedule=result.schedule,
    obj_value=result.obj_value,
    obj_bound=None,
)
self.last_stage_only_sol_p_increment = p_increment
...
return SubroutineReport(
    elapsed_time=result.elapsed_time,
    obj_value=result.obj_value,
    obj_bound=None,
)
```

Existing test `test_single_pass_last_stage_only_sch_from_mcf_lb` (if
present) and any reporting that consumed
`last_stage_only_sol.obj_bound` need to be updated. I'll grep for
those before editing and adjust accordingly.

### 5. `reverse_dispatch_full_schedule` and `Phase3State` (`phase3_dispatch.py`)

Add field to `Phase3State` (kept ordered by pipeline position):

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Phase3State:
    full_sch_from_ls_only_sch: FFcSchedule
    dispatched_obj: float

    ls_only_sch_before_delay: FFcSchedule | None = None  # NEW
    ls_only_sch_delayed: FFcSchedule | None = None
    ls_only_sch_flipped: FFcSchedule | None = None
    full_sch_before_unflip: FFcSchedule | None = None
```

Extend `reverse_dispatch_full_schedule` with a flag:

```python
def reverse_dispatch_full_schedule(
    instance: FFcDDWParameters,
    last_stage_only_schedule: FFcSchedule,
    *,
    last_stage_id: str | None = None,
    job_2_pos: dict[str, int] | None = None,
    machine_then_job: bool = False,
    rebuild_last_stage_with_original_p: bool = False,  # NEW
    logger: logging.Logger | None = None,
) -> Phase3State | None:
```

Inside the multi-stage branch (the `else` at line 94), before computing
`ls_only_sch_delayed`, optionally rebuild:

```python
if rebuild_last_stage_with_original_p:
    p_map = instance.get_job_2_p_map_for_stage(last_stage_id)
    ls_only_sch_before_delay = FFcSchedule(
        jobs=list(instance.job_id_list),
        stages=list(instance.stage_id_list),
        machines_per_stage={
            s: list(instance.stage_2_machines_map[s])
            for s in instance.stage_id_list
        },
    )
    for mc_id, _aug_start, aug_end, job_id in (
        last_stage_only_schedule.iter_operations_on_stage(last_stage_id)
    ):
        # Same end, later start: start_orig = end_aug - p_orig_j.
        ls_only_sch_before_delay.add_ops_times_2_mc(
            stage_id=last_stage_id,
            mc_id=mc_id,
            job_id=job_id,
            start_time=aug_end - p_map[job_id],
            end_time=aug_end,
        )
    schedule_for_delay = ls_only_sch_before_delay
else:
    ls_only_sch_before_delay = None
    schedule_for_delay = last_stage_only_schedule

ls_only_sch_delayed = schedule_for_delay.deepcopy()
ls_only_sch_delayed.delay_job_latest_leq_obj_contrib(instance.job_2_dw_ub_map)
```

The rest of the function is unchanged. The single-stage branch
(`stage_count == 1`) keeps `ls_only_sch_before_delay = None`.

Final `Phase3State` construction includes the new field:

```python
return Phase3State(
    full_sch_from_ls_only_sch=full_sch_from_ls_only_sch,
    dispatched_obj=dispatched_obj,
    ls_only_sch_before_delay=ls_only_sch_before_delay,
    ls_only_sch_delayed=ls_only_sch_delayed,
    ls_only_sch_flipped=ls_only_sch_flipped,
    full_sch_before_unflip=full_sch_before_unflip,
)
```

Why this is safe and why it preserves the objective:

* Per-machine ordering: under uniform `p_increment` every kept op
  shifts its start by `+p_increment` (`start_orig = start_aug +
  p_increment`), so the start-sorted order on each machine is
  unchanged.
* Non-overlap: between consecutive ops `i`, `i+1` on a machine,
  `end_aug_i ≤ start_aug_{i+1}` (augmented schedule). After rebuild,
  `end_orig_i = end_aug_i ≤ start_aug_{i+1} ≤ start_orig_{i+1}`, so
  `add_ops_times_2_mc`'s overlap check passes.
* Release feasibility: `start_orig_i = start_aug_i + p_increment ≥
  start_aug_i ≥ release_j`, so upstream-stage processing constraints
  remain satisfied.
* Objective preservation: weighted earliness/tardiness depends only on
  last-stage end times vs due windows — both unchanged. So the rebuild
  is objective-neutral, and downstream `delay_job_latest_leq_obj_contrib`
  / reverse-dispatch see a feasible original-problem schedule with the
  same per-job ET contribution as the augmented solve produced.

`run_phase3` (the wrapper used by `run_mcf_lb_4`) keeps calling
`reverse_dispatch_full_schedule` with the default
`rebuild_last_stage_with_original_p=False` — the augmented flow is
exposed only through the controller's standalone step at this stage.

### 6. `build_full_sch_from_last_stage_only_sch` (controller.py)

No new keyword arg. Branch on the stored increment when calling
`reverse_dispatch_full_schedule`, then append the new phase entry
when populated:

```python
ls_p_inc = self.last_stage_only_sol_p_increment
state = reverse_dispatch_full_schedule(
    self.instance,
    self.last_stage_only_sol.schedule,
    machine_then_job=machine_then_job,
    logger=self.logger,
    rebuild_last_stage_with_original_p=(
        ls_p_inc is not None and ls_p_inc != 0
    ),
)
...
if state.ls_only_sch_before_delay is not None:
    self.mcf_lb_phase_schedules.append(
        ("2_1_ls_only_sch_before_delayed", state.ls_only_sch_before_delay)
    )
if state.ls_only_sch_delayed is not None:
    self.mcf_lb_phase_schedules.append(
        ("3_ls_only_sch_delayed", state.ls_only_sch_delayed)
    )
# ... (rest of the existing appends unchanged)
```

The `2_1_` prefix slots the rebuilt schedule between
`2_ls_only_sch_from_mcf_lb` (single_pass output, under inflated
durations) and `3_ls_only_sch_delayed` (delay applied to the rebuilt
schedule), keeping the existing reporter Gantt sort order intact.

`reverse_dispatch_full_schedule` then runs
`delay_job_latest_leq_obj_contrib` on the (rebuilt or original) input
and proceeds with reverse-dispatch + unflip exactly as today; the
resulting full schedule is feasible for the original instance and the
dispatched objective registered on `solution_manager` is the
original-problem objective.

`last_stage_only_sol_p_increment` is not part of the precondition
check — when `last_stage_only_sol` is `None`, the existing `ValueError`
already fires; reading the increment after that point is therefore
safe.

### 7. YAML config — `metadata/20260502/mcf_lb_init_24_config.yaml`

Add scenarios that exercise the option. Concrete shape:

```yaml
scenarios:
  - name: build_full_sch_p_inc_0
    timelimit: 300.0
    output_subdir: build_full_sch_p_inc_0
    subroutine_flow:
      - method: apply_lb_by_mcf
        draw_heatmap: false
        heatmap_sort: "end_time"
        p_increment: 0
      - method: single_pass_last_stage_only_sch_from_mcf_lb
        job_priority: "end_time"
        placement_priority: "dist"
        pf_method: "PF1"
        solver_thread_cnt: 1
        total_tl: 0.01nc
        log_cp_search_progress: false
        p_increment: 0
      - method: build_full_sch_from_last_stage_only_sch
  - name: build_full_sch_p_inc_5
    timelimit: 300.0
    output_subdir: build_full_sch_p_inc_5
    subroutine_flow:
      - method: apply_lb_by_mcf
        draw_heatmap: false
        heatmap_sort: "end_time"
        p_increment: 5
      - method: single_pass_last_stage_only_sch_from_mcf_lb
        job_priority: "end_time"
        placement_priority: "dist"
        pf_method: "PF1"
        solver_thread_cnt: 1
        total_tl: 0.01nc
        log_cp_search_progress: false
        p_increment: 5
      - method: build_full_sch_from_last_stage_only_sch
```

Two scenarios let us compare augmented vs original behaviour on the same
instance set.

## Verification

1. `uv run ruff check` — passes after the edit.
2. `uv run ruff format` — apply if formatting changed.
3. Existing test
   `tests/orchestration/test_controller.py::test_build_full_sch_from_last_stage_only_sch`
   should still pass (default `p_increment=0` keeps current behaviour).
4. Run the configured scenarios:
   `uv run python main.py` (with the updated
   `metadata/20260502/mcf_lb_init_24_config.yaml`).
   * Scenario `build_full_sch_p_inc_0` — outputs identical to the prior
     run.
   * Scenario `build_full_sch_p_inc_5` — confirms:
     - MCF LB and last-stage-only obj are computed under inflated
       durations,
     - `build_full_sch_from_last_stage_only_sch` still produces a
       feasible original-problem schedule (every job present at every
       stage, no overlaps, dispatched obj equals
       `compute_weighted_earliness_tardiness` on the final schedule),
     - The final SubroutineReport for the rebuild step is valid
       (`obj_value` is the original-problem weighted ET).
5. Spot-check from the run output that `mcf_preemptive_sch_p_increment`
   and `last_stage_only_sol_p_increment` reach the runner where
   needed (they are read by reporting via `getattr(controller, ...)`
   patterns; if the runner needs them surfaced we'll plumb on demand,
   but the per-step `SubroutineReport` already encodes the intent via
   `obj_bound = None`).
