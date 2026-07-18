# Plan: `apply_lb_by_mcf` Subroutine (LB-only Variant of `run_mcf_lb_4`)

## Context

`FFcDDWSubroutineController.run_mcf_lb_4` does two things in one method:

1. Compute the MCF preemptive lower bound (`mcf_lb`, a valid LB on the
   weighted E+T objective).
2. As a byproduct, build a feasible full schedule via Phases 2–4
   (last-stage CP-SAT seed → reverse-dispatch → profile-fix CP-SAT).

There is currently **no way** to ask only for (1). Every entry path
through `run_mcf_lb_4` runs Phase 1 and then proceeds into Phase 2 (the
only early-return paths return `obj_value=None` *because* Phase 2/3
reported infeasibility, not as a deliberate LB-only mode).

We want a new subroutine that:

- Solves the MCF relaxation,
- Returns `SubroutineReport(obj_value=None, obj_bound=mcf_lb)`,
- Does **not** register any incumbent schedule (no Gantt is emitted —
  same convention `run_last_stage_cp_sat_lb` already follows for partial
  solutions).

To keep DRY, we extract the MCF-solve portion of `run_phase1` into a
small helper that both the existing pipeline and the new subroutine
call.

## Critical Files

- `src/ffc_ddw_sum_et/algorithm/mcf_lb/phase1_mcf.py`
  — refactor: extract MCF-solve helper out of `run_phase1`.
- `src/ffc_ddw_sum_et/orchestration/controller.py`
  — add new method `apply_lb_by_mcf` on `FFcDDWSubroutineController`.
- `metadata/20260428/mcf_lb_only_config.yaml`
  — new experiment config that selects the new subroutine.

## Existing Functions / Patterns Reused

- `ParallelMachinePreemptionMcf.from_instance(instance).solve()` and
  `.get_obj_value()` — `src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py`
  (the actual MCF solver).
- `MCFPreemptiveSchedule.from_flow_dict(...)` —
  `src/ffc_ddw_sum_et/solution/mcf_preemptive_schedule.py`
  (build the preemptive schedule from MCF arc flows).
- `MCFLBDiagnostic` —
  `src/ffc_ddw_sum_et/algorithm/mcf_lb/diagnostic.py`
  (records `mcf_solve_sec`, `mcf_lb`, `reached_phase`; mirrors what
  `run_mcf_lb_4` already plumbs through `self.mcf_lb_diagnostic`).
- `SubroutineReport(elapsed_time, obj_value, obj_bound)` from `routix`.
  Returning `obj_value=None` is already a supported pattern (see
  `controller.py:530`, `561`, `755`).
- The solution manager's "no register → no incumbent → no Gantt"
  convention used by `run_last_stage_cp_sat_lb` (`controller.py:640`+).

## Implementation

### 1. Refactor `phase1_mcf.py` — extract `solve_mcf_lb` helper

Split the MCF-solve portion (currently lines 84–100 of
`phase1_mcf.py`) out of `run_phase1` into a new module-level helper.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class McfLbResult:
    """Bare result of solving the MCF relaxation: bound + preemptive schedule.

    Used by `run_phase1` to seed the full 4-phase pipeline, and by
    `apply_lb_by_mcf` to report a global lower bound with no schedule.
    """

    mcf_lb: float
    mcf_preemptive_schedule: MCFPreemptiveSchedule
    mcf: ParallelMachinePreemptionMcf  # retained so phase1 can read priority maps


def solve_mcf_lb(
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
) -> McfLbResult:
    """Solve the MCF relaxation and record the bound on `diagnostic`.

    Mutates `diagnostic` in place: sets `mcf_solve_sec`, `mcf_lb`, and
    advances `reached_phase` to `"mcf"`.

    Raises:
        RuntimeError: if the MCF flow is not optimal.
    """
    last_stage_id = instance.stage_id_list[-1]
    t_mcf = time.monotonic()
    mcf = ParallelMachinePreemptionMcf.from_instance(instance)
    mcf.solve()
    if not mcf.is_optimal():
        raise RuntimeError(f"MCF not optimal for instance {instance.name}")
    mcf_lb = float(mcf.get_obj_value())
    diagnostic.mcf_solve_sec = time.monotonic() - t_mcf
    diagnostic.mcf_lb = mcf_lb
    diagnostic.reached_phase = "mcf"

    mcf_preemptive_schedule = MCFPreemptiveSchedule.from_flow_dict(
        mcf.get_variable_value_dict(),
        stage_id=last_stage_id,
        machines=instance.stage_2_machines_map[last_stage_id],
    )
    return McfLbResult(
        mcf_lb=mcf_lb,
        mcf_preemptive_schedule=mcf_preemptive_schedule,
        mcf=mcf,
    )
```

`run_phase1` is then trimmed to:

```python
def run_phase1(
    instance: FFcDDWParameters,
    diagnostic: MCFLBDiagnostic,
    logger: logging.Logger | None = None,
    last_stage_only_priority_tags: Sequence[SeedTag] | None = None,
) -> Phase1State:
    del logger  # reserved for future use
    last_stage_id = instance.stage_id_list[-1]

    mcf_result = solve_mcf_lb(instance, diagnostic)
    mcf = mcf_result.mcf

    job_2_pos = {j: i for i, j in enumerate(instance.job_id_list)}
    job_2_release_map = instance.get_job_2_p_sum_except_last_stage()
    duration_map = instance.get_job_2_p_map_for_stage(last_stage_id)

    priority_map_by_tag: dict[SeedTag, ...] = { ...mcf-derived priority maps... }
    # ... existing seed-building loop unchanged ...

    return Phase1State(
        mcf_lb=mcf_result.mcf_lb,
        last_stage_id=last_stage_id,
        job_2_pos=job_2_pos,
        job_2_release_map=job_2_release_map,
        mcf_preemptive_schedule=mcf_result.mcf_preemptive_schedule,
        last_stage_seeds=last_stage_seeds,
    )
