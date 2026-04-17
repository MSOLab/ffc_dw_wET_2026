# Algorithm Contract Principles

This document defines the boundary and data-contract rules for the current
algorithm-facing part of the repository.

The goal is to keep algorithm execution isolated from orchestration and
reporting concerns so that a single algorithm run can later be treated as a
small, verifiable unit:

- one immutable execution request goes in,
- one traceable execution record comes out,
- and no hidden dependency is required in between.

This document intentionally focuses on the execution contract around:

- `Algorithm`,
- `AlgSpec`,
- `AlgRecord`.

`AlgResult` is treated as a supporting payload type inside `AlgRecord` rather
than as a separate orchestration concept.

`Launcher` and `Reporter` are intentionally out of scope for now and should not
shape the algorithm boundary in ad hoc ways before their own contracts are
written down.

## Goal

We want the current algorithm boundary to behave like a stable execution
contract, even before the full ALADIN-style runtime is implemented.

That means:

- algorithm code should depend on an explicit input object,
- reporting code should depend on an explicit output object,
- traceability should be carried by data rather than ambient state, and
- future orchestration should not require rewriting core algorithm logic.

## Core Contract

The contract is intentionally simple:

```text
AlgSpec -> Algorithm.run(...) -> AlgRecord
```

The execution unit is one algorithm run for one problem instance under one
algorithm option setting, if an option exists for that algorithm.

The algorithm boundary should answer only one question:

- given this `AlgSpec`, what `AlgRecord` was produced?

It should not also answer:

- how the experiment was scheduled,
- what other runs are doing,
- how results are aggregated,
- how final comparison tables are produced.

## Interface Rules

### Rule 1: model `Algorithm` as a behavioral contract

`Algorithm` should be treated as a protocol-like interface rather than a base
class hierarchy that every implementation must inherit from.

Preferred shape:

```python
from typing import Protocol


class Algorithm(Protocol):
    def run(self, spec: AlgSpec) -> AlgRecord:
        ...
```

The important design choice is behavioral compatibility:

- if an object can consume `AlgSpec`,
- and can produce `AlgRecord`,
- it satisfies the contract.

This keeps algorithm implementations lightweight and easier to test in
isolation.

### Rule 2: `Algorithm` owns execution, not orchestration

`Algorithm` is responsible for executing one run.

It is not responsible for:

- enumerating experiment combinations,
- deciding parallelism,
- aggregating across runs,
- computing benchmark comparison metrics,
- inspecting sibling run directories.

If a future change requires an algorithm implementation to know about the wider
experiment, the boundary is drifting.

### Rule 3: `Algorithm` may write only within `alg_root`

If the execution contract allows file output, the only filesystem location the
algorithm should know is `alg_root` carried inside `AlgSpec`.

That implies:

- the algorithm must not assume an experiment directory layout,
- the algorithm must not navigate upward to parent directories,
- the algorithm must not inspect sibling parameter or benchmark directories.

If `alg_root` is `None`, the algorithm should still be executable and should
skip file writing cleanly.

### Rule 4: prefer `AlgSpec.logger` for logging

If logging is needed during execution, the algorithm should prefer the logger
carried in `AlgSpec`.

That means:

- if `spec.logger` is present, use it,
- if `spec.logger` is `None`, fall back to stdlib module-level logging such as
  `logging.debug(...)`,
- do not make ambient global logging configuration part of the execution
  contract.

Preferred implementation style:

```python
if spec.logger is not None:
    spec.logger.debug("...")
else:
    logging.debug("...")
```

This keeps the log destination explicit when a caller cares about it while
still allowing simple fallback behavior.

### Rule 5: time only the execution phase

Timing recorded in `AlgRecord` should measure the execution phase only.

Exclude:

- input loading already completed before `run`,
- setup that belongs to external orchestration,
- serialization and persistence after the result is produced.

This keeps timing comparable across runs and prevents reporting-oriented IO from
polluting algorithm performance numbers.

## AlgSpec Rules

### Rule 6: `AlgSpec` is the complete execution request

`AlgSpec` should bundle everything an algorithm run needs for one execution.

Expected contents:

- `instance`: the problem instance to solve,
- `option`: optional `AlgOption` for this run,
- `ref_solution`: optional warm-start or reference solution,
- `alg_root`: optional path-like output directory for algorithm-owned file
  writing,
- `logger`: optional logger for algorithm execution.

Requiredness should stay minimal:

- `instance` is required,
- `option`, `ref_solution`, `alg_root`, and `logger` are optional.

The algorithm should not rely on hidden globals, mutable module state, or
external lookup tables that are not represented in `AlgSpec`.

### Rule 7: `AlgSpec` is immutable

`AlgSpec` is an input contract, not a workspace.

Algorithm code must treat it as read-only:

- do not mutate the contained problem instance,
- do not rewrite option values,
- do not stash progress into the spec object.

Any execution-time state belongs to local variables or to the produced
`AlgRecord`, not back inside the request.

### Rule 8: `AlgSpec` carries traceability, not just convenience

Fields inside `AlgSpec` should exist because they help explain a run later, not
only because they are convenient during implementation.

In practice this means:

- the option object should include all parameters that affect behavior,
- random seed should be part of the option state when randomness matters,
- warm-start information should be explicit when used,
- output location should be explicit when file persistence is enabled,
- logger selection should be explicit when a caller wants algorithm logs routed
  somewhere specific.

If a run cannot be understood later from the spec and record together, the
contract is too thin.

### Rule 9: defaults that belong to one algorithm family should live in its option type

