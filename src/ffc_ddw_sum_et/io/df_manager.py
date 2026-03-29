from os import PathLike
from pathlib import Path
from typing import Self, TextIO, Type, cast

import pandas as pd

from .text_data_parser import TextDataParser
from .typing import ScalarTV, scalar_type_set


class DfManager:
    """A class to manage a DataFrame."""

    def __init__(self, name: str, df: pd.DataFrame) -> None:
        self.name = name
        self._df = df

    @property
    def df(self) -> pd.DataFrame:
        return self._df

    @df.setter
    def df(self, value: pd.DataFrame) -> None:
        self._df = value
        self._on_df_updated()

    def _on_df_updated(self) -> None:
        return None

    def __repr__(self) -> str:
        return f"DfManager(name='{self.name}', shape={self.df.shape})"

    @classmethod
    def from_text_stream(
        cls,
        stream: TextIO,
        row_count: int,
        dtype: Type[ScalarTV] | None = None,
        sep: str | None = None,
        name: str = "DfManager",
        transpose: bool = False,
    ) -> Self:
        _dtype = dtype or int
        if _dtype not in scalar_type_set:
            raise TypeError(f"Expected dtype to be a scalar type, got '{_dtype}'")

        rows: list[list[ScalarTV]] = TextDataParser.strip_list_of_a_typed_list(
            stream, row_count, cast(Type[ScalarTV], _dtype), sep=sep
        )
        df = pd.DataFrame(rows)
        if transpose:
            df = df.transpose()
        return cls(name, df)

    def row_count(self) -> int:
        return len(self.df)

    def col_count(self) -> int:
        return len(self.df.columns)

    def to_csv(self, path: PathLike[str] | str) -> None:
        normalized_path = Path(path)
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        self.df.to_csv(normalized_path, index=False)
