# Repository Guidance

This repository keeps long-lived coding rules in Markdown so future conversations
can pick up the same architectural intent.

## Working conventions

- Prefer `uv run ...` for Python execution.
  - Use `uv run python` instead of `python3` or just `python`.
- Run `uv run ruff check` after code changes.
- Run `uv run ruff format` when formatting is needed.

## Architecture Docs

- IO extraction and import rules:
  `docs/io-principles.md`
- Algorithm execution contract rules:
  `docs/algorithm-principles.md`

## Working Agreement

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
