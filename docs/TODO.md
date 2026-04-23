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