`AlgSpec` should stay generic. Default interpretation rules for one algorithm
family should live in a concrete `AlgOption` subtype rather than being pushed
into the generic contract.

For dispatch-style algorithms, the current defaults are:

- `job_sequence` omitted means the instance job list order is used,
- `job_2_release_t` omitted means every job has release time `0`,
- EDD, LSL, OSL, and similar sequence-generation rules remain separate features
  rather than implicit option behavior.
- decoder algorithms such as FAM may derive stage-specific internal job orders
  from completion times and due-window data, while keeping only the initial
  permutation in the concrete option type.

This keeps the base contract reusable while still allowing practical defaults in
concrete option types.

## AlgRecord Rules

### Rule 10: `AlgRecord` is the immutable result of one run

`AlgRecord` should be treated as a stable execution record for one completed or
failed run.

Once produced, it should not be edited in place to add meaning that was missing
from the original execution.

Reporter-side derived metrics belong in reporting artifacts, not back in the
record.

### Rule 11: `AlgRecord` must be self-describing enough for downstream use

`AlgRecord` should contain the information needed for later reporting without
requiring hidden context from the execution process.

Expected contents include:

- `work_status`,
- optional `instance_id`,
- optional `algorithm_id`,
- optional `option`,
- optional result payload such as `AlgResult`,
- optional timing,
- optional progress log,
- optional `termination_reason`,
- optional `error`.

Requiredness should stay minimal:

- `work_status` is required,
- other `AlgRecord` fields are optional.

The important property is not the exact field container yet, but the contract:

- downstream code should be able to understand what happened,
- the option used should be visible from the record,
- failures should be explicit rather than implied by missing files.

### Rule 12: keep primary objective fields separate from auxiliary metrics

If an `AlgRecord` contains a structured result payload such as `AlgResult`,
distinguish primary objective fields from auxiliary metrics.

Preferred shape:

- `obj_value`: the primary objective scalar for the run,
- `obj_bound`: a bound in the same objective space when applicable,
- `metrics`: other named scalars that are useful but are not the primary
  objective field.

Do not use `metrics` as a dumping ground for duplicates of `obj_value` or
`obj_bound`.

Useful examples of `metrics`:

- `makespan`,
- `sum_earliness`,
- `sum_tardiness`,
- `late_job_count`,
- `gap_percent`.

### Rule 13: keep status semantics separated

`work_status` and `termination_reason` carry different meanings and should stay
separate.

Examples:

- `work_status` describes the solution state, such as feasible or optimal,
- `termination_reason` describes why the run stopped, such as time limit or
  no-improvement.

Do not collapse both ideas into one loosely defined status string.

That separation keeps reporting logic cleaner and prevents ambiguous analysis
later.

### Rule 14: progress data should reflect execution, not presentation

Progress information in `AlgRecord` is execution evidence.

It should be structured so later tools can analyze it, not formatted for human
presentation first.

Useful principles:

- record elapsed time relative to execution start,
- record objective values in machine-readable fields,
- use optional notes only for supplemental context,
- avoid free-form console text as the primary progress representation.

### Rule 15: errors belong in the record when a run is captured

If orchestration chooses to preserve failed runs instead of aborting
immediately, the failure should appear explicitly in `AlgRecord`.

That means:

- error presence should be machine-detectable,
- traceback or equivalent diagnostics should be preserved,
- a failed run should still remain attributable to the original spec.

Silent failure or side-log-only failure makes experiments harder to audit.

## Import And Dependency Rules

### Rule 16: algorithm code depends on stable domain inputs

Algorithm implementations may depend on domain packages that define the problem
instance, option objects, and solution objects.

They should not depend on future orchestration or reporting layers just to
satisfy the execution contract.

Preferred dependency direction:

- parameters and solution objects may flow into `Algorithm`,
- `Algorithm` produces `AlgRecord`,
- reporting depends on `AlgRecord`,
- not the reverse.

### Rule 17: outside code should prefer the public algorithm API

When this contract becomes code, outside packages should prefer imports from
the public algorithm package surface, such as `ffc_ddw_sum_et.algorithm`,
instead of reaching into deep internal modules.

This keeps future refactors possible without forcing repository-wide import
rewrites.

### Rule 18: do not let reporting concerns leak into algorithm internals

Avoid adding fields, callbacks, or dependencies to algorithm code just because
they make one report easier to build.

If reporting needs more information, prefer:

- enriching `AlgRecord` as part of the execution contract,
- or documenting a new contract field explicitly.

Do not couple algorithm execution to a specific reporter implementation.

## What This Means For Future Changes

When editing or adding algorithm-facing code:

- keep the callable contract centered on `AlgSpec -> AlgRecord`,
- prefer protocol-style interfaces over inheritance-heavy frameworks,
- keep execution-time dependencies explicit in `AlgSpec`,
- keep downstream evidence explicit in `AlgRecord`,
- keep filesystem knowledge limited to `alg_root`,
- avoid introducing `Launcher` or `Reporter` assumptions early.

When editing or adding adjacent domain code:

- problem-instance semantics belong with parameter and domain objects,
- solution semantics belong with solution objects,
- experiment orchestration does not belong inside algorithm implementations,
- report-specific aggregation does not belong inside execution records.

## Migration Heuristic

If a future change makes you ask one of these questions, stop and review the
boundary first:

- "Why does this algorithm need to know about other runs?"
- "Why is this value required at runtime but absent from `AlgSpec`?"
- "Why does this report need to call back into algorithm code?"
- "Why is this execution result understandable only with external logs?"
- "Would this run still be reproducible if all I kept were the spec and the
  record?"

If the answer is weak, the contract is probably drifting.