```

Update `__all__` to export `solve_mcf_lb` and `McfLbResult`.
Re-export the helper from `ffc_ddw_sum_et.algorithm.mcf_lb.__init__` if
the package already publishes `run_phase1` (mirror that pattern).

### 2. Add `apply_lb_by_mcf` to `controller.py`

Insert **right above** `run_mcf_lb_4` (`controller.py:380`). Imports
`solve_mcf_lb` from `..algorithm.mcf_lb.phase1_mcf` (or via the package
re-export, matching how `run_phase1` is currently imported).

```python
def apply_lb_by_mcf(self) -> SubroutineReport:
    """Compute the MCF preemptive lower bound and return it without
    constructing a feasible full schedule.

    Returns a SubroutineReport with `obj_value=None` and
    `obj_bound = mcf_lb`. No incumbent is registered with the solution
    manager (this subroutine produces no full schedule), so no Gantt /
    schedule YAML is emitted for this step.
    """
    start_elapsed = time.monotonic()
    diag = MCFLBDiagnostic()
    self.mcf_lb_diagnostic = diag

    mcf_result = solve_mcf_lb(self.instance, diag)
    obj_bound_by_mcf = mcf_result.mcf_lb

    self.mcf_preemptive_schedule = mcf_result.mcf_preemptive_schedule
    self.mcf_lb_phase_schedules.clear()
    self.mcf_lb_phase_schedules.append(
        ("1_mcf_preemptive_schedule", mcf_result.mcf_preemptive_schedule)
    )

    self.logger.info("apply_lb_by_mcf: MCF LB = %d", int(obj_bound_by_mcf))

    elapsed = time.monotonic() - start_elapsed
    return SubroutineReport(
        elapsed_time=elapsed,
        obj_value=None,
        obj_bound=obj_bound_by_mcf,
    )
```

Notes:
- No `solution_manager.register(...)` call — the bound is a global LB
  on the open problem, but there is no `FFcDDWSolution.schedule` to
  register, so the incumbent is left untouched (consistent with the
  early-return paths of `run_mcf_lb_4` and with `run_last_stage_cp_sat_lb`).
- We still set `self.mcf_lb_diagnostic` and append to
  `self.mcf_lb_phase_schedules` so downstream diagnostic dumps that key
  off these attributes continue to work.

### 3. New experiment config `metadata/20260428/mcf_lb_only_config.yaml`

Mirror the layout of `metadata/20260427/wxd2_1_config.yaml`. Single
scenario, no time limit needed (MCF is quick on these sizes), one
worker per CP-SAT pool is irrelevant since we don't call CP-SAT.

```yaml
run_mode: FULL_RUN
benchmark_dir: benchmarks/PRA2017/large
ins_index_source: benchmarks/PRA2017/pra2017_hybrid_match.csv
ins_index: [1435, 1436, 1437, 1438, 1439]
output_dir: output/20260428
instance_worker_cnt: 48
draw_gantt: false
painter_thread_cnt: 48

scenarios:
  - name: mcf_lb_only
    timelimit: 60.0
    output_subdir: mcf_lb_only
    subroutine_flow:
      - method: apply_lb_by_mcf
```

Per memory, `instance_worker_cnt: 48` is the default for fast
experiment runs. `draw_gantt: false` is a belt-and-braces guard since
this scenario produces no incumbent schedule anyway.

`main.py` currently has a hardcoded `CONFIG_PATH`. Do **not** edit
`main.py` as part of this change — point at the new config by changing
`CONFIG_PATH` only when actually running the experiment, exactly as
prior configs (`wxd1_*`, `wxd2_1_*`) are switched in.

## What `obj_value=None` Means in the Pipeline (no extra wiring needed)

- `SubroutineReport.obj_value: float | None` and
  `FFcDDWSolution.obj_value: float | None` already accept `None`.
- `FFcDDWSolutionManager._get_obj_value` raises if asked to extract an
  objective from a `None` solution — but this code path is only reached
  for *registered* solutions, and we deliberately don't register.
- `ffcddw_single_instance_runner.py` only emits `*_schedule.yaml`
  (and downstream Gantt) when the incumbent is non-None, so no Gantt
  will be produced — matching the user's request.
- The `obj_bound = mcf_lb` is preserved in the per-subroutine report
  log, so `_obj_log.yaml` / `_statistics.yaml` will reflect the
  bound for this run.

## Verification

Run from repo root:

```bash
uv run ruff check
uv run ruff format
uv run pytest tests/ -q
```

Then a small experiment run on 2–3 instances (point `CONFIG_PATH` in
`main.py` at `metadata/20260428/mcf_lb_only_config.yaml`):

```bash
uv run python main.py
```

Expected artifacts under `output/20260428/mcf_lb_only/<ins>/`:
- `*_obj_log.yaml` containing one entry with `obj_value: null` (or
  absent) and `obj_bound: <mcf_lb>`.
- `*_statistics.yaml` with the elapsed MCF solve time.
- **No** `*_schedule.yaml` and **no** `*_gantt.png` (no incumbent).

Cross-check the bound: run `run_mcf_lb_4` on the same instance and
confirm the `obj_bound` reported by both subroutines is identical.

## Out of Scope

- Adding an `only_lb: bool` flag to `run_mcf_lb_4`. A separate method
  is cleaner: avoids a dead branch through Phases 2–4, keeps each
  method's contract narrow (SRP), and makes the experiment config
  intent explicit.
- Emitting the MCF preemptive schedule as a Gantt PNG. The runner's
  Gantt path is bound to the registered incumbent; lifting that is a
  separate concern.
