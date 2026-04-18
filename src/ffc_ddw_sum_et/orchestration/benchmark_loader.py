"""Load PRA2017 benchmark instances for experiment orchestration."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from ..parameters.ffc_ddw_params import FFcDueDateWindowParameters

logger = logging.getLogger(__name__)


class BenchmarkLoader:
    """Loads PRA2017 benchmark instances from a directory of .txt files."""

    def __init__(
        self,
        directory: Path,
        ins_index_source: Path | None = None,
    ):
        self.directory = Path(directory)
        self.ins_index_source = ins_index_source

    def _load_index_map(self) -> dict[int, str]:
        """Load insIndex -> ffc_ddw_sum_et_filename mapping from CSV."""
        if not self.ins_index_source:
            return {}
        index_map: dict[int, str] = {}
        with open(self.ins_index_source, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                index_map[int(row["insIndex"])] = row["ffc_ddw_sum_et_filename"]
        return index_map

    def load_all(
        self,
        file_pattern: str | None = None,
        instance_names: list[str] | None = None,
        ins_index: int | list[int] | None = None,
    ) -> list[FFcDueDateWindowParameters]:
        """Load instances from the benchmark directory.

        Args:
            file_pattern: Optional glob pattern to filter files (e.g. "*.txt").
            instance_names: Optional list of instance names to load.
            ins_index: Optional instance index or list of indices from the
                hybrid match CSV. When specified, only matching instances are
                loaded. When omitted, all ``.txt`` files are loaded.

        Returns:
            List of parsed FFcDueDateWindowParameters instances.
        """
        index_map = self._load_index_map()

        if ins_index is not None:
            indices = {ins_index} if isinstance(ins_index, int) else set(ins_index)
            filenames = set()
            missing: list[int] = []
            for idx in sorted(indices):
                if idx in index_map:
                    filenames.add(index_map[idx])
                else:
                    missing.append(idx)
            if missing:
                logger.warning("ins_index not found in CSV: %s", missing)

            files = [
                self.directory / name
                for name in sorted(filenames)
                if (self.directory / name).exists()
            ]
            if not files:
                raise FileNotFoundError(
                    f"No instances found for ins_index={sorted(indices)}."
                )
        else:
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
