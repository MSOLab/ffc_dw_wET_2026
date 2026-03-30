"""Base option contract for algorithm execution."""

from dataclasses import dataclass

__all__ = ["AlgOption"]


@dataclass(frozen=True, slots=True, kw_only=True)
class AlgOption:
    """Base class for algorithm option payloads."""

