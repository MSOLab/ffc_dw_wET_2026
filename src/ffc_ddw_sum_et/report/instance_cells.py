from __future__ import annotations

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)

CELL_DIMS = ("t_factor", "r_factor", "job_cnt", "stage_cnt")


def format_cell_value(dim: str, value: object) -> str:
    if dim in ("t_factor", "r_factor"):
        return f"{float(value):.1f}"
    return str(int(value))


def cell_key_by_instance(
    baseline_df: pd.DataFrame,
) -> dict[str, tuple[str, str, str, str]]:
    result: dict[str, tuple[str, str, str, str]] = {}
    nan_count = 0
    for _, row in baseline_df.iterrows():
        ins_id = str(row["instance_id"])
        parts: list[str] = []
        has_nan = False
        for dim in CELL_DIMS:
            val = row.get(dim)
            if val is None or (isinstance(val, float) and math.isnan(val)):
                has_nan = True
                break
            parts.append(format_cell_value(dim, val))
        if has_nan:
            nan_count += 1
            continue
        result[ins_id] = tuple(parts)
    if nan_count:
        logger.warning(
            "Excluded %d instance(s) from cell map due to NaN in cell dims",
            nan_count,
        )
    return result


def cell_dim_values(baseline_df: pd.DataFrame) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for dim in CELL_DIMS:
        raw = pd.to_numeric(baseline_df[dim], errors="coerce").dropna().unique()
        result[dim] = [format_cell_value(dim, v) for v in sorted(raw)]
    return result
