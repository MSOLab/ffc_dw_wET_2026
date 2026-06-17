# MCF-LB intermediate-stage earliness-included approximate cost — per-file work orders

**Date:** 2026-06-18 · **Branch:** `20260617_more_lb` · **Status:** PLAN (no source edited yet)

## 0. How to dispatch this document

This plan is written so that **one subagent per file** can implement its slice
independently. Dispatch rule (user opens a new conversation and says):

> "이 markdown 참조해서, 수정 파일 하나당 subagent 하나씩 시켜 수정하게 만들어."

Each subagent:
1. **Reads §1 (background), §2 (SHARED CONTRACTS), and its own Work Order (WO-n) only.**
   §2 is the single source of truth for every cross-file interface — conform to it
   exactly so independently-edited files stay compatible (contract-first).
2. Edits **only** the one file named in its WO (plus, for new-file WOs, creates that file).
3. Runs `uv run ruff check <file>` and `uv run ruff format <file>` on its file.
4. Keeps the **default / unchanged path byte-identical**. Every behavioral change is
   gated behind the new option value `intermediate_stage_cost="full_et_approx"`; with the
   default `"tardiness_only"` nothing about today's output may change.

Dependency edges are listed per WO and summarized in §3. Subagents may be dispatched in
parallel; references to not-yet-created symbols are fine because §2 pins them. The test
WOs (WO-8, WO-9) and the global build (§5) run after the source WOs land.

---

## 1. Background

### 1.1 What changes and why

The `all_stages` LB projection (`lb_stage_scope="all_stages"`, added 2026-06-17) currently
treats **every** stage's MCF as a *valid lower bound on OPT*:

- **Last stage `c`**: full earliness+tardiness MCF (`vault/bounds_A_C_P3.tex`, "Bound A").
  This is exact in completion time (`τ=0`), so it is a valid LB **and** a good seed.
- **Intermediate stages `i < c`**: weighted-**tardiness-only** MCF
  (`vault/bounds_wT_P3.tex`). Earliness is dropped because the upstream projection only
  lower-bounds completion times, which *over-counts* earliness (the objective is
  non-regular). Dropping earliness keeps `LB_T^(i) ≤ Σ w⁺T ≤ OPT` valid.

**The change:** for the intermediate stages `i = 1 … c-1` we switch the MCF cost from
tardiness-only to the **full earliness+tardiness** V-shaped cost with the due *window*
projected by the downstream tail. This is **no longer a valid lower bound** (see §1.2), so
we **give up using the intermediate-stage MCF objective as a lower bound** and instead
treat it as an **approximate objective function** whose only purpose is to produce a
preemptive schedule that seeds a full schedule. The last stage is **untouched** (its
behavior, LB, schedule, round-2, artifacts all stay byte-identical).

Concretely, with the new mode on:
- the intermediate-stage MCF objective is **excluded** from `combined_lb`
  (`combined_lb` becomes the last-stage full-ET LB alone — still valid);
- the intermediate-stage preemptive schedule is **still** fed to `build_stage_seed_full_sch`
  and its realized seed still competes for the registered global min-wET schedule.

### 1.2 Math (`vault/bounds_A_C_P3.tex`, Prop. "Which stages can be projected")

Fix an intermediate stage `i`. With head `r_j^(i)=Σ_{h<i} p_{hj}` and tail
`τ_j^(i)=Σ_{h>i} p_{hj}`, the projected window bounds are

- `d̄_j^{+,(i)} = d⁺_j − τ_j^(i)`,  `d̄_j^{-,(i)} = d⁻_j − τ_j^(i)`.

For tardiness, `T_j = (C_j − d⁺_j)⁺ ≥ (C_{ij} − d̄_j^{+,(i)})⁺` — the projection
**under-counts** tardiness, so a tardiness-only stage MCF is a valid LB. For earliness,

