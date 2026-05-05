# Full schedule from MCF LB — step chain

Three chained controller step methods that together yield a full feasible
schedule seeded from an MCF preemptive lower bound, with optional
makespan-gap-driven tightening of the release and processing-time parameters.

Defined at
[controller.py](../../src/ffc_ddw_sum_et/orchestration/controller.py).

## Step chain overview

```text
apply_lb_by_mcf
    → self.mcf_preemptive_schedule (LB computed, no full incumbent)
        ↓
heuristic_last_stage_only_sch_from_mcf_lb
    → self.last_stage_only_sol (last-stage-only schedule, no full incumbent)
        ↓
build_full_sch_from_last_stage_only_sch
    → full incumbent registered on self.solution_manager
```

The five tightening boolean flags appear on the first two steps only.
`build_full_sch_from_last_stage_only_sch` has no tightening knobs — it
consumes whatever `last_stage_only_sol_p_increment` was recorded by Step 2.

---

## Tightening flags

### Reference-schedule families

All five flags are grouped into two mutually exclusive families.
Using both families in a single call raises `ValueError`.

| Family suffix | Reference makespan source | Pre-condition |
| --- | --- | --- |
| `_last_stage_only_pmtn_sch` | `self.mcf_preemptive_schedule.makespan` | prior `apply_lb_by_mcf` |
| `_last_stage_only_sch` | `self.last_stage_only_sol.schedule.makespan` | prior step setting `last_stage_only_sol` |

Both families read the current full incumbent from `self.solution_manager`:

```text
makespan_delta = max(incumbent_makespan - reference_makespan, 0)
```

### `adjust_p_by_full_sch_and_last_stage_only_pmtn_sch` / `adjust_p_by_full_sch_and_last_stage_only_sch`

Adds a computed `p_adjust` to `effective_p_increment`:

```text
p_adjust        = ceil(makespan_delta × m_last / n)
effective_p_inc = p_increment + p_adjust
```

`n = instance.job_count`, `m_last = instance.last_stage_mc_count`.

Interpretation: the full-schedule makespan exceeds the reference last-stage
makespan by `makespan_delta`. Distributing that gap uniformly over `n` jobs
and `m_last` machines gives a per-job augmentation that pushes the MCF solve
or heuristic placement into a tighter time window, raising the LB or
compressing the last-stage-only schedule.

**LB validity:** any nonzero `effective_p_increment` makes the MCF objective
not a global LB on the original instance. `SubroutineReport.obj_bound` is
set to `None` by `apply_lb_by_mcf` in that case.

### `adjust_r_by_full_sch_and_last_stage_only_pmtn_sch` / `adjust_r_by_full_sch_and_last_stage_only_sch`

Adds a computed `r_adjust` to `effective_r_increment`:

```text
r_adjust        = makespan_delta               # default
r_adjust        = ceil(makespan_delta / 2)     # when adjust_r_by_half=True
effective_r_inc = r_increment + r_adjust
```

Every per-job release time used by the MCF solve or heuristic placement is
shifted right by `r_adjust`. This tightens the feasible window without
inflating processing times.

**LB validity:** any positive `effective_r_increment` also disqualifies the
MCF objective as a global LB (`obj_bound = None`).

### `adjust_r_by_half`

Modifier for `adjust_r_by_full_sch_and_*` flags only. Halves `r_adjust`
(ceiling division). Has no effect when neither `adjust_r_*` flag is active.

---

## Step 1 — `apply_lb_by_mcf`

### Signature (tightening flags)

```python
def apply_lb_by_mcf(
    self,
    p_increment: int = 0,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
    adjust_p_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
    adjust_r_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
    adjust_p_by_full_sch_and_last_stage_only_sch: bool = False,
    adjust_r_by_full_sch_and_last_stage_only_sch: bool = False,
    adjust_r_by_half: bool = False,
) -> SubroutineReport: ...
```

### Pre-conditions

- Any `adjust_*` flag requires a full incumbent on `self.solution_manager`.
- `adjust_*_by_full_sch_and_last_stage_only_pmtn_sch` additionally requires
  `self.mcf_preemptive_schedule` set by a prior `apply_lb_by_mcf` call.
- `adjust_*_by_full_sch_and_last_stage_only_sch` additionally requires
  `self.last_stage_only_sol.schedule` set by a prior heuristic/CP step.

### Execution flow

1. Validate flags — raise `ValueError` if both families are combined.
2. Compute `effective_p_increment` and `effective_r_increment` from the
   tightening flags (see formulas above).
3. Build augmented instance — when `effective_p_increment != 0`, construct
   `FFcDDWParameters.with_stage_processing_time_increment(instance, last_stage_id, effective_p_inc)`.
