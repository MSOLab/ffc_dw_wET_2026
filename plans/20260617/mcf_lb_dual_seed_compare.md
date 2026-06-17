# MCF-LB dual-seed compare (simple vs midpoint) — per-file work orders

**Date:** 2026-06-17 · **Branch:** `20260615_more_time_limit` · **Status:** IMPLEMENTED
(2026-06-18; all WOs landed). See **D2** in §6 — the always-on design was reversed to an
opt-in `seed_compare` option.

## 0. How to use this document

One **subagent per file**. Dispatch rule:

> "이 markdown file 참조해서, 수정해야하는 파일 하나당 하나씩의 subagent를 시켜서 수정하게 만들어."

Each subagent:

1. Reads §1 (background), §2 (SHARED CONTRACTS), and its own Work Order (WO-n) only.
   §2 is the single source of truth for every cross-file interface — conform exactly.
2. Edits **only** the one file named in its WO (new-file WOs create that file).
3. Runs `uv run ruff check <file>` / `uv run ruff format <file>`.
4. Keeps the **single-seed → identical-output** invariant where stated: the comparison
   must never produce a schedule worse than today's midpoint-only result (it adds a
   candidate, it never removes the existing one).

Dependency edges are in §3. References to not-yet-created symbols are fine (§2 pins them).
Tests (WO-7, WO-8) and the global build (§5) run after the others land.

---

## 1. Background

### 1.1 The two seed-construction methods

Both the last-stage and the intermediate (non-last-stage) seed pipelines turn an **MCF
preemptive window** on one stage into a **full schedule feasible for the original
problem**. The stage-only "seed" is built one of two ways:

- **`midpoint`** (current default everywhere): place each job at the midpoint of its MCF
  window — `desired_start = max((t_min + t_max − p_j) // 2, release_j)` — via
  `insert_jobs_at_desired_starts` (`algorithm/mcf_lb/utils.py`). Preserves where the LB
  "wants" each job.
- **`simple`** (the original staged `_build_anchor_schedule`): sort jobs by
  `(window t_max, original index)` and greedily left-pack them with
  `FFcSchedule.dispatch_stage_by_jobs(stage_id, seq, p_map, job_2_release=...)`.

**Empirically neither dominates** — on some instances `simple` yields a lower final wET,
on others `midpoint` does. So: build the full feasible schedule **both ways** and keep the
lower-wET one. This applies to **both** the last-stage seed and every intermediate seed.

### 1.2 Where each method's seed becomes a full schedule

- **Intermediate** (`algorithm/mcf_lb/stage_sch_builder.py` ·
  `build_stage_seed_full_sch`, currently `stage_sch_builder.py:57`): builds ONE anchor
  (midpoint, `_build_anchor_schedule`), then derives two full candidates —
  `two_way` (`BN2DDispatcher.get_full_schedule_from_anchor`) and `seq_both_ways`
  (forward + reverse-IIT) — and keeps the min wET. The full schedules are already
  original-feasible.
- **Last stage** (`algorithm/mcf_lb/mcf_lb_pipeline.py` ·
  `calc_mcf_lb_r1_and_derive_full_sch` `mcf_lb_pipeline.py:255-295`, and the r2 twin
  `calc_mcf_lb_r2_and_derive_full_sch`): `heuristic_last_stage_only_from_mcf_lb`
  (`last_stage_sch_builder.py:58`, midpoint last-stage-only seed) →
  `build_full_sch_from_last_stage_only_sch` (`full_sch_builder.py:285`, reverse-dispatch
  → full). r2 augments releases / last-stage `p` and rebuilds to original `p` via
  `rebuild_last_stage_with_original_p=(p_increment != 0)`.

### 1.3 Current state = starting point (do not regress)

The `midpoint` method is live in both paths (intermediate `_build_anchor_schedule`;
last-stage `heuristic_last_stage_only_from_mcf_lb`). This plan **adds** the `simple`
candidate and the "pick lower wET" choice; the midpoint candidate stays exactly as is, so
the chosen schedule is always ≤ today's wET.

### 1.4 Decisions baked in (see §6 for rationale)

- **D1** `simple` seed always uses **original** processing times and releases (no
  `p_increment`). Its `build_full` uses `rebuild_last_stage_with_original_p=False`. So both
  candidates are original-feasible; only the `midpoint` candidate carries r2 augmentation.
  The `simple` seed's job **ordering** still comes from that round's MCF window (`t_max`).
