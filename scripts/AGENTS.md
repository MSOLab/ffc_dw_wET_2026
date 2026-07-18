# Scripts

Analysis, weekly-review, and report-support scripts.

> Scripts with a leading underscore (`_aggregate_*.py`, `_fix_*.py`,
> `_insert_*.py`, …) are one-shot helpers written for a single weekly review.
> They are excluded from the standing catalog below; each file's docstring
> states its purpose and its retirement condition.

## 1. Batch Size Analysis

Statistically analyze and visualize the effect of batch size on RPDf.

### Analysis folder (`ANALYSIS_DIR`)

Each script defines an `ANALYSIS_DIR` constant at the top, and every input and
output path (input CSV, result CSV, result PNG) is resolved relative to it.
Current value:

```python
ANALYSIS_DIR = Path("analysis/diff/20260426_batch_size")
```

- A CSV name passed on the command line is used as a **basename only**, resolved
  to `ANALYSIS_DIR / <basename>`. Any leading path components are discarded.
- To move to a different analysis folder, change the `ANALYSIS_DIR` line in all
  four scripts.
- All outputs are written inside `ANALYSIS_DIR`.

### Invocation

```bash
# default (uses ANALYSIS_DIR/batch_size_5_10_15.csv)
uv run python scripts/analyze_batchsize_deep_dive.py

# custom CSV (the file must live inside ANALYSIS_DIR)
uv run python scripts/analyze_batchsize_deep_dive.py batch_size_5_10_15_20.csv
```

### Script catalog

#### analyze_batchsize_regression.py

Fits two OLS regression models (main effects / interaction) and predicts the
best batch size per scenario. Also computes the actual per-instance winner and
pairwise comparisons.

**Input**: command-line argument, or default `batch_size_5_10_15.csv` (inside
`ANALYSIS_DIR`).

**Console output**:

- Model 1 / Model 2 regression summaries
- VIF values
- Prediction-based distribution of the best batch size
- Actual per-instance winner and pairwise comparison results
- R², RMSE validation metrics

**File output** (inside `ANALYSIS_DIR`):

- `batch_size_regression_recommendations.csv` — prediction-based recommendation
- `batch_size_actual_winner.csv` — actual winner

**Use for**: quick exploratory analysis of the batch-size effect — whether the
interaction term is significant, and which parameters matter.

---

#### analyze_batchsize_deep_dive.py

Goes beyond regression: difference regression, per-slice ANOVA, interaction
decomposition, and model diagnostics. The output prefix is derived automatically
from the input filename, so it applies to 3-way (5/10/15), 4-way
(5/10/15/20), and other configurations alike.

**Input**: command-line argument, or default `batch_size_5_10_15.csv` (inside
`ANALYSIS_DIR`).

**Analysis stages**:

- **Section 0**: load data, pivot, compute pairwise batch-size differences
- **Section 0.5**: fit Model 1 (main effects) and Model 2 (interaction)
- **Section 1**: Difference Regression — regress `diff_avsb ~ params` to find
  which parameters explain the gap between batch sizes
- **Section 2**: Slicing Analysis — ANOVA + Tukey HSD post-hoc per parameter
  value
- **Section 3**: Interaction Effect Decomposition — decompose interaction
  effects via Model 2 predictions (significance judged by z-score)
- **Section 4**: Recommendation Table — prediction-based recommendation over the
  full parameter grid, plus an R×n matrix
- **Section 5**: Model Diagnostics — nested F-test, Breusch-Pagan test, residual
  analysis

**File output** (inside `ANALYSIS_DIR`; `prefix` auto-derived from the input
filename):

