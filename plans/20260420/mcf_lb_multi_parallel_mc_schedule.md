# MCF-LB: multi-seed last-stage parallel-machine schedule

## Context

Current `run_mcf_lb_4` pipeline (controller.py:277–) hard-commits to **one** last-stage seed: the MCF priority ordering from `get_job_priority_by_avg_time()`. Phase 1 even bakes the CP-SAT model for that single seed into `Phase1State`, and Phase 2 mutates that model with `profile_fix_by_machine=True, machine_precedence_stride=1` (hardcoded at [phase2_last_stage.py:57-64](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase2_last_stage.py#L57-L64)). Two richer MCF-derived priority maps already exist but are unused — commented at [phase1_mcf.py:74-75](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase1_mcf.py#L74-L75):
- `get_job_2_start_time_map()`
- `get_job_2_completion_time_map()`

We want to:
1. Try all three MCF-derived seeds, build an independent last-stage CP-SAT model per seed, solve each, and pass the **best feasible** result (min `obj`) to Phase 3. Drop any seeds whose solve is infeasible/unknown; proceed if ≥1 feasible.
2. Let Phase 2's profile-fix parameters (`profile_fix_by_machine`, `machine_precedence_stride`) be driven by the same controller arguments currently consumed only by Phase 4 — stop hardcoding `True`/`1`.
3. Remove `last_stage_only_timelimit` entirely (option, controller, Phase 2, config YAML, docs). Each per-seed CP-SAT solve runs unbounded — same semantics as the current `None` default, just no knob.

## Critical files

- [src/ffc_ddw_sum_et/algorithm/mcf_lb/phase1_mcf.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase1_mcf.py) — shrink
- [src/ffc_ddw_sum_et/algorithm/mcf_lb/phase2_last_stage.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase2_last_stage.py) — rewrite (per-seed + multi)
- [src/ffc_ddw_sum_et/algorithm/mcf_lb/phase3_dispatch.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase3_dispatch.py) — signature touch only (consume `Phase2State` shape)
- [src/ffc_ddw_sum_et/algorithm/mcf_lb/phase4_profile_fix.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase4_profile_fix.py) — signature touch only
- [src/ffc_ddw_sum_et/algorithm/mcf_lb/option.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/option.py) — drop `last_stage_only_timelimit`
- [src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py) — extend for per-seed
- [src/ffc_ddw_sum_et/orchestration/controller.py](src/ffc_ddw_sum_et/orchestration/controller.py) — `run_mcf_lb_4` rewire + `mcf_lb_phase_schedules` emit
- [metadata/20260420/1_mcf_lb_init_3_config.yaml](metadata/20260420/1_mcf_lb_init_3_config.yaml) — drop `last_stage_only_timelimit` key
- [docs/algorithms/run_mcf_lb.md](docs/algorithms/run_mcf_lb.md), [docs/algorithms/run_mcf_lb_ko.md](docs/algorithms/run_mcf_lb_ko.md) — update text

## Design

### New data types (in `phase2_last_stage.py`)

```python
SeedTag = Literal["avg_time", "start_time", "completion_time"]

@dataclass(frozen=True, slots=True, kw_only=True)
class LastStageSeed:
    tag: SeedTag
    job_sequence: list[str]
    init_schedule: FFcSchedule

@dataclass(frozen=True, slots=True, kw_only=True)
class LastStageCandidate:
    tag: SeedTag
    last_stage_only_schedule: FFcSchedule
    last_stage_only_schedule_makespan: int
    last_stage_only_obj: float
    ls_j_i_2_end: dict[tuple[str, str], int]

@dataclass(frozen=True, slots=True, kw_only=True)
class Phase2State:
    chosen: LastStageCandidate          # best-obj feasible candidate
    candidates: list[LastStageCandidate]  # all feasible (for diagnostics/export)
    # Backward-compatible shortcuts (Phase 3 reads these today):
    last_stage_only_schedule: FFcSchedule
    last_stage_only_schedule_makespan: int
    last_stage_only_obj: float
    ls_j_i_2_end: dict[tuple[str, str], int]
```

Phase 3 today reads `phase2.last_stage_only_schedule`, `.ls_j_i_2_end`, `.last_stage_only_obj`, `.last_stage_only_schedule_makespan` — mirror those on the new `Phase2State` so Phase 3 needs no logic changes.

### Phase 1 — pure MCF + seed list

Shrink `Phase1State` to only MCF/instance-wide fields:
- `mcf_lb`, `last_stage_id`, `job_2_pos`, `job_2_release_map`, `mcf_preemptive_schedule`
- **Drop** `horizon`, `ls_mdl`, `ls_params`, `ls_ops_vars`, `last_stage_only_init_schedule` (these become per-seed).
- **Add** `last_stage_seeds: list[LastStageSeed]` built from the three priority maps: `get_job_priority_by_avg_time`, `get_job_2_start_time_map`, `get_job_2_completion_time_map` (all at [parallel_mc_pmtn.py:196-247](src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py#L196-L247), all `dict[str, <T>|None]`, same None-sort-last convention already in [phase1_mcf.py:84-93](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase1_mcf.py#L84-L93)).

Seed construction reuses the existing sort key & `FFcSchedule.dispatch_stage_by_jobs(force_job_id_seq_as_priority=True, job_2_release=...)` exactly as in phase1_mcf.py:98-109 — just factored into a helper `_build_seed(tag, priority_map, instance, last_stage_id, job_2_release, job_2_pos)`.

### Phase 2 — per-seed solve + selector

Two functions:

```python
def solve_last_stage_for_seed(
    seed: LastStageSeed,
    phase1: Phase1State,
    instance: FFcDDWParameters,
    *,
    profile_fix_by_machine: bool,
    machine_precedence_stride: int,
    solver_thread_cnt: int,
) -> tuple[LastStageCandidate | None, float, str]:
    # returns (candidate_or_None, solve_sec, ls_status_name)
    # - builds its own horizon = seed.init_schedule.makespan * 2
    # - builds its own CpModel via BaseModelBuilder().build(last_stage_only=True, ...)
    # - applies add_stage_ops_precedence_constraints_after_dispatch_from_schedule
    #   with the incoming profile_fix_by_machine / machine_precedence_stride
    # - applies start/end hints from seed.init_schedule
    # - NO max_time_in_seconds (timelimit removed)
    # - INFEASIBLE -> raise (current behavior, phase2:94)
    # - non-(OPTIMAL|FEASIBLE) -> return (None, elapsed, status)

def run_phase2(
    phase1: Phase1State,
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    *,
    profile_fix_by_machine: bool,
    machine_precedence_stride: int,
    solver_thread_cnt: int = 1,
    logger: logging.Logger | None = None,
) -> Phase2State | None:
    # loops over phase1.last_stage_seeds, collects feasible LastStageCandidates,
    # sums solve_sec into diagnostic.last_stage_cp_sat_sec,
    # stores per-seed status in diagnostic.ls_status_per_seed,
    # if no feasible -> warn + return None,
    # else pick min-obj candidate, advance reached_phase="last_stage".
```

No shared model skeleton across seeds — horizon and all profile-fix constraints are seed-specific; rebuilding is simpler than resetting (KISS/YAGNI).

### Phase 3 / Phase 4 — minor

Phase 3 keeps consuming `phase2.last_stage_only_schedule` etc. via the backward-compatible shortcut fields → zero change. Phase 4 signature unchanged. Only the controller wiring changes.

### Diagnostic — per-seed records

Add to [diagnostic.py](src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py):

```python
ls_status_per_seed: dict[str, str] = field(default_factory=dict)
last_stage_obj_per_seed: dict[str, float] = field(default_factory=dict)
chosen_seed_tag: str | None = None
```

Keep the existing aggregate fields (`last_stage_only_obj`, `last_stage_only_bound`, `ls_status`, `last_stage_cp_sat_sec`) populated from the **chosen** candidate + the **sum** of per-seed solve times + the chosen seed's status — this preserves CSV columns emitted by existing diagnostic exporters ([reporting.py:511](src/ffc_ddw_sum_et/orchestration/reporting.py#L511) side).

### Controller — `run_mcf_lb_4` rewire

[controller.py:277-401](src/ffc_ddw_sum_et/orchestration/controller.py#L277-L401):
- Drop the `last_stage_only_timelimit` parameter.
- Pass `profile_fix_by_machine` + `machine_precedence_stride` into **both** `run_phase2` and `run_phase4` (currently only Phase 4).
- `mcf_lb_phase_schedules` numeric-prefix convention is consumed by [ffcddw_single_instance_runner.py:193-217](src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py#L193-L217) for per-phase YAML export. Emit all three seed init_schedules and all feasible candidate schedules:
  - `"1_mcf_preemptive_schedule"` — as today
  - `"2_last_stage_only_init_schedule__avg_time"`, `"2_last_stage_only_init_schedule__start_time"`, `"2_last_stage_only_init_schedule__completion_time"` — one per seed
  - `"3_last_stage_only_schedule__<tag>"` for each feasible candidate
  - `"3_last_stage_only_schedule_chosen"` — alias of the chosen one (keeps a stable "step 3" entry for downstream tooling that may key off the `3_` prefix)
  - `"4_last_stage_only_schedule_flipped"` / `"5_dispatched_schedule_before_unflipping"` / `"6_dispatched_schedule"` / `"7_final_schedule"` — as today

Double-underscore separator between phase label and seed tag avoids colliding with the single-underscore tokens already in filenames.

### Removals — `last_stage_only_timelimit`

- [option.py:16-26](src/ffc_ddw_sum_et/algorithm/mcf_lb/option.py#L16-L26) — remove field + docstring bullet.
- [phase2_last_stage.py:42,78-80,132-148](src/ffc_ddw_sum_et/algorithm/mcf_lb/phase2_last_stage.py) — remove parameter, `_parse_nc_timelimit`, and its call.
- [controller.py:83,100,129,279,311](src/ffc_ddw_sum_et/orchestration/controller.py) — remove from both `run_mcf_lb` (legacy) and `run_mcf_lb_4` signatures and call sites. `run_mcf_lb` is already marked `TODO: remove`; scope of this refactor is to drop only the parameter there, not delete the method.
- [metadata/20260420/1_mcf_lb_init_3_config.yaml](metadata/20260420/1_mcf_lb_init_3_config.yaml) — drop `last_stage_only_timelimit` key if present.
- [docs/algorithms/run_mcf_lb.md](docs/algorithms/run_mcf_lb.md), [docs/algorithms/run_mcf_lb_ko.md](docs/algorithms/run_mcf_lb_ko.md) — prune the timelimit mention; add a note about the three-seed last-stage solve.

## Reused utilities

- `ParallelMachinePreemptionMcf.{get_job_priority_by_avg_time,get_job_2_start_time_map,get_job_2_completion_time_map}` — three seed sources.
- `FFcSchedule.dispatch_stage_by_jobs(force_job_id_seq_as_priority=True, job_2_release=...)` — seed init schedule builder.
- `BaseModelBuilder.build(last_stage_only=True, ...)` — per-seed model.
- `BaseModelBuilder.add_stage_ops_precedence_constraints_after_dispatch_from_schedule` — now driven by controller args.
- `BaseModelBuilder.apply_start_hints_from_start_time_map` / `apply_end_hints_from_end_time_map` — reused unchanged.
- `build_schedule_from_op_starts` — reused for each candidate.

## Verification

End-to-end:
1. `uv run ruff check && uv run ruff format`
2. `uv run pytest tests/orchestration/test_controller.py::test_run_mcf_lb_registers_dispatch_incumbent tests/orchestration/test_controller.py::test_run_mcf_lb_not_greater_than_fam -q` (these pin `run_mcf_lb` legacy behavior; they should still pass — we only dropped `last_stage_only_timelimit`, which already defaulted to `None`).
3. Full suite: `uv run pytest -q`.
4. Experiment smoke: `uv run python -m ffc_ddw_sum_et.main metadata/20260420/1_mcf_lb_init_3_config.yaml` (or the equivalent entrypoint used for that config). Check:
   - Log reports three seeds and their `ls_status` outcomes.
   - Final `SubroutineReport.obj_value` ≤ the `avg_time`-only baseline on at least one non-trivial instance (strict-≤ by construction: baseline = one of the three candidates).
   - Per-instance YAML exports include the new `2_last_stage_only_init_schedule__*` and `3_last_stage_only_schedule__*` files.
5. Manual spot-check of `MCFLBDiagnostic`: `chosen_seed_tag`, `ls_status_per_seed`, `last_stage_obj_per_seed` populated; aggregate `last_stage_only_obj` equals the min across `last_stage_obj_per_seed.values()`.

## Out of scope

- Shared time budget / early-termination across seeds (could revisit once we have data on per-seed solve cost).
- Parallelising per-seed CP-SAT solves — sequential for now, matches current `solver_thread_cnt=1`.
- Touching `run_mcf_lb` (legacy) beyond dropping the timelimit arg — it remains TODO-for-removal.