- `E_j = (d⁻_j − C_j)⁺ ≤ (d̄_j^{-,(i)} − C_{ij})⁺`

— the projection **over-counts** earliness (wrong direction for an LB). Therefore the
**full** V-shaped slot cost at an intermediate stage,

```
c_jt = w⁻_j·⌈(d̄⁻_j − p_{ij} − t + 1)/p_{ij}⌉⁺  +  w⁺_j·⌈(t − d̄⁺_j)/p_{ij}⌉⁺
```

(the last-stage cost of `bounds_A_C_P3.tex` eq:slot-cost, but with `d⁻→d̄⁻`, `d⁺→d̄⁺`,
`p_{cj}→p_{ij}`), is **not** a valid LB for `i < c`. At `i = c`, `τ=0 ⇒ d̄⁻=d⁻`, `d̄⁺=d⁺`,
and the cost collapses to today's last-stage full-ET cost exactly. This is the seam we
exploit: **always project the full-ET window by `τ`** — a no-op at the last stage, the new
approximate cost at intermediate stages.

### 1.3 Scope of edits

| Layer | File | Nature of change |
|---|---|---|
| MCF cost | `algorithm/parallel_mc_pmtn.py` | project full-ET window by `τ` (no-op at last stage) |
| LB layer | `algorithm/mcf_lb/lb_last_stage_pmtn.py` | `obj_bound_is_valid` ⇐ also require `tardiness_only or last stage` |
| Pipeline | `algorithm/mcf_lb/mcf_lb_pipeline.py` | new `intermediate_stage_cost` option; gate LB update on validity |
| Controller | `orchestration/controller.py` | thread `intermediate_stage_cost`; record `*_used` |
| Diagnostic | `algorithm/mcf_lb/diagnostic.py` | `bound_kind` doc + `intermediate_stage_cost_used` field |
| Seed builder | `algorithm/mcf_lb/stage_sch_builder.py` | docstring-only (cost-agnostic clarification) |
| Experiment | `metadata/20260618/...yaml` (NEW) + `main.py` | new comparison scenario |
| Tests | two files under `tests/algorithm/mcf_lb/` | cost shape, non-LB proof, pipeline regression |

---

## 2. SHARED CONTRACTS (pinned — every subagent conforms)

### 2.1 Option name, type, default, placement

A single new option threads the change end to end:

```python
intermediate_stage_cost: Literal["tardiness_only", "full_et_approx"] = "tardiness_only"
```

- Added to **both** `controller.calc_mcf_lb_and_derive_full_sch` and
  `mcf_lb_pipeline.calc_mcf_lb_all_stages_and_derive_full_sch`.
- **Default `"tardiness_only"` ⇒ today's `all_stages` behavior byte-identical.**
- `"full_et_approx"` selects the new earliness-included projected cost at intermediate
  stages and drops those stages from the reported LB.
- Only meaningful under `lb_stage_scope="all_stages"`. Under `last_stage` no intermediate
  stage is solved, so the option is inert (accepted, forwarded nowhere, documented as such).
- Place it as the **final** keyword arg on each signature (after `lb_stage_scope` on the
  controller; after `seed_compare` on the pipeline) so positional callers are unaffected.

### 2.2 MCF cost construction — `parallel_mc_pmtn.py` (WO-1)

`ParallelMachinePreemptionMcf.from_instance(instance, *, r_multiplier=1.0, r_increment=0,
stage_id=None, tardiness_only=False)` — **signature unchanged**. The change is inside
`_define_parameters`, in the `tardiness_only=False` (full-ET) branch only:

- Compute the downstream tail once: `tau = instance.get_job_2_p_sum_after_stage(target_stage)`
  (`target_stage = stage_id or instance.stage_id_list[-1]`).
- Project **both** window bounds: `d_minus_bar = ddw[j][0] − tau[j]`,
  `d_plus_bar = ddw[j][1] − tau[j]`.