- **D2** Comparison is **always on** (no config flag) — always build both, keep the better.
- **D3** The loser is recorded in diagnostics (its wET + which method won) but only the
  winner's schedule flows downstream / into phase artifacts.

---

## 2. SHARED CONTRACTS (pinned — every subagent conforms)

### 2.1 Simple-seed primitive (WO-1, `algorithm/mcf_lb/utils.py`)

```python
def build_simple_stage_seed(
    instance: FFcDDWParameters,
    window_map: Mapping[str, tuple[int, int] | None],
    *,
    stage_id: str,
    duration_map: Mapping[str, int],
    job_2_release: Mapping[str, int],
) -> FFcSchedule
```

- Fresh `FFcSchedule(jobs=instance.job_id_list, stages=instance.stage_id_list,
  machines_per_stage=instance.stage_2_machines_map)`.
- `sequence = sorted(instance.job_id_list, key=lambda j: (window_map[j][1] if
  window_map[j] is not None else 0, job_2_pos[j]))` (`job_2_pos` = native index).
- `new_sch.dispatch_stage_by_jobs(stage_id, sequence, duration_map,
  job_2_release=job_2_release)`; return `new_sch` (only `stage_id` populated).
- This is exactly the **original staged** `_build_anchor_schedule` body, generalized to
  take `duration_map` / `job_2_release` explicitly (so last-stage callers can pass
  release maps without re-deriving). Read per-job maps with `m[j]`. Add to `__all__`.

### 2.2 Intermediate dual-anchor (WO-2, `algorithm/mcf_lb/stage_sch_builder.py`)

`StageSeedResult` gains one field (append, keep existing fields/order):

```python
anchor_method: Literal["simple", "midpoint"]
```

`build_stage_seed_full_sch` signature is unchanged. New behavior: build **both** anchors
and keep the global min-wET full schedule:

- midpoint anchor = existing `_build_anchor_schedule(instance, mcf_pmtn, stage_id, log)`.
- simple anchor = `build_simple_stage_seed(instance, window_map, stage_id=stage_id,
  duration_map=get_job_2_p_map_for_stage(stage_id),
  job_2_release=get_job_2_p_sum_before_stage(stage_id))`, where `window_map =
  window_map_from_preemptive_schedule(mcf_pmtn, instance.job_id_list)`.
