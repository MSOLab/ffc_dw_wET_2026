from functools import cached_property
from typing import Hashable, Self, TextIO, Type

import pandas as pd

from ..type_defs import ScalarTV
from .df_manager import DfManager


class Table2DManager(DfManager):
    """A class to manage a 2D table represented as a DataFrame."""

    def __init__(self, name: str, df: pd.DataFrame) -> None:
        super().__init__(name, df)

    def __repr__(self) -> str:
        return f"Table2DManager(name='{self.name}', shape={self.df.shape})"

    def _on_df_updated(self) -> None:
        self.__dict__.pop("col_idx_2_row_idx_2_value_map", None)
        self.__dict__.pop("col_name_2_row_idx_2_value_map", None)

    @classmethod
    def from_text_stream(
        cls,
        stream: TextIO,
        row_count: int,
        dtype: Type[ScalarTV] | None = None,
        sep: str | None = None,
        name: str = "Table2DManager",
        transpose: bool = False,
    ) -> Self:
        return super().from_text_stream(
            stream, row_count, dtype=dtype, sep=sep, name=name, transpose=transpose
        )

    @cached_property
    def col_idx_2_row_idx_2_value_map(self) -> dict[int, dict[Hashable, ScalarTV]]:
        return {
            col_idx: col_series.to_dict()
            for col_idx, (_, col_series) in enumerate(self.df.items())
        }

    @cached_property
    def col_name_2_row_idx_2_value_map(
        self,
    ) -> dict[Hashable, dict[Hashable, ScalarTV]]:
        return {
            col_name: col_series.to_dict() for col_name, col_series in self.df.items()
        }
