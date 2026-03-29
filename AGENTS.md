# Repository Guidance

This repository keeps long-lived coding rules in Markdown so future conversations
can pick up the same architectural intent.

## Architecture Docs

- IO extraction and import rules:
  `docs/architecture/io-principles.md`

## Working Agreement

- If a change touches `src/ffc_ddw_sum_et/io/` or code that imports from it,
  read `docs/architecture/io-principles.md` first.
- Treat the IO subtree as an extractable package candidate. Avoid introducing
  new dependencies from `io` into parent or sibling domain packages.
- Prefer changing public imports through `ffc_ddw_sum_et.io` instead of
  importing deep internal modules from outside the IO subtree.
