# Plan: `build_full_sch_from_last_stage_only_sch` subroutine + attribute rename

## Context

After `single_pass_last_stage_only_sch_from_mcf_lb` (or any sibling step) runs,
the controller holds a partial schedule on `self.last_stage_cp_sat_solution`
where only the last stage is filled. There is currently no path that turns
that partial schedule into a feasible full schedule outside of the integrated
`run_mcf_lb_4` flow (whose Phase 3 does exactly that via reverse-dispatch +
unflip on `phase2.last_stage_only_schedule`).

We want a standalone controller step that takes the partial last-stage
schedule and produces a full dispatched FFcSchedule, registers it as an
incumbent, and emits the standard Gantt artifacts. To avoid duplicating
Phase 3 logic, the reverse-dispatch core in `phase3_dispatch.py` is extracted
into a pure helper that both `run_phase3` and the new step call. The new step
does NOT chain into Phase 4 (profile-fix CP-SAT) — that stays as a separate
concern (SRP).

The user also asked to rename `self.last_stage_cp_sat_solution`
(`FFcDDWSolution | None`) to `self.last_stage_only_sol` (same type) so the
attribute name describes "the partial last-stage solution" without leaking the
CP-SAT origin (it's also populated by NEH-CP and the integrated run, not just
CP-SAT). The persisted artifact filenames (`*_last_stage_cp_sat_schedule.yaml` →
`*_last_stage_only_schedule.yaml`, `*_last_stage_cp_sat_gantt.png` →
`*_last_stage_only_gantt.png`, `last_stage_cp_sat_obj.csv` →
`last_stage_only_obj.csv`) and the corresponding artifact `kind` keys, the
`InstanceResult.last_stage_cp_sat_obj` field, and the
`_write_last_stage_cp_sat_obj_csv` writer method are renamed in lockstep so
the public surface is consistent. The diagnostic field
`MCFLBDiagnostic.last_stage_cp_sat_sec` is NOT renamed — it specifically
records CP-SAT solver elapsed time inside `phase2_last_stage`, regardless of
the wrapping flow.

## Critical files to modify / create

**Modify:**

- `src/ffc_ddw_sum_et/algorithm/mcf_lb/phase3_dispatch.py`
  - Extract reverse-dispatch core into a new public helper
    `reverse_dispatch_full_schedule(...)`.
  - Refactor `run_phase3` into a thin wrapper that calls the helper and
    mutates `MCFLBDiagnostic`.
- `src/ffc_ddw_sum_et/orchestration/controller_core.py`
  - Rename attribute `last_stage_cp_sat_solution` → `last_stage_only_sol`
    (type still `FFcDDWSolution | None`).
- `src/ffc_ddw_sum_et/orchestration/controller.py`
  - Rename four assignment sites (lines 548, 653, 831, 1079) and two docstring
    references (lines 506, 614, 958).
  - Add new step method `build_full_sch_from_last_stage_only_sch`.
- `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py`
  - Lines 285, 332: rename `last_stage_cp_sat_solution` getattr reads to
    `last_stage_only_sol`.
  - Line 290: artifact key `last_stage_cp_sat_schedule` → `last_stage_only_schedule`.
  - Lines 59, 333, 388: `InstanceResult.last_stage_cp_sat_obj` field and its
    consumers → `last_stage_only_obj` (also update the field's docstring on
    lines 60-66).
- `src/ffc_ddw_sum_et/orchestration/reporting.py`
  - Line 509 call site, line 785 method definition: rename
    `_write_last_stage_cp_sat_obj_csv` → `_write_last_stage_only_obj_csv`.
  - Line 786 docstring, lines 799/805 `ir.last_stage_cp_sat_obj` reads, line
    811 `artifact_path("last_stage_cp_sat_obj_csv")` → updated to new names.
  - Line 1239: artifact key `last_stage_cp_sat_schedule` →
    `last_stage_only_schedule`.
- `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`
  - Lines 24-25: kind/file_template `last_stage_cp_sat_schedule` →
    `last_stage_only_schedule`, file `{instance_name}_last_stage_only_schedule.yaml`.
  - Lines 30-31: comment text.
  - Lines 42-43: kind/file_template `last_stage_cp_sat_gantt_png` →
    `last_stage_only_gantt_png`, file
    `{instance_name}_last_stage_only_gantt.png`.
  - Lines 64-65: kind/file_template `last_stage_cp_sat_obj_csv` →
    `last_stage_only_obj_csv`, file `{run_id}_last_stage_only_obj.csv`.
