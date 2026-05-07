from ortools.sat.python.cp_model import CpSolverSolutionCallback


class BaseSolutionCallback(CpSolverSolutionCallback):
    """Common base for our solution callbacks.

    `CpSolverSolutionCallback` is a SWIG-generated C++ wrapper whose
    metaclass conflicts with `abc.ABC`/`ABCMeta`, so we don't use
    `@abstractmethod` here. Subclasses must override
    `on_solution_callback` by convention; calling the base raises.

    Only the SWIG side is initialized here. Mixin classes (e.g.
    ``TimeStampedRecorder``) are initialized explicitly by intermediate
    subclasses (``ByCallbackRecorder``) — relying on ``super().__init__``
    to chain through ``CpSolverSolutionCallback`` is unsafe because the
    SWIG-generated ``__init__`` does not propagate through Python MRO.
    """

    def __init__(self) -> None:
        CpSolverSolutionCallback.__init__(self)

    def on_solution_callback(self) -> None:
        raise NotImplementedError(
            "Subclasses of BaseSolutionCallback must override on_solution_callback"
        )

    def StopSearch(self) -> None:
        return super().StopSearch()
