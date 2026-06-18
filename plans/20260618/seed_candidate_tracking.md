# MCF-LB seed candidate objective tracking

**Date:** 2026-06-18 · **Status:** PLAN (rev. 2 — 3-way non-last split, last-stage r1/r2,
summary CSV) · **Branch:** `20260617_more_lb`

## 0. How to dispatch

One subagent per file; each reads §1 (background), §2 (shared contracts), and its own WO only.
§2 is the single source of truth for every cross-file interface — conform to it exactly so
independently-edited files stay compatible (contract-first). Each subagent edits only the one
file named in its WO (creating it for new-file WOs), runs `uv run ruff check <file>` and
`uv run ruff format <file>`, and keeps the **default / unchanged path byte-identical** — every
behavioral change is additive (new dict field, more candidate labels) and must not move the
registered schedule/obj/bound for any existing run.

---

## 1. Background

### 1.1 Problem

In `all_stages` mode each **non-last** stage builds up to **6** full-schedule seed candidates:

- anchor `∈ {midpoint, simple}` (simple only when `seed_compare=True`)
- candidate `∈ {bn2d, mixed_fw, mixed_rv}` per anchor

and the **last** stage builds up to **4**: round `∈ {r1, r2}` × seed `∈ {midpoint, simple}`.

Today only the per-anchor / per-round **winner** is kept (`StageLbRecord.init_sched_obj`,
`r1_full_sch_obj`, `r2_full_sch_obj`); every losing candidate's objective is discarded. There
is no data to analyse **which seed path wins under which instance-generation parameter**
(n, c, m, T, R, W), nor to measure **how much the new all-stages / multi-seed pipeline improves
over the historical last-stage-midpoint-only method**, per generation parameter.

> Naming: this plan uses **"non-last stage"** (not "intermediate stage") for stages `q < c`.
> The shipped controller/pipeline option `intermediate_stage_cost` is a different, already-
> released identifier and is **not** renamed here.

### 1.2 Goal

1. Record **every** candidate's objective in `StageLbRecord.candidate_objs` (a dict), keyed by
   its construction path, for both non-last and last stages.
2. Split the non-last candidate taxonomy into **3** types — `bn2d` / `mixed_fw` / `mixed_rv`
   (today `mixed_fw` and `mixed_rv` are merged into one `seq_both_ways` min).
3. Record the last stage at **r1 and r2** granularity (today only r1's split is propagated).
4. Reuse the existing `mcf_lb_diagnostic.yaml` `asdict` → `dump_yaml` pipeline (no new artifact
   file, no `reporting.py` change).
5. Add a post-processing script producing an analysis CSV with **both** the raw candidate
   breakdown **and** the summary objectives (registered `final_obj`, source, LB) needed to
   compute the **before-vs-now improvement** per generation parameter, joined to a last-stage
   baseline scenario.

### 1.3 Design Decisions

- **D1 (no extra compute).** `bn2d`, `mixed_fw`, `mixed_rv` objectives are *already* computed
  today (the forward and reverse arms are computed inside `_seq_both_ways` and then merged by
  `min`). Exposing all three only changes what is *returned/recorded*, not what is computed.
- **D2 (`seed_compare=False`).** `simple_*` keys are omitted from the dict entirely (no key),
  not stored as `None`.
- **D3 (last stage = r1 + r2, round-prefixed keys).** Last-stage `candidate_objs` records both
  rounds: `{r1_midpoint, r1_simple, r2_midpoint, r2_simple}` (r2 keys only when r2 ran; simple
  keys only when `seed_compare=True`). Requires propagating r2's seed-compare outcome through
  the pipeline result (WO-3) — today only r1's is carried.
- **D4 (analysis is a separate script).** WO-4 post-processes the diagnostic YAMLs; `reporting.py`
  is untouched.
- **D5 (winner byte-identical).** The 3-way split must not change any registered schedule. The
  per-anchor winner is `argmin` over `{bn2d, mixed_fw, mixed_rv}` with **tie precedence
  `bn2d > mixed_fw > mixed_rv`**, which reproduces today's selection exactly (today: `bn2d`
  beats `seq_both_ways` on tie; inside `seq_both_ways`, forward beats reverse on tie).
