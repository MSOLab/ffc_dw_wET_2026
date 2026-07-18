# Plan: Extract `neh_cp` into its own module

## Context

`FFcDDWSubroutineController.neh_cp` currently lives in
`src/ffc_ddw_sum_et/orchestration/controller.py` and occupies ~273 lines
(lines 988–1259) — by far the largest step method on the controller.

The reference project `D:/code/hybridflowshop/hybridflowshop/controller/neh_cp.py`
organizes the same algorithm as a standalone module with a
`NehCpConstructor` / `NehCpContext` / `NehCpResult` shell, and a two-stage
CP optimize loop (Stage 1 = makespan, Stage 2 = ∑Cᵢ lex) split into
dedicated helpers. We want to port that two-stage structure here, but
because the function is already long today, the extraction needs to
happen first — **this plan covers only the file separation**. The
two-stage split is a follow-up.

Goals for this step:

1. Move `neh_cp`'s body into a new file `orchestration/neh_cp.py`.
2. Keep the existing public API intact: `controller.neh_cp(...)` still
   works for both the routix-YAML dispatch (`method: neh_cp`) and the
   direct call in `tests/orchestration/test_controller.py:123`.
3. Keep `controller._neh_cp_job_sequence(...)` callable as before — two
   tests depend on it (`test_controller.py:160, 161, 202`).
4. Preserve `NehCpJobPriority` as an importable name from
   `ffc_ddw_sum_et.orchestration.controller` (it is in `__all__`).
5. No algorithmic change, no behavioral change. Green tests with no
   edits to test code.

The **two-stage optimize** port is a separate, follow-up task and is not
designed here.

## Target file layout

```
src/ffc_ddw_sum_et/orchestration/
    __init__.py
    controller.py              # FFcDDWSubroutineController (shrinks by ~275 lines)
    controller_core.py         # unchanged
    neh_cp.py                  # NEW — NehCpContext + NehCpConstructor
    tl_resolver.py             # NEW — resolve_cp_tl (shared helper)
    solution_manager.py        # unchanged
    ...
```

Why a separate `tl_resolver.py`: `resolve_cp_tl` is used by 4 methods in
`controller.py` today (lines 495, 498, 896, 1068) and will also be
needed by the extracted `neh_cp.py` (current line 1068). Leaving it in
`controller.py` would force `neh_cp.py` to import from `controller.py`
while `controller.py` imports from `neh_cp.py` → circular import.
Promoting it to a tiny shared module is the minimum-churn fix.

## Files to modify

- `src/ffc_ddw_sum_et/orchestration/controller.py` — shrink.
- `src/ffc_ddw_sum_et/orchestration/neh_cp.py` — **new**.
- `src/ffc_ddw_sum_et/orchestration/tl_resolver.py` — **new**.

