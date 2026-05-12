# Plan: Multi-dispatching sweep inside `calc_mcf_lb_and_derive_full_sch`

## Context

Today `calc_mcf_lb_and_derive_full_sch` runs each round (r1 and conditional
r2) with **one** dispatching combination
``(job_placement_priority, last_stage_only_placement_criteria)`` supplied
by the YAML scenario. The expensive part is `apply_lb_by_mcf` (MCF LP).
The two knobs only affect the cheap downstream steps:

* `heuristic_last_stage_only_from_mcf_lb` — takes the LP's preemptive
  schedule and produces a last-stage-only schedule. Cheap O(n²).
* `build_full_sch_from_last_stage_only_sch` — reverse-dispatch + unflip.
  Mid-cost (two MixedDispatcher invocations on the reversed instance).

Goal: try multiple `(job_placement_priority,
last_stage_only_placement_criteria)` combinations per round, pick the
best by **last-stage-only `obj_value`** (cheapest selection metric), and
only run the expensive `build_full_sch_from_last_stage_only_sch` on the
winning trial. r2's `makespan_delta` continues to derive from r1's
winning full schedule.

YAML syntax (controller layer):

* `None` (key omitted) — sweep the entire axis vocabulary.
* scalar string — fix that axis to one value (legacy behaviour).
* list of strings — sweep only the listed subset (must be non-empty).

`PmPrmpSortKey` vocabulary: 6 values
(`"1_rj_prmp_rel_dev"`, `"1_rj_prmp_abs_dev"`, `"start_time"`,
`"end_time"`, `"start_time_maxw"`, `"end_time_maxw"`).
`last_stage_only_placement_criteria` vocabulary: 2 values
(`"contrib"`, `"dist"`).

Full sweep = 12 trials per round → 12 cheap heuristics + 1 reverse-dispatch.

## Files to touch

| File | Change |
|---|---|
| `src/ffc_ddw_sum_et/algorithm/mcf_lb/mcf_lb_pipeline.py` | Pipeline rewrite: sweep heuristics, pick best by `obj_value`, run `build_full` only on winner. Add per-round trial records on result dataclass. |
| `src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py` | Add per-trial bookkeeping fields on `CalcMcfLbAndDeriveFullSchDiagnostic` (`r1_trials`, `r2_trials`, `r1_chosen_*`, `r2_chosen_*`). |
| `src/ffc_ddw_sum_et/orchestration/controller.py` | Accept `None | str | list[str]` for both knobs, resolve to a sweep grid, forward; populate trial fields on the diagnostic; emit new trials CSV. |
| `src/ffc_ddw_sum_et/orchestration/mcf_lb_phase_labels.py` | (no change — winning trial fills the existing label slots) |
| `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml` | Register a new `mcf_lb_trials_csv` instance/progress artifact kind. |
| `metadata/20260512/mcf_lb_flip_makespan.yaml` | Drop both knobs from the scenario so the sweep kicks in. |
| (optional) `plans/20260512/mcf_lb_dispatch_sweep.md` | This file. |

No test changes — current tests pin neither value.

## Design

### 1. Sweep grid resolution (controller)

New helper `_resolve_dispatch_sweep_grid(...)` in controller:

```python
_ALL_JOB_PRIORITIES: tuple[PmPrmpSortKey, ...] = (
    "1_rj_prmp_rel_dev", "1_rj_prmp_abs_dev",
    "start_time", "end_time", "start_time_maxw", "end_time_maxw",
)
_ALL_PLACEMENT_CRITERIA: tuple[Literal["contrib", "dist"], ...] = (
    "contrib", "dist",
)

def _resolve_axis(
    arg: str | Sequence[str] | None,
    vocab: tuple[str, ...],
    axis_name: str,
) -> tuple[str, ...]:
    if arg is None:
        return vocab
    if isinstance(arg, str):
        if arg not in vocab:
            raise ValueError(f"Unknown {axis_name}: {arg!r}; expected one of {vocab}.")
        return (arg,)
    items = tuple(arg)
    if not items:
        raise ValueError(f"{axis_name} list must be non-empty.")
    for x in items:
        if x not in vocab:
            raise ValueError(f"Unknown {axis_name}: {x!r}; expected one of {vocab}.")
    return items
```

Cartesian product produces the trial grid; deterministic order:
job_priority outer, placement_criteria inner. First combo of the grid is
also the **tiebreak winner** when multiple trials share the same
`obj_value`.

### 2. Pipeline rewrite (algorithm-side)

New per-round helper signature (replaces the per-round entries' inner
heuristic+build_full call):

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class DispatchSweepTrial:
    job_priority: PmPrmpSortKey
    placement_priority: Literal["contrib", "dist"]
    obj_value: float
    makespan: int
    heuristic: HeuristicLastStageOnlyResult

def _sweep_last_stage_only(
    instance: FFcDDWParameters,
    apply: ApplyLbByMcfResult,
    *,
    grid: Sequence[tuple[PmPrmpSortKey, Literal["contrib", "dist"]]],
    p_increment: int = 0,
    r_multiplier: float = 1.0,
    r_increment: int = 0,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
) -> tuple[list[DispatchSweepTrial], int]:
    """Run the heuristic for every grid entry; return (trials, winner_idx)."""
