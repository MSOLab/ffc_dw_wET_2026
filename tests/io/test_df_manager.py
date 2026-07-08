from io import StringIO
from pathlib import Path

import pandas as pd
import pandas.testing as pdt
import pytest

from ffc_ddw_sum_et.io import DfManager


def test_df_manager_from_text_stream_builds_dataframe() -> None:
    manager = DfManager.from_text_stream(
        StringIO("1 2\n3 4\n"),
        row_count=2,
        name="processing_times",
    )

    assert manager.name == "processing_times"
    assert manager.row_count() == 2
    assert manager.col_count() == 2
    assert repr(manager) == "DfManager(name='processing_times', shape=(2, 2))"
    pdt.assert_frame_equal(manager.df, pd.DataFrame([[1, 2], [3, 4]]))


def test_df_manager_from_text_stream_supports_separator_and_transpose() -> None:
    manager = DfManager.from_text_stream(
        StringIO("1,2,3\n4,5,6\n"),
        row_count=2,
        dtype=float,
        sep=",",
        transpose=True,
    )

    pdt.assert_frame_equal(
        manager.df,
        pd.DataFrame([[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]]),
    )


def test_df_manager_from_text_stream_rejects_non_scalar_dtype() -> None:
    with pytest.raises(TypeError, match="Expected dtype to be a scalar type"):
        DfManager.from_text_stream(
            StringIO("1 2\n"),
            row_count=1,
            dtype=list,
        )


def test_df_manager_to_csv_creates_parent_directory(tmp_path: Path) -> None:
    manager = DfManager("example", pd.DataFrame([[1, 2], [3, 4]]))
    output_path = tmp_path / "nested" / "output.csv"

    manager.to_csv(output_path)

    assert output_path.exists()
    assert output_path.read_text() == "0,1\n1,2\n3,4\n"


def test_df_manager_to_csv_accepts_string_path(tmp_path: Path) -> None:
    manager = DfManager("example", pd.DataFrame([[1, 2], [3, 4]]))
    output_path = tmp_path / "string" / "output.csv"

    manager.to_csv(str(output_path))

    assert output_path.exists()
    assert output_path.read_text() == "0,1\n1,2\n3,4\n"


def test_df_manager_to_csv_accepts_pathlike_object(tmp_path: Path) -> None:
    class CustomPath:
        def __init__(self, path: Path) -> None:
            self._path = path

        def __fspath__(self) -> str:
            return str(self._path)

    manager = DfManager("example", pd.DataFrame([[1, 2], [3, 4]]))
    output_path = tmp_path / "pathlike" / "output.csv"

    manager.to_csv(CustomPath(output_path))

    assert output_path.exists()
    assert output_path.read_text() == "0,1\n1,2\n3,4\n"
