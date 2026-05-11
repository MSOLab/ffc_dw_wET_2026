# Cross-run multi-scenario flow comparison chart

## Context

`scripts/build_subroutine_flow_charts.py` rebuilds the run-level
`<run_id>_multi_scenario_subroutine_flow_comparison.html` for a single run
directory, using scenarios discovered from that run's `_summary.csv`. There is
no way to compare scenarios that live in *different* run directories — e.g.,
two experiment runs with different scenario configs — even though every
required ingredient (per-instance `obj_log.json`, `instance_result.yaml`, the
BKS baseline) is fully self-contained per scenario directory.

This change adds a sibling script that takes scenario directories directly
(across any number of runs) and emits one cross-run flow comparison HTML.
All chart building blocks already exist; this script is purely an
orchestrator.

## Approach

Add **`scripts/build_cross_run_flow_chart.py`**. It walks an arbitrary list of
scenario directory paths, loads each instance's progression off disk, attaches
RPDf from the shared baseline, and renders one combined HTML via the existing
multi-scenario writer.

### CLI

```bash
uv run python scripts/build_cross_run_flow_chart.py \
  --output <output.html> \
  [--labels LABEL1 LABEL2 ...] \
  [--hybrid-match-csv PATH] [--bks-csv PATH] [--instance-table-csv PATH] \
  [-v] \
  <scenario_dir_1> <scenario_dir_2> ...
```

- Positional: one or more scenario directories. Each is expected to be
  `<run_dir>/<scenario_name>/` containing instance subdirs that hold
  `<instance>_obj_log.json` and `<instance>_instance_result.yaml`.
- `--output` is **required** (no default; avoid accidental writes).
- `--labels` (optional): if provided, must match positional count exactly,
  one custom label per scenario dir.
- Default label = `<scenario_dir.parent.name>/<scenario_dir.name>`
  (i.e. `<run_id>/<scenario_name>`), which keeps traces unambiguous when
  the same scenario name appears in multiple runs.
- Baseline CSV flags default to `benchmarks/PRA2017/` (same defaults as
  `build_subroutine_flow_charts.py`).
- Script only emits the cross-run flow comparison HTML; it does **not**
  write per-scenario scatter HTMLs (does not touch the source run dirs).

### Implementation flow (top-level `main`)

1. Parse args; validate that every positional scenario dir exists and is a
   directory; if `--labels` is given, validate length matches.
2. Validate the three baseline CSVs exist; if any missing, fail with a
   clear message (this script's whole purpose is RPDf — silent skip would
   hide a misconfigured invocation).
3. Call `load_baseline_df(...)` once.
4. For each `(scenario_dir, label)` pair:
   - Scan `scenario_dir` for instance subdirs that contain
     `<instance>_obj_log.json` + `<instance>_instance_result.yaml`.
     Skip subdirs without obj_log (matches
     `iter_scenario_instance_progressions` policy); raise if obj_log is
     present but manifest is missing (loud-fail policy).
   - Call `load_instance_progression(obj_log_path, manifest_path)` on
     each, collect a `list[InstanceProgression]`.
   - If the scenario has zero progressions, log a warning and skip it.
   - Build the scenario dict via the same shape used by
     `_scenario_metrics_dict`:
     - `endpoint_df = attach_rpdf_columns(build_endpoint_df(progs), baseline_df)`
     - `raw_progression_df = attach_rpdf_columns(build_raw_progression_df(progs), baseline_df)`
     - `label = <resolved label>`
5. Call
   `export_multi_scenario_method_rpdf_comparison_html(scenario_metrics, args.output)`.
   Return non-zero exit if it returns `False` (no usable traces).

### Why a small local discovery helper instead of `iter_scenario_instance_progressions`

`iter_scenario_instance_progressions` requires an `ArtifactLayout` and a
scenario name registered with it. Cross-run inputs aren't bound to a single
layout, and constructing per-run layouts adds friction with no benefit. The
on-disk structure inside a scenario dir is fixed (instance subdirs holding
`<instance>_obj_log.json` and `<instance>_instance_result.yaml`), so a 10-line
local helper that mirrors that loader's policy (skip-on-missing-obj_log,
raise-on-missing-manifest) is cleaner than threading layouts through.

