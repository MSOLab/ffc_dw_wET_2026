# MCF-LB all-stage projection — per-file work orders

**Date:** 2026-06-17 · **Branch:** `20260615_more_time_limit` · **Status:** PLAN (no source edited yet)

## 0. How to use this document

This plan is written so that **one subagent per file** can implement its slice
independently. Dispatch rule:

> "이 markdown file 참조해서, 수정해야하는 파일 하나당 하나씩의 subagent를 시켜서 수정하게 만들어."

Each subagent:
1. **Reads §1 (background), §2 (SHARED CONTRACTS), and its own Work Order (WO-n) only.**
   §2 is the single source of truth for every cross-file interface — conform to it
   exactly so independently-edited files stay compatible.
2. Edits **only** the one file named in its WO (plus, for new-file WOs, creates that file).
3. Runs `uv run ruff check <file>` / `uv run ruff format <file>` on its file.
4. Does NOT change the default (`last_stage`) behavior — it must stay byte-identical.
   The new code lives only on the `all_stages` branch.

Dependency edges are listed per WO and summarized in §3. Subagents may be dispatched
in parallel; references to not-yet-created symbols are fine because §2 pins them.
Integration tests (WO-10, WO-11) and the global build (§5) are run after all WOs land.

---

## 1. Background

`calc_mcf_lb_and_derive_full_sch` gains one option, `lb_stage_scope`. Default
`"last_stage"` = current behavior. `"all_stages"`:

1. **Last stage**: current behavior unchanged (full-ET MCF lower bound `LB^ET_c`
   + existing reverse-dispatch schedule, incl. round-2 adjust).
2. For each **intermediate stage** `i = c-1 … 1`: solve a **weighted-tardiness-only**
   MCF (`vault/bounds_wT_P3.tex`) → `LB_T^(i)`; build a stage-`i` anchor schedule, then
   two full-schedule candidates from it; keep the lower-wET one.
3. Report bound `combined_lb = max{ LB^ET_c , max_{i<c} LB_T^(i) }`; register the
   global min-wET schedule across the last stage + all intermediate candidates.
4. **No round-2 / drift-correction for intermediate stages** (decision D1). MCF solves
   total = c or c+1.
5. **No new subroutine_flow element** (decision D2) — everything happens inside the
   composite. The composite registers exactly once (controller step contract).

### Math (`vault/bounds_wT_P3.tex`)

Earliness is non-regular → over-counted by upstream projection, so it is dropped
(`Σ(w⁻E+w⁺T) ≥ Σ w⁺T`). For stage `i`:

- release `r_j^(i) = Σ_{h<i} p_{hj}`  · downstream tail `τ_j^(i) = Σ_{h>i} p_{hj}`
- projected upper-due `d̄_j^(i) = d⁺_j − τ_j^(i)`
- slot cost `c_jt = 0 (t ≤ d̄_j^(i))` ; `w⁺_j·⌈(t − d̄_j^(i)) / p_{ij}⌉ (t > d̄_j^(i))`
- time→sink capacity `|M_i|` ; job supply `p_{ij}` ; horizon `H_i = max_j r_j^(i) + ⌈Σ_j p_{ij}/|M_i|⌉`
- `LB_T^(i) ≤ OPT` for every `i`. At `i=c`, `τ=0 ⇒ d̄=d⁺` and the full-ET MCF dominates,
  so tardiness-only is used only for `i<c`.

This equals the current last-stage three-piece cost (`parallel_mc_pmtn._define_parameters`)
with the earliness arm removed and the due shifted to `d⁺_j − τ_j^(i)`.

---

## 2. SHARED CONTRACTS (pinned — every subagent conforms)

### 2.1 Option name & default
`calc_mcf_lb_and_derive_full_sch` (controller) and the new pipeline take
`lb_stage_scope: Literal["last_stage", "all_stages"] = "last_stage"`.
Added as the **final** keyword arg (after `emit_phase_schedules`) so positional callers
are unaffected.