- `{prefix}_model2_summary.csv` — Model 2 coefficients
- `{prefix}_diff_descriptive.csv` — difference distribution statistics
- `{prefix}_diff_regression.csv` — difference-regression coefficients
- `{prefix}_slicing_analysis.csv` — per-slice ANOVA / Tukey results
- `{prefix}_interaction_effects.csv` — interaction decomposition
- `{prefix}_recommendations_full.csv` — recommendation over the full grid
- `{prefix}_recommendation_matrix.csv` — R×n matrix
- `{prefix}_model_diagnostics.csv` — model diagnostic metrics

---

#### visualize_batchsize_evidence.py

Visualizes the output of `analyze_batchsize_deep_dive.py`. Answers "why is one
batch size different from another" with a 4-panel summary and a 6-panel detail
figure.

**Input**: the CSVs produced by `analyze_batchsize_deep_dive.py` (auto-loaded
from `ANALYSIS_DIR`; default prefix `batch_size_5_10_15`).

**File output** (inside `ANALYSIS_DIR`):

- `{prefix}_evidence_overview.png` — 4-panel summary
  - pairwise box plot (difference distribution)
  - per-slice heatmap
  - R×n recommendation matrix
  - actual-winner bar chart
- `{prefix}_evidence_detail.png` — 6-panel detail
  - difference histogram (threshold + mean/median marked)
  - key-statistics text summary
  - RPDf vs R and RPDf vs T prediction curves
  - ANOVA F-stat bar chart
  - per-slice box plot

---

#### visualize_batchsize_15vs20.py

For the 4-way experiment (5/10/15/20), focuses on the bs=15 vs bs=20 gap. Answers
"is a larger batch size always better?"

**Input**: the CSVs produced by
`analyze_batchsize_deep_dive.py batch_size_5_10_15_20.csv` (auto-loaded from
`ANALYSIS_DIR`).

**File output** (inside `ANALYSIS_DIR`):

- `{prefix}_evidence_15vs20.png` — 4 panels focused on bs15 vs bs20
  - `diff_15vs20` histogram
  - key statistics (difference, win rate, diminishing returns)
  - prediction curves for all four batch sizes (vs R)
  - R×n recommendation matrix
- `{prefix}_evidence_all.png` — 6 panels comparing all batch sizes

### Execution order

```plaintext
analyze_batchsize_deep_dive.py  →  visualize_batchsize_evidence.py
      (writes the CSVs)              (reads the CSVs, draws the figures)
```

`analyze_batchsize_regression.py` runs standalone as a quick exploratory script.
`visualize_batchsize_15vs20.py` requires 4-way data.

## 2. Results Index Pipeline (weekly review)

Collects the per-instance `<timestamp>_summary.csv` files of the RUNs cataloged
in `docs/reviews/<date>_weekly_experiments.md` into a single long-form CSV, then
aggregates that CSV into a `(RUN, scenario)` mean table.

### build_results_index.py

Consolidates the 23 RUNs of `docs/reviews/20260428_weekly_experiments.md`. The
RUN list and the output path (`analysis/results_index_20260428.csv`) are
hardcoded in the script.

**Output**: `analysis/results_index_20260428.csv` — one row per
`(RUN, scenario, instance)`, carrying run-level provenance, the original summary
columns, and a BKS / RPDf join.

```bash
uv run python scripts/build_results_index.py
```

### build_results_index_20260505.py

The 35-RUN version for `docs/reviews/20260505_weekly_experiments.md`. RUN 15
lives on the `hjt5950x` machine and has no local `summary.csv`, so it is skipped
on purpose.

**Output**: `analysis/results_index_20260505.csv`.

```bash
uv run python scripts/build_results_index_20260505.py
```

### aggregate_results_index.py

Reads the long-form CSV produced by either build script above and aggregates it
into a `(RUN, scenario)` summary. The `metric` field reads `bestObj` when a
full-schedule wET exists, and `mcfLb (no incumbent)` otherwise (e.g. for
`mcf_lb_only`); in the latter case `mean_RPDf` is `None`.

