from io import StringIO

import pytest

from ffc_ddw_sum_et.io import TextDataParser


def test_strip_a_line_returns_trimmed_line() -> None:
    assert TextDataParser.strip_a_line(StringIO("  value  \n")) == "value"


def test_strip_a_line_raises_on_eof() -> None:
    with pytest.raises(EOFError, match="Unexpected end of file while reading data."):
        TextDataParser.strip_a_line(StringIO(""))


def test_strip_a_typed_value_converts_line() -> None:
    assert TextDataParser.strip_a_typed_value(StringIO("42\n"), int) == 42


def test_strip_a_typed_value_wraps_value_error() -> None:
    with pytest.raises(ValueError, match="Failed to convert line to int"):
        TextDataParser.strip_a_typed_value(StringIO("abc\n"), int)


def test_strip_a_typed_list_uses_separator() -> None:
    assert TextDataParser.strip_a_typed_list(StringIO("1,2,3\n"), int, sep=",") == [
        1,
        2,
        3,
    ]


def test_strip_list_of_a_typed_list_raises_with_row_context() -> None:
    with pytest.raises(EOFError, match="while reading 3-th row among 3 rows"):
        TextDataParser.strip_list_of_a_typed_list(
            StringIO("1 2\n3 4\n"),
            num_lists=3,
            dtype=int,
        )