- **D6 (CSV = per (instance, scenario) + baseline join).** One row per (instance, scenario).
  Summary columns (`final_obj`, `best_sched_source`, `combined_lb`, `argmax_stage_id`,
  `r1_full_sch_obj`, `r2_full_sch_obj`) plus flattened candidate columns. The **before**
  baseline is the same instance's row from the last-stage `seed_compare=False` scenario; the
  improvement is computed in post by joining on `instance_name`.
- **D7 (terminology).** "non-last stage" in all new prose/keys; the shipped
  `intermediate_stage_cost` option name stays.

---

## 2. Shared Contracts (pinned — every subagent conforms)

### 2.1 Candidate taxonomy & key naming

**Non-last stage** — anchor × candidate, key = `{anchor_method}_{candidate_type}`:

| anchor_method | candidate_type | meaning |
|---|---|---|
| `midpoint`, `simple` | `bn2d` | `BN2DDispatcher.get_full_schedule_from_anchor` (was `two_way`) |
| | `mixed_fw` | forward `MixedDispatcher.get_best_mixed_schedule_by_sequence` (was the forward arm of `seq_both_ways`) |
| | `mixed_rv` | reverse-instance + IIT pipeline (was the reverse arm of `seq_both_ways`) |

- `seed_compare=False`: 3 keys — `midpoint_bn2d`, `midpoint_mixed_fw`, `midpoint_mixed_rv`.
- `seed_compare=True`: 6 keys — the above plus `simple_bn2d`, `simple_mixed_fw`, `simple_mixed_rv`.

**Last stage** — round × seed, key = `{round}_{anchor_method}`:

- `r1_midpoint`, `r1_simple`, `r2_midpoint`, `r2_simple`.
- `r1_*` always present (r1 always runs); `r2_*` only when r2 ran; `*_simple` only when
  `seed_compare=True`.

### 2.2 `_candidates_from_anchor` — `stage_sch_builder.py` (WO-1)

**Before:**

```python
def _candidates_from_anchor(...) -> tuple[FFcSchedule, float, Literal["two_way", "seq_both_ways"]]
    # (best_schedule, best_obj, best_candidate_type)
```

**After:**

```python
def _candidates_from_anchor(...) -> tuple[
    FFcSchedule,                              # best schedule
    float,                                    # best obj
    Literal["bn2d", "mixed_fw", "mixed_rv"],  # winning candidate type
    dict[str, float],                         # all three objs:
    #   {"bn2d": .., "mixed_fw": .., "mixed_rv": ..}
]
```

Selection (preserves D5 tie precedence):

```python
bn2d_sch = BN2DDispatcher().get_full_schedule_from_anchor(instance, anchor_sch, stage_id, logger=log)
bn2d_obj = _weighted_et(bn2d_sch, instance)
fw_sch, fw_obj, rv_sch, rv_obj = _seq_both_ways(instance, anchor_sch, stage_id, log)
objs = {"bn2d": bn2d_obj, "mixed_fw": fw_obj, "mixed_rv": rv_obj}
# Order matters: min() returns the first minimiser, so listing bn2d, then
# mixed_fw, then mixed_rv reproduces today's tie order exactly.
ranked = [("bn2d", bn2d_obj, bn2d_sch), ("mixed_fw", fw_obj, fw_sch), ("mixed_rv", rv_obj, rv_sch)]
best_type, best_obj, best_sch = min(ranked, key=lambda c: c[1])
return best_sch, best_obj, best_type, objs
```

### 2.2b `_seq_both_ways` — `stage_sch_builder.py` (WO-1)

**Before:** `-> tuple[FFcSchedule, float]` (returns the `min` of forward/reverse).

**After:** return both arms separately (no `min`):

```python
def _seq_both_ways(...) -> tuple[FFcSchedule, float, FFcSchedule, float]:
    # (mixed_fw_sch, mixed_fw_obj, mixed_rv_sch, mixed_rv_obj)
```

Forward = `MixedDispatcher.get_best_mixed_schedule_by_sequence(sequence, criteria="weighted_et")`;
reverse = `_dispatch_by_reversed_sequence_with_iit(...)`. The `min` selection moves up into
`_candidates_from_anchor` (§2.2). `_dispatch_by_reversed_sequence_with_iit` is **unchanged**.

