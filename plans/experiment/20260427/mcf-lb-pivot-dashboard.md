# MCF-LB pivot dashboard from per-scenario analysis CSVs

## Context

When a run includes `run_mcf_lb` scenarios, `FFcDDWReporter._write_mcf_lb_analysis_csv()`
emits one CSV per scenario:
`output/<date>/<run_id>/<scenario_name>_mcf_lb_analysis.csv`
(columns: `insIndex, error, n, c, totalMcCount, T, R, W, mcfLb, lastStageOnlyBound,
lastStageOnlyObj, bks, dispatchedObj, profileFixObj, profileFixBound, mcfSolveSec,
lastStageCpSatSec, dispatchSec, profileFixCpSatSec`).

Today, the only cross-scenario MCF-LB rollup is the
`<run_id>_mcf_lb_last_stage_only_obj.csv` table — there is no interactive view for
sweeping (n, c, T, R, W) against any of the other MCF-LB metrics (`mcfLb`,
`profileFixObj`, solve-time fields, …). The user wants the same kind of self-contained
PivotTable.js dashboard that `_write_post_run_pivot_artifacts()` already produces for
`time%` (`<run_id>_time_p_dashboard.html`), but populated from these per-scenario
MCF-LB CSV files instead of the run-wide summary.

## Design

### New method: `FFcDDWReporter._write_mcf_lb_pivot_artifacts()`

Inserted in `src/ffc_ddw_sum_et/orchestration/reporting.py` immediately after
`_write_mcf_lb_analysis_csv()` (around line 562), and called from `generate()`
right after `self._write_mcf_lb_analysis_csv()` (line 344).

Behavior:

1. For each `sc in self.scenario_results`, build the expected analysis-CSV path
   `self.output_dir / f"{sc.name}_mcf_lb_analysis.csv"`. Skip scenarios whose CSV
   does not exist (consistent with `_write_mcf_lb_analysis_csv()` skipping
   scenarios that never ran `run_mcf_lb`).
2. If no analysis CSV exists, return silently — same convention as
   `_write_post_run_pivot_artifacts()`.
3. Read each CSV with `pandas.read_csv`, prepend a `scenarioName` column set to
   `sc.name`, then `pd.concat` them.
4. After concat, derive three extra columns on every real-scenario row:
   - `mcfLbSec = mcfSolveSec + lastStageCpSatSec + dispatchSec + profileFixCpSatSec`
     (total time across the four MCF-LB sub-steps; `min_count=1` so a row of
     all-NaN stays NaN rather than collapsing to 0).
   - `timelimit = n * c * 0.09` (matches `post_run_pivot.build_rpdf_comparison_df`'s
     `timelimit_factor`).
   - `time% = mcfLbSec / timelimit` (analogous to the rpdf side's
     `elapsedTime / timelimit`).
5. Render one self-contained HTML at
   `self.output_dir / f"{self.output_dir.name}_mcf_lb_dashboard.html"` by calling
   the existing `post_run_pivot.write_pivot_html()` with `DEFAULT_AGGREGATORS_JS`
   (numeric formatting, not percent — the MCF-LB columns are absolute objective
   values and seconds, not ratios).

### Initial pivot state

- `rows`: `["scenarioName"]` (always — only the scenario discriminator is on
  by default; user can drag `n`/`R`/etc. onto axes for finer breakdowns).
- `cols`: `[]`
- `vals`: `["lastStageOnlyObj"]` (user-chosen — the MCF-LB primal we currently
  care about, not the lower bound `mcfLb` itself).
- `aggregatorName`: `"Average"`, `rendererName`: `"Heatmap"`

### Synthetic reference rows

For each unique instance in the concatenated frame, append two synthetic rows
so the heatmap can show LB/BKS reference rows alongside the real scenarios:

- `scenarioName="mcfLb"`: copies the instance meta and sets every column in
  `_MCF_LB_REF_OBJ_COLUMNS` (lastStageOnlyBound, lastStageOnlyObj,
  dispatchedObj, profileFixObj, profileFixBound) to the instance's `mcfLb`.
- `scenarioName="bks"`: same shape but the obj columns are set to the
  instance's `bks`.

For both, `_MCF_LB_REF_BLANK_COLUMNS` (the four step times +
`mcfLbSec` + `time%`) are set to NaN so they disappear in time-metric
heatmaps. `timelimit` is left populated since it is purely an instance
property (`n*c*timelimit_factor`). The original `mcfLb` and `bks` columns are
preserved on synthetic rows so picking those as `vals` still works.

