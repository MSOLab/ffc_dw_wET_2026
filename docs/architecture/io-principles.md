# IO Package Extraction Principles

This document defines the import and typing rules for the current
`ffc_ddw_sum_et.io` subtree so it can later be extracted into a standalone
package such as distribution `aladin-io` with import path `aladin.io`.

## Goal

We want the current IO code to behave like an extractable package candidate,
even before it is split out of this repository.

That means:

- the IO subtree should have a clear dependency boundary,
- its typing helpers should have clear ownership,
- outside code should depend on its public API, and
- future extraction should require as little rewriting as possible.

## Import Rules

### Rule 1: `io` is a low-level layer

Inside `src/ffc_ddw_sum_et/io/`, imports should only point to:

- Python standard library modules,
- third-party packages that are truly IO-related, such as `pandas`,
- other modules inside `src/ffc_ddw_sum_et/io/`.

Do not introduce new imports from `io` into:

- `ffc_ddw_sum_et.type_defs`,
- `ffc_ddw_sum_et.parameters`,
- `ffc_ddw_sum_et.solution`,
- `ffc_ddw_sum_et.algorithm`,
- any other parent or sibling domain package.

If `io` needs a concept from another layer, that is a design smell. Prefer
moving the concept downward, redefining it locally, or introducing a protocol
at the boundary.

### Rule 2: outside code imports the public IO API

Code outside the IO subtree should prefer:

```python
from ffc_ddw_sum_et.io import TextDataParser, DfManager, Table2DManager
```

Avoid deep imports such as:

```python
from ffc_ddw_sum_et.io.text_data_parser import TextDataParser
```

This keeps the external dependency surface stable and makes extraction or
internal refactors easier.

### Rule 3: use relative imports inside the IO subtree

Inside `src/ffc_ddw_sum_et/io/`, prefer relative imports such as:

```python
from .text_data_parser import TextDataParser
from .typing import ScalarT
```

This keeps the subtree self-contained and reduces future path rewrites.

## Type Ownership Rules

### Rule 4: do not use a root-level dumping-ground `type_defs.py`

A shared `type_defs.py` becomes hard to maintain because ownership is unclear.
Types should live where their meaning originates.

Use this ownership rule:

- parsing and table utility types belong to `io`,
- scheduling and domain semantics belong to domain packages,
- truly neutral cross-package types should be introduced only when there is a
  proven need across multiple packages.

### Rule 5: IO-generic typing belongs to IO

Examples of IO-owned typing:

- `ScalarT`,
- `NumericT`,
- parser-oriented `TypeVar`s,
- validation helpers such as `scalar_type_set` or `numeric_type_set`,
- `Protocol`s needed by the IO boundary.

These should live in an IO-local module such as:

```text
src/ffc_ddw_sum_et/io/typing.py
```

or directly inside the narrow module that owns them when they are not reused.

### Rule 6: domain typing belongs to the domain

Examples of domain-owned typing:

- `JobId`,
- `StageId`,
- `MachineId`,
- `Operation`,
- domain-specific mappings and records.

These should stay out of the IO subtree because they reflect scheduling
semantics rather than generic input/output concerns.

### Rule 7: keep types local until reuse is real

Default choice:

- if a type is used in one module, define it there,
- if a type is reused across several IO modules, promote it to `io/typing.py`,
- only create a wider shared typing module after multiple packages genuinely
  need the same abstraction.

Do not create a global shared typing layer too early.

## Boundary Design Rules

### Rule 8: IO should speak in generic shapes

At the IO boundary, prefer generic or widely understood shapes:

- `TextIO`,
- `PathLike`,
- `Sequence`,
- `Iterable`,
- `Mapping`,
- `pandas.DataFrame`,
- `Protocol`s for behavior.

Avoid signatures that force IO to know domain classes from the scheduling
package.

### Rule 9: parse in IO, interpret in the domain layer

The IO subtree should focus on reading, normalizing, and representing data.

The domain layer should decide what that data means.

Preferred direction:

- IO reads text or tables,
- IO returns generic values, structures, or managers,
- domain code converts those results into scheduling objects.

This keeps `aladin.io` reusable outside this specific scheduling project.

### Rule 10: prefer composition over inheritance across the boundary

If a domain object subclasses an IO helper, extraction becomes harder because
the domain layer is structurally coupled to IO internals.

When practical, prefer:

- domain objects containing IO helpers,
- domain adapters wrapping IO results,
- conversion functions at the boundary.

Inheritance is acceptable when it is clearly worth the coupling, but it should
be treated as an intentional tradeoff.

## What This Means For Future Changes

When editing or adding IO code:

- do not add new imports from `io` to parent or sibling packages,
- prefer adding new shared IO types to `io/typing.py` rather than the project
  root,
- keep runtime validation helpers near the implementation that uses them,
- export stable names from `src/ffc_ddw_sum_et/io/__init__.py`,
- keep deep module paths out of external callers when possible.

When editing or adding domain code:

- importing from `ffc_ddw_sum_et.io` is acceptable,
- importing from deep IO internals should be avoided,
- domain typing should stay in the domain layer,
- domain interpretation should not be pushed down into IO helpers unless reuse
  outside this project is still plausible.

## Migration Heuristic

If a future change makes you ask one of these questions, stop and review the
boundary first:

- "Why does IO need this domain type?"
- "Why does this generic type live in the project root?"
- "Why does outside code know this internal IO module path?"
- "Would this still make sense if `io` became `aladin.io` tomorrow?"

If the answer is weak, the boundary is probably drifting.