- For **each** anchor compute `two_way` (`get_full_schedule_from_anchor`) and
  `seq_both_ways`; the global argmin over all candidates sets `schedule` / `obj_value` /
  `best_candidate` (`"two_way"|"seq_both_ways"`) / `anchor_method`. Ties favour the
  existing midpoint candidate (so a tie keeps today's output).
- Factor the per-anchor candidate derivation into a private helper, e.g.
  `_candidates_from_anchor(instance, anchor_sch, stage_id, log) ->
  tuple[FFcSchedule, float, Literal["two_way","seq_both_ways"]]` (returns that anchor's
  own best). Then pick the better of the two anchors' bests.

### 2.3 Simple last-stage-only seed (WO-3, `algorithm/mcf_lb/last_stage_sch_builder.py`)

```python
def simple_last_stage_only_from_mcf_lb(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    *,
    logger: logging.Logger | None = None,
) -> HeuristicLastStageOnlyResult
```

- Builds a **last-stage-only** seed with the `simple` method (original `p`, no
  augmentation — D1): `window_map = window_map_from_preemptive_schedule(...)`;
  `last_stage_id = instance.stage_id_list[-1]`;
  `duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)`;
  `job_2_release = instance.get_job_2_p_sum_except_last_stage()`;
  `schedule = build_simple_stage_seed(instance, window_map, stage_id=last_stage_id,
  duration_map=duration_map, job_2_release=job_2_release)`.
- Score `obj_value = float(sum_e + sum_t)` via `compute_weighted_earliness_tardiness`
  (last-stage-only schedule scores fine — other stages empty, last stage is what ET uses).
- Return `HeuristicLastStageOnlyResult(schedule=…, obj_value=…, elapsed_time=…,
  status="HEURISTIC_SIMPLE", intermediate_schedules=[("lastS_only_simple_seed",
  schedule)])`.
- Keep `heuristic_last_stage_only_from_mcf_lb` (the midpoint one) **byte-identical**.

### 2.4 Last-stage dual-seed chooser (WO-4, `algorithm/mcf_lb/mcf_lb_pipeline.py`)

New module-level helper used by **both** r1 and r2:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class LastStageSeedChoice:
    heuristic: HeuristicLastStageOnlyResult     # winning seed
    build_full: BuildFullSchResult              # winning full schedule
    seed_method: Literal["simple", "midpoint"]
    alt_dispatched_obj: float | None            # loser's full wET (None if loser failed)

def _build_best_full_from_last_stage_seeds(
    instance: FFcDDWParameters,
    apply: ApplyLbByMcfResult,
    *,
    job_placement_priority: PmPrmpSortKey,
    last_stage_only_placement_criteria: Literal["contrib", "dist"],
    p_increment: int,
    r_multiplier: float,
    r_increment: int,
    rebuild_last_stage_with_original_p: bool,
    logger: logging.Logger | None,
) -> LastStageSeedChoice
```

- **midpoint branch** = today's path: `heuristic_last_stage_only_from_mcf_lb(instance,
  apply.mcf_preemptive_schedule, logger=…, job_priority=job_placement_priority,
  placement_priority=last_stage_only_placement_criteria, p_increment=p_increment,
  r_multiplier=r_multiplier, r_increment=r_increment)` →
  `build_full_sch_from_last_stage_only_sch(instance, heuristic.schedule,
  rebuild_last_stage_with_original_p=rebuild_last_stage_with_original_p, logger=…)`.
- **simple branch** (D1, original `p`): `simple_last_stage_only_from_mcf_lb(instance,
  apply.mcf_preemptive_schedule, logger=…)` →
  `build_full_sch_from_last_stage_only_sch(instance, simple_heuristic.schedule,
  rebuild_last_stage_with_original_p=False, logger=…)`.
- Winner = lower `build_full.dispatched_obj`; a `None`-schedule build loses; **ties favour
  midpoint** (keeps today's output). If midpoint's build_full produced no schedule but
  simple's did, simple wins (strict improvement on availability).
- `r1`/`r2` call this once and assign `heuristic = choice.heuristic`,
  `build_full = choice.build_full` into their existing `CalcMcfLbR1Result` /
  `CalcMcfLbR2Result` slots (so phase-schedule recording, the r1-`build_full is None`
  short-circuit, and best-of-r1/r2 selection are all **unchanged** — they just operate on
  the winning seed). r1 passes `rebuild_last_stage_with_original_p=False`; r2 passes
  `rebuild_last_stage_with_original_p=(p_increment != 0)`.
- `CalcMcfLbR1Result` / `CalcMcfLbR2Result` each gain (append, defaulted):
  `seed_method: Literal["simple", "midpoint"] | None = None` and
  `alt_dispatched_obj: float | None = None`, set from the `choice`. Propagate the r1 ones
  onto `CalcMcfLbAndDeriveFullSchResult` as `last_stage_seed_method: str | None` and
  `last_stage_alt_obj: float | None` (append, defaulted) so the controller/diagnostic can
  read them. The all-stages last-stage `StageLbRecord` then sets `seed_method` (see §2.5)
  from `last_stage_result.last_stage_seed_method`.
- **Intermediate**: in `calc_mcf_lb_all_stages_and_derive_full_sch`, set each intermediate
  `StageLbRecord.seed_method = seed.anchor_method` (from §2.2) and `best_candidate` stays
  `seed.best_candidate`.

### 2.5 Diagnostic (WO-5, `algorithm/mcf_lb/diagnostic.py`)

- `StageLbRecord` gains (append, defaulted): `seed_method: str | None = None`
  (`"simple"|"midpoint"` for built stages; `None` if unbuilt).
- `CalcMcfLbAndDeriveFullSchDiagnostic` gains (append, defaulted):
  `last_stage_seed_method: str | None = None`,
  `last_stage_alt_obj: float | None = None`.
- Defaults keep existing serialization unchanged. `asdict` still works (numpy-free per
  the existing boundary rule — these are str/float).

### 2.6 Controller (WO-6, `orchestration/controller.py`)

In `calc_mcf_lb_and_derive_full_sch`, after the existing diagnostic population, set
`c_diag.last_stage_seed_method` / `c_diag.last_stage_alt_obj` from the pipeline result
(`result.last_stage_seed_method` / `result.last_stage_alt_obj`) on **both** the
`last_stage` and `all_stages` branches. No other behavior change. Keep the single
`_register` + timing contract (CLAUDE.md). The `last_stage` registered schedule/obj/bound
is whatever the pipeline now returns (still the better-or-equal schedule), so the
incumbent never worsens.

---

## 3. Dependency graph & dispatch order

```txt
WO-1 utils.build_simple_stage_seed     (no dep)
WO-2 stage_sch_builder (intermediate)  ← WO-1
WO-3 last_stage_sch_builder (simple)   ← WO-1
WO-4 mcf_lb_pipeline (chooser + r1/r2) ← WO-3, WO-5
WO-5 diagnostic                        (no dep)
WO-6 controller                        ← WO-4, WO-5
WO-7 test dual-seed last stage         ← WO-4
WO-8 test dual-anchor intermediate     ← WO-2
```

All may be edited in parallel (§2 pins the seams). Run WO-7/WO-8 + §5 after the rest land.

---

## 4. Work Orders

### WO-1 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/utils.py`

**Goal:** add `build_simple_stage_seed` (§2.1).
**Changes:** new function = generalized original staged `_build_anchor_schedule`
(sort by `(t_max, idx)` + `dispatch_stage_by_jobs`), taking explicit `duration_map` /
`job_2_release`. Import `FFcSchedule` is already present (added for
`insert_jobs_at_desired_starts`). Add `"build_simple_stage_seed"` to `__all__`.
**Invariant:** pure; reads maps with `m[j]`.
**Accept:** `uv run ruff check`; a `uv run python -c` smoke that builds a seed on a tiny
instance and confirms only `stage_id` has operations.

### WO-2 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/stage_sch_builder.py`

**Goal:** §2.2 — build both anchors, keep global min; add `StageSeedResult.anchor_method`.
**Changes:** add `anchor_method` field; factor `_candidates_from_anchor`; in
`build_stage_seed_full_sch` build midpoint anchor (`_build_anchor_schedule`, keep) and
simple anchor (`build_simple_stage_seed`), pick global min wET (ties → midpoint). Import
`build_simple_stage_seed` from `.utils`. `_build_anchor_schedule` stays the midpoint one.
**Invariant:** if simple never beats midpoint, output == today.
**Accept:** WO-8.

### WO-3 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/last_stage_sch_builder.py`

**Goal:** §2.3 — add `simple_last_stage_only_from_mcf_lb`.
**Changes:** new function using `build_simple_stage_seed` on the last stage with original
`p` and `get_job_2_p_sum_except_last_stage()` releases; return
`HeuristicLastStageOnlyResult(status="HEURISTIC_SIMPLE", …)`. Add to `__all__`. Keep
`heuristic_last_stage_only_from_mcf_lb` byte-identical.
**Accept:** WO-7.

### WO-4 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/mcf_lb_pipeline.py`

**Goal:** §2.4 — `LastStageSeedChoice` + `_build_best_full_from_last_stage_seeds`; wire
into r1 & r2; new result fields; set intermediate `StageLbRecord.seed_method`.
**Changes:** add the helper; replace the inline `heuristic_…` + `build_full_…` blocks in
`calc_mcf_lb_r1_and_derive_full_sch` (`mcf_lb_pipeline.py:255-295`) and the r2 twin with a
single call to the helper (r1 `rebuild=False`; r2 `rebuild=(p_increment != 0)`), assigning
the winner into the existing `heuristic`/`build_full` flow so phase-schedule recording and
the `build_full is None` short-circuit are unchanged. Add `seed_method` /
`alt_dispatched_obj` to `CalcMcfLbR1Result`/`CalcMcfLbR2Result` and
`last_stage_seed_method` / `last_stage_alt_obj` to `CalcMcfLbAndDeriveFullSchResult`
(append, defaulted). In `calc_mcf_lb_all_stages_and_derive_full_sch`, set each intermediate
record's `seed_method = seed.anchor_method` and the last-stage record's `seed_method =
last_stage_result.last_stage_seed_method`.
**Invariant:** with `simple` always losing, every artifact/obj/bound equals today.
**Accept:** WO-7 + §5 regression.

### WO-5 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py`

**Goal:** §2.5 — add `StageLbRecord.seed_method` and the two
`CalcMcfLbAndDeriveFullSchDiagnostic` fields (append, defaulted).
**Accept:** `asdict(CalcMcfLbAndDeriveFullSchDiagnostic())` runs; defaults unchanged.

### WO-6 — `src/ffc_ddw_sum_et/orchestration/controller.py`

**Goal:** §2.6 — populate `c_diag.last_stage_seed_method` / `last_stage_alt_obj` on both
branches from the pipeline result. No other change; single `_register` + timing contract.
**Accept:** §5 + manual: a 3-stage `all_stages` run records a `seed_method` per stage.

### WO-7 — `tests/algorithm/mcf_lb/test_dual_seed_last_stage.py` (NEW)

**Goal:** validate §2.3/§2.4 (last-stage dual seed).
**Asserts:** on a small instance, `_build_best_full_from_last_stage_seeds` returns a
feasible full schedule whose wET ≤ min(midpoint-only, simple-only) built directly;
`seed_method` matches the lower; **regression**: when the midpoint wins, the r1
`build_full.schedule` makespan/obj equals the current single-seed pipeline. Construct one
instance where `simple` strictly wins and assert `seed_method == "simple"`.
**Accept:** `uv run pytest` green.

### WO-8 — `tests/algorithm/mcf_lb/test_dual_anchor_intermediate.py` (NEW)

**Goal:** validate §2.2 (intermediate dual anchor).
**Asserts:** `build_stage_seed_full_sch` returns wET ≤ the midpoint-only anchor's best;
`anchor_method ∈ {"simple","midpoint"}`; `best_candidate ∈ {"two_way","seq_both_ways"}`;
schedule is a feasible full schedule (`validate_schedule`). Construct one instance where
`simple` strictly wins.
**Accept:** `uv run pytest` green.

---

## 5. Global acceptance (after all WOs)

- `uv run ruff check && uv run ruff format && uv run pytest`
  (excluding the pre-existing-broken `tests/algorithm/neh_cp/test_tl_schedule.py`).
- **Regression:** force `simple` to lose (e.g. a fixture where midpoint is known better)
  and confirm the incumbent/LB/diagnostic match `main`'s midpoint-only output.
- Real-instance smoke (`metadata/20260617/20260617_mcf_lb_all_stages_009nc.yaml`,
  `ins_index: [0]`): both scenarios run; `instance_result.yaml` records `seed_method` per
  stage; chosen wET ≤ the previous run's.

## 6. Decisions & risks

- **D1** simple seed = original `p`, no augmentation, `rebuild=False`. *Why:* keeps the
  simple candidate genuinely simple and original-feasible without threading r2 augmentation
  through it; the only round-dependence is the `t_max` ordering from that round's window.
- **D2** ~~always-both, no flag (YAGNI: the ask is "pick the better"). *When to revisit:* if
  the doubled build cost threatens the `0.09nc` budget, gate behind a `seed_compare` flag.~~
  **REVERSED (2026-06-18):** the comparison is now gated behind a `seed_compare: bool`
  option (default `False`), not always-on. *Why:* always-on was rejected — the comparison
  should be opt-in per scenario so the baseline path stays the default and the doubled build
  cost (R1) is only paid when explicitly requested. *How it threads:*
  `controller.calc_mcf_lb_and_derive_full_sch(seed_compare=…)` →
  `calc_mcf_lb_and_derive_full_sch` / `calc_mcf_lb_all_stages_and_derive_full_sch` → r1/r2 →
  `_build_best_full_from_last_stage_seeds(seed_compare=…)` and
  `build_stage_seed_full_sch(seed_compare=…)`. When `False`, **only** the midpoint seed is
  built (no simple seed constructed at all), so the path is byte-identical to `main` *and*
  carries no extra build cost — strengthening the §5 "never worse than today" invariant from
  "ties favour midpoint" to "midpoint-only is the literal default". Two `_seedcmp` scenarios
  (`mcf_lb_last_stage_seedcmp`, `mcf_lb_all_stages_seedcmp`) opt in via `seed_compare: true`.
  Note: this conflicts with the original always-on intent baked into §2.2/§2.4; those
  contracts now describe the `seed_compare=True` behavior, and `seed_compare=False` short-
  circuits to the midpoint-only return before the simple branch.
- **D3** record the loser's wET + winner method in diagnostics; only the winner flows
  downstream. *Why:* enables per-instance analysis of which method wins without bloating
  artifacts.
- **R1** doubled `build_full` / dual-anchor work roughly doubles the per-stage seed cost.
  Intermediate horizons are cheap; last stage adds one extra reverse-dispatch. `stop_predicate`
  still bounds the whole composite. Measure in the smoke run.
- **R2** ties must favour midpoint everywhere so the "never worse than today" invariant is
  literal and the regression in §5 is byte-exact.
- **R3** numpy-at-YAML boundary: `seed_method` is `str`; the loser obj is already a plain
  `float` (`build_full.dispatched_obj` via `float(...)`). No new coercion needed.