Every instance is included in the aggregation. RPDf is the symmetric form
`(bestObj - BKS) / ((bestObj + BKS) / 2)`, bounded in [-2, +2], so `BKS=0` needs
no special handling: `BKS=0 < bestObj` pins RPDf at exactly `+2` (the maximum
symmetric distance) and contributes to the mean as a real signal, while
`bestObj = BKS = 0` is defined as `0.0` by the build script.

**Output**:

- `<input>_agg.csv` — flat table, one row per `(RUN, scenario)`
- `<input>_agg.json` — the same content as JSON

```bash
uv run python scripts/aggregate_results_index.py analysis/results_index_<date>.csv \
    [--top 10] [--bottom 5]
```

## 3. Report Rebuild

### build_subroutine_flow_charts.py

Redraws the two subroutine-flow HTML artifacts for an existing run directory,
using the same writers as the live reporting pipeline. Useful for refreshing the
figures from unchanged data after editing chart code.

**Input**: a run directory (e.g. `output/20260507/20260507T191425_860284`). Reads
each scenario's `<instance>_obj_log.json` plus the manifest directly.

**Output (overwritten in place)**:

- `<run_dir>/<scenario>/summary_method_rpdf_and_norm_time_scatter.html`
- `<run_dir>/<run_id>_multi_scenario_subroutine_flow_comparison.html`

```bash
uv run python scripts/build_subroutine_flow_charts.py <run_dir> [-v]
```

The benchmark CSVs default to `benchmarks/PRA2017/`. For a different family,
override with `--bks-csv` / `--hybrid-match-csv` / `--instance-table-csv`.

### build_cross_run_flow_chart.py

Compares scenarios drawn from several run directories in one chart. Unlike
`build_subroutine_flow_charts.py`, which is limited to a single run, this script
takes N scenario directories directly and draws only one multi-scenario flow
comparison HTML (no per-scenario scatter, so it writes nothing into the source
run directories).

**Input**: N scenario directories (positional), each of the form
`<run_dir>/<scenario_name>/`. Reads the `<instance>_obj_log.json` and
`<instance>_instance_result.yaml` inside their instance subfolders directly.

**Output**: a single HTML file. Default path is
`analysis/<YYYYMMDDTHHMMSS_uuuuuu>/cross_run_flow.html` (timestamp generated at
run time). Override with `--output`.

**Labels**: default is `<run_id>/<scenario_name>`
(= `<scenario_dir.parent.name>/<scenario_dir.name>`), so identical scenario names
across runs are distinguished automatically. Pass `--labels` with as many custom
labels as positional arguments to override.

```bash
# default (output goes to analysis/<timestamp>/cross_run_flow.html)
uv run python scripts/build_cross_run_flow_chart.py \
    output/20260507/20260507T191425_860284/mcf_lb_best_neh_cp_best_base_cpsat \
    output/20260507/20260507T192835_679926/mcf_lb_best_neh_cp_best_base_cpsat

# override output path + custom labels
uv run python scripts/build_cross_run_flow_chart.py \
    --output analysis/my_cross_run_flow.html \
    --labels run-A run-B \
    output/.../scenarioA output/.../scenarioB
```

The benchmark-CSV defaults and override flags (`--bks-csv` /
`--hybrid-match-csv` / `--instance-table-csv`) are the same as in
`build_subroutine_flow_charts.py`.

### build_merged_run_dir.py

Assembles a synthetic run directory that merges scenarios owned by several runs,
so `RunMode.POST_PROCESS_ONLY` can regenerate the **full** run-level report set
across them. `build_cross_run_flow_chart.py` spans runs but draws only the flow
comparison; this route additionally yields the method-mean scatter, the win/tie
and RPDf dashboards, `*_rpdf_comparison.csv`, and `*_report.xlsx`.

**Input**: N scenario directories (positional), each `<run_dir>/<scenario_name>`,
optionally suffixed `=<label>` to rename the scenario. Labels must be unique —
they become the subdir names, and `main._validate_scenario_uniqueness` rejects
duplicates. Instance sets must match unless `--allow-instance-mismatch` is given.

