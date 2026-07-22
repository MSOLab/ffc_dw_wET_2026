# Plan: Add orchestration test coverage

## Context

The review identified zero test coverage for the new orchestration package (~1000 lines across 7 files). This PR adds unit tests for the most testable units: `BenchmarkLoader`, `FFcDDWSubroutineController`, `FFcDDWReporter`, `FFcDDWMultiScenarioRunner`, `FFcDDWSolutionManager`, and `InstanceResult`.

Out of scope for this PR: `FFcDDWSingleInstanceRunner` (267 lines). Its `post_run_process` path has many file-I/O branches that are better covered by an integration test against a real tmp workspace; deferring to a follow-up keeps this PR reviewable.

## Test files to create

### `tests/orchestration/__init__.py`

Empty package marker.

### `tests/orchestration/test_benchmark_loader.py`

Tests for `BenchmarkLoader`:

- `test_load_all_with_ins_index` — create a tmp directory with a small `.txt` file, point `ins_index_source` at the existing CSV, verify correct instance loaded
- `test_load_all_with_ins_index_partially_missing` — some requested indices are in the CSV and some are not; verify warning logged for missing ones AND matched files still loaded
- `test_load_all_with_ins_index_all_missing` — no requested indices match; verify `FileNotFoundError` is raised
- `test_load_all_with_file_pattern` — verify glob filtering works
- `test_load_all_no_files_raises` — empty directory raises `FileNotFoundError`
- `test_load_all_skips_parse_errors` — malformed file logged but not crashed

### `tests/orchestration/test_solution_manager.py`

Tests for `FFcDDWSolutionManager`:

- `test_tracks_incumbent` — register multiple solutions, verify best (lowest obj) is incumbent
- `test_rejects_none_obj_value` — `_get_obj_value` raises `ValueError` for solution without `obj_value`
- `test_a_is_better_minimization` — lower obj_value wins
- `test_a_is_better_obj_bound_always_false` — FAM never improves bound

### `tests/orchestration/test_reporting.py`

Tests for `FFcDDWReporter` and helpers:

- `test_last_non_empty_line_empty` — None input
- `test_last_non_empty_line_single` — single line
- `test_last_non_empty_line_multi` — returns last non-empty line
- `test_aggregate_scenario_basic` — construct `ScenarioResult` with 3 instances, verify counts and stats
- `test_aggregate_scenario_with_errors` — mixed success/error instances
- `test_aggregate_scenario_no_completed` — all errored → None stats
- `test_aggregate_scenario_improvement_ratio_skips_none_first` — instance with `first_obj_value is None` is excluded from `meanImprovementRatio`
- `test_aggregate_scenario_improvement_ratio_skips_zero_first` — instance with `first_obj_value == 0` is excluded (avoid div-by-zero)
- `test_write_summary_csv` — use `tmp_path`, write CSV, verify columns and row count
- `test_write_statistics_json` — verify JSON output matches `_aggregate_scenario`
- `test_generate_summary_filename` — returns `{dir}_summary.csv`

### `tests/orchestration/test_controller.py`

Tests for `FFcDDWSubroutineController`:

- `test_run_fam_default_sequence` — use `make_ddw_instance()` factory, call `run_fam()`, verify `SubroutineReport` has valid `obj_value` and `solution_manager` has 1 record
- `test_run_fam_with_sequence` — pass explicit job sequence, verify it's used
- `test_work_status_feasible` — after run, `work_status` is `WorkStatus.FEASIBLE`
- `test_best_obj_value_after_run` — `best_obj_value` matches report `obj_value`
- `test_numpy_float_conversion` — verify `obj_value` is `float` not `np.int64` (boundary type check)

### `tests/orchestration/test_multi_scenario_runner.py`

Tests for `FFcDDWMultiScenarioRunner.run()` error isolation:

- `test_run_captures_scenario_exception_as_none` — build a runner with two `Mock` sub-runners where the first raises and the second returns a normal list; verify `self.results` ends up as `[None, [...]]` so one failed scenario does not poison the others
- `test_post_run_process_handles_none_result` — with `self.results = [None, [InstanceResult(...)]]`, verify `post_run_process` builds a `ScenarioResult` with empty `instance_results` for the failed slot (no crash)

### `tests/orchestration/test_instance_result.py`

Tests for `InstanceResult` dataclass:

- `test_default_values` — construct with required fields only, verify defaults
- `test_all_fields` — construct with all fields, verify values

## Key patterns

- Follow existing test conventions: pytest, `assert` statements, `tmp_path` fixture for file I/O tests
- Use `make_ddw_instance()` from `tests/algorithm/test_fam_algorithm.py` for creating test instances
- No `conftest.py` — all helpers inline per existing convention
- Use `unittest.mock.Mock` for logger where needed

## Verification

```bash
uv run pytest tests/orchestration/ -v
uv run ruff check tests/orchestration/
```