### 2.3 `StageSeedResult` — `stage_sch_builder.py` (WO-1)

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class StageSeedResult:
    schedule: FFcSchedule
    obj_value: float
    best_candidate: Literal["bn2d", "mixed_fw", "mixed_rv"]   # CHANGED literal
    anchor_method: Literal["simple", "midpoint"]
    # NEW (required kwarg; kw_only ⇒ no field-ordering constraint):
    candidate_objs: dict[str, float]
    # Keys per §2.1 non-last convention:
    #   "midpoint_bn2d", "midpoint_mixed_fw", "midpoint_mixed_rv" (always)
    #   "simple_bn2d", "simple_mixed_fw", "simple_mixed_rv"       (only seed_compare=True)
```

`candidate_objs` is **required** (no default), so **all 3** `StageSeedResult(...)` construction
sites in `build_stage_seed_full_sch` (the `seed_compare=False` early return, the simple-wins
branch, and the midpoint-wins branch) must pass it. Build the dict by prefixing each anchor's
`_candidates_from_anchor` objs dict with the anchor name.

### 2.4 `StageLbRecord` — `diagnostic.py` (WO-2)

```python
@dataclass(slots=True)
class StageLbRecord:
    ...
    best_candidate: str | None = None  # "bn2d" | "mixed_fw" | "mixed_rv" | "last_stage_pipeline"
    ...
    seed_method: str | None = None
    # NEW (defaulted; append after seed_method — no reordering):
    candidate_objs: dict[str, float] | None = None
    # Non-last stage keys: "{anchor}_{bn2d|mixed_fw|mixed_rv}" (3 or 6 keys, §2.1).
    # Last stage keys:     "{r1|r2}_{midpoint|simple}" (1..4 keys, §2.1).
```

Update the `best_candidate` inline comment to the new literals. No other field changes; the
existing `last_stage` serialization gains exactly one additive defaulted key.

### 2.5 Pipeline result fields for last-stage r2 — `mcf_lb_pipeline.py` (WO-3)

`CalcMcfLbAndDeriveFullSchResult` gains two defaulted fields next to the existing
`last_stage_seed_method` / `last_stage_alt_obj` (which carry **r1**'s outcome):

```python
last_stage_r2_seed_method: str | None = None   # r2 winner: "midpoint" | "simple" | None
last_stage_r2_alt_obj: float | None = None      # r2 loser's full wET; None when no r2/no compare
```

In `_assemble`, set them from r2 when present:

```python
last_stage_r2_seed_method=r2.seed_method if r2 is not None else None,
last_stage_r2_alt_obj=r2.alt_dispatched_obj if r2 is not None else None,
```

(`r2.seed_method` / `r2.alt_dispatched_obj` already exist on `CalcMcfLbR2Result`.)

### 2.6 Analysis CSV schema — `analyze_seed_candidates.py` (WO-4)

One row per **(instance, scenario)**. Columns (NaN where a field/stage is absent):

```
# keys
instance_name, scenario, n, c, m, T_factor, R_factor, W_factor, rep,
# registered-result summary (from calc_mcf_lb_and_derive_full_sch_diagnostic)
final_obj, best_sched_source, combined_lb, argmax_stage_id,
r1_full_sch_obj, r2_full_sch_obj,
# last-stage candidate split (from the last-stage StageLbRecord.candidate_objs)
last_stage_r1_midpoint, last_stage_r1_simple, last_stage_r2_midpoint, last_stage_r2_simple,
# non-last candidate flatten, per non-last stage q (from each StageLbRecord.candidate_objs)
stage_<q>_midpoint_bn2d, stage_<q>_midpoint_mixed_fw, stage_<q>_midpoint_mixed_rv,
stage_<q>_simple_bn2d,   stage_<q>_simple_mixed_fw,   stage_<q>_simple_mixed_rv, ...
```

**Before-vs-now (computed in post, not a stored controller field):** the *before* baseline is
the same `instance_name`'s row from the **last-stage, `seed_compare=False`** scenario
(`before_obj = that row's final_obj`). After loading all rows, join each non-baseline row to its
baseline by `instance_name` and emit `before_obj`, `improvement = before_obj - final_obj`, and
`improvement_pct = improvement / before_obj`. The baseline scenario name is a script argument
(default: the scenario whose config has `lb_stage_scope="last_stage"` and no `seed_compare`).