- Slot cost (replace raw `d_minus`/`d_plus` with the projected `d_minus_bar`/`d_plus_bar`):
  ```
  C[j][t] = w_minus[j]·ceil((d_minus_bar − p_j − t + 1)/p_j)   if t ≤ d_minus_bar − p_j
            0                                                   if d_minus_bar − p_j < t ≤ d_plus_bar
            w_plus[j]·ceil((t − d_plus_bar)/p_j)                if t > d_plus_bar
  ```
- Horizon: pass the **projected** lower due to the estimator —
  `d_lower = {j: d_minus_bar}` into `compute_parallel_mc_horizon(self.p, self.r,
  self.mc_count, d_lower=d_lower)`.
- **Invariant (last stage byte-identical):** at the last stage `tau ≡ 0`
  (`get_job_2_p_sum_after_stage(last)` is all zeros), so `d_minus_bar == d⁻`,
  `d_plus_bar == d⁺`, and the resulting `C`, `calT`, arcs, supplies are **bit-for-bit**
  what they are today. Do **not** special-case the last stage — projecting by a zero tail
  is the no-op that keeps it identical.
- The `tardiness_only=True` branch (lines ~162–187) is **unchanged**.
- Update the class docstring cost note (lines ~52–67): the full-ET cost now uses the
  τ-projected window `[d⁻−τ, d⁺−τ]`; cite that `τ=0` at the last stage reproduces the
  current cost, and that for `i<c` the earliness arm makes it an **approximate (non-LB)**
  objective per `vault/bounds_A_C_P3.tex`.

### 2.3 LB validity flag — `lb_last_stage_pmtn.py` (WO-2)

`solve_mcf_lb` / `apply_lb_by_mcf` signatures **unchanged**. Only the validity rule in
`apply_lb_by_mcf` changes (line ~284):

```python
last_stage_id = instance.stage_id_list[-1]
is_last_stage = stage_id is None or stage_id == last_stage_id
no_augment = p_increment == 0 and r_multiplier <= 1.0 and r_increment == 0
obj_bound_is_valid = no_augment and (tardiness_only or is_last_stage)
```

Rationale: a non-augmented MCF objective is a valid LB on OPT **iff** it is one of the two
valid relaxations — last-stage full-ET, or any-stage tardiness-only. The new
intermediate-stage full-ET cost (`tardiness_only=False` and `stage_id` an intermediate
stage) is **not** a valid LB, so it must report `obj_bound_is_valid=False`.

- **No regression:** the last-stage path (`stage_id=None, tardiness_only=False`) →
  `is_last_stage=True` → valid as before; the intermediate tardiness-only path
  (`tardiness_only=True`) → valid as before. The only newly-reachable combination
  (intermediate + full-ET) is the one we intend to flag invalid.
- Update the `apply_lb_by_mcf` docstring for `tardiness_only` to state that with
  `tardiness_only=False` at an intermediate `stage_id` the bound is **not** valid
  (`obj_bound_is_valid=False`); the last-stage and tardiness-only cases stay valid.
- Heatmap: leave as-is. Callers pass `draw_heatmap=False` for intermediate stages, and the
  existing guard `if draw_heatmap and not tardiness_only and heatmap_yaml_path is not None`
  remains correct (no behavior change needed for this plan).

### 2.4 All-stages pipeline — `mcf_lb_pipeline.py` (WO-3)

Add `intermediate_stage_cost` (§2.1) to `calc_mcf_lb_all_stages_and_derive_full_sch`.
Inside the intermediate loop (`for q in reversed(stage_id_list[:-1])`, lines ~1027–1087):

