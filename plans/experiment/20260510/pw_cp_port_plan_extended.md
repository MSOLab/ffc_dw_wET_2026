# PW-CP Port — Plan vs. Uncommitted Code Gap Analysis

Companion to `sw_cp_port_plan.md` and `sw_cp_port_plan_detail.md`. This
file captures every point where the current uncommitted change set
diverges from the two plan documents, as of 2026-05-11. Read together
with the two plan files; on conflict this file describes *what the code
actually does*, while the original plans describe *what was intended*.

The plan HTMLs (`sw_cp_port_plan.html`, `sw_cp_port_plan_detail.html`)
are direct renders of the same-named MDs, so the comparison below is
against the MD bodies.

## A. Code goes beyond plan (scope additions)

### A.1. `incremental_sw_cp` composite step

- **Location**: `src/ffc_ddw_sum_et/orchestration/controller.py:2295-2417`
  (~137 lines), `metadata/20260510/incremental_sw_cp.yaml`.
- **Behaviour**: iterates `unfixed_batch_count` from
  `unfixed_batch_count_min` to `unfixed_batch_count_max`. Two policies:
  - `"always"` — single `sw_cp` pass per count.
  - `"if_no_improvement"` — repeats `sw_cp` at the current count until a
    pass leaves the incumbent's weighted E+T unchanged, then advances.
- Each inner `sw_cp` call registers its own report; the composite does
  not register itself. Uses `temporarily_extended_context` to
  disambiguate per-iteration step-log paths.
- **Plan coverage**: none. Both plan files describe only the single
  `sw_cp` step method.

### A.2. `horizon_makespan_multiplier` (incumbent-makespan-based CP horizon)

Replaces the plan's `horizon = sum(p[j,i])` bound with
`horizon = ceil(incumbent.makespan * multiplier)`, defaulted to
`1.25`. Cross-cutting addition that touches multiple files:

- **`src/ffc_ddw_sum_et/algorithm/sw_cp/option.py`** (lines 51-57,
  92-97) — `PwCpOption.horizon_makespan_multiplier: float = 1.25`,
  validated `>= 1.0`.
- **`src/ffc_ddw_sum_et/algorithm/sw_cp/dispatcher.py`** (lines 61-64)
  — `horizon = max(1, int(math.ceil(incumbent.makespan * option.horizon_makespan_multiplier)))`.
- **`src/ffc_ddw_sum_et/algorithm/cpsat_adapter.py`** (lines 44-52,
  70-83) — adds `_compute_horizon(instance, ref_schedule, multiplier)`
  helper plus `CpsatOption.horizon_makespan_multiplier`. Falls back to
  `sum(p)` only when `ref_solution is None`.
- **`src/ffc_ddw_sum_et/orchestration/controller.py`** — added to:
  - `sw_cp` (new method)
  - `cpsat` (existing)
  - `mixed_dispatcher_warm_start_cp` (existing; lines around 1608-1640)

**Plan coverage**: plan dispatcher pseudocode (`sw_cp_port_plan.md:135-136`)
explicitly uses
`params_for_horizon = BaseModelBuilder.make_params(instance); horizon = sum(params_for_horizon.p.values())`.
The `cpsat_adapter.py` file is **not** mentioned anywhere in either
plan — its scope is strictly `algorithm/sw_cp/` + `controller.py` +
`ffc_schedule.py` + `main.py`.

### A.3. `log_search_progress` / `log_search_progress_max_steps`

Per-step CP-SAT solve log capture for hint verification.

- **`sw_cp/option.py:59-71`** — two new fields on `PwCpOption`.
- **`sw_cp/dispatcher.py:187-202`** — when enabled, sets
  `solver.parameters.log_search_progress = True`,
  `log_to_response = True`, `log_to_stdout = False`, then iterates the
  captured `solver.response_proto.solve_log` lines into the logger at
  INFO. `max_steps` caps capture to the first N steps to avoid log
  bloat.
- **`controller.py` `sw_cp` method** — exposes both kwargs.
- **`metadata/20260510/sw_cp_hint_check.yaml`** — dedicated scenario
  (single instance, single-thread, `log_search_progress_max_steps: 1`).

**Plan coverage**: none.

### A.4. `main.py` CONFIG_PATH points to the hint-check scenario

Code change:

```diff
-CONFIG_PATH = Path("metadata/20260509/20260509_mcf_lb_best_neh_cp_best_base_cpsat.yaml")
+CONFIG_PATH = Path("metadata/20260510/sw_cp_hint_check.yaml")
```