- `tests/orchestration/test_artifact_layout_overlay.py`
  - Line 48: artifact key string `last_stage_cp_sat_schedule` →
    `last_stage_only_schedule`.
- `docs/algorithms/run_mcf_lb.md` and `docs/algorithms/run_mcf_lb_ko.md`
  - Update prose references to attribute and YAML filename.
- `docs/io/20260429_artifact_manager.md`
  - Lines 75, 103, 202-203, 590, 643: update sample filenames and kind names.
- `main.py`
  - Point `CONFIG_PATH` at the new debug config.

**Create:**

- `metadata/20260502/build_full_sch_debug_config.yaml` — debug scenario:
  `apply_lb_by_mcf` → `single_pass_last_stage_only_sch_from_mcf_lb` →
  `build_full_sch_from_last_stage_only_sch`.
- `plans/20260502/build_full_sch_from_last_stage_only_sch.md` — copy of this
  plan into the project repo (per project convention).

## Existing functions to reuse

- `MixedDispatcher.get_best_mixed_schedule_by_sequence` and
  `FFcDDWParameters.reverse_stages` —
  `src/ffc_ddw_sum_et/algorithm/dispatcher/mixed.py`,
  `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py`. The reverse-dispatch
  helper is just a thin wrapper around these (already correct in
  `run_phase3`).
- `FFcSchedule.iter_operations_on_stage`, `FFcSchedule.add_ops_times_2_mc`,
  `FFcSchedule.as_reversed`, `FFcSchedule.makespan` —
  `src/ffc_ddw_sum_et/solution/ffc_schedule.py:174,303,315,395`.
  `makespan` returns max end on the last stage's machines, exactly the value
  Phase 3 needs to flip times against.
- `compute_weighted_earliness_tardiness` —
  `src/ffc_ddw_sum_et/solution/objectives.py`. Used to score the dispatched
  schedule for the `obj_value` field.
- `FFcDDWSolutionManager.register` /
  `FFcDDWSubroutineControllerCore.solution_manager` —
  `src/ffc_ddw_sum_et/orchestration/solution_manager.py`. Existing path for
  registering full incumbents.

## Implementation outline

### 1. Extract `reverse_dispatch_full_schedule` in `phase3_dispatch.py`

New pure helper that takes only what it needs. Defaults derived from
`instance` and the input schedule when not explicitly supplied.

```python
def reverse_dispatch_full_schedule(
    instance: FFcDDWParameters,
    last_stage_only_schedule: FFcSchedule,
    *,
    last_stage_id: str | None = None,
    last_stage_only_makespan: int | None = None,
    job_2_pos: dict[str, int] | None = None,
    machine_then_job: bool = False,
    logger: logging.Logger | None = None,
) -> Phase3State | None:
    """Reverse-dispatch + unflip; returns Phase3State or None on failure.

    No diagnostic side effects — caller mutates the diagnostic if needed.
    """
    last_stage_id = last_stage_id or instance.stage_id_list[-1]
    if last_stage_only_makespan is None:
        last_stage_only_makespan = last_stage_only_schedule.makespan
    if job_2_pos is None:
        job_2_pos = {j: i for i, j in enumerate(instance.job_id_list)}
    # ... body identical to current run_phase3 lines 62-117 (single-stage
    # short-circuit, build reversed_seed, MixedDispatcher.run, unflip,
    # weighted-ET on the unflipped schedule). Build last_stage_end_map by
    # reading end times via iter_operations_on_stage (no need for
    # ls_j_i_2_end).
```

The `last_stage_end_map` (line 72-74 of the current `run_phase3`) currently
reads from `phase2.ls_j_i_2_end`; we replace it with a fold over
`last_stage_only_schedule.iter_operations_on_stage(last_stage_id)`, since
that map is also derivable from the schedule itself. This drops the helper's
dependency on `Phase2State`.

### 2. Refactor `run_phase3` into a thin wrapper

`run_phase3` keeps the same public signature (Phase 4 still calls it via
`run_mcf_lb_4`) but internally:

1. Sets `diagnostic.single_stage = (instance.stage_count == 1)`.
2. Records `t_disp = time.monotonic()`.
3. Calls `reverse_dispatch_full_schedule(...)` with the values pulled from
   `phase1`/`phase2`.
4. On `None`: returns `None` (warning is logged inside the helper).
5. On success: sets `diagnostic.dispatch_sec`, `diagnostic.dispatched_obj`,
   `diagnostic.reached_phase = "dispatched"`; returns the Phase3State.

### 3. Rename `last_stage_cp_sat_*` → `last_stage_only_*`

