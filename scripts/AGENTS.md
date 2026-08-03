# Scripts

Analysis, weekly-review, and report-support scripts.

> Scripts with a leading underscore (`_aggregate_*.py`, `_fix_*.py`,
> `_insert_*.py`, …) are one-shot helpers written for a single weekly review.
> They are excluded from the standing catalog below; each file's docstring
> states its purpose and its retirement condition.

> **Reading `<instance>_obj_log.json`:** use
> `report.obj_log_loader.build_step_registrations` for step boundaries rather
> than parsing `obj_value.notes` by hand — see
> [`docs/artifacts/obj_log.md`](../docs/artifacts/obj_log.md) for the schema and
> the three traps (absent steps, own-output vs incumbent, dropped note-less
> series). Parsing the raw payload is a valid choice for plotting or per-point
> inner-step labels; state why in the script's docstring when you do.

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

### 20260718/analyze_p_sweep.py

Merges the SW-CP TL **capture-percentile** scenarios into one p-axis sweep, the
counterpart to `analyze_kappa_sweep.py`'s κ axis. Loading, the BKS join and the
RPDf formula are imported from that script (which in turn imports them from
`build_results_index.py`), so neither can drift.

Two regimes are handled differently because their scenario names collide:
the three `unfixed_batch_count_max=8` runs share a base incumbent and disjoint
names, so they are concatenated into one 7-percentile + κ-family sweep; the
`max=12` run repeats the same seven names, so it is loaded separately and joined
per (scenario, instance) for a paired comparison.

**Output** (`--outdir`, default `analysis/20260718_sw_cp_tl_p_u8_merge/`):

- `p_sweep_by_scenario.csv` — one row per (slice, scenario), max=8 p + κ
- `p_u8_vs_u12.csv` — one row per (slice, p), both regimes + paired win/tie/loss
- `p_sweep_u8_vs_u12.png` — mean RPDf vs p, one panel per slice, both regimes

```bash
# defaults to the four runs the merged analysis was written from
uv run python scripts/20260718/analyze_p_sweep.py

# explicit runs / a single custom slice
uv run python scripts/20260718/analyze_p_sweep.py \
    --u8-run <run_dir> --u8-run <run_dir> --u12-run <run_dir_or_csv> --t 0.6 --r 0.2
```

> Conclusion (`plans/analysis/20260718/sw_cp_tl_p25_p75_u8_fill.md`): at max=8 the
> U-shape holds across p25–p75 and **p60 is the best percentile in all three
> slices**. κ=0.005 edges p60 on the overall mean only — both `T=0.6` slices pick
> p60, so **read this per slice**. The max=8 vs max=12 comparison is confounded
> (the max=12 run is `FULL_RUN`, not a resume from the shared base).

---

### 20260719/analyze_csr_init_methods.py

Phase 1 of the CSR 3-phase analysis
(`plans/analysis/20260719/csr_triple_analysis_plan.md`): ranks 10 init scenarios
(5 init approaches × 2 NEH priorities + baselines) on the full 1440-instance
grid. Emits the overall ranking, five paired key comparisons, the **(T, R) 3×3
cell decomposition** with per-cell winners and unique-best counts, and an
**oracle portfolio table** (per-instance `min` over every 2- and 3-subset, plus
marginal contributions). Also emits the 160-instance secondary table carrying
`csr_fmm_base`.

Reads `<ts>_rpdf_comparison.csv` and uses `RPDf_BKS_data` verbatim; the loader
and the oracle helpers are imported from `analyze_dispatch_sweep.py` rather than
re-derived.

```bash
uv run python scripts/20260719/analyze_csr_init_methods.py    # analysis/20260719_csr_init/
```

> **Two caveats the numbers do not carry themselves.** (1) Every scenario is a
> single-step init flow spending ~25 % of the `0.09nc` cap, so these measure
> **init quality**, not final solution quality. (2) All `csr_*` scenarios are
> **K=4** (`metadata/20260713/csr_init_methods.yaml`, `factor: 4`) — the
> inner-flow verdict here is a K=4 verdict and reverses at K≤2 (see the K-range
> script). Portfolio numbers are **oracle bounds at k × budget**, not runnable.

### 20260719/analyze_csr_k_range.py

Phase 2 of the same analysis: mean RPDf vs coarsening factor
K ∈ {1,2,4,8,16,32} at a fixed f=25 % budget, per init flow.