```python
tardiness_only = intermediate_stage_cost == "tardiness_only"
apply = apply_lb_by_mcf(
    instance, stage_id=q, tardiness_only=tardiness_only,
    draw_heatmap=False, stop_predicate=stop_predicate, logger=logger,
)
...
seed = build_stage_seed_full_sch(instance, apply.mcf_preemptive_schedule, q,
                                 seed_compare=seed_compare, logger=logger)
...
bound_kind = "tardiness_only" if tardiness_only else "full_et_approx"
intermediate_records.append(StageLbRecord(
    stage_id=q, is_last_stage=False,
    bound_kind=bound_kind,
    mcf_lb=apply.mcf_lb,                      # approximate objective when not valid
    mcf_lb_valid=apply.obj_bound_is_valid,    # False in full_et_approx mode
    init_sched_obj=seed.obj_value,
    delta=seed.obj_value - apply.mcf_lb,
    best_candidate=seed.best_candidate,
    mcf_solve_sec=apply.mcf_solve_sec,
    horizon=apply.mcf.calT[-1], slot_count=len(apply.mcf.calT),
    load_index=load_index, max_release=max_release,
    seed_method=seed.anchor_method,
))

# LB update only when this stage's bound is a *valid* LB:
if apply.obj_bound_is_valid and (combined_lb is None or apply.mcf_lb > combined_lb):
    combined_lb = apply.mcf_lb
    argmax_stage_id = q
# Best-schedule update (min wET) is unconditional — identical in both modes:
if best_obj is None or seed.obj_value < best_obj:
    best_schedule = seed.schedule
    best_obj = seed.obj_value
    best_sched_source = q
```

Key points:
- **Gate the `combined_lb` update on `apply.obj_bound_is_valid`** (NOT on a literal
  comparison). In `tardiness_only` mode every intermediate `apply.obj_bound_is_valid` is
  `True` ⇒ byte-identical to today's max. In `full_et_approx` mode it is `False` ⇒
  intermediate stages never enter the max, so `combined_lb` equals the last-stage full-ET
  LB (the seeded value) and `argmax_stage_id` stays the last stage.
- `mcf_lb_valid` on the record is `apply.obj_bound_is_valid` (was hard-coded `True`).
- The last-stage record (`bound_kind="full_ET"`, `mcf_lb_valid=True`) is unchanged.
- Seed building, `best_*`, accumulators (`total_mcf_solve_sec`, `mcf_solve_count`),
  record ordering (ascending), and stop handling are **all unchanged**.
- Forward `intermediate_stage_cost` from the controller (do **not** thread it into the
  last-stage `calc_mcf_lb_and_derive_full_sch` call — the last stage is untouched).
- Update the `CalcMcfLbAllStagesResult` docstring: intermediate LBs are eligible for the
  `combined_lb` max **only when valid** (tardiness-only mode); in `full_et_approx` mode the
  intermediate MCF is an approximate objective used solely for seeding and is excluded.

### 2.5 Controller — `controller.py` (WO-4)

`calc_mcf_lb_and_derive_full_sch` gains `intermediate_stage_cost` (§2.1) as the final
kwarg. In the `lb_stage_scope == "all_stages"` branch (call at lines ~1290–1307) forward
`intermediate_stage_cost=intermediate_stage_cost`. In the all-stages diagnostic block
(lines ~1424–1435) set `c_diag.intermediate_stage_cost_used = intermediate_stage_cost`
(alongside `c_diag.lb_stage_scope_used = "all_stages"`). The `last_stage` branch, the
single-`_register`-per-call contract, the `start_elapsed`/`elapsed` timing pattern, and the
`result.r1_build_full is None` stop short-circuit are **unchanged**. Update the method
docstring to document the new option (and that it is inert under `last_stage`).

### 2.6 Diagnostic — `diagnostic.py` (WO-5)

- `StageLbRecord.bound_kind` comment/docstring: extend the enumerated values to
  `"full_ET" | "tardiness_only" | "full_et_approx"`. **No field added** to `StageLbRecord`
  (`mcf_lb` holds the approximate objective; `mcf_lb_valid=False` flags it).