### 2.2 `FFcParameters` helpers (WO-1)
```python
def get_job_2_p_sum_before_stage(self, stage_id: str) -> dict[str, int]
def get_job_2_p_sum_after_stage(self, stage_id: str) -> dict[str, int]
```
- `before`: Σ p over stages strictly before `stage_id` in `stage_id_list` (first stage → all 0).
- `after`: Σ p over stages strictly after `stage_id` (last stage → all 0).
- Invalid `stage_id` raises `ValueError` (mirror `get_job_2_p_map_for_stage`).
- Identity: `get_job_2_p_sum_before_stage(stage_id_list[-1]) == get_job_2_p_sum_except_last_stage()`.

### 2.3 MCF builder (WO-2)
`ParallelMachinePreemptionMcf.from_instance(instance, *, r_multiplier=1.0,
r_increment=0, stage_id: str | None = None, tardiness_only: bool = False)`
- `stage_id is None` → last stage (current path, unchanged).
- target stage `q = stage_id or last`. `p = get_job_2_p_map_for_stage(q)`,
  `r = get_job_2_p_sum_before_stage(q)` (then r_multiplier/r_increment), `mc_count = |M_q|`.
- `tardiness_only=False` → current three-piece cost + `d_lower` horizon (unchanged).
- `tardiness_only=True` → `τ = get_job_2_p_sum_after_stage(q)`, `d̄_j = d⁺_j − τ_j`;
  `C[j][t] = 0 if t ≤ d̄_j else twt[j]·ceil((t − d̄_j)/p_j)`; horizon via
  `compute_parallel_mc_horizon(p, r, mc_count, d_lower=None)`. (w⁻/earliness unused.)
- `mcf.calT` (slot list) and `mcf.opt_cost` accessors stay as today; pipeline reads
  `len(mcf.calT)` (slot_count) and `mcf.calT[-1]` (horizon).

### 2.4 LB layer (WO-3)
`solve_mcf_lb` and `apply_lb_by_mcf` gain `stage_id: str | None = None,
tardiness_only: bool = False`, forwarded to `from_instance`. The preemptive schedule
is built on the target stage:
`MCFPreemptiveSchedule.from_flow_dict(..., stage_id=q, machines=instance.stage_2_machines_map[q])`.
`obj_bound_is_valid` rule unchanged (`p_increment==0 and r_multiplier<=1 and
r_increment==0`) — `tardiness_only` does **not** invalidate it (still a valid LB on OPT).
Heatmap path: callers pass `draw_heatmap=False` for intermediate stages; no heatmap
change needed.

### 2.5 BN2D anchor seam (WO-4)
New public method on `BN2DDispatcher`:
```python
def get_full_schedule_from_anchor(
    self, instance: FFcDDWParameters, anchor_schedule: FFcSchedule,
    anchor_stage_id: str, *, option: BN2DOption | None = None,
    logger: logging.Logger | None = None,
) -> FFcSchedule
```
- Uses `anchor_schedule` as the fixed stage-`anchor_stage_id` schedule; `anchor_cmax =
  max end time on that stage`. Dispatches later stages forward and former stages on a
  reversed sub-instance (right-shift to fit) — i.e. the existing two-way logic in
  `_get_schedule_from_bottleneck_stage` (current lines 196–286), factored out.
- `_get_schedule_from_bottleneck_stage` must call the same factored helper after building
  its own bottleneck schedule, so its behavior (and `run()` / `all_stages_as_bottleneck`)
  is unchanged.

### 2.6 Stage seed builder (WO-5, new file `algorithm/mcf_lb/stage_sch_builder.py`)
```python
@dataclass(frozen=True, slots=True, kw_only=True)
class StageSeedResult:
    schedule: FFcSchedule
    obj_value: float                       # wET of `schedule`
    best_candidate: Literal["two_way", "seq_both_ways"]

def build_stage_seed_full_sch(
    instance: FFcDDWParameters,
    mcf_preemptive_schedule: MCFPreemptiveSchedule,
    stage_id: str, *, logger: logging.Logger | None = None,
) -> StageSeedResult
```
Internally: (1) anchor → (2-1) two-way → (2-2) seq-both-ways → min-wET. See WO-5.

