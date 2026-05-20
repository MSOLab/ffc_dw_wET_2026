# Step 4 Plan — extract `calc_mcf_lb_and_derive_full_sch` to algorithm side; nested r1/r2 artifact layout

## Context

This is the second of the two commits planned in
`plans/20260509/algo_folder_extraction_thin_wrappers.md`. **Step 3 has
already landed** (commit `a38f2b0`,
`refactor(mcf-lb): extract leaf algorithm functions; thin controller wrappers`):

- Three leaf algorithm functions are now pure (no controller state, no
  adjust-by-gap knobs):
  - `apply_lb_by_mcf` (in `algorithm/mcf_lb/lb_last_stage_pmtn.py` —
    same module as `solve_mcf_lb`, `McfLbResult`, `MCFLBStopRequested`,
    `ApplyLbByMcfResult`)
  - `heuristic_last_stage_only_from_mcf_lb` (in
    `algorithm/mcf_lb/last_stage_sch_builder.py`) — gained
    `p_increment` kwarg, builds the augmented instance internally
  - `build_full_sch_from_last_stage_only_sch` (in
    `algorithm/mcf_lb/full_sch_builder.py`) — alongside
    `reverse_dispatch_full_schedule` and `Phase3State`. Returns
    `BuildFullSchResult` with a flat `intermediate_schedules` list of
    `(label, schedule)` pairs whose labels are unprefixed (`lastS_only_before_rs`,
    `lastS_only_after_rs`, `lastS_only_flipped`, `fullS_before_unflip`,
    `fullS_after_unflip`, `fullS_after_sa_iti`).
- The matching three controller wrappers (`apply_lb_by_mcf`,
  `heuristic_last_stage_only_sch_from_mcf_lb`,
  `build_full_sch_from_last_stage_only_sch`) became thin translation
  layers: they still accept the five `adjust_*_by_full_sch_*` /
  `adjust_r_by_half` kwargs and `_register_report=False`, resolve them
  into plain `p_increment` / `r_increment` from controller state, then
  call the pure algorithm function. They still record snapshots into
  `self.mcf_lb_phase_schedules` with numbered prefixes
  (`1_mcf_preemptive`, `2_*`, `3_*`, `4_*`–`9_*`) — the
  `_BUILD_FULL_SCH_LABEL_TO_INDEX` dict in `controller.py` does the
  build-step prefix mapping.
- The composite (`calc_mcf_lb_and_derive_full_sch`) is **unchanged** in
  step 3 — it still lives inline in `controller.py` and still calls
  `self.apply_lb_by_mcf(_register_report=False)`, etc., wrapped in
  `self.temporarily_extended_context("r1")` / `("r2")` so the recorded
  full names carry an `r1` / `r2` round marker that
  `MCF_LB_ROUND_RE` later parses out for CSV emission.

**What step 4 does** is the rest of the refactor:

1. Move the composite body into a pure algorithm function.
2. Switch the on-disk artifact layout for phase schedules to a nested
   per-round directory tree, with a new `makespan_delta.yaml` sidecar
   in the r1 directory.
3. Strip dead controller plumbing whose only callers were the
   composite (the five `adjust_*` kwargs on the per-step wrappers,
   `_register_report`, `adjust_ref_full_sol`, `_build_full_sch_core`,
   the `temporarily_extended_context` round marker, the
   `MCF_LB_ROUND_RE` / `MCF_LB_LOCAL_NAME_RE` regexes).

## Design decisions (agreed with user before context reset)

### Algorithm-side composite owns delta math

`apply_lb_by_mcf` and `heuristic_last_stage_only_from_mcf_lb` already
take plain `p_increment` / `r_increment` (no delta-from-makespan logic
inside). The composite computes:

```
makespan_delta  = r1_full_sch_makespan - r1_ls_only_pmtn_makespan   # signed; recorded BEFORE skip
r2_p_increment  = ceil(makespan_delta * m_last / n) if adjust_p else 0
r2_r_increment  = ceil(makespan_delta / 2)         if adjust_r else 0
```