4. Solve MCF — `solve_mcf_lb(instance_for_mcf, diag, r_multiplier=…, r_increment=effective_r_inc)`.
5. Store state:
   - `self.mcf_preemptive_schedule = mcf_result.mcf_preemptive_schedule`
   - `self.mcf_lb_diagnostic = diag` (records `mcf_lb`, timing, adjust params)
   - `self.mcf_lb_phase_schedules` reset to `[("1_mcf_preemptive_sch", …)]`.
6. Return `SubroutineReport(obj_value=None, obj_bound=mcf_lb if valid else None)`.

No full schedule is produced; no incumbent is registered.

### LB validity rule

```text
obj_bound is valid  iff  effective_p_increment == 0
                    and  r_multiplier <= 1.0
                    and  effective_r_increment == 0
```

---

## Step 2 — `heuristic_last_stage_only_sch_from_mcf_lb`

### Signature (tightening flags)

```python
def heuristic_last_stage_only_sch_from_mcf_lb(
    self,
    job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
    placement_priority: Literal["contrib", "dist"] = "contrib",
    p_increment: int = 0,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
    adjust_p_by_full_sch_and_last_stage_only_sch: bool = False,
    adjust_r_by_full_sch_and_last_stage_only_sch: bool = False,
    adjust_p_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
    adjust_r_by_full_sch_and_last_stage_only_pmtn_sch: bool = False,
    adjust_r_by_half: bool = False,
) -> SubroutineReport: ...
```

### Pre-conditions

- `self.mcf_preemptive_schedule` — set by Step 1.
- `self.mcf_lb_diagnostic` — set by Step 1.
- `adjust_*_by_full_sch_and_last_stage_only_pmtn_sch` additionally requires a
  full incumbent (mcf_preemptive_schedule presence is already guaranteed by the
  method-level precondition above).
- `adjust_*_by_full_sch_and_last_stage_only_sch` additionally requires a full
  incumbent and `self.last_stage_only_sol.schedule`.

### Execution flow

1. Validate flags — same mutual-exclusion check as Step 1.
2. Compute `effective_p_increment` and `effective_r_increment` — identical
   formulas.
3. Build augmented instance — when `effective_p_increment != 0`, inflate
   last-stage processing times.
4. Heuristic placement — `heuristic_last_stage_only_from_mcf_lb(instance_for_solve, self.mcf_preemptive_schedule, …)`:
   - Midpoint warm-start: place each job at its MCF preemptive midpoint on the
     last stage (sorted by `job_priority`; see `PmPrmpSortKey`).
   - Refinement: `make_semi_active` (left-shift with upstream release times)
     then `insert_idle_time`.
   - No CP-SAT solve — deterministic only.
5. Store state:
   - `self.last_stage_only_sol = FFcDDWSolution(schedule=result.schedule, …)`
   - `self.last_stage_only_sol_p_increment = effective_p_increment`
   - Appends `"2_ls_only_sch_from_mcf_lb_heur"` to `mcf_lb_phase_schedules`.
6. Return `SubroutineReport(obj_value=last_stage_ET, obj_bound=None)`.

No full schedule; no full incumbent registered.

---

## Step 3 — `build_full_sch_from_last_stage_only_sch`

### Signature

```python
def build_full_sch_from_last_stage_only_sch(
    self,
    machine_then_job: bool = False,
) -> SubroutineReport: ...
```

### Pre-conditions

- `self.last_stage_only_sol` — set by Step 2 or any compatible step
  (`single_pass_last_stage_only_sch_from_mcf_lb`,
  `neh_cp_last_stage_only_sch_from_mcf_lb`,
  `run_last_stage_cp_sat_lb`, `run_mcf_lb_4`).

### Execution flow

Calls `reverse_dispatch_full_schedule` from
[phase3_dispatch.py](../../src/ffc_ddw_sum_et/algorithm/mcf_lb/phase3_dispatch.py).

**(a) p_increment rebuild** — when `self.last_stage_only_sol_p_increment != 0`,
the last-stage schedule was built under inflated durations. Each operation's
end time is kept and its start is recomputed as `end - p_orig_j`, restoring
original processing times before reverse-dispatch. The rebuilt schedule is
stored as `"2_1_ls_only_sch_before_delayed"`.

**(b) Single-stage short-circuit** — when `instance.stage_count == 1`, the
last-stage-only schedule IS the full schedule. Phases c–f are skipped.

**(c) Delay** → `"3_ls_only_sch_delayed"` — `FFcSchedule.delay_job_latest_leq_obj_contrib`
pushes each last-stage operation as late as possible without increasing its
per-job ET contribution. The delayed makespan becomes the flip horizon.

