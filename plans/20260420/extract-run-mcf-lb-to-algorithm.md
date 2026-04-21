# Extract `run_mcf_lb` + `MCFLBDiagnostic` into `algorithm/`

`FFcDDWSubroutineController.run_mcf_lb` currently lives in
`src/ffc_ddw_sum_et/orchestration/controller.py:98` together with its
dataclass `MCFLBDiagnostic` (line 25). The body is a 4-phase pipeline
(see `diag.reached_phase`: `init → mcf → last_stage → dispatched →
profile_fix`). Per `docs/algorithm-principles.md` the execution logic
belongs behind the `AlgSpec -> Algorithm.run -> AlgRecord` contract,
while orchestration-side concerns (incumbent registration, partial
schedule handoff) stay in the controller.

The extraction must happen **one phase at a time** so we can verify the
step-by-step behavior against the existing diagnostic before collapsing
everything behind a single `Algorithm.run` call.

## Target layout

Create a new package `src/ffc_ddw_sum_et/algorithm/mcf_lb/`:

```text
algorithm/mcf_lb/
  __init__.py          # re-exports MCFLB, MCFLBOption, MCFLBDiagnostic, MCFLBResult
  option.py            # MCFLBOption(AlgOption)
  diagnostic.py        # MCFLBDiagnostic (moved verbatim)
  result.py            # MCFLBResult (carries schedules each phase can set)
  phase1_mcf.py        # MCF LB + priority score + dispatch seed + last-stage-only model build
  phase2_last_stage.py # last-stage-only CP-SAT warm-start & solve
  phase3_dispatch.py   # reverse-dispatch + unflip (single-stage short-circuit included)
  phase4_profile_fix.py# profile-fix CP-SAT full solve
  algorithm.py         # MCFLB.run(spec) composing all 4 phases
```

Public API lands in `ffc_ddw_sum_et.algorithm` via `__init__.py`
re-exports (`MCFLB`, `MCFLBOption`, `MCFLBDiagnostic`).

## Contracts

### `MCFLBOption`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class MCFLBOption(AlgOption):
    last_stage_only_timelimit: float | str | None = None
    profile_fix_by_machine: bool = False
    machine_precedence_stride: int = 1
    solver_thread_cnt: int = 1  # currently hard-coded to 1, lift to option
```

### `MCFLBDiagnostic`

Moved verbatim from `controller.py`. Still consumed by experiment code
via `self.mcf_lb_diagnostic`; re-exported from
`ffc_ddw_sum_et.algorithm`.

### `MCFLBResult` (carried inside `AlgRecord.result.metrics` sideband + custom fields)

Algorithm layer cannot register incumbents mid-run (orchestration
concern). To preserve the current intermediate-register + partial-
schedule behavior, and to also surface **diagnostic schedules** that
are not valid full schedules but are useful for offline inspection, we
surface them in a structured result:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class MCFLBResult:
    # Progress diagnostics
    diagnostic: MCFLBDiagnostic

    # Schedules - incomplete (diagnostic-only; not valid full incumbents)
    mcf_preemptive_schedule: MCFPreemptiveSchedule | None = None    # step 1-1
    last_stage_only_init_schedule: FFcSchedule | None = None        # step 1-2
    last_stage_only_schedule: FFcSchedule | None = None             # step 1-3 (post CP-SAT)
    last_stage_only_schedule_flipped: FFcSchedule | None = None     # step 2-1 seed (reversed-instance)

    # Schedules - complete
    dispatched_schedule_before_unflipping: FFcSchedule | None = None  # step 2-1 (reversed-instance)
    dispatched_schedule: FFcSchedule | None = None                    # step 2-2 (original instance)
    final_schedule: FFcSchedule | None = None                         # step 2-3

    # Results
    obj_value: float | None = None  # final run objective
    obj_bound: float | None = None  # max(mcf_lb, pf_bound)
```

Seven schedule slots in total: four incomplete (preemptive / partial
pre-CP-SAT / partial post-CP-SAT / reversed-instance seed) and three
complete (reversed-instance dispatched, unflipped original-instance
dispatched, final).

