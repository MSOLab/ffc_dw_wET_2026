# Plan: extract MCF-LB step bodies to `algorithm/` folder; controllers become thin wrappers

## Context

This is the deferred follow-up of the broader MCF-LB refactor whose
first three commits already landed:

- Step 5 (orphan deletion) — `run_mcf_lb_4`, `run_last_stage_cp_sat_lb`,
  `run_mcf_lb_then_neh_cp`, `neh_cp_last_stage_only_sch_from_mcf_lb`,
  `single_pass_last_stage_only_sch_from_mcf_lb` controller methods and
  the algorithm-side phase 1 / 2 / 4 modules deleted.
- Step 1 (diagnostic split) — `MCFLBDiagnostic` reduced to
  `apply_lb_by_mcf`'s own fields; three new dataclasses
  (`HeuristicLastStageOnlyDiagnostic`, `BuildFullSchDiagnostic`,
  `CalcMcfLbAndDeriveFullSchDiagnostic`) added; controller carries one
  slot per entry-point.
- Step 2 (Rep3 fix) — composite owns its own diagnostic and records
  the raw signed `makespan_delta` *before* the round-2 skip decision.

What is *not* done yet:

- The four kept controller methods (`apply_lb_by_mcf`,
  `heuristic_last_stage_only_sch_from_mcf_lb`,
  `build_full_sch_from_last_stage_only_sch`,
  `calc_mcf_lb_and_derive_full_sch`) still hold the full algorithm
  bodies inline.
