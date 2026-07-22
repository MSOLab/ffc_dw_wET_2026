# CSR inner-solve trajectory → `obj_log.json`

**Date:** 2026-07-20 · **Status:** planned (not executed)
**Related:** [`../../analysis/20260719/csr_init_k_budget_consolidation.md`](../../analysis/20260719/csr_init_k_budget_consolidation.md),
`plans/20260715/csr_candidates_found_sec_column.md` (added `sec_elapsed_step`)

---

## 1. Question

The `20260719_merge_csr_k1_32` scatter shows κ > 1 doing badly. To a first-time
viewer that reads as "the implementation is broken", and the scatter carries
nothing that distinguishes *broken* from *correctly solving an approximated
problem*. We want the objective-over-time picture that
`*_multi_scenario_subroutine_flow_comparison.html` already draws for `sw_cp`
scenarios, so the inner solve's convergence can be seen to be healthy while its
endpoint is bad.

Today CSR draws as a **single point** in that chart:

```
csr_full_d2wp_k2 / …_obj_log.json
  obj_value.data = {"22.571": 246453.0}
```

## 2. Why it is a single point (and why that is not a bug)

`coarsen_solve_reconstruct` is a **composite step**: it runs the whole
`solve_flow` on the coarsened instance via a headless child controller and then
registers once. That is required by the subroutine step contract in
`CLAUDE.md` — "at most one register per step call" — which the `_obj_log.json`
aggregator depends on. Emitting more registrations would break it.