- `CalcMcfLbAndDeriveFullSchDiagnostic`: add one defaulted field next to
  `lb_stage_scope_used`:
  ```python
  intermediate_stage_cost_used: str = "tardiness_only"
  ```
  Defaulted ⇒ the `last_stage` / today's serialization only gains one additive key
  (mirrors how `lb_stage_scope_used` was added). Do not reorder existing fields.

### 2.7 Seed builder — `stage_sch_builder.py` (WO-6)

**Docstring-only.** The module/function docstrings say the input is a "tardiness-only MCF
preemptive schedule." The builder is cost-agnostic — it only reads the preemptive *window*.
Reword to "an intermediate-stage MCF preemptive schedule (tardiness-only **or** full-ET
approximate)" in the module docstring (lines ~1–31) and `build_stage_seed_full_sch`
docstring (lines ~69–91). **No code change.**

### 2.8 Experiment config — `metadata/20260618/...yaml` (NEW) + `main.py` (WO-7)

New file `metadata/20260618/20260618_mcf_lb_etapprox_009nc.yaml`, copied from
`metadata/20260617/20260617_mcf_lb_all_stages_009nc.yaml` (same `benchmark_dir`,
`ins_index_source`, `bks_table_csv_path`, `instance_worker_cnt`, `draw_gantt`,
`timelimit: "0.09nc"`), `output_dir: output/20260618`. Scenarios: a paired comparison of
the existing valid-LB intermediate mode vs the new approximate mode (keep the last-stage
baselines too):

```yaml
scenarios:
  - name: mcf_lb_all_stages_tardonly
    output_subdir: mcf_lb_all_stages_tardonly
    timelimit: "0.09nc"
    subroutine_flow:
      - method: calc_mcf_lb_and_derive_full_sch
        lb_stage_scope: all_stages
        intermediate_stage_cost: tardiness_only
        adjust_p: true
        adjust_r: true
        proceed_r2_when_nonpositive_cmax: true
  - name: mcf_lb_all_stages_etapprox
    output_subdir: mcf_lb_all_stages_etapprox
    timelimit: "0.09nc"
    subroutine_flow:
      - method: calc_mcf_lb_and_derive_full_sch
        lb_stage_scope: all_stages
        intermediate_stage_cost: full_et_approx
        adjust_p: true
        adjust_r: true
        proceed_r2_when_nonpositive_cmax: true
```

Routix forwards every key under a `method:` entry as a kwarg to the controller method (this
is how `lb_stage_scope`/`seed_compare`/`adjust_p` are already wired), so **no config-parser
change is needed** — only the new option on the controller signature (WO-4). Point
`main.py` `CONFIG_PATH` at the new file (or run with `--config <path>`).

---

## 3. Dependency graph & dispatch order

```
WO-1 parallel_mc_pmtn   (no dep — cost math; last stage stays no-op)
WO-2 lb_last_stage_pmtn (no dep — validity rule; conforms to §2.3)
WO-5 diagnostic         (no dep — field + doc)
WO-6 stage_sch_builder  (no dep — docstring only)
WO-3 mcf_lb_pipeline    ← WO-1, WO-2 (reads new cost + obj_bound_is_valid)
WO-4 controller         ← WO-3 (option), WO-5 (diag field)
WO-7 config + main.py   ← WO-4 (option name only)
WO-8 test stage MCF     ← WO-1, WO-2
WO-9 test all_stages    ← WO-3, WO-4
```

All source WOs (1, 2, 5, 6) own one file and may be edited in parallel; §2 pins the seams.
WO-3 and WO-4 may also be edited in parallel with the rest (they reference §2-pinned
symbols). Run WO-8 / WO-9 and §5 after the source WOs land.

---

## 4. Work Orders