R2 runs only when `(adjust_p or adjust_r) and not stop_predicate() and
s1 is not None and makespan_delta > 0`. Otherwise the result records a
`r2_skip_reason` in `{"no_adjust", "stop_guard", "s1_none", "delta_le_0"}`.

### Artifact layout — nested r1/r2 directories

```
progress/<instance>/calc_mcf_lb_and_derive_full_sch/
├── r1/
│   ├── 1_mcf_preemptive.json
│   ├── 2_lastS_only_from_mcf_lb_before_sa_iti.json
│   ├── 3_lastS_only_from_mcf_lb_after_sa_iti.json
│   ├── 4_lastS_only_after_rs.json
│   ├── 5_lastS_only_flipped.json
│   ├── 6_fullS_before_unflip.json
│   ├── 7_fullS_after_unflip.json
│   ├── 8_fullS_after_sa_iti.json
│   └── makespan_delta.yaml         # 6 fields, see below
└── r2/                             # only when r2 ran
    ├── 1_mcf_preemptive.json
    ├── 2_lastS_only_from_mcf_lb_before_sa_iti.json
    ├── 3_lastS_only_from_mcf_lb_after_sa_iti.json
    ├── 4_lastS_only_before_rs.json   # r2-only
    ├── 5_lastS_only_after_rs.json
    ├── 6_lastS_only_flipped.json
    ├── 7_fullS_before_unflip.json
    ├── 8_fullS_after_unflip.json
    └── 9_fullS_after_sa_iti.json
```

Confirmed with the user: **8 JSONs in r1 and 9 in r2** (matching
`MCF_LB_R1_LABEL_ORDER` / `MCF_LB_R2_LABEL_ORDER`); the user explicitly
rejected the earlier "3 files per round" sketch.

`r1/makespan_delta.yaml` carries six fields (mirrors today's columns
in `*_adjust_params_by_makespan_delta.csv`):

```yaml
lastStageOnlyPmtnMakespan: <int>
lastStageOnlyMakespan: <int>
incumbentMakespan: <int>
makespanDelta: <int>             # signed; can be negative
pIncrementAdded: <int|null>      # null when r2 did not run
rIncrementAdded: <int|null>
```

`pIncrementAdded` / `rIncrementAdded` are written even when `adjust_p`
or `adjust_r` is False (value is `0` in that case) so consumers can
distinguish "skipped" (null) from "ran with zero adjust" (0).

### Per-step controller wrappers lose adjust kwargs entirely

User chose **"전부 제거 (Recommended)"** for the per-step adjust
kwargs and `_build_full_sch_core`. Step 4 deletes:

- `adjust_p_by_full_sch_and_last_stage_only_pmtn_sch`,
  `adjust_r_by_full_sch_and_last_stage_only_pmtn_sch`,
  `adjust_p_by_full_sch_and_last_stage_only_sch`,
  `adjust_r_by_full_sch_and_last_stage_only_sch`,
  `adjust_r_by_half` — five kwargs each on
  `controller.apply_lb_by_mcf` and
  `controller.heuristic_last_stage_only_sch_from_mcf_lb`.
- `_register_report` parameter on both (composite no longer goes
  through these wrappers).
- `_build_full_sch_core` private helper.
- `self.adjust_ref_full_sol` controller state slot (declared in
  `controller_core.py`).
- The `_ensure_makespans` closures inside each wrapper.
- The `_log_effective_release_stats` calls keyed on `"effective_*"`
  (now just `r_multiplier` / `r_increment` directly).

## Current touchpoints (from Explore agent results)

### ArtifactLayout (overlay YAML)

`metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`, lines 24–57,
defines four MCF-LB artifact kinds. The two relevant ones for step 4:

```yaml
- kind: mcf_lb_phase_schedule       # currently flat:  progress/<inst>/{phase_name}.json
  zone: progress
  file_template: "{phase_name}.json"
- kind: phase_gantt_png             # report:          report/<inst>/{phase_name}_gantt.png
  zone: report
  file_template: "{phase_name}_gantt.png"
```

