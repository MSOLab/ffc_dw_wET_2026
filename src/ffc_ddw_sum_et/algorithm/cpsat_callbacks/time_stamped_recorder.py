import time
from typing import Generic, List, Tuple, TypeVar

RecordValT = TypeVar("RecordValT")
"""
Type of the metric being recorded (objective value, bound, etc.).
"""


class TimeStampedRecorder(Generic[RecordValT]):
    """
    Base for recording timestamped metric values.

    Maintains a list of (elapsed_time, value) pairs. Subclasses integrate
    ``self.record(value)`` into their solver-specific callback, and override
    ``on_record()`` if they need custom side-effects (e.g. printing or logging).

    Not declared as ``abc.ABC`` because subclasses combined with SWIG-generated
    bases (``CpSolverSolutionCallback``) hit a metaclass conflict with
    ``ABCMeta``. Override-by-convention; the default ``on_record`` is a no-op.

    Thread-safety contract: ``record()`` is invoked from CP-SAT's solution-
    callback thread, while ``self.entries`` is read from the main thread by
    callers like ``cpsat_adapter._build_progress_log``. Reads are only safe
    *after* ``solver.solve()`` returns (i.e. after the callback thread has
    joined). Do not iterate ``entries`` mid-solve from the main thread, and
    do not share a recorder instance across concurrent solves.
    """

    def __init__(self, **kwargs) -> None:
        self.time_started = time.monotonic()

        # the master list of (time, value) pairs
        self.entries: List[Tuple[float, RecordValT]] = []
        """A list of tuples containing (elapsed time, value)."""

    def record(self, value: RecordValT) -> None:
        """Append a new record with the current elapsed time, then invoke the on_record hook.

        Args:
            value (T): The recorded value.
        """
        t = time.monotonic() - self.time_started
        self.entries.append((t, value))
        self.on_record(t, value)

    def on_record(self, timestamp: float, value: RecordValT) -> None:
        """
        Hook called after a new (timestamp, value) is added. Default no-op.
        Subclasses can override to print, log, or trigger other side-effects.

        Args:
            timestamp (float): The time at which the value was recorded.
            value (T): The recorded value.
        """
