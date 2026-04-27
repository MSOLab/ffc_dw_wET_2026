# Make `POST_PROCESS_ONLY` work by splitting single-instance post-run

## Problem

`run_mode: POST_PROCESS_ONLY` currently cannot regenerate reports/dashboards
from an existing output directory.

`FFcDDWSingleInstanceRunner.run()` (in
`src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py:69-81`)
skips the controller execution when `self.mode != RunMode.FULL_RUN`, so
`self.ctrlr` is never set. `_post_run_process_inner()` (lines 110-120) then
short-circuits on `controller is None` and returns a placeholder
`InstanceResult` with all metric fields equal to `None`.

The downstream reporter (`FFcDDWReporter.generate()` in
`reporting.py:339-351`) consumes those empty `InstanceResult`s and:

- `_write_summary_csv()` (line 376) calls `path.unlink()` first then writes a
  near-empty CSV from the placeholder rows → **destroys the prior
  `*_summary.csv`**.
- `_write_post_run_pivot_artifacts()` (line 353) reads that summary CSV; with
  it gone or empty, the three RPDf/win-tie/time% dashboards either fail or
  contain no rows.
- `_write_mcf_lb_analysis_csv()` happens to be safe because it `continue`s
  when `mcf_lb_diagnostic is None`, so per-scenario `*_mcf_lb_analysis.csv`
  files survive — but the two MCF-LB dashboards depend on summary CSV
  through other paths in practice, and even when they survive, their
  initial-state defaults are baked into HTML at write time, so a defaults
  change still requires rewriting them.

The user's intent: in `POST_PROCESS_ONLY` mode, re-render every report
artifact (summary CSV, statistics YAML, Excel, all dashboards) from
the per-instance/per-scenario files already on disk, without ever touching
the controllers or solvers.

## Root-cause framing

`_post_run_process_inner()` does two responsibilities at once:

1. **Persist** raw per-instance artifacts derived from controller in-memory
   state (solution.json, schedule.yaml, obj_log.yaml, statistics.yaml/json,
   mcf_lb_diagnostic.yaml, per-phase schedule yamls, last-stage CP-SAT
   schedule yaml).
2. **Build** an in-memory `InstanceResult` dataclass that the reporter
   consumes for cross-instance/cross-scenario aggregation.

In FULL_RUN both happen on a live controller, so the coupling is invisible.
In POST_PROCESS_ONLY there is no controller, but step 2 must still produce
the same `InstanceResult` — that's only possible if step 2 reads from disk
instead of memory. The fix is to split the two and route POST_PROCESS_ONLY
through "load InstanceResult from disk".

## Design

### Single-instance runner: two-phase post-run

Replace the single `_post_run_process_inner()` with two methods:

- **`_persist_run_artifacts(controller) -> None`** — pure side-effect; writes
  every per-instance file currently produced by `_post_run_process_inner`
  (solution.json, schedule.yaml, obj_log.yaml, statistics.yaml/json,
  mcf_lb_diagnostic.yaml, phase yamls, last_stage_cp_sat_schedule.yaml).
  Plus, **at the end**, writes a new manifest file (see below). Returns
  `None`. Only called in FULL_RUN.

