# `initialize_by_best_of_selected_dispatches` port

> **Scope is substantial (Johnson + CDS + Gupta + Palmer generators, a new
> controller step method, forward/reverse instance handling, YAML wiring).**
> Per user guidance, this plan is captured and implementation should happen in
> a dedicated follow-up chat.

## Context

Upstream `hybridflowshop/controller/hfs_cp_lns.py:5417` has a step method
`initialize_by_best_of_selected_dispatches` that:

1. Runs a configurable list of dispatching heuristics as candidate schedules.
2. Picks the one with the smallest makespan across candidates.
3. Registers it as the incumbent.

This project wants the same pattern, wired through the YAML
`metadata/20260423/cmax_init_pfns_config.yaml` (already present) whose
`subroutine_flow` is:

```yaml
- method: initialize_by_best_of_selected_dispatches
  left_cap_portion: 0.25
  right_cap_portion: 0.25
  normalize_by_stage_cnt: false
  mixed_schedule_for_former_stages: true
  mixed_schedule_for_later_stages: true
  machine_then_job: true
  all_stages_as_bottleneck: true
  error_if_infeasible: false
  draw_gantt: false
  method_list:
    - run_bn2d
    - select_best_of_mixed_dispatches
- method: run_profile_fixed_ns
  solver_thread_cnt: 2
  cp_pf_method: "PF1"
```

### Design decisions (confirmed with user)

- `select_best_of_mixed_dispatches` = best of **CDS, Gupta, Palmer** on the
  forward and reversed instance, mirroring upstream
  `_get_schedule_by_best_of_mixed_dispatches` (`hfs_cp_lns.py:5183`).
  CDS/Gupta/Palmer are **not** currently in this project — they must be ported.
- Candidate selection criterion (two-level):
  - **Mixed dispatch wrappers** (`get_schedule_by_{cds,gupta,palmer}`) always
    select their internal best (across k-cuts / np variants) **by makespan** —
    `criteria` parameter was removed from these methods.
  - **Controller-level final selection** across all candidates:
    - `iit_after_each_dispatch=True` → IIT applied to each candidate first,
      then compare by **weighted E+T**.
    - `iit_after_each_dispatch=False` → compare by **makespan** (upstream default).
  - The two levels are independent: mixed dispatch internals never use E+T,
    regardless of `iit_after_each_dispatch`.
- `AlgRecord.obj_value` / `SubroutineReport.obj_value` reports weighted E+T
  always (project convention; makespan stays in `metrics`).

## Files to create / modify

### 1. `src/ffc_ddw_sum_et/algorithm/dispatcher/base.py`

Add to `BaseDispatcher` (port from upstream `hybridflowshop/dispatcher/base.py`):

- `get_johnsons_rule_sequence(p1_map, p2_map) -> list[str]` — classical
  two-machine Johnson split. Source: upstream `base.py:121`.
- `get_cds_sequence(k) -> list[str]` — Campbell–Dudek–Smith k-cut aggregation.
  Source: upstream `base.py:163`.
- `get_gupta_sequence() -> list[str]` — Gupta's functional heuristic score.
  Source: upstream `base.py:184`.
- `get_palmer_sequence() -> list[str]` — Palmer slope index. Source: upstream
  `base.py:217`.

All four use `self.job_2_stage_2_p`, `self.stage_id_list`, `self.job_id_list`
already exposed on `BaseDispatcher` in this project.

### 2. `src/ffc_ddw_sum_et/algorithm/dispatcher/mixed.py`

Add three sequence-to-schedule wrappers mirroring upstream `mixed.py:135/204/254`:

- `get_schedule_by_cds(schedule=None, from_stage=None, machine_then_job=False, head_for_all_stages=False) -> FFcSchedule | None`
  — iterate `k` in `range(1, stage_count)`, compute CDS sequence, dispatch via
  existing `get_best_mixed_schedule_by_sequence`, keep best by **makespan** (hardcoded).
- `get_schedule_by_gupta(...)` — single call via Gupta sequence, best by makespan.
- `get_schedule_by_palmer(...)` — single call via Palmer sequence, best by makespan.

`criteria` parameter is **not** exposed on these wrappers — they always use
`"makespan"` internally, matching upstream. E+T comparison lives at the
controller layer only.

### 3. Reversed-instance pipeline

- `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py` — reuse the existing
  `FFcDDWParameters.create_instance_of_stage_subset(instance, set(instance.stage_id_list), reverse_stage_seq=True)`
  to produce the reversed instance. No API change needed.
- `src/ffc_ddw_sum_et/solution/ffc_schedule.py` — **verify** that
  `FFcSchedule.as_reversed()` and `make_semi_active(...)` exist. If missing,
  port them from upstream, **or** skip the reversed-instance pass in the first
  iteration and defer via `docs/TODO.md` with a clear "when to act". See
  Risks below.

### 4. `src/ffc_ddw_sum_et/orchestration/controller.py`

**Private helper** — `_get_schedule_by_best_of_mixed_dispatches(*, machine_then_job, head_for_all_stages) -> dict[str, FFcSchedule | None]`:

1. Build fwd + reversed `FFcDDWParameters`.
2. For each of {CDS, Gupta, Palmer}:
   - Instantiate a `MixedDispatcher` on the fwd instance; call
     `get_schedule_by_<name>(machine_then_job=..., head_for_all_stages=...)`.
   - Instantiate a `MixedDispatcher` on the reversed instance; call the same
     method; if result is non-None, convert via `as_reversed()` +
     `make_semi_active(stage_2_job_2_p)` to map back to forward indexing.