---

## 3. Work Orders

### WO-1: `src/ffc_ddw_sum_et/algorithm/mcf_lb/stage_sch_builder.py`

**Depends on:** WO-2 (for `StageLbRecord.candidate_objs` typing reference only — no import needed).

**Changes:**

1. `_seq_both_ways` → return `(fw_sch, fw_obj, rv_sch, rv_obj)` (§2.2b); drop the internal `min`.
2. `_candidates_from_anchor` → 4-tuple with the 3-way `objs` dict and `bn2d|mixed_fw|mixed_rv`
   winner via the ranked-`min` (§2.2, D5).
3. `build_stage_seed_full_sch` → collect each anchor's `objs` dict, prefix keys with the anchor
   name, and pass `candidate_objs` at **all 3** `StageSeedResult(...)` sites (§2.3). With
   `seed_compare=False` only the midpoint dict (3 keys) is built; with `True`, both (6 keys).
4. `StageSeedResult.best_candidate` literal → `Literal["bn2d", "mixed_fw", "mixed_rv"]`;
   add the `candidate_objs` field (§2.3).
5. Update module + function docstrings: rename the candidate trio to `bn2d` / `mixed_fw` /
   `mixed_rv`, and the `_candidates_from_anchor` tie note to the precedence in D5.

**Reference:** current `_candidates_from_anchor` (`stage_sch_builder.py:143-165`), `_seq_both_ways`
(`:206-240`), `StageSeedResult` (`:56-67`), the 3 `StageSeedResult(...)` sites (`:106, :129, :135`).

**Invariant:** for every input, the returned best `schedule` and `obj_value` are byte-identical
to today (D5). Only the `best_candidate` label gains precision (`mixed_fw`/`mixed_rv` instead of
`seq_both_ways`) and `candidate_objs` is new.

**Acceptance:**

- `seed_compare=False`: `candidate_objs` has exactly the 3 `midpoint_*` keys.
- `seed_compare=True`: exactly the 6 keys.
- `min(candidate_objs.values()) == obj_value`; `best_candidate` ∈ the new trio and the
  `{anchor}_{best_candidate}` entry equals `obj_value`.
- A hand-built instance where `bn2d` ties `mixed_fw` → winner is `bn2d` (precedence).

---

### WO-2: `src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py`

**Depends on:** none.

**Changes:**

1. `StageLbRecord`: add `candidate_objs: dict[str, float] | None = None` after `seed_method`
   (§2.4).
2. Update the `best_candidate` inline comment to `"bn2d" | "mixed_fw" | "mixed_rv" |
   "last_stage_pipeline"`.

**Invariant:** no field reordering; existing serialization gains one additive defaulted key.

**Acceptance:** `asdict(StageLbRecord(stage_id="s", is_last_stage=False, bound_kind="full_et_approx"))`
includes `candidate_objs` (value `None`) and is YAML-serializable.

---

### WO-3: `src/ffc_ddw_sum_et/algorithm/mcf_lb/mcf_lb_pipeline.py`

**Depends on:** WO-1 (`StageSeedResult.candidate_objs`), WO-2 (`StageLbRecord.candidate_objs`).

**Changes:**

1. Add the two r2 fields to `CalcMcfLbAndDeriveFullSchResult` and set them in `_assemble`
   (§2.5).
2. **Non-last** `StageLbRecord` (intermediate loop, ~`mcf_lb_pipeline.py:1086-1103`): pass
   `candidate_objs=seed.candidate_objs`.
