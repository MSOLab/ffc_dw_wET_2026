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

```txt
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

## Promote `main.py` config helpers to a public API

`scripts/validate_resume_config.py` (via `import main as entrypoint`) and
`tests/test_scenario_uniqueness.py` both reach into `main`'s private helpers:
`_load_config`, `_parse_run_mode`, `_resolve_resume_dir`, and
`_validate_scenario_uniqueness`.

Re-implementing them in the validator would defeat its purpose — it exists to
answer "will `main.main()` resume where I think it will?" using the *same* code
paths the entrypoint uses, so duplicated logic that silently drifts is a worse
failure than an underscore import.

**Change (if acted on):** extract the four into a `config_resolution` module
(or promote them to public names on `main`), and update both consumers.

**Why deferred:** only two in-repo consumers, both covered by tests that fail
loudly if any of the four is renamed (`tests/scripts/test_validate_resume_config.py`
pins them transitively by importing the script). Extracting a module today buys
no decoupling that the tests do not already provide.

**When to act:** when a third consumer appears, or when any of the four grows
logic a caller wants to *override* rather than reuse.

## CSR solve_flow — additional candidate sources

`coarsen_solve_reconstruct(solve_flow=...)` (plans/experiment/20260711/csr_solve_flow.md)
v1 harvests exactly one coarse candidate per child sub-step registration
(the schedule sitting on `solution_manager.history` at each `_register`).
Two richer sources are deliberately out of v1 scope:

- **CP solution-callback snapshot ring buffer** — capture *intermediate*
  incumbents a sub-step's CP-SAT solver passes through, not just its final
  registered schedule. Port hybridflowshop
  `hfs_cp_lns.py::_FullScheduleSnapshotRecorder` (a bounded ring buffer keyed
  on distinct objective values) so every improving coarse incumbent becomes a
  candidate.
- **Dispatch / NEH side-candidates** — the pre-CP dispatch seeds and per-batch
  NEH partial schedules are additional coarse schedules that could reconstruct
  to a better original-scale schedule than the sub-step's final registered one.

**Change (if acted on):** add an opt-in snapshot recorder to the sub-algorithms
(guarded by an option flag so it stays off by default), harvest its buffer plus
the dispatch/NEH intermediates into the candidate pool, then dedup/reconstruct
as today.

**Why deferred:** user decision ("다음에 생각하자", 2026-07-11). v1's
end-of-step harvest already produces multiple candidates (mcf_lb / flip /
neh_cp / sw_cp / base-CP), and restore-all-pick-best over those is the first
thing to validate before widening the pool.

**When to act:** when the per-step winners plateau and profiling shows a
better coarse schedule was visited mid-solve but discarded, or when a
dispatch/NEH seed reconstructs better than the CP result on real instances.

## CSR solve_flow — ordering-replay restore modes + post-restore CP polish

v1 reconstructs each coarse candidate with the existing scale-up +
`make_semi_active` + `insert_idle_time` (`reconstruct_coarse_schedule`, always
flooring). hybridflowshop's coarsened-CP restore additionally offers
**ordering-replay** restore modes and a polish pass:

- `machine_sequence` — replay the coarse per-machine job order on the original
  instance (re-dispatch honouring that permutation) instead of scaling starts.
- `stage_sequence` — replay the coarse per-stage time-ordered job order.
- **post-restore CP polish** — a short CP-SAT pass warm-started from the
  reconstructed schedule to locally repair scale-up artifacts.

**Change (if acted on):** add a `restore_mode` option to the CSR step
(`scale_up` [today] | `machine_sequence` | `stage_sequence`), each producing a
candidate, plus an optional bounded polish-CP pass; reconstruct/score all and
keep the original-scale argmin.

**Why deferred:** the scale-up reconstruction already yields feasible,
competitive schedules in the smoke run; ordering-replay + polish is an
optimization on top of a working restore, and each mode multiplies the
per-candidate reconstruction cost.

**When to act:** when scale-up reconstruction is shown to leave avoidable
original-scale slack that an ordering replay or a short polish CP removes.

## CSR solve_flow — exact time_factor threading of MCF arc costs

W4 threaded `time_factor` through the MCF-LB pipeline
(`calc_mcf_lb_and_derive_full_sch(..., time_factor=...)`), but in coarse mode
(`time_factor > 1`) the pipeline **suppresses** the LB
(`final_obj_bound=None`, `lb_suppressed_by_time_factor=True`) rather than
scaling the min-cost-flow arc costs exactly. The parallel-machine preemptive
LP (`algorithm/mcf_lb/parallel_mc_pmtn.py`) builds arc/slot costs against the
original-scale due window; making those costs exactly consistent with a coarse
completion (`time_factor * C^c`) would let the child controller carry a *valid*
coarse-problem LB for its own optimality logic.

**Change (if acted on):** thread `time_factor` into the slot-cost / penalty
math in `parallel_mc_pmtn.py` (and the last-stage preemptive builder) so the
coarse MCF LB is exact on the coarse problem, then drop the
`lb_suppressed_by_time_factor` fallback.

**Why deferred:** the coarse LB is only ever used *inside* the child controller
(the parent CSR step always registers `obj_bound=None` — a coarse LB is never a
valid original-scale bound, plan §3). The suppression fallback is sound and the
child still derives its full schedule; exactness buys only earlier
optimality proofs on the coarse sub-problem.

**When to act:** when the child controller's coarse solve is time-bound and an
exact coarse LB would let CP-SAT prove optimality early enough to matter for the
overall CSR budget.

## Drop the `idle_mode` knob and hardcode `"lookahead"`

`idle_mode` (`"flooring"` | `"ceiling"` | `"lookahead"`) is configurable across
the CSR / sw_cp surface:

- `FFcSchedule.insert_idle_time(idle_mode=...)` — the three-branch
  implementation (`src/ffc_ddw_sum_et/solution/ffc_schedule.py:1814/1830/1845`),
  reachable only when `K == 1` (the `K > 1` gate at `:1751` `continue`s past it).
- `coarsen_solve_reconstruct` — controller param
  (`orchestration/controller.py:2653`; `sw_cp` / `incremental_sw_cp` carry their
  own at `:2305` / `:2478`, all defaulting to `"flooring"`) + option
  (`algorithm/coarsen_solve_reconstruct.py`), default `"flooring"`.
- `SwCpOption.idle_mode` (added 2026-07-13) + controller `sw_cp` /
  `incremental_sw_cp` params + the four `csr_*` scenario keys in
  `metadata/20260713/csr_init_methods.yaml`.

flooring/ceiling only ever existed as CSR coarse-grid experiments
(`vault/20260702_진행사항_P3.pdf`). lookahead was shown to be the per-instance
best there (p.11-12), and the sw_cp RJ-warning investigation
(`plans/experiment/20260713/sw_cp_rj_warning_investigation.md`) confirmed flooring's
coarse-grid undershoot is what produces the "left E/T on the table" warning
while lookahead avoids it. Once lookahead is validated as always-preferred, the
flooring/ceiling branches and the whole `idle_mode` plumbing become dead weight:
hardcode `"lookahead"` and delete the field/params/scenario keys.

At `time_factor == 1` all three modes are byte-identical, so hardcoding
lookahead is a no-op for every non-CSR (`K == 1`) caller.

> ⚠️ **This paragraph is false as of `c36fa5e`** — see the 2026-07-19 status
> block below. At `K == 1` lookahead now differs from flooring/ceiling on
> 132/1440 instances, so hardcoding lookahead is **not** a no-op for K==1
> callers (whose defaults are `"flooring"`). Kept here to show what the
> original premise was.

**Why:** YAGNI once one mode wins — the knob multiplies config surface (field,
validation, controller params, scenario keys) that must stay aligned
(`SwCpOption.idle_mode` and both controller-step defaults are `"flooring"` as
of the 195341 run-setting commit; the option docstring still describes
`"lookahead"` as the CSR-aligned choice). Collapsing to one mode removes that
divergence surface.

**When to act:** after `idle_mode: "lookahead"` is validated across the full
grid and higher `K` (8, 16) — i.e. flooring/ceiling are confirmed never
preferable — so removing them cannot regress any experiment. Do this together
with dropping the now-unused branches in `insert_idle_time`.

**Status (2026-07-14):** Superseded by commit `9b7ad2a` (coarse-exact
`insert_idle_time`). `idle_mode` now has **no effect at any `K`** — the `K > 1`
branch is exact (the three heuristics are bypassed) and `K == 1` is
byte-identical across modes. [**`K == 1` claim retracted 2026-07-19 — see
below.**] The knob is dead everywhere, so the
"validate lookahead as best on the coarse grid" premise above is moot; removal no
longer needs a flooring-vs-lookahead comparison, only the mechanical cleanup of
the field / params / four `csr_*` scenario keys. No-regression confirmed on the
`csr_neh_d2wp` 1440-instance full run (K=4; CpsatAdapter warning 27→0, sw_cp RJ
0→0).

**Status (2026-07-14, higher-K validated → ready to act):** The last blocker —
"higher-`K` grid unverified" — is now **cleared**. The K-sweep run
`output/20260714_csr_higher_k_validation/20260714T154426_711694/`
(`K ∈ {2,4,8,16,32}` × {`csr_neh_d2wp`, `csr_full_d2wp`}, 160 instances, 1600
runs) shows **0 warnings, 0 crashes, all feasible** — `idle_mode` is confirmed
dead at every K in scope. The "when to act" condition is satisfied; this item is
no longer deferred but a **ready mechanical cleanup** (delete the field /
controller+option params / four `csr_*` scenario keys / the flooring+ceiling
branches in `insert_idle_time`). Kept (not deleted) because it still tracks real
uncommitted code work. See `plans/experiment/20260714/coarse_exact_higher_k_validation.md`
§"결과 (실행 후)".

**Status (2026-07-19, K==1 no-op premise RETRACTED):** A deterministic
1440-instance re-derivation (`solve=False` seed-only path, so no CP-SAT noise)
compared today's code against the 2026-07-02 baseline
`analysis/20260702T013931_438875/csr_idle_modes_v4_full_20260702.csv`:

```bash
uv run python scripts/dump_csr_coarse_obj.py \
    --config metadata/20260702/csr_idle_modes_v4_config.yaml \
    --out <out>.csv --workers 96      # ~5 min, 21600 rows