### Reused infrastructure (no new helpers needed)

- `post_run_pivot.write_pivot_html()` — already builds the self-contained HTML
  from a DataFrame + initial-state dict + aggregators-JS string.
- `post_run_pivot.DEFAULT_AGGREGATORS_JS` — numeric formatter, no percent
  scaling (the MCF-LB columns are not fractions).
- The `_PIVOT_TEMPLATE` HTML string — unchanged.

### Companion artifacts: `_pivot.html` + `_table.html`

Two follow-up reports written right after `_write_mcf_lb_pivot_artifacts()`:

1. `_write_mcf_lb_last_stage_only_obj_bks_wintie_pivot()` →
   `<run_id>_mcf_lb_lastStageOnlyObj_BKS_wintie_pivot.html`. It re-reads the
   per-scenario analysis CSVs, drops every column except the standard pivot
   dimensions plus six metrics:

- `lastStageOnlyObj`, `BKS` (renamed from `bks`),
- `RPDf` = `2 * (lastStageOnlyObj - BKS) / (lastStageOnlyObj + BKS)`,
- `win` = 1 when `lastStageOnlyObj < BKS`, else 0,
- `tie` = 1 when `lastStageOnlyObj == BKS`, else 0,
- `time%` = sum-of-4-step-secs / (`n*c*0.09`) — same calculation as the
  primary MCF-LB dashboard.

   Initial state: `rows=["scenarioName"], cols=[], vals=["win","tie"]`,
   aggregator `"Win / Tie sum"` from `WIN_TIE_AGGREGATORS_JS`, renderer
   `"Table"`. `time%`/`RPDf`/`lastStageOnlyObj`/`BKS` are still in the
   underlying data so users can drag them onto axes interactively. No
   synthetic mcfLb/bks reference rows (would always be trivial winners
   against BKS).

2. `_write_mcf_lb_last_stage_only_obj_bks_wintie_table()` →
   `<run_id>_mcf_lb_lastStageOnlyObj_BKS_wintie_table.html`. Static
   per-scenario summary HTML (no PivotTable.js, no JS at all). One row per
   scenario, columns `timePctAverage` (mean of per-instance `time%`),
   `winCount` (sum), `tieCount` (sum). Floats formatted to 4 decimal places
   via `to_html(float_format=...)`.

### Files modified

- `src/ffc_ddw_sum_et/orchestration/reporting.py`
  - `generate()` (line 339): insert `self._write_mcf_lb_pivot_artifacts()`
    right after `self._write_mcf_lb_analysis_csv()` (line 344), then
    `self._write_mcf_lb_last_stage_only_obj_bks_wintie()` after that.
  - Add new method `_write_mcf_lb_pivot_artifacts()` after
    `_write_last_stage_only_obj_summary_csv()` (after line 655) — keeps all
    MCF-LB-specific reporting code grouped together.
  - Add `import pandas as pd` lazily inside the new method (mirrors the
    pattern used by `_write_post_run_pivot_artifacts()` which lazily
    imports `.post_run_pivot`).

### Out of scope

- No new DataFrame transformations beyond stacking `scenarioName` onto the rows
  (no derived RPDf-like columns, no gap recomputations — those already exist
  in `_build_mcf_lb_extras()` and live in the run-wide summary CSV).
- No changes to `post_run_pivot.py` — the existing `write_pivot_html()` is
  sufficient.
- No additional Win/Tie or RPDf dashboards for MCF-LB; just the one heatmap
  dashboard per the user's request.

## Verification

1. Run a small MCF-LB experiment (or re-run the existing config that produced
   `output/20260426/20260427T025803_513725/`):
   ```
   uv run python -m ffc_ddw_sum_et.main metadata/<mcf_lb_config>.yaml
   ```
2. Confirm `output/<date>/<run_id>/<run_id>_mcf_lb_dashboard.html` is written
   and references the inlined CSV (grep for one `insIndex,scenarioName,…` line
   in the HTML).
3. Open the HTML in a browser, verify:
   - All scenarios appear in the "scenarioName" pill.
   - Default heatmap renders with `n` rows × `R` columns showing the chosen
     `vals` metric.
   - Switching `vals` to other MCF-LB columns (`mcfLb`, `lastStageOnlyObj`,
     `profileFixObj`, `mcfSolveSec`) updates the heatmap.
4. Run `uv run ruff check` and `uv run ruff format --check` after the edit.
5. `uv run pytest tests/orchestration/test_reporting.py` — the existing tests
   should still pass (no changes to their fixtures expected).
