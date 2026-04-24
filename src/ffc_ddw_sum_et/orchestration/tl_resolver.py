"""Shared helper: resolve raw CP-SAT time-limit values to seconds."""

from __future__ import annotations


def resolve_cp_tl(
    tl_raw: float | str | None,
    job_count: int,
    stage_count: int,
) -> float | None:
    """Resolve a raw CP-SAT time-limit value to ``float | None``.

    - ``None``  → ``None`` (no limit)
    - ``float`` → used as-is (seconds)
    - ``str`` ending with ``"nc"`` with a numeric prefix → ``number * job_count * stage_count``
    - ``str`` ending with ``"c"`` with a numeric prefix → ``number * stage_count``
    - other ``str`` → ``float(value)``; raises ``ValueError`` if the cast fails

    Args:
        tl_raw (float | str | None): Raw time limit value.
        job_count (int): Number of jobs in the instance.
        stage_count (int): Number of stages in the instance.

    Raises:
        ValueError: If the time limit value is invalid.
        ValueError: If the time limit value is not a valid number.
        ValueError: If the time limit value does not match any recognized pattern.

    Returns:
        float | None: The resolved time limit value in seconds,
            or None if no limit is specified.
    """
    if tl_raw is None:
        return None
    if isinstance(tl_raw, (int, float)):
        return float(tl_raw)
    # str branch
    s = tl_raw.strip()
    if s.endswith("nc"):
        prefix = s[:-2]
        try:
            factor = float(prefix)
        except ValueError:
            raise ValueError(
                f"cp_tl string '{tl_raw}' ends with 'nc' but the prefix "
                f"'{prefix}' is not a valid number"
            )
        return factor * job_count * stage_count
    elif s.endswith("c"):
        prefix = s[:-1]
        try:
            factor = float(prefix)
        except ValueError:
            raise ValueError(
                f"cp_tl string '{tl_raw}' ends with 'c' but the prefix "
                f"'{prefix}' is not a valid number"
            )
        return factor * stage_count
    try:
        return float(s)
    except ValueError:
        raise ValueError(
            f"cp_tl string '{tl_raw}' cannot be interpreted as a float "
            "and does not match the '<number>nc' or '<number>c' pattern"
        )