Two views are kept in **separate frames on purpose**: `csr_{full,neh}_d2wp_k{2,4,8}`
exists in *both* the 1440-instance `full_grid_k248` run and the 160-instance
`higher_k_validation` run, so concatenating them would double-count K=2,4,8.
(`analyze_kappa_sweep.load_runs` cannot be used here for the same reason — it
raises on a scenario appearing in more than one run.) The script refuses any
source that does not cover its expected grid, which rejects the 2-instance
smoke-test run `20260715T175237_658738`.

```bash
uv run python scripts/20260719/analyze_csr_k_range.py         # analysis/20260719_csr_k/
```

> Conclusion (`plans/analysis/20260719/csr_init_k_budget_consolidation.md`):
> **coarsening hurts at equal budget** — K=1 wins, `full` is monotone worsening
> in K (K=1 beats K=2 by ~20 %p), `neh` is flat over K∈{1,2} then worsens, and
> K=32 is catastrophic. Best measured triple: `(csr_full_d2wp, K=1, f=30 %)`.

### 20260719/analyze_csr_equal_budget.py

The **equal-budget** read of the CSR budget sweep: at a *fixed* f, which
`(flow, K)` setting wins? Reads each f-column **vertically**, which is the
comparison that discriminates between settings — "which f is best?" is trivially
answered by the largest f measured and says nothing about the algorithm.

`analyze_csr_tl_scaling_sweep.py` already prints the setting × f table (its block
1.5); this script adds what is needed to judge how *decisive* each column is: the
winner→runner-up gap in %p, a per-instance **paired win/tie/loss** for that pair,
and a stability check over all (slice × f) columns. The paired test is not
redundant with the gap — see the T=0.6 finding below.

```bash
uv run python scripts/20260719/analyze_csr_equal_budget.py   # analysis/20260719_csr_budget_sweep/
```

> Conclusion: `F_k1` (`csr_full_d2wp`, K=1) wins **all 18 (slice × f) columns**,
> so the setting choice is budget-independent over f ∈ [5, 30] %. **But the
> dominance erodes with budget on hard instances**: on T=0.6 the gap falls
> 17.70 → 2.17 %p from f=5 to f=30, and at f=30 `F_k1` wins the mean while
> *losing* the per-instance count **233/0/247**. Quote that exception alongside
> the headline; a crossover past f=30 % is plausible and untested.

### 20260719/analyze_csr_vs_baseline.py

Answers "is CSR better than the initialization we already had?" — the best CSR
setting (`csr_full_d2wp`, **K=1**) against the existing methods (`mcf_lb_fmm`,
`neh`, and their budget-matched `_25p` variants) at **matched budget**.

Phase 1 (`analyze_csr_init_methods.py`) already compares these, but only against
CSR at **K=4**, which Phases 2–3 showed is far from best. This script redoes it
at the K that actually wins. The baseline family spans f ≈ 0/10/25/30 on its own,
so CSR is met head-to-head at three of its six budget points and **no new
baseline sweep is needed**; f = 5/15/20 have no comparator and are skipped.

Budget parity is verified on measured `elapsedTime`, not assumed. Emits the
overall/T=0.6/(0.6,0.2) comparison plus a full **(T, R) cell view**, which is the
test that matters: at K=4 `mcf_lb_fmm_25p` wins 3 of 9 cells outright.

```bash
uv run python scripts/20260719/analyze_csr_vs_baseline.py   # analysis/20260719_csr_init/
```

> Conclusion: **CSR K=1 beats every existing init method at matched budget, in
> every slice, and in all 9 (T,R) cells** — `mcf_lb_fmm` −40.33 %p,
> `mcf_lb_fmm_25p` −49.67, `neh_25p` −35.83, `neh` −34.79. The 3-cell loss at
> K=4 is a K artifact and does not survive. Starved to **f=5 %** it still
> dominates the mcf_lb family in all 9 cells at 4.8× less budget — but against
> the NEH family it then **loses the whole R=1.0 column** (4/9 cells, paired
> 628/83/729 vs `neh`). Its −2.67 %p mean there is a big win at R=0.2 cancelling
> a big loss at R=1.0, so that diagonal comparison is parity, not victory.

---

### 20260725/analyze_csr_winner_source.py

Answers **"how deep into the inner `solve_flow` did the budget actually let the
search get?"** — the algorithmic-depth diagnostic that objective means hide.

`coarsen_solve_reconstruct` in `solve_flow` mode logs exactly one summary line
per instance into `<scenario>/<instance>/*_SubroutineController.log`:

```plaintext
coarsen_solve_reconstruct[solve_flow]: candidates=3 deduped=3 dropped=0 \
winner_source=2-run_flip_makespan_cp_from_incumbent winner_coarse_obj=... ...
```

`winner_source` is `<step_idx>-<method>` (with a `.<detail>` tail for per-batch
registrations, e.g. `4-incremental_sw_cp.1-batch_002`), so **the step index is
the depth the flow reached before the budget ran out**. The script scans those
lines across a run, joins instance metadata, and pivots by scenario.

**Input**: a run directory. Metadata (`n, c, T, R, RPDf_BKS_data, elapsedTime`)
is joined from the run's `<ts>_rpdf_comparison.csv` through
`pra2017_hybrid_match.csv` (instance dir name → `insIndex`); the loader is
imported from `analyze_dispatch_sweep.py` so the join cannot drift. A run with
no rpdf CSV still works, but slice flags and the RPDf block are unavailable.

**Console output**: three blocks — winner_source counts by scenario, candidate
count (mean/min/max) by scenario, and mean RPDf by scenario × winning depth.

**File output** (`--outdir`, default `analysis/<run_id>_winner_source/`):

- `winner_source_long.csv` — one row per (scenario, instance)
- `winner_source_by_scenario.csv` — the scenario × depth pivot

```bash
# whole run
uv run python scripts/20260725/analyze_csr_winner_source.py <run_dir>

# the (n=200, c=10) slice of the lastsemi full grid, csr_* scenarios only
uv run python scripts/20260725/analyze_csr_winner_source.py \
    output/20260724_lastsemi_fullgrid/20260724T155337_875856 \
    --scenario csr_k --n 200 --c 10
```

> **Finding that motivated the script** (the command above): at **f=5 %** on the
> 180 `(n=200, c=10)` instances, K=1 lands on
> `run_flip_makespan_cp_from_incumbent` 127/180 times and reaches
> `incremental_sw_cp` only 10 times, while K=8 reaches `incremental_sw_cp`
> 119/180. **Coarsening buys algorithmic depth** — and yet K=8 still loses badly
> on RPDf (65.0 vs 26.6), so the depth gained does not pay for the resolution
> lost. The starvation is specific to f≤5 %: at f=10/15 % K=1 also reaches
> `incremental_sw_cp` on ~160/180. Used as the depth channel of
> `plans/experiment/20260725/coarsening_short_budget_crossover.md`.

> Scenarios whose CSR step runs the **legacy non-`solve_flow` path** (e.g. the
> `solve=False` dispatch-only arms) log no such line and are absent from the
> pivot — the script exits with an error if that leaves nothing to report.

---

### 20260726/analyze_crossover_ladder.py

Answers **"is there a budget f at which coarsening (K>1) beats K=1?"** — the
objective half of the sub-5 % crossover analysis, whose depth half is
`20260725/analyze_csr_winner_source.py`.

Reads the run's `<ts>_rpdf_comparison.csv`, parses `{arm}_k{K}[_{mode}][_f{NN}]`
scenario names, and pairs every coarsened scenario against **its own arm's and
own f's K=1 baseline** by `insIndex`, so a positive dRPDf always means coarsening
hurt. Four arms isolate the two channels: `a` (dispatch-only) and `b` (mcf_lb
only) carry resolution loss alone, `c` adds an equal-budget flip CP, `m1` runs
the full inner `solve_flow` and carries both channels.

**Two things it emits that a hand-rolled pivot tends to miss.**

1. **Fixed-mode and best-over-mode are kept separate.** The 20260724
   rounding-robustness ladder is quoted in `cumulative`; splicing a
   best-over-mode number onto it makes part of the resulting jump a mode
   artifact. `m1_ladder.csv` carries all four modes so a document never has to
   splice.
2. **The feasibility asymmetry is counted, not dropped.** A scenario that
   registers no incumbent has a NaN RPDf and vanishes from the paired
   comparison — silently, and precisely on the instances where one side has
   nothing to compare. `coarse_only_feasible` / `k1_only_feasible` count those,
   and a console block lists every scenario whose `n_paired` fell short.

**File output** (`--outdir`, default `analysis/<run_id>_crossover_ladder/`):

- `drpdf_by_mode_k.csv` — one row per (arm, f, k, mode): dRPDf, win/tie/loss,
  `n_paired`, and the two feasibility-only counts