### 2.7 Diagnostic (WO-6)
```python
@dataclass(slots=True)
class StageLbRecord:
    stage_id: str
    is_last_stage: bool
    bound_kind: str                  # "full_ET" | "tardiness_only"
    mcf_lb: float | None = None
    mcf_lb_valid: bool = False
    init_sched_obj: float | None = None
    delta: float | None = None       # init_sched_obj - mcf_lb
    best_candidate: str | None = None  # "two_way" | "seq_both_ways" | "last_stage_pipeline"
    mcf_solve_sec: float | None = None
    horizon: int | None = None
    slot_count: int | None = None
    load_index: float | None = None
    max_release: int | None = None
```
Add to `CalcMcfLbAndDeriveFullSchDiagnostic` (defaults keep last_stage output identical):
```python
lb_stage_scope_used: str = "last_stage"
per_stage_records: list[StageLbRecord] = field(default_factory=list)
combined_lb: float | None = None
argmax_stage_id: str | None = None
best_init_sched_obj: float | None = None
best_sched_source: str | None = None     # stage_id, or "last_stage_pipeline"
total_mcf_solve_sec: float | None = None
mcf_solve_count: int | None = None
```
`dataclasses.asdict` already serializes a list of dataclasses → **no runner change** needed.
(`@dataclass(slots=True)` + `field(default_factory=list)` ⇒ `from dataclasses import field`.)

### 2.8 All-stages pipeline (WO-7)
```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CalcMcfLbAllStagesResult:
    last_stage_result: CalcMcfLbAndDeriveFullSchResult  # for existing artifact/diag reuse
    best_schedule: FFcSchedule | None
    best_obj: float | None
    combined_lb: float | None
    argmax_stage_id: str | None
    best_sched_source: str | None
    stage_records: list[StageLbRecord]
    total_mcf_solve_sec: float
    mcf_solve_count: int
    elapsed_sec: float

def calc_mcf_lb_all_stages_and_derive_full_sch(
    instance, *, draw_pmtn_sch_heatmap=False, heatmap_sort="end_time",
    job_placement_priority="end_time", last_stage_only_placement_criteria="dist",
    makespan_delta_ref="mcfLbMakespan", adjust_p=False, adjust_r=False,
    p_adjust_coeff=1.0, r_adjust_coeff=0.5, proceed_r2_when_nonpositive_cmax=False,
    stop_predicate=None, logger=None, r1_heatmap_yaml_path=None, r2_heatmap_yaml_path=None,
) -> CalcMcfLbAllStagesResult
```
- `last_stage_result` = the existing `calc_mcf_lb_and_derive_full_sch(...)` with the same
  kwargs (so last-stage LB/schedule/round-2/artifacts are identical).
- `combined_lb = max(non-None valid LBs)`; intermediate LB valid since round-1 only.
- `best_*` = argmin over `{last_stage_result.best_*}` ∪ intermediate seeds.
- `stage_records`: one `StageLbRecord` per stage (last = `bound_kind="full_ET"`,
  `best_candidate="last_stage_pipeline"`; intermediate = `"tardiness_only"`).
- `stop_predicate` checked at each stage boundary; on stop, return with stages solved so
  far (last stage always attempted first).

### 2.9 Controller branch (WO-8)
`calc_mcf_lb_and_derive_full_sch` keeps current body for `last_stage`. For
`all_stages`: call `algo_calc_mcf_lb_all_stages_and_derive_full_sch(...)`, set
`last_result = all_result.last_stage_result`, run the **existing** diagnostic-population +
artifact-emission + backward-compat-state code against `last_result`, then additionally:
`c_diag.lb_stage_scope_used="all_stages"`, `c_diag.per_stage_records=all_result.stage_records`,
`c_diag.combined_lb/argmax_stage_id/best_init_sched_obj/best_sched_source/total_mcf_solve_sec/
mcf_solve_count` from `all_result`; final register uses `all_result.best_schedule/best_obj`
and `obj_bound=all_result.combined_lb` (also set `c_diag.final_obj`/`final_obj_bound`).

---

## 3. Dependency graph & dispatch order

```
WO-1 ffc_params        (no dep)
WO-2 parallel_mc_pmtn  ← WO-1
WO-3 lb_last_stage_pmtn← WO-2
WO-4 bn2d              (no dep)
WO-5 stage_sch_builder ← WO-4, §2.6
WO-6 diagnostic        (no dep)
WO-7 mcf_lb_pipeline   ← WO-3, WO-5, WO-6
WO-8 controller        ← WO-7, WO-6
WO-9 config + main.py  ← WO-8 (option name only)
WO-10 test mcf proj    ← WO-2, WO-3
WO-11 test all_stages  ← WO-7, WO-8
```
All WOs may be edited in parallel (each owns one file; §2 pins the seams). Run WO-10/WO-11
and §5 after the others land.