Three coupled renames, applied in lockstep. The CSV column inside the
generated `last_stage_only_obj.csv` (header `objValue`) is unchanged — only
the file basename changes.

**3a. Controller attribute** — type stays `FFcDDWSolution | None`:
- `controller_core.py:72` declaration `last_stage_cp_sat_solution` →
  `last_stage_only_sol`.
- `controller.py:548,653,831,1079` assignments.
- `controller.py:506,614,958` docstring references.
- `ffcddw_single_instance_runner.py:285,332` `getattr` reads.
- `reporting.py:786` docstring.
- `docs/algorithms/run_mcf_lb*.md` prose.

**3b. Artifact kind / filename**:
- Layout entry `last_stage_cp_sat_schedule` →
  `last_stage_only_schedule`, file
  `{instance_name}_last_stage_only_schedule.yaml`.
- Layout entry `last_stage_cp_sat_gantt_png` →
  `last_stage_only_gantt_png`, file
  `{instance_name}_last_stage_only_gantt.png`.
- Layout entry `last_stage_cp_sat_obj_csv` → `last_stage_only_obj_csv`,
  file `{run_id}_last_stage_only_obj.csv`.
- Update every `layout.artifact_path("last_stage_cp_sat_*", ...)` call site
  (runner line 290, reporting line 811, reporting line 1239,
  test_artifact_layout_overlay.py line 48).

**3c. `InstanceResult` field + writer method**:
- `last_stage_cp_sat_obj` → `last_stage_only_obj` on
  `ffcddw_single_instance_runner.py:59` (with docstring update),
  reporter reads `reporting.py:799,805`, and runner assignment
  lines 333,388.
- `_write_last_stage_cp_sat_obj_csv` → `_write_last_stage_only_obj_csv` at
  `reporting.py:509,785` (definition + caller).

**NOT renamed (intentional)**:
- `MCFLBDiagnostic.last_stage_cp_sat_sec` — that field specifically records
  the CP-SAT solver wall-clock time inside `phase2_last_stage`, regardless of
  the surrounding flow. Renaming would mislabel what it measures.

### 4. New controller step `build_full_sch_from_last_stage_only_sch`

```python
def build_full_sch_from_last_stage_only_sch(
    self,
    machine_then_job: bool = False,
) -> SubroutineReport:
    """Build a full dispatched FFcSchedule from
    ``self.last_stage_only_sol.schedule`` via reverse-dispatch + unflip
    (Phase 3 of the MCF-LB pipeline applied standalone).

    Pre-condition (else ``ValueError``): ``self.last_stage_only_sol`` is set
    by a prior step (``single_pass_last_stage_only_sch_from_mcf_lb``,
    ``neh_cp_last_stage_only_sch_from_mcf_lb``, ``run_last_stage_cp_sat_lb``,
    or ``run_mcf_lb_4``).

    Side effects:
      - Registers the dispatched schedule as a full incumbent
        (``self.solution_manager.register``).
      - Appends ``4_last_stage_only_schedule_flipped``,
        ``5_dispatched_schedule_before_unflipping``, and
        ``6_dispatched_schedule`` to ``self.mcf_lb_phase_schedules`` (numbered
        to match ``run_mcf_lb_4``'s phase-3 outputs so reporter Gantt sort
        order is consistent across paths).

    Returns:
      ``SubroutineReport`` with ``obj_value`` = dispatched weighted ET,
      ``obj_bound`` = ``0.0`` (the step itself does not compute a global LB;
      the MCF LB is reported separately by ``apply_lb_by_mcf``).
    """
    if self.last_stage_only_sol is None:
        raise ValueError(
            "build_full_sch_from_last_stage_only_sch requires "
            "self.last_stage_only_sol; run a step that populates it first."
        )
    start_elapsed = time.monotonic()
    state = reverse_dispatch_full_schedule(
        self.instance,
        self.last_stage_only_sol.schedule,
        machine_then_job=machine_then_job,
        logger=self.logger,
    )
    elapsed = time.monotonic() - start_elapsed
    if state is None:
        return SubroutineReport(elapsed_time=elapsed, obj_value=None, obj_bound=0.0)

    if state.last_stage_only_schedule_flipped is not None:
        self.mcf_lb_phase_schedules.append(
            ("4_last_stage_only_schedule_flipped",
             state.last_stage_only_schedule_flipped)
        )
    if state.dispatched_schedule_before_unflipping is not None:
        self.mcf_lb_phase_schedules.append(
            ("5_dispatched_schedule_before_unflipping",
             state.dispatched_schedule_before_unflipping)
        )
    self.mcf_lb_phase_schedules.append(
        ("6_dispatched_schedule", state.dispatched_schedule)
    )

    report = SubroutineReport(
        elapsed_time=elapsed,
        obj_value=state.dispatched_obj,
        obj_bound=0.0,
    )
    self.solution_manager.register(
        report,
        FFcDDWSolution(
            schedule=state.dispatched_schedule,
            obj_value=state.dispatched_obj,
            obj_bound=0.0,
        ),
    )
    return report
```