- `arm_summary.csv` — per (arm, f, k) the best mode and the K=1 RPDf
- `m1_ladder.csv` — k=2 dRPDf vs f, per mode (the crossover ladder itself)
- `elapsed_by_scenario.csv` — mean elapsed on the (n=200, c=10) slice

```bash
uv run python scripts/20260726/analyze_crossover_ladder.py \
    output/20260725_crossover_ladder/20260726T002619_971440
```

> Conclusion (`plans/analysis/20260726/coarsening_short_budget_crossover.md`):
> **no objective crossover** — all 200 (arm, f, k, mode) combinations have
> dRPDf > 0 and win < loss, even at f=1 %. **But there is a feasibility
> crossover**: at f=1 % the `m1` K=1 baseline registers no incumbent at all on
> 20/160 instances (the (n=150,c=5) and (n=200,c=5) cells, where the
> `0.0009·f·n·c` budget is smallest), while K≥4 solves all 20. Those 20 drop out
> of the paired tables, so the f=1 % dRPDf row is a 140-instance mean **biased in
> K=1's favour**. Quote that alongside the headline.

---

### 20260726/verdict_mcf_lb_atomic.py

Judges the **mcf_lb atomic-gate-removal re-run** against the run it replaces —
the go/no-go for `plans/experiment/20260726/mcf_lb_atomic_gate_removal.md` §4.
Run it once after the re-run finishes; it never polls or waits.

Both runs live under the same output base (the re-run reuses the config
unchanged), so they are told apart by timestamp only. Defaults are baked in:
before = `20260726T002619_971440`, after = `20260726T173841_347539`.

**Completion gate**: the run's `*_rpdf_comparison.csv` exists only after the
final report pass, so its presence *is* the done signal and also the input every
table needs. If it is missing the script prints `<done>/33600 instance results`
and exits 1 — nothing else runs.

**Gates, in order** (`load_run` / `paired_drpdf` are imported from
`analyze_crossover_ladder.py`, so the verdict cannot drift from the tables the
analysis document quotes):

1. **G1 incumbents** — `bestObj` is never empty. Reports `m1_k1_f01` before→after
   and, on failure, every affected scenario with its `(n, c)` cells.
2. **G2 a/b control** — arms `a`/`b` bit-identical to the before-run (`a` never
   calls mcf_lb; `b`'s budget was never binding). **A G2 failure suppresses the
   conclusion section on purpose** — §4.1 says to re-read the code rather than
   interpret the re-run, so the script exits 1 without printing it.
3. **G3 c noise** — arm `c` mean `bestObj` delta within `NOISE_FLOOR_MEAN_OBJ`
   (±350, established over the 1440-instance grid; the 160-instance slice here
   is noisier per cell, so read a near-miss as "not distinguishable from noise",
   not "proven unchanged").

**Conclusion re-read** (only when G2 holds): how many of the 200
(arm, f, k, mode) cells contradict "no crossover" (`dRPDf > 0 AND win < loss`)
before vs after, whether the f=1 % `coarse_only_feasible` asymmetry went to zero,
and the m1 k=2 dRPDf ladder as `before -> after` per mode. Then it shells out to
`analyze_crossover_ladder.py` and `analyze_csr_winner_source.py --n 200 --c 10`
to emit the standard CSVs (`--skip-artifacts` to suppress).

Exits 0 only when all three gates pass.

```bash
uv run python scripts/20260726/verdict_mcf_lb_atomic.py
uv run python scripts/20260726/verdict_mcf_lb_atomic.py --after <run_dir> --skip-artifacts
```

> **Self-check**: passing the same finished run as both `--before` and `--after`
> reproduces the committed analysis exactly — `0/200` cells contradicting,
> `m1_k1_f01` at 140/160, and the k=2 ladder at cumulative
> +10.40/+12.34/+17.62/+18.48. G1 correctly fails there (42 nulls: 20 in
> `m1_k1_f01`, 4–6 per `m1_k2_*_f01`), which is the defect the re-run exists to
> remove.

---

### 20260726/analyze_csr_init_tl_curve.py

Judges the **W2 P1 gate** (`plans/experiment/20260726/csr_init_roadmap.md` §2):
does the τ=1 CSR initializer beat `best(MCF-LB → FMM, NEH-CP)` in **all nine
(T, R) cells** while spending ≤ 40 % of the `0.09nc` budget? Both arms are
initializer-only, so this scores **initial-solution quality**, not final
objective.

The baseline is the `c5_init_only` scenario measured **inside the same run**, so
machine, code and load match — historical C5 numbers were read at an obj_log
midpoint of a tail-carrying arm and are not a like-for-like comparator.

Reads `<ts>_rpdf_comparison.csv` (verbatim `RPDf_BKS_data`) plus each instance's
`_obj_log.json` for the inner-step breakdown, which the CSR inner-point notes
(`...inner-NN-<idx>-<step>`, added in `2c7ef28`) make possible.

**File output** (`--outdir`, default `analysis/<run_id>_csr_init_tl_curve/`):

- `gate_cells.csv` — per (f, T, R): both arms' mean RPDf, Δ, `cell_pass`,
  `indistinct` (|Δ| < 0.5 pp), win/tie/loss
- `f_curve.csv` — per scenario: pooled + per-T mean RPDf, mean elapsed and its
  share of the outer `0.09nc` cap (the budget-compliance check)
- `win_tie_loss.csv` — paired counts vs the baseline, pooled and per T
- `inner_steps.csv` — per (f, inner step) mean seconds, share, and objective drop
- `gate_verdict.txt` — the verdict block printed to stdout

Exits 1 when no f ≤ 40 % wins all nine cells.

```bash
uv run python scripts/20260726/analyze_csr_init_tl_curve.py \
    output/20260726_csr_init_tl_curve/20260726T231158_246105
```

> Conclusion (`plans/analysis/20260726/csr_init_tl_curve.md`): **gate PASS,
> minimum passing f = 20 %** — at half the baseline's wall time (15.9 s vs
> 32.0 s). At equal budget (f=40, 37.2 % both) CSR wins pooled −17.18 pp,
> 1227/132/81. Two caveats worth quoting: f=5 % **loses** (+16.16 pp) because the
> ~2.0 s non-interruptible `mcf_lb` eats 46 % of that budget, and f=20's weakest
> cell (T=0.6, R=1.0) is −0.56 pp / 88-72 — a tie in all but sign, so the robust
> pick is f ≥ 25 %.