```

Two findings, pulling in opposite directions:

1. **`K > 1`: confirmed fully dead.** All three modes now produce *identical*
   `coarse_obj` on 1440/1440 instances at every `factor ∈ {2,4,8,16}` (baseline:
   only 368–469/1440 agreed). Deleting flooring/ceiling is a genuine no-op here.
   Incidentally this quantifies what `9b7ad2a` bought: flooring's coarse
   objective improved by −5.59 / −5.28 / −3.76 / −1.72 % at `factor` 2/4/8/16,
   while **lookahead was byte-identical to the new exact gate at every factor
   (`max|diff| = 0`)** — the old lookahead heuristic was already *optimal* on
   v4 seeds, not merely dominant.

2. **`K == 1`: the modes have diverged.** Commit `c36fa5e` deliberately changed
   the lookahead tie-break (`block_obj(db) < …` → `<=`, "prefer the larger shift
   on objective ties"). The `K > 1` gate bypasses `idle_mode`, so this `<=`
   survives **only at `K == 1`** — exactly where lookahead is supposed to
   degenerate to flooring. Measured: the three modes agree on 1440/1440 in the
   baseline but only **1308/1440** today. The 132 divergent instances carry the
   same `coarse_obj` (it is a tie) but a different schedule, which reconstruction
   turns into a different `recon_obj`: 82 better / 50 worse, net **−0.003 %**.

   Impact: `SwCpOption.idle_mode` and both controller-step defaults are
   `"flooring"`, as is `insert_idle_time`'s signature default — so hardcoding
   `"lookahead"` **changes behaviour for every `K == 1` caller**, contrary to the
   original premise. The effect is tiny and slightly favourable, but it must be
   an explicit decision, not an assumed no-op.

**Revised recommendation:** widen the exact gate from `if K > 1:` to `K >= 1`
and delete all three branches, rather than hardcoding `"lookahead"`. This
dissolves the tie-break question entirely and fixes a second inconsistency:
`K == 1` currently still uses the `sum_e > sum_t` *weight-sum* gate, whereas
`9b7ad2a`'s comment establishes that comparing **magnitudes** is what makes
narrow due windows correct — so K==1 is presently running the weaker gate.

**When to act (updated):** the flooring/ceiling half is unblocked — finding 1
confirms they are never preferable and their removal cannot regress anything.
Before touching `K == 1`, verify two things the measurement does not settle:
(a) that the exact gate is genuinely optimal at `K == 1`, and (b) that its
`O(n²)` unit-stepping is acceptable at `n = 200, K = 1` (the coarse problem is
no longer shrunk). Note also that the seed-only dump never exercises
CP-SAT-produced coarse schedules — which is where the original "left E/T on the
table" warnings came from — so it bounds the *seed* impact only.

## `make_semi_active` — allow machine reassignment (time-sorted per stage)

`FFcSchedule.make_semi_active`
([solution/ffc_schedule.py:1024](src/ffc_ddw_sum_et/solution/ffc_schedule.py#L1024))
today left-shifts each operation **while keeping its machine assignment fixed**:
it walks each machine's existing job sequence and packs it left within that same
machine (`ffc_schedule.py:1072-1091`). The machine grouping is taken as given and
never changed.

A schedule can often be improved further by **also re-choosing which machine each
operation runs on**. The reassignment must be done per stage on operations
**sorted in time order**: within a stage, order operations chronologically (by
start, then end) and greedily place each on the earliest-available machine —
i.e. list-scheduling / earliest-machine dispatch on the time-sorted operation
list. Packing left under a *better* grouping reaches positions the fixed-assignment
left-shift cannot.

The time-sorted greedy is **already implemented** in
`build_schedule_from_op_starts`
([solution/schedule_build.py:42](src/ffc_ddw_sum_et/solution/schedule_build.py#L42)):
it sorts each stage's jobs by `(start, end, job)` and assigns the first machine
free at the op's start. `reconstruct_coarse_schedule` leans on exactly this
reassignment before calling `make_semi_active` + `insert_idle_time`, which is why
the K=1 reconstruct polish never worsened a candidate across 677 sampled
candidates (see the CSR K=1 monotonicity scan, 2026-07-15). So the building block
exists — the change is to let `make_semi_active` itself optimize the assignment
rather than inherit whatever grouping it was handed.

**Change (if acted on):** add a mode (or a sibling method) that, per stage,
re-derives the machine assignment from the time-sorted operation list before (or
jointly with) the left-shift, instead of iterating the pre-existing per-machine
sequences. Preserve the current fixed-assignment behaviour as the default so no
existing caller's semantics change.

**Why:** a fixed machine grouping can be strictly suboptimal — two operations that
"want" the same completion window can be stranded on one machine while another
sits idle. Time-sorted reassignment removes that, tightening makespan and opening
better `insert_idle_time` placements downstream. It also unifies the assignment
logic that `build_schedule_from_op_starts` and `make_semi_active` currently split.

**When to act:** when a schedule-improvement pass needs to beat what
fixed-assignment left-shift achieves (e.g. tightening makespan/E-T beyond the
current polish), or when the CSR reconstruct's dependence on
`build_schedule_from_op_starts` for reassignment is folded back into
`make_semi_active` so the two share one time-sorted assignment implementation.
