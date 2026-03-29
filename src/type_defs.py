# type_defs.py
from typing import TypeVar

# Type hinting
ScalarTV = TypeVar("ScalarTV", int, float, str, bool)
NumericTV = TypeVar("NumericTV", int, float)

# Type membership
scalar_type_set = {int, float, str, bool}
numeric_type_set = {int, float}
