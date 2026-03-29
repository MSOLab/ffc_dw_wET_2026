from typing import TypeVar

ScalarTV = TypeVar("ScalarTV", int, float, str, bool)
NumericTV = TypeVar("NumericTV", int, float)

scalar_type_set = {int, float, str, bool}
numeric_type_set = {int, float}