---

## 4. Work Orders

### WO-1 — `src/ffc_ddw_sum_et/parameters/ffc_params.py`
**Goal:** add the two helpers in §2.2.
**Changes:** Implement `get_job_2_p_sum_before_stage` / `get_job_2_p_sum_after_stage`
using `stage_id_list` index + `stage_2_job_2_p_map`, mirroring the existing
`get_job_2_p_sum_except_last_stage` style (read maps with `m[j]`, no defensive `.get`).
Validate `stage_id` like `get_job_2_p_map_for_stage`.
**Invariant:** `before(last) == get_job_2_p_sum_except_last_stage()`.
**Accept:** `uv run ruff check`; a quick `uv run python -c` sanity on a loaded instance
(before+after+p_stage == full per-job p-sum).

### WO-2 — `src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py`
**Goal:** generalize the MCF to an arbitrary stage with a tardiness-only cost (§2.3).
**Changes:** Add `stage_id`/`tardiness_only` to `from_instance` + `_define_parameters`.
Branch `p/r/mc_count/cost/horizon` per §2.3. Keep `stage_id=None, tardiness_only=False`
byte-identical to current. Use `get_job_2_p_sum_before_stage` (WO-1) for `r`;
`get_job_2_p_sum_after_stage` for `τ`. Update the class docstring cost note.
**Reference:** current `_define_parameters` (lines 116–156) is the full-ET template.
**Invariant:** default-arg construction identical to today (same arcs/costs/supplies).
**Accept:** see WO-10.

### WO-3 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/lb_last_stage_pmtn.py`
**Goal:** thread `stage_id`/`tardiness_only` through `solve_mcf_lb` + `apply_lb_by_mcf` (§2.4).
**Changes:** add the two kwargs (default last/full-ET), forward to `from_instance`, build
the preemptive schedule on the target stage. Validity flag unchanged. Don't draw heatmap
when `tardiness_only`.
**Invariant:** default call path unchanged.
**Accept:** WO-10.

### WO-4 — `src/ffc_ddw_sum_et/algorithm/dispatcher/bn2d.py`
**Goal:** expose `get_full_schedule_from_anchor` (§2.5); preserve existing behavior.
**Changes:** Factor the two-way extension (later-forward + former-reversed + right_shift,
current lines ~196–286) into a private helper taking `(base, mixed, anchor_stage_id,
anchor_schedule, anchor_cmax, option, spec_or_logger)`. `_get_schedule_from_bottleneck_stage`
calls it after building its own bottleneck schedule (behavior identical). Add the public
`get_full_schedule_from_anchor` that builds `base`/`mixed`, computes
`anchor_cmax = max(end time of anchor_schedule on anchor_stage_id)`, and delegates.
**Invariant:** `run()`, `all_stages_as_bottleneck`, and the bottleneck-heuristic path
produce identical schedules to today (the existing bn2d tests must still pass).
**Accept:** existing bn2d tests green; new public method returns a full feasible schedule
when given a stage-anchored input.

### WO-5 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/stage_sch_builder.py` (NEW)
**Goal:** §2.6 `build_stage_seed_full_sch` (+ `StageSeedResult`).
**Changes:** pure module (no controller import). Steps:
- **(1) anchor:** `window_map_from_preemptive_schedule(mcf_preemptive_schedule,
  instance.job_id_list)` (from `mcf_lb/utils.py`); sequence = jobs sorted by `(t_max,
  original index)`; build a fresh `FFcSchedule(jobs, stages, machines_per_stage)` and
  `dispatch_stage_by_jobs(stage_id, sequence, get_job_2_p_map_for_stage(stage_id),
  job_2_release=get_job_2_p_sum_before_stage(stage_id))`.
- **(2-1) two_way:** `BN2DDispatcher().get_full_schedule_from_anchor(instance, anchor_sch,
  stage_id, logger=logger)`; score wET via `compute_weighted_earliness_tardiness`.