3. Return a flat map `{"mixed.cds": sch, "mixed.cds_rev": sch, "mixed.gupta": …, …}`.

`criteria` is **not** a parameter — mixed wrappers always use makespan internally.

**Public step** — `initialize_by_best_of_selected_dispatches` on
`FFcDDWSubroutineController` (follows the `run_bn2d` / `initialize_by_edd`
template):

```python
def initialize_by_best_of_selected_dispatches(
    self,
    left_cap_multiplier: int | None = None,
    right_cap_multiplier: int | None = None,
    left_cap_portion: float | None = None,
    right_cap_portion: float | None = None,
    normalize_by_stage_cnt: bool = False,
    mixed_schedule_for_former_stages: bool = False,
    mixed_schedule_for_later_stages: bool = False,
    machine_then_job: bool = False,
    all_stages_as_bottleneck: bool = False,
    error_if_infeasible: bool = False,
    draw_gantt: bool = False,
    method_list: list[str] | None = None,
    iit_after_each_dispatch: bool = False,
) -> SubroutineReport: ...
```

Behavior:

1. `method_list` defaults to `["run_bn2d", "select_best_of_mixed_dispatches"]`.
2. Build `candidates: dict[str, FFcSchedule | None]`:
   - `"run_bn2d"` → build a `BN2DOption` from the relevant kwargs and run
     `BN2DDispatcher().run(AlgSpec(...))`. Take `result.schedule`. **Do not**
     invoke `self.run_bn2d` (avoids a duplicate `solution_manager.register`).
   - `"select_best_of_mixed_dispatches"` → call the private helper above and
     merge its dict into `candidates`.
   - Unknown name → `self.logger.warning(...)` and skip.
3. If `iit_after_each_dispatch=True` → apply
   `sch.insert_idle_time(instance.job_2_due_window_map, instance.job_2_ewt_map, instance.job_2_twt_map)`
   in place on each non-None candidate.
4. Pick best:
   - `iit_after_each_dispatch=True` → by `compute_weighted_earliness_tardiness(sch, instance)`.
   - Else → by `sch.makespan`.
5. If no non-None candidate and `error_if_infeasible=True` → raise `RuntimeError`.
   Else return an empty `SubroutineReport`.
6. Compute final report values:
   - `obj_value = weighted E+T of best_sch` (always, per project convention).
   - `obj_bound = None`.
7. Build `SubroutineReport`, wrap best_sch in `FFcDDWSolution`, register via
   `self.solution_manager.register(report, solution)`.
8. `self.logger.info("best_of_selected_dispatches: best=%s obj_value=%s", best_method_name, obj_value)`.

### 5. `main.py`

Switch `CONFIG_PATH` to `Path("metadata/20260423/cmax_init_pfns_config.yaml")`.

### 6. YAML

`metadata/20260423/cmax_init_pfns_config.yaml` already exists and references the
new step name. No YAML edit required.

## Reusable helpers (do not re-implement)

- `FFcDDWParameters.create_instance_of_stage_subset(...)` — reversed instance.
- `MixedDispatcher.get_best_mixed_schedule_by_sequence(..., criteria=...)` —
  already supports `"weighted_et"` and `"makespan"`.
- `FFcSchedule.insert_idle_time(due_window_map, ewt_map, twt_map)` — IIT.
- `compute_weighted_earliness_tardiness(sch, instance)` — E+T scoring.
- `AlgSpec` + `BN2DDispatcher.run(spec)` — BN2D candidate entry point.
- `FFcDDWSolution` + `solution_manager.register(report, solution)` — incumbent
  registration pattern used by `run_bn2d` / `run_fam` / `initialize_by_edd`.

## Verification

1. **Unit tests — sequence generators.** Add pytest cases for
   `get_cds_sequence`, `get_gupta_sequence`, `get_palmer_sequence` on a small
   3-stage, 4-job fixture with hand-computed expected orders.
2. **Unit tests — mixed wrappers.** Assert that
   `MixedDispatcher.get_schedule_by_{cds,gupta,palmer}` return a non-None
   `FFcSchedule` respecting flowshop constraints on the fixture.
3. **Integration.** `uv run python main.py` (with `CONFIG_PATH` pointing at
   `cmax_init_pfns_config.yaml`) on one or two PRA instances. Confirm:
   - `initialize_by_best_of_selected_dispatches` registers an incumbent.
   - `run_profile_fixed_ns` runs afterward and does not regress the incumbent.
4. **Lint / format.** `uv run ruff check` and `uv run ruff format`.
5. **Full test run.** `uv run pytest`.

## Risks / open questions to resolve at the top of the implementation chat

- **`FFcSchedule.as_reversed()` / `make_semi_active(...)` availability.** If
  absent, the reversed-instance pass must either (a) port those methods — adds
  non-trivial scope — or (b) be skipped for the initial implementation, running
  CDS/Gupta/Palmer only on the forward instance. Prefer (b) with a `docs/TODO.md`
  entry if (a) would blow up the scope.
- **Tie-break rank.** Upstream seeds a `job_tiebreak_rank`. This project does
  not currently have one; fallback to `instance.job_id_list` position (already
  the default in `_get_rank_tiebreak_key`) is sufficient. No extra plumbing
  needed unless tests reveal non-determinism across runs.
- **Criterion under `iit_after_each_dispatch=True`.** ~~Plan uses weighted E+T
  for comparison in that case.~~ **Resolved:** mixed dispatch wrappers always
  use makespan internally; `iit_after_each_dispatch` only governs the
  controller-level final selection (IIT-then-E+T vs makespan). The two levels
  are decoupled.
