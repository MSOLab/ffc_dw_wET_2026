from __future__ import annotations


def _parse_factor(prefix: str, original: str, suffix: str) -> float:
    """Parse the numeric prefix of a value expression.

    Empty prefix is treated as ``1.0`` so bare ``"m"`` / ``"n"`` / ``"c"`` /
    ``"nc"`` resolve to ``1 × (symbol)``.
    """
    if prefix == "":
        return 1.0
    try:
        return float(prefix)
    except ValueError:
        raise ValueError(
            f"value expression '{original}' ends with '{suffix}' but the prefix "
            f"'{prefix}' is not a valid number"
        )


def resolve_value_expr(
    value_expr: float | str | None,
    job_count: int,
    stage_count: int,
    last_stage_mc_count: int,
) -> float | None:
    if value_expr is None:
        return None
    if isinstance(value_expr, (int, float)):
        return float(value_expr)
    # str branch
    s = value_expr.strip()
    if s.endswith("nc"):
        return _parse_factor(s[:-2], value_expr, "nc") * job_count * stage_count
    elif s.endswith("n"):
        return _parse_factor(s[:-1], value_expr, "n") * job_count
    elif s.endswith("c"):
        return _parse_factor(s[:-1], value_expr, "c") * stage_count
    elif s.endswith("m"):
        return _parse_factor(s[:-1], value_expr, "m") * last_stage_mc_count
    try:
        return float(s)
    except ValueError:
        raise ValueError(
            f"value expression '{value_expr}' cannot be interpreted as a float "
            "and does not match the '<number>nc' / '<number>n' / '<number>c' / "
            "'<number>m' pattern"
        )