- **`_load_instance_result() -> InstanceResult`** — reads the manifest from
  `self.working_dir`, returns the `InstanceResult`. Raises if the manifest
  is missing (can be caught and converted to an error-bearing
  `InstanceResult` by `post_run_process()`'s outer try/except, like today).
  Called in both modes — FULL_RUN reads the manifest it just wrote;
  POST_PROCESS_ONLY reads the one from the prior run.

`post_run_process()` becomes:

```python
def post_run_process(self) -> InstanceResult:
    try:
        if self.mode == RunMode.FULL_RUN and getattr(self, "ctrlr", None):
            self._persist_run_artifacts(self.ctrlr)
        return self._load_instance_result()
    except Exception:
        # same combined-error InstanceResult fallback as today
        ...
```

`run()` keeps its current shape but the FULL_RUN-only branch is now just the
controller execution; `post_run_process()` is always called and is the only
thing that runs in POST_PROCESS_ONLY.

### New manifest artifact: `<ins_name>_instance_result.yaml`

Single file per instance, written as the **last** step of
`_persist_run_artifacts` so its presence implies every other artifact for
that instance has been written. Schema = the `InstanceResult` dataclass
serialized via `dataclasses.asdict` + `routix.io.dump_yaml`.

Why a dedicated manifest instead of reconstructing `InstanceResult` from
existing artifacts:

- `work_status` (`controller.work_status.value`) and `error` (Python
  traceback string) are **not currently persisted anywhere** — adding them
  to the manifest is one line; otherwise we'd need to add new artifacts
  for both and write boilerplate reconstruction logic.
- `method_call_counts` is in `*_statistics.yaml` but mixed with other
  statistics; manifest avoids re-parsing.
- `mcf_lb_diagnostic` is in `*_mcf_lb_diagnostic.yaml`; manifest just
  duplicates the dict — small and avoids cross-file coupling.
- Single read of one YAML in POST_PROCESS_ONLY is much cheaper and more
  robust than scanning ~10 files per instance and reconstructing fields.

The manifest is the **single source of truth** for what the reporter
consumes (per the project's single-source-of-truth architecture rule). All
other per-instance artifacts remain valid downstream inputs (Gantt
painters, schedule reloaders) but are not consulted to build
`InstanceResult`.

### `InstanceResult` field coverage

Every field on `InstanceResult` (lines 41-61 of single_instance_runner)
must round-trip through YAML. Audit:

| field | type | YAML-safe? | notes |
|---|---|---|---|
| `instance_name` | str | ✓ | |
| `elapsed_time` | float | ✓ | |
| `obj_value` / `obj_bound` | float \| None | ✓ | |
| `work_status` | str \| None | ✓ | already coerced via `.value` |
| `solution_path` | str \| None | ✓ | |
| `has_incumbent` | bool | ✓ | |
| `method_call_counts` | dict[str,int] | ✓ | |
| `report_count` | int | ✓ | |
| `first_obj_value` / `first_obj_bound` | float \| None | ✓ | |
| `error` | str \| None | ✓ | newlines preserved by yaml block scalar |
| `job_count` / `stage_count` / `machines_per_stage` | int \| None | ✓ | |
| `timelimit` | float \| None | ✓ | |
| `mcf_lb_diagnostic` | dict \| None | ✓ | already serialized via asdict today |
| `makespan` | int \| None | ✓ | |

No new types needed; `dump_yaml(asdict(instance_result), …)` and
`InstanceResult(**load_yaml(path))` should suffice. (`load_yaml` returning
unknown extra keys would break `InstanceResult(**…)`; either filter to
known fields or use `dataclasses.fields(InstanceResult)` to project — keep
forward-compat in mind.)

### Multi-instance / multi-scenario layer (no change required)

`FFcDDWMultiInstanceRunner.post_run_process()` already just collects
`self.results` (list of InstanceResult). `MultiInstanceConcurrentRunner`
(routix) calls each single-instance runner's `run()`; in POST_PROCESS_ONLY
that path now correctly produces real `InstanceResult`s loaded from disk,
so nothing above the single-instance runner needs to change.

`FFcDDWMultiScenarioRunner.run()` (reporting.py:211) and `post_run_process`
(line 236) likewise need no logic changes — they just consume
`InstanceResult`s and feed the reporter.

### Reporter side

No structural change required once `InstanceResult`s are real in
POST_PROCESS_ONLY mode. Two safety improvements worth bundling:

1. `_write_summary_csv` currently `path.unlink()`s before any rows are
   written. If `scenario_results` is somehow empty (no instances loaded),
   we'd still nuke the prior summary. Defensive change: skip the unlink
   when no instance_results would be written. Small, cheap, removes an
   entire class of foot-gun.
2. `_write_post_run_pivot_artifacts` already early-returns if
   `summary_csv` doesn't exist — fine, no change needed.

### Working-dir resolution in POST_PROCESS_ONLY

`MultiInstanceConcurrentRunner` (routix) constructs `working_dir` for each
instance based on the parent `output_dir` and the instance name. In
POST_PROCESS_ONLY, since `_resolve_post_process_dir()` (main.py:155) hands
the runner a pre-existing top-level dir, the per-instance working dirs
should resolve to the existing scenario-name/instance-name folders without
any change — verify this assumption in the implementation pass; if routix
re-creates dirs or changes naming under a non-FULL_RUN mode, that needs a
follow-up fix in routix or a glob-based fallback in the loader.

## Touchpoints

- `src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py`
  - Split `_post_run_process_inner` into `_persist_run_artifacts` and
    `_load_instance_result`.
  - Add `_write_instance_result_manifest` (or inline at end of persist).
  - Update `post_run_process` to gate persistence on FULL_RUN and always
    delegate to load.
  - Verify `RunMode` import is still the only entry point that branches on
    mode (we want the mode check localized).
- `src/ffc_ddw_sum_et/orchestration/reporting.py`
  - Skip-unlink-when-empty guard in `_write_summary_csv`.
  - No other changes.
- `main.py`
  - No code change. Confirm `_resolve_post_process_dir` already does the
    right thing (it does — yields the existing timestamped dir).

## Verification

1. **FULL_RUN baseline**. Run any small MCF-LB config end-to-end:
   ```
   uv run python main.py
   ```
   Snapshot the resulting `<run_id>_summary.csv` and the five
   `*_dashboard.html`/`*_pivot.html` files.

2. **POST_PROCESS_ONLY rerun on same dir**. Edit the config to set
   `run_mode: POST_PROCESS_ONLY` and `analysis_timestamp: <run_id>` (or
   `analysis_dir_path`). Re-run `uv run python main.py`.

3. **Diff**:
   - `<run_id>_summary.csv` must be byte-identical (or differ only in
     column ordering / float formatting if those are touched).
   - All five HTML dashboards must contain identical CSV payloads
     (`grep '<div id="output"'` and compare). Initial-state JSON in the
     HTML may differ if defaults were changed between runs — expected.
   - All `*_instance_result.yaml` files must exist post step 1 and survive
     step 2 unchanged.
   - Per-instance `solution.json`, `schedule.yaml`, `obj_log.yaml`,
     `statistics.yaml/json`, `mcf_lb_diagnostic.yaml`, phase yamls must
     all be **untouched** by step 2 (mtime check).

4. `uv run ruff check` / `uv run ruff format --check`.

5. `uv run pytest` — existing tests for the reporter and single-instance
   runner should still pass; add a minimal test that round-trips
   `InstanceResult → manifest YAML → InstanceResult`.

## Out of scope

- Reporter refactor (e.g. splitting "rebuild data" from "render
  dashboards"). The reporter is already a pure function of
  `InstanceResult`s; no change needed once those load correctly in
  POST_PROCESS_ONLY.
- Changing routix's runner mode semantics. We work within the existing
  `RunMode.POST_PROCESS_ONLY` skip-controller behavior.
- Backfilling old output dirs that lack `*_instance_result.yaml`. Those
  predate this change and require either a one-shot migration script or
  a fall-back reconstructor — defer until needed (YAGNI).
- Selectively re-running only some scenarios in POST_PROCESS_ONLY. The
  current resolver hands the whole dir; per-scenario filtering is a
  separate feature.

## Open questions

- **routix per-instance working_dir resolution under POST_PROCESS_ONLY.**
  Confirm during implementation that `working_dir` lands on the existing
  per-instance folder without re-creating or renaming. If it doesn't, a
  small adapter is needed (likely in routix).
- **Manifest format (YAML vs JSON).** YAML is consistent with existing
  per-instance artifacts (`*_statistics.yaml`, `*_mcf_lb_diagnostic.yaml`)
  and supports block scalars for multi-line `error` tracebacks cleanly.
  Lean YAML; revisit only if load-time parsing dominates.
- **Forward-compat for new InstanceResult fields.** Once a manifest from
  an older run lacks a newly added field, `InstanceResult(**fields)` will
  break. Project the loaded dict through `dataclasses.fields(InstanceResult)`
  with `default`/`default_factory` fall-back to keep old manifests
  loadable. Worth doing on day one even at small cost.