3. **Last-stage** `StageLbRecord` (~`:1019-1040`): build round-prefixed keys from the r1/r2
   seed-compare outcomes. Use the winner method to key the winner obj and the *other* key for
   the alt (do **not** hard-code "midpoint"):

   ```python
   def _round_keys(prefix, method, winner_obj, alt_obj):
       out: dict[str, float] = {}
       if method is not None and winner_obj is not None:
           out[f"{prefix}_{method}"] = winner_obj
           if alt_obj is not None:
               loser = "simple" if method == "midpoint" else "midpoint"
               out[f"{prefix}_{loser}"] = alt_obj
       return out

   r1_bf = last_stage_result.r1_build_full
   r2_bf = last_stage_result.r2_build_full
   last_keys = {
       **_round_keys("r1", last_stage_result.last_stage_seed_method,
                     r1_bf.dispatched_obj if r1_bf is not None else None,
                     last_stage_result.last_stage_alt_obj),
       **_round_keys("r2", last_stage_result.last_stage_r2_seed_method,
                     r2_bf.dispatched_obj if r2_bf is not None else None,
                     last_stage_result.last_stage_r2_alt_obj),
   }
   last_stage_candidate_objs = last_keys or None
   ```

   Pass `candidate_objs=last_stage_candidate_objs` on the last-stage `StageLbRecord`.
   All fields come from `last_stage_result` (a `CalcMcfLbAndDeriveFullSchResult`); there is no
   `CalcMcfLbR1Result` named `r1` in this scope.
4. Update the `CalcMcfLbAllStagesResult` / `CalcMcfLbAndDeriveFullSchResult` docstrings to note
   the new fields and that last-stage `candidate_objs` is r1+r2 round-prefixed, r1-and-r2 basis.

**Invariant:** `seed_compare="tardiness_only"`/`False` default path produces the same registered
schedule/obj/bound and the same `combined_lb` / `best_*` / accumulators as today; only
`candidate_objs` and the two new result fields are added.

**Acceptance:**

- `seed_compare=False`, r2 ran: last-stage `candidate_objs == {"r1_midpoint": .., "r2_midpoint": ..}`.
- `seed_compare=False`, r2 skipped: `{"r1_midpoint": ..}`.
- `seed_compare=True`, **simple wins r1**: `candidate_objs["r1_simple"] == r1.build_full.dispatched_obj`
  and `candidate_objs["r1_midpoint"] == last_stage_alt_obj` (no key swap).
- non-last `StageLbRecord.candidate_objs` equals the producing `StageSeedResult.candidate_objs`.

---

### WO-4: `scripts/analyze_seed_candidates.py` (new file)

**Depends on:** WO-1, WO-2, WO-3 deployed + an experiment run produced.

**Functionality:**

1. Arg: run output directory; optional `--baseline-scenario <name>` (default per §2.6).
2. Glob all `mcf_lb_diagnostic.yaml` under the run dir; for each, read
   `calc_mcf_lb_and_derive_full_sch_diagnostic` (load via `routix.io.load_yaml`).
3. Emit one row per (instance, scenario): summary columns from the diagnostic top level;
   `last_stage_*` from the last-stage record's `candidate_objs`; `stage_<q>_*` from each
   non-last record's `candidate_objs` (q = `StageLbRecord.stage_id`).
4. Generation params via `FFcDDWParameters._parse_instance_name(instance_name)` →
   `InstanceParams(n, c, m, T_factor, R_factor, W_factor, rep)`; emit `None`/NaN when it returns
   `None`. (Do not hand-roll a regex — reuse the parser; DRY.)
5. Scenario = output subdir name (the per-scenario `output_subdir`).
6. After collecting rows, join each non-baseline row to its baseline row (same `instance_name`,
   scenario == baseline) and add `before_obj`, `improvement`, `improvement_pct` (§2.6).
7. Write `analysis/seed_candidate_analysis_<date>.csv` (pandas).

**Acceptance:**

- Runs as `uv run python scripts/analyze_seed_candidates.py <run_output_dir>` and writes the CSV.
- Each row's `instance_name` maps to an instance_result; absent candidates are NaN.
- `improvement == before_obj - final_obj` for rows that have a baseline; NaN otherwise.

---

### WO-5: `tests/algorithm/mcf_lb/test_dual_anchor_intermediate.py`

**Depends on:** WO-1.

**Changes (the 3-way split breaks current asserts/unpacks):**

1. `_candidates_from_anchor` unpack at `:121` (`_, midpoint_obj, _ = ...`) → 4-tuple
   (`_, midpoint_obj, _, _ = ...`).
2. `best_candidate in ("two_way", "seq_both_ways")` at `:160`, `:211` →
   `("bn2d", "mixed_fw", "mixed_rv")`.