The two CSV kinds (`mcf_lb_phase_obj_csv`, `mcf_lb_phase_makespan_csv`)
**stay flat** — the user explicitly said to leave them next to where
they are today.

`FFcArtifactLayout` is at
`src/ffc_ddw_sum_et/orchestration/artifact_layout.py:23-76`. It loads
the overlay and exposes `artifact_path(kind, **scope)` and
`find_artifacts(kind, **scope)`.

### Runner emission

`src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py:337-369`
iterates `controller.mcf_lb_phase_schedules` and calls
`layout.artifact_path("mcf_lb_phase_schedule", phase_name=name, **scope)`
to resolve each output path, then dumps the schedule (compact=True).
The composite controls visibility via `emit_phase_schedules`: when
`False` (default) it `clear()`s `self.mcf_lb_phase_schedules` before
returning so the runner sees nothing.

### Phase Gantt PNG rendering

`src/ffc_ddw_sum_et/orchestration/reporting.py:1530-1551` discovers
phase JSONs via `layout.find_artifacts("mcf_lb_phase_schedule",
**scope)`, then for each found JSON resolves a paired
`phase_gantt_png` path using the JSON file's stem as `phase_name`. The
new nested layout means `find_artifacts` must recurse into
`r1/` and `r2/` (or the renderer must walk those subdirs explicitly),
and the resulting PNG path needs to land alongside its source JSON
(probably under `report/<inst>/calc_mcf_lb_and_derive_full_sch/r1/...`).

### Per-instance phase metric CSVs

`controller._emit_calc_mcf_lb_phase_metrics_csv`
(`controller.py:1121-1186`) currently scans
`self.mcf_lb_phase_schedules`, regex-extracts the round (r1/r2) and
local label, and writes flat
`mcf_lb_phase_obj_csv` / `mcf_lb_phase_makespan_csv` rows. After step
4, this method should iterate the algorithm composite's
`r1_intermediate_schedules` / `r2_intermediate_schedules` directly —
no regex parsing needed. Output CSV format and on-disk path stay
identical so the per-scenario summary aggregator
(`reporting.py:773-875`, `_write_calc_mcf_lb_phase_metric_summaries`,
keyed on `(round, label)`) keeps working unchanged.

### Phase label orders

`src/ffc_ddw_sum_et/orchestration/mcf_lb_phase_labels.py` —
`MCF_LB_R1_LABEL_ORDER` and `MCF_LB_R2_LABEL_ORDER` are still used by
the CSV emitter and the per-scenario summary. **Keep them.** The two
regexes (`MCF_LB_LOCAL_NAME_RE`, `MCF_LB_ROUND_RE`) become unused once
the CSV emitter is rewritten — delete them.

### Diagnostic dataclass

`src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py` —
`CalcMcfLbAndDeriveFullSchDiagnostic` already has the fields the
composite needs: r1/r2 sub-results as flat fields,
`makespan_delta`, `r2_ran`, `r2_skip_reason`,
`r2_p_increment_added`, `r2_r_increment_added`, `final_obj`,
`final_obj_bound`, `elapsed_sec`. **No schema changes expected.**

### Composite caller path

`src/ffc_ddw_sum_et/orchestration/controller.py:1231-1490` is the
current inline composite. It runs r1 inside
`self.temporarily_extended_context("r1")`, computes the signed
`makespan_delta` after r1, then optionally runs r2 inside
`temporarily_extended_context("r2")`. It registers exactly once per
call. Stop guards either return `_make_stop_report` (before r1
produces a full sched) or `_finalize(r1, s1)` (registers the r1
result). All of this logic moves to the algorithm side except the
register / artifact-path-resolution / diagnostic-population shell.

## Step 4 — implementation plan

Order matters; do these in roughly this sequence so each commit can
land independently if needed.

### 1. Algorithm-side composite