### WO-1 — `src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py`
**Goal:** project the full-ET slot-cost window by the downstream tail `τ` (§2.2), making the
last stage byte-identical (`τ=0`) and intermediate stages an earliness-included approximate
cost.
**Changes:** In `_define_parameters`, full-ET branch (lines ~189–212): compute
`tau = instance.get_job_2_p_sum_after_stage(target_stage)` (the helper already exists and is
used by the tardiness-only branch at line ~168); derive `d_minus_bar`/`d_plus_bar` and feed
the V-shaped cost + the horizon's `d_lower` from the projected lower due. Leave the
`tardiness_only=True` branch untouched. Update the class docstring cost note (lines ~52–67).
**Reference:** tardiness-only branch (lines ~162–187) already shows the `tau` lookup;
full-ET cost template at lines ~200–212.
**Invariant:** default (`stage_id=None, tardiness_only=False`) construction is bit-for-bit
identical — same `C`, `calT`, arcs, supplies — because `τ≡0` at the last stage.
**Accept:** `uv run ruff check`; covered by WO-8 (last-stage cost equality + intermediate
projected-window shape).

### WO-2 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/lb_last_stage_pmtn.py`
**Goal:** make `obj_bound_is_valid` reflect that an intermediate full-ET cost is **not** a
valid LB (§2.3).
**Changes:** at line ~284 replace the validity expression with the `no_augment and
(tardiness_only or is_last_stage)` rule (resolve `last_stage_id = instance.stage_id_list[-1]`
and `is_last_stage = stage_id is None or stage_id == last_stage_id`). Update the
`tardiness_only` arg docstring in `apply_lb_by_mcf`.
**Invariant:** last-stage full-ET and any-stage tardiness-only callers keep
`obj_bound_is_valid=True`; only the intermediate full-ET combination flips to `False`.
**Accept:** `uv run ruff check`; covered by WO-8 (validity matrix) and existing
`test_stage_projection_mcf.py::test_upstream_bottleneck_intermediate_lb_beats_last_stage_et`
(tardiness-only stays valid) must still pass.

### WO-3 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/mcf_lb_pipeline.py`
**Goal:** add `intermediate_stage_cost` and route the intermediate cost + LB-validity gating
(§2.1, §2.4). Do **not** touch `calc_mcf_lb_and_derive_full_sch` or the last-stage call.
**Changes:** add the kwarg (final position) to
`calc_mcf_lb_all_stages_and_derive_full_sch`; in the intermediate loop set
`tardiness_only = intermediate_stage_cost == "tardiness_only"`, pass it to
`apply_lb_by_mcf`, set `bound_kind`/`mcf_lb_valid` from the apply result, and gate the
`combined_lb`/`argmax_stage_id` update on `apply.obj_bound_is_valid`. Update the
`CalcMcfLbAllStagesResult` docstring.
**Reference:** intermediate loop at lines ~1027–1087; record build ~1061–1078; LB/best
update ~1080–1087.
**Invariant:** with `intermediate_stage_cost="tardiness_only"` (default) the result is
byte-identical to today (every intermediate `obj_bound_is_valid` is `True`).
**Accept:** WO-9.

### WO-4 — `src/ffc_ddw_sum_et/orchestration/controller.py`
**Goal:** thread `intermediate_stage_cost` through `calc_mcf_lb_and_derive_full_sch` (§2.5).
**Changes:** add the kwarg (final position, after `lb_stage_scope`); forward it into the
`algo_calc_mcf_lb_all_stages_and_derive_full_sch(...)` call (lines ~1290–1307); set
`c_diag.intermediate_stage_cost_used = intermediate_stage_cost` in the all-stages diagnostic
block (lines ~1424–1435). Extend the method docstring (note it is inert under `last_stage`).
**Invariant:** `last_stage` path unchanged; exactly one `_register` per call; timing pattern
unchanged.
**Accept:** WO-9 + manual: a 3-stage instance with
`lb_stage_scope="all_stages", intermediate_stage_cost="full_et_approx"` registers a
schedule and `obj_bound == last-stage LB` (intermediate stages excluded from the bound).

