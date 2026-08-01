from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest
from routix.io import load_yaml

from ffc_ddw_sum_et.orchestration.ins_filter import (
    FILTERABLE_COLUMNS,
    resolve_ins_index,
)

BKS_COLUMNS = (
    "insIndex",
    "n",
    "c",
    "totalMcCount",
    "T",
    "R",
    "W",
    "BKS_data",
    "BKS_calc",
    "BKS_T",
    "BKS_F",
)

_DEFAULT_ROW = {
    "n": "50",
    "c": "5",
    "totalMcCount": "15",
    "T": "0.2",
    "R": "0.2",
    "W": "10",
    "BKS_data": "0",
    "BKS_calc": "0",
    "BKS_T": "0",
    "BKS_F": "0",
}


def _row(ins_index: int, **overrides: Any) -> dict[str, str]:
    """One bks_table row; only the columns a test cares about are named."""
    return {
        **_DEFAULT_ROW,
        "insIndex": str(ins_index),
        **{key: str(value) for key, value in overrides.items()},
    }


def _write_bks_csv(
    path: Path, rows: list[dict[str, str]], columns: tuple[str, ...] = BKS_COLUMNS
) -> Path:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in columns})
    return path


def _config(csv_path: Path, **ins_filter: Any) -> dict[str, Any]:
    return {"ins_filter": ins_filter, "bks_table_csv_path": str(csv_path)}


# --- selection semantics ---


def test_scalar_single_condition(tmp_path: Path) -> None:
    csv_path = _write_bks_csv(
        tmp_path / "bks.csv",
        [_row(0, T=0.2), _row(1, T=0.6), _row(2, T=0.6, W=20)],
    )
    assert resolve_ins_index(_config(csv_path, T=0.6)) == [1, 2]


def test_two_keys_are_and(tmp_path: Path) -> None:
    csv_path = _write_bks_csv(
        tmp_path / "bks.csv",
        [_row(0, T=0.2, R=0.2), _row(1, T=0.6, R=0.2), _row(2, T=0.6, R=0.6)],
    )
    assert resolve_ins_index(_config(csv_path, T=0.6, R=0.2)) == [1]


def test_list_value_is_or(tmp_path: Path) -> None:
    csv_path = _write_bks_csv(
        tmp_path / "bks.csv",
        [_row(0, n=50), _row(1, n=150), _row(2, n=200), _row(3, n=100)],
    )
    assert resolve_ins_index(_config(csv_path, n=[150, 200])) == [1, 2]


def test_value_normalization(tmp_path: Path) -> None:
    """YAML ints/floats match the CSV's string form (``R: 1`` vs ``"1.0"``)."""
    csv_path = _write_bks_csv(tmp_path / "bks.csv", [_row(0, T=0.6, R="1.0", n=50)])
    assert resolve_ins_index(_config(csv_path, T=0.6, R=1, n=50)) == [0]


def test_result_is_ascending_regardless_of_csv_order(tmp_path: Path) -> None:
    csv_path = _write_bks_csv(
        tmp_path / "bks.csv",
        [_row(7, T=0.6), _row(2, T=0.6), _row(5, T=0.6)],
    )
    assert resolve_ins_index(_config(csv_path, T=0.6)) == [2, 5, 7]


# --- passthrough / absent keys ---


def test_neither_key_returns_none() -> None:
    assert resolve_ins_index({}) is None


def test_ins_index_only_passthrough() -> None:
    assert resolve_ins_index({"ins_index": [1, 2, 3]}) == [1, 2, 3]
    assert resolve_ins_index({"ins_index": 5}) == 5


# --- errors ---


def test_both_keys_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="ins_index.*ins_filter"):
        resolve_ins_index({"ins_index": [1], "ins_filter": {"T": 0.6}})


def test_ins_filter_without_bks_path_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="bks_table_csv_path"):
        resolve_ins_index({"ins_filter": {"T": 0.6}})


def test_missing_bks_file_raises_valueerror(tmp_path: Path) -> None:
    config = {
        "ins_filter": {"T": 0.6},
        "bks_table_csv_path": str(tmp_path / "absent.csv"),
    }
    with pytest.raises(ValueError, match="file not found"):
        resolve_ins_index(config)


def test_non_mapping_filter_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="must be a mapping.*list"):
        resolve_ins_index({"ins_filter": [0.6], "bks_table_csv_path": "unused.csv"})


def test_unknown_column_raises_valueerror() -> None:
    """A typo is reported as a typo even when the CSV path is unusable."""
    config = {"ins_filter": {"t": 0.6}, "bks_table_csv_path": "absent.csv"}
    with pytest.raises(ValueError, match="Unknown filter column.*Known columns"):
        resolve_ins_index(config)


def test_column_absent_from_csv_raises_valueerror(tmp_path: Path) -> None:
    columns = tuple(col for col in BKS_COLUMNS if col != "W")
    csv_path = _write_bks_csv(tmp_path / "bks.csv", [_row(0)], columns=columns)
    with pytest.raises(ValueError, match="'W' is absent"):
        resolve_ins_index(_config(csv_path, W=10))


def test_zero_matches_raises_valueerror(tmp_path: Path) -> None:
    csv_path = _write_bks_csv(tmp_path / "bks.csv", [_row(0, T=0.2), _row(1, T=0.6)])
    with pytest.raises(
        ValueError, match=r"(?s)matched zero instances.*T: \[0.2, 0.6\]"
    ):
        resolve_ins_index(_config(csv_path, T=0.5))


def test_non_float_filter_value_raises_valueerror(tmp_path: Path) -> None:
    csv_path = _write_bks_csv(tmp_path / "bks.csv", [_row(0)])
    with pytest.raises(ValueError, match="Cannot convert T.*'hard'.*float"):
        resolve_ins_index(_config(csv_path, T="hard"))


def test_non_float_filter_value_raises_on_empty_csv(tmp_path: Path) -> None:
    """The value check must not depend on any row being examined."""
    csv_path = _write_bks_csv(tmp_path / "bks.csv", [])
    with pytest.raises(ValueError, match="Cannot convert T.*'hard'.*float"):
        resolve_ins_index(_config(csv_path, T="hard"))


# --- integration: the contract pinned to the real benchmark table ---

BKS_TABLE = Path("benchmarks/PRA2017/pra2017_bks_table.csv")


def test_t06_r02_matches_coarsening_crossover() -> None:
    """{T: 0.6, R: 0.2} must reproduce the 160-element list that twelve configs
    carry verbatim -- the claim that this filter *is* that cell."""
    expected = set(
        load_yaml(Path("metadata/20260725/coarsening_crossover.yaml"))["ins_index"]
    )

    result = resolve_ins_index(
        {"ins_filter": {"T": 0.6, "R": 0.2}, "bks_table_csv_path": str(BKS_TABLE)}
    )

    assert set(result) == expected
    assert len(result) == 160


def test_t06_length_480() -> None:
    result = resolve_ins_index(
        {"ins_filter": {"T": 0.6}, "bks_table_csv_path": str(BKS_TABLE)}
    )
    assert len(result) == 480


def test_every_filterable_column_selects_on_the_real_table() -> None:
    """Guards against a column being renamed or dropped in the bks table."""
    with open(BKS_TABLE, newline="") as f:
        first = next(csv.DictReader(f))

    for column in FILTERABLE_COLUMNS:
        result = resolve_ins_index(
            {
                "ins_filter": {column: float(first[column])},
                "bks_table_csv_path": str(BKS_TABLE),
            }
        )
        assert result, f"filtering on {column} returned nothing"
