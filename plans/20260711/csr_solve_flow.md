# CSR configurable solve flow (`solve_flow`)

- **Status:** IMPLEMENTED (2026-07-11) — W1-W5 landed; smoke run green on
  PRA2017/large ins_index [60, 61]. See "Implementation notes" (§9).
- **Branch:** `20260711_csr_subalg`
- **Owner decisions (AskUserQuestion, 2026-07-11):**
  1. Config shape: **inline `solve_flow`** list under the `coarsen_solve_reconstruct`
     step, same schema as a scenario `subroutine_flow` (`method` + kwargs).
  2. v1 sub-step scope: **the full 5-step example flow** —
     `calc_mcf_lb_and_derive_full_sch`, `run_flip_makespan_cp_from_incumbent`,
     `neh_cp`, `incremental_sw_cp` (hence `sw_cp`), `solve_base_model_cpsat`.
     Experimental validation restricted to PRA2017/large `ins_index: [60, 61]`.
  3. Execution structure: **nested (child) `FFcDDWSubroutineController`** on the
     coarsened instance.

## 1. Goal

Today `coarsen_solve_reconstruct` (CSR) = quantize (`coarsen_processing_times`,
`factor`) → initialize (dispatch seed) + solve with a **hard-coded base CP-SAT**
(`_solve_coarsened_model` in `algorithm/coarsen_solve_reconstruct.py`) →
reconstruct a feasible original-scale schedule.

This plan makes the *solve* phase configurable: a `solve_flow` list in the
scenario YAML (identical schema to `subroutine_flow`) runs on the coarsened
instance via a child controller. Borrowed from hybridflowshop
`initialize_by_tau_coarsened_cp` (`hfs_cp_lns.py`):

- **Candidate store:** keep multiple coarsened schedules — v1 captures the
  schedule registered at the END of each sub-step (harvested from the child
  `solution_manager.history`). CP solution-callback snapshots are deferred
  (see TODOS.md).
- **Restore-all-pick-best:** reconstruct EVERY stored candidate to the original
  scale and select the argmin original-scale wET (not "latest with fallback").
- Other adopted practices: structural dedup of candidates, validate-and-drop of
  reconstructed schedules, per-candidate CSV artifact for offline analysis,
  remaining-time-clamped budgets, graceful fallback when no candidate survives.

## 2. Config schema

```yaml
scenarios:
  - name: csr_subalg
    timelimit: "0.1nc"
    subroutine_flow:
      - method: coarsen_solve_reconstruct
        factor: 50
        timelimit: "0.09nc"          # total CSR budget (coarsen + solve_flow + reconstruct)
        solve_flow:                   # NEW — same schema as subroutine_flow
          - method: calc_mcf_lb_and_derive_full_sch
            ...
          - method: run_flip_makespan_cp_from_incumbent
            cp_tl: "0.009nc"
            ...
          - method: neh_cp
            ...
          - method: incremental_sw_cp
            non_time_fixed_op_time_limit_multiplier: 0.005
            batch_tl_mode: "proportional"
            ...
          - method: solve_base_model_cpsat
            solver_thread_cnt: 8
```

Semantics:

- `solve_flow` present → it **replaces** the built-in dispatch-seed init AND the
  hard-coded base-CP solve. The flow is fully responsible for creating the
  first coarse incumbent (the example flow's first step is constructive).
  `seed_dispatch` and `solve` are ignored in this mode (log a warning if the
  user sets them explicitly alongside `solve_flow`).
- `solve_flow` absent → **behavior is bit-for-bit unchanged** (existing pure
  pipeline `run_coarsen_solve_reconstruct`). Backward compatibility is a hard
  requirement; all existing tests must pass untouched.
- Time-limit expressions inside `solve_flow` (e.g. `"0.009nc"`) resolve against
  the child (coarse) instance; job/stage counts are unchanged by coarsening so
  values match the original instance.
- Child controller `stopping_criteria = {"timelimit": min(resolved CSR
  timelimit, parent remaining time)}`. Reconstruction runs after the child
  finishes regardless (cheap, non-solver work), inside the parent step's
  elapsed time.

## 3. Cross-cutting contract: `time_factor` in sub-algorithms

The coarse instance (`coarsen_processing_times`) keeps due windows at the
ORIGINAL scale (SSOT decision, plans/20260627 + 20260629). Any algorithm run on
it must interpret coarse completion `C^c` as original-scale `time_factor * C^c`.

**Reference semantics = `BaseModelBuilder`** (`algorithm/cumulative.py`, field
at line ~78, E/T terms at ~375-433): `E_j = max(0, d^-_orig − time_factor*C^c)`,
`T_j = max(0, time_factor*C^c − d^+_orig)`; scoring SSOT is
`compute_weighted_earliness_tardiness(schedule, instance, time_factor)`;
idle insertion via `FFcSchedule.insert_idle_time(..., time_factor=...)`.

Contract (per algorithm-principles Rule 8 — behavior-affecting params live in
the `AlgOption`):

- Each affected `AlgOption` subtype gains `time_factor: int = 1` (validate
  `>= 1` in `__post_init__`).
- `time_factor` must be threaded into: CP model E/T objective terms, any
  due-window comparison in heuristics/partition/promotion logic, every
  `insert_idle_time` call, and every objective evaluation
  (`compute_weighted_earliness_tardiness(..., time_factor=...)`).
- **Invariance:** `time_factor=1` must reproduce current behavior exactly
  (existing tests are the regression net; do not modify them).
- `FFcDDWSubroutineController` gains a `time_factor: int = 1` constructor
  param/attribute. Each step method passes `self.time_factor` into the option
  it builds. The parent controller always has `time_factor=1`; only the child
  controller created by CSR gets `time_factor=factor`.

**LB soundness rules:**

- The coarse problem (ceil-rounded p) is NOT a relaxation of the original —
  a coarse LB is never a valid original-scale LB. The parent CSR step keeps
  `obj_bound=None` (existing rule, controller.py ~2740).
- Inside the child controller, an exactly-threaded MCF LB is only used for the
  child's own incumbent/optimality logic on the coarse problem. If exact
  `time_factor` threading of the MCF LB math turns out unsound or
  disproportionately complex, the fallback is: in coarse mode
  (`time_factor > 1`) the pipeline still derives its full schedule but reports
  `obj_bound=None`. Document whichever is implemented.

## 4. Architecture (solve_flow mode)

Parent step `FFcDDWSubroutineController.coarsen_solve_reconstruct`
(orchestration layer — a controller-level composite; the algorithm layer must
NOT import orchestration):

1. Resolve budget (existing `resolve_value_expr` + strict-min with remaining).
2. Coarsen: `FFcDDWParameters.coarsen_processing_times(instance, factor)`
   (unchanged).
3. Build child `FFcDDWSubroutineController(coarse_instance, solve_flow,
   stopping_criteria, time_factor=factor)` and run it headless (no gantt /
   painter / per-step artifact files; verify what construction minimally
   needs vs. what the runner normally attaches).
4. Harvest candidates: every `SolutionRecord` in child
   `solution_manager.history` with a non-None schedule →
   `(source=step_label/call_context, coarse_schedule, coarse_obj, coarse_bound)`.
   Note `incremental_sw_cp` registers once per inner `sw_cp` call — each inner
   result is naturally a candidate. Dedup by a structural signature
   (per-machine job sequences + per-stage time-ordered job order; port of
   hybridflowshop `_schedule_sequence_signature`, adapted to `FFcSchedule`).
5. Reconstruct ALL deduped candidates with the existing
   `reconstruct_coarse_schedule(coarse_schedule, instance, factor)`
   (semi-active + `insert_idle_time` with the CSR option's `idle_mode`);
   validate; drop-and-log invalid ones; score each with
   `compute_weighted_earliness_tardiness(final, instance)` (original scale).
6. Winner = argmin original wET. Register ONCE:
   `_register(report, FFcDDWSolution(schedule=winner, obj_value=..., obj_bound=None))`.
   Step contract invariants hold: one register per call, `elapsed_time`
   monotonic from step entry to `_register`, post-work after `_register`.
7. Post-register artifacts (existing channels):
   - `emit_phase_schedules` → `csr_phase_schedules` gets
     `1_coarse_solver_result` (winner's coarse schedule),
     `2_reconstructed_raw` (winner via `reconstruct_raw_coarse_schedule`),
     `3_final` (winner reconstructed).
   - `draw_cp_trajectory` → in solve_flow mode there is no single CP
     trajectory; synthesize `csr_cp_trajectory` from child history
     registrations (one point per sub-step completion: child-clock elapsed,
     coarse obj). Coarse-scale data stays OUT of the shared `_obj_log.json`
     (existing rule).
   - NEW candidate table: one row per (candidate × reconstruction) —
     `source, coarse_obj, coarse_bound, restored_obj, valid, elapsed_sec` —
     emitted like the `csr_phase_schedules` pattern (controller attribute +
     runner emission, e.g. `<instance>_csr_candidates.csv`). Compact summary
     only (winner source, candidate/dedup counts) goes into `AlgRecord`-level
     `metrics`; no duplicate bulk dumps (Rule 12/14).
8. Fallback: no feasible candidate → follow existing `error_if_infeasible`
   handling (raise or register-nothing stop path consistent with the current
   no-solution branch).

Pure helpers (signature/dedup/candidate selection) go in the algorithm or
solution layer (no orchestration imports) so they are unit-testable.

## 5. Workstreams and file ownership

TDD (red-green-refactor) for all workstreams; tiny instances, solver TLs of a
few seconds; per-package added test runtime target < ~60 s. Run
`uv run ruff check` / `uv run ruff format` on touched files. No git commands.

- **W1 — sw_cp `time_factor`:** `src/ffc_ddw_sum_et/algorithm/sw_cp/**` +
  new tests. `SwCpOption.time_factor`, `SwCpModelBuilder` E/T terms, dispatcher
  objective evals / `insert_idle_time` / due-window logic (partition, PF,
  promotion) audit.
- **W2 — neh_cp `time_factor`:** `src/ffc_ddw_sum_et/algorithm/neh_cp/**` +
  new tests. Includes partial-schedule evaluation and `skip_pf_below_obj`
  logic if due-window-dependent.
- **W3 — flip_makespan_cp `time_factor`:**
  `src/ffc_ddw_sum_et/algorithm/flip_makespan_cp/**` + new tests. Makespan
  model itself is scale-free; wET evaluation/registration must use
  `time_factor`.
- **W4 — mcf_lb `time_factor`:** `src/ffc_ddw_sum_et/algorithm/mcf_lb/**` +
  new tests. Slot-cost / penalty math with `time_factor`; apply the LB
  soundness fallback rule from §3 if exactness is not attainable.
- **W5 — orchestration (after W1-W4):** `orchestration/**`,
  `algorithm/coarsen_solve_reconstruct.py`, `solution/**` (only if helpers land
  there), `metadata/20260711/*.yaml`, integration tests, TODOS.md entry.
  Child-controller construction, `time_factor` plumbing through step methods,
  `solve_flow` parsing/validation, candidate store + reconstruction + winner
  selection + artifacts, experiment/smoke configs.

W1-W4 run in parallel and must not edit files outside their ownership (shared
modules such as `algorithm/cumulative.py`, `solution/**`, `orchestration/**`
are read-only for them — if a change there seems required, stop and report).

## 6. Validation

- Unit: per-workstream `time_factor` tests + `time_factor=1` invariance
  (existing suites untouched and green).
- Integration (W5): tiny synthetic instance, full 5-step `solve_flow`, assert
  (a) child produced ≥ 2 candidates, (b) dedup works, (c) every reconstructed
  candidate validates, (d) registered incumbent is the original-scale argmin,
  (e) parent `obj_bound is None`, (f) legacy no-`solve_flow` path unchanged.
- Experiment configs (`metadata/20260711/`):
  - `csr_subalg_smoke.yaml` — `ins_index: [60, 61]`, small absolute TLs, for a
    quick end-to-end run.
  - `csr_subalg.yaml` — `ins_index: [60, 61]`, the real 5-step flow mirroring
    `metadata/20260710/sw_cp_tl_kappa_0.005.yaml` step options (kappa 0.005,
    `solver_thread_cnt: 8`), `FULL_RUN`.

## 7. Out of scope (v1) — recorded in TODOS.md

- Additional candidate sources: CP solution-callback snapshot ring buffer,
  dispatch/NEH side-candidates (user: "다음에 생각하자").
- Ordering-replay restore modes (machine_sequence / stage_sequence) and the
  post-restore CP polish pass from hybridflowshop.
- External-YAML `solve_flow` reference (rejected in favor of inline).

## 8. Terminology guard

`kappa` in `sw_cp_tl_kappa_0.005.yaml` is the SW-CP per-window TL multiplier
(`non_time_fixed_op_time_limit_multiplier`), NOT the CSR coarsening factor
(`factor`, default 50). Keep the names distinct in code, configs, and docs.

## 9. Implementation notes (W5, 2026-07-11)

Deviations / decisions made during implementation (deltas from the plan text):

- **`solve_base_model_cpsat` time_factor was W5, not W1-W4.** The base CP
  path runs through `CpsatAdapter`/`CpsatOption`, which W1-W4 did not own. W5
  added `CpsatOption.time_factor` (default 1, `>=1` validated) and threaded it
  into `BaseModelBuilder.build`, `insert_idle_time`, and
  `compute_weighted_earliness_tardiness` in `algorithm/cpsat_adapter.py`.
  `time_factor=1` is bit-for-bit unchanged (existing tests untouched, green).
- **`run_profile_fixed_ns` also threaded.** It builds the base CP model
  directly (not via an option), so `self.time_factor` was threaded into its
  `builder.build(...)` and its wET scoring for completeness (not in the v1
  solve_flow, but the audit flagged it as a base-CP construction site).
- **Reconstruction uses `reconstruct_coarse_schedule` as-is (flooring).** The
  task phrasing "honoring the CSR option's `idle_mode`" does not apply in
  solve_flow mode: `idle_mode` only affects the *coarse seed* (which solve_flow
  replaces), and the final original-scale reconstruction always uses standard
  flooring by design (`CoarsenSolveReconstructOption.idle_mode` docstring).
  Plan §4 step 5 already prescribes `reconstruct_coarse_schedule(coarse, inst,
  factor)`, which is what the legacy path uses — followed verbatim.
- **neh_cp `job_priority` in the real config.** The reference
  `sw_cp_tl_kappa_0.005.yaml` uses `due2-weight-pos`, which mixes coarse `p`
  with original due windows on the coarse instance (heuristic ORDER only —
  the registered objective stays correct). The config uses the scale-free
  `weight-due-pos` instead, with a YAML comment explaining why.
- **Compact summary location.** The controller step registers a plain
  `SubroutineReport` (no `metrics` field) and produces no `AlgRecord`, so the
  Rule 12/14 "compact summary" lives on a new controller attribute
  `csr_solve_flow_summary` (candidate/dedup/drop counts + winner source &
  objectives) and is logged; the bulk per-candidate detail is the CSV only.
- **Trajectory convention.** `csr_cp_trajectory` in solve_flow mode is
  synthesized from the child history: one `ProgressLogEntry` per child
  registration at child-clock completion time
  (`report.start_time + report.elapsed_time`, the same convention
  `_save_obj_log` uses for a step's end point), carrying the child's
  coarse-scale `obj_value`/`obj_bound`. It is a dedicated artifact only —
  never merged into the parent's original-scale obj_log.
- **Headless child guards.** The child `FFcDDWSubroutineController` is built
  with no artifact layout / working dir. Verified safe because: the mcf_lb
  emit helpers early-return on `layout is None`; flip's `solver_log_path_getter`
  is only invoked under `log_search_progress`; `phase_schedule_path_getter`
  only under `emit_phase_schedules`; sw_cp / neh_cp step-log emission uses
  `try_get_file_path_for_subroutine` (returns `None` with no sink). v1
  solve_flow configs must keep all draw / emission / log-search flags OFF
  (documented in the step docstring and both configs).
- **Files:** `orchestration/controller_core.py` (time_factor param/attr,
  `csr_candidate_rows`, `csr_solve_flow_summary`, `_record_csr_candidate`),
  `orchestration/controller.py` (time_factor threading ×6 sites,
  `solve_flow` mode + `_coarsen_solve_reconstruct_via_flow` +
  `_synthesize_csr_trajectory`), `algorithm/coarsen_solve_reconstruct.py`
  (`CsrCandidate`, `schedule_sequence_signature`, `dedup_candidates`),
  `algorithm/cpsat_adapter.py` (`CpsatOption.time_factor`),
  `orchestration/ffcddw_single_instance_runner.py` (candidates CSV emit),
  `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml` (`csr_candidates_csv`
  kind), `metadata/20260711/{csr_subalg,csr_subalg_smoke}.yaml`,
  `tests/orchestration/test_csr_solve_flow.py`, `TODOS.md`.
- **Smoke result (metadata/20260711/csr_subalg_smoke.yaml, ~25s wall):**
  Instance 60 (Rep0) final wET 61944 (4 deduped candidates, winner
  `incremental_sw_cp.batch_002`); Instance 61 (Rep1) final wET 48184 (4
  candidates, same winner source). Both `obj_bound` None. Candidates CSV
  emitted to `<instance>/progress/<instance>_csr_candidates.csv`; parent
  obj_log carried only the original-scale winner (no coarse leakage).

## 10. Recommended reading order (understanding the code change)

Two equivalent paths; line numbers are as of commit `cb5d31c` (2026-07-11).

**Path A — commit by commit.** The 8 commits on `20260711_csr_subalg` are
dependency-ordered and each is a self-contained reviewable unit:
`5884a65` docs(plans) → `1a8f7df` feat(sw-cp) → `a1c46b9` feat(neh-cp) →
`7667637` feat(flip-makespan-cp) → `24de340` feat(mcf-lb) →
`14877c8` feat(cpsat-adapter) → `580f40a` feat(csr) → `cb5d31c` chore(config).
Read each with `git show <sha>`; the four algorithm commits are variations of
one pattern, so after sw-cp the next three skim quickly.

**Path B — concept order (recommended for a first pass):**

1. **What the feature looks like:** `metadata/20260711/csr_subalg.yaml` —
   the annotated user-facing schema (`solve_flow` inside the
   `coarsen_solve_reconstruct` step). Then §2-§4 of this document.
2. **The pre-existing `time_factor` reference semantics:**
   `algorithm/cumulative.py` `BaseModelBuilder` (`time_factor` field, E/T
   objective terms). Everything else copies this convention; internalize
   `E_j = max(0, d^- − k·C^c)`, `T_j = max(0, k·C^c − d^+)` here first.
3. **Smallest new threading example:** `algorithm/cpsat_adapter.py` — one
   screenful showing the whole pattern: option field → `build(time_factor=)`
   → `insert_idle_time(time_factor=)` → wET scoring.
4. **The deepest algorithm change, sw_cp:** `algorithm/sw_cp/option.py`
   (field + validation) → `cp_model.py` `_define_partial_et_objective`
   (l.154; scaled completion, T-bound, time-fixed offset) → `dispatcher.py`
   (objective evals, and the `d^+ // k` floor-divided right-justify map —
   the trick used to avoid touching read-only `FFcSchedule`).
5. **The three variations:** `neh_cp/dispatcher.py` (same pattern, many call
   sites), `flip_makespan_cp/dispatcher.py` (makespan model is scale-free —
   only scoring + the `d^+ // k` right-shift cap change),
   `mcf_lb/mcf_lb_pipeline.py` (the `time_factor > 1` ⇒ LB `None` fallback,
   `lb_suppressed_by_time_factor`; threading in `last_stage_sch_builder.py`
   and `full_sch_builder.py`).
6. **Pure candidate helpers:** `algorithm/coarsen_solve_reconstruct.py` —
   `CsrCandidate` (l.76), `schedule_sequence_signature` (l.91),
   `dedup_candidates` (l.126). No orchestration imports; unit-tested directly.
7. **Orchestration state:** `orchestration/controller_core.py` — ctor
   `time_factor` (l.68, attr l.95), `csr_candidate_rows` (l.141),
   `_record_csr_candidate`.
8. **The heart:** `orchestration/controller.py` —
   `coarsen_solve_reconstruct` (l.2640, `solve_flow` branch + legacy path),
   `_coarsen_solve_reconstruct_via_flow` (l.2816: child controller, harvest,
   dedup, restore-all-pick-best, single `_register` with `obj_bound=None`),
   `_synthesize_csr_trajectory` (l.3017). Also the six `self.time_factor`
   threading sites into step options.
9. **Artifact plumbing:** `orchestration/ffcddw_single_instance_runner.py`
   (`csr_candidates_csv` emit, l.769) +
   `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`.
10. **Tests as executable documentation:**
    `tests/orchestration/test_csr_solve_flow.py` (full 5-step integration,
    dedup, argmin selection, legacy-path regression); then the per-package
    `test_*time_factor*` files when diving into a specific algorithm.

Evidence the candidate store matters (first real run,
`output/20260711_csr_subalg/20260711T212342_577417`): on BOTH instances the
winner was NOT the last (coarse-best) candidate — Rep0 coarse 99570 restored
to 66414 while the earlier 99870 restored to 64883; Rep1's coarse-better
batch_003 restored worse (46246 vs 46036). Restore-all-pick-best beat
take-last on its first outing.