**The contract already provides the escape hatch.**
`FFcDDWSubroutineReport.progress_log` exists exactly for this
("*algorithm-frame trajectory … empty for steps that don't capture intra-step
trajectories*", `subroutine_report.py:32`) and `_save_obj_log` folds it into the
global timeline alongside the step's end point
(`ffcddw_single_instance_runner.py:605-614`):

```python
for entry in report.progress_log:
    t_global = report.start_time + entry.elapsed_sec
```

`neh_cp`, `sw_cp` and `solve_base_model_cpsat` already pass it through
`self._register(report, sol, progress_log=…)`. CSR is the only step with a real
intra-step trajectory that does not.

**The data is already harvested.** `_coarsen_solve_reconstruct_via_flow` builds
`candidate_rows` carrying, per child registration, `sec_elapsed_step`
(child-frame wall clock, added in `4a08adf`) and `restored_obj` (original-scale
objective). Measured, κ=1, `Instance_100_10_3_0,6_0,2_10_Rep0`:

| source | `sec_elapsed_step` | `restored_obj` |
| --- | --- | --- |
| `1-calc_mcf_lb_and_derive_full_sch` | 1.25 | 299 485 |
| `2-run_flip_makespan_cp_from_incumbent` | 3.59 | 285 578 |
| `3-neh_cp` | 10.87 | 278 658 |
| `4-incremental_sw_cp.1-batch_002` | 19.05 | 253 600 |
| `4-incremental_sw_cp.2-batch_003` | 22.51 | 250 281 |

That is the trajectory, in the original objective scale, at batch granularity.
It is written to `progress/<ins>_csr_candidates.csv` and then dropped on the
floor instead of reaching `obj_log.json`.

> **κ > 1 runs cannot be replotted from the archive.** `sec_elapsed_step` landed
> in `4a08adf` (2026-07-15 18:32); every κ > 1 run (2026-07-13/14) predates it and
> carries only the old `elapsed_sec` column, which is reconstruction cost, not a
> timestamp. A re-run is required regardless of this code change.

## 3. Change

One production site, in `_coarsen_solve_reconstruct_via_flow`
(`orchestration/controller.py`, at the `_register` call ~line 2978).

```python
self._register(report, sol, progress_log=progress_log)
```

where `progress_log` is built from the already-computed `candidate_rows`.

### 3.1 Design decisions

**(a) `obj_value` = `restored_obj`, never `coarse_obj`.** The chart overlays CSR
against non-CSR scenarios on one axis; a coarse objective is measured on an
inflated instance (`p → ceil(p/κ)` with due windows preserved) and is not
comparable. `restored_obj` is the original-scale objective of the reconstructed
schedule — the same quantity every other scenario plots.

**(b) Emit the running minimum, not the raw per-candidate value.** `obj_log`
semantics is an incumbent trajectory. Nothing guarantees `restored_obj` is
monotone across inner steps (a later coarse solution can reconstruct worse), and
the step's registered end value is the **argmin** over candidates. Emitting raw
values would let the line rise and then disagree with its own endpoint — the
precise "looks broken" artifact this work exists to remove. Running-min keeps
the trajectory consistent with the registered end point by construction.

**(c) `obj_bound` only when `factor == 1`.** At κ > 1 the coarse bound is a lower
bound for the inflated instance and is **not** a valid original-scale LB — the
step already hard-codes `obj_bound=None` for exactly this reason. At `factor == 1`
coarsening is an exact identity (verified in the consolidation doc), so
`coarse_bound` is a genuine LB and is worth carrying: it is the only κ where the
chart can show a UB/LB pair. Guard on `factor == 1`, not on "bound is not None".

**(d) Time frame: add the pre-child offset.** `sec_elapsed_step` is child-frame,
but `_save_obj_log` adds `report.start_time`, which is *parent step entry*. The
gap is coarsening + child construction. Capture it explicitly rather than
letting it silently under-report:

```python
child_offset = time.monotonic() - start_elapsed   # measured just before child.run()
elapsed_sec = child_offset + row["sec_elapsed_step"]
```

**(e) Skip invalid candidates.** `restored_obj is None` when reconstruction
failed; those rows must not enter the trajectory.

**(f) Build the tuple before `elapsed = time.monotonic() - start_elapsed`.** The
contract requires no work between the elapsed measurement and `_register`
(`CLAUDE.md` §Subroutine step contract, invariant 2). The loop is O(candidates)
and trivial, but it goes *above* the measurement line, not below.

### 3.2 Honest limitation to record in the plan and the write-up

Reconstruction happens **after** the child finishes, in the scoring loop. So CSR
did not *hold* a schedule of quality `restored_obj` at time `sec_elapsed_step` —
the trajectory is a **post-hoc attribution** of "when the inner solve reached
this coarse solution" onto the original scale. This is the right quantity for
"was the inner solve converging healthily", and it is not a live incumbent
trace. Say so in the slide notes; a reviewer will otherwise find it and it will
cost more than volunteering it.

The legacy (non-`solve_flow`) CSR path is out of scope — every scenario in this
analysis uses `solve_flow`.

## 4. TDD steps

Tests live in `tests/orchestration/test_csr_solve_flow.py` (already asserts
non-decreasing `sec_elapsed_step` and the CSV headers).

1. **Red** — assert the registered report's `progress_log` is non-empty, is
   non-decreasing in `elapsed_sec`, and its last `obj_value` equals the report's
   registered `obj_value`.
2. **Green** — implement §3.
3. **Red** — `factor=2` run emits every entry with `obj_bound is None`;
   `factor=1` run carries the `calc_mcf_lb…` bound.
4. **Red** — a non-monotone `restored_obj` sequence still yields a non-increasing
   `progress_log` (running-min), and no entry is emitted for an invalid candidate.
5. **Refactor**, then `uv run ruff check` / `uv run ruff format`.

Existing guard to keep green: the one-register-per-step assertion and the
`_obj_log` aggregation tests.

## 5. Experiment

Re-run the **160-instance (T,R)=(0.6,0.2) subset** — the same subset the
`merge_csr_k1_32` report and the slide use.

- Scenarios: `csr_full_d2wp` × κ ∈ {1,2,4,8,16,32}. Add `csr_neh_d2wp` only if
  the slide keeps both flows; it doubles the cost for a second line.
- Budget: unchanged (`0.0225nc`, f = 25 %) — the point is to explain the
  *existing* numbers, so nothing else may move.
- Config: new `metadata/20260720/csr_k_sweep_progress.yaml`, copying scenario
  flows from `metadata/20260713/csr_init_methods.yaml` + the higher-K config,
  with `draw_progress_plot: false` (the flow-comparison chart reads
  `obj_log.json` directly and does not need the per-instance plot).
- Cost: `csr_full_d2wp_k2` averaged 21.2 s/instance → ≈ 5 min per κ at
  `instance_worker_cnt: 12` (× `solver_thread_cnt: 8` = 96 cores). Six κ ≈ 30 min.

### 5.1 Validation gate (do this before drawing anything)

The re-run uses **newer code** than the archived κ > 1 runs (notably the
`insert_idle_time` fix, `9b7ad2a`). Before the trajectory is used to explain the
slide, confirm it explains *that* slide: mean RPDf per (flow, κ) must reproduce
the merged report within run-to-run noise —

| κ | 1 | 2 | 4 | 8 | 16 | 32 |
| --- | --- | --- | --- | --- | --- | --- |
| `full` | 23.396 | 33.778 | 34.853 | 34.157 | 35.784 | 43.613 |

(`analysis/20260719_csr_k/csr_k_range.csv`, secondary view). A material drift
means the trajectory describes a different experiment than the slide, and the
slide numbers must be regenerated from the new run instead.

## 6. Deliverables

1. `output/20260720_csr_k_progress/<ts>/` — run + its
   `*_multi_scenario_subroutine_flow_comparison.html`, now with real CSR curves.
2. A `scripts/20260720/` script for the **mean** trajectory (the chart is
   per-instance; the slide needs "average objective over time" across the 160).
   Instances have different budgets (`0.09nc` varies with n, c), so averaging on
   raw seconds mixes scales — average over **normalized time** `t / (0.0225nc)`,
   matching the scatter's existing normalized-time axis.
3. Analysis doc under `plans/analysis/20260720/` per the merged-analysis rule,
   carrying the §3.2 limitation and the §5.1 gate result.

## 7. What this does *not* do

It does not explain *why* κ > 1 is worse — it shows the inner solve converging
normally, which rules out "broken". The mechanism is the `ceil` inflation of the
coarse instance (measured: +3.0 % / +9.5 % / +23.7 % / +54.4 % / +120.8 % in mean
effective processing time at κ = 2/4/8/16/32 over the 160-instance subset), and
that argument needs no re-run at all. The two are complementary: this plan
supplies "correctly solved", the inflation table supplies "the wrong problem".
The inflation numbers are currently ad-hoc and must be committed as a script
before being quoted.

---

## 8. Follow-up decision (2026-07-21): a dedicated coarse-scale CSR flow artifact

§3.1(a)/(b) made a deliberate, correct choice **for the shared chart**: plot the
running-min of `restored_obj` (original scale), never `coarse_obj`. That keeps
CSR comparable to non-CSR scenarios on one axis and monotone by construction.
**That choice is kept unchanged** — the merged `progress_log` shipped in §3 stays
exactly as-is.

But it has a cost §3.2 already half-admits: the shared curve is a *post-hoc
attribution* onto the original scale, not the trajectory the inner solver
actually optimized. The inner solve minimizes the **coarse** objective; "is the
inner solve converging healthily" is literally a question about *that* curve.
Running-min-of-`restored_obj` cannot rise, so it structurally cannot show the one
diagnostic that would distinguish a healthy inner solve on the wrong problem from
a sick one.

**Decision:** keep the shared UB curve (§3.1a/b), and add a **separate
coarse-scale CSR inner-flow artifact** that treats the child solve as a
first-class flow in its own right. Two files answer two different questions:

| file | scale | y-axis | monotone? | question |
| --- | --- | --- | --- | --- |
| `*_multi_scenario_subroutine_flow_comparison.html` (shared) | original | `restored_obj` running-min | yes (UB incumbent) | how fast does CSR hand back a good *original* solution vs other scenarios? |
| new CSR inner-flow artifact | coarse-world (`factor·C^c`, original-scale magnitude) | child `obj_value` / `obj_bound` | yes (coarse incumbent, minimized) | is the inner solve converging healthily on the problem it actually solves? |

### 8.1 Why a separate file (coarse obj is original-scale, but a different quantity)

**Correction to an earlier draft of this section.** The coarse objective is
*not* on a shrunken 1/κ scale, and `rpd_f` against `BKS_data` is *not*
meaningless. `compute_weighted_earliness_tardiness(schedule, instance,
time_factor=factor)` scores a coarse completion as `factor·C^c` against the
**original** due window (`objectives.py:12-27`; `coarsen_processing_times`
preserves the original-scale window as SSOT). The `factor` exactly cancels the
coarse grid, so the child's `obj_value` is an **original-scale magnitude**,
directly RPDf-comparable to `BKS_data`. The reused RPDf chart is therefore
semantically valid — the earlier "units error" claim was wrong.

The separate file is justified not by units but by three other differences:

1. **Different quantity.** coarse `obj_value` is the coarse schedule's quality
   measured on the original grid (`factor·C^c`); `restored_obj` (shared chart)
   is the *reconstructed* feasible schedule re-optimized on the true processing
   times. Same scale, different number.
2. **Different time frame.** The inner solve runs in child-clock over the
   *inner* budget; the shared chart's x is the outer controller clock over the
   *outer* budget. Overlaying them on one x-axis mixes frames.
3. **Per-inner-step flow.** The point is to watch the inner flow
   (mcf→…→base_cp) converge, which the shared chart collapses to one CSR point.

The rounding mode still biases the coarse *value* (not its units), and this is a
genuine caveat for reading the chart — keep the table as a **value-bias** guide:

| mode | `factor·p'` vs `p` | coarse obj vs original optimum |
| --- | --- | --- |
| `ceil` | `≥` | over-states → valid **UB** on OPT |
| `round` | either | neither UB nor LB |
| `floor` | `≤` | under-states → **neither** (can dip below `BKS_data`, giving a negative RPDf — itself a "coarse world is easier" signal) |

The inner trajectory is **monotone decreasing**: the inner solver minimizes
exactly this `factor·C^c` objective, so each child step improves or holds the
coarse incumbent. The "curve rises" worry belongs to `restored_obj` (§3.1b),
not here.

### 8.2 What already exists (the seed)

`_synthesize_csr_trajectory(child)` (`controller.py:3073`) already builds a
coarse-scale trajectory: one `ProgressLogEntry` per child *registration*, carrying
the child's coarse `obj_value` / `obj_bound`, x = child-clock step-end. It is
stored on `self.csr_cp_trajectory`, gated behind `draw_cp_trajectory=True`, and
its docstring already states it is **never merged into the parent obj_log**. The
runner emits it as `<ins>_csr_cp_trajectory.json` + `.png`
(`_render_trajectory_line` in `reporting.py`, artifact keys
`csr_cp_trajectory_json` / `csr_cp_trajectory_png`).

So the code already agrees with the §8 direction — a coarse trajectory kept out
of the shared chart. What is missing is (a) granularity and (b) a cross-scenario
view.

### 8.3 The gap and the design

**Granularity.** `_synthesize_csr_trajectory` records one point per child *step*
(mcf, flip, neh_cp, sw_cp, base_cp) — the step endpoints only. But each inner
step already carries its own intra-step `progress_log` (neh_cp / sw_cp /
base_cpsat pass it through `_register`). To render the inner solve *as a flow*
with batch-level convergence, fold each child step's `progress_log` in too — i.e.
run the same aggregation `_save_obj_log` already performs, but over the **child's**
history and on the coarse scale, emitting `<ins>_csr_inner_obj_log.json`.

This is the honest realization of "treat the inner solve as its own flow": the
child is a full `FFcDDWSubroutineController` with a real multi-step history; give
it the same obj_log treatment the parent gets, in its own coarse namespace.

- **`obj_value`** = child coarse `obj_value` (the quantity being minimized).
- **`obj_bound`** = child coarse `obj_bound`. This *is* a valid LB **for the
  inflated instance** (the child's own problem), so it belongs here even at κ>1 —
  label it "coarse LB", not an original-scale bound.
- **x** = child-clock (`report.start_time + entry.elapsed_sec`), same convention
  as `_save_obj_log`. No parent offset — this file lives in the child frame, so
  t=0 is inner-solve start.

**Cross-scenario view.** Generalize `iter_scenario_instance_progressions` with an
`obj_log_kind` parameter (default `obj_log_json`) and pass
`csr_inner_obj_log_json`; **do not fork the writer**. (This resolves the earlier
open question — the one-parameter loader generalization won.) Two coarse-specific
requirements the shared writer does *not* satisfy out of the box, both **required**:

- **Normalize x by the inner budget, not the outer timelimit.** The shared
  builder computes `norm_time = global_sec / manifest.timelimit`, where
  `manifest.timelimit` is the *outer* instance budget. The inner obj_log lives in
  child-clock `[0, inner_budget]`, so dividing by the outer budget squeezes every
  inner trajectory into the leftmost `inner/outer` fraction. The inner chart must
  normalize by the **inner (child) budget** so each trajectory **starts at 0 and
  spans `[0, 1]`**. There is no intent to show "where the inner solve sits within
  the outer budget".
- **A distinct title / axis label.** Reusing
  `export_multi_scenario_method_rpdf_comparison_html` verbatim inherits the shared
  chart's title ("Subroutine flow mean over-time RPDf by scenario") — a DRY slip
  that makes the coarse chart indistinguishable from the original-scale one. The
  inner chart needs its own labels marking it coarse-world inner-flow (e.g. title
  "CSR inner-solve (coarse) RPDf by κ", x = "Normalized inner-solve time").

### 8.4 Plumbing

1. New artifact keys `csr_inner_obj_log_json` (and, if a per-instance PNG is
   wanted, `csr_inner_obj_log_png`) in the `ArtifactLayout` progress zone,
   alongside the existing `csr_cp_trajectory_*`.
2. Aggregation: a small function that takes `child.solution_manager.history` and
   writes the coarse obj_log — factor the shared body out of `_save_obj_log`
   rather than copy it (DRY; `_save_obj_log` is currently a method that hard-codes
   the `obj_log_json` key and the parent history).
3. Emission site: in `coarsen_solve_reconstruct`'s post-register block (the
   contract-safe zone, after `_register`), guard identical to the existing
   `draw_cp_trajectory` gate — or promote to always-on if the file is cheap and
   the slide always wants it. Decide against `draw_progress_plot`/`draw_cp_trajectory`
   at implementation.
4. Chart writer: one added invocation in the post-run reporting path, honoring
   the two §8.3 coarse-specific requirements (inner-budget x-normalization, and a
   distinct title/axis label rather than the shared chart's).
5. **Deprecate `csr_cp_trajectory`.** `csr_inner_obj_log_json` is a strict
   superset of the coarse step-endpoint `csr_cp_trajectory` (intra-step
   granularity + a cross-scenario view). Remove `_synthesize_csr_trajectory`, the
   `csr_cp_trajectory` attribute, the `draw_cp_trajectory` gate,
   `_render_trajectory_line`, and the `csr_cp_trajectory_json` / `csr_cp_trajectory_png`
   artifacts (and their tests).

### 8.5 TDD steps

Tests live beside the existing CSR flow tests
(`tests/orchestration/test_csr_solve_flow.py`,
`tests/orchestration/test_csr_artifact_emit.py`).

1. **Red** — the coarse aggregator over a child history with multi-step
   `progress_log` yields a JSON payload whose `obj_value.data` has more points
   than there are child steps (proves intra-step folding, not just step
   endpoints).
2. **Green** — implement §8.4(2).
3. **Red** — `obj_bound.data` is populated at κ>1 (child coarse LB is carried
   here, unlike the parent obj_log which nulls it) and x-coordinates start at
   the child frame origin, not the parent offset.
4. **Red** — the artifact is written to `csr_inner_obj_log_json` and is absent
   when the CSR step finds no solution.
5. **Refactor**, then `uv run ruff check` / `uv run ruff format`.

### 8.6 Scope

`solve_flow` CSR path only (the legacy path has no child flow). The κ-sweep
re-run in §5 is the natural place to first render the new comparison; no separate
experiment is required for it.

### 8.7 Implementation status (2026-07-21)

**Landed** (per-instance data + cross-scenario plumbing), `pytest` + `ruff`
green:

- `csr_inner_obj_log_json` artifact key; per-instance emission in
  `_emit_csr_artifacts`, **always-on** for the solve_flow path (resolves §8.4(3):
  not gated behind `draw_cp_trajectory`).
- `_fold_history_into_obj_log_dicts` extracted from `_save_obj_log` (DRY,
  §8.4(2)); `csr_child_history` preserved on the controller.
- `iter_scenario_instance_progressions(obj_log_kind=…)` generalization +
  `_maybe_write_csr_inner_flow_comparison_html` + `csr_inner_flow_comparison_html`
  key (resolves §8.3: loader generalized, writer not forked).

Validity note: because coarse `obj_value` is original-scale (§8.1), the reused
RPDf-vs-`BKS_data` y-axis is correct as-is; no y-axis change is needed.

**Pending code** (separate conversation):

1. **x-axis: normalize by inner budget** so each trajectory starts at 0 and spans
   `[0, 1]` (§8.3) — currently normalizes by the outer manifest timelimit.
2. **Distinct title / axis label** for the inner chart (§8.3) — currently inherits
   the shared chart's labels via the DRY writer reuse.
3. **Remove `csr_cp_trajectory`** (§8.4(5)) — superseded by
   `csr_inner_obj_log_json`.

Minor cleanup while in the file: the obj_log payload-wrapping dict is now
duplicated between `_save_obj_log` and the inner-emit block — fold into a
`_write_obj_log_payload(path, value_data, value_notes, bound_data, bound_notes)`
helper.