**Output**: `<--dest>/<run_id>/` holding one subdir per label, each filled with
**symlinks** to the source instance dirs, plus a transplanted
`<run_id>_artifact_layout.yaml` (its templates are keyed on `{run_id}`, so any
source run's stamp works). Symlinks keep the merged dir at a few MB; the reporter
only reads instance dirs, so the source runs are never written to.

```bash
uv run python scripts/build_merged_run_dir.py \
    --dest output/20260711_merge_base_p25_p50 \
    output/20260704/20260704T164349_114896/s0_c5_base \
    output/20260707_sw_cp_tl_p25_p50/20260708T014624_039386/s0_c5_p25 \
    output/20260707_sw_cp_tl_p25_p50/20260708T014624_039386/s0_c5_p50
```

Then point a POST_PROCESS_ONLY config at the printed run dir
(`analysis_dir_path`), give it one `scenarios` entry per label carrying that
scenario's `subroutine_flow` copied from the source run's config, and run
`uv run python main.py --config <config>`. A worked example lives in
`metadata/20260711/merged_base_p25_p50.yaml`.

> Keep `draw_gantt: false` and `draw_progress_plot: false`. Those painters write
> *inside* instance dirs, which would reach through the symlinks and pollute the
> source runs. Every other artifact lands in the merged dir.

## 4. Other Analysis Utilities

### analyze_bestobj_randomness.py

A one-shot script (for the 2026-04-23 experiment) that measures `bestObj`
variability across N repeated runs of the same scenario. Reads a list of
timestamp directories from `/tmp/new_dirs.txt` (one per line, relative to
`output/20260423/`) and aggregates `bestObj` per
`(instanceName, scenarioName)` pair.

**Output**: `output/20260424/bestobj_randomness_summary.csv`

```bash
# write /tmp/new_dirs.txt first
uv run python scripts/analyze_bestobj_randomness.py
```

> The input path (`/tmp/new_dirs.txt`), the base dir (`output/20260423`), and the
> output path are all hardcoded. Reusing this on another batch requires editing
> the code.

### analyze_dispatch_sweep.py

Standing post-hoc analysis of dispatch-sequence sweep results
(`*_rpdf_comparison.csv`). Ranks dispatch methods (scenarios) and computes the
oracle performance of method combinations (taking the per-instance best).

**Metrics (`--metric`; both minimized)**:

- `rpdf` (default) — `RPDf_BKS_data`, relative deviation from BKS. **Scale-free**,
  so every instance contributes equally → the fair metric for "is this method
  better overall?"
- `obj` — `bestObj`, the absolute weighted E+T objective. Useful from a
  total-cost perspective, but the objective magnitude scales with instance size
  (n ≤ 200), so **the mean is dominated by large instances**. Read it as "total
  cost", not "per-instance quality".

Questions answered:

- **(1) best single method**: the method minimizing the mean metric, overall and
  within the `--t` and `--t --r` slices.
- **(2) best pair / (3) best triple**: for each subset of size `k`, computes
  `mean_instance( min_{m∈S} metric )` and selects the most complementary method
  set. Set sizes come from `--combo-size` (default `2 3`).

**Method filter (`--methods PREFIX`)**: restricts candidates to methods whose
`scenarioName` starts with PREFIX. E.g. `--methods sd_` → simple-dispatch only
(useful for a like-for-like comparison against a paper that uses a single decode
family).

```bash
# overall, then the T=0.6 and (T=0.6, R=0.2) slices — same command, new flags (RPDf)
uv run python scripts/analyze_dispatch_sweep.py output/<date>/<run_dir>
uv run python scripts/analyze_dispatch_sweep.py output/<date>/<run_dir> --t 0.6
uv run python scripts/analyze_dispatch_sweep.py output/<date>/<run_dir> --t 0.6 --r 0.2

# absolute objective, simple-dispatch only, top-10
uv run python scripts/analyze_dispatch_sweep.py output/<date>/<run_dir> \
    --metric obj --methods sd_ --top 10
```

> The positional argument is either a run directory (timestamp folder) or a
> direct path to a `*_rpdf_comparison.csv`. The functions (`mean_by_method`,
> `metric_matrix`, `best_combos`, `oracle_value`, `marginal_contribution`) are
> importable and reusable from notebooks or other scripts. To score a specific
> baseline combination (e.g. the 2017 paper's triple) on the same footing as the
> best one, use `oracle_value(mat, combo)`.

---

### analyze_kappa_sweep.py

Merges SW-CP TL-policy scenarios scattered across several RUNs into **a single κ
sweep** and compares mean RPDf per slice. As long as the scenario names do not
collide and the instance grids match, concatenating the `<ts>_summary.csv` files
yields one sweep (a name collision aborts with an error).

`kappa_*` scenarios use size-proportional TL
(`non_time_fixed_op_time_limit_multiplier`); `p50` / `p60` / `p70` are percentile
TL baselines. The latter do not lie on the κ axis, so they are drawn as
**reference lines**, not as sweep points.

The BKS join and the RPDf computation are imported from `build_results_index.py`
so they cannot drift from the weekly-review pipeline. Like
`aggregate_results_index.py`, this **scores every instance** (no exclusions).

**Slices**: by default all three are produced — `all` (1440) / `T=0.6` (480) /
`T=0.6,R=0.2` (160). Passing `--t` / `--r` computes only that one combination
(flag names match `analyze_dispatch_sweep.py`).

> **Facet caveat**: `timelimit` is derived as `0.09 × n × c`, so `TL=45` mixes
> `(n=50, c=10)` with `(n=100, c=5)`. Small multiples are always faceted by
> `(n, c)`, never by `timelimit`.

**Output** (`--outdir`, default `analysis/kappa_sweep_20260710/`):

- `kappa_sweep_long.csv` — one row per `(run, scenario, instance)`
- `kappa_sweep_by_scenario.csv` — one row per `(slice, scenario)`
- `kappa_sweep_by_scenario_nc.csv` — one row per `(slice, scenario, n, c)`
- `kappa_sweep_by_slice.png` — one panel per slice, κ vs mean RPDf
- `kappa_sweep_rpdf_<slice>.png` — `(n, c)` 8-panel small multiples, per slice

```bash
# merge two runs and analyze all three slices
uv run python scripts/analyze_kappa_sweep.py \
    output/20260709_sw_cp_tl_test/20260710T003128_565779 \
    output/20260710_sw_cp_tl_kappa_0.005/20260710T165804_500924

# a single custom slice
uv run python scripts/analyze_kappa_sweep.py <run_dir> ... --t 0.6 --r 0.2
```

> The conclusion is recorded in
> `plans/experiment/20260705/sw_cp_tl_policy_investigation.md` §3.4: no κ beats `p60`, and
> at `T=0.6` `kappa_0.006` is significantly *worse*. The overall mean alone makes
> `kappa_0.005` look like the winner, so **always read this per slice**.

### analyze_csr_tl_scaling_sweep.py

Reproduces the **entire** `## 결과 (실행 후)` section of
`plans/experiment/20260714/csr_tl_scaling_sweep.md` in one invocation — the CSR budget-
fraction sweep over `f ∈ {5,10,15,20,30}%` (+ `f=25%` from the prior fixed-budget
run) × `K ∈ {1,2,4,8}` × two init flows (`csr_full_d2wp` / `csr_neh_d2wp`).

Reads the run's `<ts>_rpdf_comparison.csv` and uses its precomputed
`RPDf_BKS_data` verbatim (symmetric RPD), so it cannot drift from
`post_run_pivot.py`. optimality gap is **not** used for ranking (coarse
`obj_bound` is loose at K≥2; K=1 is the sole exception and is reported separately).

Eight blocks, all printed to stdout: (1) f→RPDf curve per (flow, K), (1.5)
**equal-budget setting comparison** — mean RPDf% by setting × f, read *down* each
f-column (same budget → fair setting ranking; `*` marks the column winner), for
three slices `overall / T=0.6 / (T,R)=(0.6,0.2)` (the meaningful comparison; best
f is trivially the largest f and is *not* it), (2) best f + marginal Δ per +5%p
(kept only as a budget-efficiency read), (3) T-level decomposition, (4)
`csr_full` vs `csr_neh` paired win/tie/loss by K/f/T, (5) sanity gate re-check
(two invariant warnings + Traceback/AssertionError file counts), (6) budget-
starvation (`no feasible`) counts by scenario, (7) K=1 optimality
(`obj_value==obj_bound`) counts. Blocks 1–4 read the two CSVs; 5–7 scan the sweep
run dir's `*.log` (via `grep`) and per-instance `*_instance_result.yaml`.

```bash
# defaults to the 2026-07-14 sweep + its 25% baseline run
uv run python scripts/analyze_csr_tl_scaling_sweep.py

# explicit run dirs (pass 'none' as the 2nd arg to omit the f=25 column)
uv run python scripts/analyze_csr_tl_scaling_sweep.py \
    output/20260714_csr_tl_scaling_sweep/<ts> \
    output/20260714_csr_full_grid_k248/<ts>
```

> Conclusion (plan §결론): coarsening hurts at equal budget — `K=1` (no
> coarsening) is the uniform winner across all f and all T; best combo
> `csr_full_d2wp_k1 @ f=30%` (mean −5.60%). init-flow is K-dependent (full wins
> K≤2, neh wins K≥4). best f = 30% (curve not yet saturated).

---

## 5. Experiment Config Validation

### validate_resume_config.py

A dry-run validator that confirms a `RunMode.RESUME` config **resumes at the
intended point** before an experiment is launched. It reproduces exactly the
checks `main.main()` performs between config load and runner construction.

It imports the `main` module under the alias `entrypoint` and calls the four
helpers (`_load_config`, `_parse_run_mode`, `_resolve_resume_dir`,
`_validate_scenario_uniqueness`) **directly rather than reimplementing them**, so
the validation logic cannot silently drift from the real entrypoint
(→ see "Promote `main.py` config helpers to a public API" in `TODO.md`).

Checks performed:

1. **`resume_dir` resolution** — an explicit scenario directory, or
   `latest:<scenario_name>`.
2. **flow prefix validation** — derives `flow_resume_idx` per scenario. On a
   mismatch it prints **only the keys that actually differ**, not the entire
   default-filled step dict.
3. **`flow_resume_idx >= step_cnt` guard** — catches the accident where
   `resume_dir` points at a *case* run instead of the base run, causing **every
   step to be skipped**. Exits with code 1 in that case.

With `--check-artifacts`, it additionally verifies that every target instance has
a base incumbent (`<ins>_solution.json` + `<ins>_instance_result.yaml`) under
`resume_dir`. Without this check, the problem only surfaces after the experiment
starts, as a `RuntimeError` from
`FFcDDWMultiInstanceRunner._load_resume_data`.

```bash
# prefix / guard checks only (fast)
uv run python scripts/validate_resume_config.py metadata/20260710/sw_cp_tl_kappa_0.005.yaml

# also check that base incumbents exist
uv run python scripts/validate_resume_config.py <config> --check-artifacts
```

Exits 0 if the resume is valid, 1 otherwise. A non-RESUME config returns 0
without running any checks.

> Tests: `tests/scripts/test_validate_resume_config.py`. It imports the script by
> path, so renaming any of the four helpers above surfaces immediately as a test
> failure.
