# Plan: Lift `NehCpConstructor` onto the `Algorithm` execution contract

## Context

`NehCpConstructor` currently lives in `src/ffc_ddw_sum_et/orchestration/neh_cp.py`
(657 lines). It is the only step on `FFcDDWSubroutineController` that:

- carries its own `NehCpContext(Protocol)` instead of going through
  `AlgSpec`,
- returns `SubroutineReport` directly,
- calls `solution_manager.register(...)` from inside the algorithm body,
- writes `_step_log.yaml` via `ctx.get_file_path_for_subroutine(...)` (a
  context method, not via `alg_root`).

`docs/algorithm-principles.md` already defines the target contract
(`Algorithm` / `AlgSpec` / `AlgRecord`) and `docs/TODO.md` captures this
lift as a deferred refactor. Other algorithms in `src/ffc_ddw_sum_et/algorithm/`
(`FAMDispatcher`, `BN2DDispatcher`, the MCF-LB phases) already conform to
that contract — `NEH-CP` is the outlier.

This plan covers **only** the boundary lift. It is a structural refactor
with no algorithmic change: the same primary E/T solve, the same optional
secondary makespan solve, the same per-batch logs, the same final
schedule.

## Goals

1. Move the core builder under `src/ffc_ddw_sum_et/algorithm/neh_cp/`
   (a small package, not a single file — see Layout below).
2. Replace `NehCpContext(Protocol)` with the standard `AlgSpec`
   (`instance`, `option`, `logger`, `alg_root`).
3. Define `NehCpOption(AlgOption)` carrying every parameter currently in
   `NehCpConstructor.run(...)`. `cp_tl` / `total_timelimit` /
   `extra_batch_size_expr` / `cp_tl_2nd_obj` etc. should arrive as
   already-resolved scalars where possible — see "Option resolution"
   below.
4. Return `AlgRecord` whose `result.obj_value` is the weighted E+T of the
   final schedule (project convention) and whose `progress_log` carries
   the per-batch step entries (replacing the ad-hoc `sub_step_log` dict
   list).
5. Move `solution_manager.register(...)` and per-batch log file emission
   into a thin orchestration adapter on the controller; the algorithm
   core no longer touches these.
6. Keep the public surface from the caller's perspective unchanged:
   - `controller.neh_cp(...)` still exists, still returns `SubroutineReport`,
   - YAML configs (`method: neh_cp`) keep working without edits,
   - all existing tests still pass.

## Out of scope

- Any algorithmic change (priority rules, batch shape, two-stage solve).
- Changing the `_neh_cp_job_sequence` priority semantics (already settled
  this PR — desc on `w⁻+w⁺`).
- Lifting `MCF-LB`'s `run_mcf_lb_4` onto the algorithm contract — it has
  its own controller-level orchestration (Phase 1–4 multi-step) that
  needs a separate plan.
- Changing the `SubroutineReport` shape used by `routix`.
- Renaming any YAML keys.

## Prerequisites (already satisfied)

- `Algorithm` Protocol — `src/ffc_ddw_sum_et/algorithm/base/algorithm.py`.
- `AlgSpec` — `src/ffc_ddw_sum_et/algorithm/base/alg_spec.py` carries
  `instance`, `option`, `ref_solution`, `alg_root`, `logger`.
- `AlgRecord` / `AlgResult` / `ProgressLogEntry` —
  `src/ffc_ddw_sum_et/algorithm/base/alg_record.py`.
- Project convention: `AlgRecord.obj_value` = weighted E+T (memory:
  `feedback_alg_record_obj_value`).
- Working precedent: `FAMDispatcher` (`algorithm/fam.py`) and
  `BN2DDispatcher` (`algorithm/dispatcher/bn2d.py`) already follow the
  same shape we want here.

## Target file layout

```
src/ffc_ddw_sum_et/algorithm/
    step_tl_resolver.py             # resolve_per_step_tl + BatchTlMode
                                    # (shared across subroutines)
    utils.py                        # trunc4 (shared across subroutines)
    neh_cp/                         # NEW package
        __init__.py                 # re-export NehCpDispatcher, NehCpOption,
                                    # NehCpJobPriority, NehCpStepEntry,
                                    # neh_cp_job_sequence
        option.py                   # NehCpOption(AlgOption)
        dispatcher.py               # NehCpDispatcher (Algorithm)
        sequence.py                 # _neh_cp_job_sequence + NehCpJobPriority
        step_log.py                 # NehCpStepEntry dataclass +
                                    # progress_log <-> step_log conversion
src/ffc_ddw_sum_et/algorithm/__init__.py
                                    # export NehCpDispatcher, NehCpOption
src/ffc_ddw_sum_et/orchestration/
    controller.py                   # neh_cp(...) becomes a thin adapter
    neh_cp.py                       # DELETE (after content migrated)
tests/algorithm/neh_cp/             # NEW
    test_option.py
    test_sequence.py                # moved from tests/orchestration/test_neh_cp.py
    test_tl_schedule.py             # _resolve_per_step_tl coverage
    test_dispatcher.py              # AlgSpec → AlgRecord smoke test
tests/orchestration/
    test_controller.py              # keep test_neh_cp_registers_full_schedule
    test_neh_cp.py                  # DELETE (sequence tests move to algorithm/)
```