- `calc_mcf_lb_and_derive_full_sch` still calls `self.apply_lb_by_mcf`
  / `self.heuristic_last_stage_only_sch_from_mcf_lb` /
  `self._build_full_sch_core` for r1 and r2 — the user's explicit
  invariant ("ban calling self.apply_lb_by_mcf inside
  calc_mcf_lb_and_derive_full_sch method") is therefore still violated.
- `solve_mcf_lb` already shed its `diagnostic` parameter (returns
  `mcf_solve_sec` on `McfLbResult`). Other algorithm primitives
  (`heuristic_last_stage_only_from_mcf_lb`,
  `reverse_dispatch_full_schedule`) are already pure but live next to
  legacy helpers.

The goal of this plan: cut the algorithm code out of the controller,
keep four ~30-line wrappers that translate between controller state
and the pure algorithm surface, and let the composite call the pure
algorithm functions directly.

## Step 3 — extract the three leaf functions

### New algorithm-folder layout

```
src/ffc_ddw_sum_et/algorithm/mcf_lb/
├── __init__.py             # adds: ApplyLbByMcfResult, BuildFullSchResult,
│                           # apply_lb_by_mcf, build_full_sch_from_last_stage_only_sch
├── diagnostic.py           # (already split in step 1, no change)
├── option.py               # (no change)
├── preemptive.py           # solve_mcf_lb (already pure after step 1)
├── utils.py                # (no change)
├── apply.py                # NEW — apply_lb_by_mcf algorithm function
├── heuristic.py            # RENAMED from last_stage_only.py
└── build_full_sch.py       # NEW — owns reverse_dispatch_full_schedule
                            #       (moved from phase3_dispatch.py) and
                            #       build_full_sch_from_last_stage_only_sch
```

Delete `last_stage_only.py` (after move) and `phase3_dispatch.py`
(after move). Update every importer.

### Algorithm function contracts (pure, no controller state)

All three take **explicit** inputs — no `self`, no
`solution_manager`, no `adjust_ref_full_sol`, no
`mcf_lb_phase_schedules`. Each returns a result dataclass that
includes `intermediate_schedules: list[tuple[str, MCFLBPhaseSchedule]]`
so the controller wrapper can call `_record_mcf_lb_phases` over it.

**`algorithm/mcf_lb/apply.py`**

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class ApplyLbByMcfResult:
    mcf_lb: float
    mcf_preemptive_schedule: MCFPreemptiveSchedule
    mcf: ParallelMachinePreemptionMcf
    mcf_solve_sec: float
    p_increment_used: int
    r_multiplier_used: float
    r_increment_used: int
    obj_bound_is_valid: bool   # mirror of the diagnostic property
    intermediate_schedules: list[tuple[str, MCFLBPhaseSchedule]]


def apply_lb_by_mcf(
    instance: FFcDDWParameters,
    *,
    p_increment: int = 0,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
    draw_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "due2-weight-pos",
    heatmap_yaml_path: Path | None = None,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
) -> ApplyLbByMcfResult:
    """Build the (possibly augmented) instance, solve the MCF
    relaxation, and return the bound + preemptive schedule. Raises
    ``MCFLBStopRequested`` if the predicate fires before the LP solve.
    """
```

The adjust-by-gap knobs (`adjust_*_by_full_sch_and_last_stage_*`)
**do not exist** on the algorithm function. Controller-state-driven
adjust logic is handled by the composite (which passes already-resolved
`p_increment`/`r_increment`).

**`algorithm/mcf_lb/heuristic.py`** (rename of `last_stage_only.py`)

`heuristic_last_stage_only_from_mcf_lb` already exists and is pure;
this step just renames the file and trims unrelated docstrings. The
algorithm fn does **not** take adjust-by-gap knobs either — same
reasoning. Augmented-instance build (when `p_increment != 0`) moves
into the function body.

```python
def heuristic_last_stage_only_from_mcf_lb(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    *,
    p_increment: int = 0,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
    job_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
    placement_priority: Literal["contrib", "dist"] = "contrib",
    logger: logging.Logger | None = None,
) -> HeuristicLastStageOnlyResult:
    ...
```

(`HeuristicLastStageOnlyResult` already lives in this module after
step 5; only `p_increment` / `r_multiplier` / `r_increment` are added
to the signature, replacing the controller-side
`with_stage_processing_time_increment` call.)

**`algorithm/mcf_lb/build_full_sch.py`**

Move `reverse_dispatch_full_schedule` here from `phase3_dispatch.py`.
Add `build_full_sch_from_last_stage_only_sch` — exactly today's
`_build_full_sch_core` body, with controller dependencies replaced by
explicit inputs.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class BuildFullSchResult:
    schedule: FFcSchedule | None
    dispatched_obj: float | None
    full_sch_makespan: int | None
    dispatch_sec: float
    intermediate_schedules: list[tuple[str, FFcSchedule]]


def build_full_sch_from_last_stage_only_sch(
    instance: FFcDDWParameters,
    last_stage_only_schedule: FFcSchedule,
    *,
    rebuild_last_stage_with_original_p: bool = False,
    logger: logging.Logger | None = None,
) -> BuildFullSchResult:
    ...
```

### Controller wrappers (≤ 40 lines each)

After extraction, each kept controller method becomes a translation
layer: validate kwargs / preconditions, resolve `<n>nc` time exprs,
compute adjust-knob increments from controller state (when knobs fire),
call the algorithm fn, record returned `intermediate_schedules` via
`_record_mcf_lb_phases`, populate the entry-point's diagnostic, build
`SubroutineReport`, register exactly once.

The wrappers keep their adjust-by-gap knobs (`adjust_p_by_full_sch_*`
etc.) because their data dependencies (`adjust_ref_full_sol`,
`solution_manager.get_incumbent()`, `last_stage_only_sol`,
`mcf_preemptive_schedule`) are controller-side state. The wrapper
resolves those into a plain `p_increment` / `r_increment` and forwards
to the algorithm function.

Concretely:

```python
def apply_lb_by_mcf(self, *, draw_heatmap=False, heatmap_sort=...,
                   p_increment=0, r_multiplier=1.0, r_increment=0,
                   adjust_p_by_full_sch_and_last_stage_only_pmtn_sch=False,
                   adjust_r_by_full_sch_and_last_stage_only_pmtn_sch=False,
                   adjust_p_by_full_sch_and_last_stage_only_sch=False,
                   adjust_r_by_full_sch_and_last_stage_only_sch=False,
                   adjust_r_by_half=False,
                   _register_report=True) -> SubroutineReport:
    self._validate_p_r_kwargs(p_increment, r_multiplier, r_increment)
    eff_p, eff_r = self._resolve_adjust_increments(
        p_increment, r_increment, adjust_r_by_half,
        adjust_p_by_full_sch_and_last_stage_only_pmtn_sch, ...
    )
    start_elapsed = time.monotonic()
    try:
        result = algo_apply_lb_by_mcf(
            self.instance,
            p_increment=eff_p,
            r_multiplier=r_multiplier,
            r_increment=eff_r,
            draw_heatmap=draw_heatmap,
            heatmap_sort=heatmap_sort,
            heatmap_yaml_path=self.try_get_file_path_for_subroutine(
                "_C_heatmap.yaml"),
            stop_predicate=self.is_stopping_condition,
            logger=self.logger,
        )
    except MCFLBStopRequested:
        return self._make_stop_report(start_elapsed)
    self.mcf_preemptive_schedule = result.mcf_preemptive_schedule
    self.mcf_preemptive_sch_p_increment = eff_p
    if _register_report:
        self.mcf_lb_phase_schedules.clear()
    self._record_mcf_lb_phases(result.intermediate_schedules)
    if _register_report:
        self.mcf_lb_diagnostic = MCFLBDiagnostic(
            mcf_lb=result.mcf_lb,
            mcf_solve_sec=result.mcf_solve_sec,
            p_increment_used=eff_p,
            r_multiplier_used=r_multiplier,
            r_increment_used=eff_r,
        )
    report = SubroutineReport(
        elapsed_time=time.monotonic() - start_elapsed,
        obj_value=None,
        obj_bound=result.mcf_lb if result.obj_bound_is_valid else None,
    )
    if _register_report:
        self._register(report, None)
    return report
```

A small private helper `_resolve_adjust_increments` factors out today's
`_ensure_makespans` / `fire_p` / `fire_r` block so both
`apply_lb_by_mcf` and `heuristic_last_stage_only_sch_from_mcf_lb`
wrappers can share it.

### Tests

The four kept controller-method tests
(`test_apply_lb_by_mcf_r_increment_voids_lb_and_does_not_decrease`,
`test_heuristic_last_stage_only_sch_from_mcf_lb_sets_solution`,
`test_heuristic_last_stage_only_sch_then_build_full`,
`test_build_full_sch_from_last_stage_only_sch`,
`test_r_increment_negative_raises`) must continue passing without
edit. Add focused unit tests for the algorithm functions that don't
go through the controller (probably new files
`tests/algorithm/mcf_lb/test_apply.py`,
`test_heuristic.py`, `test_build_full_sch.py`).

### Verification (after step 3)

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest tests/
uv run python main.py --config metadata/20260509/flip_makespan_cp_debug.yaml
```

Active config still produces Rep3 in
`*_adjust_params_by_makespan_delta.csv` with `makespanDelta=-6` and
empty `pIncrementAdded`/`rIncrementAdded` (regression guard for the
step-2 fix).

## Step 4 — extract the composite

### Goal

Move the `calc_mcf_lb_and_derive_full_sch` body into
`algorithm/mcf_lb/calc.py`. The controller wrapper becomes ~30 lines:
build the option dict, call the algorithm composite once, record
returned phase snapshots, populate
`CalcMcfLbAndDeriveFullSchDiagnostic` from the returned sub-results,
register the synthesized report.

The composite calls the three algorithm-folder leaf functions
directly. **`self.apply_lb_by_mcf` / `self.heuristic_*` /
`self._build_full_sch_core` are not called from inside the composite**
(user invariant).

### Algorithm-side composite

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CalcMcfLbAndDeriveFullSchResult:
    best_schedule: FFcSchedule | None
    best_obj: float | None
    final_obj_bound: float          # = r1.mcf_lb (always a valid LB)
    elapsed_sec: float
    # Round 1 (always populated unless r1 itself was stopped early).
    r1: ApplyLbByMcfResult | None
    r1_heuristic: HeuristicLastStageOnlyResult | None
    r1_build_full: BuildFullSchResult | None
    # Round 2 (populated only when r2 ran).
    r2: ApplyLbByMcfResult | None
    r2_heuristic: HeuristicLastStageOnlyResult | None
    r2_build_full: BuildFullSchResult | None
    r2_p_increment_added: int | None
    r2_r_increment_added: int | None
    # Skip metadata.
    makespan_delta: int | None      # raw signed; recorded BEFORE skip
    r2_ran: bool
    r2_skip_reason: str | None
    intermediate_schedules: list[tuple[str, MCFLBPhaseSchedule]]


def calc_mcf_lb_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "due2-weight-pos",
    job_placement_priority: PmPrmpSortKey = "1_rj_prmp_rel_dev",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "contrib",
    adjust_p: bool = False,
    adjust_r: bool = False,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
    heatmap_path_getter: Callable[[str], Path | None] | None = None,
) -> CalcMcfLbAndDeriveFullSchResult:
    ...
```

The composite is responsible for:

1. r1: call `algo_apply_lb_by_mcf` → `algo_heuristic_*` →
   `algo_build_full_sch`. Collect intermediates with `r1_` prefix.
2. Compute the raw signed `makespan_delta` and record it on the
   result struct unconditionally.
3. Decide whether to run r2 (`(adjust_p or adjust_r)`,
   `not stop_predicate()`, `s1 is not None`,
   `makespan_delta > 0`) — record `r2_skip_reason` on miss.
4. r2: compute `p_inc = ceil(delta * m_last / n) if adjust_p else 0`
   and `r_inc = ceil(delta / 2) if adjust_r else 0`. Call
   `algo_apply_lb_by_mcf(p_increment=p_inc, r_increment=r_inc)` then
   `algo_heuristic_*(p_increment=p_inc, r_increment=r_inc)` then
   `algo_build_full_sch`. Collect intermediates with `r2_` prefix.
5. Pick best of `(r1.full, r2.full)` by `obj_value`.
6. Return.

The phase-label namespacing (`r1_*` / `r2_*`) lives in the composite
itself — no `temporarily_extended_context` / controller-side namespace
is needed for the algorithm function. The controller wrapper pipes
`heatmap_path_getter` through so r1's and r2's heatmap YAMLs land in
distinct files (`r1__C_heatmap.yaml`, `r2__C_heatmap.yaml`); the
getter receives the round prefix.

### Controller wrapper

```python
def calc_mcf_lb_and_derive_full_sch(self, *, ...) -> SubroutineReport:
    start_elapsed = time.monotonic()
    self.mcf_lb_phase_schedules.clear()

    def _heatmap_path(round_prefix: str) -> Path | None:
        return self.try_get_file_path_for_subroutine(
            f"_{round_prefix}_C_heatmap.yaml"
        )

    result = algo_calc_mcf_lb_and_derive_full_sch(
        self.instance,
        draw_pmtn_sch_heatmap=draw_pmtn_sch_heatmap,
        heatmap_sort=heatmap_sort,
        job_placement_priority=job_placement_priority,
        last_stage_only_placement_criteria=last_stage_only_placement_criteria,
        adjust_p=adjust_p,
        adjust_r=adjust_r,
        stop_predicate=self.is_stopping_condition,
        logger=self.logger,
        heatmap_path_getter=_heatmap_path,
    )
    self._record_mcf_lb_phases(result.intermediate_schedules)
    if not emit_phase_schedules:
        self.mcf_lb_phase_schedules.clear()

    self.calc_mcf_lb_and_derive_full_sch_diagnostic = (
        _build_calc_diagnostic_from_result(result)
    )
    self._emit_calc_mcf_lb_phase_metrics_csv()

    final_report = SubroutineReport(
        elapsed_time=time.monotonic() - start_elapsed,
        obj_value=result.best_obj,
        obj_bound=result.final_obj_bound,
    )
    self._register(
        final_report,
        FFcDDWSolution(schedule=result.best_schedule, obj_value=result.best_obj,
                       obj_bound=result.final_obj_bound)
        if result.best_schedule is not None else None,
    )
    return final_report
```

`_build_calc_diagnostic_from_result` is a small free function next to
the wrapper that shapes the algorithm result into the
`CalcMcfLbAndDeriveFullSchDiagnostic` dataclass.

### Tests

Add `tests/algorithm/mcf_lb/test_calc.py` for the pure composite
(no controller). Existing controller-side tests for
`calc_mcf_lb_and_derive_full_sch` (whichever exist) keep passing.
Add a regression test that confirms `makespan_delta` is recorded on
the diagnostic in BOTH the skip path (`delta <= 0`) and the
non-skip path.

### Verification (after step 4)

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest tests/
uv run python main.py --config metadata/20260509/flip_makespan_cp_debug.yaml
```

End-to-end equivalence check: the active config's `summary.csv` must
be numerically identical (within float tolerance) to the
post-step-1+2 baseline. Rep3 still appears with `makespanDelta = -6`.

## Out of scope

- Touching `metadata/2026042*/` and `metadata/2026050{1..7}/` historic
  configs that reference deleted methods. Per agreement, they remain
  on disk as unrunnable historical record.
- Touching `docs/algorithms/20260426_mcf_lb.md` and friends — those
  are archival.
