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

2. **CSV column duplicate inside
   `{run_id}_adjust_params_by_makespan_delta.csv`.** When `adjust_r`
   fired (and `adjust_p` did not), `makespanDelta` and `rIncrementAdded`
   are guaranteed equal — `effective_r_increment = r_increment +
   makespan_delta`, so the "increment added" *is* the delta. The column
   exists because the writer also handles `adjust_p`, where the
   "increment added" is `ceil(delta * m_last / n)` and so genuinely
   differs from the delta.

3. **Setter blocks for shared `adjust_params_*` triple are duplicated
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
- Item #2: when adding a third knob in the same family, OR when a
  reader explicitly complains about the redundant column. Likely fix:
  drop `rIncrementAdded`, document that for `adjust_r` rows the value
  equals `makespanDelta`.
- Item #3: when a third subroutine grows the same setter block, OR when
  any of the field names rotate again. Likely fix: extract a single
  helper.

Until then, leave as-is.

## `_insert_jobs_at_desired_starts` desired-start floor

`src/ffc_ddw_sum_et/algorithm/mcf_lb/last_stage_only.py:519` clamps the
midpoint warm-start `desired_start` at `0`:

```python
desired_start = max((t_min + t_max - p_j) // 2, 0)
```

A tighter floor would be `job_2_release[job_id]`, since the job cannot
start before its upstream-stage release time anyway. Using the release
floor would let the midpoint placement skip release-blocked positions
directly instead of relying on the downstream `make_semi_active` /
profile-fix solve to pull operations forward to the release time.

**Why:** YAGNI today — the current `0` floor is conservative and the
downstream passes recover the release-time constraint. Switching the
floor changes both (a) which machine wins the `_interval_free` short
path and (b) the `dist_a` / `dist_b` lex-tiebreak distances, so it has
non-trivial behaviour effects worth measuring before adopting.

**When to act:** When a profiling run shows midpoint placements being
consistently overridden by `make_semi_active` because the chosen
machine had earlier free space than the release time, OR when adding a
new placement-priority mode that would benefit from a tighter window.
Likely fix: replace the literal `0` with `job_2_release[job_id]` and
re-run the `mcf_lb_init_*` benchmark sweep to confirm objective parity.
