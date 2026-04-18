"""Load PRA2017 benchmark instances for experiment orchestration."""

from __future__ import annotations

import logging
from pathlib import Path

from ..parameters.ffc_ddw_params import FFcDueDateWindowParameters

logger = logging.getLogger(__name__)


class BenchmarkLoader:
    """Loads PRA2017 benchmark instances from a directory of .txt files."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def load_all(
        self,
        file_pattern: str | None = None,
        instance_names: list[str] | None = None,
    ) -> list[FFcDueDateWindowParameters]:
        """Load instances from the benchmark directory.

        Args:
            file_pattern: Optional glob pattern to filter files (e.g. "*.txt").
            instance_names: Optional list of instance name prefixes to load.

        Returns:
            List of parsed FFcDueDateWindowParameters instances.
        """
        files = sorted(
            self.directory.glob(f"*{file_pattern}" if file_pattern else "*.txt")
        )

        if not files:
            raise FileNotFoundError(
                f"No .txt files found in {self.directory}. "
                "Ensure the benchmark directory contains PRA2017 format files."
            )

        instances: list[FFcDueDateWindowParameters] = []
        for filepath in files:
            name = filepath.stem
            if instance_names is not None and name not in instance_names:
                continue
            try:
                with open(filepath, "r") as stream:
                    instance = FFcDueDateWindowParameters.from_pra_2017_data(
                        name, stream
                    )
                instances.append(instance)
            except Exception:
                logger.exception("Failed to parse %s, skipping", name)

        return instances
