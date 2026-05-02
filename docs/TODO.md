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
