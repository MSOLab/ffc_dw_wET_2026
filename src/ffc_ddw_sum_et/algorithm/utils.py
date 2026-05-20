"""General utility functions for algorithm modules."""

from __future__ import annotations

import math


def trunc4(x: float | None) -> float | None:
    """Truncate a float to 4 decimal places toward zero."""
    if x is None:
        return None
    return math.trunc(x * 10000) / 10000
