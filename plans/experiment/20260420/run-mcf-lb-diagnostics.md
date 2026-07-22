# run_mcf_lb diagnostic artifacts

## Context

`run_mcf_lb` ([controller.py:73-357](../../../src/ffc_ddw_sum_et/orchestration/controller.py#L73-L357)) chains four solves — MCF preemptive LB, last-stage-only CP-SAT, reverse-dispatch with profile alignment, then a profile-fix full CP-SAT solve — but only reports a single aggregate `elapsed_time` and the final `(obj_value, obj_bound)` via `SubroutineReport`. We cannot currently answer:

1. How tight is the last-stage-only CP-SAT **certified LB** (`ls_solver.best_objective_bound`) relative to the MCF LB?
2. How much does the last-stage-only **primal objective** (`ls_solver.objective_value`) exceed the LS-only LB when the solver didn't prove optimality?
3. How much does the profile-fix CP-SAT full solve actually improve on the dispatched schedule?
4. Where is the time going — MCF vs last-stage CP-SAT vs reverse-dispatch vs profile-fix CP-SAT?
5. Where does the final `profileFixObj` land relative to BKS for each instance?

Goal: capture these as a per-instance YAML artifact and surface scalar fields as columns in the experiment-level summary CSV so we can read the distribution across a full sweep. The six scalar values the downstream analysis must be able to plot per row are: **MCF LB, last-stage-only LB (CP-SAT dual bound), last-stage-only objective (CP-SAT primal), BKS, dispatched objective, profile-fix objective**, plus the four per-phase timings.

## Approach

Three-layer change, mirroring the existing `last_stage_cp_sat_solution` flow (controller attr → runner dump → reporter column):

1. Controller captures per-phase values and times into a new dataclass attached to `self`.
2. Single-instance runner dumps it to `{ins}_mcf_lb_diagnostic.yaml` and forwards a plain-dict copy on `InstanceResult`.
3. Reporter flattens the dict into `extra_outputs` so it appears in the experiment summary CSV.

Per-phase timing uses `self.timer.elapsed_sec` deltas — same timer already used for the aggregate `elapsed_time`, so the diagnostic and `SubroutineReport` stay consistent.

## Files to modify

- [src/ffc_ddw_sum_et/orchestration/controller.py](../../../src/ffc_ddw_sum_et/orchestration/controller.py) — add `MCFLBDiagnostic` dataclass; instrument `run_mcf_lb`; correct stale step-number comments.
- [src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py](../../../src/ffc_ddw_sum_et/orchestration/ffcddw_single_instance_runner.py) — dump YAML; carry dict on `InstanceResult`.
- [src/ffc_ddw_sum_et/orchestration/reporting.py](../../../src/ffc_ddw_sum_et/orchestration/reporting.py) — merge diagnostic scalars + BKS into `extra_outputs` in `_write_summary_csv`.

No new packages. No changes under `io/` or `algorithm/` — matches the architecture guardrails in `CLAUDE.md`.

## Changes

### 1. `controller.py` — `MCFLBDiagnostic` + instrumentation

Add near the top of the file (module-level, alongside other controller dataclasses):

```python
@dataclass(slots=True)
class MCFLBDiagnostic:
    # Objective / bound values
    mcf_lb: float | None = None                    # step 1-1
    last_stage_only_obj: float | None = None       # step 1-3 ls_solver.objective_value (primal)
    last_stage_only_bound: float | None = None     # step 1-3 ls_solver.best_objective_bound (dual)
    dispatched_obj: float | None = None            # step 2-2 ET of reverse-dispatched schedule
    profile_fix_obj: float | None = None           # step 2-3 pf_solver.objective_value (primal)
    profile_fix_bound: float | None = None         # step 2-3 pf_solver.best_objective_bound (dual)
    # Phase timings (seconds)
    mcf_solve_sec: float | None = None             # step 1-1
    last_stage_cp_sat_sec: float | None = None     # step 1-3
    dispatch_sec: float | None = None              # step 2-1 + 2-2 (reverse-dispatch + ET compute)
    profile_fix_cp_sat_sec: float | None = None    # step 2-3
    # Context
    reached_phase: str = "init"  # init | mcf | last_stage | dispatched | profile_fix
    ls_status: str | None = None
    pf_status: str | None = None
    single_stage: bool = False
```

Naming rationale: the original draft conflated "LB" with "objective value" on a single field.
In CP-SAT terms they are distinct — `objective_value` is the best primal solution found
(upper bound on the LS-only optimum), while `best_objective_bound` is the dual bound
(certified lower bound on the LS-only optimum, and therefore a valid LB for the full
FFc-DDW problem since LS-only is a relaxation). They coincide only when CP-SAT proves
optimality within the time limit; under time pressure they diverge, and the divergence
is itself a diagnostic signal.

Inside `run_mcf_lb`, at line 99 just after `start_elapsed = ...`:

```python
diag = MCFLBDiagnostic()
self.mcf_lb_diagnostic = diag  # expose up-front so early returns retain partial data
```

Wrap each solve block with a local `t_*` delta and write into `diag`. Update `reached_phase` only *after* each phase's values are filled, so the label never lies:

| Insertion point | Action |
| --- | --- |
| Before `mcf = Parallel...` (line 108) | `t0 = self.timer.elapsed_sec` |
| After `mcf_lb = float(...)` (line 112) | `diag.mcf_solve_sec = self.timer.elapsed_sec - t0`; `diag.mcf_lb = mcf_lb`; `diag.reached_phase = "mcf"` |
| Before `ls_status = ls_solver.Solve(...)` (line 173) | `t1 = self.timer.elapsed_sec` |
| Immediately after line 173 | `diag.last_stage_cp_sat_sec = self.timer.elapsed_sec - t1`; `diag.ls_status = ls_solver.StatusName(ls_status)` |
| After line 204 (`ls_solver.objective_value` read) | `diag.last_stage_only_obj = float(ls_solver.objective_value)`; `diag.last_stage_only_bound = float(ls_solver.best_objective_bound)`; `diag.reached_phase = "last_stage"` |
| Immediately before `if c == 1:` (line 210) | `t_disp = self.timer.elapsed_sec`; `diag.single_stage = (c == 1)` |
| After `step2_obj = ...` (line 262) | `diag.dispatch_sec = self.timer.elapsed_sec - t_disp`; `diag.dispatched_obj = step2_obj`; `diag.reached_phase = "dispatched"` |
| Before `pf_status = pf_solver.Solve(...)` (line 299) | `t2 = self.timer.elapsed_sec` |
| Immediately after line 299 | `diag.profile_fix_cp_sat_sec = self.timer.elapsed_sec - t2`; `diag.pf_status = pf_solver.StatusName(pf_status)` |
| After `pf_bound = ...` (line 302-304) | `diag.profile_fix_bound = pf_bound` |
| After `final_obj = ...` (line 333) | `diag.profile_fix_obj = final_obj`; `diag.reached_phase = "profile_fix"` |

Capture `last_stage_only_bound` at line 204 (post-feasibility), not at line 173, because
`best_objective_bound` is only meaningful once CP-SAT has produced a feasible solution.
The infeasible early-return at line 175-184 intentionally leaves it `None`.

Three early-return paths (lines 182-184, 254-256, 313-316) need no special handling — `diag` is already stored on `self`, so whatever has been filled survives.

### 2. `ffcddw_single_instance_runner.py` — dump + carry

Extend `InstanceResult` (line 37-56):

```python
mcf_lb_diagnostic: dict[str, Any] | None = None
```

Add top-level imports (not inside the function):

```python
from dataclasses import asdict
from routix.io import dump_yaml
```

(Place alongside existing `dataclasses` / `routix` imports at the top of the file.)

In `_post_run_process_inner`, after the existing `last_stage_cp_sat` block (line 187):

```python
diag = getattr(controller, "mcf_lb_diagnostic", None)
diag_dict = asdict(diag) if diag is not None else None
if diag_dict is not None and self.working_dir is not None:
    try:
        dump_yaml(
            diag_dict,
            self.working_dir / f"{self.ins_name}_mcf_lb_diagnostic.yaml",
        )
    except Exception:
        logger.exception(
            "Error saving mcf_lb_diagnostic yaml for %s", self.ins_name
        )
```

Pass `mcf_lb_diagnostic=diag_dict` in the final `return InstanceResult(...)` (line 209). Storing a plain dict (not the dataclass) keeps `reporting.py` from needing to import the controller-level type.

### 3. `reporting.py` — surface in summary CSV

Inside `_write_summary_csv` (line 289-334), before constructing `FFcDDWSummary`:

```python
diag = ir.mcf_lb_diagnostic or {}

# BKS from the instance-table meta loaded in _load_index_to_meta
ins_index = self._resolve_ins_index(ir.instance_name)
bks = (
    self._index_to_meta.get(ins_index, {}).get("BKS")
    if ins_index is not None
    else None
)

# Reported obj_bound mirrors what run_mcf_lb actually returns in SubroutineReport:
#   obj_bound_final = max(mcf_lb, pf_bound)   (controller.py:305)
# Surfacing it lets downstream analysis cross-check best_bound against the
# per-phase bounds without re-deriving the max.
reported_obj_bound = None
if diag.get("mcf_lb") is not None:
    reported_obj_bound = diag["mcf_lb"]
    if diag.get("profile_fix_bound") is not None:
        reported_obj_bound = max(reported_obj_bound, diag["profile_fix_bound"])

def _gap(a_key: str, b_key: str) -> float | None:
    a, b = diag.get(a_key), diag.get(b_key)
    return a - b if a is not None and b is not None else None

ls_bound_gap = _gap("last_stage_only_bound", "mcf_lb")   # >= 0 expected
ls_primal_gap = _gap("last_stage_only_obj", "last_stage_only_bound")  # >= 0: primal-dual
pf_improvement = _gap("dispatched_obj", "profile_fix_obj")  # >= 0 expected
pf_vs_bks = (
    diag["profile_fix_obj"] - bks
    if diag.get("profile_fix_obj") is not None and bks is not None
    else None
)

mcf_extras = {
    # The six core values requested for the per-instance diagnostic table
    "mcfLb": diag.get("mcf_lb"),
    "lastStageOnlyBound": diag.get("last_stage_only_bound"),
    "lastStageOnlyObj": diag.get("last_stage_only_obj"),
    "bks": bks,
    "dispatchedObj": diag.get("dispatched_obj"),
    "profileFixObj": diag.get("profile_fix_obj"),
    # Supporting bounds
    "profileFixBound": diag.get("profile_fix_bound"),
    "reportedObjBound": reported_obj_bound,
    # Derived gaps
    "lastStageBoundMinusMcfGap": ls_bound_gap,
    "lastStagePrimalMinusBoundGap": ls_primal_gap,
    "dispatchedMinusProfileFixGap": pf_improvement,
    "profileFixMinusBksGap": pf_vs_bks,
    # Per-phase timings
    "mcfSolveSec": diag.get("mcf_solve_sec"),
    "lastStageCpSatSec": diag.get("last_stage_cp_sat_sec"),
    "dispatchSec": diag.get("dispatch_sec"),
    "profileFixCpSatSec": diag.get("profile_fix_cp_sat_sec"),
    # Context
    "mcfLbReachedPhase": diag.get("reached_phase") or "",
}
```

Merge into `extra_outputs={**mcf_extras, "error": _last_non_empty_line(ir.error) or ""}`. Because `FFcDDWSummary._stringified_outputs` maps `None → ""`, scenarios that never ran `run_mcf_lb` produce empty cells automatically — no special casing needed.

Column key naming follows camelCase to match the existing `FFcDDWOutputSummary.to_string_dict` convention. Signs are self-documenting: all three gap columns are expected `>= 0`; a negative value signals a bug and is surfaced as-is. `profileFixMinusBksGap` may be negative when the new run genuinely beats the recorded BKS — that is informative, not a bug.

`bks` is resolved via reporting's existing `_load_index_to_meta` / `_resolve_ins_index` path (populated from `benchmarks/PRA2017/pra2017_instance_table.csv`). No controller- or runner-side BKS plumbing is needed because BKS is a property of the instance, not of the run.

### 4. `controller.py` — comment corrections

Inline step-number comments in `run_mcf_lb` drifted from the docstring's 2-1/2-2/2-3
numbering. Fix two cases while we're editing this function:

| Line | Current | Fix |
| --- | --- | --- |
| 274 | `# ----- Step 2-2: profile-fix CP-SAT full solve -----` | `# ----- Step 2-3: profile-fix CP-SAT full solve -----` |
| 336-338 | `"run_mcf_lb step 2-3: post-build objective %.3f ..."` | keep as-is (already correct) |

Also, the docstring (lines 80-90) lists step 2-2 as "Align last stage back to CP-SAT
times via right_shift"; the current code does not call `right_shift` anywhere, so the
alignment step described in the docstring is either implicit in `as_reversed()` or has
been removed. This is out of scope for the diagnostic change — flagged here for a
follow-up docstring audit, not fixed in this plan.

## Edge cases

- **MCF non-optimal**: line 111 raises `RuntimeError`, propagated to the runner's try/except — `mcf_lb_diagnostic` stays `None`, CSV cells empty. No change needed.
- **Last-stage CP-SAT infeasible** (line 175-184): `diag` contains only the `mcf_*` fields; `reached_phase == "mcf"`. `last_stage_only_obj` and `last_stage_only_bound` both stay `None` (we intentionally do not read `best_objective_bound` on infeasible).
- **Reverse-dispatch returns `None`** (line 249-256): `diag` contains `mcf_*` + `last_stage_only_*`; `reached_phase == "last_stage"`. `dispatch_sec` stays `None` because the early return happens before the `t_disp` delta is written.
- **Profile-fix infeasible** (line 307-316): `profile_fix_obj` stays `None`; `profile_fix_bound` and `pf_status` are captured. `reportedObjBound` still resolves via `max(mcf_lb, profile_fix_bound)`.
- **Single-stage (`c == 1`)**: `dispatched_obj` equals last-stage ET (no reverse-dispatch executed). `single_stage=True` disambiguates rows where `dispatchedMinusProfileFixGap` is trivially tight. `dispatch_sec` will be ~0 in this branch.
- **BKS missing for an instance** (index not in `pra2017_instance_table.csv`, or non-PRA2017 scenario): `bks` and `profileFixMinusBksGap` both stay empty in the CSV. No warning — this is the expected path for non-PRA2017 runs.

## Verification

- **End-to-end on the smallest config**:
  ```
  uv run python -m ffc_ddw_sum_et.main --config metadata/20260420/1_mcf_lb_init_3_config.yaml --output-dir /tmp/mcf_lb_diag_check
  ```
  (scope it to a few instances via `ins_index` override if the full 1440 is too slow for a smoke test.)
  Check:
  1. `/tmp/mcf_lb_diag_check/<stamp>/mcf_lb_init/<instance>/<instance>_mcf_lb_diagnostic.yaml` exists and has every field populated.
  2. `<stamp>_summary.csv` has the 17 new columns, populated for every row (6 core values + 2 supporting bounds + 4 derived gaps + 4 phase timings + 1 reached-phase label).
  3. Expected monotonicity on a healthy run:
     - `mcfLb <= lastStageOnlyBound <= lastStageOnlyObj` (MCF LB ≤ LS-only dual ≤ LS-only primal, since LS-only model is given MCF LB as `obj_lb` and the primal bounds its own dual from above).
     - `profileFixObj <= dispatchedObj` (profile-fix warm-started from dispatched, so never worse).
     - `profileFixBound <= profileFixObj` (dual ≤ primal for CP-SAT minimization).
     - `reportedObjBound == max(mcfLb, profileFixBound)`.
     Spot-check with a quick pandas load.
  4. For PRA2017 rows: `bks` is populated and `profileFixMinusBksGap = profileFixObj - bks` reproduces on the head of the frame.
- **Partial-failure smoke**: override `last_stage_only_timelimit: "0.0001nc"` in a throwaway config. Confirm at least one instance lands with `mcfLbReachedPhase == "mcf"` and downstream columns (including `lastStageOnlyBound`) empty.
- **Lint**: `uv run ruff check` after edits.