- **(2-2) seq_both_ways:** sequence = anchor jobs sorted by stage-`stage_id` end time asc;
  forward = `MixedDispatcher(instance).get_best_mixed_schedule_by_sequence(seq,
  criteria="weighted_et")`; reversed = port of controller
  `_dispatch_by_reversed_sequence_with_iit` (controller.py lines 1422–1506) as a pure
  helper (reverse instance, dispatch `reversed(seq)` twice min-makespan, `as_reversed()`,
  `make_semi_active`, `insert_idle_time`, score wET); keep the min-wET of forward/reversed.
- Return `StageSeedResult` with the global min-wET of {two_way, seq_both_ways} and the
  winning `best_candidate`.
**Reference:** controller `_dispatch_by_sequence` (1383–1420) and
`_dispatch_by_reversed_sequence_with_iit` (1422–1506) for (2-2);
`last_stage_sch_builder.py` for util usage.
**Accept:** WO-11 (via pipeline). Standalone: returns a feasible full schedule + finite wET
for a 2–3 stage instance.

### WO-6 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py`
**Goal:** §2.7 — add `StageLbRecord`, extend `CalcMcfLbAndDeriveFullSchDiagnostic`, export.
**Changes:** add the dataclass + fields with defaults (so `last_stage` serialization is
unchanged: empty list, None summary). Add `from dataclasses import field`. Add
`"StageLbRecord"` to `__all__`.
**Invariant:** existing fields/order untouched; default instance serializes as today plus
the new defaulted keys.
**Accept:** `asdict(CalcMcfLbAndDeriveFullSchDiagnostic())` runs; nested list serializes.

### WO-7 — `src/ffc_ddw_sum_et/algorithm/mcf_lb/mcf_lb_pipeline.py`
**Goal:** §2.8 — add `CalcMcfLbAllStagesResult` + `calc_mcf_lb_all_stages_and_derive_full_sch`.
Export both in `__all__`. Do **not** modify `calc_mcf_lb_and_derive_full_sch` (reuse it).
**Changes:**
- Call existing `calc_mcf_lb_and_derive_full_sch(instance, ...same kwargs...)` →
  `last_stage_result`. Seed `combined_lb = last_stage_result.final_obj_bound`,
  `best_* = last_stage_result.best_schedule/best_obj`, `best_sched_source = "last_stage_pipeline"`,
  `argmax_stage_id = stage_id_list[-1]`. Build the last-stage `StageLbRecord`
  (`bound_kind="full_ET"`, `mcf_lb_valid=True`, fields from `last_stage_result.r1_apply`).
- For `q in reversed(stage_id_list[:-1])` (c-1 … 1): `stop_predicate` check; `apply =
  apply_lb_by_mcf(instance, stage_id=q, tardiness_only=True, stop_predicate=..., logger=...)`;
  `seed = build_stage_seed_full_sch(instance, apply.mcf_preemptive_schedule, q, logger=...)`;
  append `StageLbRecord` (mcf_lb=apply.mcf_lb, valid=True, init_sched_obj=seed.obj_value,
  delta=seed.obj_value-apply.mcf_lb, best_candidate=seed.best_candidate,
  mcf_solve_sec=apply.mcf_solve_sec, slot_count=len(apply.mcf.calT),
  horizon=apply.mcf.calT[-1], load_index=Σp_q/|M_q|, max_release=max(r before q));
  update `combined_lb`/`argmax_stage_id` (max) and `best_*` (min wET, source=q).
- Accumulate `total_mcf_solve_sec`, `mcf_solve_count` (last r1 + last r2-if-ran + intermediates).
- Order `stage_records` last→first or first→last consistently (document which; suggest
  ascending stage order for readability).
**Invariant:** does not touch the `last_stage` function.
**Accept:** WO-11.