New file: `src/ffc_ddw_sum_et/algorithm/mcf_lb/calc.py`
(or a similarly-named module — `calc_mcf_lb_and_derive_full_sch.py` is
also fine; `calc.py` matches the verb-first naming in
`apply.py`/`heuristic.py` style but those got renamed in step 3 — the
new project convention is descriptive nouns
(`lb_last_stage_pmtn.py`, `last_stage_sch_builder.py`,
`full_sch_builder.py`), so prefer something like
`composite_solver.py` or `calc_full_sch_with_lb.py`. Confirm with
user.)

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CalcMcfLbAndDeriveFullSchResult:
    # Final picks (best of r1.full / r2.full by obj_value).
    best_schedule: FFcSchedule | None
    best_obj: float | None
    final_obj_bound: float                # = r1.mcf_lb (always a valid LB on original)
    elapsed_sec: float

    # Round 1 sub-results (always populated unless r1 was stopped early).
    r1_apply: ApplyLbByMcfResult | None
    r1_heuristic: HeuristicLastStageOnlyResult | None
    r1_build_full: BuildFullSchResult | None
    # Round 2 sub-results (populated only when r2 ran).
    r2_apply: ApplyLbByMcfResult | None
    r2_heuristic: HeuristicLastStageOnlyResult | None
    r2_build_full: BuildFullSchResult | None

    # r2 trigger metadata (always populated; makespan_delta is the
    # signed raw delta recorded BEFORE the skip decision — Rep3 fix).
    makespan_delta: int | None
    r2_ran: bool
    r2_skip_reason: Literal["no_adjust", "stop_guard", "s1_none", "delta_le_0"] | None
    r2_p_increment: int | None             # what was passed; None when r2 didn't run
    r2_r_increment: int | None

    # Phase snapshots, ALREADY split per round and ALREADY numbered
    # (1..8 / 1..9). The wrapper iterates these directly without
    # rebuilding any prefix logic.
    r1_phase_schedules: list[tuple[str, MCFLBPhaseSchedule]]
    r2_phase_schedules: list[tuple[str, MCFLBPhaseSchedule]]


def calc_mcf_lb_and_derive_full_sch(
    instance: FFcDDWParameters,
    *,
    draw_pmtn_sch_heatmap: bool = False,
    heatmap_sort: HeatmapSort = "end_time",
    job_placement_priority: PmPrmpSortKey = "end_time",
    last_stage_only_placement_criteria: Literal["contrib", "dist"] = "dist",
    adjust_p: bool = False,
    adjust_r: bool = False,
    stop_predicate: Callable[[], bool] | None = None,
    logger: logging.Logger | None = None,
    r1_heatmap_yaml_path: Path | None = None,
    r2_heatmap_yaml_path: Path | None = None,
) -> CalcMcfLbAndDeriveFullSchResult:
    ...
```

Default values for `heatmap_sort` / `job_placement_priority` /
`last_stage_only_placement_criteria` **must match the current
`controller.calc_mcf_lb_and_derive_full_sch` signature** at
`controller.py:1231-1239` — `"end_time"` / `"end_time"` / `"dist"`.
Do **not** copy the values from the original
`algo_folder_extraction_thin_wrappers.md` plan (`"due2-weight-pos"` /
`"1_rj_prmp_rel_dev"` / `"contrib"`); those are stale.

The composite is responsible for building the numbered phase labels
(prefixing r1 ones `1_..8_` and r2 ones `1_..9_`) using
`MCF_LB_R1_LABEL_ORDER` / `MCF_LB_R2_LABEL_ORDER` index positions —
keep those constants in `mcf_lb_phase_labels.py`.

Stop predicate flow: matches the controller's current behavior. The
result struct should accommodate "stopped before r1.full" by leaving
`best_schedule = None`, `best_obj = None`,
`r1_build_full = None`, etc. The wrapper detects this and returns
`_make_stop_report`.

### 2. Artifact layout YAML — new kinds

Edit `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`:

```yaml
- kind: mcf_lb_phase_schedule_round
  zone: progress
  file_template: "calc_mcf_lb_and_derive_full_sch/{round}/{index}_{label}.json"

- kind: makespan_delta_yaml
  zone: progress
  file_template: "calc_mcf_lb_and_derive_full_sch/r1/makespan_delta.yaml"

