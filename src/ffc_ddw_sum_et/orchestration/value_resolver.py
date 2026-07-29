from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


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


def resolve_jd_count_target(
    jd_target: int | str,
    job_count: int,
) -> int:
    """Resolve ``jd_target`` (raw config value) to ``jd_count_target`` (int ≥ 1).

    ``jd_target`` forms:

    - ``int`` or numeric ``str`` (e.g. ``2``, ``"2"``) → absolute count.
    - ``"<ratio>n"`` (e.g. ``"0.05n"``) → ``ceil(job_count * ratio)``.

    Validation:

    - Ratio must be ``> 0`` (``"0n"`` / ``"0.0n"`` → ``ValueError``).
    - Absolute value must be ``≥ 1`` (``0`` / ``"0"`` → ``ValueError``).
      No lower-bound clamping — a ``jd_target=0`` is a configuration error,
      not "clamp to 1 silently."

    Upper bound: ``min(result, job_count)`` with an info-level log
    when the raw target exceeds ``job_count`` (this is "destroy all jobs"
    and carries clear intent, so it is allowed).
    """
    if isinstance(jd_target, str):
        s = jd_target.strip()
        if s.endswith("n"):
            prefix = s[:-1]
            if prefix == "" or prefix == ".":
                raise ValueError(
                    f"jd_target '{jd_target}': ratio prefix is missing or empty"
                )
            try:
                ratio = float(prefix)
            except ValueError:
                raise ValueError(
                    f"jd_target '{jd_target}': ratio prefix '{prefix}' "
                    f"is not a valid number"
                )
            if ratio <= 0:
                raise ValueError(
                    f"jd_target '{jd_target}': ratio must be > 0, got {ratio}"
                )
            result = math.ceil(job_count * ratio)
        else:
            try:
                result = int(s)
            except ValueError:
                raise ValueError(
                    f"jd_target '{jd_target}': does not match '<int>' or "
                    f"'<ratio>n' pattern"
                )
    else:
        result = int(jd_target)

    if result < 1:
        raise ValueError(f"jd_target '{jd_target}' resolved to {result}; must be ≥ 1")

    if result > job_count:
        logger.info(
            "resolve_jd_count_target: jd_target=%r -> %d > n=%d, saturating to %d",
            jd_target,
            result,
            job_count,
            job_count,
        )
        result = job_count

    return result