### WO-5 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py`
**Goal:** §2.6 — `bound_kind` doc + `intermediate_stage_cost_used` field.
**Changes:** extend the `StageLbRecord.bound_kind` comment to include `"full_et_approx"`;
add `intermediate_stage_cost_used: str = "tardiness_only"` to
`CalcMcfLbAndDeriveFullSchDiagnostic` next to `lb_stage_scope_used`.
**Invariant:** no field reordering; existing serialization gains exactly one additive
defaulted key.
**Accept:** `uv run python -c "from dataclasses import asdict; from
ffc_ddw_sum_et.algorithm.mcf_lb.diagnostic import CalcMcfLbAndDeriveFullSchDiagnostic as D;
print(asdict(D()))"` runs and shows `intermediate_stage_cost_used='tardiness_only'`.

### WO-6 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/stage_sch_builder.py`
**Goal:** §2.7 — docstring clarification only (no code change).
**Changes:** reword module docstring (lines ~1–31) and `build_stage_seed_full_sch` docstring
(lines ~69–91) so they no longer claim the input is necessarily tardiness-only; it is "an
intermediate-stage MCF preemptive schedule (tardiness-only or full-ET approximate)."
**Invariant:** zero behavioral change.
**Accept:** `uv run ruff check`.

### WO-7 — `metadata/20260618/20260618_mcf_lb_etapprox_009nc.yaml` (NEW) + `main.py`
**Goal:** §2.8 — comparison harness for the two intermediate-cost modes.
**Changes:** create the YAML per §2.8 (copy infra fields from
`metadata/20260617/20260617_mcf_lb_all_stages_009nc.yaml`, `output_dir: output/20260618`,
the four scenarios — keep last-stage baselines optional but include at least the two
`all_stages` scenarios shown). Point `main.py` `CONFIG_PATH` at the new file.
**Invariant:** no Python behavior change beyond the `CONFIG_PATH` constant.
**Accept:** `uv run python main.py --config metadata/20260618/20260618_mcf_lb_etapprox_009nc.yaml`
with a tiny `ins_index` smoke list runs both `all_stages` scenarios without error.

### WO-8 — `tests/algorithm/mcf_lb/test_stage_projection_mcf.py`
**Goal:** validate the new intermediate full-ET projected cost and the validity flip
(WO-1/WO-2). **Extend** the existing file (do not delete the tardiness-only tests).
**Add asserts:**
- **Last-stage cost unchanged:** build `ParallelMachinePreemptionMcf.from_instance(instance)`
  on the existing instances; assert its `C`/`calT` equal those built before the change
  (pin the exact hand-derived V-shaped values on a tiny instance — `τ=0` no-op).
- **Intermediate full-ET projected-window shape:** with
  `from_instance(instance, stage_id=<intermediate>, tardiness_only=False)`, assert both
  arms appear and use `d̄⁻=d⁻−τ`, `d̄⁺=d⁺−τ`, `p=p_{ij}`
  (`C[j][t] = w⁻·⌈(d̄⁻−p−t+1)/p⌉⁺ + w⁺·⌈(t−d̄⁺)/p⌉⁺`).
- **Non-LB demonstration (the point):** build a tiny instance (reuse `_brute_force_opt`)
  where the intermediate full-ET MCF objective **exceeds** OPT — e.g. an early upstream
  stage with tight `d⁻` so the projection charges phantom earliness — proving it is not a
  valid LB.
- **Validity matrix (WO-2):** `apply_lb_by_mcf(instance, stage_id=<intermediate>,
  tardiness_only=False).obj_bound_is_valid is False`; `…tardiness_only=True…` is `True`;
  last-stage full-ET (`stage_id=None`) is `True`.
**Accept:** `uv run pytest tests/algorithm/mcf_lb/test_stage_projection_mcf.py` green.