- kind: phase_gantt_png_round
  zone: report
  file_template: "calc_mcf_lb_and_derive_full_sch/{round}/{index}_{label}_gantt.png"
```

(Final names are bikeshed-able but the parameter set
`{round, index, label}` is what the resolver needs.)

**Decision**: keep the old `mcf_lb_phase_schedule` kind too — other
flows (e.g. `flip_makespan_cp_phase_schedule`) coexist with it and
nothing else points at the new nested kind. The composite wrapper
emits via the new kind only.

### 3. Composite controller wrapper

Replace the inline body of `controller.calc_mcf_lb_and_derive_full_sch`
(`controller.py:1231-1490`) with:

1. `start_elapsed = time.monotonic()`
2. `c_diag = CalcMcfLbAndDeriveFullSchDiagnostic(); self.calc_mcf_lb_and_derive_full_sch_diagnostic = c_diag`
3. `result = algo_calc_mcf_lb_and_derive_full_sch(self.instance, ..., stop_predicate=self.is_stopping_condition, logger=self.logger, r1_heatmap_yaml_path=self.try_get_file_path_for_subroutine("calc_mcf_lb_and_derive_full_sch/r1/_C_heatmap.yaml") if draw_pmtn_sch_heatmap else None, r2_heatmap_yaml_path=...)`
4. Iterate `result.r1_phase_schedules` and write each to
   `layout.artifact_path("mcf_lb_phase_schedule_round", round="r1", index=<n>, label=<lbl>, **scope)` via the existing
   `dump_solution_json` / `dump_preemptive_schedule_json` switch.
   Same for r2.
5. Write `r1/makespan_delta.yaml` via `dump_yaml(layout.artifact_path("makespan_delta_yaml", **scope), {...six fields...})`.
6. Populate `c_diag` from `result` (mostly straight-through).
7. Call `self._emit_calc_mcf_lb_phase_metrics_csv(result)` (rewritten — see step 4 below).
8. Build `SubroutineReport(elapsed_time=time.monotonic() - start_elapsed, obj_value=result.best_obj, obj_bound=result.final_obj_bound)`.
9. `self._register(report, FFcDDWSolution(...))` if `best_schedule` non-null.
10. Return.

The `emit_phase_schedules` flag goes away — the new layout puts phase
JSONs in their own subdirectory, so they don't pollute the flat
progress zone, and there's no reason to suppress them.

The wrapper does **not** clear or read
`self.mcf_lb_phase_schedules` anymore. That list (and the runner's
emission of it) only matters for non-composite code paths
(direct calls to `apply_lb_by_mcf` / `heuristic_*` /
`build_full_sch_*` step methods).

### 4. CSV emitter — direct from result

Rewrite `_emit_calc_mcf_lb_phase_metrics_csv` to take the algorithm
result directly:

```python
def _emit_calc_mcf_lb_phase_metrics_csv(
    self, result: CalcMcfLbAndDeriveFullSchResult
) -> None:
    layout = self._artifact_layout
    if layout is None or self._artifact_scenario_name is None or self._artifact_instance_name is None:
        return
    per_round = {"r1": dict(_strip_index_from_labels(result.r1_phase_schedules)),
                 "r2": dict(_strip_index_from_labels(result.r2_phase_schedules))}
    # ... existing CSV writing loop using MCF_LB_R1_LABEL_ORDER / R2 ...
