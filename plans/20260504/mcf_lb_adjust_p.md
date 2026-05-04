# Plan: `adjust_p_by_full_sch_and_last_stage_only_sch` flag for MCF-LB pipeline

Mirrors the existing `adjust_r_by_full_sch_and_last_stage_only_sch` knob added in
commit `3114c22` (`feat(mcf-lb): adjust r by incumbent-ls-only gap`).

## Behaviour

When the flag is `True` on `apply_lb_by_mcf` or
`heuristic_last_stage_only_sch_from_mcf_lb`:

1. Read the incumbent schedule from `self.solution_manager.get_incumbent()`
   and the last-stage-only schedule from `self.last_stage_only_sol`. Both
   must already exist (raise `ValueError` otherwise — same fail-fast as
   adjust_r).
2. Compute `makespan_delta = max(incumbent.makespan - ls_only.makespan, 0)`.
3. Compute `p_adjust = ceil(makespan_delta * last_stage_mc_count / job_count)`
   — i.e. `ceil(delta / (n / m_last))` where `n = self.instance.job_count`
   and `m_last = self.instance.last_stage_mc_count`.
4. `effective_p_increment = p_increment + p_adjust` (additive, exactly the
   same shape as `effective_r_increment = r_increment + makespan_delta`).
   Applies regardless of whether `p_increment` is 0 or ≥ 1.

The existing `with_stage_processing_time_increment(instance, last_stage_id,
effective_p_increment)` is used unchanged — it adds the increment uniformly
to every job at the last stage. ("모든 processing time에 더해줌" is read as
"every job's processing time at the last stage", matching the established
`p_increment` semantics; the alternative — augmenting every stage — is a
larger change that this knob is not asking for.)

`obj_bound_is_valid` already checks `p_increment == 0`. Update it to use
`effective_p_increment == 0` so the bound is correctly invalidated when
adjust_p adds a positive delta.

## What was actually implemented

### 1. `src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py`

Initial implementation added a parallel `adjust_p_*` triple alongside the
existing `adjust_r_*` triple. A follow-up step then collapsed both into a
single shared triple plus per-knob increment fields, since `adjust_r` and
`adjust_p` always read from the same incumbent / `last_stage_only_sol`
sources and therefore stored identical makespan values:

```python
adjust_params_last_stage_only_makespan: int | None = None
adjust_params_incumbent_makespan:       int | None = None
adjust_params_makespan_delta:           int | None = None
adjust_p_increment_added: int | None = None  # = ceil(delta * m_last / n)
adjust_r_increment_added: int | None = None  # = makespan_delta
```

### 2. `src/ffc_ddw_sum_et/orchestration/controller.py`

Top of file: added `import math` for `math.ceil`.

#### `apply_lb_by_mcf`
- Added `adjust_p_by_full_sch_and_last_stage_only_sch: bool = False` kwarg
  (alongside the existing `adjust_r_…` kwarg).
- Hoisted incumbent/ls-only lookup into a local `_ensure_makespans()`
  closure shared by both adjust_r and adjust_p branches (avoids duplicating
  the fail-fast / makespan computation).
- Compute `effective_p_increment = p_increment + p_adjust` (when the flag
  is set) before `solve_mcf_lb`. Replaced every internal use of
  `p_increment` after that point with `effective_p_increment`:
  - `with_stage_processing_time_increment(instance, last_stage_id, effective_p_increment)`
  - `if effective_p_increment == 0` (skip-augmentation branch)
  - `self.mcf_preemptive_sch_p_increment = effective_p_increment`
  - `obj_bound_is_valid = (effective_p_increment == 0 and r_multiplier <= 1.0 and effective_r_increment == 0)`
  - Summary log line (`p_increment=%d (effective=%d)`).
- Diag setters: write the shared `adjust_params_*` triple once when either
  flag fired; write `adjust_r_increment_added = makespan_delta` and/or
  `adjust_p_increment_added = p_adjust` in their respective branches.

#### `heuristic_last_stage_only_sch_from_mcf_lb`
- Same pattern: new kwarg, shared `_ensure_makespans()` closure,
  `effective_p_increment` plumbed through `with_stage_processing_time_increment`,
  the skip branch, `self.last_stage_only_sol_p_increment`, and the log line.
- Diag setters mirror `apply_lb_by_mcf`'s shared-triple + per-knob
  increment_added pattern.

### 3. `src/ffc_ddw_sum_et/orchestration/reporting.py`

Replaced the original `_write_adjust_r_makespan_delta_csv` and the briefly
introduced `_write_adjust_p_makespan_delta_csv` with a single
`_write_adjust_params_by_makespan_delta_csv`:

- Row-emission criterion: `adjust_params_makespan_delta` non-null.
- Reads `adjust_params_*` triple plus both `adjust_p_increment_added` and
  `adjust_r_increment_added`.
- Columns: `scenarioName, insIndex, instanceName,
  lastStageOnlyMakespan, incumbentMakespan, makespanDelta,
  pIncrementAdded, rIncrementAdded` (empty string when the corresponding
  knob did not fire).
- `generate()` calls only this single writer.

### 4. `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`

Registered a single artifact kind (no separate r / p kinds):

```yaml
- scope: run
  kind: adjust_params_by_makespan_delta_csv
  file_template: "{run_id}_adjust_params_by_makespan_delta.csv"
```

### 5. `metadata/20260504/mcf_lb_init_adjust_pj_debug_config.yaml`

Created a debug config that exercises adjust_p on the second
`apply_lb_by_mcf` and `heuristic_last_stage_only_sch_from_mcf_lb` steps of
a 6-step `build_full_sch_p_adjust` scenario (4 PRA2017 instances, 96
workers). `main.py` already targets this config. After the user toggled
`output_dir` to `output/20260504_debug`, runs land under that subtree.

## Validation (executed)

`uv run ruff check` — clean. `uv run main.py` — 4 instances, all feasible:

| Rep | ls_only | incumbent | delta | n  | m_last | p_adjust | MCF LB before / after |
|-----|---------|-----------|-------|----|--------|----------|-----------------------|
| 0   | 1179    | 1262      | 83    | 50 | 3      | 5        | 7261 → 9502           |
| 1   | 1168    | 1192      | 24    | 50 | 3      | 2        | 5771 → 6527 (etc.)    |
| 2   | 1204    | 1308      | 104   | 50 | 3      | 7        | 8586 → 11763          |
| 3   | 1227    | 1309      | 82    | 50 | 3      | 5        | 7441 → 9238           |

`{run_id}_adjust_params_by_makespan_delta.csv` populated correctly:
`pIncrementAdded` filled, `rIncrementAdded` empty (this scenario doesn't
exercise adjust_r). Per-instance `mcf_lb_diagnostic.yaml` shows the
`adjust_params_*` triple plus `adjust_p_increment_added`, with
`adjust_r_increment_added: null`. `bestBound` in `summary.csv` correctly
falls back to the *first* (un-augmented) MCF LB — the second call's bound
is rejected by `obj_bound_is_valid` because `effective_p_increment != 0`.

## Deferred follow-ups

Recorded under `docs/TODO.md` ("Same-meaning values managed under
different names"). Not addressed in this change.
