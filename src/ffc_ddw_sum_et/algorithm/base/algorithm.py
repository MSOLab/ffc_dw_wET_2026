"""Behavioral contract for algorithm execution."""

from typing import Protocol, runtime_checkable

from .alg_record import AlgRecord
from .alg_spec import AlgSpec

__all__ = ["Algorithm"]


@runtime_checkable
class Algorithm(Protocol):
    """Protocol for one self-contained algorithm execution.

    Implementations accept one `AlgSpec` and return one `AlgRecord`.
    The contract is intentionally limited to the execution boundary and stays
    unaware of launcher or reporter concerns.
    """

    def run(self, spec: AlgSpec) -> AlgRecord:
        """Execute one run from spec to record."""
        ...