```

`_strip_index_from_labels` returns `{label: schedule}` after dropping
the `"<n>_"` prefix from each entry. (Or have the algorithm composite
store labels both prefixed and unprefixed in its result — caller
preference.)

Delete `MCF_LB_LOCAL_NAME_RE` and `MCF_LB_ROUND_RE` from
`mcf_lb_phase_labels.py` after this rewrite — no remaining users.

### 5. Strip dead controller-side code

In order:

- `controller.py`:
  - Remove the five `adjust_*` kwargs + `adjust_r_by_half` from
    `apply_lb_by_mcf` (lines ~441-454) and from
    `heuristic_last_stage_only_sch_from_mcf_lb` (lines ~750-763).
  - Remove the `uses_ls_only_pmtn` / `uses_ls_only_full` /
    `_ensure_makespans` / `effective_p_increment` /
    `effective_r_increment` blocks in both methods. Keep validation
    (`p_increment >= 0`, `r_multiplier >= 0`, `r_increment >= 0`).
  - Remove `_register_report: bool = True` from both signatures and
    its branches in the body. Diagnostic creation always runs.
  - Delete `_build_full_sch_core` (composite no longer calls it; the
    public `build_full_sch_from_last_stage_only_sch` already calls
    the algorithm function directly).
  - Delete the `temporarily_extended_context("r1"|"r2")` calls in the
    composite (composite is gone).
- `controller_core.py`:
  - Remove `self.adjust_ref_full_sol: FFcDDWSolution | None = None`.
- `mcf_lb_phase_labels.py`:
  - Remove `MCF_LB_LOCAL_NAME_RE` and `MCF_LB_ROUND_RE` (and adjust
    the module docstring).
- Update controller imports (no `MCF_LB_LOCAL_NAME_RE`/`MCF_LB_ROUND_RE` import).

### 6. Phase Gantt rendering

`reporting.py` Gantt-rendering loop (`reporting.py:1530-1551`):

- Add a second discovery pass for `mcf_lb_phase_schedule_round` that
  walks `r1/` and `r2/` subdirectories and renders each JSON to
  `phase_gantt_png_round` (same `{round, index, label}` scope).
- Keep the existing `mcf_lb_phase_schedule` discovery for the
  flat-emission case (non-composite step methods).

### 7. Tests

- Add `tests/algorithm/mcf_lb/test_calc.py` (or matching the chosen
  module name) covering:
  - Default flow (no adjust): `r2_ran=False`,
    `r2_skip_reason="no_adjust"`, `makespan_delta` recorded,
    `best_schedule` non-null.
  - `adjust_p=True, adjust_r=True` with a hand-picked instance where
    delta > 0 forces r2 to run: `r2_ran=True`,
    `r2_p_increment` and `r2_r_increment` match `ceil(delta*m_last/n)`
    and `ceil(delta/2)`.
  - `adjust_p=True` with delta <= 0: `r2_skip_reason="delta_le_0"`,
    `makespan_delta` still negative/zero on result.
  - Single-stage instance: still works
    (`build_full_sch_from_last_stage_only_sch` short-circuit).
  - Stop predicate fires before r1.full: `best_schedule is None`.
- Existing controller-side tests
  (`tests/orchestration/test_controller.py`) keep passing without
  edit. The Rep3 regression check
  (the `*_adjust_params_by_makespan_delta.csv` produced from
  `metadata/20260509/flip_makespan_cp_debug.yaml`) must continue to
  show `makespanDelta=-6` with empty `pIncrementAdded` / `rIncrementAdded`.

### 8. Active config sweep

The `out of scope` clause in
`plans/20260509/algo_folder_extraction_thin_wrappers.md` only excuses
historic configs (`metadata/2026042*/`,
`metadata/2026050{1..7}/`). For the active configs (anything
referenced by current scripts or smoke runs), grep for
`adjust_p_by_full_sch_and_last_stage_only_pmtn_sch`,
`adjust_r_by_full_sch_and_last_stage_only_pmtn_sch`,
`adjust_p_by_full_sch_and_last_stage_only_sch`,
`adjust_r_by_full_sch_and_last_stage_only_sch`,
`adjust_r_by_half`, and `_register_report`. Migrate any active
config that calls per-step methods with these kwargs to call the
composite instead.

### 9. Verification

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest tests/
uv run python main.py --config metadata/20260509/flip_makespan_cp_debug.yaml
```

End-to-end equivalence checks:

1. The active config's `*_adjust_params_by_makespan_delta.csv` numbers
   must match the post-step-3 baseline. Specifically, Rep3 still
   records `makespanDelta=-6` with empty `pIncrementAdded` /
   `rIncrementAdded`.
