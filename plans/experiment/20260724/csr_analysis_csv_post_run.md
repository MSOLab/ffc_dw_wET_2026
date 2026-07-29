# Plan — per-scenario CSR analysis CSV (post-run artifact)

**Date:** 2026-07-24
**Type:** code change (new reporter artifact) — experiment plan, written *before* the work.

## 1. Goal

Emit a **per-scenario** CSV, `<scenario_name>_csr_analysis.csv`, at the **same
level** as `<scenario_name>_statistics.yaml` (inside each scenario directory of a
run). One row per instance. It records, for the *best* CSR candidate, the
coarse-scale objective and the restored objective, alongside the standard
`analysis_long` identity/timing columns.

This is the standing, reporter-produced version of the ad-hoc script
`scripts/20260724/coarse_vs_restored.py` (which averages the same winner pair per
`(kappa, TL)` cell). The script stays for cell-level rollups; the new CSV is the
per-instance source it (and future analyses) can read.

### 1.1 Output spec (frozen)

Columns, in order — the `analysis_long` sheet's `insIndex`‥`time%` prefix, then
two CSR columns:

```
insIndex, n, c, totalMcCount, T, R, W, TL, elapsedSec, time%, objValueC, objValueR
```

| column | source |
|---|---|
| `insIndex` | `_resolve_ins_index(ir.instance_name)` (raw int, matching the xlsx `analysis_long` sheet; empty when unresolved) |
| `n, c, totalMcCount, T, R, W` | `_index_to_meta[ins_index]` (PRA2017 instance table) |
| `TL` | `TIMELIMIT_NC_MULTIPLIER * ir.job_count * ir.stage_count` (= `0.09·n·c`) |
| `elapsedSec` | `round(ir.elapsed_time, 3)` |
| `time%` | `elapsedSec / TL * 100` |
| `objValueC` | **coarse** objective of the winning candidate (`coarse_obj`) |
| `objValueR` | **restored** objective of the winning candidate (`restored_obj`) |

- **Winner** = the *valid* candidate row with the **minimum `restored_obj`** in
  `progress/<ins>_csr_candidates.csv`. That row's `restored_obj` is what became
  the incumbent, so `(coarse_obj, restored_obj)` = `(objValueC, objValueR)`.
  This is exactly `coarse_vs_restored.winner()` today.
