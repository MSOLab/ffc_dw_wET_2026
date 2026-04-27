# Repository Guidance

This repository keeps long-lived coding rules in Markdown so future conversations
can pick up the same architectural intent.

## Working conventions

- Prefer `uv run ...` for Python execution.
  - Use `uv run python` instead of `python3` or just `python`.
- Run `uv run ruff check` after code changes.
- Run `uv run ruff format` when formatting is needed.

## Architecture Docs

- **Problem definition** (parameters, variables, constraints, objective):
  `docs/problem-description.md`
- IO extraction and import rules:
  `docs/io-principles.md`
- Algorithm execution contract rules:
  `docs/algorithm-principles.md`

## Working Agreement

- Before any domain-level work (objective, scheduling logic, algorithm design),
  read `docs/problem-description.md` to understand the main problem and confirm
  symbol usage.
- If a change touches `src/ffc_ddw_sum_et/io/` or code that imports from it,
  read `docs/architecture/io-principles.md` first.
- If a change touches `src/ffc_ddw_sum_et/algorithm/` or code that imports from
  it, read `docs/architecture/algorithm-principles.md` first.
- Treat the IO subtree as an extractable package candidate. Avoid introducing
  new dependencies from `io` into parent or sibling domain packages.
- Treat the algorithm boundary as a stable execution contract candidate. Avoid
  introducing `Launcher`, `Reporter`, or report-orchestration concerns into
  `Algorithm`, `AlgSpec`, or `AlgRecord` code before those contracts are
  defined.
- Prefer changing public imports through `ffc_ddw_sum_et.io` instead of
  importing deep internal modules from outside the IO subtree.
- Prefer changing public imports through `ffc_ddw_sum_et.algorithm` instead of
  importing deep internal modules from outside the algorithm subtree.

## Deferred Design Notes

- `docs/TODO.md` collects refactor ideas that are deliberately deferred
  (YAGNI today but worth capturing so the reasoning isn't re-derived).
- Before proposing a refactor, check `docs/TODO.md` to see if it has
  already been considered — respect the "When to act" condition.
- When a design idea is agreed to be deferred rather than acted on,
  append it to `docs/TODO.md` with **Why** and **When to act** fields.
- Do not execute TODO items autonomously — they are deferred by intent.