No test edits. No YAML edits. No `__init__.py` edits (see the
re-export note below — `controller.py` keeps exporting `NehCpJobPriority`,
so importers don't need to change).

## `tl_resolver.py` (new, ~65 lines)

Pure move. Verbatim copy of `resolve_cp_tl` from `controller.py:43-102`,
with its docstring. No logic changes.

```python
# orchestration/tl_resolver.py
from __future__ import annotations

def resolve_cp_tl(
    tl_raw: float | str | None,
    job_count: int,
    stage_count: int,
) -> float | None:
    ...  # body unchanged from controller.py:43-102
```

In `controller.py`:
- Remove the inline definition at lines 43–102.
- Add `from .tl_resolver import resolve_cp_tl` to the existing imports block.
- The 4 call sites (lines 495, 498, 896, 1068 — note 1068 moves out)
  continue to work unchanged.

## `neh_cp.py` (new)

### Module shell

```python
# orchestration/neh_cp.py
"""NEH-CP constructor: incremental batched CP-SAT schedule construction."""

from __future__ import annotations

import time
from typing import Literal, Protocol

from ortools.sat.python import cp_model
from routix.report import SubroutineReport

from ffc_ddw_sum_et.algorithm.cumulative import (
    BaseModelBuilder,
    PFMethod,
    decode_pf_method,
)
from ffc_ddw_sum_et.algorithm.dispatcher import MixedDispatcher
from ffc_ddw_sum_et.parameters.ffc_ddw_params import FFcDDWParameters
from ffc_ddw_sum_et.solution.ffc_schedule import FFcSchedule
from ffc_ddw_sum_et.solution.objectives import compute_weighted_earliness_tardiness
from ffc_ddw_sum_et.solution.schedule_build import build_schedule_from_op_starts

from .tl_resolver import resolve_cp_tl

__all__ = ["NehCpConstructor", "NehCpContext", "NehCpJobPriority"]

NehCpJobPriority = Literal["weight-due-pos", "due-weight-pos"]
```

`NehCpJobPriority` is authored here; `controller.py` re-exports it
(see below) so any existing
`from ffc_ddw_sum_et.orchestration.controller import NehCpJobPriority`
keeps working.

### `NehCpContext` Protocol

A minimum interface the `NehCpConstructor` needs from the calling
controller. This is the shape the reference project uses and makes the
module testable/mirrorable. Required members:

```python
class NehCpContext(Protocol):
    instance: FFcDDWParameters
    logger: ... # logging.Logger — import as needed
    solution_manager: ...  # FFcDDWSolutionManager

    def _neh_cp_job_sequence(
        self, job_priority: NehCpJobPriority = "weight-due-pos"
    ) -> list[str]: ...
```

`FFcDDWSubroutineController` already exposes `instance`, `logger`,
`solution_manager` (inherited from `FFcDDWSubroutineControllerCore`) and
`_neh_cp_job_sequence` (lives on the controller — see below). So it
satisfies this Protocol structurally with no new code.

### `NehCpConstructor` class

Single public method `run(...)`, body is the current `neh_cp` body
transplanted verbatim with `self` rebound to `ctx` where it reads
controller state.

```python
class NehCpConstructor:
    def __init__(self, ctx: NehCpContext) -> None:
        self._ctx = ctx

    def run(
        self,
        solver_thread_cnt: int = 1,
        added_batch_size: int = 1,
        cp_tl: float | str | None = None,
        apply_cumulative_tl: bool = False,
        pf_method: PFMethod = "PF1",
        skip_pf_below_obj: str | float | None = None,
        error_if_infeasible: bool = False,
        job_priority: NehCpJobPriority = "weight-due-pos",
    ) -> SubroutineReport:
        """(docstring moved verbatim from controller.neh_cp)"""
        # Body is controller.py:1050-1259 with these 3 textual substitutions:
        #   self.instance          → self._ctx.instance
        #   self.logger            → self._ctx.logger
        #   self._neh_cp_job_sequence(...)  → self._ctx._neh_cp_job_sequence(...)
        # Everything else is unchanged. The FFcDDWSolution registration
        # at the tail (controller.py ~1260) is done by the caller — see below.
```

Important: the current `neh_cp` registers its incumbent via
`self.solution_manager.register_subroutine_solution(...)` at the tail
(around lines 1260-before-return in the trimmed view we read). To keep
the Constructor focused on algorithm + report and avoid leaking the
solution-manager call deep into the module, the **incumbent
registration stays in the thin controller wrapper** (see next section).
The Constructor returns a `SubroutineReport` *plus* the final
`FFcSchedule`. Two ways to do this:

- Option A (simplest, chosen): Constructor returns `SubroutineReport`
  and stores the final schedule on the instance as
  `self.final_schedule: FFcSchedule | None`. The controller wrapper
  reads `constructor.final_schedule` and calls
  `solution_manager.register_subroutine_solution(...)`.
- Option B: introduce `NehCpResult` dataclass now. Saved for the
  follow-up (two-stage work) where a dataclass carries more fields.

Pick **Option A** for this step — it keeps the data surface minimal and
avoids introducing a dataclass we will re-shape in the next step.

### `controller.neh_cp` wrapper (replaces lines 988-1259)

The controller keeps a thin method so routix-YAML dispatch
(`method: neh_cp`) and `controller.neh_cp(...)` in tests resolve to the
same call path. The method becomes:

```python
def neh_cp(
    self,
    solver_thread_cnt: int = 1,
    added_batch_size: int = 1,
    cp_tl: float | str | None = None,
    apply_cumulative_tl: bool = False,
    pf_method: PFMethod = "PF1",
    skip_pf_below_obj: str | float | None = None,
    error_if_infeasible: bool = False,
    job_priority: NehCpJobPriority = "weight-due-pos",
) -> SubroutineReport:
    """(same docstring; redirect reader to NehCpConstructor.run for detail)"""
    from .neh_cp import NehCpConstructor  # local import to avoid cycles

    constructor = NehCpConstructor(self)
    report = constructor.run(
        solver_thread_cnt=solver_thread_cnt,
        added_batch_size=added_batch_size,
        cp_tl=cp_tl,
        apply_cumulative_tl=apply_cumulative_tl,
        pf_method=pf_method,
        skip_pf_below_obj=skip_pf_below_obj,
        error_if_infeasible=error_if_infeasible,
        job_priority=job_priority,
    )
    if constructor.final_schedule is not None and report.obj_value is not None:
        self.solution_manager.register_subroutine_solution(
            FFcDDWSolution(
                schedule=constructor.final_schedule,
                obj_value=report.obj_value,
            ),
        )
    return report
```

(The exact registration call should mirror what lines 1260-area do in
the current `neh_cp` — copy that shape, don't invent one.)

### `controller._neh_cp_job_sequence`

**Stays on the controller unchanged** (lines 979-987). Tests call
`controller._neh_cp_job_sequence(...)` directly
(`test_controller.py:160, 161, 202`), so its public surface must be
preserved. The Constructor reaches it via the Context Protocol.

### Controller imports cleanup

In `controller.py`:
- Remove the `resolve_cp_tl` definition (→ moved to `tl_resolver.py`).
- Remove the direct imports that only `neh_cp` used, if any become
  unused. Specifically, after extraction verify these are still needed
  by remaining controller methods (they almost certainly are, but
  confirm via grep before deleting):
  - `BaseModelBuilder`, `PFMethod`, `decode_pf_method`
  - `MixedDispatcher`
  - `build_schedule_from_op_starts`, `compute_weighted_earliness_tardiness`
  - `FFcSchedule`, `FFcDDWParameters`
  - `cp_model`, `time`
  Do NOT remove anything that other step methods still use.
- Keep `NehCpJobPriority` importable from `controller.py` for
  backwards compatibility:
  ```python
  from .neh_cp import NehCpJobPriority  # re-exported; keep in __all__
  ```
  `__all__` at line 38 stays the same.

## What explicitly does NOT happen in this step

- **No two-stage optimize split.** The reference project's
  `_solve_cp_model` (Stage 1 makespan → Stage 2 ∑Cᵢ lex) and the
  `NehCpRunState` / `NehCpResult` dataclasses land in a follow-up plan.
- No new parameters on `neh_cp`.
- No change to YAML configs under `metadata/20260423/neh_cp_config_*.yaml`.
- No change to tests.

## Verification

1. **Static**
   - `python -m compileall src/ffc_ddw_sum_et/orchestration` — syntax /
     import-cycle check.
   - `ruff check src tests` (or whatever the project runs) — no new
     lint errors.
2. **Unit tests**
   - `pytest tests/orchestration/test_controller.py -q` — specifically
     the two existing `neh_cp` tests must pass unchanged:
     - `test_neh_cp_registers_full_schedule` (line 119)
     - `test_neh_cp_job_sequence_priority` (line 140)
     - `test_neh_cp_job_sequence_due_weight_pos` (line 168)
   - Full suite: `pytest -q`.
3. **End-to-end smoke**
   - Run `python main.py` with `CONFIG_PATH = metadata/20260423/neh_cp_config_4.yaml`
     on a small instance subset; confirm the routix YAML dispatch
     resolves `method: neh_cp` and produces a `SubroutineReport` with a
     finite `obj_value`. The incumbent registered in the
     `solution_manager` should match the pre-extraction behavior.
4. **Diff sanity**
   - `git diff --stat` — expect roughly: `controller.py` −280 lines,
     `neh_cp.py` +280 lines, `tl_resolver.py` +65 lines. Net LOC flat (small
     uptick from the Context Protocol + thin wrapper).

## Critical files to re-read before coding

- `src/ffc_ddw_sum_et/orchestration/controller.py:38-102` — imports,
  `__all__`, `NehCpJobPriority`, `resolve_cp_tl`.
- `src/ffc_ddw_sum_et/orchestration/controller.py:977-1260` — the body
  to extract.
- `src/ffc_ddw_sum_et/orchestration/controller_core.py:34-90` — confirm
  which attrs (`instance`, `logger`, `solution_manager`) the base class
  provides so the Context Protocol matches exactly.
- `src/ffc_ddw_sum_et/orchestration/solution_manager.py` — confirm the
  exact `register_subroutine_solution` / `FFcDDWSolution` call shape
  used in the wrapper.
- `tests/orchestration/test_controller.py:119-210` — the contract the
  extraction must preserve.
