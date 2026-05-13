"""Pure calculation helpers shared across packages."""

import math


def rpd_f(obj: float, ref: float) -> float:
    """Relative percentage difference.

    ``(obj - ref) / ((obj + ref) / 2)``.

    ``obj == ref == 0`` → 0.0 by definition.
    ``obj + ref == 0`` but ``obj != ref`` → NaN (undefined).
    """
    denom = obj + ref
    if denom == 0:
        return 0.0 if obj == ref else math.nan
    return 2.0 * (obj - ref) / denom