The helper should use `instance_name = subdir.name` and construct paths as
`subdir / f"{instance_name}_obj_log.json"` / `..._instance_result.yaml`
(the same naming convention the runner writes).

### Reused code (do not duplicate)

- `ffc_ddw_sum_et.report.obj_log_loader.load_instance_progression`
  (`src/ffc_ddw_sum_et/report/obj_log_loader.py:173`)
- `ffc_ddw_sum_et.report.obj_log_loader.build_endpoint_df`
  (`src/ffc_ddw_sum_et/report/obj_log_loader.py:267`)
- `ffc_ddw_sum_et.report.obj_log_loader.build_raw_progression_df`
  (`src/ffc_ddw_sum_et/report/obj_log_loader.py:302`)
- `ffc_ddw_sum_et.report.post_run_chart_writer.load_baseline_df`
  (`src/ffc_ddw_sum_et/report/post_run_chart_writer.py:63`)
- `ffc_ddw_sum_et.report.post_run_chart_writer.attach_rpdf_columns`
  (`src/ffc_ddw_sum_et/report/post_run_chart_writer.py:100`)
- `ffc_ddw_sum_et.report.multi_scenario_method_chart.export_multi_scenario_method_rpdf_comparison_html`
  (`src/ffc_ddw_sum_et/report/multi_scenario_method_chart.py`)

Check whether `load_baseline_df` / `attach_rpdf_columns` are exported from
`ffc_ddw_sum_et.report.__init__`; if not, import them from the submodule
(don't add re-exports just for this script — YAGNI).

## Files to create / modify

- **Create**: `scripts/build_cross_run_flow_chart.py` (new script,
  ~120 lines).
- **Modify**: `scripts/README.md` — add a new entry under section
  **"3. Report Rebuild"**, immediately after the existing
  `build_subroutine_flow_charts.py` block. Match the existing Korean
  style and structure (입력 / 출력 / 실행 예시), and briefly contrast
  with the single-run script:
  - "여러 run 디렉터리의 시나리오를 한 차트에서 비교할 때 사용."
  - **입력**: 시나리오 디렉터리 N개 (각각
    `<run_dir>/<scenario_name>/` 형태). 각 디렉터리 안 인스턴스
    서브폴더의 `<instance>_obj_log.json` + manifest를 직접 읽는다.
  - **출력**: `--output` 으로 지정한 단일 HTML
    (per-scenario scatter는 만들지 않음 — 원본 run 디렉터리를 건드리지 않음).
  - 라벨 기본값 `<run_id>/<scenario_name>`, `--labels` 로 override.
  - 벤치마크 CSV 기본값 `benchmarks/PRA2017/`, override 플래그는
    `build_subroutine_flow_charts.py`와 동일.
  - 실행 예시 두 개 (기본 라벨 / `--labels` override).
- **No source changes** to `src/ffc_ddw_sum_et/report/*`. All needed helpers
  are already public-shaped within the report subpackage.
- **Plan archive**: per user memory (`plans/<YYYYMMDD>/<slug>.md`), copy
  this plan file to `plans/20260511/cross_run_multi_scenario_flow.md` once
  plan mode exits — the implementation pass can do this as its first step.

## Verification

1. Smoke run against two scenario dirs from two recent runs in `output/`:
   ```bash
   uv run python scripts/build_cross_run_flow_chart.py \
     --output /tmp/cross_run_flow.html \
     output/<dateA>/<runA>/<scenarioA> \
     output/<dateB>/<runB>/<scenarioB>
   ```
   Open the HTML and confirm two distinct trace groups appear with labels
   `<runA>/<scenarioA>` and `<runB>/<scenarioB>`.
2. Negative path: pass one scenario dir that has zero instances with
   `obj_log.json` → expect warning + non-zero exit (or graceful skip with
   single-scenario chart, depending on the remaining scenarios).
3. `--labels` override path: pass two dirs with `--labels A B`; confirm the
   chart legend uses `A` / `B`.
4. Lint/format:
   ```bash
   uv run ruff check scripts/build_cross_run_flow_chart.py
   uv run ruff format scripts/build_cross_run_flow_chart.py
   ```
5. Read `scripts/README.md` end-to-end and confirm the new entry follows
   the existing Korean style of section 3 and that the example commands
   are runnable verbatim.