**Reversed-instance intermediates (Phase 3 only).** Two of the new
slots capture artifacts that live on `reversed_instance =
FFcDDWParameters.reverse_stages(instance)` before being unflipped back
to the original instance:

- `last_stage_only_schedule_flipped` — seed passed into the reversed
  `MixedDispatcher`. It mirrors `last_stage_only_schedule` around
  `last_stage_only_schedule_makespan`: each op `(mc, s, e, j)` becomes
  `(mc, makespan - e, makespan - s, j)` on the same machine, inserted
  into an otherwise empty schedule over `reversed_instance`.
- `dispatched_schedule_before_unflipping` — result of
  `MixedDispatcher.get_best_mixed_schedule_by_sequence(...)` on
  `reversed_instance`, i.e. the complete reversed schedule just before
  `.as_reversed()` flips it back. Useful when debugging discrepancies
  between the reversed dispatcher and the unflipped output.

Per-phase objective scalars are **not** duplicated here — the
controller reads `diagnostic.last_stage_only_obj` and
`diagnostic.dispatched_obj` when it builds intermediate
`SubroutineReport` incumbents. `obj_value` is kept as the single
canonical "final run objective" even though it overlaps
`diagnostic.profile_fix_obj` in practice; the intent is to let
callers read the run's primary scalar without having to know which
phase produced it (step-2-3 normally; step-2-2 on phase-4 early
return).

Per-phase objective scalars are **not** duplicated here — the
controller reads `diagnostic.last_stage_only_obj` and
`diagnostic.dispatched_obj` when it builds intermediate
`SubroutineReport` incumbents. `obj_value` is kept as the single
canonical "final run objective" even though it overlaps
`diagnostic.profile_fix_obj` in practice; the intent is to let
callers read the run's primary scalar without having to know which
phase produced it (step-2-3 normally; step-2-2 on phase-4 early
return).

### `MCFPreemptiveSchedule` (new type)

