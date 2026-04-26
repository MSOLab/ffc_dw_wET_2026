from __future__ import annotations


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
        prefix = s[:-2]
        try:
            factor = float(prefix)
        except ValueError:
            raise ValueError(
                f"cp_tl string '{value_expr}' ends with 'nc' but the prefix "
                f"'{prefix}' is not a valid number"
            )
        return factor * job_count * stage_count
    elif s.endswith("c"):
        prefix = s[:-1]
        try:
            factor = float(prefix)
        except ValueError:
            raise ValueError(
                f"cp_tl string '{value_expr}' ends with 'c' but the prefix "
                f"'{prefix}' is not a valid number"
            )
        return factor * stage_count
    elif s.endswith("m"):
        prefix = s[:-1]
        try:
            factor = float(prefix)
        except ValueError:
            raise ValueError(
                f"cp_tl string '{value_expr}' ends with 'm' but the prefix "
                f"'{prefix}' is not a valid number"
            )
        return factor * last_stage_mc_count
    try:
        return float(s)
    except ValueError:
        raise ValueError(
            f"cp_tl string '{value_expr}' cannot be interpreted as a float "
            "and does not match the '<number>nc' or '<number>c' or '<number>m' pattern"
        )
