from io import StringIO

import pandas as pd

from ffc_ddw_sum_et.io import Table2DManager


def test_table_2d_manager_builds_value_maps() -> None:
    df = pd.DataFrame(
        [[1, 2], [3, 4]],
        index=["job_a", "job_b"],
        columns=["stage_1", "stage_2"],
    )
    manager = Table2DManager("table", df)

    assert manager.col_idx_2_row_idx_2_value_map == {
        0: {"job_a": 1, "job_b": 3},
        1: {"job_a": 2, "job_b": 4},
    }
    assert manager.col_name_2_row_idx_2_value_map == {
        "stage_1": {"job_a": 1, "job_b": 3},
        "stage_2": {"job_a": 2, "job_b": 4},
    }


def test_table_2d_manager_invalidates_cached_maps_when_df_changes() -> None:
    manager = Table2DManager(
        "table",
        pd.DataFrame([[1, 2], [3, 4]], columns=["stage_1", "stage_2"]),
    )
    first_by_index = manager.col_idx_2_row_idx_2_value_map
    first_by_name = manager.col_name_2_row_idx_2_value_map

    manager.df = pd.DataFrame([[10, 20]], columns=["stage_1", "stage_2"])

    assert first_by_index != manager.col_idx_2_row_idx_2_value_map
    assert first_by_name != manager.col_name_2_row_idx_2_value_map
    assert manager.col_idx_2_row_idx_2_value_map == {0: {0: 10}, 1: {0: 20}}
    assert manager.col_name_2_row_idx_2_value_map == {
        "stage_1": {0: 10},
        "stage_2": {0: 20},
    }


def test_table_2d_manager_from_text_stream_preserves_subclass_type() -> None:
    manager = Table2DManager.from_text_stream(
        stream=StringIO("1 2\n3 4\n"),
        row_count=2,
        name="table",
    )

    assert isinstance(manager, Table2DManager)
    assert repr(manager) == "Table2DManager(name='table', shape=(2, 2))"