`FFcSchedule` stores one `(start, end)` pair per `(stage, job)`
([ffc_schedule.py:425-426](src/ffc_ddw_sum_et/solution/ffc_schedule.py#L425-L426)),
so it cannot represent the multi-segment preemptive output of MCF. We
add a small dedicated type under
`src/ffc_ddw_sum_et/solution/mcf_preemptive_schedule.py` (sibling to
`FFcSchedule`, not under `algorithm/`, because it's a solution-domain
object):

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class MCFPreemptiveSchedule:
    """Preemptive last-stage assignment produced by
    ``ParallelMachinePreemptionMcf``. Each job may run in multiple
    unit-time slots, possibly on different last-stage machines.
    """
    stage_id: str
    machines: tuple[str, ...]
    # (machine_id, job_id, start_t, end_t) — half-open [start_t, end_t)
    segments: tuple[tuple[str, str, int, int], ...]
```

Construction (new helper, likely on `ParallelMachinePreemptionMcf` or a
standalone function in the same module):

```python
def mcf_to_preemptive_schedule(
    mcf: ParallelMachinePreemptionMcf,
    stage_id: str,
    machines: Sequence[str],
) -> MCFPreemptiveSchedule:
    """Convert MCF x[j][t] flow into per-machine unit-time segments.

    At each time t, the set of jobs with x[j][t] > 0 has size ≤ |machines|
    (capacity constraint). Greedily assign them to machines with the
    smallest last-used time, so adjacent same-job unit slots can be
    merged into a single [s, s+k) segment for readability.
    """
```

**Note.** MCF flow is integer per unit time, so each segment is an
integer interval. The assignment algorithm is a simple greedy: for each
time `t`, list jobs in a stable order (e.g. `x[j][t]` non-zero, sorted
by job_id), assign to machines preferring "same job continued from
t-1" then "machine with smallest last-used time". Adjacent identical
segments merge.

**Scope note.** `MCFPreemptiveSchedule` is diagnostic-only. It does not
plug into `compute_weighted_earliness_tardiness`, `FFcSchedule`-based dispatchers, or
`BaseModelBuilder`. Its only consumers are offline inspection /
per-phase Gantt emission.

`MCFLB.run` returns `AlgRecord` with `AlgResult(schedule=final_or_step22,
obj_value=..., obj_bound=..., metrics={...})`. The richer
`MCFLBResult` is attached via a new field on the algorithm's return or
threaded as `metrics` entries. **Decision point for review:** do we (a)
extend `AlgRecord` with an extension payload, (b) add `MCFLBResult` as
the value of a single metrics key, or (c) store it on the `MCFLB`
instance and let the controller pull it alongside `AlgRecord`? Default
choice here is (c) — keep `AlgRecord` strictly per-contract, expose
intermediate schedules via a sidecar attribute on the algorithm object.

### `MCFLB.run(spec: AlgSpec) -> AlgRecord`

- Reads `MCFLBOption` from `spec.option`.
- Uses `spec.logger` for warnings (per Rule 4).
- Calls `phase1 → phase2 → phase3 → phase4` in order, each returning
  a small dataclass that the next phase consumes.
- Stores the full `MCFLBResult` on `self.last_result` for controller
  pickup.
- Maps outcomes to `WorkStatus`/`TerminationReason`:
  - All 4 phases succeed → `FEASIBLE` + `COMPLETED`.
  - Phase 4 infeasible → `FEASIBLE` (step-2-2 result) + `COMPLETED`.
  - Phase 2 infeasible or Phase 3 dispatcher `None` → `INFEASIBLE` +
    `COMPLETED` with `result.schedule=None`.
  - MCF non-optimal → still raise `RuntimeError` (same as today; this
    is a data/instance validity assertion, not a run outcome).

## Controller changes (after all phases land)

`controller.py::run_mcf_lb` becomes a thin wrapper (~30 lines):

```python
def run_mcf_lb(self, ...) -> SubroutineReport:
    start_elapsed = self.timer.elapsed_sec
    option = MCFLBOption(
        last_stage_only_timelimit=last_stage_only_timelimit,
        profile_fix_by_machine=profile_fix_by_machine,
        machine_precedence_stride=machine_precedence_stride,
    )
    alg = MCFLB()
    record = alg.run(AlgSpec(instance=self.instance,
                             option=option, logger=self.logger))
    result = alg.last_result  # MCFLBResult
    self.mcf_lb_diagnostic = result.diagnostic
    if result.last_stage_only_schedule is not None:
        self.last_stage_cp_sat_solution = FFcDDWSolution(
            schedule=result.last_stage_only_schedule,
            obj_value=result.last_stage_only_obj, obj_bound=result.diagnostic.mcf_lb,
        )
    if result.dispatched_schedule is not None:
        self.solution_manager.register(
            SubroutineReport(
                elapsed_time=self.timer.elapsed_sec - start_elapsed,
                obj_value=result.dispatched_obj,
                obj_bound=result.diagnostic.mcf_lb,
            ),
            FFcDDWSolution(schedule=result.dispatched_schedule,
                           obj_value=result.dispatched_obj,
                           obj_bound=result.diagnostic.mcf_lb),
        )
    # build + register final SubroutineReport from record/result
    ...
```

The timer-based `start_elapsed` stays in the controller because the
routix `SubroutineReport.elapsed_time` is orchestration-shaped, not an
algorithm-contract concern.

`_parse_nc_timelimit` and `_build_schedule_from_op_starts` move into
the algorithm package too (they are algorithm-internal helpers).

## Step-by-step execution plan (what we do in the code session)

The user has asked to verify each phase as it lands. Each phase below
is its own commit / round of review.

### Step A — scaffold

Create the `algorithm/mcf_lb/` package with:

- `option.py` (full `MCFLBOption`)
- `diagnostic.py` (moved `MCFLBDiagnostic`)
- `result.py` (full `MCFLBResult`)
- `__init__.py` exposing the above
- helper relocation: move `_parse_nc_timelimit` and
  `_build_schedule_from_op_starts` into `algorithm/mcf_lb/_helpers.py`
  (re-exported from controller for now via import alias to keep
  call sites working).

No behavior change. Controller still owns `run_mcf_lb`. The
`MCFLBDiagnostic` name in controller becomes a re-export.

**Verify:** `uv run ruff check`, `uv run ruff format --check`,
existing tests pass.

### Step B — Phase 1 (MCF LB + dispatch seed)

Add `phase1_mcf.py`:

```python
def run_phase1(instance, logger, diag) -> Phase1State: ...
```

Returns: `mcf_lb`, `mcf_job_sequence`, `job_2_release_map`,
`duration_map`, `horizon`, last-stage id, job-position map, the
`ls_builder`/model/params/ops_vars, and `last_stage_only_init_schedule` already
dispatched.

Controller's `run_mcf_lb` calls `run_phase1(...)` first, keeps the
rest of the body unchanged. Diagnostic is filled by the phase.

**Verify:** run a small `fast` experiment and compare the controller's
final `MCFLBDiagnostic` against a baseline on `main` for a fixed
seed/instance.

### Step C — Phase 2 (last-stage-only CP-SAT)

Add `phase2_last_stage.py`:

```python
def run_phase2(phase1, option, logger, diag) -> Phase2State | None
```

Returns last-stage CP-SAT start/end maps together with
`last_stage_only_schedule` and `last_stage_only_schedule_makespan`, or
`None` on infeasibility.

Controller calls phase 2 after phase 1; maps `None` return to the
existing early-return path with `self.last_stage_cp_sat_solution`
left unset.

**Verify:** same experiment comparison.

### Step D — Phase 3 (reverse-dispatch + unflip)

Add `phase3_dispatch.py`:

```python
def run_phase3(phase1, phase2, logger, diag) -> Phase3State | None
```

Handles the `c == 1` short-circuit, reverse-seed construction,
`MixedDispatcher` call, `as_reversed()`, `compute_weighted_earliness_tardiness`. Returns
`dispatched_schedule` + `step2_obj`, or `None` on dispatcher failure.

Controller still does the intermediate-incumbent registration on the
returned dispatched schedule (orchestration side).

**Verify:** same experiment comparison.

### Step E — Phase 4 (profile-fix full solve)

Add `phase4_profile_fix.py`:

```python
def run_phase4(phase1, phase3, option, logger, diag) -> Phase4State | None
```

Builds the profile-fixed CP-SAT model from `dispatched_schedule`,
warm-starts, solves, and returns the final schedule together with
`obj_value` and `obj_bound_final`, or `None` on infeasibility (step-2-2
incumbent remains the reported result).

Controller wires phase 4 and emits the final `SubroutineReport`.

**Verify:** same experiment comparison.

### Step F — `MCFLB.run`

Compose all four phases behind `MCFLB.run(spec)` in `algorithm.py`.
Return `AlgRecord`. Controller's `run_mcf_lb` becomes the thin wrapper
described above. `self.last_result` (or an equivalent sidecar) exposes
`MCFLBResult` for incumbent/partial-schedule registration.

**Verify:** full experiment parity, diff against `main` on the
experiment summary CSV.

## Risks / open questions

1. **Intermediate-incumbent handoff.** Current algorithm boundary does
   not have a "yield intermediate schedule to the caller" idiom. We are
   using a sidecar (`alg.last_result`) rather than extending `AlgRecord`.
   If we later want multiple `MCFLB.run` instances active concurrently,
   the sidecar breaks. Not an issue today (controller uses one per run).
2. **Diagnostic mutation semantics.** `MCFLBDiagnostic` is mutable by
   design so that early returns retain partial data. We keep it mutable
   but pass it into each phase. Alternative (per-phase immutable
   snapshot) is more orthodox but changes the observable shape the
   controller relies on.
3. **`SubroutineReport` shape.** Stays in the controller — it's the
   routix reporting contract and does not belong under `algorithm/`.

## Rollback / safety

Each Step A–F is a standalone commit. Any step can be reverted
independently without touching later phases' code, because the
controller keeps calling the still-inline code paths for phases that
have not yet been extracted.
