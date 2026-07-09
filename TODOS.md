# TODO

Future refactor ideas and design notes that are not urgent enough to act on
today but worth capturing so future work does not re-derive the reasoning.

## Decorator for `solution_manager.register` boilerplate

Step methods in `src/ffc_ddw_sum_et/orchestration/controller.py` currently call
`self.solution_manager.register(report, solution_or_None)` explicitly at the
end. Once the number of step methods grows enough that the boilerplate becomes
a real maintenance cost, introduce a decorator (e.g. `@registers_result`) that:

- wraps a step method returning `(SubroutineReport, FFcDDWSolution | None)`
- calls `self.solution_manager.register(...)` automatically
- optionally absorbs the `start_elapsed = self.timer.elapsed_sec` /
  `elapsed = ...` boilerplate as well

Step methods that register multiple times, or register conditionally within
loops (see hybridflowshop's `repeat_while_improvement` pattern), should stay on
the explicit `self.solution_manager.register(...)` call — the decorator assumes
"1 step = 1 register" and is not a fit for those.

**Why:** YAGNI today (only 2 step methods — `run_fam`, `run_mcf_lb`) but step
count is expected to grow.

**When to act:** When step method count noticeably increases and the `register`
boilerplate is repeated with no meaningful variation.

**Alternative hook point:** If the decorator approach runs into issues with
timer/context management, consider overriding `_call_method` instead — see
hybridflowshop's `hybridflowshop/controller/controller_core.py:467` for an
existing precedent of extending the routix step hook.

**Status (2026-07-08):** Triggered. The controller now has ~20+ step methods
and ~29 `_register` call sites (was 2 when the TODO was written). The
boilerplate cost is real and the decorator is now actionable.

## Hardcoded TL (time limit) formula in analysis_long sheet

The `analysis_long` Excel sheet computes `TL = 0.09 * job_count * stage_count`
as a reference time limit. The `time%` column is then `(elapsedSec / TL) * 100`.
The coefficient `0.09` is hardcoded in `src/ffc_ddw_sum_et/orchestration/reporting.py`
in the `_write_analysis_sheets` method.

**Why:** The coefficient is experimentally determined and may need adjustment
for different instance families or solver configurations. Making it
configurable adds complexity (config key, validation, default) for a single
reporting column.

**When to act:** When the coefficient needs to change, or when multiple
teams use different TL thresholds and want to configure it per experiment.

## Decompose `io/` into typed-utility, parser, and serialization layers

`src/ffc_ddw_sum_et/io/` is currently a grab-bag, not a true I/O layer. It
mixes four distinct concerns:

- True serialization: `schedule_yaml.py`, `schedule_json.py`,
  `dump_signed_cost_heatmap_yaml`
- Parsing utility: `text_data_parser.py`
- Generic data containers: `df_manager.py`, `table_2d_manager.py`
- Type vocabulary: `typing.py` (`NumericTV`, `ScalarTV`, `numeric_type_set`)
- Algorithm-coupled visualization: `parallel_mc_cost_heatmap.py`

This grab-bag forces `parameters/` to depend on `io/` at module-load time
(`parameters/base/job_stage_p.py` inherits from `io.Table2DManager`;
`parameters/ffc_*params.py` parses streams via `io.TextDataParser`), which
together with `io/parallel_mc_cost_heatmap.py`'s runtime dependency on
`algorithm/` closes a `parameters → io → algorithm → parameters` import
cycle.

The cycle was first surfaced when `HeatmapSort` was unified as
`Union[ParamSortKey, PmPrmpSortKey]` (SSOT refactor). Two band-aids are in
place today:

- `parameters/{ffc_ddw_params,ffc_params}.py`: `TextDataParser` import
  moved to method-local in `from_pra_*` so `parameters/` no longer pulls
  `io/` at module-load.
- `algorithm/__init__.py`: emptied so a `from algorithm.X import …` does
  not amplify into the entire algorithm subtree, breaking the cycle's
  amplifier.

These keep things working but the underlying layering is still wrong.

**Proposed clean decomposition:**

```
_typing/        # leaf — NumericTV, ScalarTV, numeric_type_set
_data/          # uses _typing — DfManager, Table2DManager
_parser/        # leaf — TextDataParser
parameters/     # uses _data + _parser
algorithm/      # uses parameters
io/             # true serialization only — uses everything (schedule_yaml,
                # schedule_json)
algorithm/visualization/  # parallel_mc_cost_heatmap (algorithm-coupled)
```

With this layout there is no `io → algorithm` edge and no cycle.

**Why:** The current cycle was hidden until the SSOT refactor exposed it.
A proper decomposition removes the need for the band-aids and brings the
codebase into line with CLAUDE.md's "io subtree is an extractable
package" guidance.

**When to act:**

- A future refactor introduces a new `io ↔ domain` import that re-opens
  the cycle, OR
- An engineer is genuinely trying to extract `io/` as a separate package
  (the CLAUDE.md aspirational goal), OR
- The method-local `TextDataParser` import in `parameters/` becomes a
  recurring source of confusion or breaks under a future change.

Until then, document the band-aids in place and leave the structure as is.

## Same-meaning values managed under different names

Several diagnostics / report fields store the same logical value under
distinct names or in distinct containers. The duplication isn't broken,
but it forces consumers to know multiple aliases and risks one of them
silently going stale.

Items identified (2026-05-05, while adding the `adjust_p` knob and
collapsing the `adjust_r_*`/`adjust_p_*` triples in `MCFLBDiagnostic`
into a shared `adjust_params_*` triple):

1. **`InstanceResult.last_stage_only_obj` (top-level field) vs
   `InstanceResult.mcf_lb_diagnostic["last_stage_only_obj"]` (inside the
   diag dict).** Both store the weighted E+T of the last-stage-only
   schedule.
   - Top-level is set unconditionally from
     `controller.last_stage_only_sol.obj_value` in
     `FFcDDWSingleInstanceRunner.post_run_process` (~L322).
   - Diag-dict entry is set only by the legacy `run_mcf_lb` path through
     `algorithm/mcf_lb/phase2_last_stage.py:162`. Newer subroutines
     (`heuristic_*`, `single_pass_*`, `neh_cp_*`) do *not* update it.
   - Consumers diverge: `_write_last_stage_only_obj_csv` reads the
     top-level field; `_write_last_stage_only_obj_summary_csv` and
     `_load_mcf_lb_analysis_rows` ("lastStageOnlyObj" column) read the
     diag-dict entry. Result: depending on which subroutine produced the
     ls-only schedule, two reports can show different (or
     null-vs-non-null) values for what is conceptually the same quantity.

2. **Setter blocks for shared `adjust_params_*` triple are duplicated
   between `apply_lb_by_mcf` and `heuristic_last_stage_only_sch_from_mcf_lb`
   in `orchestration/controller.py`.** Same three-line write of
   `adjust_params_{last_stage_only_makespan, incumbent_makespan,
   makespan_delta}` plus the per-knob `*_increment_added` field exists
   at both call sites. Could be a small `_record_adjust_params_diag`
   helper on the controller (or the diagnostic itself).

**Why:** Each item works today but burdens future readers/refactors —
they must learn the alias graph, and any new subroutine has to remember
to populate every alias to keep downstream reports consistent. (#1 in
particular has actual soundness implications: two reports can disagree
because only the legacy path populates the diag-dict copy.)

**When to act:**

- Item #1: when a third report or analysis starts depending on the
  ls-only weighted E+T, OR when a bug surfaces because one of the two
  consumers reads stale/null data. Likely fix: drop the diag-dict copy
  and route both consumers through `InstanceResult.last_stage_only_obj`,
  *or* have the new subroutines also write into the diag (single source
  of truth either way).
- Item #2: when a third subroutine grows the same setter block, OR when
  any of the field names rotate again. Likely fix: extract a single
  helper.

Until then, leave as-is.

## SubroutineController file log level controllable via CLI flag

`attach_fh_to_logger` in routix ([routix/logging.py:51](../../routix/src/routix/logging.py#L51))
hardcodes the file handler level to `logging.DEBUG`, so `*_SubroutineController.log` always
captures DEBUG regardless of the `-v`/`-vv` flag.

The `quiet`/`verbose` values are already unpacked at line 140 of
`ffcddw_single_instance_runner.py`, just above the `attach_fh_to_logger` call at line 157,
so once routix exposes a `level` parameter the caller can pass it immediately.

**Changes needed:**

1. **routix** — add `level=logging.DEBUG` parameter to `attach_fh_to_logger`.
2. **`ffcddw_single_instance_runner.py`** — pass the appropriate level derived from
   `verbose`: DEBUG only for `-vv` (verbose ≥ 2), INFO otherwise.

**Why:** SubroutineController log files accumulate DEBUG-level noise on every run even
when it is not needed, inflating file size and making logs harder to scan.

**When to act:** When log file size or noise becomes a real problem, or when routix API
is being tidied up and the change can be bundled in.

## routix MultiInstanceRunner progress logging

`MultiInstanceConcurrentRunner` runs instances concurrently but the
`MultiInstanceRunner.log` only records scenario start and finish — no
intermediate progress. Checking status requires manually scanning
hundreds of instance directories.

**Improvement directions:**

1. **Background monitoring thread** (preferred) — Run a periodic thread
   inside `MultiInstanceConcurrentRunner` that logs completed count,
   mean elapsed time, and error count. Can hook into the
   `ProcessPoolExecutor` completion iterator or a shared counter with
   negligible overhead.
2. **Summary log on completion** — Add a summary line at the
   `Finished Scenario` log location with completed count, mean obj,
   mean timelimit utilization, etc.
3. **Atomic progress file** — Each instance subprocess atomically
   increments a shared `progress_count` file in the scenario directory
   on completion; the main process reads it periodically and logs.

**Why:** With 1440 instances × 2 scenarios, checking mid-run progress
requires manual file system scans because the log carries no signal.

**When to act:** When bundling routix changes (log level, dump_json),
or when progress monitoring becomes a recurring pain point.

## `routix.io.dump_json` accept formatting kwargs

`routix.io.dump_json` ([routix/io/json.py:22](../../routix/src/routix/io/json.py#L22)) is
hardcoded to `json.dump(obj, f, indent=2, default=...)`. Compact output
(`separators=(",", ":")`, `indent=None`) is impossible without bypassing the helper.

`ffc_ddw_sum_et`'s `_obj_log.json` writer (added 2026-05-07 in
`orchestration/ffcddw_single_instance_runner.py`) uses inline `json.dump(...)` to get
single-line, compact output, sidestepping the helper entirely. That's a precedent that
will recur whenever a JSON artifact wants compact / sort_keys / non-default separators.

**Changes needed:**

1. **routix** — extend `dump_json(obj, path, *, encoding="utf-8", indent=2,
   separators=None, sort_keys=False)` (or accept `**dump_kwargs` forwarded to
   `json.dump`). Keep current default (`indent=2`) so existing callers don't change.
2. **`ffcddw_single_instance_runner._save_obj_log`** — switch from inline
   `json.dump(...)` back to `dump_json(payload, out_path, indent=None,
   separators=(",", ":"))`.

**Why:** Centralize JSON serialization (encoding, default fallback for paths and
to_dict objects) instead of duplicating it inline at every compact-JSON call site.

**When to act:** When a second compact-JSON site appears in ffc_ddw_sum_et, or when
routix is being tidied up and the change can be bundled in.

## Persistent controller `obj_store` instead of merging base obj_log on RESUME

The RunMode.RESUME path (commit `feat(resume): seed tail from base run incumbent`)
reconstructs the full prefix+tail progress trajectory by **re-reading and merging**
the base run's `<ins>_obj_log.json` at persist time
(`ffcddw_single_instance_runner._merge_base_obj_log`). This works, but it duplicates
knowledge of the obj_log JSON schema (`{obj_value:{data,notes}, obj_bound:{...}}`)
across the writer (`_save_obj_log`) and the reader (`_merge_base_obj_log`), and it
exists only because FFcDDW **derives** the obj_log from `solution_manager.history` at
save time rather than accumulating it as it runs.

The sibling repo `../hybridflowshop` instead gives its controller a persistent
`obj_store` (`ObjValueBoundStore`) that accumulates across the whole run; on resume it
simply installs the base run's `obj_store` (`self.ctrlr.obj_store = self.resume_obj_store`)
and the tail appends to it — the full trajectory is present with no file re-merge and no
duplicated schema.

**Change (if acted on):** introduce a persistent obj_store on
`FFcDDWSubroutineControllerCore`, have `_register` append to it, and replace both the
derive-at-save `_save_obj_log` reconstruction and the RESUME `_merge_base_obj_log`
re-read with a single store install + dump.

**Why:** DRY (single obj_log schema owner) and removes the RESUME file-remerge
workaround. Deferred because it is a broad change to how *every* run (not just resume)
produces its obj_log — YAGNI while the merge works and obj_log handling is otherwise
stable.

**When to act:** When obj_log handling needs another feature (a third producer/consumer
of the JSON schema appears), or when aligning FFcDDW's controller with hybridflowshop's
obj_store model for other reasons.

## Upstream the RESUME flow-skip loop into routix

Both `../hybridflowshop` and FFcDDW override the controller's `run()` to implement the
`flow_resume_idx` prefix-skip (routix's base `SubroutineController.run()` is just
`_run_flow(flow); post_run_process()` and does **not** honour `flow_resume_idx` itself,
even though the *runner* classes carry the attribute and `set_flow_resume_idx`). The two
overrides are **divergent copies** of the same idea:

- FFcDDW (`orchestration/controller_core.py::run`) — slice-based
  (`flow[:idx]` skipped, `flow[idx:]` run), back-dates the clock **once** from the base
  manifest's real `elapsed_time` (in `_apply_resume`, before `run`). Cleaner + more
  correct.
- hybridflowshop (`hybridflowshop/controller/controller_core.py::run`) — enumerate with
  `idx < flow_resume_idx`, and re-times skipped no-ops with an `ElapsedTimer` accumulator
  (near-zero) to re-adjust the clock. More convoluted.

There is already a `# TODO: apply to routix` on FFcDDW's outer wall-clock wrapper in the
same method; the resume-skip belongs in the same upstream change.

**Change (if acted on):** move the flow-skip loop (honouring
`method_names_to_run_before_resume`) and the wall-clock wrapper into routix's
`SubroutineController.run(flow_resume_idx=-1)`, adopting FFcDDW's slice + single-back-date
form. Both repos then drop their override.

**Why:** single implementation shared by both repos instead of two copies that can drift;
FFcDDW's better timer handling becomes the shared baseline.

**When to act:** When routix (vendored dependency) is open for changes and the wall-clock
`# TODO: apply to routix` is being addressed — bundle the two together.