Splitting into a sub-package (rather than a single 657-line file under
`algorithm/`) keeps each file focused and matches the `mcf_lb/` /
`dispatcher/` precedent already in the algorithm subtree.

## Files to modify

- `src/ffc_ddw_sum_et/algorithm/neh_cp/*` — **new** (5 files).
- `src/ffc_ddw_sum_et/algorithm/__init__.py` — add exports.
- `src/ffc_ddw_sum_et/orchestration/controller.py` — replace
  `NehCpConstructor(self).run(...)` body with `NehCpDispatcher().run(spec)`
  + register loop.
- `src/ffc_ddw_sum_et/orchestration/neh_cp.py` — **delete**.
- `tests/orchestration/test_neh_cp.py` — **delete** (sequence tests move
  under `tests/algorithm/neh_cp/`).
- `tests/algorithm/neh_cp/*` — **new**.
- `docs/algorithms/neh_cp.md` — update the "Job ordering" line and any
  reference to `ctx._neh_cp_job_sequence`; add a one-line note that
  `NehCpDispatcher` is the algorithm-side entry point.
- `docs/TODO.md` — remove the "Lift `NehCpConstructor` onto the
  `Algorithm` boundary" entry once this plan lands.

No YAML config edits — `controller.neh_cp(...)` keeps the same kwargs.

## Design

### `NehCpOption(AlgOption)`

Every parameter currently on `NehCpConstructor.run(...)` becomes a field
on `NehCpOption`. Two design decisions to make explicit:

1. **Pre-resolved vs. raw expressions.** Today the runner accepts
   `cp_tl: float | str | None` and resolves the `"<n>nc"` grammar inside.
   The algorithm boundary should not know about `value_resolver` (it's
   an orchestration helper) — so the controller adapter resolves all
   four expressions (`cp_tl`, `total_timelimit`, `cp_tl_2nd_obj`,
   `extra_batch_size_expr`) **before** building `NehCpOption`. Option
   fields hold pre-resolved `float | None` / `int | None` values.

2. **`skip_pf_below_obj`.** Stays as `Literal["makespan"] | float | None`.
   The string-to-float coercion currently inside `run(...)` moves to
   `NehCpOption.__post_init__` (or a `from_raw(...)` classmethod) so the
   validation error path stays where the field lives.

Sketch:

```python
# src/ffc_ddw_sum_et/algorithm/neh_cp/option.py

@dataclass(frozen=True, slots=True, kw_only=True)
class NehCpOption(AlgOption):
    job_priority: NehCpJobPriority = "weight-due-pos"
    solver_thread_cnt: int = 1
    added_batch_size: int = 1
    extra_batch_size_extra: int = 0   # pre-resolved (was extra_batch_size_expr)
    cp_tl_seconds: float | None = None
    total_timelimit_seconds: float | None = None
    num_batches: int | None = None
    batch_tl_mode: BatchTlMode = "constant"
    batch_tl_offset_seconds: float = 0.01
    apply_cumulative_tl: bool = False
    pf_method: PFMethod = "PF1"
    skip_pf_below_obj: Literal["makespan"] | float | None = None
    make_semi_active_after_cp: bool = False
    minimize_makespan_lex: bool = False
    cp_tl_2nd_obj_seconds: float | None = None
    error_if_infeasible: bool = False
```

### `NehCpDispatcher(Algorithm)`