```

Tiebreak: lowest `(obj_value, grid_index)`.

`calc_mcf_lb_r1_and_derive_full_sch` and
`calc_mcf_lb_r2_and_derive_full_sch` change shape:

* Accept a `grid: Sequence[tuple[...,...]]` instead of single
  `job_placement_priority` / `last_stage_only_placement_criteria`.
* Call `apply_lb_by_mcf` once.
* Call `_sweep_last_stage_only` → winner trial.
* Call `build_full_sch_from_last_stage_only_sch` on **winner only**.
* Returned `CalcMcfLbR1Result` / `CalcMcfLbR2Result` carries the trial
  list + `chosen_idx` + the unchanged `apply` / `heuristic` /
  `build_full` slots (each pointing at the **winner's** sub-result).

The composite `calc_mcf_lb_and_derive_full_sch` continues to:

* Use `r1.apply` (the LP) for `final_obj_bound`.
* Derive `makespan_delta` from `r1.build_full.schedule.makespan` (the
  winner's full schedule makespan) minus `ref_makespan`.
* Compare `r1.build_full.dispatched_obj` vs `r2.build_full.dispatched_obj`
  (the winning trials' full schedules) for `best_schedule`.

### 3. Diagnostic additions

```python
@dataclass(slots=True)
class DispatchTrialDict:
    job_priority: str
    placement_priority: str
    obj_value: float
    makespan: int
    chosen: bool

@dataclass(slots=True)
class CalcMcfLbAndDeriveFullSchDiagnostic:
    ...  # existing fields kept
    # New: per-round trial sweep records.
    r1_trials: list[dict] = field(default_factory=list)
    r2_trials: list[dict] = field(default_factory=list)
    r1_chosen_job_priority: str | None = None
    r1_chosen_placement_criteria: str | None = None
    r2_chosen_job_priority: str | None = None
    r2_chosen_placement_criteria: str | None = None
```

(Plain `dict` payloads keep YAML/JSON serialization paths unchanged.)

### 4. Trials CSV artifact

Per-instance, zone=progress, new kind `mcf_lb_trials_csv`:

```
file_template: "{instance_name}_calc_mcf_lb_trials.csv"
columns: round, job_priority, placement_criteria, obj_value, makespan, chosen
```

* One row per trial; `chosen` is `"yes"` only on the winner.
* Always emitted when a layout is bound (no gating flag).

Controller method emits this immediately after the pipeline returns.

### 5. Phase-schedule emission semantics

* `mcf_lb_phase_obj_csv` / `mcf_lb_phase_makespan_csv`: continue to write
  exactly one r1 row + one r2 row per label slot, populated from the
  winning trial's `phase_schedules`. Schema unchanged.
* `calc_mcf_lb_phase_schedule` JSONs (gated by `emit_phase_schedules`):
  same — winning trial only.
* `calc_mcf_lb_r1_summary_yaml` / `calc_mcf_lb_r2_summary_yaml`: extend
  with `chosenJobPriority` and `chosenPlacementCriteria` fields. Other
  numbers reflect the winning trial.

### 6. Controller signature change

```python
def calc_mcf_lb_and_derive_full_sch(
    self,
    *,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey | Sequence[PmPrmpSortKey] | None = None,
    last_stage_only_placement_criteria:
        Literal["contrib", "dist"]
        | Sequence[Literal["contrib", "dist"]]
        | None = None,
    ...  # rest unchanged
) -> SubroutineReport:
```

Defaults change from `"end_time"` / `"dist"` to `None` / `None` (full
sweep is the new default). Single-value YAML keeps working — passes
through as a singleton grid.

Subroutine-step contract honoured: `start_elapsed = time.monotonic()`
captured at entry, single `_register` at exit, no work between
`elapsed = ...` and `_register`. Stop-predicate is consulted between
trials inside `_sweep_last_stage_only` so a long sweep can short-circuit.

### 7. r2 reference makespan

`makespan_delta` still uses r1's **winner**:
* `"mcfLbMakespan"` → `r1.apply.mcf_preemptive_schedule.makespan`
  (independent of the sweep — same LP).
* `"lastStageOnlyMakespan"` → r1 winner's `heuristic.schedule.makespan`.

The `proceed_r2_when_nonpositive_cmax` flag is untouched; clamp logic
still operates on the same signed delta.

### 8. Backward compatibility

* Scenarios with both knobs set keep producing one trial each round.
  The trials CSV will still be emitted (one row + winner=yes).
* Scenarios that omit both keys (or set them to `null`) get the full
  12-combo sweep per round.

## Risks & mitigations

* **Cost increase** — last-stage heuristic is O(n² · m); 12 of them is
  bounded. Reverse-dispatch is unchanged (winner only). Net cost should
  be marginally above today.
* **Diagnostic field churn** — new dataclass fields default to empty
  list / `None`, so existing serializers (`_diag_to_dict` in the runner)
  remain compatible.
* **Reporting summary CSVs** — `_write_calc_mcf_lb_phase_metric_summaries`
  reads CSV columns by label, not by trial, so the wide aggregate works
  unchanged.