Imports added to `controller.py`:
```python
from ffc_ddw_sum_et.algorithm.mcf_lb.phase3_dispatch import (
    reverse_dispatch_full_schedule,
    run_phase3,
)
```

### 5. Debug config + `main.py`

`metadata/20260502/build_full_sch_debug_config.yaml`:

```yaml
run_mode: FULL_RUN
benchmark_dir: benchmarks/PRA2017/large
ins_index_source: benchmarks/PRA2017/pra2017_hybrid_match.csv
ins_index: [ 0, 1, 2, 3 ]
bks_table_csv_path: benchmarks/PRA2017/pra2017_bks_table.csv

output_dir: output/20260502

instance_worker_cnt: 48
draw_gantt: true
painter_thread_cnt: 48

scenarios:
  - name: build_full_sch_debug
    timelimit: 300.0
    output_subdir: build_full_sch_debug
    subroutine_flow:
      - method: apply_lb_by_mcf
        draw_heatmap: false
      - method: single_pass_last_stage_only_sch_from_mcf_lb
        job_priority: "1_rj_prmp_rel_dev"
        placement_priority: "contrib"
        pf_method: "PF1"
        solver_thread_cnt: 1
        total_tl: 0.01nc
        log_cp_search_progress: false
      - method: build_full_sch_from_last_stage_only_sch
```

`main.py:26`:
```python
CONFIG_PATH = Path("metadata/20260502/build_full_sch_debug_config.yaml")
```

## Verification

1. `uv run ruff check` — clean.
2. `uv run ruff format` — apply formatting.
3. `uv run pytest tests/ -q` — all tests pass. The existing
   `test_run_mcf_lb_registers_dispatch_incumbent` should still pass since
   `run_phase3`'s public behavior is unchanged.
4. New unit test (in `tests/algorithm/mcf_lb/` or `tests/orchestration/`):
   given a small toy instance and a hand-built last-stage-only `FFcSchedule`,
   `reverse_dispatch_full_schedule` returns a `Phase3State` whose
   `dispatched_schedule` covers every `(job, stage)` pair and whose
   `dispatched_obj` equals
   `compute_weighted_earliness_tardiness(dispatched_schedule, instance)`.
5. New integration test in `tests/orchestration/test_controller.py`:
   build a controller, manually populate `self.last_stage_only_sol` with a
   feasible last-stage schedule, call
   `build_full_sch_from_last_stage_only_sch()`, and assert:
   - `solution_manager.get_incumbent()` is set,
   - the registered schedule covers every `(j, i)`,
   - `report.obj_value == compute_weighted_earliness_tardiness(...)`,
   - `report.obj_bound == 0.0`,
   - `mcf_lb_phase_schedules` got the three expected appends (or two when
     `stage_count == 1`).
6. End-to-end: `uv run python main.py` on `ins_index: [0, 1, 2, 3]`. For each
   instance verify the output subdir contains:
   - `<ins>_solution.json` (full dispatched schedule),
   - `<ins>_schedule.yaml` + `<ins>_gantt.png`,
   - `<ins>_last_stage_only_schedule.yaml` (partial, dumped by runner),
   - `<ins>_last_stage_only_gantt.png` (rendered post-run from the YAML),
   - `<ins>_4_last_stage_only_schedule_flipped*.yaml`,
   - `<ins>_5_dispatched_schedule_before_unflipping*.yaml`,
   - `<ins>_6_dispatched_schedule*.yaml`,
   - matching `*_gantt.png` for each phase YAML.
   Top-level summary CSV's `bestObj` for the scenario should equal the
   step's reported `obj_value`.
7. Spot-check one Gantt PNG: lanes are `(stage, machine)`, every job appears
   on every stage exactly once, last-stage operations of `<ins>_gantt.png`
   match those of `<ins>_last_stage_only_gantt.png` for the same instance.
8. Confirm the run-scoped CSV is now named
   `<run_id>_last_stage_only_obj.csv` (no leftover
   `<run_id>_last_stage_cp_sat_obj.csv`).
