# Review: NEH-CP & Heatmap (2026-04-23 ~ 2026-04-24)

**Base**: `c1a3bc8`
**HEAD**: `251c921`
**Commits**: 7
**Author**: JuneTech

---

## Commit Summary

| # | Hash | Date | Message |
| - | ---- | ---- | ------- |
| 1 | `831a86c` | 04-23 | feat(pra2017): clip Z and sort jobs by due window |
| 2 | `570520b` | 04-24 | feat(neh-cp): add incremental CP-SAT constructor |
| 3 | `90aecbc` | 04-24 | feat(neh-cp): add skip_pf_below_obj param |
| 4 | `64a7e36` | 04-24 | feat(neh-cp): add cumulative TL and c-suffix |
| 5 | `9bbff3f` | 04-24 | create analyze_bestobj_randomness.py |
| 6 | `bdb17b7` | 04-24 | create logging-improvements.md for future |
| 7 | `251c921` | 04-24 | feat(pra2017): add wET heatmap and NEH-CP sort |

---

## Commit-by-Commit Review

### 1. `feat(pra2017): clip Z and sort jobs by due window` (`831a86c`)

**Files**: `benchmarks/PRA2017/visualize_parallel_mc_cost.py`

**Changes**:

- Added `MAX_Z_ABS = 100` constant to clip heatmap cost values.
- Changed job ordering from `instance.job_id_list` (unsorted) to sorted by due window.

**Review**:

- Z-clipping prevents a single outlier from washing out the heatmap color scale. The hardcoded `100` is not configurable but acceptable for a visualization script.
- Due-window sort gives a more structured visual layout than the raw job order. This sort logic is later refactored into `_sort_jobs()` in commit 7, making this commit the initial step.

---

### 2. `feat(neh-cp): add incremental CP-SAT constructor` (`570520b`)

**Files**: `main.py`, `metadata/20260423/neh_cp_config.yaml`, `plans/20260423/neh_cp.md`, `controller.py`, `ffc_ddw_params.py`, `schedule_build.py`, `test_controller.py`

**Changes**:

- **`create_instance_of_job_subset()`** — New classmethod to create a job-subset instance. Follows the existing `create_instance_of_stage_subset` pattern. Uses `filter_by_job_indices` on the processing time manager.
- **`build_schedule_from_op_starts(jobs=...)`** — Extended with optional `jobs` parameter to restrict greedy interval coloring to a subset. Backward-compatible (default `None` uses all jobs).
- **`_neh_cp_job_sequence()`** — Priority-based sort: `(max(w-, w+) desc, w-+w+ desc, d+-d- asc, position)`.
- **`neh_cp()`** — Full incremental constructor: batches jobs, dispatches each batch via `MixedDispatcher`, builds CP-SAT model with PF constraints from previous step, warm-starts from dispatch, keeps better of CP vs dispatch.
- **`neh_cp_config.yaml`** — 8 scenarios (batch sizes 1/2/5/10 x PF0/PF1).
- **Tests** — `test_neh_cp_registers_full_schedule` and `test_neh_cp_job_sequence_priority`.

**Review**:

- The implementation closely follows the plan in `plans/20260423/neh_cp.md`. The plan is well-structured with algorithm pseudocode, file list, and verification steps.
- `create_instance_of_job_subset` properly preserves job order and copies machine lists (shallow copy of lists, which is correct since machine IDs are strings).
- The `neh_cp` method adds `dispatched.make_semi_active()` and `dispatched.insert_idle_time()` calls that are not in the plan. These are constructive additions — semi-active schedule and idle time insertion improve the warm-start quality for CP-SAT.
- The `criteria="makespan"` in `MixedDispatcher` call differs from the plan (which says `"weighted_et"`). Using makespan for the dispatch phase is reasonable since the outer objective is weighted E+T and the CP solver optimizes that.
- Two tests cover the happy path and the job sequence priority. Missing: test for `error_if_infeasible`, test for empty instance (should raise), and test for the log file dump path.

---

### 3. `feat(neh-cp): add skip_pf_below_obj param` (`90aecbc`)

**Files**: `main.py`, `metadata/20260423/neh_cp_config_*.yaml` (renamed + new), `controller.py`

**Changes**:

- Renamed `neh_cp_config.yaml` to `neh_cp_config_1.yaml` (preserves previous experiment config).
- Added `neh_cp_config_2.yaml` with `skip_pf_below_obj` parameter.
- **`skip_pf_below_obj: str | float | None`** — When set, suppresses partial-fix precedence constraints if the previous step's weighted E+T is at or below the threshold.
  - `"makespan"` uses the previous solution's makespan as the dynamic threshold.
  - `float` value is used directly.
  - `0` means "only skip when obj <= 0" (effectively never skips for positive objectives).
- Added docstring with full parameter docs and `Raises` section.
- Changed `pf_method` default from `None = "PF1"` to positional default `"PF1"` (no longer optional in type, always a string).

**Review**:

- The `skip_pf_below_obj` idea is sound: when the objective is already good (below makespan or a threshold), skipping PF constraints lets the CP solver explore more freely.
- The validation logic converts non-"makespan" strings to float with a clear error message. Good.
- Changed `pf_method` from `PFMethod | None = "PF1"` to `PFMethod = "PF1"`. This is a breaking change for callers that pass `pf_method=None`. The skip logic uses `not skip_pf` as the guard now (instead of `pf_method is not None`), so `None` is no longer needed. Consider whether existing callers pass `None` explicitly.
- The `last_obj_value` tracking is correct — updated at the end of each iteration with `se + st`.

---

### 4. `feat(neh-cp): add cumulative TL and c-suffix` (`64a7e36`)

**Files**: `main.py`, `metadata/20260423/neh_cp_config_3.yaml`, `controller.py`

**Changes**:

- **`_resolve_cp_tl()`** — Extended to support `"c"` suffix: `"<number>c"` to `number * stage_count`. Existing `"nc"` suffix unchanged. Added docstring.
- **`apply_cumulative_tl: bool = False`** — When enabled, each batch receives a time limit based on the remaining cumulative budget: `cp_tl_seconds * (step + 1) - elapsed`. Minimum floor is `cp_tl_seconds` (per-batch value).
- **`neh_cp_config_3.yaml`** — Multi-instance experiment (6 instances, indices 0, 21, 41, 61, 81, 101). Uses `apply_cumulative_tl: true` and `cp_tl` with `"c"` suffix (e.g., `"0.03c"`).
- **Commented-out hint application** — Three `BaseModelBuilder.apply_*_hints_*` calls are now commented out. This removes the warm-start hints from the dispatch schedule.

**Review**:

- The `"c"` suffix is a clean extension. The `"nc"` pattern already existed; adding stage-count-only scaling makes sense for experiments where job count doesn't affect solver time proportionally.
- Cumulative TL is a good feature: early batches get more relative time, late batches get the remaining budget. The floor prevents the solver from being starved.
- **Commented-out code** (lines 1157-1163 in controller.py): The three `apply_*_hints_*` calls are commented out but not removed. This is a debugging artifact. Either remove them or add a comment explaining why they're disabled. Leaving dead code violates DRY and makes maintenance harder.
- The config file changes `ins_index` to 6 instances and reduces `instance_worker_cnt` from 48 to 12. This suggests a shift from single-instance debugging to multi-instance benchmarking.

---

### 5. `create analyze_bestobj_randomness.py` (`9bbff3f`)

**Files**: `scripts/analyze_bestobj_randomness.py` (new)

**Changes**:

- New script to analyze `bestObj` variance across N repeated runs.
- Reads run IDs from `/tmp/new_dirs.txt`, loads summary CSVs, aggregates by `(instanceName, scenarioName)`.
- Outputs: count, min, max, mean, std, unique count, range, CV%, deterministic scenarios, top-5 most random, pivot table.

**Review**:

- Hardcoded paths: `/tmp/new_dirs.txt` and `output/20260423` base directory. Consider making these CLI arguments for reusability.
- The script is a one-shot analysis tool — appropriate for the `scripts/` directory.
- Uses `pd.set_option("display.max_rows", None)` which can be verbose for large datasets. Consider a cap or `--verbose` flag.

---

### 6. `create logging-improvements.md for future` (`bdb17b7`)

**Files**: `plans/20260423/logging-improvements.md` (new)

**Changes**:

- Detailed plan for improving logging: CLI `-q`/`-v` flags, per-domain log files, routix logger injection.
- Proposes new `logging_setup.py` module with `setup_logging()` function.
- Identifies current issues: terminal noise, single log file, missing routix logs.
- Includes verification checklist and rollback plan.

