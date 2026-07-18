# `scripts/validate_resume_config.py` — review follow-ups

Review of the staged `scripts/validate_resume_config.py` raised 4 warnings.
This plan records the verdict and the fix for each, so the reasoning isn't
re-derived at commit time.

Summary: two are real and cheap to fix (#3 test, #4 name shadow), one is a
staging step with no code change (#2), and one is an accepted coupling that
already has precedent in this repo (#1).

---

## Findings verified against the codebase

Before planning, three claims in the review were checked:

- **`metadata/` is tracked, not ignored.** `git check-ignore` returns
  non-zero for `metadata/20260710/sw_cp_tl_kappa_0.005.yaml`, and the last
  three commits touching `metadata/` are config commits ("… run setting").
  So the docstring example can be made valid simply by staging the file.
- **Importing `main`'s private helpers already has precedent.**
  `tests/test_scenario_uniqueness.py` imports `main._validate_scenario_uniqueness`
  directly. The new script is not introducing a new pattern.
- **Testing a `scripts/` module already has precedent.**
  `tests/scripts/test_analyze_dispatch_sweep.py` loads its target by path with
  `importlib.util.spec_from_file_location`, because `scripts/` is not an
  importable package. That is the template for the new test.

---

## 1. Private-helper import — accept, and document the coupling

`_load_config`, `_parse_run_mode`, `_resolve_resume_dir`, and
`_validate_scenario_uniqueness` are imported from `main`.

**Verdict: accept.** Promoting four names to a public API of `main.py` would be
speculative — nothing outside this script and one existing test consumes them,
and there is no third caller on the horizon (YAGNI). Re-implementing them in the
script would violate DRY and defeat the script's entire purpose, which is to
answer "will `main.main()` resume where I think it will?" using the same code
paths `main.main()` uses. Duplicated validation logic that silently drifts from
the entrypoint is a worse failure than an underscore import.

The review's actual concern — "a `main.py` refactor could silently break this" —
is a *testing* problem, not an API-surface problem, and item #3 fixes it: once
the script has a test, renaming any of the four helpers turns a silent runtime
breakage into a red test.

Two things to do:

- Rewrite the import so the coupling is visible at every call site (see #4 —
  the same edit resolves both).
- Append a `TODO.md` entry recording the deferral, per the repo's
  Deferred Design Notes convention.

Proposed `TODO.md` entry:

> **Promote `main.py` config helpers to a public API**
> `scripts/validate_resume_config.py` and `tests/test_scenario_uniqueness.py`
> both reach into `main._load_config` / `_parse_run_mode` / `_resolve_resume_dir`
> / `_validate_scenario_uniqueness`.
> **Why deferred:** two in-repo consumers, both covered by tests that fail loudly
> on rename. Extracting a `config_resolution` module today buys nothing.
> **When to act:** when a third consumer appears, or when any of the four grows
> logic that a caller wants to override rather than reuse.

## 2. Docstring points at an untracked file — stage the file

The usage example names `metadata/20260710/sw_cp_tl_kappa_0.005.yaml`, which is
currently untracked.

**Fix:** the user has confirmed this file goes into the same commit.
`git add metadata/20260710/` before committing, so checking out this commit alone
leaves the docstring example runnable. No code change.

Also stage the `main.py` `CONFIG_PATH` switch, which points at the same config
and is currently unstaged — otherwise the commit is internally inconsistent about
which config exists.

## 3. No tests — add `tests/scripts/test_validate_resume_config.py`

The gap is real, and the "scripts/ have no tests" defence is not fully true:
`tests/scripts/test_analyze_dispatch_sweep.py` exists precisely because that
script's arithmetic feeds the paper. This script's output gates whether a
multi-hour experiment resumes correctly, which is the same class of stake.

Load the module by path, matching the existing test:

```python
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_resume_config.py"
_spec = importlib.util.spec_from_file_location("validate_resume_config", _SCRIPT)
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)
```

Note this import also executes the module's `sys.path.insert` and
`from main import …`, so the test transitively pins the four helper names —
that is the guard #1 relies on.

Cover the four behaviours whose regressions would be silent, written as
Given/When/Then per the repo's BDD convention. Skip the two that only exercise
`argparse` or `BenchmarkLoader`:

| Test | Given | Then |
|---|---|---|
| `test_non_resume_config_returns_ok` | a `FULL_RUN` config | exits 0, prints nothing about resume |
| `test_no_steps_guard_fails` | a scenario flow fully covered by the base flow (`idx >= step_cnt`) | exits 1, message names `resume_dir` |
| `test_prefix_mismatch_prints_only_differing_keys` | a `ValueError(dict)` payload with one differing key among many | only that key is printed |
| `test_check_artifacts_reports_missing` | a `resume_dir` missing one instance's `_solution.json` | that instance name is returned |

The first two drive `V.main()` end-to-end against a `tmp_path` config +
fake `resume_dir` (a `subroutine_flow.yaml` and per-instance dirs), with
`monkeypatch.setattr(sys, "argv", …)`. The last two call `_print_prefix_mismatch`
(via `capsys`) and `_check_artifacts` directly.

Write each test red first, per the repo's TDD convention — the `idx >= step_cnt`
guard in particular should be confirmed to fail before the guard is trusted.

## 4. Name shadow — alias the module instead of renaming `main()`

`from main import _load_config, …` at module top, then `def main()` locally.

**Do not rename the local `main()`.** 16 of the ~25 scripts in `scripts/` define
`def main()`; renaming it would break the convention to fix a cosmetic issue.

**Fix the import side instead** — bind the module under an unambiguous alias:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as entrypoint  # noqa: E402
```

and call `entrypoint._load_config(args.config)`, `entrypoint._parse_run_mode(...)`,
`entrypoint._resolve_resume_dir(...)`, `entrypoint._validate_scenario_uniqueness(...)`.

This is the better edit for three reasons: the local `def main()` no longer sits
next to a same-named import; every use of a private helper is now visibly
attributed to `entrypoint`, which is exactly the coupling #1 wants readers to
notice; and it collapses four `noqa: E402` imports into one.

Keep the existing docstring sentence ("All three helpers are imported from
`main` rather than re-implemented, so this stays in sync with the real
entrypoint") — but correct "three" to "four", since four are imported.

---

## Execution order

1. Rewrite the import as `import main as entrypoint`, update the four call
   sites, fix the "three helpers" → "four helpers" docstring count. (#4, #1)
2. `uv run ruff check` + `uv run ruff format`.
3. Write `tests/scripts/test_validate_resume_config.py` red → green. (#3)
4. Append the `TODO.md` deferral entry. (#1)
5. `git add metadata/20260710/ main.py` alongside the script. (#2)
6. Commit — suggested subject (49 chars incl. prefix):
   `feat(scripts): add resume config dry-run validator`

Steps 1 and 3 are the only ones that touch behaviour, and neither changes what
the script does — only how it is imported and whether regressions are caught.