```python
# src/ffc_ddw_sum_et/algorithm/neh_cp/dispatcher.py

class NehCpDispatcher:
    algorithm_id = "neh_cp"

    def run(self, spec: AlgSpec) -> AlgRecord:
        instance = self._validate_instance(spec)   # FFcDDWParameters
        option = self._resolve_option(spec)        # NehCpOption
        if spec.ref_solution is not None:
            raise NotImplementedError("...")

        # Body identical to NehCpConstructor.run(...) today, except:
        #   - logger comes from spec.logger (fall back to logging module)
        #   - no calls to ctx.solution_manager.register(...)
        #   - per-batch step entries collected as ProgressLogEntry tuples
        #   - if alg_root is set, dump step log JSON to alg_root/_step_log.yaml
        #   - return AlgRecord(...) instead of SubroutineReport

        return AlgRecord(
            work_status=WorkStatus.FEASIBLE,
            instance_id=instance.name,
            algorithm_id=self.algorithm_id,
            option=option,
            result=AlgResult(
                schedule=final_schedule,
                obj_value=weighted_et,             # primary objective
                obj_bound=None,
                metrics={
                    "sum_earliness": sum_e,
                    "sum_tardiness": sum_t,
                    "makespan": final_schedule.makespan,
                },
            ),
            progress_log=tuple(progress_entries),  # see below
            termination_reason=TerminationReason.COMPLETED,
        )
```

### Progress log mapping

Today's `sub_step_log` entry is a dict with 9 fields. Map to
`ProgressLogEntry` like this:

| current `sub_step_log` field | new home                                      |
|------------------------------|-----------------------------------------------|
| `step`                       | derived from index in `progress_log` tuple    |
| `elapsed_time`               | `ProgressLogEntry.elapsed_sec`                |
| `sub_obj` (UB)               | `ProgressLogEntry.obj_value`                  |
| `sub_obj_lb`                 | `ProgressLogEntry.obj_bound`                  |
| `TL`, `elapsed_portion`, `gap`, `job_count`, `makespan`, `ran_2nd_obj` | `ProgressLogEntry.note` as a structured `dict` if we widen `note`, OR keep them in a side dataclass `NehCpStepEntry` referenced from `AlgRecord` via a separate field |

Two routes — pick one before implementing:

- **Route A (preferred).** Keep `ProgressLogEntry` slim (only
  `obj_value` / `obj_bound` / `elapsed_sec` / `note: str | None`). The
  extra NEH-CP-specific fields (`gap`, `makespan`, `TL`, …) get a
  dedicated `NehCpStepEntry` tuple stored in
  `AlgRecord.result.metrics["step_log"]` (loosens `metrics` typing to
  also accept tuple-of-dicts; the principles doc allows "auxiliary
  metrics"). The dispatcher emits both: standard `progress_log` for
  generic tooling + algorithm-specific step log via metrics.
- **Route B.** Widen `ProgressLogEntry` to allow `note: str | Mapping[str, Any]`
  and pack everything into `note`. Less typed but keeps the contract
  flatter.

Recommendation: Route A. Generic `obj_value`/`obj_bound` traces stay
machine-comparable across algorithms; NEH-CP-specific fields don't
contaminate the base record shape.

### `alg_root` and the step log file

Per Rule 3 of the algorithm principles: the only filesystem location the
algorithm may write to is `spec.alg_root`. The current implementation
uses `ctx.get_file_path_for_subroutine("_step_log.yaml")` which pulls a
controller-aware path.

Migration:

- The controller adapter resolves
  `alg_root = self.get_file_path_for_subroutine("").parent`
  (or equivalent — verify against `controller_core.py`) and passes it
  via `AlgSpec.alg_root`.
- `NehCpDispatcher.run` writes `alg_root / "neh_cp_step_log.yaml"` if
  `alg_root` is not `None`. If `alg_root` is `None`, skip the dump
  cleanly (per Rule 3).
- The dump itself stays `routix.io.dump_yaml`; its content is now the
  serialized `NehCpStepEntry` tuple from `AlgResult.metrics["step_log"]`.

Open question for review during implementation: does
`get_file_path_for_subroutine("_step_log.yaml")` produce a per-step
subroutine path or the top-level instance dir? Whichever it is, the
adapter should pre-compute the correct directory and pass it in
`alg_root`.

### Logger

`NehCpDispatcher.run` reads `spec.logger`. Fall back to `logging` module
when `None` (per Rule 4, matches the `BN2DDispatcher._debug` helper).
The current code uses `ctx.logger.info(...)` / `ctx.logger.warning(...)`
directly — replace each with `_log = spec.logger or logging.getLogger(__name__)`
once at the top of `run(...)`.

### Controller adapter

`controller.neh_cp(...)` becomes a thin shim:

1. Resolve all `<expr>` strings to scalars via `resolve_value_expr`.
2. Build `NehCpOption(...)`.
3. Build `AlgSpec(instance=self.instance, option=opt, logger=self.logger,
   alg_root=self.get_file_path_for_subroutine("").parent)`.
4. Call `record = NehCpDispatcher().run(spec)`.
5. Build `SubroutineReport(elapsed_time=<measured-by-controller>,
   obj_value=record.result.obj_value, obj_bound=None)`. Wall-clock comes
   from the controller frame (`time.monotonic()` around `.run(spec)`),
   not from the `AlgRecord` — `TimingInfo`/`AlgRecord.timing` were
   removed as YAGNI; algorithm-side ortools `solver.wall_time` is
   already in sec.
6. `self.solution_manager.register(report,
       FFcDDWSolution(schedule=record.result.schedule,
                      obj_value=record.result.obj_value))`.
7. Return `report`.

Total shim length: ~30 lines, replaces today's ~50-line
`controller.neh_cp(...)` plus the `NehCpConstructor(self)` boilerplate.

## Migration sequence

Each step below should leave the test suite green before moving on.

1. **Skeleton in place.** Create `algorithm/neh_cp/__init__.py`,
   `option.py`, `sequence.py`, `tl_schedule.py`, `step_log.py`,
   `dispatcher.py` as empty stubs that re-export the same names from
   `orchestration/neh_cp.py`. Add the new package to
   `algorithm/__init__.py` exports. Run tests — should still pass.
2. **Move sequence helpers.** Move `_neh_cp_job_sequence` and the
   `NehCpJobPriority` literal into `algorithm/neh_cp/sequence.py`.
   `orchestration/neh_cp.py` keeps `from .sequence import ...` shims for
   one commit. Move `tests/orchestration/test_neh_cp.py` to
   `tests/algorithm/neh_cp/test_sequence.py`. Run tests.
3. **Move TL schedule.** Move `resolve_per_step_tl` and
   `BatchTlMode` into `algorithm/step_tl_resolver.py` (shared module
   at the algorithm package level, not inside `neh_cp/`, since the same
   per-step TL resolution is reusable across subroutines). Add
   `tests/algorithm/test_step_tl_resolver.py` covering: constant
   mode, linear mode, the `B*offset > total_seconds` fallback, and the
   `total_seconds is None` short-circuit. (These cases are not
   currently unit-tested.) Run tests.
4. **Define `NehCpOption`.** Add `option.py` with the dataclass and the
   `from_raw(...)` classmethod (handles `skip_pf_below_obj` string
   coercion). Don't wire it in yet. Add unit tests covering: each
   priority literal round-trips, the `from_raw` validation paths.
5. **Implement `NehCpDispatcher`.** Port the body of
   `NehCpConstructor.run(...)` into `dispatcher.py:run(spec)`. The
   diff is mostly:
   - Replace `ctx.instance` → `spec.instance` after a type guard.
   - Replace `ctx.logger` → `spec.logger or logging.getLogger(__name__)`.
   - Drop `ctx.solution_manager.register(...)`.
   - Drop the `ctx.get_file_path_for_subroutine` import; gate the
     `_step_log.yaml` dump on `spec.alg_root is not None`.
   - Build `progress_log` tuple from per-batch results.
   - Return `AlgRecord` instead of `SubroutineReport`.
6. **Rewire the controller.** Replace the `NehCpConstructor(self).run(...)`
   call in `controller.neh_cp(...)` with the adapter described in
   "Controller adapter" above. Delete the import of
   `NehCpConstructor` / `NehCpContext`. Run the full test suite,
   including `test_neh_cp_registers_full_schedule`.
7. **Run an end-to-end smoke check.** Pick one fast YAML config that
   exercises `method: neh_cp` (e.g. `metadata/20260424/neh_cp_config_5.yaml`)
   and run it via `uv run python main.py`. Compare the run's
   `*_step_log.yaml` against a baseline run from before this refactor —
   the two should be identical (same schedule, same per-batch obj
   trajectory).
8. **Delete legacy files.** Remove `orchestration/neh_cp.py`,
   `tests/orchestration/test_neh_cp.py` (now superseded). Trim
   `controller.py`'s import of `NehCpConstructor` / `NehCpContext`. Run
   tests.
9. **Doc cleanup.** Update `docs/algorithms/neh_cp.md` to point at
   `NehCpDispatcher`. Remove the "Lift `NehCpConstructor` onto the
   `Algorithm` boundary" entry from `docs/TODO.md`.
10. **One commit per logical step.** Squash if needed; the recommended
    grain is steps 1–3 in one commit (preparatory moves), step 4 in
    one (option type), steps 5–6 in one (the actual lift), and 8–9
    bundled (cleanup).

## Test plan

- Existing tests must keep passing without behavior edits:
  - `tests/orchestration/test_controller.py::test_neh_cp_registers_full_schedule`
    (controller path).
- Sequence tests move to `tests/algorithm/neh_cp/test_sequence.py`,
  unchanged in body.
- New `tests/algorithm/neh_cp/test_dispatcher.py`:
  - Build a tiny instance (3 jobs × 2 stages — reuse `_make_instance`
    helper from `test_controller.py`).
  - Build an `AlgSpec(instance=ins, option=NehCpOption(cp_tl_seconds=1.0))`.
  - Assert `record.work_status == WorkStatus.FEASIBLE`.
  - Assert `record.result.schedule` is a complete schedule (every job
    × stage).
  - Assert `record.result.obj_value == compute_weighted_earliness_tardiness(
      record.result.schedule, ins).sum()`.
  - Assert `len(record.progress_log) == number_of_batches`.
  - Assert `record.algorithm_id == "neh_cp"`.
- New `tests/algorithm/neh_cp/test_tl_schedule.py`:
  - Constant mode with `total_seconds=10.0`, `batch_count=4` → all four
    entries are `2.5`.
  - Linear mode with `total_seconds=10.0`, `batch_count=4`,
    `offset=0.1` → sum equals `10.0`, monotonically non-decreasing,
    first entry is `0.1 + x`.
  - Fallback path: `offset=10.0`, `batch_count=4`, `total_seconds=10.0`
    → constant `2.5` + warning logged.
  - `total_seconds=None`, `cp_tl_from_arg=None` → returns `None`.
  - `total_seconds=None`, `cp_tl_from_arg=2.5` → `[2.5] * batch_count`.
- New `tests/algorithm/neh_cp/test_option.py`:
  - `NehCpOption.from_raw(skip_pf_below_obj="3.5")` → coerces to `3.5`.
  - `NehCpOption.from_raw(skip_pf_below_obj="not-a-number")` → raises
    `ValueError`.
  - `NehCpOption.from_raw(skip_pf_below_obj="makespan")` → preserved.

Run command: `uv run pytest tests/algorithm/neh_cp/ tests/orchestration/test_controller.py`.

## Risks

- **Behavior drift via floating-point.** The expression resolution moves
  from inside `run(...)` to the controller adapter. As long as we resolve
  with the same `resolve_value_expr` and pass scalars through unchanged,
  results stay byte-identical. The end-to-end smoke check (step 7) is
  the safety net.
- **`alg_root` semantics.** If we hand the dispatcher the wrong
  directory, the step log file ends up in the wrong place. Verify the
  path in step 6 against a baseline run before deleting the legacy file
  in step 8.
- **`metrics` typing.** Route A widens `metrics` to allow tuple-valued
  entries (for `step_log`). The current type is
  `Mapping[str, int | float] | None`. Option 1: relax to
  `Mapping[str, Any] | None` (small, justified by Rule 12 already
  allowing "auxiliary metrics"). Option 2: keep the strict mapping and
  store `step_log` as a separate top-level field on `AlgRecord` — but
  that touches the contract for one caller. Recommendation: relax in
  this plan and note it in `algorithm-principles.md` as "metrics may
  carry algorithm-specific structured payloads".
- **YAML compatibility.** No YAML keys change; `controller.neh_cp(...)`
  signature is preserved. If a config relies on the existence of
  `NehCpConstructor` / `NehCpContext` in `ffc_ddw_sum_et.orchestration`,
  it will break — `grep` shows there are no such users today, only the
  controller itself.

## Done criteria

- `src/ffc_ddw_sum_et/algorithm/neh_cp/` package exists and is the only
  source of truth for the NEH-CP algorithm.
- `orchestration/neh_cp.py` is deleted.
- `controller.neh_cp(...)` is a < 50-line adapter that goes
  `expr → option → spec → dispatcher → record → register`.
- `uv run pytest tests/` is green (114 + new tests).
- `uv run ruff check src/ tests/` is clean.
- One end-to-end YAML config (`neh_cp_config_5.yaml`) reproduces the
  same schedule + per-batch trajectory as a pre-refactor baseline run.
- `docs/TODO.md`'s "Lift `NehCpConstructor` onto the `Algorithm`
  boundary" entry is removed.

## Why now

This was deferred during the original `feat(neh-cp): adaptive batch + TL
log fields` work because the priority then was getting the experiment
configs landed. With the BN2D port also conforming to `Algorithm` /
`AlgSpec` / `AlgRecord`, NEH-CP is the only remaining outlier on the
controller — every additional config or tweak we ship without the lift
makes the eventual migration noisier. Doing it now keeps the algorithm
boundary uniformly enforced before further NEH-CP variants (e.g., the
two-stage optimize follow-up referenced in
`plans/20260424/neh_cp_two_stage_optimize.md`) accrete more
controller-facing seams.
