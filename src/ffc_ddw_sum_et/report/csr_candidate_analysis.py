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
