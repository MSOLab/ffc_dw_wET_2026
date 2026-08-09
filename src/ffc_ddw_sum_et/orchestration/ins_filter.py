"""Resolve the instance-index selection from an experiment config.

Public contract
---------------

``resolve_ins_index(config)`` reads ``ins_index`` / ``ins_filter`` /
``bks_table_csv_path`` from the config dict and returns the value to pass to
``BenchmarkLoader.load_all(ins_index=...)``.

Return values::

    neither key present   -> None                (loader globs every .txt file)
    only ``ins_index``    -> that value verbatim (existing behaviour)
    only ``ins_filter``   -> ascending list[int] of matching insIndex

The ``ins_filter`` path never returns an empty list -- zero matches raise.

Errors -- all ``ValueError``, raised before any instance is loaded::

    both ins_index and ins_filter    -> message names both keys
    ins_filter, no bks_table_csv_path-> message names the missing key
    bks_table_csv_path not a file    -> message carries the path
    ins_filter is not a mapping      -> message carries the actual type
    unknown filter column (typo)     -> message lists FILTERABLE_COLUMNS
    column absent from the CSV       -> message names the column and the path
    filter value not float()-able    -> message carries the offending value
    zero matches                     -> message lists the values the CSV holds
                                        for each filtered column

Filter semantics
----------------

- A scalar value means equality; a list value means membership (OR).
- Multiple keys are combined with AND.
- Filter values and CSV cells are both normalised through ``float()`` before
  comparison, so ``T: 0.6`` matches ``"0.6"``, ``R: 1`` matches ``"1.0"``, and
  ``n: 50`` matches ``"50"``. Every filterable column is numeric.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

FILTERABLE_COLUMNS = ("n", "c", "totalMcCount", "T", "R", "W")

_INDEX_COLUMN = "insIndex"


def resolve_ins_index(config: dict[str, Any]) -> int | list[int] | None:
    """Return the ``insIndex`` selection described by ``config``.

    See the module docstring for the full contract.
    """
    ins_index = config.get("ins_index")
    ins_filter = config.get("ins_filter")

    if ins_index is not None and ins_filter is not None:
        raise ValueError(
            "ins_index and ins_filter are both specified; use only one of them"
        )
    if ins_index is not None:
        return ins_index
    if ins_filter is None:
        return None

    # Validate the expression itself before touching the filesystem, so a typo
    # is reported as a typo even when the CSV path is also wrong.
    criteria = _normalize_filter(ins_filter)
    bks_path = _resolve_bks_path(config)
    matched, observed = _match_rows(bks_path, criteria)
    if not matched:
        raise ValueError(_no_match_message(ins_filter, observed))
    return matched


def _resolve_bks_path(config: dict[str, Any]) -> Path:
    raw = config.get("bks_table_csv_path")
    if raw is None:
        raise ValueError("ins_filter requires bks_table_csv_path to be set")
    path = Path(raw)
    if not path.is_file():
        raise ValueError(f"bks_table_csv_path file not found: {path}")
    return path


def _normalize_filter(ins_filter: Any) -> dict[str, set[float]]:
    """Validate the filter and turn every value into a set of floats.

    Normalising up front makes the "not float()-able" error independent of the
    CSV contents -- it fires even when no row would have been examined.
    """
    if not isinstance(ins_filter, dict):
        raise ValueError(
            f"ins_filter must be a mapping, got {type(ins_filter).__name__}"
        )

    criteria: dict[str, set[float]] = {}
    for column, wanted in ins_filter.items():
        if column not in FILTERABLE_COLUMNS:
            raise ValueError(
                f"Unknown filter column {column!r}. "
                f"Known columns: {', '.join(FILTERABLE_COLUMNS)}"
            )
        values = wanted if isinstance(wanted, list) else [wanted]
        criteria[column] = {_to_float(value, column) for value in values}
    return criteria


def _to_float(value: Any, column: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"Cannot convert {column} filter value {value!r} to float"
        ) from None


def _match_rows(
    path: Path, criteria: dict[str, set[float]]
) -> tuple[list[int], dict[str, set[float]]]:
    """Return the matching ``insIndex`` (ascending) and the values seen per column.

    Only the filtered columns are parsed, so an unrelated malformed column in
    the CSV cannot fail a run.
    """
    matched: list[int] = []
    observed: dict[str, set[float]] = {column: set() for column in criteria}

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        _check_header(path, reader.fieldnames, criteria)
        for line in reader:
            try:
                row = {column: float(line[column]) for column in criteria}
                ins_index = int(line[_INDEX_COLUMN])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Malformed value in {path} (line {reader.line_num}): {exc}"
                ) from None
            for column, value in row.items():
                observed[column].add(value)
            if all(row[column] in wanted for column, wanted in criteria.items()):
                matched.append(ins_index)

    return sorted(matched), observed


def _check_header(
    path: Path, fieldnames: list[str] | None, criteria: dict[str, set[float]]
) -> None:
    present = set(fieldnames or ())
    for column in (_INDEX_COLUMN, *criteria):
        if column not in present:
            raise ValueError(f"Column {column!r} is absent from {path}")


def _no_match_message(
    ins_filter: dict[str, Any], observed: dict[str, set[float]]
) -> str:
    lines = [f"ins_filter matched zero instances. Filter: {ins_filter}."]
    lines.append("Values present in the CSV for each filtered column:")
    for column, values in observed.items():
        lines.append(f"  {column}: {sorted(values)}")
    return "\n".join(lines)