**(d) Flip** → `"4_ls_only_sch_flipped"` — mirror each operation into the
reversed instance: `start_rev = horizon - end`, `end_rev = horizon - start`.

**(e) Reverse dispatch** — `MixedDispatcher(reversed_instance)` fills the
remaining stages using the reversed job sequence (sorted descending by delayed
last-stage end time, tie-broken by native position).

**(f) Unflip** → `"5_full_sch_before_unflip"` → `"6_full_sch_from_ls_only_sch"` —
`reversed_full.as_reversed()` maps back to forward stage order.
`make_semi_active` + `insert_idle_time` are applied to land operations at
ET-optimal positions.

### Store state

- Appends intermediate Gantt frames to `self.mcf_lb_phase_schedules`.
- Registers the full schedule as the incumbent:
  `self.solution_manager.register(report, FFcDDWSolution(…))`.

**Return** `SubroutineReport(obj_value=dispatched_ET, obj_bound=0.0)`.

`obj_bound=0.0` is a sentinel — this step does not compute a bound. Chain with
`apply_lb_by_mcf` earlier in the flow when a meaningful LB is needed.

---

## Controller state flow

| Attribute | Written by | Read by |
| --- | --- | --- |
| `mcf_preemptive_schedule` | Step 1 | Step 2 (placement seed; `_pmtn_sch` makespan) |
| `mcf_lb_diagnostic` | Step 1 | Step 2 (`mcf_lb` value, adjust fields) |
| `last_stage_only_sol` | Step 2 (or compatible) | Step 3 (input schedule); `_only_sch` makespan in Steps 1–2 |
| `last_stage_only_sol_p_increment` | Step 2 | Step 3 (rebuild gate) |
| `mcf_lb_phase_schedules` | each step (append) | reporter Gantt render |
| `solution_manager` (full incumbent) | Step 3 | `adjust_*` flags in Steps 1–2 (incumbent makespan) |

---

## Typical call patterns

### Pattern A — LB only

```python
ctl.apply_lb_by_mcf()
# obj_bound = MCF LB, obj_value = None
```

### Pattern B — full chain, no tightening

```python
ctl.apply_lb_by_mcf()
ctl.heuristic_last_stage_only_sch_from_mcf_lb()
ctl.build_full_sch_from_last_stage_only_sch()
```

### Pattern C — iterative tightening using pmtn reference

Run Pattern B once to seed an incumbent, then run a tightened second pass using
`mcf_preemptive_schedule` as the reference:

```python
# first pass
ctl.apply_lb_by_mcf()
ctl.heuristic_last_stage_only_sch_from_mcf_lb()
ctl.build_full_sch_from_last_stage_only_sch()

# second pass: r_adjust = incumbent_makespan - mcf_preemptive_schedule.makespan
ctl.apply_lb_by_mcf(
    adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=True,
)
ctl.heuristic_last_stage_only_sch_from_mcf_lb(
    adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=True,
)
ctl.build_full_sch_from_last_stage_only_sch()
```

### Pattern D — tightening using ls_only reference

After Pattern B, use the previous last-stage-only schedule as the reference
(typically a tighter baseline than the MCF preemptive schedule):

```python
ctl.apply_lb_by_mcf(
    adjust_r_by_full_sch_and_last_stage_only_sch=True,
    adjust_r_by_half=True,  # r_adjust = ceil(delta / 2)
)
ctl.heuristic_last_stage_only_sch_from_mcf_lb(
    adjust_r_by_full_sch_and_last_stage_only_sch=True,
    adjust_r_by_half=True,
)
ctl.build_full_sch_from_last_stage_only_sch()
```

### Pattern E — p-tightening with inflated durations

```python
ctl.apply_lb_by_mcf(
    adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=True,
)
# obj_bound = None (p_increment != 0 → not a global LB)
ctl.heuristic_last_stage_only_sch_from_mcf_lb(
    adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=True,
)
ctl.build_full_sch_from_last_stage_only_sch()
# rebuild gate fires: last-stage end times preserved, starts recomputed under original p
```

---

## Related

- [run_mcf_lb.md](run_mcf_lb.md) — original end-to-end pipeline (CP-SAT
  Phase 2, profile-fix Phase 4). The reverse-dispatch step (Phase 3 there) is
  the same `reverse_dispatch_full_schedule` called by Step 3 here.
- [`phase3_dispatch.py`](../../src/ffc_ddw_sum_et/algorithm/mcf_lb/phase3_dispatch.py) —
  `reverse_dispatch_full_schedule` used by Step 3.
- [`diagnostic.py`](../../src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py) —
  `MCFLBDiagnostic` fields written by Steps 1–2 and read by the reporter.
