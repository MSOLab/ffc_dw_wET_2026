"""Per-batch CP-SAT time-limit schedule for NEH-CP."""

from __future__ import annotations

import logging
from typing import Literal

__all__ = ["NehCpBatchTlMode", "resolve_per_step_tl"]

NehCpBatchTlMode = Literal["constant", "linear"]


def resolve_per_step_tl(
    *,
    cp_tl_from_arg: float | None,
    total_seconds: float | None,
    num_batches: int | None,
    batch_count: int,
    batch_tl_mode: NehCpBatchTlMode,
    batch_tl_offset_seconds: float,
    logger: logging.Logger,
) -> list[float] | None:
    """Build the per-batch CP-SAT time-limit list (or None for no limit).

    When ``total_seconds`` is None, this is a flat ``[cp_tl_from_arg] * B``
    (or None if ``cp_tl_from_arg`` is also None).  When ``total_seconds`` is
    set, ``cp_tl_from_arg`` is ignored — the per-batch limit is derived from
    ``batch_tl_mode``: ``"constant"`` yields a flat ``total_seconds /
    divisor`` per batch (``divisor`` is ``num_batches`` when set, else
    ``batch_count``); ``"linear"`` yields ``offset + i * x`` per batch with
    ``x`` chosen so the limits sum to ``total_seconds``, falling back to
    constant ``total_seconds / batch_count`` when the offset would consume
    the whole budget.
    """
    if total_seconds is None:
        if cp_tl_from_arg is None:
            return None
        return [cp_tl_from_arg] * batch_count

    if cp_tl_from_arg is not None:
        logger.info(
            "neh_cp: cp_tl=%s ignored because total_timelimit is set; "
            "per-batch limit governed by batch_tl_mode=%r.",
            cp_tl_from_arg,
            batch_tl_mode,
        )

    if batch_tl_mode == "constant":
        divisor = num_batches if num_batches is not None else batch_count
        return [total_seconds / divisor] * batch_count

    if batch_tl_mode == "linear":
        offset = batch_tl_offset_seconds
        x = (
            2.0
            * (total_seconds - batch_count * offset)
            / (batch_count * (batch_count + 1))
        )
        if x <= 0:
            logger.warning(
                "neh_cp: batch_tl_offset_seconds=%.4f * B=%d exceeds "
                "total_timelimit=%.2f; falling back to constant schedule.",
                offset,
                batch_count,
                total_seconds,
            )
            return [total_seconds / batch_count] * batch_count
        return [offset + (i + 1) * x for i in range(batch_count)]

    raise ValueError(f"Unknown batch_tl_mode: {batch_tl_mode!r}")