**Plan coverage**: plan body says "wire the new scenario name into the
dispatch table (mirror the existing `neh_cp` / `mcf_lb` registration)".
The repo has no such dispatch table — `main.py` uses a single
`CONFIG_PATH` constant and YAML's `subroutine_flow.method:` resolves
via `getattr` (clarified in `sw_cp_port_plan_detail.md` §"Naming and
placement"). The plan's "registration" mental model is therefore
incorrect for this repo; the actual change is a single-line
`CONFIG_PATH` redirect, currently aimed at the hint-check scenario
rather than the `sw_cp_grid.yaml` the plan would have implied.

## B. Code differs from plan in shape (intent preserved)

### B.1. `PwCpStepEntry` field set

Plan-listed fields (`sw_cp_port_plan.md:232-243`):
`step, unfixed_batch_start_idx, non_time_fixed_op_count,
incumbent_obj_before, cp_obj, incumbent_obj_after, accepted, status,
applied_tl_seconds, wall_seconds`.

Code-actual fields (`sw_cp/step_log.py`):

- Renamed: `applied_tl_seconds` → `TL`.
- Added: `elapsed_time`, `elapsed_portion`, `sub_job_count`,
  `cp_divergence_count`.

### B.2. `build_full_schedule_from_cp` return signature

- Plan: returns a single `FFcSchedule`.
- Code (`sw_cp/cp_model.py:397-457`): returns
  `tuple[FFcSchedule, cp_divergence_count: int]`. The diagnostic
  counter measures how many non-time-fixed ops realised a later end
  than the CP-promised end (cumulative-vs-greedy machine assignment
  drift, foreseen in `sw_cp_port_plan.md:327` Risk 3). Logged at DEBUG
  per step and persisted in `PwCpStepEntry`.

### B.3. `delay_job_latest_leq_obj_contrib_all_stages` extracted helper

- Plan (`sw_cp_port_plan_detail.md:83-102`): pseudocode inlines the
  per-stage rewrite of `__stage_2_mc_2_job_tuple_seq[i][mc_id]`.
- Code (`solution/ffc_schedule.py:1499-1562`): the per-stage loop body
  is extracted into private helper `_make_stage_right_justified`.
  Behaviour is identical; structural refactor only.

### B.4. `build_operation_partition` is public

- Plan calls it `_build_operation_partition` (private).
- Code exports it as `build_operation_partition` and re-exports from
  `algorithm/sw_cp/__init__.py` so tests can call it directly.

## C. Plan items missing from code

### C.1. `docs/TODO.md` rename note for `mcf_lb_phase_schedules`

`sw_cp_port_plan_detail.md` §"Per-step Gantt" states "a TODO entry
should track renaming the container to a generic `phase_schedules`".
No such entry exists in `docs/TODO.md`.

### C.2. `sw_cp` registration in `tests/algorithm/test_algorithm_contracts.py`

Plan §Verification 2 (`sw_cp_port_plan.md:305`): "register `sw_cp` in
`tests/algorithm/test_algorithm_contracts.py` so the shared shape-checks
run".

In practice the file does not have an algorithm registry — it tests
the generic `AlgSpec`/`AlgRecord` shapes against a toy instance with no
algorithm-specific hooks. The plan's mental model of a per-algorithm
registration slot is incorrect; no code change is actually required,
but the plan instruction stands unresolved in literal terms.

## C.3. Test coverage of right-justification invariants (applied 2026-05-11)

The plan's §Risks 1 (`sw_cp_port_plan.md:323`) enumerates the
correctness invariants for
`delay_job_latest_leq_obj_contrib_all_stages` and lists them as test
targets. Pre-existing tests in
`tests/solution/test_ffc_schedule_delay_all_stages.py` already covered
durations, per-machine sequence order, total-E+T non-increasing,
`validate_schedule`, and monotone-rightward operation movement. Four
invariants from the risk list were not explicitly asserted; tests were
added:

| Added test | Invariant pinned |
|---|---|
| `test_delay_all_stages_matches_last_stage_helper_on_last_stage` | Earlier-stage passes never mutate any `C_j` — the last-stage end-times produced by `..._all_stages` equal those produced by the last-stage delegate alone. |
| `test_delay_all_stages_per_job_objective_non_increasing` | Per-job (not just summed) weighted E+T is non-increasing. Catches a regression where one job's ET could increase while another's decreases and the totals still pass. |
| `test_delay_all_stages_satisfies_explicit_job_stage_precedence` | Risk-listed `end[j,i] <= start[j,i+1]` over every (j, i with i<c). `validate_schedule` already implies this, but the explicit assertion makes the contract visible at the test level. |
| `test_delay_all_stages_single_stage_equivalent_to_last_stage_helper` | Boundary: with `len(stages) == 1` the earlier-stage loop is empty, so `..._all_stages` is exactly the last-stage delegate. |

Result: 5 → 9 tests in the file; all pass; lint clean. No algorithm
change.

A few risk-listed invariants are intentionally not added as standalone
tests:

- `new_start[j,i] >= prev_op_end[j,i]_on_same_machine` is covered by
  `validate_schedule` and is more clearly characterised as a post-hoc
  property than as a step-by-step invariant. Adding it as an explicit
  assert duplicates `validate_schedule`'s overlap check.
- `new_end[j,i] - new_start[j,i] == p[j,i]` is already pinned by
  `test_delay_all_stages_preserves_durations` (which checks
  `end - start` equality before/after — duration is the only quantity
  that matters for FFcDDW since per-job p is fixed).

## D. Plan items present in code as expected (no gap)

For completeness — the following are correctly implemented and match
the plan:

- `delay_job_latest_leq_obj_contrib_all_stages` (two-pass right-
  justification, invariants per `sw_cp_port_plan_detail.md:104-114`).
- `PwCpOption` base field set (all plan fields present; extras in
  §A.2, §A.3 above).
- Five-region operation partition + `promote_job_contained_ops` +
  `validate_and_get_batch_count`.
- Partition-aware `PwCpModelBuilder`: non-time-fixed-only op vars,
  fixed left/right dummy bars sourced from `rj_schedule`, cumulative
  capacity, profile-fix precedence via existing
  `add_stage_ops_precedence_constraints_after_dispatch_from_schedule`,
  partial weighted-E/T objective with `et_offset_partial` for
  last-stage time-fixed jobs.
- Dispatcher accept/reject loop with semi-active + idle-time insertion
  pre-CP and post-merge.
- Two-phase Gantt via `_record_mcf_lb_phase("sw_cp_before" / "_after")`.
- `_step_log.yaml` artifact via `try_get_file_path_for_subroutine`.
- All six plan-mandated test files plus
  `tests/solution/test_ffc_schedule_delay_all_stages.py`.
- `sw_cp_grid.yaml` 4-scenario grid (u∈{2,3} × pf∈{PF0,PF1}).

## Suggested reconciliation

If the plans should reflect the code as-is, the following edits are
needed:

1. Add a §"Composite step: `incremental_sw_cp`" subsection to
   `sw_cp_port_plan_detail.md` describing the two policies and the
   per-iteration `temporarily_extended_context` tagging.
2. Replace `horizon = sum(p)` references in both plans with the
   incumbent-makespan-multiplier formula, and document the
   cross-cutting impact on `cpsat_adapter.py` (or move that change to
   its own plan).
3. Document `log_search_progress` / `log_search_progress_max_steps` on
   `PwCpOption` and the `sw_cp_hint_check.yaml` debug scenario.
4. Correct the "dispatch table" wording in `sw_cp_port_plan.md` §"File
   layout" to match `sw_cp_port_plan_detail.md` §"Naming and placement"
   (YAML `method:` → `getattr` on controller).
5. Update `PwCpStepEntry` field list in
   `sw_cp_port_plan.md` to match `sw_cp/step_log.py` (rename
   `applied_tl_seconds` → `TL`; add `elapsed_time`, `elapsed_portion`,
   `sub_job_count`, `cp_divergence_count`).
6. Note the `(schedule, cp_divergence_count)` return tuple of
   `build_full_schedule_from_cp` and its diagnostic role.
7. Either add the deferred-rename entry to `docs/TODO.md` or drop the
   instruction from the detail plan.
8. Drop the `test_algorithm_contracts.py` registration instruction (or
   rewrite the contracts test to actually have a per-algorithm
   registry).

If instead the code should be pulled back to the plan, the candidates
for revert/extraction are §A.1 (`incremental_sw_cp` to a follow-up
plan), §A.2 (`horizon_makespan_multiplier` — at minimum the
`cpsat_adapter.py` portion, since it touches a file outside the plan's
declared scope), and §A.3 (`log_search_progress` and its scenario).
