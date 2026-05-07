import logging

from routix import ElapsedTimer

from .by_call_recorder import ByCallRecorder


class ObjectiveBoundRecorder(ByCallRecorder[float]):
    """
    Callable object to record (elapsed time, objective bound) pairs.

    Args:
        e_timer (ElapsedTimer | None): Timer to measure elapsed time.
            If None, a new ElapsedTimer is created and started.
        print_on_record (bool): If True, prints each record.
        log_level_on_record (int | None): If set, logs each record at this logging level.

    Attributes:
        elapsed_time_and_bound (list[tuple[float, float]]): List of (elapsed_sec, objective bound) pairs.
    """

    def __init__(
        self,
        e_timer: ElapsedTimer | None = None,
        print_on_record: bool = False,
        log_level_on_record: int | None = None,
    ) -> None:
        """
        Args:
            e_timer (ElapsedTimer | None, optional): Timer for measuring elapsed time.
                If None, a new ElapsedTimer is created and started.
            print_on_record (bool, optional): If True, prints progress at each call.
                Defaults to False.
            log_level_on_record (int | None, optional): If set to a valid logging level,
                logs the progress message at that level.
                Defaults to None.
        """
        super().__init__(e_timer=e_timer)

        self.print_on_record = print_on_record
        """If True, prints progress on each call."""

        self.log_level_on_record = log_level_on_record
        """If set, logs progress at the specified logging level."""

    # Start abstract method implementation

    def on_record(self, timestamp: float, value: float) -> None:
        """Hook called after a new (timestamp, value) is added.

        - Prints progress if print_on_record is True.
        - Logs progress if log_level_on_record is set to a valid logging level.

        Args:
            timestamp (float): The elapsed time in seconds.
            value (float): The objective bound value.
        """
        info_str = f"Elapsed: {timestamp:.2f} sec, \tObjBound: {value}"
        if self.print_on_record:
            print(info_str)
        if (
            self.log_level_on_record is not None
            and 10 <= self.log_level_on_record <= 50
            and self.log_level_on_record in logging._levelToName
        ):
            logging.log(self.log_level_on_record, info_str)

    def __call__(self, obj_bound: float):
        """
        Called by the solver at each call.

        - Records the current objective bound and elapsed time.
        - Prints progress if print_on_record is True.
        - Logs progress if log_level_on_record is set to a valid logging level.
        """
        self.record(obj_bound)

    # End abstract method implementation
    # Start getter

    @property
    def elapsed_time_and_bound(self) -> list[tuple[float, float]]:
        """Returns the list of (elapsed time, objective bound) pairs."""
        return self.entries.copy()

    # End getter