2. The active config's `summary.csv` (if any) and per-scenario
   `*_calc_mcf_lb_phase_obj_summary.csv` /
   `*_calc_mcf_lb_phase_makespan_summary.csv` numbers must be
   numerically identical to the baseline.
3. New on-disk artifacts appear at the expected paths
   (`progress/<inst>/calc_mcf_lb_and_derive_full_sch/r1/...`).
   `r2/` directory only appears for instances where r2 ran.
4. Phase Gantt PNGs (when `draw_gantt: true`) render alongside their
   source JSONs.

## Critical files (paths and rough lines)

| Path | What it does | Step 4 action |
| ---- | ------------ | ------------- |
| `src/ffc_ddw_sum_et/orchestration/controller.py:1231-1490` | composite step body | replace with thin wrapper |
| `src/ffc_ddw_sum_et/orchestration/controller.py:441-748` | `apply_lb_by_mcf` wrapper | strip 5 adjust kwargs + `_register_report` |
| `src/ffc_ddw_sum_et/orchestration/controller.py:750-1033` | `heuristic_*` wrapper | strip 5 adjust kwargs + `_register_report` |
| `src/ffc_ddw_sum_et/orchestration/controller.py:1086-1162` | `_build_full_sch_core` | delete |
| `src/ffc_ddw_sum_et/orchestration/controller.py:1121-1186` | `_emit_calc_mcf_lb_phase_metrics_csv` | rewrite to take result directly |
| `src/ffc_ddw_sum_et/orchestration/controller_core.py` | `self.adjust_ref_full_sol = None` slot | delete |
| `src/ffc_ddw_sum_et/orchestration/mcf_lb_phase_labels.py` | round + label regexes + label orders | delete the two regexes; keep the label-order tuples |
| `src/ffc_ddw_sum_et/algorithm/mcf_lb/__init__.py` | re-exports | add new composite + result |
| `src/ffc_ddw_sum_et/algorithm/mcf_lb/<new>.py` | algorithm composite | new file |
| `src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py` | `CalcMcfLbAndDeriveFullSchDiagnostic` | unchanged (already has the right fields) |
| `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml` | artifact kinds + path templates | add `mcf_lb_phase_schedule_round`, `makespan_delta_yaml`, `phase_gantt_png_round` |
| `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py:337-369` | flat phase-JSON dumper | unchanged (still serves non-composite paths) |
| `src/ffc_ddw_sum_et/orchestration/reporting.py:1530-1551` | Gantt PNG discovery | extend to walk r1/ + r2/ |
| `src/ffc_ddw_sum_et/orchestration/reporting.py:773-875` | per-scenario CSV summary | unchanged (reads CSVs which keep the same columns) |

## Open questions to confirm before coding

These were not pinned down before the context reset; ask the user if
unsure:

1. **Algorithm-side composite filename.** Step-3 conventions favor
   descriptive names (`lb_last_stage_pmtn.py`,
   `last_stage_sch_builder.py`, `full_sch_builder.py`). Suggested:
   `composite_solver.py` or `calc_full_sch_with_lb.py`. The user has
   not approved a name yet.
2. **Default values for the new artifact kind names** in the YAML
   overlay. The names in this doc (`mcf_lb_phase_schedule_round`,
   `phase_gantt_png_round`, `makespan_delta_yaml`) are placeholders.
3. **Should the YAML overlay's `mcf_lb_phase_schedule` flat kind be
   removed entirely?** It still serves the non-composite step-method
   path (e.g. someone runs `apply_lb_by_mcf` directly via
   subroutine_flow). Probably keep it. Confirm.
4. **Wrapper still pipes `emit_phase_schedules` through?** With the
   new nested layout it's redundant — phase JSONs go to their own
   subdir. Plan above removes the kwarg. Confirm with user before
   deleting; some active config might set it.

## Out of scope (carry-over from step 3)

- `metadata/2026042*/` and `metadata/2026050{1..7}/` historic configs.
  They reference deleted methods or kwargs and are kept on disk as
  historical record per agreement.
- `docs/algorithms/20260426_mcf_lb.md` and friends — archival.