---

### 20260729/analyze_init_budget_curve.py

Reads the **merged** run dir of the initialization-budget experiment
(`dv4_c5init_f{10,20,40}` vs the 20260722 full-init scenarios) and emits every
table `plans/analysis/20260729/init_budget_curve.md` quotes.

Inputs are the merged run's own artifacts, so nothing is recomputed from raw
solutions: `analysis_wide` (report.xlsx) for per-instance RPDf,
`<run_id>_summary.csv` for the two initialization objectives, and the **scatter
chart's embedded payload** for the step trajectory — parsing the chart's own
numbers keeps the document and the figure from drifting apart.

Three things it separates that a single mean hides:

1. **Confounded vs unconfounded comparisons.** Deltas against
   `a_v2_kappa005_max8` carry the v4 dispatch prefix and the 20260722→now code
   drift on top of the budget effect; `within_family.csv` (f-vs-f inside one
   run) is the only clean budget comparison. Both are printed, labelled.
2. **The dispatch prefix's actual contribution** — `initObj` vs `dispatchedObj`
   per instance, i.e. how often the dispatch schedule beats the MCF-derived one.
   The same pair doubles as a **drift probe**: the deterministic MCF-LB step's
   objective must match the older run instance-for-instance.
3. **Where the curves cross.** The *last* sign change, not the first — a
   shortened-init curve dips below early (the prefix) and comes back up while
   the baseline is still inside its longer initialization.

**Output** (`--out-dir`, default `analysis/20260729_init_budget_curve/`):
`scenario_summary.csv`, `within_family.csv`, `prefix_value.csv`,
`trajectory.csv`, `crossing.csv`, `tr_cells.csv`, `size_cells.csv`.

```bash
uv run python scripts/20260729/analyze_init_budget_curve.py \
    output/20260728_init_budget_merge/20260729T041116_435991
```

> Conclusion (`plans/analysis/20260729/init_budget_curve.md`): cutting the
> initialization from 40 % to 16 % of the `0.09nc` cap **gains** −1.09 %p mean
> RPDf (728/564, 3.6 σ) and cutting to 4 % is indistinguishable from the
> baseline — but within one run f10 < f20 < f40 monotonically, so the knee is at
> **≥ 16 %** and the "saved time is worth more in the tail" hypothesis does not
> hold over 4–16 %. Losses are local to T=0.6·R≥0.6 and mid/large c=5 cells.

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
