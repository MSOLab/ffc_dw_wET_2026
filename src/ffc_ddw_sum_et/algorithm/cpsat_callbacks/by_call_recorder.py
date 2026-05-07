from abc import ABC, abstractmethod

from .time_stamped_recorder import RecordValT, TimeStampedRecorder


class ByCallRecorder(TimeStampedRecorder[RecordValT], ABC):
    """For simple callables (e.g. best_bound_callback, log_callback).
    Subclasses implement ``__call__(value)`` and call ``self.record(value)``.

    Unlike ``ByCallbackRecorder``, this class does not mix in
    ``CpSolverSolutionCallback`` (SWIG), so ``ABCMeta`` and ``@abstractmethod``
    can be used normally.
    """

    @abstractmethod
    def __call__(self, value: RecordValT) -> None: ...
