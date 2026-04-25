# TODO

Future refactor ideas and design notes that are not urgent enough to act on
today but worth capturing so future work does not re-derive the reasoning.

## Decorator for `solution_manager.register` boilerplate

Step methods in `src/ffc_ddw_sum_et/orchestration/controller.py` currently call
`self.solution_manager.register(report, solution_or_None)` explicitly at the
end. Once the number of step methods grows enough that the boilerplate becomes
a real maintenance cost, introduce a decorator (e.g. `@registers_result`) that:

- wraps a step method returning `(SubroutineReport, FFcDDWSolution | None)`
- calls `self.solution_manager.register(...)` automatically
- optionally absorbs the `start_elapsed = self.timer.elapsed_sec` /
  `elapsed = ...` boilerplate as well

Step methods that register multiple times, or register conditionally within
loops (see hybridflowshop's `repeat_while_improvement` pattern), should stay on
the explicit `self.solution_manager.register(...)` call — the decorator assumes
"1 step = 1 register" and is not a fit for those.

**Why:** YAGNI today (only 2 step methods — `run_fam`, `run_mcf_lb`) but step
count is expected to grow.

**When to act:** When step method count noticeably increases and the `register`
boilerplate is repeated with no meaningful variation.

**Alternative hook point:** If the decorator approach runs into issues with
timer/context management, consider overriding `_call_method` instead — see
hybridflowshop's `hybridflowshop/controller/controller_core.py:467` for an
existing precedent of extending the routix step hook.

## Lift `NehCpConstructor` onto the `Algorithm` boundary

`src/ffc_ddw_sum_et/orchestration/neh_cp.py` currently lives in the
orchestration layer and exposes its own `NehCpContext(Protocol)` (logger,
instance, solution_manager, `get_file_path_for_subroutine`). It also returns
`SubroutineReport` directly and registers via `solution_manager.register`
inside the constructor.

Once the algorithm-side execution contract (`Algorithm` / `AlgSpec` /
`AlgRecord`) defined in `docs/architecture/algorithm-principles.md` is firm
enough for new entries:

1. Move `NehCpConstructor` (or its execution-only core) under
   `src/ffc_ddw_sum_et/algorithm/...`.
2. Drop the local `NehCpContext` Protocol in favor of the standard algorithm
   inputs (instance + params + a step-log sink supplied by the caller).
3. Change the return type from `SubroutineReport` to `AlgRecord` — keep
   `obj_value = weighted E+T` per the project's `AlgRecord.obj_value`
   convention. Move the `solution_manager.register` call to the orchestration
   adapter that wraps the algorithm.
4. Per-step log emission (`_step_log.yaml`) should still be supported, but
   via an injected sink rather than a context method, so the algorithm core
   has no dependency on `get_file_path_for_subroutine`.

**Why:** YAGNI today — the only caller is `FFcDDWSubroutineController`, the
`Algorithm` boundary is still being shaped, and lifting `neh_cp` would force
that shape prematurely (KISS). The current Protocol-based seam is enough for
the single caller. Doing this now would also expand the diff well beyond the
scope of the per-step log change that triggered this note.

**When to act:** When (a) a second caller appears that wants to invoke
`neh_cp` outside the orchestration controller, or (b) the algorithm-side
contract (`Algorithm` / `AlgRecord`) has an established sibling that
`neh_cp` would simply slot into without inventing new conventions.

## Hardcoded TL (time limit) formula in analysis_long sheet

The `analysis_long` Excel sheet computes `TL = 0.09 * job_count * stage_count`
as a reference time limit. The `time%` column is then `(elapsedSec / TL) * 100`.
The coefficient `0.09` is hardcoded in `src/ffc_ddw_sum_et/orchestration/reporting.py`
in the `_write_analysis_sheets` method.

**Why:** The coefficient is experimentally determined and may need adjustment
for different instance families or solver configurations. Making it
configurable adds complexity (config key, validation, default) for a single
reporting column.

**When to act:** When the coefficient needs to change, or when multiple
teams use different TL thresholds and want to configure it per experiment.
