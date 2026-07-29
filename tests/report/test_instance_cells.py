from __future__ import annotations

import pandas as pd

from ffc_ddw_sum_et.report.instance_cells import (
    CELL_DIMS,
    cell_dim_values,
    cell_key_by_instance,
    format_cell_value,
)


def _baseline(**overrides) -> pd.DataFrame:
    data = {
        "instance_id": ["A", "B", "C"],
        "t_factor": [0.2, 0.6, 1.0],
        "r_factor": [0.2, 0.6, 1.0],
        "job_cnt": [50, 100, 200],
        "stage_cnt": [5, 10, 5],
        "ref_obj": [100.0, 200.0, 300.0],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def test_cell_dims_are_four() -> None:
    assert CELL_DIMS == ("t_factor", "r_factor", "job_cnt", "stage_cnt")


def test_format_cell_value_t_r_one_decimal() -> None:
    assert format_cell_value("t_factor", 0.2) == "0.2"
    assert format_cell_value("r_factor", 1.0) == "1.0"


def test_format_cell_value_n_c_integer() -> None:
    assert format_cell_value("job_cnt", 50) == "50"
    assert format_cell_value("stage_cnt", 5) == "5"


def test_cell_key_by_instance_normalizes_correctly() -> None:
    bdf = _baseline()
    keys = cell_key_by_instance(bdf)
    assert keys == {
        "A": ("0.2", "0.2", "50", "5"),
        "B": ("0.6", "0.6", "100", "10"),
        "C": ("1.0", "1.0", "200", "5"),
    }


def test_cell_key_skips_nan_dims(caplog) -> None:
    bdf = _baseline(t_factor=[0.2, None, 1.0])
    keys = cell_key_by_instance(bdf)
    assert "A" in keys
    assert "B" not in keys
    assert "C" in keys
    assert "Excluded 1 instance(s) from cell map due to NaN in cell dims" in caplog.text


def test_cell_dim_values_sorted_numerically() -> None:
    bdf = _baseline(
        job_cnt=[50, 100, 200],
        t_factor=[0.2, 0.6, 1.0],
    )
    vals = cell_dim_values(bdf)
    assert vals["job_cnt"] == ["50", "100", "200"]
    assert vals["t_factor"] == ["0.2", "0.6", "1.0"]
