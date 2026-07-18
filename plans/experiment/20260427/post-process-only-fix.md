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
serialized via `dataclasses.asdict` + `routix.io.dump_yaml` (the same
`PrettyKeyDumper` used by existing `*_statistics.yaml` /
`*_mcf_lb_diagnostic.yaml` artifacts; multi-line `error` tracebacks
survive cleanly via block scalars).

**Atomic write**: dump to `<ins_name>_instance_result.yaml.tmp` first, then
`os.replace` to the final name. SIGKILL/OOM mid-write must not leave a
partial YAML that crashes the next POST_PROCESS_ONLY load. The loader
ignores any `.tmp` siblings.

**Schema versioning**: include `_schema_version: 1` as the top-level YAML
key alongside the field dict. Future breaking changes get a clean branch
point in the loader. Cost: 1 line; value: high.

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

No new types needed for serialization, but two design points must land in
the first implementation:

- **Forward-compat projection (loader)** — day-one requirement.
  `InstanceResult(**load_yaml(path))` breaks both on unknown keys (older
  manifest before a field was removed/renamed) and missing keys (older
  manifest before a field was added). Loader must project: drop keys not
  in `dataclasses.fields(InstanceResult)`, fall back to field
  default/`None` for missing ones. Day-one cost: small; retroactive cost:
  a migration script. Do it now.
- **Enum coercion (dumper)**. `dataclasses.asdict` does **not** unwrap
  enums; `work_status` is a string today only because
  `_post_run_process_inner` passes `.value` explicitly (line 271).
  `_persist_run_artifacts` must keep that contract — write a small helper
  `_to_serializable(d)` that maps enum values to `.value` before
  `dump_yaml`, so future enum fields don't silently break safe-dumping.

### Multi-instance / multi-scenario layer (mostly no change)

`FFcDDWMultiInstanceRunner.post_run_process()` already just collects
`self.results` (list of InstanceResult). `MultiInstanceConcurrentRunner`
(routix) calls each single-instance runner's `run()`; in POST_PROCESS_ONLY
that path now correctly produces real `InstanceResult`s loaded from disk,
so nothing above the single-instance runner needs structural change.

**One small addition**: in POST_PROCESS_ONLY, if every per-instance
`_load_instance_result` raises (e.g. wrong dir specified, or pre-manifest
old run), the multi-scenario aggregation will silently produce empty
output. Add a fail-fast at scenario aggregation time: if **all**
`InstanceResult`s carry only the error placeholder (no real fields),
raise `RuntimeError("no instance manifests found in <dir> — was this dir
created before the manifest feature, or is the path wrong?")`. Triggered
once at scenario level, not per-instance, so transient single-instance
errors don't false-fire.

`FFcDDWMultiScenarioRunner.run()` (reporting.py:211) and `post_run_process`
(line 236) likewise need no logic changes — they just consume
`InstanceResult`s and feed the reporter.

### Reporter side

No structural change required once `InstanceResult`s are real in
POST_PROCESS_ONLY mode. Two safety improvements worth bundling:

1. `_write_summary_csv` currently `path.unlink()`s before any rows are
   written, then appends row-by-row (lines 388, 427 of `reporting.py`).
   This pattern destroys the prior CSV both on empty input AND on
   mid-loop crashes. **Fix: write to a sibling tmpfile, then `os.replace`
   to the final path** when the loop finishes cleanly. The unlink
   disappears entirely; race/crash classes both go away; the empty-input
   case naturally writes a header-only CSV that the pivot stage already
   early-returns on.
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
   runner should still pass. Add round-trip tests covering:
   - Multi-line `error` traceback (newlines, special chars, indentation
     — verifies block-scalar round-trip).
   - `mcf_lb_diagnostic=None` and `mcf_lb_diagnostic={"foo": {"bar": 1}}`
     (nested dict).
   - Forward-compat: load a manifest YAML dict that **lacks** a field
     (simulating a future `InstanceResult` with a new field) and assert
     the loader fills with default/None; load one that has an **extra**
     unknown key and assert it's dropped without raising.
   - Atomic write: create a `<ins_name>_instance_result.yaml.tmp` file
     manually and assert the loader does NOT pick it up.

6. **Atomic write end-to-end**: FULL_RUN, then `kill -9` a worker mid-run.
   Re-run with POST_PROCESS_ONLY on the same dir. Crashed instance must
   either have a complete manifest (write happened before kill) or no
   manifest file (`os.replace` did not run); never a partial YAML.

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

All three originally listed questions resolved during plan review:

- ~~routix per-instance working_dir under POST_PROCESS_ONLY~~ —
  **resolved**: `routix/runner/single_instance_runner.py:68-77` always
  resolves `working_dir = output_dir / ins_name` regardless of mode, so
  no adapter needed.
- ~~Manifest format (YAML vs JSON)~~ — **resolved**: YAML via
  `routix.io.dump_yaml` (the same `PrettyKeyDumper` used by the existing
  `*_statistics.yaml` / `*_mcf_lb_diagnostic.yaml` artifacts). Multi-line
  `error` tracebacks survive cleanly via block scalars.
- ~~Forward-compat for new InstanceResult fields~~ — **resolved**:
  promoted to §Design `InstanceResult field coverage` as a day-one
  requirement.
