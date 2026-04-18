# Plan: Timestamp the output directory

## Context

Every run currently writes into a fixed `output_dir` (`metadata/fam_config.yaml` → `output`). Re-running overwrites the prior run's per-instance artifacts (solutions, Gantt charts, `*_statistics.yaml/json`, `obj_log.yaml`, summary CSV, Excel report). We want each run to land in its own subdirectory so previous outputs are preserved.

Scope for this change: **FULL_RUN only** — generate a fresh timestamped subdirectory at startup. RESUME/POST_PROCESS_ONLY path reuse is explicitly out of scope per the user's "지금 당장" (for-now) framing; the sibling `../hybridflowshop` has a more elaborate resume flow, but we are not porting that yet.

## Approach

Reuse the existing routix helper `init_timestamped_working_dir` (same helper `../hybridflowshop/main.py` already uses) and insert one call in `main.py` so the timestamped path is what gets handed to `FFcDDWMultiScenarioRunner`. Downstream scenario/instance directory creation works unchanged because the rest of the chain simply takes `output_dir` as given.

### The helper

```python
# routix.io.path
def init_timestamped_working_dir(
    base_output_dir: Path, e_timer: ElapsedTimer | None = None
) -> Path
```

- Creates `base_output_dir/<timestamp>` with `mkdir(parents=True, exist_ok=True)` and returns the `Path`.
- Timestamp format: `%Y%m%dT%H%M%S_%f` (e.g. `20260418T203045_123456`).
- `e_timer=None` → fresh `ElapsedTimer()` inside the helper, which is all we need for FULL_RUN.

## Files to modify

**`main.py`** — the single insertion point.

Add import:

```python
from routix.io.path import init_timestamped_working_dir
```

In `main()`, replace the current direct use of `config["output_dir"]` with:

```python
base_output_dir = Path(config["output_dir"])
output_dir = init_timestamped_working_dir(base_output_dir=base_output_dir)
logger.info("Run output directory: %s", output_dir)
```

Then pass `output_dir` (the timestamped path) to `FFcDDWMultiScenarioRunner` exactly where `output_dir` is currently passed.

## What does NOT change

- `metadata/fam_config.yaml` — `output_dir: output` stays the base directory.
- `src/ffc_ddw_sum_et/orchestration/reporting.py` (`FFcDDWMultiScenarioRunner`, `FFcDDWReporter`) — they already accept an `output_dir` and build scenario subdirs beneath it. No edits needed.
- `ffcddw_single_instance_runner.py` / `controller.py` — untouched.
- `.gitignore` — `output/` is already ignored, so new `output/<timestamp>/` folders are covered.

## Out of scope (flagged for follow-up)

- `RunMode.RESUME` — needs to *reuse* an earlier timestamped dir rather than create a new one. Will require reading the prior timestamp from config (or picking the latest under `output/`) and passing `resume_root` to the runners, mirroring `../hybridflowshop/main.py`. Track as a separate change.
- `RunMode.POST_PROCESS_ONLY` — same constraint as RESUME.
- Writing a `run_metadata.yaml` (config snapshot, timestamp, git SHA) into the new directory — nice-to-have, not requested now.

## Verification

1. `uv run ruff check` and `uv run ruff format` after the edit.
2. `uv run python main.py` with `run_mode: FULL_RUN` and the existing `ins_index: [0, 1, 2]` filter.
   - Expect a new directory `output/<YYYYMMDDTHHMMSS_microseconds>/` to appear.
   - Expect scenario subdirectories (e.g. `fam_default/`) inside it, with per-instance folders and `summary.csv` / `*_statistics.{json,yaml}` / Excel report beneath.
3. Run `main.py` a second time immediately — confirm a *second* sibling directory is created under `output/` and the first one is untouched.
4. Existing tests: `uv run pytest tests/parameters/test_ffc_ddw_params.py` (unchanged; regression check).