**Review**:

- Well-structured plan with clear scope boundaries (YAGNI for per-instance logs, JSON logs, log rotation).
- The handler routing approach (single root handler with `AddFilter` per domain) avoids the common pitfall of `propagate=False` breaking the logger hierarchy.
- Notes the logger name issue: `FFcDDWSubroutineControllerCore` uses `ffc_ddw_sum_et.{instance_name}` which doesn't match any domain prefix. The plan proposes moving to `ffc_ddw_sum_et.orchestration.controller.{instance_name}`.
- This is a deferred plan (not implemented). Stored in `plans/` per project convention.

---

### 7. `feat(pra2017): add wET heatmap and NEH-CP sort` (`251c921`)

**Files**: `benchmarks/PRA2017/README.md`, `visualize_parallel_mc_cost.py`, `visualize_wET_cost.py` (new), `controller.py`, `ffc_ddw_params.py`

**Changes**:

- **`get_neh_cp_job_sequence()`** — Extracted the job sequence logic from `_neh_cp_job_sequence()` into a method on the parameters class. Controller method now delegates to it.
- **`visualize_wET_cost.py`** — New heatmap script. Computes actual wET penalty: `w- * (d- - t)` for earliness, `w+ * (t - d+)` for tardiness. Same structure as `visualize_parallel_mc_cost.py` but with the real penalty formula.
- **`visualize_parallel_mc_cost.py`** — Refactored sort logic into `_sort_jobs()` helper, added `--sort` CLI argument with `due-window` (default) and `neh-cp` options.
- **`README.md`** — Updated with standalone visualization section and new output file descriptions.

**Review**:

- Extracting job sequence to `FFcDDWParameters` is the right direction — the sequence is a property of the instance data, not the controller. This also lets the visualization scripts use the same sort without importing the controller.
- The wET heatmap script is a clean parallel to the C cost heatmap. Both now share `--sort` options and `MAX_Z_ABS` clipping.
- Code duplication between `visualize_parallel_mc_cost.py` and `visualize_wET_cost.py`: `_weights_or_default`, `_sort_jobs`, `make_figure`, and `main()` are nearly identical in structure. A shared helper module could reduce duplication, but for two scripts it's acceptable. Refactor if a third visualization is added.
- The `get_neh_cp_job_sequence` docstring uses full Unicode superscripts (w-, w+) — consistent with the commit message style but different from the controller docstring which uses `w^-_j`. Minor inconsistency.

---

## Cross-Cutting Observations

### Strengths

1. **Incremental development** — Each commit adds one feature on top of the previous. The progression from basic constructor to skip PF to cumulative TL to extraction is logical.
2. **Plan-driven** — `plans/20260423/neh_cp.md` provides a clear spec. Implementation follows it closely.
3. **Test coverage** — Unit tests for the job sequence priority and full schedule registration give confidence in the core logic.
4. **Documentation** — README updated, docstrings added to `neh_cp()`, deferred logging plan written.

### Issues

| Severity | File | Issue |
| -------- | ---- | ----- |
| **Medium** | `controller.py:1157-1163` | Three `apply_*_hints_*` calls are commented out but not removed. Either delete or add a comment explaining why. |
| **Low** | `controller.py` | `pf_method` changed from `PFMethod \| None = "PF1"` to `PFMethod = "PF1"`. Any caller passing `pf_method=None` will break. Verify no such callers exist. |
| **Low** | `scripts/analyze_bestobj_randomness.py` | Hardcoded `/tmp/new_dirs.txt` and `output/20260423`. Make CLI args for reusability. |
| **Low** | `visualize_parallel_mc_cost.py` vs `visualize_wET_cost.py` | Duplicate `_weights_or_default`, `_sort_jobs`, `make_figure` functions. Acceptable for 2 scripts, refactor if a third is added. |
| **Low** | `neh_cp_config_3.yaml` | `instance_worker_cnt: 12` differs from the project convention of 48 workers. Document if this is intentional for multi-instance runs. |

### Suggestions (non-blocking)

- Add a test for `neh_cp` with `error_if_infeasible=True` on an infeasible instance.
- Add a test for `_resolve_cp_tl` with the new `"c"` suffix.
- Consider adding `get_neh_cp_job_sequence` to the `FFcDDWParameters` public API documentation or docstring.
