from .base_solution_callback import BaseSolutionCallback
from .time_stamped_recorder import RecordValT, TimeStampedRecorder


class ByCallbackRecorder(BaseSolutionCallback, TimeStampedRecorder[RecordValT]):
    """
    For solver callbacks (e.g. CP-SAT on_solution_callback).
    Subclasses only need to call self.record(...) in on_solution_callback().

    Both bases are initialized explicitly: ``BaseSolutionCallback`` for the
    SWIG side, ``TimeStampedRecorder`` for the Python ``time_started`` /
    ``entries`` state. ``super().__init__`` chaining is unreliable across the
    SWIG ``CpSolverSolutionCallback`` boundary.
    """

    def __init__(self, **kwargs) -> None:
        BaseSolutionCallback.__init__(self)
        TimeStampedRecorder.__init__(self, **kwargs)
