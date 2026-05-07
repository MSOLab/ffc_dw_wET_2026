import logging

from .by_callback_recorder import ByCallbackRecorder


class ValueBoundPair:
    """A simple class to hold a value and its corresponding bound."""

    def __init__(self, value: float, bound: float) -> None:
        self.value = value
        self.bound = bound

    def __repr__(self) -> str:
        return f"ValueBoundPair(value={self.value}, bound={self.bound})"


class ObjectiveValueRecorder(ByCallbackRecorder[ValueBoundPair]):
    """
    Solution callback that records (elapsed time, objective value) pairs
    at each solution found during the CP-SAT search.
    """

    def __init__(
        self,
        print_on_record: bool = False,
        log_level_on_record: int | None = None,
        **kwargs,
    ) -> None:
        """
        Args:
            print_on_record (bool, optional): If True, prints progress at each solution callback.
                Defaults to False.
            log_level_on_record (int | None, optional): If set to a valid logging level,
                logs the progress message at that level.
                Defaults to None.
        """
        super().__init__(**kwargs)

        self.print_on_record = print_on_record
        """If True, prints progress on each solution callback."""

        self.log_level_on_record = log_level_on_record
        """If set, logs progress at the specified logging level."""

    # Start abstract method implementation

    def on_record(self, timestamp: float, value: ValueBoundPair) -> None:
        """Hook called after a new (timestamp, value) is added.

        - Prints progress if print_on_record is True.
        - Logs progress if log_level_on_record is set to a valid logging level.

        Args:
            timestamp (float): The elapsed time in seconds.
            value (ValueBoundPair): The objective value and corresponding bound.
        """
        obj_value = value.value
        obj_bound = value.bound
        info_str = (
            f"Elapsed: {timestamp:.2f} sec, ObjValue: {obj_value}, Bound: {obj_bound}"
        )
        if self.print_on_record:
            print(info_str)
        if (
            self.log_level_on_record is not None
            and 10 <= self.log_level_on_record <= 50
            and self.log_level_on_record in logging._levelToName
        ):
            logging.log(self.log_level_on_record, info_str)

    def on_solution_callback(self) -> None:
        """
        Called by the solver at each solution.

        - Records the current objective value and elapsed time.
        - Prints progress if print_on_record is True.
        - Logs progress if log_level_on_record is set to a valid logging level.
        """
        self.record(ValueBoundPair(self.objective_value, self.best_objective_bound))

    # End abstract method implementation
    # Start getter

    @property
    def elapsed_time_and_value(self) -> list[tuple[float, ValueBoundPair]]:
        """
        Returns:
            list[tuple[float, ValueBoundPair]]: A list of (elapsed time, ValueBoundPair) pairs
            collected during the search.
        """
        return self.entries.copy()

    # End getter