### WO-9 — `tests/algorithm/mcf_lb/test_calc_mcf_lb_all_stages.py`
**Goal:** validate the pipeline + controller option (WO-3/WO-4). **Extend** the existing
file.
**Add asserts:**
- **`full_et_approx` LB semantics:** run the controller (and/or pipeline) with
  `lb_stage_scope="all_stages", intermediate_stage_cost="full_et_approx"` on a ≥3-stage
  instance; assert `combined_lb == last-stage full-ET LB`, `argmax_stage_id == last stage`,
  every intermediate `StageLbRecord` has `bound_kind == "full_et_approx"` and
  `mcf_lb_valid is False`, and the registered `obj_bound == combined_lb`.
- **Schedule still seeded:** `best_init_sched_obj`/registered obj is finite and `≤` the
  last-stage-only seed obj (intermediate seeds still compete).
- **Regression:** `intermediate_stage_cost="tardiness_only"` (default) reproduces today's
  `all_stages` result — same `combined_lb`, `argmax_stage_id`, per-stage `mcf_lb_valid` all
  `True`, registered obj/bound — and `lb_stage_scope="last_stage"` is unchanged.
- **Diagnostic field:** `c_diag.intermediate_stage_cost_used` reflects the passed value.
**Accept:** `uv run pytest tests/algorithm/mcf_lb/test_calc_mcf_lb_all_stages.py` green.

---

## 5. Global acceptance (after all WOs)
- `uv run ruff check && uv run ruff format && uv run pytest`.
- **Regression:** a representative instance under `lb_stage_scope="last_stage"` and under
  `lb_stage_scope="all_stages", intermediate_stage_cost="tardiness_only"` yields the same
  incumbent / LB / diagnostic as `main` (both default paths untouched).
- Smoke run of the WO-7 config on a few `ins_index`, then the full `0.09nc` run; confirm
  `etapprox` reports `combined_lb` equal to the last-stage LB while its registered wET may
  differ from `tardonly` (different seeds).

## 6. Decisions & risks
- **D1 (gated option, default off).** New behavior lives behind
  `intermediate_stage_cost="full_et_approx"`; default `"tardiness_only"` keeps every existing
  path byte-identical and lets the two modes be A/B compared (matches the 2026-06-17
  `lb_stage_scope` / `seed_compare` convention). *Tradeoff vs KISS:* a single replacement
  would be simpler, but the codebase consistently keeps prior LB behavior runnable for
  comparison; flipping the default later is a one-line change.
- **D2 (last stage untouched).** Achieved by projecting the full-ET window by `τ` (no-op at
  the last stage) rather than special-casing — one code path, last stage provably identical.
- **D3 (intermediate = approximate objective, not LB).** `combined_lb` excludes
  intermediate stages in the new mode; gating is driven by `apply.obj_bound_is_valid`
  (single source of validity, set in WO-2) rather than re-deriving validity in the pipeline.
- **D4 (single MCF per intermediate stage).** The new mode solves only the full-ET MCF per
  intermediate stage (no extra tardiness-only solve) — we deliberately give up the valid
  intermediate LB, so there is nothing to also compute. Net MCF count is unchanged vs the
  current `all_stages` path.
- **R1 (validity correctness).** If `obj_bound_is_valid` were left `True` for the
  intermediate full-ET cost, `combined_lb` could exceed OPT (invalid bound). WO-2 + the
  WO-3 gating + the WO-8 non-LB test guard this directly.
- **R2 (seed quality).** Whether the earliness-included seed beats the tardiness-only seed
  is empirical; the WO-7 paired scenarios measure it. No correctness dependency — both feed
  the same min-wET registration.
- **R3 (horizon).** The projected `d̄⁻` can be ≤ 0 for early stages with large tails; the
  earliness arm then contributes nothing in `[1, H]` and the horizon estimator still returns
  `≥ max_j r_j + ceil(Σp/m)`. No empty-`calT` risk beyond today's guard.