### WO-8 — `src/ffc_ddw_sum_et/orchestration/controller.py`
**Goal:** §2.1 + §2.9 — add `lb_stage_scope` and the `all_stages` branch in
`calc_mcf_lb_and_derive_full_sch`. Import `algo_calc_mcf_lb_all_stages_and_derive_full_sch`
(alias) alongside the existing `algo_calc_mcf_lb_and_derive_full_sch` import (line ~51).
**Changes:** add the kwarg (final position); in the body, if `last_stage` keep current code
unchanged. If `all_stages`: call the new algo fn, set `last_result = all_result.last_stage_result`,
and reuse the current population/emission/back-compat code (currently operating on `result`,
lines ~1280–1349 + ~1356–1361) against `last_result`; then set the new diagnostic fields
(§2.9) and register `all_result.best_schedule`/`best_obj` with `obj_bound=all_result.combined_lb`.
Keep the **single `_register` per call** contract and the `elapsed`/`start_elapsed`
timing pattern (controller step contract in CLAUDE.md). The pre-`build_full` stop
short-circuit must use `last_result.r1_build_full`.
**Invariant:** `last_stage` path identical to today; one register per call.
**Accept:** WO-11 + manual: a 3-stage instance with `all_stages` registers a schedule and
`obj_bound == combined_lb ≥ last-stage LB`.

### WO-9 — `metadata/20260617/20260617_mcf_lb_all_stages_009nc.yaml` (NEW) + `main.py`
**Goal:** the "MCF LB only" experiment harness.
**Changes:** new config: `benchmark_dir`/`ins_index_source`/`bks_table_csv_path` copied
from `metadata/20260615/20260615_c5_036nc.yaml`; `output_dir: output/20260617`;
`instance_worker_cnt: 48`; `draw_gantt: false`. Two scenarios, each
`timelimit: "0.09nc"`, with a **single** flow step:
```yaml
scenarios:
  - name: mcf_lb_last_stage
    output_subdir: mcf_lb_last_stage
    timelimit: "0.09nc"
    subroutine_flow:
      - method: calc_mcf_lb_and_derive_full_sch
        lb_stage_scope: last_stage
  - name: mcf_lb_all_stages
    output_subdir: mcf_lb_all_stages
    timelimit: "0.09nc"
    subroutine_flow:
      - method: calc_mcf_lb_and_derive_full_sch
        lb_stage_scope: all_stages
        adjust_p: true
        adjust_r: true
        proceed_r2_when_nonpositive_cmax: true
```
Point `main.py` `CONFIG_PATH` to the new file (or run with `--config`).
**Accept:** `uv run python main.py --config metadata/20260617/20260617_mcf_lb_all_stages_009nc.yaml`
with a small `ins_index` smoke list runs both scenarios without error.

### WO-10 — `tests/.../test_stage_projection_mcf.py` (NEW)
**Goal:** validate the intermediate-stage MCF (WO-2/WO-3).
**Changes/asserts:** on a tiny hand-checkable instance — verify `r^(i)`, `d̄^(i)`, slot
cost, and `LB_T^(i)`; `LB_T^(i) ≤ brute-force OPT` for all `i`; `LB_T^(c) ≤ LB^ET_c`;
construct one upstream-bottleneck instance where `max_{i<c} LB_T^(i) > LB^ET_c`.
**Accept:** `uv run pytest` green.

### WO-11 — `tests/.../test_calc_mcf_lb_all_stages.py` (NEW)
**Goal:** validate the pipeline + controller branch (WO-7/WO-8).
**Changes/asserts:** `all_stages` → `combined_lb ≥ last-stage LB`;
`best_init_sched_obj ≤ last-stage-only seed obj`; `per_stage_records` populated (len == c);
**regression**: `last_stage` result equals the current `calc_mcf_lb_and_derive_full_sch`
(LB, schedule makespan, obj).
**Accept:** `uv run pytest` green.

---

## 5. Global acceptance (after all WOs)
- `uv run ruff check && uv run ruff format && uv run pytest`.
- Regression: a representative instance under `lb_stage_scope="last_stage"` yields the
  same incumbent/LB/diagnostic as `main` (default path untouched).
- Smoke run of WO-9 config on a few `ins_index`, then the full `0.09nc` run.

## 6. Decisions & risks
- **D1** intermediate stages: round-1 only (no drift correction). **D2** all inside the
  composite, no new flow step. **D3** last stage = existing pipeline. **D4** option
  `lb_stage_scope`, default `last_stage`.
- **R1** runtime of c MCFs + per-stage (2-1)/(2-2) within `0.09nc`; intermediate horizons
  are cheaper (smaller release sums); `stop_predicate` bounds it, last stage solved first.
- **R2** (resolved) `asdict` serializes the dataclass list — no runner change.
- **R3** BN2D fills stages by makespan internally (its native objective); candidates are
  compared by wET. Left as-is.