3. Add: `StageSeedResult.candidate_objs` has 3 keys when `seed_compare=False`, 6 when `True`;
   `min(candidate_objs.values()) == obj_value`.

**Acceptance:** `uv run pytest tests/algorithm/mcf_lb/test_dual_anchor_intermediate.py` green.

---

### WO-6: `tests/algorithm/mcf_lb/test_calc_mcf_lb_all_stages.py`

**Depends on:** WO-3.

**Changes:**

1. `best_candidate in ("two_way", "seq_both_ways")` at `:158`, `:290` →
   `("bn2d", "mixed_fw", "mixed_rv")`.
2. Add: non-last `StageLbRecord.candidate_objs` key counts match `seed_compare`; last-stage
   `candidate_objs` carries `r1_*` (and `r2_*` iff r2 ran), with the **simple-wins-r1** key
   mapping asserted (no swap); `candidate_objs[winner_key] == r1_full_sch_obj`-equivalent.
3. Add a YAML round-trip: `asdict(diag)` → `dump_yaml` → `load_yaml` preserves `candidate_objs`.

**Acceptance:** `uv run pytest tests/algorithm/mcf_lb/test_calc_mcf_lb_all_stages.py` green.

---

### WO-7: `metadata/20260618/20260618_mcf_lb_etapprox_009nc.yaml`

**Depends on:** none (config only; no Python change).

**Goal:** add the **before baseline** scenario so WO-4's improvement join (§2.6) has a
last-stage, `seed_compare`-off row per instance.

**Changes:** append a third scenario to the existing `scenarios:` list (keep the two
`all_stages` scenarios byte-identical):

```yaml
  - name: mcf_lb_last_stage_baseline
    output_subdir: mcf_lb_last_stage_baseline
    timelimit: "0.09nc"
    subroutine_flow:
      - method: calc_mcf_lb_and_derive_full_sch
        lb_stage_scope: last_stage
        adjust_p: true
        adjust_r: true
        proceed_r2_when_nonpositive_cmax: true
```

`seed_compare` is omitted (defaults off) → this reproduces the historical last-stage
midpoint-only method exactly, so its `final_obj` is the `before_obj` baseline. The
`adjust_*` / `proceed_r2_*` knobs match the `all_stages` scenarios so the only varied axis
is `lb_stage_scope` / cost mode.

**Invariant:** the two pre-existing scenarios are unchanged.

**Acceptance:** `uv run python main.py --config metadata/20260618/20260618_mcf_lb_etapprox_009nc.yaml`
on a tiny `ins_index` smoke list runs all three scenarios without error; WO-4 finds a
`mcf_lb_last_stage_baseline` row per instance and `--baseline-scenario` defaults to it.

---

## 4. Dependency Graph

```
WO-2 (diagnostic.py) ───┐
                        ├─▶ WO-3 (mcf_lb_pipeline.py) ─▶ WO-4 (analyze script)
WO-1 (stage_sch_builder)┘                    │
   │                                         └─▶ WO-6 (test_calc_mcf_lb_all_stages)
   └─▶ WO-5 (test_dual_anchor_intermediate)
```

WO-1 and WO-2 are independent (parallel). WO-3 needs both. WO-5 needs WO-1; WO-6 needs WO-3.
WO-4 runs after WO-1/2/3 land and an experiment has been run.

---

## 5. Dispatch Order

1. WO-2, WO-1 (parallel)
2. WO-3
3. WO-5, WO-6, WO-7 (parallel — WO-7 is config-only)
4. `uv run ruff check && uv run ruff format && uv run pytest`
5. Run the WO-7 config (all three scenarios), then WO-4.

---

## 6. Global acceptance (after all WOs)

- `uv run ruff check && uv run ruff format && uv run pytest` green.
- **Regression:** a representative instance under `lb_stage_scope="last_stage"` and under
  `lb_stage_scope="all_stages", intermediate_stage_cost="tardiness_only"` yields the same
  registered incumbent / LB / `best_sched_source` as `main` (the 3-way split is winner-neutral,
  D5).
- WO-4 CSV on a small run: contains both candidate columns and summary columns, and
  `improvement` is finite for instances present in the baseline scenario.