- No `scenarioName` column: the file lives inside the scenario dir, so it is
  implicit (matches the user's "insIndex~time%" range).
- A row is emitted **only** when that instance has a `*_csr_candidates.csv` on
  disk. A scenario with no such file (non-CSR scenario) produces **no** CSV —
  mirrors `_write_mcf_lb_analysis_csv`.

### 1.2 Why read the on-disk candidates CSV (not in-memory)

Candidate rows are **not** carried on `InstanceResult` — they are written per
instance by `FFcDDWSingleInstanceRunner._emit_csr_artifacts` to
`progress/<ins>_csr_candidates.csv`. Reading them back from disk makes the new
writer work identically under `FULL_RUN` **and** `POST_PROCESS_ONLY` (the reporter
only reconstructs `InstanceResult`s in the latter, never the candidate rows), and
is the same source the existing script uses.

## 2. Design & data flow

```
progress/<ins>_csr_candidates.csv  ──read_csr_winner()──▶ (coarse_obj, restored_obj)
                                                                │
InstanceResult (insIndex, n..W, TL, elapsed)  ──────────────────┤
                                                                ▼
                             <scenario>_csr_analysis.csv  (one row / instance)
```

**Single source of truth for the winner rule:** a new pure helper
`read_csr_winner()` in `src/ffc_ddw_sum_et/report/csr_candidate_analysis.py`.
Both the reporter and `coarse_vs_restored.py` import it — the selection logic
must exist in exactly one place (DRY).

Hook point: a new `FFcDDWReporter._write_csr_analysis_csv()`, called from
`generate()` right after `_write_mcf_lb_analysis_csv()` (reporting.py:749).
New artifact `kind: csr_analysis` registered in the layout template.

## 3. Per-file work breakdown (one Sonnet subagent per file)

All contracts below are **frozen** — every agent codes against this spec, so the
files can be built against each other without waiting on the actual code. Global
rules for **every** subagent:

- **Do NOT run any `git` command** (no add/commit/checkout/stash). This is a
  shared worktree; a stray checkout has destroyed uncommitted work before.
- Edit **only** the file(s) named in your card. Do not touch others.
- After editing, run `uv run ruff check <your file>` and the verification in your
  card. Report the exact command output back.
- `uv run` for all Python. Do not reformat unrelated code.

### Wave 1 (parallel — no cross-deps)

---

#### File A — `src/ffc_ddw_sum_et/report/csr_candidate_analysis.py` **(NEW)**

The pure winner helper. **Full contents to create:**

```python
"""Winner extraction for CSR (coarsen_solve_reconstruct) candidate CSVs.

Single source of truth for the rule shared by the post-run reporter
(``FFcDDWReporter._write_csr_analysis_csv``) and the ad-hoc analysis script
``scripts/20260724/coarse_vs_restored.py``.
"""

from __future__ import annotations

import csv
from pathlib import Path


def read_csr_winner(candidates_csv: str | Path) -> tuple[float, float] | None:
    """Return ``(coarse_obj, restored_obj)`` of the winning candidate.

    The winner is the *valid* row (``valid == "True"`` and a non-empty
    ``restored_obj``) with the minimum ``restored_obj`` — that row's
    ``restored_obj`` is what becomes the incumbent. Returns ``None`` when the
    file holds no valid candidate. ``coarse_obj`` is ``float('nan')`` when that
    field is blank on the winning row.
    """
    best: tuple[float, float] | None = None
    with open(candidates_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("valid") != "True" or not row.get("restored_obj"):
                continue
            r = float(row["restored_obj"])
            c = float(row["coarse_obj"]) if row.get("coarse_obj") else float("nan")
            if best is None or r < best[1]:
                best = (c, r)
    return best
```

- This is byte-for-byte the current `coarse_vs_restored.winner()` logic (verify
  against `scripts/20260724/coarse_vs_restored.py:33-44`) — behavior must not
  change.
- **Verify:** `uv run ruff check src/ffc_ddw_sum_et/report/csr_candidate_analysis.py`,
  then a quick REPL sanity read of one real file:
  `uv run python -c "from ffc_ddw_sum_et.report.csr_candidate_analysis import read_csr_winner; print(read_csr_winner('output/20260724_merge_recon_ab_vs_prior/20260724T073347_605861/csr_k1_tl05_semi/Instance_100_10_3_0,2_0,2_10_Rep0/progress/Instance_100_10_3_0,2_0,2_10_Rep0_csr_candidates.csv'))"`
  — expect `(51882.0, 51882.0)` (min-restored valid row from that file).

---

#### File B — `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml` (+ doc note)

Register the new scenario-scoped artifact. Insert **immediately after** the
`mcf_lb_analysis` entry (after line 157):

```yaml
  # Per-scenario CSR winner table — one row per instance, columns
  # insIndex..time% (as in the analysis_long sheet) plus objValueC / objValueR,
  # the coarse-scale and restored objective of the best CSR candidate (min
  # restored_obj valid row of that instance's csr_candidates_csv). Written only
  # for scenarios that ran coarsen_solve_reconstruct.
  - scope: scenario
    kind: csr_analysis
    file_template: "{scenario_name}_csr_analysis.csv"
```

Also: in `docs/io/20260429_artifact_manager.md`, add a `<scenario_name>_csr_analysis.csv`
line next to the existing `<scenario_name>_mcf_lb_analysis.csv` in the tree at
line ~60, and (if a `kind:` list is present around line 176+) a matching
`kind: csr_analysis` doc row. Keep it terse, one line each.

- **Verify:** `uv run python -c "import yaml; yaml.safe_load(open('metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml'))"` parses;
  confirm the new `kind: csr_analysis` block is well-formed and scope is
  `scenario` (no `zone:` — scenario/run scope forbids zone, see the file header).

### Wave 2 (parallel — both import File A; start after A exists)

---

#### File C — `src/ffc_ddw_sum_et/orchestration/reporting.py`

Three edits inside `class FFcDDWReporter`:

1. **Wire into `generate()`** — after `self._write_mcf_lb_analysis_csv()`
   (line 749) add:
   ```python
           self._write_csr_analysis_csv()
   ```

2. **Column constant** — near `_MCF_LB_ANALYSIS_COLUMNS` (line 940) add:
   ```python
       _CSR_ANALYSIS_COLUMNS: tuple[str, ...] = (
           "insIndex",
           "n",
           "c",
           "totalMcCount",
           "T",
           "R",
           "W",
           "TL",
           "elapsedSec",
           "time%",
           "objValueC",
           "objValueR",
       )
   ```

3. **Writer + row builder** — add near `_write_mcf_lb_analysis_csv`
   (after line 1298). Reuse the module-level `_s` helper and
   `TIMELIMIT_NC_MULTIPLIER` already in this file, and `_resolve_ins_index` /
   `_index_to_meta` already on the class:

   ```python
       def _write_csr_analysis_csv(self) -> None:
           """Per-scenario CSR winner table, one row per instance.

           Columns mirror the ``analysis_long`` sheet's ``insIndex``..``time%``
           prefix, then ``objValueC`` / ``objValueR`` — the coarse-scale and
           restored objective of the best CSR candidate (min ``restored_obj``
           valid row of that instance's ``csr_candidates_csv``). A scenario with
           no candidate CSV on disk (non-CSR scenario) is skipped. Rows are
           sorted by ``insIndex``.
           """
           from ..report.csr_candidate_analysis import read_csr_winner

           for sc in self.scenario_results:
               collected: list[tuple[int, list[str]]] = []
               for ir in sc.instance_results:
                   cand_csv = self.layout.artifact_path(
                       "csr_candidates_csv",
                       scenario_name=sc.name,
                       instance_name=ir.instance_name,
                   )
                   if not cand_csv.exists():
                       continue
                   winner = read_csr_winner(cand_csv)
                   idx = self._resolve_ins_index(ir.instance_name)
                   collected.append(
                       (idx if idx is not None else 10**9,
                        self._csr_analysis_row(ir, winner))
                   )
               if not collected:
                   continue
               collected.sort(key=lambda t: t[0])
               path = self.layout.artifact_path("csr_analysis", scenario_name=sc.name)
               with open(path, "w", encoding="utf-8", newline="") as f:
                   writer = csv.writer(f)
                   writer.writerow(self._CSR_ANALYSIS_COLUMNS)
                   for _, row in collected:
                       writer.writerow(row)
               logger.info("CSR analysis CSV written to %s", path)

       def _csr_analysis_row(
           self, ir: InstanceResult, winner: tuple[float, float] | None
       ) -> list[str]:
           ins_index = self._resolve_ins_index(ir.instance_name)
           meta = (
               self._index_to_meta.get(ins_index, {})
               if ins_index is not None
               else {}
           )
           tl = None
           if ir.job_count is not None and ir.stage_count is not None:
               tl = TIMELIMIT_NC_MULTIPLIER * ir.job_count * ir.stage_count
           elapsed = round(ir.elapsed_time, 3) if ir.elapsed_time is not None else None
           time_pct = (elapsed / tl * 100) if (tl and elapsed is not None) else None
           coarse, restored = winner if winner is not None else (None, None)
           values: dict[str, Any] = {
               "insIndex": ins_index if ins_index is not None else "",
               "n": meta.get("n"),
               "c": meta.get("c"),
               "totalMcCount": meta.get("totalMcCount"),
               "T": meta.get("T"),
               "R": meta.get("R"),
               "W": meta.get("W"),
               "TL": tl,
               "elapsedSec": elapsed,
               "time%": time_pct,
               "objValueC": coarse,
               "objValueR": restored,
           }
           return [_s(values[col]) for col in self._CSR_ANALYSIS_COLUMNS]
   ```

   - Confirm `InstanceResult` exposes `job_count` / `stage_count` /
     `elapsed_time` (the `analysis_long` builder at reporting.py:2197-2205 uses
     these same fields — copy its exact attribute names). If a name differs, use
     whatever `analysis_long` uses.
   - Decision to keep: when the winner's `coarse_obj` is blank, `_s(nan)` yields
     `"nan"`. This is acceptable (rare); do **not** add special-casing.
- **Verify:** `uv run ruff check src/ffc_ddw_sum_et/orchestration/reporting.py`
  and the File E tests once they exist (`uv run pytest tests/orchestration/test_csr_analysis_csv.py`).

---

#### File D — `scripts/20260724/coarse_vs_restored.py`

Refactor to consume File A (DRY) with **zero behavior change**:

- Delete the local `winner()` function (lines 33-44).
- Add near the imports:
  `from ffc_ddw_sum_et.report.csr_candidate_analysis import read_csr_winner`
- In `cell_stats`, replace `w = winner(cands[0])` with
  `w = read_csr_winner(cands[0])`.
- Remove the now-unused `import csv`. Keep `glob`, `re`, `mean`, etc.
- Update the module docstring's "takes the WINNER row" sentence only if wording
  drifts; the semantics are unchanged.
- **Verify:** `uv run ruff check scripts/20260724/coarse_vs_restored.py`, then run
  it on the run in the ticket and confirm the table still prints identically to a
  pre-refactor run:
  `uv run python scripts/20260724/coarse_vs_restored.py output/20260724_merge_recon_ab_vs_prior/20260724T073347_605861 --cells k1_tl05`
  (should print a `k1_tl05` row with finite coarse/restored means).

### Wave 3 (after A + C)

---

#### File E — `tests/orchestration/test_csr_analysis_csv.py` **(NEW)**

Mirror the fixtures in `tests/orchestration/test_reporting.py` (`_layout`,
`_make_instance_result`, `ScenarioResult`, `FFcDDWReporter`) and
`tests/orchestration/test_csr_artifact_emit.py` (layout construction).

Cover:

1. **`read_csr_winner`** (pure): a fabricated CSV string with (a) an invalid row
   (`valid=False`), (b) a valid row with blank `restored_obj`, (c) two valid rows
   → asserts the min-`restored_obj` valid row wins and its `coarse_obj` comes
   back; an all-invalid file → `None`; a valid winner with blank `coarse_obj`
   → `math.isnan(coarse)`.
2. **`_write_csr_analysis_csv`** (integration, tmp layout): build a
   `ScenarioResult` with 2 `InstanceResult`s; write a
   `progress/<ins>_csr_candidates.csv` at
   `layout.artifact_path("csr_candidates_csv", scenario_name=..., instance_name=...)`
   for one instance only. Call the writer. Assert:
   - the output at `layout.artifact_path("csr_analysis", scenario_name=...)`
     exists, header == `_CSR_ANALYSIS_COLUMNS`, exactly **one** data row (the
     instance with a candidates CSV), and its `objValueC`/`objValueR` equal the
     winner, `time%` == `elapsedSec/TL*100`.
   - a scenario whose instances have **no** candidates CSV → **no** output file.
- **Verify:** `uv run pytest tests/orchestration/test_csr_analysis_csv.py -q` green;
  `uv run ruff check tests/orchestration/test_csr_analysis_csv.py`.

## 4. End-to-end verification (after all waves, run by the orchestrator — not a subagent)

Full suite + regenerate against the ticket's real run to eyeball one file:

```bash
uv run ruff check
uv run pytest tests/orchestration/ -q
```

Optional real-data regeneration via `POST_PROCESS_ONLY` on the merged run
(`output/20260724_merge_recon_ab_vs_prior/20260724T073347_605861`) — keep
`draw_gantt: false` and `draw_progress_plot: false` (the dir is symlink-merged;
painters would reach through symlinks into source runs). Then confirm
`csr_k1_tl05_semi/csr_k1_tl05_semi_csr_analysis.csv` exists with the 12 columns
and that its `objValueC/objValueR` for `Instance_100_10_3_0,2_0,2_10_Rep0` ==
`(51882.0, 51882.0)`, matching File A's REPL check.

## 5. Orchestration summary

| wave | files (parallel) | depends on |
|---|---|---|
| 1 | A (new helper), B (layout yaml + doc) | — |
| 2 | C (reporting.py), D (script refactor) | A |
| 3 | E (new tests) | A, C |

Each file → one Sonnet subagent. Waves are barriers only for the import
dependency; within a wave the files are disjoint and run concurrently. No agent
runs git; the orchestrator reviews all diffs and commits once at the end.
